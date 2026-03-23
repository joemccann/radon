#!/usr/bin/env python3
"""Cross-Asset Volatility-Credit Gap (VCG) Scanner.

Detects divergence between the volatility complex (VIX/VVIX) and cash
credit (HYG/JNK/LQD) using a rolling 21-day OLS model.  When the
standardised residual exceeds +2 sigma and the High-Divergence-Risk
conditions hold, the scanner fires a Risk-Off signal.

Mathematical specification: docs/VCG_institutional_research_note.md
Strategy spec:              docs/strategies.md (Strategy 5)

Data sources (priority order):
  1. Interactive Brokers — Index('VIX','CBOE'), Index('VVIX','CBOE'),
     Stock('HYG','SMART','USD')
  2. Unusual Whales — OHLC for stocks/ETFs (HYG, JNK, LQD).
     Does NOT support VIX/VVIX indices.
  3. Yahoo Finance — ABSOLUTE LAST RESORT. Only for VIX/VVIX when
     IB unavailable (UW cannot serve index data).

Usage:
    python3 scripts/vcg_scan.py                 # Human-readable summary
    python3 scripts/vcg_scan.py --json           # JSON to stdout
    python3 scripts/vcg_scan.py --proxy JNK      # Alternate credit proxy
    python3 scripts/vcg_scan.py --backtest --days 252   # Rolling backtest
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.request import Request, urlopen

import numpy as np

# ── path setup ────────────────────────────────────────────────────
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

# ── constants ─────────────────────────────────────────────────────
OLS_WINDOW = 21        # Rolling regression window (business days)
Z_WINDOW = 63          # Standardisation lookback (business days)
MIN_BARS = OLS_WINDOW + Z_WINDOW + 10   # Minimum price history needed
VIX_PANIC_LOW = 40.0   # Panic overlay lower bound (Pi clamp)
VIX_PANIC_HIGH = 48.0  # Panic overlay upper bound (Pi clamp)
# VCG signal thresholds — VIX gate is a FLOOR (elevated stress), not a ceiling
VIX_FLOOR = 28.0       # RO gate: VIX must be ABOVE this (overrideable via --vix-floor)
VIX_EDR = 25.0         # EDR watch gate: VIX must be above this
VCG_TRIGGER = 2.0      # EDR / Watch VCG z-score threshold
VCG_RO_TRIGGER = 2.5   # RO VCG z-score threshold (overrideable via --vcg-trigger)
BOUNCE_TRIGGER = -3.5  # Counter-signal (tactical long) VCG threshold
VVIX_EXTREME = 120.0   # VVIX severity amplifier: extreme
VVIX_ELEVATED = 100.0  # VVIX severity amplifier: elevated (below = moderate)

# Yahoo-to-IB ticker map
YAHOO_TICKERS = {
    "VIX": "^VIX",
    "VVIX": "^VVIX",
    "HYG": "HYG",
    "JNK": "JNK",
    "LQD": "LQD",
}


# ══════════════════════════════════════════════════════════════════
# Data Fetching
# ══════════════════════════════════════════════════════════════════

def _fetch_ib(tickers: List[str]) -> Dict[str, List[Tuple[str, float]]]:
    """Fetch 1Y daily bars from IB.  Returns {ticker: [(date_str, close), ...]}."""
    try:
        from clients.ib_client import IBClient, IBConnectionError
        from ib_insync import Index, Stock
    except ImportError:
        return {}

    results: Dict[str, List[Tuple[str, float]]] = {}
    client = IBClient()
    try:
        client.connect(client_name="vcg_scanner", timeout=8, max_retries=1)
    except Exception:
        return {}

    try:
        for ticker in tickers:
            if ticker in ("VIX", "VVIX"):
                contract = Index(ticker, "CBOE")
            else:
                contract = Stock(ticker, "SMART", "USD")
            try:
                client.qualify_contract(contract)
                bars = client.get_historical_data(
                    contract,
                    duration="1 Y",
                    bar_size="1 day",
                    what_to_show="TRADES" if ticker not in ("VIX", "VVIX") else "TRADES",
                    use_rth=True,
                )
                if bars:
                    results[ticker] = [
                        (str(b.date), float(b.close)) for b in bars
                    ]
                    print(f"  IB: {ticker} — {len(bars)} bars", file=sys.stderr)
            except Exception as exc:
                print(f"  IB: {ticker} failed — {exc}", file=sys.stderr)
    finally:
        client.disconnect()

    return results


def _fetch_uw(tickers: List[str]) -> Dict[str, List[Tuple[str, float]]]:
    """Fetch 1Y daily bars from Unusual Whales OHLC endpoint.

    UW supports stocks and ETFs but NOT indices (VIX, VVIX).
    Returns {ticker: [(date_str, close), ...]} for successful fetches.
    """
    try:
        from clients.uw_client import UWClient
    except ImportError:
        return {}

    # UW cannot serve index data
    INDEX_TICKERS = {"VIX", "VVIX"}
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


def _fetch_yahoo(ticker: str, days: int = 400) -> List[Tuple[str, float]]:
    """ABSOLUTE LAST RESORT: Fetch daily bars from Yahoo Finance.

    Returns [(date_str, close), ...].
    """
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
        result = data["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
        bars = []
        for ts, c in zip(timestamps, closes):
            if c is not None:
                dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
                bars.append((dt, float(c)))
        return bars
    except Exception as exc:
        print(f"  Yahoo: {ticker} ({yahoo_sym}) failed — {exc}", file=sys.stderr)
        return []


def fetch_all(tickers: List[str]) -> Dict[str, np.ndarray]:
    """Fetch close prices for all tickers.

    Priority: IB (concurrent) → UW (stocks/ETFs) → Yahoo (LAST RESORT).
    Returns {ticker: np.array of closes} aligned by date intersection.
    """
    # Priority 1: Interactive Brokers
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

    # Priority 3 (LAST RESORT): Yahoo Finance
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

    # Build aligned arrays
    aligned: Dict[str, np.ndarray] = {}
    for t in tickers:
        lookup = {d: c for d, c in raw[t]}
        aligned[t] = np.array([lookup[d] for d in common_dates])

    print(f"  Aligned: {len(common_dates)} common trading days", file=sys.stderr)
    return aligned, common_dates


# ══════════════════════════════════════════════════════════════════
# VCG Computation
# ══════════════════════════════════════════════════════════════════

def log_returns(prices: np.ndarray) -> np.ndarray:
    """Compute log returns: ln(P_t / P_{t-1})."""
    return np.log(prices[1:] / prices[:-1])


def rolling_ols(y: np.ndarray, X: np.ndarray, window: int = OLS_WINDOW):
    """Rolling OLS: y = alpha + beta1*X[:,0] + beta2*X[:,1] + eps.

    Uses numpy.linalg.lstsq.  Returns arrays of (alpha, beta1, beta2, residual)
    for each valid window position.
    """
    n = len(y)
    alphas = np.full(n, np.nan)
    beta1s = np.full(n, np.nan)
    beta2s = np.full(n, np.nan)
    residuals = np.full(n, np.nan)

    for t in range(window - 1, n):
        start = t - window + 1
        y_w = y[start : t + 1]
        X_w = X[start : t + 1]
        # Design matrix: [1, VVIX_ret, VIX_ret]
        A = np.column_stack([np.ones(window), X_w])
        try:
            coeff, _, _, _ = np.linalg.lstsq(A, y_w, rcond=None)
        except np.linalg.LinAlgError:
            continue
        alphas[t] = coeff[0]
        beta1s[t] = coeff[1]
        beta2s[t] = coeff[2]
        y_hat = A @ coeff
        residuals[t] = y_w[-1] - y_hat[-1]

    return alphas, beta1s, beta2s, residuals


def standardise_residuals(
    residuals: np.ndarray, window: int = Z_WINDOW
) -> np.ndarray:
    """Compute z-scores of residuals over a trailing window."""
    n = len(residuals)
    z = np.full(n, np.nan)
    for t in range(window - 1, n):
        start = t - window + 1
        chunk = residuals[start : t + 1]
        valid = chunk[~np.isnan(chunk)]
        if len(valid) < 10:
            continue
        mu = np.mean(valid)
        sigma = np.std(valid, ddof=1)
        if sigma < 1e-12:
            continue
        z[t] = (residuals[t] - mu) / sigma
    return z


def compute_vcg(
    vix_prices: np.ndarray,
    vvix_prices: np.ndarray,
    credit_prices: np.ndarray,
) -> Dict[str, np.ndarray]:
    """Compute the full VCG model.

    Returns dict of arrays (all same length = len(prices) - 1):
        vcg, vcg_div, residuals, beta1, beta2, alpha,
        vix_ret, vvix_ret, credit_ret
    """
    vix_ret = log_returns(vix_prices)
    vvix_ret = log_returns(vvix_prices)
    credit_ret = log_returns(credit_prices)

    X = np.column_stack([vvix_ret, vix_ret])
    alphas, beta1s, beta2s, residuals = rolling_ols(credit_ret, X, OLS_WINDOW)
    vcg = standardise_residuals(residuals, Z_WINDOW)

    # Panic overlay: Pi = clamp((VIX - 40) / 8, 0, 1)
    # VIX prices are one element longer than returns; use the tail
    vix_levels = vix_prices[1:]  # align with returns
    pi = np.clip((vix_levels - VIX_PANIC_LOW) / (VIX_PANIC_HIGH - VIX_PANIC_LOW), 0, 1)
    vcg_div = (1 - pi) * vcg

    return {
        "vcg": vcg,
        "vcg_div": vcg_div,
        "residuals": residuals,
        "alpha": alphas,
        "beta1": beta1s,
        "beta2": beta2s,
        "vix_ret": vix_ret,
        "vvix_ret": vvix_ret,
        "credit_ret": credit_ret,
        "vix_levels": vix_levels,
        "vvix_levels": vvix_prices[1:],
        "credit_levels": credit_prices[1:],
        "pi": pi,
    }


def evaluate_signal(
    model: Dict[str, np.ndarray],
    credit_prices: np.ndarray,
    vix_floor: float = VIX_FLOOR,
    vcg_trigger: float = VCG_RO_TRIGGER,
) -> Dict[str, Any]:
    """Evaluate the VCG signal for the most recent bar.

    VIX gate is now a FLOOR (stress must be elevated), not a ceiling.
    HDR has been removed; replaced by RO/EDR/tier/bounce/vvix_severity.
    """
    idx = -1  # latest bar
    vcg_val = model["vcg"][idx]
    vcg_div_val = model["vcg_div"][idx]
    beta1 = model["beta1"][idx]
    beta2 = model["beta2"][idx]
    alpha = model["alpha"][idx]
    vix = model["vix_levels"][idx]
    vvix = model["vvix_levels"][idx]
    credit = model["credit_levels"][idx]
    residual = model["residuals"][idx]
    pi_val = model["pi"][idx]

    # 5-day credit return (retained for display / context only)
    credit_all = credit_prices
    if len(credit_all) >= 6:
        credit_5d_ret = (credit_all[-1] / credit_all[-6]) - 1
    else:
        credit_5d_ret = 0.0

    # Sign discipline
    sign_ok = (beta1 <= 0) and (beta2 <= 0)
    sign_suppressed = not sign_ok

    # VVIX severity amplifier (informational, not a gate)
    if vvix > VVIX_EXTREME:
        vvix_severity = "extreme"
    elif vvix >= VVIX_ELEVATED:
        vvix_severity = "elevated"
    else:
        vvix_severity = "moderate"

    # Primary signal: Risk-Off (RO) — VIX is a FLOOR (elevated stress required)
    ro = bool(
        not np.isnan(vcg_val)
        and vix > vix_floor
        and vcg_val > vcg_trigger
        and sign_ok
    )

    # Watch state: Elevated Divergence Risk (EDR)
    edr = bool(
        not np.isnan(vcg_val)
        and vix > VIX_EDR
        and vcg_val > VCG_TRIGGER
        and sign_ok
    )

    # Severity tier (only when ro or edr)
    tier: Optional[int] = None
    if ro:
        tier = 1 if vix > 30 else 2   # 1 = Severe, 2 = High
    elif edr and not np.isnan(vcg_val) and vcg_val > vcg_trigger:
        tier = 3                        # 3 = Elevated

    # Counter-signal: Bounce (tactical long)
    bounce = bool(not np.isnan(vcg_val) and vcg_val < BOUNCE_TRIGGER)

    # Attribution split
    vvix_component = beta1 * model["vvix_ret"][idx] if not np.isnan(beta1) else 0.0
    vix_component = beta2 * model["vix_ret"][idx] if not np.isnan(beta2) else 0.0
    model_implied = alpha + vvix_component + vix_component if not np.isnan(alpha) else 0.0
    total_component = abs(vvix_component) + abs(vix_component) if (abs(vvix_component) + abs(vix_component)) > 1e-12 else 1.0
    vvix_pct = abs(vvix_component) / total_component * 100
    vix_pct = abs(vix_component) / total_component * 100

    # Regime label
    if pi_val >= 1.0:
        regime = "PANIC"
    elif pi_val > 0:
        regime = "TRANSITION"
    else:
        regime = "DIVERGENCE"

    # Interpretation
    if np.isnan(vcg_val):
        interpretation = "INSUFFICIENT_DATA"
    elif not sign_ok:
        interpretation = "SUPPRESSED"
    elif pi_val >= 1.0:
        interpretation = "PANIC"
    elif ro:
        interpretation = "RISK_OFF"
    elif edr:
        interpretation = "EDR"
    elif bounce:
        interpretation = "BOUNCE"
    elif not np.isnan(vcg_val) and vcg_val > VCG_TRIGGER:
        interpretation = "WATCH"
    else:
        interpretation = "NORMAL"

    return {
        "vcg": round(float(vcg_val), 4) if not np.isnan(vcg_val) else None,
        "vcg_adj": round(float(vcg_div_val), 4) if not np.isnan(vcg_div_val) else None,
        "residual": round(float(residual), 6) if not np.isnan(residual) else None,
        "beta1_vvix": round(float(beta1), 6) if not np.isnan(beta1) else None,
        "beta2_vix": round(float(beta2), 6) if not np.isnan(beta2) else None,
        "alpha": round(float(alpha), 6) if not np.isnan(alpha) else None,
        "vix": round(float(vix), 2),
        "vvix": round(float(vvix), 2),
        "credit_price": round(float(credit), 2),
        "credit_5d_return_pct": round(float(credit_5d_ret * 100), 3),
        "ro": int(ro),
        "edr": int(edr),
        "tier": tier,
        "bounce": int(bounce),
        "vvix_severity": vvix_severity,
        "sign_ok": bool(sign_ok),
        "sign_suppressed": bool(sign_suppressed),
        "pi_panic": round(float(pi_val), 4),
        "regime": regime,
        "interpretation": interpretation,
        "attribution": {
            "vvix_pct": round(vvix_pct, 1),
            "vix_pct": round(vix_pct, 1),
            "vvix_component": round(float(vvix_component), 6),
            "vix_component": round(float(vix_component), 6),
            "model_implied": round(float(model_implied), 6),
        },
    }


def backtest_signals(
    model: Dict[str, np.ndarray],
    credit_prices: np.ndarray,
    days: int = 252,
    vix_floor: float = VIX_FLOOR,
    vcg_trigger: float = VCG_RO_TRIGGER,
) -> List[Dict[str, Any]]:
    """Run the VCG signal evaluation over the last N trading days."""
    n = len(model["vcg"])
    start = max(0, n - days)
    results = []
    for i in range(start, n):
        vcg_val = model["vcg"][i]
        vcg_div_val = model["vcg_div"][i]
        beta1 = model["beta1"][i]
        beta2 = model["beta2"][i]
        vix = model["vix_levels"][i]
        vvix = model["vvix_levels"][i]
        credit = model["credit_levels"][i]
        pi_val = model["pi"][i]

        sign_ok = (not np.isnan(beta1) and beta1 <= 0) and (not np.isnan(beta2) and beta2 <= 0)

        ro = bool(
            not np.isnan(vcg_val)
            and vix > vix_floor
            and vcg_val > vcg_trigger
            and sign_ok
        )
        edr = bool(
            not np.isnan(vcg_val)
            and vix > VIX_EDR
            and vcg_val > VCG_TRIGGER
            and sign_ok
        )
        tier: Optional[int] = None
        if ro:
            tier = 1 if vix > 30 else 2
        elif edr and not np.isnan(vcg_val) and vcg_val > vcg_trigger:
            tier = 3

        bounce = bool(not np.isnan(vcg_val) and vcg_val < BOUNCE_TRIGGER)

        results.append({
            "idx": int(i),
            "vcg": round(float(vcg_val), 4) if not np.isnan(vcg_val) else None,
            "vcg_adj": round(float(vcg_div_val), 4) if not np.isnan(vcg_div_val) else None,
            "vix": round(float(vix), 2),
            "vvix": round(float(vvix), 2),
            "credit": round(float(credit), 2),
            "ro": int(ro),
            "edr": int(edr),
            "tier": tier,
            "bounce": int(bounce),
            "beta1": round(float(beta1), 6) if not np.isnan(beta1) else None,
            "beta2": round(float(beta2), 6) if not np.isnan(beta2) else None,
            "sign_ok": bool(sign_ok),
            "pi": round(float(pi_val), 4),
        })

    return results


# ══════════════════════════════════════════════════════════════════
# Output
# ══════════════════════════════════════════════════════════════════

def print_summary(
    signal: Dict[str, Any],
    model: Dict[str, np.ndarray],
    dates: List[str],
    proxy: str,
    market_open: bool,
) -> None:
    """Print human-readable VCG summary to stderr."""
    market_note = "" if market_open else "  [Market closed -- using last available data]"
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"VCG SCAN — {proxy} | {dates[-1]}{market_note}", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    # Current levels
    print(f"\n  VIX   : {signal['vix']:.2f}", file=sys.stderr)
    print(f"  VVIX  : {signal['vvix']:.2f}", file=sys.stderr)
    print(f"  {proxy} : ${signal['credit_price']:.2f}  (5d: {signal['credit_5d_return_pct']:+.3f}%)", file=sys.stderr)

    # VCG
    vcg_str = f"{signal['vcg']:.4f}" if signal['vcg'] is not None else "N/A"
    vcg_adj_str = f"{signal['vcg_adj']:.4f}" if signal['vcg_adj'] is not None else "N/A"
    print(f"\n  VCG          : {vcg_str}", file=sys.stderr)
    print(f"  VCG (adj)    : {vcg_adj_str}", file=sys.stderr)
    print(f"  Regime       : {signal['regime']} (Pi={signal['pi_panic']:.2f})", file=sys.stderr)
    print(f"  Interpretation: {signal['interpretation']}", file=sys.stderr)

    # Betas
    b1 = f"{signal['beta1_vvix']:.6f}" if signal['beta1_vvix'] is not None else "N/A"
    b2 = f"{signal['beta2_vix']:.6f}" if signal['beta2_vix'] is not None else "N/A"
    sign_icon = "OK" if signal['sign_ok'] else "SUPPRESSED (positive beta)"
    print(f"\n  beta1 (VVIX) : {b1}", file=sys.stderr)
    print(f"  beta2 (VIX)  : {b2}", file=sys.stderr)
    print(f"  Sign check   : {sign_icon}", file=sys.stderr)

    # Attribution
    attr = signal['attribution']
    print(f"\n  Attribution  : VVIX {attr['vvix_pct']:.1f}% | VIX {attr['vix_pct']:.1f}%", file=sys.stderr)

    # Signal state
    tier_str = f"Tier {signal['tier']}" if signal['tier'] is not None else "None"
    print(f"\n  VVIX Severity : {signal['vvix_severity'].upper()} ({signal['vvix']:.2f})", file=sys.stderr)
    print(f"  EDR           : {signal['edr']}  (VIX>{VIX_EDR}, VCG>{VCG_TRIGGER})", file=sys.stderr)
    print(f"  RO            : {signal['ro']}  (VIX>{VIX_FLOOR}, VCG>{VCG_RO_TRIGGER})", file=sys.stderr)
    print(f"  Tier          : {tier_str}", file=sys.stderr)
    print(f"  Bounce        : {signal['bounce']}  (VCG<{BOUNCE_TRIGGER})", file=sys.stderr)

    if signal['ro'] == 1:
        severity_name = "SEVERE" if signal['tier'] == 1 else "HIGH"
        tier_label = f"Tier {signal['tier']} — {severity_name}" if signal['tier'] is not None else ""
        print(f"\n  *** RISK-OFF SIGNAL ACTIVE ({tier_label}) ***", file=sys.stderr)
        print(f"  Credit divergence is statistically significant.", file=sys.stderr)
        print(f"  Action: Reduce credit beta, raise quality, add convex hedges.", file=sys.stderr)
    elif signal['edr'] == 1:
        print(f"\n  EDR active — elevated divergence risk. Monitor for VCG escalation above {VCG_RO_TRIGGER}.", file=sys.stderr)
    elif signal['bounce'] == 1:
        print(f"\n  BOUNCE signal — VCG deeply negative ({signal['vcg']}), potential tactical long.", file=sys.stderr)
    else:
        print(f"\n  No signal. Market in normal regime.", file=sys.stderr)

    # Rolling residuals (last 20 sessions)
    n = len(model["residuals"])
    print(f"\n  Last 10 days — rolling residuals:", file=sys.stderr)
    print(f"  {'Date':<12} {'Resid':>10} {'VCG':>8} {'VCG_div':>9} {'B1':>10} {'B2':>10}", file=sys.stderr)
    print(f"  {'-'*12} {'-'*10} {'-'*8} {'-'*9} {'-'*10} {'-'*10}", file=sys.stderr)
    for i in range(max(0, n - 20), n):
        date_idx = i + 1  # dates are 1 longer than returns
        d = dates[date_idx] if date_idx < len(dates) else "?"
        r = model["residuals"][i]
        v = model["vcg"][i]
        vd = model["vcg_div"][i]
        b1v = model["beta1"][i]
        b2v = model["beta2"][i]
        r_s = f"{r:.6f}" if not np.isnan(r) else "N/A"
        v_s = f"{v:.4f}" if not np.isnan(v) else "N/A"
        vd_s = f"{vd:.4f}" if not np.isnan(vd) else "N/A"
        b1_s = f"{b1v:.6f}" if not np.isnan(b1v) else "N/A"
        b2_s = f"{b2v:.6f}" if not np.isnan(b2v) else "N/A"
        print(f"  {d:<12} {r_s:>10} {v_s:>8} {vd_s:>9} {b1_s:>10} {b2_s:>10}", file=sys.stderr)

    print(f"\n{'='*60}\n", file=sys.stderr)


def build_json_output(
    signal: Dict[str, Any],
    model: Dict[str, np.ndarray],
    dates: List[str],
    proxy: str,
    market_open: bool,
    backtest_results: Optional[List[Dict]] = None,
    vix_floor: float = VIX_FLOOR,
    vcg_trigger: float = VCG_RO_TRIGGER,
) -> Dict:
    """Build JSON output dict."""
    # Recent history (last 20 sessions) — include per-day signal fields
    n = len(model["residuals"])
    history = []
    for i in range(max(0, n - 20), n):
        date_idx = i + 1
        d = dates[date_idx] if date_idx < len(dates) else None
        vcg_i = model["vcg"][i]
        vix_i = model["vix_levels"][i]
        beta1_i = model["beta1"][i]
        beta2_i = model["beta2"][i]
        sign_ok_i = (
            not np.isnan(beta1_i) and beta1_i <= 0
            and not np.isnan(beta2_i) and beta2_i <= 0
        )
        ro_i = bool(
            not np.isnan(vcg_i)
            and vix_i > vix_floor
            and vcg_i > vcg_trigger
            and sign_ok_i
        )
        edr_i = bool(
            not np.isnan(vcg_i)
            and vix_i > VIX_EDR
            and vcg_i > VCG_TRIGGER
            and sign_ok_i
        )
        tier_i: Optional[int] = None
        if ro_i:
            tier_i = 1 if vix_i > 30 else 2
        elif edr_i and not np.isnan(vcg_i) and vcg_i > vcg_trigger:
            tier_i = 3
        bounce_i = bool(not np.isnan(vcg_i) and vcg_i < BOUNCE_TRIGGER)

        history.append({
            "date": d,
            "residual": round(float(model["residuals"][i]), 6) if not np.isnan(model["residuals"][i]) else None,
            "vcg": round(float(vcg_i), 4) if not np.isnan(vcg_i) else None,
            "vcg_adj": round(float(model["vcg_div"][i]), 4) if not np.isnan(model["vcg_div"][i]) else None,
            "beta1": round(float(beta1_i), 6) if not np.isnan(beta1_i) else None,
            "beta2": round(float(model["beta2"][i]), 6) if not np.isnan(model["beta2"][i]) else None,
            "vix": round(float(vix_i), 2),
            "vvix": round(float(model["vvix_levels"][i]), 2),
            "credit": round(float(model["credit_levels"][i]), 2),
            "ro": int(ro_i),
            "edr": int(edr_i),
            "tier": tier_i,
            "bounce": int(bounce_i),
        })

    result = {
        "scan_time": datetime.now().isoformat(),
        "market_open": market_open,
        "credit_proxy": proxy,
        "signal": signal,
        "history": history,
    }
    if backtest_results is not None:
        # Summarise backtest
        ro_days = sum(1 for r in backtest_results if r["ro"] == 1)
        edr_days = sum(1 for r in backtest_results if r.get("edr", 0) == 1)
        bounce_days = sum(1 for r in backtest_results if r.get("bounce", 0) == 1)
        result["backtest"] = {
            "days": len(backtest_results),
            "ro_signals": ro_days,
            "edr_days": edr_days,
            "bounce_days": bounce_days,
            "ro_pct": round(ro_days / len(backtest_results) * 100, 2) if backtest_results else 0,
            "daily": backtest_results,
        }

    return result


# ══════════════════════════════════════════════════════════════════
# HTML Report
# ══════════════════════════════════════════════════════════════════

def generate_html_report(
    signal: Dict[str, Any],
    model: Dict[str, np.ndarray],
    dates: List[str],
    proxy: str,
    market_open: bool,
    elapsed: float,
) -> str:
    """Generate a dark-themed HTML report for the VCG scan."""
    template_path = _PROJECT_DIR / ".pi/skills/html-report/template.html"
    template = template_path.read_text()

    now = datetime.now().strftime("%Y-%m-%d %I:%M %p ET")
    market_label = "LIVE" if market_open else "CLOSED"

    # Signal status
    if signal["ro"] == 1:
        tier_suffix = f" T{signal['tier']}" if signal["tier"] is not None else ""
        signal_label = f"RISK-OFF{tier_suffix}"
        signal_cls = "pill-negative"
    elif signal["edr"] == 1:
        signal_label = "EDR"
        signal_cls = "pill-warning"
    elif signal["bounce"] == 1:
        signal_label = "BOUNCE"
        signal_cls = "pill-positive"
    else:
        signal_label = "NORMAL"
        signal_cls = "pill-positive"

    vcg_val = f"{signal['vcg']:.4f}" if signal['vcg'] is not None else "N/A"
    vcg_adj_val = f"{signal['vcg_adj']:.4f}" if signal['vcg_adj'] is not None else "N/A"
    beta1_val = f"{signal['beta1_vvix']:.6f}" if signal['beta1_vvix'] is not None else "N/A"
    beta2_val = f"{signal['beta2_vix']:.6f}" if signal['beta2_vix'] is not None else "N/A"
    alpha_val = f"{signal['alpha']:.6f}" if signal['alpha'] is not None else "N/A"
    residual_val = f"{signal['residual']:.6f}" if signal['residual'] is not None else "N/A"
    attr = signal["attribution"]

    body_parts = []

    # Header
    body_parts.append(f"""
<header class="header">
  <div>
    <h1 class="title">Volatility-Credit Gap (VCG) Scan</h1>
    <p class="subtitle">{proxy} vs VIX/VVIX | {dates[-1]} | Market {market_label}</p>
  </div>
  <div class="header-actions">
    <span class="{signal_cls} pill">{signal_label}</span>
    <span class="timestamp">Generated: {now} ({elapsed:.1f}s)</span>
    <button class="theme-toggle" onclick="toggleTheme()">&#9684; THEME</button>
  </div>
</header>""")

    # Metrics row
    vcg_cls = "text-negative" if signal['vcg'] is not None and signal['vcg'] > VCG_TRIGGER else "text-positive" if signal['vcg'] is not None and signal['vcg'] < -VCG_TRIGGER else ""
    vvix_cls = "text-warning" if signal['vvix_severity'] != 'moderate' else ""
    tier_display = str(signal['tier']) if signal['tier'] is not None else "—"
    body_parts.append(f"""
<div class="metrics">
  <div class="metric">
    <div class="metric-label">VCG (z-score)</div>
    <div class="metric-value {vcg_cls}">{vcg_val}</div>
    <div class="metric-change">Panic-adj: {vcg_adj_val}</div>
  </div>
  <div class="metric">
    <div class="metric-label">VIX</div>
    <div class="metric-value">{signal['vix']:.2f}</div>
    <div class="metric-change">Regime: {signal['regime']}</div>
  </div>
  <div class="metric">
    <div class="metric-label">VVIX</div>
    <div class="metric-value {vvix_cls}">{signal['vvix']:.2f}</div>
    <div class="metric-change">Severity: {signal['vvix_severity'].upper()}</div>
  </div>
  <div class="metric">
    <div class="metric-label">{proxy}</div>
    <div class="metric-value">${signal['credit_price']:.2f}</div>
    <div class="metric-change">5d return: {signal['credit_5d_return_pct']:+.3f}%</div>
  </div>
  <div class="metric">
    <div class="metric-label">EDR</div>
    <div class="metric-value {"text-warning" if signal['edr'] else ""}">{signal['edr']}</div>
    <div class="metric-change">Tier: {tier_display}</div>
  </div>
  <div class="metric">
    <div class="metric-label">Risk-Off</div>
    <div class="metric-value {"text-negative" if signal['ro'] else "text-positive"}">{signal['ro']}</div>
    <div class="metric-change">Bounce: {signal['bounce']}</div>
  </div>
</div>""")

    # Signal conditions panel
    ro_vix_pass = signal['vix'] > VIX_FLOOR
    ro_vcg_pass = signal['vcg'] is not None and signal['vcg'] > VCG_RO_TRIGGER
    edr_vix_pass = signal['vix'] > VIX_EDR
    edr_vcg_pass = signal['vcg'] is not None and signal['vcg'] > VCG_TRIGGER
    vvix_sev = signal['vvix_severity'].upper()
    body_parts.append("""<div class="section-header">VCG Signal Conditions</div>""")
    body_parts.append("""<div class="panel"><table>
<thead><tr><th>Condition</th><th class="text-center">Required</th><th class="text-center">Actual</th>"""
                      """<th class="text-center">RO</th><th class="text-center">EDR</th></tr></thead><tbody>""")

    signal_rows = [
        (
            "VIX floor (stress required)",
            f"&gt; {VIX_FLOOR:.0f} (RO) / &gt; {VIX_EDR:.0f} (EDR)",
            f"{signal['vix']:.2f}",
            ro_vix_pass,
            edr_vix_pass,
        ),
        (
            "VCG z-score",
            f"&gt; {VCG_RO_TRIGGER:.1f} (RO) / &gt; {VCG_TRIGGER:.1f} (EDR)",
            vcg_val,
            ro_vcg_pass,
            edr_vcg_pass,
        ),
        (
            "&beta;&#8321; &le; 0 and &beta;&#8322; &le; 0 (sign discipline)",
            "Both &le; 0",
            "OK" if signal['sign_ok'] else "FAIL",
            signal['sign_ok'],
            signal['sign_ok'],
        ),
    ]
    for label, req, actual, ro_pass, edr_pass in signal_rows:
        ro_icon = '<span class="text-positive">&#10003;</span>' if ro_pass else '<span class="text-negative">&#10007;</span>'
        edr_icon = '<span class="text-positive">&#10003;</span>' if edr_pass else '<span class="text-negative">&#10007;</span>'
        body_parts.append(
            f'<tr><td>{label}</td><td class="text-center">{req}</td>'
            f'<td class="text-center">{actual}</td>'
            f'<td class="text-center">{ro_icon}</td>'
            f'<td class="text-center">{edr_icon}</td></tr>'
        )
    # VVIX severity row (amplifier, not a gate)
    body_parts.append(
        f'<tr><td>VVIX severity (amplifier, not a gate)</td>'
        f'<td class="text-center">—</td>'
        f'<td class="text-center">{signal["vvix"]:.2f} &rarr; {vvix_sev}</td>'
        f'<td class="text-center">—</td><td class="text-center">—</td></tr>'
    )
    # Bounce counter-signal row
    bounce_pass = signal['vcg'] is not None and signal['vcg'] < BOUNCE_TRIGGER
    bounce_icon = '<span class="text-positive">&#10003;</span>' if bounce_pass else '—'
    body_parts.append(
        f'<tr><td>Bounce counter-signal (tactical long)</td>'
        f'<td class="text-center">&lt; {BOUNCE_TRIGGER:.1f}</td>'
        f'<td class="text-center">{vcg_val}</td>'
        f'<td class="text-center">—</td><td class="text-center">{bounce_icon}</td></tr>'
    )
    body_parts.append("</tbody></table></div>")

    # Model coefficients panel
    sign_icon = '<span class="text-positive">OK</span>' if signal['sign_ok'] else '<span class="text-negative">SUPPRESSED</span>'
    body_parts.append("""<div class="section-header">OLS Model Coefficients (21-day rolling)</div>""")
    body_parts.append(f"""
<div class="grid-2">
  <div class="panel">
    <div class="panel-header">Regression: &Delta;{proxy} = &alpha; + &beta;&#8321;&middot;&Delta;VVIX + &beta;&#8322;&middot;&Delta;VIX + &epsilon;</div>
    <div class="panel-body">
      <table>
        <tr><td>&alpha; (intercept)</td><td class="text-right">{alpha_val}</td></tr>
        <tr><td>&beta;&#8321; (VVIX)</td><td class="text-right">{beta1_val}</td></tr>
        <tr><td>&beta;&#8322; (VIX)</td><td class="text-right">{beta2_val}</td></tr>
        <tr><td>Residual &epsilon;</td><td class="text-right">{residual_val}</td></tr>
        <tr><td>Sign discipline</td><td class="text-right">{sign_icon}</td></tr>
      </table>
    </div>
  </div>
  <div class="panel">
    <div class="panel-header">Attribution Split</div>
    <div class="panel-body">
      <table>
        <tr><td>VVIX component</td><td class="text-right">{attr['vvix_pct']:.1f}%</td></tr>
        <tr><td>VIX component</td><td class="text-right">{attr['vix_pct']:.1f}%</td></tr>
      </table>
      <div style="margin-top:12px">
        <div class="bar-container" style="width:100%">
          <div class="bar-fill" style="width:{attr['vvix_pct']:.0f}%;background:var(--warning);display:inline-block;height:100%"></div>
        </div>
        <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--text-muted);margin-top:4px">
          <span>VVIX {attr['vvix_pct']:.1f}%</span><span>VIX {attr['vix_pct']:.1f}%</span>
        </div>
      </div>
    </div>
  </div>
</div>""")

    # Interpretation callout
    _tier = signal["tier"]
    _tier_severity = "SEVERE" if _tier == 1 else ("HIGH" if _tier == 2 else "ELEVATED")
    _tier_label = f" (Tier {_tier} &mdash; {_tier_severity})" if _tier is not None else ""
    if signal["ro"] == 1:
        body_parts.append(f"""
<div class="callout negative">
  <div class="callout-title">RISK-OFF SIGNAL ACTIVE{_tier_label}</div>
  VCG = {vcg_val} exceeds +{VCG_RO_TRIGGER:.1f}&sigma; while VIX = {signal['vix']:.2f} &gt; {VIX_FLOOR:.0f} floor.
  VVIX severity: {signal['vvix_severity'].upper()}. Credit ({proxy}) is artificially calm relative to the volatility complex.
  <br><br>
  <strong>Recommended action:</strong> Reduce credit beta, raise quality, add convex hedges (HYG puts, bear put spreads).
</div>""")
    elif signal["edr"] == 1:
        body_parts.append(f"""
<div class="callout warning">
  <div class="callout-title">ELEVATED DIVERGENCE RISK (EDR){_tier_label}</div>
  EDR conditions met: VIX = {signal['vix']:.2f} &gt; {VIX_EDR:.0f} and VCG = {vcg_val} &gt; {VCG_TRIGGER:.1f}&sigma;.
  Not yet at RO trigger (needs VIX &gt; {VIX_FLOOR:.0f} and VCG &gt; {VCG_RO_TRIGGER:.1f}&sigma;). Monitor for escalation.
  VVIX severity: {signal['vvix_severity'].upper()}.
</div>""")
    elif signal["bounce"] == 1:
        body_parts.append(f"""
<div class="callout positive">
  <div class="callout-title">BOUNCE SIGNAL &mdash; TACTICAL LONG</div>
  VCG = {vcg_val} has breached the {BOUNCE_TRIGGER:.1f}&sigma; floor.
  Credit has over-adjusted relative to the volatility complex &mdash; potential mean-reversion long in credit.
</div>""")
    else:
        body_parts.append(f"""
<div class="callout positive">
  <div class="callout-title">NO SIGNAL &mdash; NORMAL REGIME</div>
  The volatility complex and credit markets are in alignment. No actionable divergence detected.
  VCG = {vcg_val}. Interpretation: {signal['interpretation'].replace('_', ' ').title()}.
</div>""")

    # Rolling residuals table (last 20 sessions)
    body_parts.append("""<hr class="divider"><div class="section-header">Rolling Model — Last 10 Trading Days</div>""")
    body_parts.append("""<div class="panel"><table>
<thead><tr>
  <th>Date</th>
  <th class="text-right">VIX</th>
  <th class="text-right">VVIX</th>
  <th class="text-right">Credit</th>
  <th class="text-right">Residual</th>
  <th class="text-right">VCG</th>
  <th class="text-right">VCG adj</th>
  <th class="text-right">&beta;&#8321;</th>
  <th class="text-right">&beta;&#8322;</th>
</tr></thead><tbody>""")

    n = len(model["residuals"])
    for i in range(max(0, n - 20), n):
        date_idx = i + 1
        d = dates[date_idx] if date_idx < len(dates) else "?"
        r = model["residuals"][i]
        v = model["vcg"][i]
        vd = model["vcg_div"][i]
        b1v = model["beta1"][i]
        b2v = model["beta2"][i]
        vix_l = model["vix_levels"][i]
        vvix_l = model["vvix_levels"][i]
        credit_l = model["credit_levels"][i]

        r_s = f"{r:.6f}" if not np.isnan(r) else "---"
        v_s = f"{v:.4f}" if not np.isnan(v) else "---"
        vd_s = f"{vd:.4f}" if not np.isnan(vd) else "---"
        b1_s = f"{b1v:.6f}" if not np.isnan(b1v) else "---"
        b2_s = f"{b2v:.6f}" if not np.isnan(b2v) else "---"

        vcg_cls = ""
        if not np.isnan(v):
            if v > VCG_TRIGGER:
                vcg_cls = "text-negative"
            elif v < -VCG_TRIGGER:
                vcg_cls = "text-positive"

        is_last = (i == n - 1)
        hl = ' class="highlight"' if is_last else ""

        body_parts.append(
            f'<tr{hl}><td>{d}</td>'
            f'<td class="text-right">{vix_l:.2f}</td>'
            f'<td class="text-right">{vvix_l:.2f}</td>'
            f'<td class="text-right">${credit_l:.2f}</td>'
            f'<td class="text-right">{r_s}</td>'
            f'<td class="text-right {vcg_cls}">{v_s}</td>'
            f'<td class="text-right">{vd_s}</td>'
            f'<td class="text-right">{b1_s}</td>'
            f'<td class="text-right">{b2_s}</td></tr>'
        )

    body_parts.append("</tbody></table></div>")

    # Footer
    body_parts.append(f"""
<div class="footer">
  <strong>VCG Scan — Volatility-Credit Gap</strong><br>
  Model: &Delta;{proxy} = &alpha; + &beta;&#8321;&middot;&Delta;VVIX + &beta;&#8322;&middot;&Delta;VIX + &epsilon;<br>
  OLS window: {OLS_WINDOW}d | Z-score window: {Z_WINDOW}d | RO trigger: VIX &gt; {VIX_FLOOR:.0f}, VCG &gt; {VCG_RO_TRIGGER:.1f}&sigma; | EDR: VIX &gt; {VIX_EDR:.0f}, VCG &gt; {VCG_TRIGGER:.1f}&sigma;<br>
  Data: {"IB (primary)" if True else "Yahoo Finance (fallback)"} | {now}<br>
  Strategy spec: <code>docs/strategies.md</code> (Strategy 5) | Math: <code>docs/VCG_institutional_research_note.md</code>
</div>""")

    body = "\n".join(body_parts)
    title = f"VCG Scan — {proxy} | {dates[-1]}"
    html = template.replace("{{TITLE}}", title)
    html = html.replace("{{BODY}}", body)
    return html


# ══════════════════════════════════════════════════════════════════
# Market Hours Check
# ══════════════════════════════════════════════════════════════════

def is_market_open() -> bool:
    """Check if US equity markets are currently open."""
    from datetime import timezone as tz
    import zoneinfo
    try:
        et = zoneinfo.ZoneInfo("America/New_York")
    except Exception:
        # Fallback: estimate ET from UTC
        now_utc = datetime.now(tz.utc)
        et_offset = timedelta(hours=-5)  # EST (approximate)
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
        description="Cross-Asset Volatility-Credit Gap (VCG) Scanner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
The VCG detects divergence between the volatility complex (VIX/VVIX) and
cash credit markets (HYG/JNK/LQD).  When VVIX spikes but credit stays
calm, the VCG fires a risk-off overlay signal.

Examples:
  python3 scripts/vcg_scan.py                  # Human-readable summary
  python3 scripts/vcg_scan.py --json           # JSON output
  python3 scripts/vcg_scan.py --proxy JNK      # Use JNK as credit proxy
  python3 scripts/vcg_scan.py --backtest --days 252   # 1-year backtest
""",
    )
    parser.add_argument("--proxy", default="HYG", help="Credit proxy ticker (default: HYG)")
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    parser.add_argument("--backtest", action="store_true", help="Run rolling backtest")
    parser.add_argument("--days", type=int, default=252, help="Backtest lookback days (default: 252)")
    parser.add_argument("--no-open", action="store_true", help="Don't open HTML report in browser")
    parser.add_argument("--output", "-o", help="Custom output path for HTML")
    parser.add_argument(
        "--vix-floor", type=float, default=VIX_FLOOR,
        help=f"VIX floor for RO trigger — VIX must be ABOVE this (default: {VIX_FLOOR})",
    )
    parser.add_argument(
        "--vcg-trigger", type=float, default=VCG_RO_TRIGGER,
        help=f"VCG z-score threshold for RO trigger (default: {VCG_RO_TRIGGER})",
    )

    args = parser.parse_args()
    proxy = args.proxy.upper()
    tickers = ["VIX", "VVIX", proxy]

    market_open = is_market_open()

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"VCG SCANNER — {proxy} vs VIX/VVIX", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    if not market_open:
        print(f"  Market closed — using last available data.", file=sys.stderr)

    t_start = time.time()

    # Fetch data
    aligned, common_dates = fetch_all(tickers)

    vix_prices = aligned["VIX"]
    vvix_prices = aligned["VVIX"]
    credit_prices = aligned[proxy]

    print(f"  Data range: {common_dates[0]} to {common_dates[-1]} ({len(common_dates)} bars)", file=sys.stderr)

    # Compute VCG
    model = compute_vcg(vix_prices, vvix_prices, credit_prices)

    # Evaluate current signal
    signal = evaluate_signal(model, credit_prices, vix_floor=args.vix_floor, vcg_trigger=args.vcg_trigger)

    # Optional backtest
    bt = None
    if args.backtest:
        bt = backtest_signals(model, credit_prices, args.days, vix_floor=args.vix_floor, vcg_trigger=args.vcg_trigger)
        ro_days = sum(1 for r in bt if r["ro"] == 1)
        edr_days = sum(1 for r in bt if r.get("edr", 0) == 1)
        bounce_days = sum(1 for r in bt if r.get("bounce", 0) == 1)
        print(
            f"\n  Backtest ({args.days}d): {ro_days} RO signals, "
            f"{edr_days} EDR days, {bounce_days} bounce days",
            file=sys.stderr,
        )

    elapsed = time.time() - t_start

    # Output
    if args.json:
        result = build_json_output(signal, model, common_dates, proxy, market_open, bt, vix_floor=args.vix_floor, vcg_trigger=args.vcg_trigger)
        print(json.dumps(result, indent=2))
    else:
        # Print summary
        print_summary(signal, model, common_dates, proxy, market_open)

        # Generate HTML report
        html = generate_html_report(signal, model, common_dates, proxy, market_open, elapsed)
        date_str = datetime.now().strftime("%Y-%m-%d")
        out_path = Path(args.output) if args.output else _PROJECT_DIR / f"reports/vcg-scan-{date_str}.html"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html)
        print(f"  Report: {out_path}", file=sys.stderr)

        if not args.no_open:
            import webbrowser
            webbrowser.open(f"file://{out_path.resolve()}")


if __name__ == "__main__":
    main()
