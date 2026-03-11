#!/usr/bin/env python3
"""Crash Risk Index (CRI) Scanner.

Detects systematic crash risk by scoring four components: VIX, VVIX,
COR1M implied correlation, and SPX momentum. When the composite CRI
score is HIGH/CRITICAL and the crash trigger conditions hold (SPX below
100d MA, realized vol > 25%, COR1M > 60), CTAs are forced to deleverage
— creating predictable selling cascades.

Strategy spec: docs/strategies.md (Strategy 6)

Data sources (priority order):
  1. Interactive Brokers — Index('VIX','CBOE'), Index('VVIX','CBOE'),
     Index('COR1M','CBOE'), Stock('SPY','SMART','USD')
  2. Unusual Whales — OHLC for SPY only. Does NOT support VIX/VVIX/COR1M.
  3. Cboe COR1M dashboard historical feed — COR1M only, via the official
     dashboard endpoint used by the site's download workflow.
  4. Yahoo Finance — ABSOLUTE LAST RESORT. Only for remaining gaps after
     higher-priority sources fail; COR1M reaches Yahoo only if IB + Cboe fail.

Usage:
    python3 scripts/cri_scan.py                 # HTML report (opens in browser)
    python3 scripts/cri_scan.py --json           # JSON to stdout
    python3 scripts/cri_scan.py --no-open        # HTML report, don't open browser
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# ── path setup ────────────────────────────────────────────────────
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

# ── constants ─────────────────────────────────────────────────────
ALL_TICKERS = ["VIX", "VVIX", "SPY", "COR1M"]

MA_WINDOW = 100        # SPX moving average window
VOL_WINDOW = 20        # Realized vol window (annualized)
MIN_BARS = MA_WINDOW + 20  # Minimum price history

# CTA model parameters
CTA_VOL_TARGET = 10.0  # 10% target volatility
CTA_MAX_EXPOSURE = 200.0  # Max 200% exposure (leverage)
CTA_AUM_BN = 400.0     # Estimated CTA AUM in billions
CRI_IB_HISTORY_CLIENT_IDS = (50, 52, 53, 54, 57)
CRI_IB_QUOTE_CLIENT_IDS = (51, 58, 59, 60, 61)

# Yahoo-to-IB ticker map
YAHOO_TICKERS = {
    "VIX": "^VIX",
    "VVIX": "^VVIX",
    "COR1M": "^COR1M",
}
CBOE_COR1M_HISTORICAL_URL = "https://cdn.cboe.com/api/global/delayed_quotes/charts/historical/_COR1M.json"


# ══════════════════════════════════════════════════════════════════
# Data Fetching
# ══════════════════════════════════════════════════════════════════

def _valid_quote_value(value: Any) -> Optional[float]:
    """Return a positive finite quote value, else None."""
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric) or math.isinf(numeric) or numeric <= 0:
        return None
    return numeric

def _connect_ib_with_retry(
    ib: Any,
    client_ids: Tuple[int, ...],
    ports: Tuple[int, ...] = (4001, 7497),
    timeout: int = 8,
) -> bool:
    """Connect to IB by cycling a small client-id pool before giving up."""
    for client_id in client_ids:
        for port in ports:
            try:
                ib.connect("127.0.0.1", port, clientId=client_id, timeout=timeout)
                return True
            except Exception as exc:
                print(
                    f"  IB connect failed on port {port} clientId {client_id}: {exc}",
                    file=sys.stderr,
                )
    return False

def _fetch_ib(tickers: List[str]) -> Dict[str, List[Tuple[str, float]]]:
    """Fetch 1Y daily bars from IB concurrently using asyncio.gather.

    Fires all qualify + historical data requests in parallel.

    Returns {ticker: [(date_str, close), ...]}.
    """
    try:
        from ib_insync import IB, Index, Stock
    except ImportError:
        return {}

    ib = IB()
    if not _connect_ib_with_retry(ib, CRI_IB_HISTORY_CLIENT_IDS):
        return {}

    import asyncio

    results: Dict[str, List[Tuple[str, float]]] = {}
    failed: List[str] = []

    async def _qualify_and_fetch(ticker: str) -> None:
        """Qualify contract and fetch historical data for a single ticker."""
        if ticker in ("VIX", "VVIX", "COR1M"):
            contract = Index(ticker, "CBOE")
        else:
            contract = Stock(ticker, "SMART", "USD")
        try:
            await ib.qualifyContractsAsync(contract)
            bars = await ib.reqHistoricalDataAsync(
                contract,
                endDateTime="",
                durationStr="1 Y",
                barSizeSetting="1 day",
                whatToShow="TRADES",
                useRTH=True,
                formatDate=1,
            )
            if bars:
                results[ticker] = [
                    (str(b.date), float(b.close)) for b in bars
                ]
                print(f"  IB: {ticker} — {len(bars)} bars", file=sys.stderr)
            else:
                failed.append(ticker)
                print(f"  IB: {ticker} — no bars returned", file=sys.stderr)
        except Exception as exc:
            failed.append(ticker)
            print(f"  IB: {ticker} failed — {exc}", file=sys.stderr)

    async def _fetch_all_concurrent() -> None:
        tasks = [_qualify_and_fetch(t) for t in tickers]
        await asyncio.gather(*tasks)

    try:
        ib.run(_fetch_all_concurrent())
    finally:
        ib.disconnect()

    return results


def _fetch_uw(tickers: List[str]) -> Dict[str, List[Tuple[str, float]]]:
    """Fetch 1Y daily bars from Unusual Whales OHLC endpoint.

    UW supports stocks and ETFs but NOT indices (VIX, VVIX, COR1M).
    Returns {ticker: [(date_str, close), ...]} for successful fetches.
    """
    try:
        from clients.uw_client import UWClient
    except ImportError:
        return {}

    # UW cannot serve index data
    INDEX_TICKERS = {"VIX", "VVIX", "COR1M"}
    fetchable = [t for t in tickers if t not in INDEX_TICKERS]
    if not fetchable:
        return {}

    results: Dict[str, List[Tuple[str, float]]] = {}
    try:
        with UWClient() as uw:
            for ticker in fetchable:
                try:
                    data = uw.get_stock_ohlc(ticker, candle_size="1d")
                    bars = data.get("data", [])
                    if bars:
                        parsed = [
                            (b["date"], float(b["close"]))
                            for b in bars
                            if b.get("close") is not None
                        ]
                        if parsed:
                            results[ticker] = parsed
                            print(f"  UW: {ticker} — {len(parsed)} bars", file=sys.stderr)
                except Exception as exc:
                    print(f"  UW: {ticker} failed — {exc}", file=sys.stderr)
    except Exception as exc:
        print(f"  UW connection failed — {exc}", file=sys.stderr)

    return results


def _fetch_yahoo_chart_result(ticker: str, days: int = 400) -> Optional[Dict[str, Any]]:
    """Fetch Yahoo chart payload for a ticker."""
    from urllib.request import Request, urlopen

    yahoo_sym = YAHOO_TICKERS.get(ticker, ticker)
    end = int(datetime.now().timestamp())
    start = int((datetime.now() - timedelta(days=days)).timestamp())
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_sym}"
        f"?period1={start}&period2={end}&interval=1d"
    )
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(req, timeout=10) as resp:
            data = json.load(resp)
        return data["chart"]["result"][0]
    except Exception as exc:
        print(f"  Yahoo: {ticker} ({yahoo_sym}) failed — {exc}", file=sys.stderr)
        return None


@lru_cache(maxsize=1)
def _fetch_cboe_cor1m_payload() -> Optional[Dict[str, Any]]:
    """Fetch the official COR1M history payload used by the Cboe dashboard."""
    from urllib.request import Request, urlopen

    req = Request(CBOE_COR1M_HISTORICAL_URL, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(req, timeout=10) as resp:
            data = json.load(resp)
    except Exception as exc:
        print(f"  CBOE: COR1M historical feed failed — {exc}", file=sys.stderr)
        return None

    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        print("  CBOE: COR1M historical feed returned an unexpected payload", file=sys.stderr)
        return None

    return data


def _fetch_cboe_cor1m() -> List[Tuple[str, float]]:
    """Fetch COR1M historical bars from the official Cboe dashboard feed."""
    payload = _fetch_cboe_cor1m_payload()
    if not payload:
        return []

    bars: List[Tuple[str, float]] = []
    for row in payload.get("data", []):
        if not isinstance(row, dict):
            continue
        date = row.get("date")
        close = row.get("close")
        if not isinstance(date, str):
            continue
        try:
            close_value = float(close)
        except (TypeError, ValueError):
            continue
        if math.isnan(close_value) or math.isinf(close_value):
            continue
        bars.append((date, close_value))

    return bars


def _fetch_cboe_cor1m_current_quote() -> Optional[float]:
    """Use the last Cboe historical close as the official COR1M fallback quote."""
    bars = _fetch_cboe_cor1m()
    if not bars:
        return None
    return bars[-1][1]


def _fetch_yahoo(ticker: str, days: int = 400) -> List[Tuple[str, float]]:
    """Fetch daily bars from Yahoo Finance.  Returns [(date_str, close), ...]."""
    result = _fetch_yahoo_chart_result(ticker, days=days)
    if not result:
        return []

    timestamps = result.get("timestamp", [])
    quote = result.get("indicators", {}).get("quote", [{}])
    closes = quote[0].get("close", []) if quote else []
    bars = []
    for ts, c in zip(timestamps, closes):
        if c is not None:
            dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            bars.append((dt, float(c)))
    return bars


def _fetch_yahoo_current_quote(ticker: str) -> Optional[float]:
    """Fetch the current quote/last value from Yahoo chart metadata."""
    result = _fetch_yahoo_chart_result(ticker, days=30)
    if not result:
        return None

    meta = result.get("meta", {})
    for key in ("regularMarketPrice", "regularMarketPreviousClose", "previousClose", "chartPreviousClose"):
        value = _valid_quote_value(meta.get(key))
        if value is not None:
            return value
    return None


def _extract_ib_quote_value(ticker: Any) -> Optional[float]:
    """Return an actionable current quote, ignoring stale close-only snapshots."""
    last = _valid_quote_value(getattr(ticker, "last", None))
    if last is not None:
        return last

    bid = _valid_quote_value(getattr(ticker, "bid", None))
    ask = _valid_quote_value(getattr(ticker, "ask", None))
    if bid is not None and ask is not None:
        return (bid + ask) / 2.0

    for candidate in (bid, ask):
        value = _valid_quote_value(candidate)
        if value is not None:
            return value
    return None


def _fetch_ib_current_quote(ticker: str) -> Optional[float]:
    """Fetch a current quote from IB, trying live then delayed data."""
    try:
        from ib_insync import IB, Index
    except ImportError:
        return None

    ib = IB()
    if not _connect_ib_with_retry(ib, CRI_IB_QUOTE_CLIENT_IDS):
        return None

    contract = Index(ticker, "CBOE")
    try:
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            return None
        contract = qualified[0]

        for data_type in (1, 3, 4):
            try:
                ib.reqMarketDataType(data_type)
                snapshot = ib.reqMktData(contract, "", True, False)
                ib.sleep(2)
                value = _extract_ib_quote_value(snapshot)
                ib.cancelMktData(contract)
                if value is not None:
                    return value
            except Exception:
                continue
        return None
    finally:
        ib.disconnect()


def select_cor1m_current_quote(
    ib_quote: Optional[float],
    yahoo_quote: Optional[float] = None,
    cboe_quote: Optional[float] = None,
    discrepancy_threshold: float = 1.0,
) -> Optional[float]:
    """Choose the most reliable current COR1M level.

    IB is preferred when it is the only source or when it agrees with the
    official Cboe dashboard feed. If IB diverges materially from Cboe, prefer
    Cboe. Yahoo remains an absolute last resort only when Cboe is unavailable.
    """
    if ib_quote is None:
        return cboe_quote if cboe_quote is not None else yahoo_quote
    if cboe_quote is not None:
        if abs(ib_quote - cboe_quote) > discrepancy_threshold:
            return cboe_quote
        return ib_quote
    if yahoo_quote is None:
        return ib_quote
    if abs(ib_quote - yahoo_quote) > discrepancy_threshold:
        return yahoo_quote
    return ib_quote


def fetch_cor1m_current_quote() -> Optional[float]:
    """Fetch the current COR1M level separate from daily history."""
    ib_quote = _fetch_ib_current_quote("COR1M")
    cboe_quote = _fetch_cboe_cor1m_current_quote()
    yahoo_quote = _fetch_yahoo_current_quote("COR1M")

    if ib_quote is not None:
        print(f"  IB: COR1M current quote {ib_quote:.2f}", file=sys.stderr)
    if cboe_quote is not None:
        print(f"  CBOE: COR1M current quote {cboe_quote:.2f}", file=sys.stderr)
    if yahoo_quote is not None:
        print(f"  Yahoo: COR1M current quote {yahoo_quote:.2f}", file=sys.stderr)

    selected = select_cor1m_current_quote(ib_quote, yahoo_quote=yahoo_quote, cboe_quote=cboe_quote)
    if ib_quote is not None and cboe_quote is not None and selected == cboe_quote and abs(ib_quote - cboe_quote) > 1.0:
        print(
            f"  COR1M quote discrepancy detected (IB {ib_quote:.2f} vs CBOE {cboe_quote:.2f}) — using CBOE current value",
            file=sys.stderr,
        )
    elif ib_quote is not None and yahoo_quote is not None and selected == yahoo_quote and abs(ib_quote - yahoo_quote) > 1.0:
        print(
            f"  COR1M quote discrepancy detected (IB {ib_quote:.2f} vs Yahoo {yahoo_quote:.2f}) — using Yahoo current value",
            file=sys.stderr,
        )
    elif selected is None:
        print("  ERROR: No current COR1M quote available", file=sys.stderr)

    return selected


def fetch_all(tickers: List[str]) -> Tuple[Dict[str, np.ndarray], List[str]]:
    """Fetch close prices for all tickers. IB first, then source-specific fallbacks.

    Returns ({ticker: np.array of closes}, [common_dates]).
    """
    print("  Attempting IB connection...", file=sys.stderr)
    ib_data = _fetch_ib(tickers)

    raw: Dict[str, List[Tuple[str, float]]] = {}
    fallback_needed: List[str] = []

    for t in tickers:
        if t in ib_data and len(ib_data[t]) >= MIN_BARS:
            raw[t] = ib_data[t]
        else:
            if t in ib_data:
                print(f"  IB: {t} only {len(ib_data[t])} bars (need {MIN_BARS}), trying fallbacks", file=sys.stderr)
            fallback_needed.append(t)

    # Priority 2: Unusual Whales (stocks/ETFs only, not VIX/VVIX)
    if fallback_needed:
        print("  Trying Unusual Whales for fallback tickers...", file=sys.stderr)
        uw_data = _fetch_uw(fallback_needed)
        still_needed: List[str] = []
        for t in fallback_needed:
            if t in uw_data and len(uw_data[t]) >= MIN_BARS:
                raw[t] = uw_data[t]
            else:
                still_needed.append(t)
        fallback_needed = still_needed

    # Priority 3: official Cboe dashboard history for COR1M
    if "COR1M" in fallback_needed:
        print("  Trying CBOE dashboard fallback for COR1M...", file=sys.stderr)
        cboe_cor1m = _fetch_cboe_cor1m()
        if len(cboe_cor1m) >= MIN_BARS:
            raw["COR1M"] = cboe_cor1m
            print(f"  CBOE: COR1M — {len(cboe_cor1m)} bars", file=sys.stderr)
        elif cboe_cor1m:
            print(f"  CBOE: COR1M only {len(cboe_cor1m)} bars (need {MIN_BARS}), trying Yahoo", file=sys.stderr)
        fallback_needed = [ticker for ticker in fallback_needed if ticker != "COR1M" or "COR1M" not in raw]

    # Priority 4 (LAST RESORT): Yahoo Finance
    for t in fallback_needed:
        print(f"  LAST RESORT: Yahoo for {t}", file=sys.stderr)
        time.sleep(0.5)  # Rate limit Yahoo
        yahoo = _fetch_yahoo(t)
        if yahoo:
            raw[t] = yahoo
            print(f"  Yahoo: {t} — {len(yahoo)} bars", file=sys.stderr)
        else:
            print(f"  ERROR: No data for {t}", file=sys.stderr)

    if len(raw) < len(tickers):
        missing = set(tickers) - set(raw.keys())
        print(f"  FATAL: Missing data for {missing}", file=sys.stderr)
        sys.exit(1)

    # Align by common dates
    date_sets = [set(d for d, _ in bars) for bars in raw.values()]
    common_dates = sorted(set.intersection(*date_sets))
    if len(common_dates) < MIN_BARS:
        print(
            f"  FATAL: Only {len(common_dates)} common dates (need {MIN_BARS})",
            file=sys.stderr,
        )
        sys.exit(1)

    aligned: Dict[str, np.ndarray] = {}
    for t in tickers:
        lookup = {d: c for d, c in raw[t]}
        aligned[t] = np.array([lookup[d] for d in common_dates])

    print(f"  Aligned: {len(common_dates)} common trading days", file=sys.stderr)
    return aligned, common_dates


# ══════════════════════════════════════════════════════════════════
# COR1M Implied Correlation
# ══════════════════════════════════════════════════════════════════

def cor1m_level_and_change(
    cor1m_values: np.ndarray,
    current_override: Optional[float] = None,
) -> Tuple[float, float]:
    """Return current COR1M level and 5-session change.

    COR1M is already quoted as a percentage index (e.g. 31.1 means
    31.1% implied average correlation), so no scaling is applied here.
    """
    if cor1m_values is None or len(cor1m_values) == 0:
        return float("nan"), float("nan")
    if np.all(np.isnan(cor1m_values)):
        return float("nan"), float("nan")

    current = current_override if current_override is not None else float(cor1m_values[-1])
    if math.isnan(current):
        return float("nan"), float("nan")

    if len(cor1m_values) >= 6:
        prev = float(cor1m_values[-6])
        change = current - prev if not math.isnan(prev) else float("nan")
    else:
        change = float("nan")

    return current, change


# ══════════════════════════════════════════════════════════════════
# Realized Volatility
# ══════════════════════════════════════════════════════════════════

def compute_realized_vol(prices: np.ndarray, window: int = VOL_WINDOW) -> float:
    """Compute annualized realized volatility from the trailing window.

    Returns vol in percentage points (e.g. 25.0 for 25%).
    """
    if len(prices) < window + 1:
        return float("nan")
    log_returns = np.log(prices[-window:] / prices[-window - 1:-1])
    return float(np.std(log_returns, ddof=1) * np.sqrt(252) * 100)


# ══════════════════════════════════════════════════════════════════
# CRI Component Scoring (each 0-25)
# ══════════════════════════════════════════════════════════════════

def score_vix_component(vix: float, vix_5d_roc: float) -> float:
    """Score VIX component (0-25).

    Inputs:
        vix: current VIX level
        vix_5d_roc: 5-day rate of change in % (e.g. 50 means VIX rose 50%)
    """
    if math.isnan(vix) or math.isnan(vix_5d_roc):
        return 0.0

    # VIX level score (0-15): linear from 15 → 40
    level_score = np.clip((vix - 15.0) / (40.0 - 15.0) * 15.0, 0.0, 15.0)

    # VIX rate-of-change score (0-10): linear from 0% → 60%
    roc_score = np.clip(max(vix_5d_roc, 0.0) / 60.0 * 10.0, 0.0, 10.0)

    return float(np.clip(level_score + roc_score, 0.0, 25.0))


def score_vvix_component(vvix: float, vvix_vix_ratio: float) -> float:
    """Score VVIX component (0-25).

    Inputs:
        vvix: current VVIX level
        vvix_vix_ratio: VVIX / VIX ratio
    """
    if math.isnan(vvix) or math.isnan(vvix_vix_ratio):
        return 0.0

    # VVIX level score (0-17): linear from 90 → 140
    level_score = np.clip((vvix - 90.0) / (140.0 - 90.0) * 17.0, 0.0, 17.0)

    # VVIX/VIX ratio score (0-8): high ratio (> 8) means convexity demand
    ratio_score = np.clip((vvix_vix_ratio - 5.0) / (8.0 - 5.0) * 8.0, 0.0, 8.0)

    return float(np.clip(level_score + ratio_score, 0.0, 25.0))


def score_correlation_component(corr: float, corr_5d_change: float) -> float:
    """Score correlation component (0-25).

    Inputs:
        corr: current COR1M level (percentage points)
        corr_5d_change: 5-session change in COR1M (percentage points)
    """
    if math.isnan(corr):
        return 0.0
    if math.isnan(corr_5d_change):
        corr_5d_change = 0.0

    # COR1M level score (0-17): linear from 25 → 70
    level_score = np.clip((corr - 25.0) / (70.0 - 25.0) * 17.0, 0.0, 17.0)

    # COR1M spike score (0-8): linear from 0 → 20 points of 5d change
    spike_score = np.clip(max(corr_5d_change, 0.0) / 20.0 * 8.0, 0.0, 8.0)

    return float(np.clip(level_score + spike_score, 0.0, 25.0))


def score_momentum_component(spx_distance_pct: float) -> float:
    """Score momentum component (0-25).

    Inputs:
        spx_distance_pct: SPX distance from 100d MA in % (negative = below MA)
    """
    if math.isnan(spx_distance_pct):
        return 0.0

    # Score rises as SPX goes below MA. Linear from 0% to -10%.
    if spx_distance_pct >= 0:
        return 0.0

    return float(np.clip(abs(spx_distance_pct) / 10.0 * 25.0, 0.0, 25.0))


# ══════════════════════════════════════════════════════════════════
# Composite CRI
# ══════════════════════════════════════════════════════════════════

def cri_level(score: float) -> str:
    """Classify CRI score into signal level."""
    if score < 25:
        return "LOW"
    elif score < 50:
        return "ELEVATED"
    elif score < 75:
        return "HIGH"
    else:
        return "CRITICAL"


def compute_cri(
    vix: float, vix_5d_roc: float,
    vvix: float, vvix_vix_ratio: float,
    corr: float, corr_5d_change: float,
    spx_distance_pct: float,
) -> Dict[str, Any]:
    """Compute the CRI composite score (0-100) from four components."""
    vix_score = score_vix_component(vix, vix_5d_roc)
    vvix_score = score_vvix_component(vvix, vvix_vix_ratio)
    corr_score = score_correlation_component(corr, corr_5d_change)
    momentum_score = score_momentum_component(spx_distance_pct)

    total = vix_score + vvix_score + corr_score + momentum_score
    total = float(np.clip(total, 0.0, 100.0))

    return {
        "score": round(total, 1),
        "level": cri_level(total),
        "components": {
            "vix": round(vix_score, 1),
            "vvix": round(vvix_score, 1),
            "correlation": round(corr_score, 1),
            "momentum": round(momentum_score, 1),
        },
    }


# ══════════════════════════════════════════════════════════════════
# CTA Exposure Model
# ══════════════════════════════════════════════════════════════════

def cta_exposure_model(
    realized_vol: float,
    vol_target: float = CTA_VOL_TARGET,
    aum_bn: float = CTA_AUM_BN,
) -> Dict[str, Any]:
    """Model CTA exposure based on vol-targeting.

    Exposure = vol_target / realized_vol
    Forced_reduction = max(0, 1 - Exposure)
    """
    if math.isnan(realized_vol) or realized_vol <= 0:
        return {
            "realized_vol": realized_vol if not math.isnan(realized_vol) else 0.0,
            "exposure_pct": CTA_MAX_EXPOSURE,
            "forced_reduction_pct": 0.0,
            "est_selling_bn": 0.0,
        }

    exposure = min(vol_target / realized_vol * 100.0, CTA_MAX_EXPOSURE)
    reduction = max(0.0, 1.0 - exposure / 100.0)
    est_selling = reduction * aum_bn

    return {
        "realized_vol": round(realized_vol, 2),
        "exposure_pct": round(exposure, 1),
        "forced_reduction_pct": round(reduction * 100.0, 1),
        "est_selling_bn": round(est_selling, 1),
    }


# ══════════════════════════════════════════════════════════════════
# Crash Trigger
# ══════════════════════════════════════════════════════════════════

def crash_trigger(
    spx_below_ma: bool,
    realized_vol: float,
    cor1m: float,
) -> Dict[str, Any]:
    """Evaluate the three crash trigger conditions.

    All three must fire simultaneously:
      1. SPX < 100-day MA
      2. 20d realized vol > 25% annualized
      3. COR1M implied correlation > 60
    """
    vol_ok = (not math.isnan(realized_vol)) and realized_vol > 25.0
    corr_ok = (not math.isnan(cor1m)) and cor1m > 60.0
    triggered = spx_below_ma and vol_ok and corr_ok

    return {
        "triggered": triggered,
        "conditions": {
            "spx_below_100d_ma": spx_below_ma,
            "realized_vol_gt_25": vol_ok,
            "cor1m_gt_60": corr_ok,
        },
        "values": {
            "realized_vol": round(realized_vol, 2) if not math.isnan(realized_vol) else None,
            "cor1m": round(cor1m, 2) if not math.isnan(cor1m) else None,
        },
    }


# ══════════════════════════════════════════════════════════════════
# Full Analysis
# ══════════════════════════════════════════════════════════════════

def run_analysis(
    aligned: Dict[str, np.ndarray],
    common_dates: List[str],
    current_quotes: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Run full CRI analysis on aligned price data."""
    vix = aligned["VIX"]
    vvix = aligned["VVIX"]
    spy = aligned["SPY"]
    cor1m_values = aligned["COR1M"]
    current_quotes = current_quotes or {}

    # Current values
    vix_now = float(vix[-1])
    vvix_now = float(vvix[-1])
    spy_now = float(spy[-1])

    # VIX 5-day RoC
    if len(vix) >= 6 and vix[-6] > 0:
        vix_5d_roc = (vix[-1] / vix[-6] - 1) * 100
    else:
        vix_5d_roc = 0.0

    # VVIX/VIX ratio
    vvix_vix_ratio = vvix_now / vix_now if vix_now > 0 else float("nan")

    # SPX vs 100d MA
    if len(spy) >= MA_WINDOW:
        ma_100 = float(np.mean(spy[-MA_WINDOW:]))
        spx_distance_pct = (spy_now / ma_100 - 1) * 100
        spx_below_ma = spy_now < ma_100
    else:
        ma_100 = float("nan")
        spx_distance_pct = 0.0
        spx_below_ma = False

    # CBOE 1-Month Implied Correlation Index (COR1M)
    cor1m_now, cor1m_5d_change = cor1m_level_and_change(
        cor1m_values,
        current_override=current_quotes.get("COR1M"),
    )
    cor1m_previous_close = float(cor1m_values[-1]) if len(cor1m_values) > 0 else float("nan")

    # Realized vol (SPY)
    realized_vol = compute_realized_vol(spy, VOL_WINDOW)

    # CRI score
    cri = compute_cri(
        vix=vix_now, vix_5d_roc=float(vix_5d_roc),
        vvix=vvix_now, vvix_vix_ratio=float(vvix_vix_ratio),
        corr=cor1m_now, corr_5d_change=cor1m_5d_change,
        spx_distance_pct=float(spx_distance_pct),
    )

    # CTA exposure model
    cta = cta_exposure_model(realized_vol)

    # MenthorQ CTA positioning (institutional data overlay)
    menthorq_cta = None
    try:
        from fetch_menthorq_cta import load_menthorq_cache, find_by_underlying
        menthorq = load_menthorq_cache()
        if menthorq:
            main_table = menthorq.get("tables", {}).get("main", [])
            spx_entry = find_by_underlying(main_table, "S&P 500")
            menthorq_cta = {
                "date": menthorq.get("date"),
                "source": menthorq.get("source"),
                "spx": spx_entry,
                "tables": menthorq.get("tables", {}),
            }
            print(f"  MenthorQ CTA data loaded ({menthorq.get('date')})", file=sys.stderr)
    except Exception as exc:
        print(f"  MenthorQ CTA unavailable: {exc}", file=sys.stderr)

    # Crash trigger
    trigger = crash_trigger(
        spx_below_ma=spx_below_ma,
        realized_vol=realized_vol,
        cor1m=cor1m_now,
    )

    # Rolling 20-day history
    history = []
    n = len(vix)
    for i in range(max(0, n - 20), n):
        v = float(vix[i])
        vv = float(vvix[i])
        s = float(spy[i])
        # Per-day MA
        if i >= MA_WINDOW - 1:
            day_ma = float(np.mean(spy[i - MA_WINDOW + 1:i + 1]))
            day_dist = (s / day_ma - 1) * 100
        else:
            day_ma = float("nan")
            day_dist = 0.0
        # Per-day VIX RoC
        if i >= 5 and vix[i - 5] > 0:
            day_vix_roc = (vix[i] / vix[i - 5] - 1) * 100
        else:
            day_vix_roc = 0.0
        # Per-day realized vol (20d annualized)
        if i >= VOL_WINDOW:
            day_rvol = compute_realized_vol(spy[:i + 1], VOL_WINDOW)
        else:
            day_rvol = float("nan")

        history.append({
            "date": common_dates[i],
            "vix": round(v, 2),
            "vvix": round(vv, 2),
            "spy": round(s, 2),
            "cor1m": round(float(cor1m_values[i]), 2),
            "realized_vol": round(day_rvol, 2) if not math.isnan(day_rvol) else None,
            "spx_vs_ma_pct": round(float(day_dist), 2),
            "vix_5d_roc": round(float(day_vix_roc), 1),
        })

    return {
        "date": common_dates[-1],
        "vix": round(vix_now, 2),
        "vvix": round(vvix_now, 2),
        "spy": round(spy_now, 2),
        "vix_5d_roc": round(float(vix_5d_roc), 1),
        "vvix_vix_ratio": round(float(vvix_vix_ratio), 2) if not math.isnan(vvix_vix_ratio) else None,
        "spx_100d_ma": round(ma_100, 2) if not math.isnan(ma_100) else None,
        "spx_distance_pct": round(float(spx_distance_pct), 2),
        "cor1m": round(cor1m_now, 2) if not math.isnan(cor1m_now) else None,
        "cor1m_previous_close": round(cor1m_previous_close, 2) if not math.isnan(cor1m_previous_close) else None,
        "cor1m_5d_change": round(cor1m_5d_change, 2) if not math.isnan(cor1m_5d_change) else None,
        "realized_vol": round(realized_vol, 2) if not math.isnan(realized_vol) else None,
        "cri": cri,
        "cta": cta,
        "menthorq_cta": menthorq_cta,
        "crash_trigger": trigger,
        "history": history,
        # Last 40 SPY daily closes so the UI/API can rebuild the trailing
        # 20-session realized-vol history (20 windows + 20 leading closes).
        "spy_closes": [round(float(p), 4) for p in spy[-(VOL_WINDOW * 2):]],
    }


# ══════════════════════════════════════════════════════════════════
# Console Summary
# ══════════════════════════════════════════════════════════════════

def print_summary(result: Dict[str, Any], market_open: bool) -> None:
    """Print human-readable CRI summary to stderr."""
    market_note = "" if market_open else "  [Market closed — using last available data]"
    cri = result["cri"]

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"CRASH RISK INDEX (CRI) SCAN — {result['date']}{market_note}", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    # CRI Score
    print(f"\n  CRI SCORE: {cri['score']:.0f}/100 — {cri['level']}", file=sys.stderr)
    print(f"    VIX         : {cri['components']['vix']:.1f}/25", file=sys.stderr)
    print(f"    VVIX        : {cri['components']['vvix']:.1f}/25", file=sys.stderr)
    print(f"    Correlation : {cri['components']['correlation']:.1f}/25", file=sys.stderr)
    print(f"    Momentum    : {cri['components']['momentum']:.1f}/25", file=sys.stderr)

    # Market levels
    print(f"\n  LEVELS:", file=sys.stderr)
    print(f"    VIX  : {result['vix']:.2f} (5d RoC: {result['vix_5d_roc']:+.1f}%)", file=sys.stderr)
    print(f"    VVIX : {result['vvix']:.2f} (VVIX/VIX: {result['vvix_vix_ratio']:.2f})", file=sys.stderr)
    print(f"    SPY  : ${result['spy']:.2f} (vs 100d MA: {result['spx_distance_pct']:+.2f}%)", file=sys.stderr)
    cor1m_str = f"{result['cor1m']:.2f}" if result['cor1m'] is not None else "N/A"
    cor1m_chg_str = f"{result['cor1m_5d_change']:+.2f}" if result['cor1m_5d_change'] is not None else "N/A"
    print(f"    COR1M: {cor1m_str} (5d chg: {cor1m_chg_str})", file=sys.stderr)
    vol_str = f"{result['realized_vol']:.2f}%" if result['realized_vol'] is not None else "N/A"
    print(f"    RVol : {vol_str}", file=sys.stderr)

    # CTA model
    cta = result["cta"]
    print(f"\n  CTA EXPOSURE MODEL:", file=sys.stderr)
    print(f"    Implied exposure : {cta['exposure_pct']:.1f}%", file=sys.stderr)
    print(f"    Forced reduction : {cta['forced_reduction_pct']:.1f}%", file=sys.stderr)
    print(f"    Est. selling     : ${cta['est_selling_bn']:.1f}B", file=sys.stderr)

    # MenthorQ CTA positioning
    menthorq = result.get("menthorq_cta")
    if menthorq and menthorq.get("spx"):
        spx = menthorq["spx"]
        print(f"\n  MENTHORQ CTA POSITIONING (institutional):", file=sys.stderr)
        print(f"    SPX Position Today : {spx.get('position_today', '---')}", file=sys.stderr)
        print(f"    SPX Position Yest  : {spx.get('position_yesterday', '---')}", file=sys.stderr)
        print(f"    3M Percentile      : {spx.get('percentile_3m', '---')}", file=sys.stderr)
        print(f"    3M Z-Score         : {spx.get('z_score_3m', '---')}", file=sys.stderr)
        print(f"    Data date          : {menthorq.get('date', '---')}", file=sys.stderr)
    else:
        print(f"\n  MENTHORQ CTA: Data unavailable (run: python3 scripts/fetch_menthorq_cta.py)", file=sys.stderr)

    # Crash trigger
    trigger = result["crash_trigger"]
    conds = trigger["conditions"]
    print(f"\n  CRASH TRIGGER CONDITIONS:", file=sys.stderr)
    print(f"    SPX < 100d MA    : {'PASS' if conds['spx_below_100d_ma'] else 'FAIL'}", file=sys.stderr)
    print(f"    RVol > 25%       : {'PASS' if conds['realized_vol_gt_25'] else 'FAIL'}", file=sys.stderr)
    print(f"    COR1M > 60      : {'PASS' if conds['cor1m_gt_60'] else 'FAIL'}", file=sys.stderr)
    print(f"    TRIGGERED        : {'YES' if trigger['triggered'] else 'NO'}", file=sys.stderr)

    # Decision
    if trigger["triggered"]:
        print(f"\n  *** CRASH REGIME ACTIVE — CTA DELEVERAGING LIKELY ***", file=sys.stderr)
    elif cri["level"] in ("HIGH", "CRITICAL"):
        print(f"\n  ELEVATED RISK — Multiple CRI components stressed. Monitor for trigger.", file=sys.stderr)
    else:
        print(f"\n  No crash signal. Markets in normal regime.", file=sys.stderr)

    print(f"\n{'='*60}\n", file=sys.stderr)


# ══════════════════════════════════════════════════════════════════
# HTML Report
# ══════════════════════════════════════════════════════════════════

def generate_html_report(
    result: Dict[str, Any],
    market_open: bool,
    elapsed: float,
) -> str:
    """Generate a dark-themed HTML report for the CRI scan."""
    template_path = _PROJECT_DIR / ".pi/skills/html-report/cri-template.html"
    template = template_path.read_text()

    now = datetime.now().strftime("%Y-%m-%d %I:%M %p ET")
    market_label = "LIVE" if market_open else "CLOSED"
    cri = result["cri"]
    cta = result["cta"]
    trigger = result["crash_trigger"]

    # Score pill color
    if cri["level"] == "CRITICAL":
        pill_cls = "pill-negative"
    elif cri["level"] == "HIGH":
        pill_cls = "pill-warning"
    elif cri["level"] == "ELEVATED":
        pill_cls = "pill-warning"
    else:
        pill_cls = "pill-positive"

    body_parts = []

    # ── Header ──
    body_parts.append(f"""
<header class="header">
  <div>
    <h1 class="title">Crash Risk Index (CRI) Scanner</h1>
    <p class="subtitle">{result['date']} | Market {market_label}</p>
  </div>
  <div class="header-actions">
    <span class="{pill_cls} pill">{cri['level']} — {cri['score']:.0f}/100</span>
    <span class="timestamp">Generated: {now} ({elapsed:.1f}s)</span>
    <button class="theme-toggle" onclick="toggleTheme()">&#9684; THEME</button>
  </div>
</header>""")

    # ── CRI Score Bar ──
    score = cri["score"]
    bar_color = "var(--positive)" if score < 25 else "var(--warning)" if score < 75 else "var(--negative)"
    body_parts.append(f"""
<div class="panel" style="margin-bottom:24px">
  <div class="panel-header">CRI COMPOSITE SCORE</div>
  <div class="panel-body">
    <div style="font-size:48px;font-weight:600;letter-spacing:-0.02em;color:{bar_color}">{score:.0f}<span style="font-size:20px;color:var(--text-muted)">/100</span></div>
    <div style="margin-top:12px;height:12px;background:var(--border-dim);width:100%">
      <div style="height:100%;width:{score}%;background:{bar_color}"></div>
    </div>
    <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--text-muted);margin-top:4px;text-transform:uppercase;letter-spacing:0.1em">
      <span>LOW (0)</span><span>ELEVATED (25)</span><span>HIGH (50)</span><span>CRITICAL (75+)</span>
    </div>
  </div>
</div>""")

    # ── 4-Card Metric Grid ──
    corr_str = f"{result['cor1m']:.2f}" if result['cor1m'] is not None else "---"
    corr_chg = f"{result['cor1m_5d_change']:+.2f}" if result.get('cor1m_5d_change') is not None else "---"
    vol_str = f"{result['realized_vol']:.2f}%" if result['realized_vol'] is not None else "---"
    ma_str = f"${result['spx_100d_ma']:.2f}" if result['spx_100d_ma'] is not None else "---"

    body_parts.append(f"""
<div class="metrics">
  <div class="metric">
    <div class="metric-label">VIX</div>
    <div class="metric-value {"text-negative" if result['vix'] > 30 else "text-warning" if result['vix'] > 20 else ""}">{result['vix']:.2f}</div>
    <div class="metric-change">5d RoC: {result['vix_5d_roc']:+.1f}%</div>
  </div>
  <div class="metric">
    <div class="metric-label">VVIX</div>
    <div class="metric-value {"text-warning" if result['vvix'] > 110 else ""}">{result['vvix']:.2f}</div>
    <div class="metric-change">VVIX/VIX: {result['vvix_vix_ratio']:.2f}</div>
  </div>
  <div class="metric">
    <div class="metric-label">COR1M Implied Corr</div>
    <div class="metric-value {"text-negative" if result.get('cor1m', 0) and result['cor1m'] > 60 else ""}">{corr_str}</div>
    <div class="metric-change">5d change: {corr_chg} pts</div>
  </div>
  <div class="metric">
    <div class="metric-label">SPY vs 100d MA</div>
    <div class="metric-value {"text-negative" if result['spx_distance_pct'] < -5 else "text-warning" if result['spx_distance_pct'] < 0 else ""}">{result['spx_distance_pct']:+.2f}%</div>
    <div class="metric-change">${result['spy']:.2f} | MA: {ma_str}</div>
  </div>
  <div class="metric">
    <div class="metric-label">20d Realized Vol</div>
    <div class="metric-value {"text-negative" if result.get('realized_vol', 0) and result['realized_vol'] > 25 else ""}">{vol_str}</div>
    <div class="metric-change">Annualized</div>
  </div>
  <div class="metric">
    <div class="metric-label">Crash Trigger</div>
    <div class="metric-value {"text-negative" if trigger['triggered'] else "text-positive"}">{("ACTIVE" if trigger['triggered'] else "INACTIVE")}</div>
    <div class="metric-change">3 conditions</div>
  </div>
</div>""")

    # ── CRI Component Breakdown ──
    body_parts.append("""<div class="section-header">CRI Component Breakdown</div>""")
    components = cri["components"]
    body_parts.append("""<div class="panel"><div class="panel-body">""")
    for name, score_val in components.items():
        pct = score_val / 25.0 * 100
        bar_c = "positive" if score_val < 8 else "negative" if score_val > 16 else ""
        label = name.upper()
        body_parts.append(f"""
  <div style="display:flex;align-items:center;gap:16px;margin-bottom:12px">
    <div style="width:120px;font-size:10px;text-transform:uppercase;letter-spacing:0.15em;color:var(--text-muted)">{label}</div>
    <div class="bar-container" style="flex:1;height:12px">
      <div class="bar-fill {bar_c}" style="width:{pct:.0f}%"></div>
    </div>
    <div style="width:60px;text-align:right;font-weight:500">{score_val:.1f}/25</div>
  </div>""")
    body_parts.append("""</div></div>""")

    # ── CTA Exposure Model ──
    body_parts.append("""<div class="section-header">CTA Exposure Model</div>""")
    body_parts.append(f"""
<div class="grid-2">
  <div class="panel">
    <div class="panel-header">Vol-Target Model (10% target)</div>
    <div class="panel-body">
      <table>
        <tr><td>Realized Volatility</td><td class="text-right">{cta['realized_vol']:.2f}%</td></tr>
        <tr><td>Implied Equity Exposure</td><td class="text-right {"text-negative" if cta['exposure_pct'] < 50 else ""}">{cta['exposure_pct']:.1f}%</td></tr>
        <tr><td>Forced Reduction</td><td class="text-right {"text-negative" if cta['forced_reduction_pct'] > 0 else "text-positive"}">{cta['forced_reduction_pct']:.1f}%</td></tr>
        <tr><td>Est. CTA Selling</td><td class="text-right {"text-negative" if cta['est_selling_bn'] > 50 else ""}">${cta['est_selling_bn']:.1f}B</td></tr>
      </table>
    </div>
  </div>
  <div class="panel">
    <div class="panel-header">Exposure = Target Vol / Realized Vol</div>
    <div class="panel-body">
      <p style="color:var(--text-muted);font-size:11px">
        CTAs target 10% portfolio volatility. When realized vol doubles,
        they must halve equity exposure. At 40% vol, only 25% exposure
        remains — forcing ~$300B+ in systematic selling (est. $400B AUM).
      </p>
      <div style="margin-top:16px">
        <div style="font-size:10px;text-transform:uppercase;letter-spacing:0.15em;color:var(--text-muted);margin-bottom:4px">EXPOSURE GAUGE</div>
        <div class="bar-container" style="width:100%;height:16px">
          <div class="bar-fill {"positive" if cta['exposure_pct'] >= 80 else "negative"}" style="width:{min(cta['exposure_pct'], 200) / 2:.0f}%"></div>
        </div>
        <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--text-muted);margin-top:4px">
          <span>0%</span><span>50%</span><span>100%</span><span>150%</span><span>200%</span>
        </div>
      </div>
    </div>
  </div>
</div>""")

    # ── MenthorQ CTA Positioning ──
    menthorq = result.get("menthorq_cta")
    if menthorq and menthorq.get("tables"):
        body_parts.append(f"""<div class="section-header">MenthorQ CTA Positioning <span style="font-size:10px;color:var(--text-muted);font-weight:400">(institutional data — {menthorq.get('date', '---')})</span></div>""")

        # SPX highlight card
        spx = menthorq.get("spx")
        if spx:
            pos_t = spx.get("position_today", 0)
            pctl_3m = spx.get("percentile_3m", 50)
            z3 = spx.get("z_score_3m", 0)
            # Color code: low percentile = bearish (red), high = bullish (green)
            pctl_cls = "text-negative" if pctl_3m < 25 else "text-positive" if pctl_3m > 75 else "text-warning"
            z_cls = "text-negative" if z3 < -1.5 else "text-positive" if z3 > 1.5 else ""

            body_parts.append(f"""
<div class="metrics" style="margin-bottom:16px">
  <div class="metric">
    <div class="metric-label">SPX CTA Position</div>
    <div class="metric-value">{pos_t:.2f}</div>
    <div class="metric-change">Yesterday: {spx.get('position_yesterday', '---')}</div>
  </div>
  <div class="metric">
    <div class="metric-label">1M Ago</div>
    <div class="metric-value">{spx.get('position_1m_ago', '---')}</div>
    <div class="metric-change">Position delta</div>
  </div>
  <div class="metric">
    <div class="metric-label">3M Percentile</div>
    <div class="metric-value {pctl_cls}">{pctl_3m}</div>
    <div class="metric-change">1Y: {spx.get('percentile_1y', '---')}</div>
  </div>
  <div class="metric">
    <div class="metric-label">3M Z-Score</div>
    <div class="metric-value {z_cls}">{z3:.2f}</div>
    <div class="metric-change">Standard deviations</div>
  </div>
</div>""")

        # Full table for main + index assets
        for tkey, tlabel in [("main", "CTAs Main"), ("index", "CTAs Index")]:
            table_data = menthorq.get("tables", {}).get(tkey, [])
            if not table_data:
                continue
            body_parts.append(f"""<div class="panel" style="margin-bottom:12px">
<div class="panel-header">{tlabel}</div>
<table>
<thead><tr>
  <th>Underlying</th>
  <th class="text-right">Pos Today</th>
  <th class="text-right">Pos Yest</th>
  <th class="text-right">1M Ago</th>
  <th class="text-right">Pctl 3M</th>
  <th class="text-right">Z-Score</th>
</tr></thead><tbody>""")
            for entry in table_data:
                name = entry.get("underlying", "?")
                pt = entry.get("position_today", "---")
                py_ = entry.get("position_yesterday", "---")
                p1m = entry.get("position_1m_ago", "---")
                pctl = entry.get("percentile_3m", "---")
                zs = entry.get("z_score_3m", "---")
                pt_str = f"{pt:.2f}" if isinstance(pt, (int, float)) else str(pt)
                py_str = f"{py_:.2f}" if isinstance(py_, (int, float)) else str(py_)
                p1m_str = f"{p1m:.2f}" if isinstance(p1m, (int, float)) else str(p1m)
                pctl_str = str(pctl) if isinstance(pctl, (int, float)) else str(pctl)
                zs_str = f"{zs:.2f}" if isinstance(zs, (int, float)) else str(zs)
                # Highlight low percentiles
                row_cls = ""
                if isinstance(pctl, (int, float)) and pctl < 20:
                    row_cls = ' class="highlight"'
                body_parts.append(
                    f'<tr{row_cls}><td>{name}</td>'
                    f'<td class="text-right">{pt_str}</td>'
                    f'<td class="text-right">{py_str}</td>'
                    f'<td class="text-right">{p1m_str}</td>'
                    f'<td class="text-right">{pctl_str}</td>'
                    f'<td class="text-right">{zs_str}</td></tr>'
                )
            body_parts.append("</tbody></table></div>")

        # Compact tables for commodity + currency
        for tkey, tlabel in [("commodity", "CTAs Commodity"), ("currency", "CTAs Currency")]:
            table_data = menthorq.get("tables", {}).get(tkey, [])
            if not table_data:
                continue
            body_parts.append(f"""<div class="panel" style="margin-bottom:12px">
<div class="panel-header">{tlabel}</div>
<table>
<thead><tr>
  <th>Underlying</th>
  <th class="text-right">Pos Today</th>
  <th class="text-right">Pctl 3M</th>
  <th class="text-right">Z-Score</th>
</tr></thead><tbody>""")
            for entry in table_data:
                name = entry.get("underlying", "?")
                pt = entry.get("position_today", "---")
                pctl = entry.get("percentile_3m", "---")
                zs = entry.get("z_score_3m", "---")
                pt_str = f"{pt:.2f}" if isinstance(pt, (int, float)) else str(pt)
                pctl_str = str(pctl) if isinstance(pctl, (int, float)) else str(pctl)
                zs_str = f"{zs:.2f}" if isinstance(zs, (int, float)) else str(zs)
                body_parts.append(
                    f'<tr><td>{name}</td>'
                    f'<td class="text-right">{pt_str}</td>'
                    f'<td class="text-right">{pctl_str}</td>'
                    f'<td class="text-right">{zs_str}</td></tr>'
                )
            body_parts.append("</tbody></table></div>")
    else:
        body_parts.append("""
<div class="section-header">MenthorQ CTA Positioning</div>
<div class="panel">
  <div class="panel-body" style="color:var(--text-muted);font-size:11px">
    Data unavailable. Run <code>python3 scripts/fetch_menthorq_cta.py</code> to fetch institutional CTA positioning data.
  </div>
</div>""")

    # ── Crash Trigger Conditions ──
    body_parts.append("""<div class="section-header">Crash Trigger Conditions (All Must Fire)</div>""")
    conds = trigger["conditions"]
    body_parts.append("""<div class="panel"><table>
<thead><tr><th>Condition</th><th class="text-center">Required</th><th class="text-center">Actual</th><th class="text-center">Status</th></tr></thead><tbody>""")

    trigger_rows = [
        ("SPX below 100-day MA", "Below", f"{result['spx_distance_pct']:+.2f}%", conds['spx_below_100d_ma']),
        ("20d Realized Vol > 25%", "> 25%", vol_str, conds['realized_vol_gt_25']),
        ("COR1M > 60", "> 60", corr_str, conds['cor1m_gt_60']),
    ]
    for label, req, actual, passed in trigger_rows:
        icon = '<span class="text-positive">PASS</span>' if passed else '<span class="text-negative">FAIL</span>'
        body_parts.append(
            f'<tr><td>{label}</td><td class="text-center">{req}</td>'
            f'<td class="text-center">{actual}</td><td class="text-center">{icon}</td></tr>'
        )
    body_parts.append("</tbody></table></div>")

    # ── Interpretation Callout ──
    if trigger["triggered"]:
        body_parts.append(f"""
<div class="callout negative">
  <div class="callout-title">CRASH REGIME ACTIVE — CTA DELEVERAGING LIKELY</div>
  All three crash trigger conditions are met. Systematic vol-targeting CTAs (~$400B AUM) are being forced
  to reduce equity exposure. Estimated selling pressure: <strong>${cta['est_selling_bn']:.1f}B</strong>.
  <br><br>
  <strong>Implications:</strong> Predictable selling cascades over 3-5 days. Liquidity gaps widen.
  Correlation stays elevated. Prepare for gamma-negative dealer hedging to amplify moves.
  <br><br>
  <strong>Playbook:</strong> Reduce equity exposure, add tail hedges (SPY puts), avoid catching the knife.
  Wait for vol mean-reversion signal before re-entering.
</div>""")
    elif cri["level"] in ("HIGH", "CRITICAL"):
        body_parts.append(f"""
<div class="callout warning">
  <div class="callout-title">ELEVATED CRASH RISK — MONITORING</div>
  CRI score is {cri['score']:.0f}/100 ({cri['level']}). Multiple components are stressed but the
  full crash trigger has not yet fired. Monitor for all three conditions converging.
</div>""")
    else:
        body_parts.append(f"""
<div class="callout positive">
  <div class="callout-title">NO CRASH SIGNAL — NORMAL REGIME</div>
  CRI score is {cri['score']:.0f}/100 ({cri['level']}). Systematic risk is low.
  No CTA deleveraging pressure detected.
</div>""")

    # ── Rolling 10-Day History ──
    body_parts.append("""<hr class="divider"><div class="section-header">Rolling 10-Day History</div>""")
    body_parts.append("""<div class="panel"><table>
<thead><tr>
  <th>Date</th>
  <th class="text-right">VIX</th>
  <th class="text-right">VVIX</th>
  <th class="text-right">SPY</th>
  <th class="text-right">vs 100d MA</th>
  <th class="text-right">VIX 5d RoC</th>
</tr></thead><tbody>""")

    for i, h in enumerate(result["history"]):
        is_last = (i == len(result["history"]) - 1)
        hl = ' class="highlight"' if is_last else ""
        dist_cls = "text-negative" if h["spx_vs_ma_pct"] < -5 else "text-warning" if h["spx_vs_ma_pct"] < 0 else ""
        body_parts.append(
            f'<tr{hl}><td>{h["date"]}</td>'
            f'<td class="text-right">{h["vix"]:.2f}</td>'
            f'<td class="text-right">{h["vvix"]:.2f}</td>'
            f'<td class="text-right">${h["spy"]:.2f}</td>'
            f'<td class="text-right {dist_cls}">{h["spx_vs_ma_pct"]:+.2f}%</td>'
            f'<td class="text-right">{h["vix_5d_roc"]:+.1f}%</td></tr>'
        )

    body_parts.append("</tbody></table></div>")

    # ── Footer ──
    body_parts.append(f"""
<div class="footer">
  <strong>CRI Scanner — Crash Risk Index</strong><br>
  Components: VIX (level + RoC) | VVIX (level + ratio) | COR1M implied correlation (level + 5d change) | SPX Momentum (vs 100d MA)<br>
  CTA Model: Exposure = 10% target / Realized Vol | Estimated AUM: $400B<br>
  Crash Trigger: SPX &lt; 100d MA AND 20d RVol &gt; 25% AND COR1M &gt; 60<br>
  Data: IB (primary) | UW for SPY fallback | Cboe dashboard for COR1M fallback | Yahoo Finance last-resort for remaining index gaps | {now}<br>
  Strategy spec: <code>docs/strategies.md</code> (Strategy 6) |
  <a href="https://chatgpt.com/share/69ab7eee-fe34-8013-b489-7758297da446" style="color:var(--text-muted)">Source: CTA Deleveraging Research</a>
</div>""")

    body = "\n".join(body_parts)
    title = f"CRI Scan — {result['date']}"
    html = template.replace("{{TITLE}}", title)
    html = html.replace("{{BODY}}", body)
    return html


# ══════════════════════════════════════════════════════════════════
# Market Hours Check
# ══════════════════════════════════════════════════════════════════

def is_market_open() -> bool:
    """Check if US equity markets are currently open."""
    import zoneinfo
    try:
        et = zoneinfo.ZoneInfo("America/New_York")
    except Exception:
        now_utc = datetime.now(timezone.utc)
        et_offset = timedelta(hours=-5)
        now_et = now_utc + et_offset
        return now_et.weekday() < 5 and 9 * 60 + 30 <= now_et.hour * 60 + now_et.minute <= 16 * 60

    now_et = datetime.now(et)
    if now_et.weekday() >= 5:
        return False
    minutes = now_et.hour * 60 + now_et.minute
    return 9 * 60 + 30 <= minutes <= 16 * 60


# ══════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Crash Risk Index (CRI) Scanner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
The CRI detects systematic crash risk by scoring four components: VIX level
and momentum, VVIX convexity demand, COR1M implied correlation, and SPX
distance from the 100-day moving average. When the crash trigger fires
(all three conditions met), CTAs are forced to deleverage.

Examples:
  python3 scripts/cri_scan.py                  # HTML report
  python3 scripts/cri_scan.py --json           # JSON output
  python3 scripts/cri_scan.py --no-open        # Don't open browser
""",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    parser.add_argument("--no-open", action="store_true", help="Don't open HTML report in browser")
    parser.add_argument("--output", "-o", help="Custom output path for HTML")

    args = parser.parse_args()

    market_open = is_market_open()

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"CRI SCANNER — Crash Risk Index", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    if not market_open:
        print(f"  Market closed — using last available data.", file=sys.stderr)

    t_start = time.time()

    # Fetch data for all required instruments
    aligned, common_dates = fetch_all(ALL_TICKERS)

    print(f"  Data range: {common_dates[0]} to {common_dates[-1]} ({len(common_dates)} bars)", file=sys.stderr)

    cor1m_current = fetch_cor1m_current_quote()
    current_quotes = {"COR1M": cor1m_current} if cor1m_current is not None else {}

    # Run analysis
    result = run_analysis(aligned, common_dates, current_quotes=current_quotes)

    elapsed = time.time() - t_start

    # Output
    if args.json:
        output = {
            "scan_time": datetime.now().isoformat(),
            "market_open": market_open,
            **result,
        }
        print(json.dumps(output, indent=2))
    else:
        print_summary(result, market_open)

        # Generate HTML report
        html = generate_html_report(result, market_open, elapsed)
        date_str = datetime.now().strftime("%Y-%m-%d")
        out_path = Path(args.output) if args.output else _PROJECT_DIR / f"reports/cri-scan-{date_str}.html"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html)
        print(f"  Report: {out_path}", file=sys.stderr)

        if not args.no_open:
            import webbrowser
            webbrowser.open(f"file://{out_path.resolve()}")


if __name__ == "__main__":
    main()
