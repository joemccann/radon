#!/usr/bin/env python3
"""Gamma Exposure (GEX) Levels Scanner.

Fetches dealer gamma exposure by strike from Unusual Whales,
computes key levels (flip, magnets, accelerators), and outputs
a structured JSON signal for the regime dashboard.

Data sources:
  1. Unusual Whales — greek-exposure/strike, greek-exposure (aggregate),
     iv_rank (30D IV + rank), screener/stocks (vol P/C)
  2. MenthorQ — key levels (HVL, call resistance, put support, top GEX strikes)
     fetched via Playwright browser automation (optional, graceful fallback)

Usage:
    python3 scripts/gex_scan.py --json --ticker SPX
    python3 scripts/gex_scan.py --json --ticker SPY
    python3 scripts/gex_scan.py --json --ticker QQQ --no-mq
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── path setup ────────────────────────────────────────────────────
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

# ── constants ─────────────────────────────────────────────────────
INDEX_TICKERS = {"SPX", "NDX"}
BUCKET_SIZE_INDEX = 25      # $25 buckets for SPX/NDX
BUCKET_SIZE_ETF = 5         # $5 buckets for SPY/QQQ
PROFILE_RANGE_PCT = 0.10    # Show strikes within ±10% of spot
HISTORY_DAYS = 20           # Number of sessions in history
TRADING_DAYS_PER_YEAR = 252


def _bucket_size_for(ticker: str, spot: float) -> int:
    if ticker in INDEX_TICKERS:
        return BUCKET_SIZE_INDEX
    if ticker in ("SPY", "QQQ"):
        return BUCKET_SIZE_ETF
    return max(1, round(spot * 0.005))


# ══════════════════════════════════════════════════════════════════
# Data Fetching
# ══════════════════════════════════════════════════════════════════

def _get_uw_client():
    from clients.uw_client import UWClient
    return UWClient()


def fetch_strike_gex(client, ticker: str) -> List[Dict[str, Any]]:
    """Fetch per-strike GEX data from UW. Returns parsed rows."""
    data = client.get_greek_exposure_by_strike(ticker)
    rows = data.get("data", [])
    parsed = []
    for r in rows:
        try:
            strike = float(r["strike"])
            call_gex = float(r.get("call_gex", 0))
            put_gex = float(r.get("put_gex", 0))
            call_delta = float(r.get("call_delta", 0))
            put_delta = float(r.get("put_delta", 0))
            parsed.append({
                "strike": strike,
                "call_gex": call_gex,
                "put_gex": put_gex,
                "net_gex": call_gex + put_gex,
                "call_delta": call_delta,
                "put_delta": put_delta,
                "net_delta": call_delta + put_delta,
            })
        except (KeyError, ValueError, TypeError):
            continue
    return parsed


def fetch_aggregate_gex(client, ticker: str) -> List[Dict[str, Any]]:
    """Fetch aggregate GEX time series for history."""
    data = client.get_greek_exposure(ticker)
    rows = data.get("data", [])
    parsed = []
    for r in rows:
        try:
            parsed.append({
                "date": r["date"],
                "call_gex": float(r.get("call_gex", 0)),
                "put_gex": float(r.get("put_gex", 0)),
                "call_delta": float(r.get("call_delta", 0)),
                "put_delta": float(r.get("put_delta", 0)),
            })
        except (KeyError, ValueError, TypeError):
            continue
    return parsed


def fetch_atm_iv(client, ticker: str, spot: float) -> Optional[float]:  # noqa: ARG001
    """Fetch 30D ATM IV from UW iv_rank endpoint.

    Previous implementation used /greeks (0DTE chain) which produces
    numerically unstable IV values as T→0.  iv_rank returns the
    properly-computed 30-day implied volatility.
    """
    try:
        data = client.get_iv_rank(ticker)
        rows = data.get("data", [])
        if not rows:
            return None
        latest = max(rows, key=lambda r: r.get("date", ""))
        vol = latest.get("volatility")
        return float(vol) if vol is not None else None
    except Exception as exc:
        print(f"  ATM IV fetch failed: {exc}", file=sys.stderr)
        return None


def fetch_iv_rank(client, ticker: str) -> Optional[float]:
    """Fetch IV rank (1-year percentile) from UW iv_rank endpoint."""
    try:
        data = client.get_iv_rank(ticker)
        rows = data.get("data", [])
        if not rows:
            return None
        latest = max(rows, key=lambda r: r.get("date", ""))
        rank = latest.get("iv_rank_1y")
        return float(rank) if rank is not None else None
    except Exception:
        return None


def fetch_vol_pc(client, ticker: str) -> Optional[float]:
    """Fetch volume put/call ratio from UW screener."""
    try:
        data = client.get_stock_screener(ticker=ticker)
        rows = data.get("data", [])
        if not rows:
            return None
        for r in rows:
            t = r.get("ticker", r.get("symbol", ""))
            if t.upper() == ticker.upper():
                pc = r.get("put_call_ratio")
                return float(pc) if pc is not None else None
        return None
    except Exception as exc:
        print(f"  Vol P/C fetch failed: {exc}", file=sys.stderr)
        return None


# ══════════════════════════════════════════════════════════════════
# MenthorQ Integration
# ══════════════════════════════════════════════════════════════════

# Map UW tickers to MenthorQ dashboard slugs
_MQ_TICKER_MAP: Dict[str, str] = {
    "SPX": "spx", "NDX": "ndx", "SPY": "spy", "QQQ": "qqq",
    "IWM": "iwm", "SMH": "smh", "IBIT": "ibit", "VIX": "vix",
    "RUT": "rut", "NVDA": "nvda", "GOOGL": "googl", "META": "meta",
    "TSLA": "tsla", "AMZN": "amzn", "MSFT": "msft", "NFLX": "nflx",
}


def fetch_mq_levels(ticker: str) -> Optional[Dict[str, Any]]:
    """Fetch MenthorQ key levels (HVL, call resistance, put support, IV).

    Uses Playwright browser automation to intercept the admin-ajax.php
    JSON responses that carry structured level data.  Returns None on any
    failure so callers can proceed with UW-only data.
    """
    mq_slug = _MQ_TICKER_MAP.get(ticker.upper())
    if not mq_slug:
        print(f"  MQ: ticker {ticker} not supported, skipping", file=sys.stderr)
        return None

    ss_path = _PROJECT_DIR / "data" / "menthorq_cache" / "menthorq_storage_state.json"
    if not ss_path.exists():
        print("  MQ: no session state found, skipping", file=sys.stderr)
        return None

    try:
        from playwright.sync_api import sync_playwright

        collected: Dict[str, Any] = {}

        def _on_response(resp):
            if "admin-ajax.php" not in resp.url:
                return
            try:
                body = resp.json()
                if not body.get("success"):
                    return
                resource = body.get("data", {}).get("resource", {})
                d = resource.get("data", {})
                if not isinstance(d, dict) or not d:
                    return
                # Primary key_levels card: has HVL, call resistance, put support
                if "High Vol Level" in d or "HVL" in d:
                    collected.update(d)
                    collected["_mq_date"] = body.get("data", {}).get("date")
                # Secondary key_levels card: has Top Net GEX Strikes
                elif "Top Net GEX Strikes" in d and "Top Net GEX Strikes" not in collected:
                    collected["Top Net GEX Strikes"] = d["Top Net GEX Strikes"]
                    if not collected.get("_mq_date"):
                        collected["_mq_date"] = body.get("data", {}).get("date")
            except Exception:
                pass

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(storage_state=str(ss_path))
            page = ctx.new_page()
            page.on("response", _on_response)

            url = (
                f"https://menthorq.com/account/?action=data"
                f"&type=dashboard&commands=eod&tickers=commons&ticker={mq_slug}"
            )
            try:
                page.goto(url, wait_until="networkidle", timeout=60000)
            except Exception:
                # networkidle can time out on slow connections; data may still
                # have arrived via the intercepted responses
                pass
            import time as _t
            _t.sleep(3)
            browser.close()

        if not collected:
            print("  MQ: no key_levels data captured", file=sys.stderr)
            return None

        def _f(key: str) -> Optional[float]:
            v = collected.get(key)
            try:
                return float(v) if v is not None else None
            except (TypeError, ValueError):
                return None

        return {
            "source_date": collected.get("_mq_date"),
            "spot": _f("Spot Price"),
            "hvl": _f("High Vol Level") or _f("HVL"),
            "call_resistance_all": _f("Call Resistance"),
            "call_resistance_0dte": _f("Call Resistance 0DTE"),
            "put_support_all": _f("Put Support"),
            "put_support_0dte": _f("Put Support 0DTE"),
            "expected_high": _f("1D Max."),
            "expected_low": _f("1D Min."),
            "distance_to_hvl_pct": collected.get("Distance to HVL %"),
            "iv30d": _f("Implied Vol 30D"),
            "hv30": _f("Historical Vol 30D"),
            "iv_rank": collected.get("IV Rank"),
            "top_gex_strikes": collected.get("Top Net GEX Strikes", []),
        }

    except Exception as exc:
        print(f"  MQ levels fetch failed: {exc}", file=sys.stderr)
        return None


def compute_source_delta(
    levels: Dict[str, Any], mq: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Compute per-level deltas between UW and MenthorQ.

    delta > 0 means UW is higher; delta < 0 means MQ is higher.
    """
    if not mq:
        return None

    def _strike(level_dict: Any) -> Optional[float]:
        if not level_dict:
            return None
        return level_dict.get("strike")

    def _diff(uw_val: Optional[float], mq_val: Optional[float]) -> Optional[Dict[str, Any]]:
        if uw_val is None or mq_val is None:
            return None
        return {"uw": uw_val, "mq": mq_val, "delta": round(uw_val - mq_val, 1)}

    uw_flip = _strike(levels.get("gex_flip"))
    uw_put  = _strike(levels.get("put_wall"))
    uw_call = _strike(levels.get("call_wall"))

    return {
        k: v for k, v in {
            "flip_vs_hvl": _diff(uw_flip, mq.get("hvl")),
            "put_wall_vs_support_all": _diff(uw_put, mq.get("put_support_all")),
            "put_wall_vs_support_0dte": _diff(uw_put, mq.get("put_support_0dte")),
            "call_wall_vs_resistance_all": _diff(uw_call, mq.get("call_resistance_all")),
            "call_wall_vs_resistance_0dte": _diff(uw_call, mq.get("call_resistance_0dte")),
        }.items() if v is not None
    } or None


def fetch_spot_price(client, ticker: str) -> Optional[float]:
    """Get spot price: UW stock info → UW iv_rank close → Yahoo Finance.

    Never falls back to strike-midpoint estimation which can be wildly
    wrong when IB is unavailable (e.g. weekends).
    """
    # 1. UW stock info
    try:
        data = client.get_stock_info(ticker)
        info = data.get("data", [{}])
        if isinstance(info, list) and info:
            info = info[0]
        price = info.get("last", info.get("close", info.get("price")))
        if price is not None:
            return float(price)
    except Exception:
        pass

    # 2. UW iv_rank (has daily close prices)
    try:
        data = client.get_iv_rank(ticker)
        rows = data.get("data", [])
        if rows:
            latest = max(rows, key=lambda r: r.get("date", ""))
            price = latest.get("close")
            if price is not None:
                return float(price)
    except Exception:
        pass

    # 3. Yahoo Finance fallback (never estimate from strike midpoint)
    try:
        import urllib.request as _urlreq
        yt = "^GSPC" if ticker.upper() == "SPX" else ticker.upper()
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yt}?interval=1d&range=2d"
        req = _urlreq.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with _urlreq.urlopen(req, timeout=8) as r:
            payload = json.loads(r.read())
        meta = payload["chart"]["result"][0]["meta"]
        price = meta.get("regularMarketPrice") or meta.get("chartPreviousClose")
        if price:
            print(f"  Spot from Yahoo Finance: {price}", file=sys.stderr)
            return float(price)
    except Exception as exc:
        print(f"  Yahoo spot fallback failed: {exc}", file=sys.stderr)

    return None


# ══════════════════════════════════════════════════════════════════
# GEX Computation
# ══════════════════════════════════════════════════════════════════

def bucket_profile(
    strike_data: List[Dict[str, Any]],
    bucket_size: int,
    spot: float,
    range_pct: float = PROFILE_RANGE_PCT,
) -> List[Dict[str, Any]]:
    """Aggregate per-strike GEX into buckets within range of spot."""
    low_bound = spot * (1 - range_pct)
    high_bound = spot * (1 + range_pct)

    buckets: Dict[float, Dict[str, float]] = defaultdict(
        lambda: {"call_gex": 0.0, "put_gex": 0.0, "net_gex": 0.0}
    )

    for row in strike_data:
        s = row["strike"]
        if s < low_bound or s > high_bound:
            continue
        b = round(s / bucket_size) * bucket_size
        buckets[b]["call_gex"] += row["call_gex"]
        buckets[b]["put_gex"] += row["put_gex"]
        buckets[b]["net_gex"] += row["net_gex"]

    result = []
    for strike in sorted(buckets.keys()):
        vals = buckets[strike]
        result.append({
            "strike": strike,
            "call_gex": round(vals["call_gex"], 2),
            "put_gex": round(vals["put_gex"], 2),
            "net_gex": round(vals["net_gex"], 2),
            "pct_from_spot": round((strike - spot) / spot * 100, 2),
            "tag": None,
        })
    return result


def compute_gex_flip(profile: List[Dict[str, Any]], spot: float) -> Optional[float]:
    """Find the GEX flip: last strike below spot where net GEX crosses from negative to positive.

    Scans buckets low→high. The flip is the transition point where
    per-bucket net GEX changes sign from negative (destabilizing) to
    positive (stabilizing), closest to and below spot.
    """
    flip = None
    for i in range(1, len(profile)):
        prev_net = profile[i - 1]["net_gex"]
        curr_net = profile[i]["net_gex"]
        strike = profile[i]["strike"]
        if prev_net < 0 and curr_net > 0 and strike <= spot:
            flip = strike
    return flip


def find_key_levels(
    profile: List[Dict[str, Any]], spot: float
) -> Dict[str, Optional[Dict[str, Any]]]:
    """Identify max magnet, 2nd magnet, max accelerator, put wall, call wall."""
    if not profile:
        return {
            "max_magnet": None,
            "second_magnet": None,
            "max_accelerator": None,
            "put_wall": None,
            "call_wall": None,
        }

    positive = [b for b in profile if b["net_gex"] > 0]
    negative = [b for b in profile if b["net_gex"] < 0]

    positive_sorted = sorted(positive, key=lambda b: b["net_gex"], reverse=True)
    negative_sorted = sorted(negative, key=lambda b: b["net_gex"])

    def _make_level(bucket):
        if bucket is None:
            return None
        return {
            "strike": bucket["strike"],
            "gamma": round(bucket["net_gex"], 2),
            "distance": round(bucket["strike"] - spot, 2),
            "distance_pct": round((bucket["strike"] - spot) / spot * 100, 2),
        }

    max_magnet = _make_level(positive_sorted[0] if positive_sorted else None)
    second_magnet = _make_level(positive_sorted[1] if len(positive_sorted) > 1 else None)
    max_accel = _make_level(negative_sorted[0] if negative_sorted else None)

    # Put wall: strike with largest absolute put_gex
    put_wall_bucket = max(profile, key=lambda b: abs(b["put_gex"])) if profile else None
    # Call wall: strike with largest call_gex
    call_wall_bucket = max(profile, key=lambda b: b["call_gex"]) if profile else None

    return {
        "max_magnet": max_magnet,
        "second_magnet": second_magnet,
        "max_accelerator": max_accel,
        "put_wall": _make_level(put_wall_bucket),
        "call_wall": _make_level(call_wall_bucket),
    }


def tag_profile(
    profile: List[Dict[str, Any]],
    spot: float,
    flip: Optional[float],
    levels: Dict[str, Optional[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Add tags to profile buckets for chart annotation."""
    spot_bucket = None
    min_dist = float("inf")
    for b in profile:
        d = abs(b["strike"] - spot)
        if d < min_dist:
            min_dist = d
            spot_bucket = b["strike"]

    tag_map: Dict[float, str] = {}
    if spot_bucket is not None:
        tag_map[spot_bucket] = "SPOT"
    if flip is not None:
        tag_map[flip] = "GEX FLIP"

    for name, level in levels.items():
        if level is None:
            continue
        label = name.upper().replace("_", " ")
        s = level["strike"]
        if s not in tag_map:
            tag_map[s] = label

    for b in profile:
        b["tag"] = tag_map.get(b["strike"])
    return profile


def compute_expected_range(
    spot: float, atm_iv: Optional[float]
) -> Dict[str, Any]:
    """Compute 1-day expected range from ATM IV."""
    if atm_iv is None or atm_iv <= 0:
        return {"low": None, "high": None, "iv_1d": None}
    iv_1d = atm_iv / math.sqrt(TRADING_DAYS_PER_YEAR)
    move = spot * iv_1d
    return {
        "low": round(spot - move, 2),
        "high": round(spot + move, 2),
        "iv_1d": round(iv_1d * 100, 4),
    }


def compute_directional_bias(
    spot: float,
    flip: Optional[float],
    net_gex: float,
    levels: Dict[str, Optional[Dict[str, Any]]],
    days_above_flip: int,
) -> Dict[str, Any]:
    """Determine directional bias heuristic."""
    reasons = []

    if flip is None:
        return {
            "direction": "NEUTRAL",
            "reasons": ["GEX flip not computable"],
            "days_above_flip": 0,
            "flip_migration": [],
        }

    above_flip = spot > flip
    magnet_above = (
        levels.get("max_magnet") is not None
        and levels["max_magnet"]["strike"] > spot
    )

    if above_flip and net_gex > 0 and magnet_above:
        direction = "BULL"
        reasons.append(f"Spot above flip ({flip:.0f})")
        reasons.append("Net GEX positive (stabilizing)")
        reasons.append(f"Max magnet at {levels['max_magnet']['strike']:.0f} pulls higher")
    elif above_flip and magnet_above:
        direction = "CAUTIOUS_BULL"
        reasons.append(f"Spot above flip ({flip:.0f})")
        if net_gex < 0:
            reasons.append("Net GEX still negative")
        if magnet_above:
            reasons.append(f"Magnet at {levels['max_magnet']['strike']:.0f} above spot")
    elif not above_flip and net_gex < 0:
        direction = "BEAR"
        reasons.append(f"Spot below flip ({flip:.0f})")
        reasons.append("Net GEX negative (destabilizing)")
        accel = levels.get("max_accelerator")
        if accel and accel["strike"] < spot:
            reasons.append(f"Accelerator at {accel['strike']:.0f} below")
    elif not above_flip:
        direction = "CAUTIOUS_BEAR"
        reasons.append(f"Spot below flip ({flip:.0f})")
    else:
        direction = "NEUTRAL"
        reasons.append("Near flip level")

    if abs(days_above_flip) >= 3:
        side = "above" if days_above_flip > 0 else "below"
        reasons.append(f"{abs(days_above_flip)} consecutive days {side} flip")

    return {
        "direction": direction,
        "reasons": reasons,
        "days_above_flip": days_above_flip,
        "flip_migration": [],
    }


def compute_days_above_flip(
    history: List[Dict[str, Any]],
) -> int:
    """Count consecutive sessions where spot was above/below the GEX flip.
    Returns positive for above, negative for below."""
    if not history:
        return 0
    count = 0
    for entry in reversed(history):
        spot_h = entry.get("spot")
        flip_h = entry.get("gex_flip")
        if spot_h is None or flip_h is None:
            break
        if spot_h > flip_h:
            if count < 0:
                break
            count += 1
        else:
            if count > 0:
                break
            count -= 1
    return count


# ══════════════════════════════════════════════════════════════════
# History Building
# ══════════════════════════════════════════════════════════════════

def build_history_from_cache(cache_path: Path) -> List[Dict[str, Any]]:
    """Load prior history from cache file for flip migration and day counting."""
    if not cache_path.exists():
        return []
    try:
        with open(cache_path) as f:
            data = json.load(f)
        return data.get("history", [])
    except Exception:
        return []


def merge_history(
    prior: List[Dict[str, Any]],
    current_entry: Dict[str, Any],
    max_days: int = HISTORY_DAYS,
) -> List[Dict[str, Any]]:
    """Merge current day's data into history, deduplicating by date."""
    by_date = {h["date"]: h for h in prior}
    by_date[current_entry["date"]] = current_entry
    sorted_entries = sorted(by_date.values(), key=lambda h: h["date"])
    return sorted_entries[-max_days:]


# ══════════════════════════════════════════════════════════════════
# Market Hours
# ══════════════════════════════════════════════════════════════════

def is_market_open() -> bool:
    """Check if US equity markets are currently open."""
    import zoneinfo
    try:
        et = zoneinfo.ZoneInfo("America/New_York")
    except Exception:
        from datetime import timezone as tz, timedelta
        now_utc = datetime.now(tz.utc)
        et_offset = timedelta(hours=-5)
        now_et = now_utc + et_offset
        return now_et.weekday() < 5 and 9 * 60 + 30 <= now_et.hour * 60 + now_et.minute <= 16 * 60

    now_et = datetime.now(et)
    if now_et.weekday() >= 5:
        return False
    minutes = now_et.hour * 60 + now_et.minute
    return 9 * 60 + 30 <= minutes <= 16 * 60


# ══════════════════════════════════════════════════════════════════
# Main Build
# ══════════════════════════════════════════════════════════════════

def build_gex_output(
    ticker: str,
    strike_data: List[Dict[str, Any]],
    aggregate_history: List[Dict[str, Any]],
    spot: float,
    close: Optional[float],
    atm_iv: Optional[float],
    vol_pc: Optional[float],
    prior_history: List[Dict[str, Any]],
    market_open: bool,
    iv_rank: Optional[float] = None,
    mq: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the full GEX output JSON."""
    bucket_size = _bucket_size_for(ticker, spot)

    # Bucket the profile
    profile = bucket_profile(strike_data, bucket_size, spot)

    # Compute levels
    flip = compute_gex_flip(profile, spot)
    levels = find_key_levels(profile, spot)

    # Add flip as a level entry
    flip_level = None
    if flip is not None:
        flip_level = {
            "strike": flip,
            "gamma": 0.0,
            "distance": round(flip - spot, 2),
            "distance_pct": round((flip - spot) / spot * 100, 2),
        }

    # Tag profile for chart annotations
    profile = tag_profile(profile, spot, flip, levels)

    # Aggregate net values
    net_gex = sum(row["net_gex"] for row in strike_data)
    net_dex = sum(row["net_delta"] for row in strike_data)

    # Expected range
    expected_range = compute_expected_range(spot, atm_iv)

    # Today's date (ET)
    import zoneinfo
    try:
        et = zoneinfo.ZoneInfo("America/New_York")
        today_et = datetime.now(et).strftime("%Y-%m-%d")
    except Exception:
        today_et = datetime.utcnow().strftime("%Y-%m-%d")

    # Build today's history entry
    today_entry = {
        "date": today_et,
        "net_gex": round(net_gex, 2),
        "net_dex": round(net_dex, 2),
        "gex_flip": flip,
        "spot": spot,
        "atm_iv": round(atm_iv * 100, 2) if atm_iv else None,
        "vol_pc": round(vol_pc, 4) if vol_pc else None,
        "bias": None,  # filled after bias computation
    }

    # Merge with prior history
    history = merge_history(prior_history, today_entry)

    # Compute days above/below flip
    days_above = compute_days_above_flip(history)

    # Flip migration from history
    flip_migration = []
    for h in history[-5:]:
        if h.get("gex_flip") is not None:
            flip_migration.append({"date": h["date"], "flip": h["gex_flip"]})

    # Directional bias
    bias = compute_directional_bias(spot, flip, net_gex, levels, days_above)
    bias["flip_migration"] = flip_migration

    # Update today's bias in history
    today_entry["bias"] = bias["direction"]

    # Day change
    day_change = round(spot - close, 2) if close else None
    day_change_pct = round((spot - close) / close * 100, 4) if close else None

    # Count contracts and expirations from raw data
    data_date = strike_data[0].get("date") if strike_data else today_et

    all_levels = {**levels}
    if flip_level is not None:
        all_levels["gex_flip"] = flip_level

    # Source delta (UW vs MenthorQ level comparison)
    source_delta = compute_source_delta(all_levels, mq)

    # IV data: prefer UW 30D iv (from iv_rank endpoint); supplement with MQ
    iv_data: Dict[str, Any] = {
        "iv30d": round(atm_iv * 100, 2) if atm_iv else None,
        "iv_rank": round(iv_rank, 2) if iv_rank is not None else None,
        "hv30": round(mq["hv30"] * 100, 2) if mq and mq.get("hv30") else None,
        "mq_iv30d": round(mq["iv30d"] * 100, 2) if mq and mq.get("iv30d") else None,
        "mq_iv_rank": mq.get("iv_rank") if mq else None,
        "source": (
            "both" if (atm_iv and mq and mq.get("iv30d")) else
            "uw"   if atm_iv else
            "mq"   if (mq and mq.get("iv30d")) else
            None
        ),
    }

    return {
        "scan_time": datetime.now().isoformat(),
        "market_open": market_open,
        "ticker": ticker.upper(),
        "spot": spot,
        "close": close,
        "day_change": day_change,
        "day_change_pct": day_change_pct,
        "data_date": data_date if isinstance(data_date, str) else today_et,
        "net_gex": round(net_gex, 2),
        "net_dex": round(net_dex, 2),
        "atm_iv": round(atm_iv * 100, 2) if atm_iv else None,
        "vol_pc": round(vol_pc, 4) if vol_pc else None,
        "levels": all_levels,
        "profile": profile,
        "expected_range": expected_range,
        "bias": bias,
        "history": history,
        "iv": iv_data,
        "mq": mq,
        "source_delta": source_delta,
    }


# ══════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Gamma Exposure (GEX) Levels Scanner",
    )
    parser.add_argument("--ticker", default="SPX", help="Ticker (default: SPX)")
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    parser.add_argument("--no-cache", action="store_true", help="Skip reading prior cache")
    parser.add_argument("--no-mq", action="store_true", help="Skip MenthorQ enrichment")
    args = parser.parse_args()

    ticker = args.ticker.upper()
    market_open = is_market_open()

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"GEX SCANNER — {ticker}", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    if not market_open:
        print("  Market closed — using last available data.", file=sys.stderr)

    t_start = time.time()

    # Fetch all data from UW
    print("  Fetching UW data...", file=sys.stderr)
    client = _get_uw_client()

    try:
        print("  Fetching strike-level GEX...", file=sys.stderr)
        strike_data = fetch_strike_gex(client, ticker)
        print(f"  Got {len(strike_data)} strikes", file=sys.stderr)

        if not strike_data:
            print("  FATAL: No strike data returned", file=sys.stderr)
            sys.exit(1)

        print("  Fetching aggregate GEX history...", file=sys.stderr)
        agg_history = fetch_aggregate_gex(client, ticker)
        print(f"  Got {len(agg_history)} history days", file=sys.stderr)

        print("  Fetching spot price...", file=sys.stderr)
        spot = fetch_spot_price(client, ticker)
        if spot is None:
            print("  FATAL: Could not determine spot price (UW + Yahoo both failed)", file=sys.stderr)
            sys.exit(1)
        print(f"  Spot: {spot}", file=sys.stderr)

        close = spot  # UW doesn't separate last vs close; same when market closed

        print("  Fetching 30D IV + IV rank...", file=sys.stderr)
        atm_iv = fetch_atm_iv(client, ticker, spot)
        iv_rank_val = fetch_iv_rank(client, ticker)
        if atm_iv:
            print(f"  IV 30D: {atm_iv*100:.1f}%  IV Rank: {iv_rank_val:.1f}%" if iv_rank_val else f"  IV 30D: {atm_iv*100:.1f}%", file=sys.stderr)

        print("  Fetching Vol P/C...", file=sys.stderr)
        vol_pc = fetch_vol_pc(client, ticker)
        if vol_pc:
            print(f"  Vol P/C: {vol_pc:.2f}", file=sys.stderr)

    finally:
        if hasattr(client, "close"):
            client.close()

    # MenthorQ enrichment (optional, Playwright-based)
    mq_data: Optional[Dict[str, Any]] = None
    if not args.no_mq:
        print("  Fetching MenthorQ key levels...", file=sys.stderr)
        mq_data = fetch_mq_levels(ticker)
        if mq_data:
            hvl = mq_data.get("hvl")
            cr  = mq_data.get("call_resistance_all")
            ps  = mq_data.get("put_support_all")
            mq_iv = mq_data.get("iv30d")
            print(
                f"  MQ HVL={hvl}  CallResist={cr}  PutSupport={ps}"
                + (f"  IV30D={mq_iv:.1f}%" if mq_iv else ""),
                file=sys.stderr,
            )
        else:
            print("  MQ: unavailable (proceeding with UW only)", file=sys.stderr)
    else:
        print("  MQ: skipped (--no-mq)", file=sys.stderr)

    # Load prior history from cache
    cache_path = _PROJECT_DIR / "data" / "gex.json"
    prior_history = []
    if not args.no_cache:
        prior_history = build_history_from_cache(cache_path)

    # Build output
    result = build_gex_output(
        ticker=ticker,
        strike_data=strike_data,
        aggregate_history=agg_history,
        spot=spot,
        close=close,
        atm_iv=atm_iv,
        vol_pc=vol_pc,
        prior_history=prior_history,
        market_open=market_open,
        iv_rank=iv_rank_val,
        mq=mq_data,
    )

    elapsed = time.time() - t_start
    print(f"\n  Scan completed in {elapsed:.1f}s", file=sys.stderr)

    # Output
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_summary(result)

    # Always write cache
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    from utils.atomic_io import atomic_save
    atomic_save(str(cache_path), result)
    print(f"  Cache written: {cache_path}", file=sys.stderr)


def _print_summary(result: Dict[str, Any]) -> None:
    """Print human-readable GEX summary."""
    t = result["ticker"]
    spot = result["spot"]
    flip = result["levels"].get("gex_flip", {})
    flip_strike = flip.get("strike") if flip else None

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"GEX LEVELS — {t} | {result.get('data_date', '?')}", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    print(f"  Spot        : {spot:,.2f}", file=sys.stderr)
    print(f"  Net GEX     : {result['net_gex']:+,.2f}", file=sys.stderr)
    print(f"  Net DEX     : {result['net_dex']:+,.2f}", file=sys.stderr)
    iv = result.get("iv", {})
    if iv.get("iv30d"):
        rank_str = f"  (rank {iv['iv_rank']:.0f}%)" if iv.get("iv_rank") else ""
        hv_str = f"  HV30={iv['hv30']:.1f}%" if iv.get("hv30") else ""
        print(f"  IV 30D (UW) : {iv['iv30d']:.1f}%{rank_str}{hv_str}", file=sys.stderr)
    if iv.get("mq_iv30d"):
        print(f"  IV 30D (MQ) : {iv['mq_iv30d']:.1f}%", file=sys.stderr)
    if result.get("vol_pc"):
        print(f"  Vol P/C     : {result['vol_pc']:.2f}", file=sys.stderr)
    if flip_strike:
        print(f"  GEX Flip    : {flip_strike:,.0f} ({flip.get('distance_pct', 0):+.1f}%)", file=sys.stderr)

    levels = result.get("levels", {})
    for name in ("max_magnet", "second_magnet", "max_accelerator", "put_wall"):
        lvl = levels.get(name)
        if lvl:
            label = name.replace("_", " ").title()
            print(f"  {label:14s}: {lvl['strike']:,.0f} (gamma={lvl['gamma']:+,.2f}, {lvl['distance_pct']:+.1f}%)", file=sys.stderr)

    bias = result.get("bias", {})
    print(f"\n  Bias: {bias.get('direction', 'UNKNOWN')}", file=sys.stderr)
    for r in bias.get("reasons", []):
        print(f"    - {r}", file=sys.stderr)

    er = result.get("expected_range", {})
    if er.get("low") and er.get("high"):
        print(f"\n  Expected Range: {er['low']:,.2f} — {er['high']:,.2f}", file=sys.stderr)

    print(f"\n{'='*60}\n", file=sys.stderr)


if __name__ == "__main__":
    main()
