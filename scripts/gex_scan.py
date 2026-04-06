#!/usr/bin/env python3
"""Gamma Exposure (GEX) Levels Scanner.

Fetches dealer gamma exposure by strike from Unusual Whales,
computes key levels (flip, magnets, accelerators), and outputs
a structured JSON signal for the regime dashboard.

Data sources:
  1. Unusual Whales — greek-exposure/strike, greek-exposure (aggregate),
     greeks (ATM IV), screener/stocks (vol P/C)

Usage:
    python3 scripts/gex_scan.py --json --ticker SPX
    python3 scripts/gex_scan.py --json --ticker SPY
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


def fetch_atm_iv(client, ticker: str, spot: float) -> Optional[float]:
    """Fetch ATM IV from UW greeks endpoint using nearest expiry."""
    try:
        data = client.get_greeks(ticker)
        rows = data.get("data", [])
        if not rows:
            return None
        nearest = min(rows, key=lambda r: abs(float(r.get("strike", 0)) - spot))
        call_iv = float(nearest.get("call_volatility", 0))
        put_iv = float(nearest.get("put_volatility", 0))
        atm_iv = (call_iv + put_iv) / 2 if call_iv and put_iv else call_iv or put_iv
        return atm_iv if atm_iv > 0 else None
    except Exception as exc:
        print(f"  ATM IV fetch failed: {exc}", file=sys.stderr)
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


def fetch_spot_price(client, ticker: str) -> Optional[float]:
    """Get spot price from UW stock info."""
    try:
        data = client.get_stock_info(ticker)
        info = data.get("data", [{}])
        if isinstance(info, list) and info:
            info = info[0]
        price = info.get("last", info.get("close", info.get("price")))
        return float(price) if price is not None else None
    except Exception as exc:
        print(f"  Spot price fetch failed: {exc}", file=sys.stderr)
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

        # Spot price: try to derive from strike data midpoint or fetch
        print("  Fetching spot price...", file=sys.stderr)
        spot = fetch_spot_price(client, ticker)
        if spot is None:
            # Fallback: estimate from strike data
            strikes = [r["strike"] for r in strike_data]
            spot = (min(strikes) + max(strikes)) / 2
            print(f"  Using estimated spot: {spot}", file=sys.stderr)
        else:
            print(f"  Spot: {spot}", file=sys.stderr)

        close = spot  # UW doesn't separate last vs close; same when market closed

        print("  Fetching ATM IV...", file=sys.stderr)
        atm_iv = fetch_atm_iv(client, ticker, spot)
        if atm_iv:
            print(f"  ATM IV: {atm_iv*100:.1f}%", file=sys.stderr)

        print("  Fetching Vol P/C...", file=sys.stderr)
        vol_pc = fetch_vol_pc(client, ticker)
        if vol_pc:
            print(f"  Vol P/C: {vol_pc:.2f}", file=sys.stderr)

    finally:
        if hasattr(client, "close"):
            client.close()

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
    if result.get("atm_iv"):
        print(f"  ATM IV      : {result['atm_iv']:.1f}%", file=sys.stderr)
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
