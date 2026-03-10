#!/usr/bin/env python3
"""
Discover new trading candidates via dark pool + options flow analysis.

Two modes:
  1. Market-wide (default): Fetch flow alerts → aggregate → validate with DP
  2. Targeted: Pass tickers or a preset → fetch per-ticker flow + DP directly

Scoring: Normalized 0-100 scale based on edge quality, not dollar size.

Requires UW_TOKEN environment variable.

API Reference: docs/unusual_whales_api.md
Full Spec: docs/unusual_whales_api_spec.yaml

Key endpoints used:
  - GET /api/option-trades/flow-alerts - Market-wide or per-ticker flow alerts
  - GET /api/darkpool/{ticker} - Dark pool validation
  - GET /api/stock/{ticker}/info - Ticker metadata
"""

import argparse
import json
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from clients.uw_client import UWClient, UWAPIError
from utils.market_calendar import (
    get_last_n_trading_days,
    load_holidays,
    _is_trading_day,
)

WATCHLIST = Path(__file__).parent.parent / "data" / "watchlist.json"
PORTFOLIO = Path(__file__).parent.parent / "data" / "portfolio.json"

# Keep for backward compatibility with existing tests
MARKET_HOLIDAYS_2026 = load_holidays(2026)


def is_market_open(date: datetime) -> bool:
    """Check if market is open on a given date (date-only, no time check).

    Backward-compatible wrapper used by existing tests and internal logic.
    """
    return _is_trading_day(date)


# Scoring weights (must sum to 100)
WEIGHTS = {
    "dp_strength": 30,      # Dark pool flow strength (0-100)
    "dp_sustained": 20,     # Consecutive days same direction
    "confluence": 20,       # Options + DP alignment
    "vol_oi": 15,           # Unusual volume/OI ratio
    "sweeps": 15,           # Sweep trades (urgency)
}


def get_existing_tickers() -> set:
    """Get tickers already in watchlist or portfolio."""
    tickers = set()
    if WATCHLIST.exists():
        with open(WATCHLIST) as f:
            data = json.load(f)
            tickers.update(t.get("ticker") or t.get("symbol", "") for t in data.get("tickers", []) if isinstance(t, dict))
    if PORTFOLIO.exists():
        with open(PORTFOLIO) as f:
            data = json.load(f)
            tickers.update(p["ticker"] for p in data.get("positions", []))
    return tickers


def fetch_options_flow(min_premium: int = 500000, limit: int = 100, _client: UWClient = None) -> list:
    """Fetch market-wide options flow alerts."""
    def _fetch(client):
        try:
            resp = client.get_flow_alerts(min_premium=min_premium, limit=limit)
            return resp.get("data", [])
        except UWAPIError:
            return []

    if _client is not None:
        return _fetch(_client)
    with UWClient() as client:
        return _fetch(client)


def analyze_darkpool_day(trades: list) -> dict:
    """Analyze a single day's dark pool trades."""
    if not trades:
        return {"buy_ratio": None, "direction": "NO_DATA", "strength": 0, "prints": 0}
    
    buy_vol = sell_vol = 0
    for t in trades:
        if t.get("canceled"):
            continue
        size = int(t.get("size", 0))
        price = float(t.get("price", 0))
        bid = float(t.get("nbbo_bid", 0))
        ask = float(t.get("nbbo_ask", 0))
        if bid > 0 and ask > 0:
            mid = (bid + ask) / 2
            if price >= mid:
                buy_vol += size
            else:
                sell_vol += size
    
    total = buy_vol + sell_vol
    ratio = buy_vol / total if total > 0 else None
    
    if ratio is None:
        return {"buy_ratio": None, "direction": "NO_DATA", "strength": 0, "prints": len(trades)}
    
    if ratio >= 0.55:
        direction = "ACCUMULATION"
        strength = (ratio - 0.5) * 200  # 0-100 scale
    elif ratio <= 0.45:
        direction = "DISTRIBUTION"
        strength = (0.5 - ratio) * 200
    else:
        direction = "NEUTRAL"
        strength = 0
    
    return {
        "buy_ratio": round(ratio, 4),
        "direction": direction,
        "strength": round(min(strength, 100), 1),
        "prints": len(trades)
    }


def fetch_darkpool_multi(ticker: str, days: int = 3, _client: UWClient = None) -> dict:
    """Fetch multiple days of dark pool data for sustained direction analysis.

    Always includes today (if trading day) even during market hours.
    """
    from datetime import datetime
    trading_days = get_last_n_trading_days(days)
    today_str = datetime.now().strftime("%Y-%m-%d")
    if _is_trading_day(datetime.now()) and today_str not in trading_days:
        trading_days.insert(0, today_str)

    daily_results = []
    all_trades = []

    def _fetch_day(client, date):
        try:
            resp = client.get_darkpool_flow(ticker, date=date)
        except UWAPIError:
            return None, []
        trades = resp.get("data", [])
        if isinstance(trades, list):
            day_analysis = analyze_darkpool_day(trades)
            day_analysis["date"] = date
            return day_analysis, trades
        return None, []

    def _fetch(client):
        with ThreadPoolExecutor(max_workers=len(trading_days)) as pool:
            futures = {pool.submit(_fetch_day, client, d): d for d in trading_days}
            # Collect in date order
            results_by_date = {}
            for future in as_completed(futures):
                date = futures[future]
                day_analysis, trades = future.result()
                results_by_date[date] = (day_analysis, trades)
            for date in trading_days:
                day_analysis, trades = results_by_date.get(date, (None, []))
                if day_analysis:
                    daily_results.append(day_analysis)
                    all_trades.extend(trades)

    if _client is not None:
        _fetch(_client)
    else:
        with UWClient() as client:
            _fetch(client)

    # Aggregate analysis
    aggregate = analyze_darkpool_day(all_trades)

    # Calculate sustained direction (consecutive days same direction)
    sustained = 0
    if daily_results:
        first_dir = daily_results[0]["direction"]
        if first_dir in ("ACCUMULATION", "DISTRIBUTION"):
            sustained = 1
            for d in daily_results[1:]:
                if d["direction"] == first_dir:
                    sustained += 1
                else:
                    break

    return {
        "aggregate": aggregate,
        "daily": daily_results,
        "sustained_days": sustained,
        "total_prints": sum(d["prints"] for d in daily_results)
    }


def calculate_score(
    dp_strength: float,
    dp_sustained: int,
    has_confluence: bool,
    vol_oi_ratio: float,
    sweep_count: int,
    alert_count: int
) -> dict:
    """
    Calculate normalized 0-100 score based on edge quality.
    
    Returns dict with total score and component breakdown.
    """
    # DP Strength: already 0-100
    dp_strength_score = min(dp_strength, 100)
    
    # DP Sustained: 0-5 days → 0-100 (1 day = 20, 5 days = 100)
    dp_sustained_score = min(dp_sustained * 20, 100)
    
    # Confluence: binary 0 or 100
    confluence_score = 100 if has_confluence else 0
    
    # Vol/OI: normalize (1.0 = normal, 2.0+ = unusual)
    # Scale: 0-1 = 0, 1-2 = 0-50, 2-4 = 50-100, >4 = 100
    if vol_oi_ratio <= 1.0:
        vol_oi_score = 0
    elif vol_oi_ratio <= 2.0:
        vol_oi_score = (vol_oi_ratio - 1.0) * 50
    elif vol_oi_ratio <= 4.0:
        vol_oi_score = 50 + (vol_oi_ratio - 2.0) * 25
    else:
        vol_oi_score = 100
    
    # Sweeps: presence and count (1 = 50, 2+ = 100)
    if sweep_count == 0:
        sweep_score = 0
    elif sweep_count == 1:
        sweep_score = 50
    else:
        sweep_score = 100
    
    # Weighted total
    total = (
        dp_strength_score * WEIGHTS["dp_strength"] / 100 +
        dp_sustained_score * WEIGHTS["dp_sustained"] / 100 +
        confluence_score * WEIGHTS["confluence"] / 100 +
        vol_oi_score * WEIGHTS["vol_oi"] / 100 +
        sweep_score * WEIGHTS["sweeps"] / 100
    )
    
    return {
        "total": round(total, 1),
        "components": {
            "dp_strength": round(dp_strength_score, 1),
            "dp_sustained": round(dp_sustained_score, 1),
            "confluence": round(confluence_score, 1),
            "vol_oi": round(vol_oi_score, 1),
            "sweeps": round(sweep_score, 1),
        },
        "weighted": {
            "dp_strength": round(dp_strength_score * WEIGHTS["dp_strength"] / 100, 1),
            "dp_sustained": round(dp_sustained_score * WEIGHTS["dp_sustained"] / 100, 1),
            "confluence": round(confluence_score * WEIGHTS["confluence"] / 100, 1),
            "vol_oi": round(vol_oi_score * WEIGHTS["vol_oi"] / 100, 1),
            "sweeps": round(sweep_score * WEIGHTS["sweeps"] / 100, 1),
        }
    }


def resolve_tickers(args_tickers: list) -> list:
    """Resolve a mix of preset names and raw tickers into a flat ticker list.

    Each element in *args_tickers* is either:
      - A bare ticker symbol  (e.g. "AAPL")
      - A preset name         (e.g. "ndx100", "sp500-semiconductors")

    Presets are detected by checking if a matching file exists in data/presets/.
    """
    from utils.presets import load_preset, PRESETS_DIR

    tickers: list[str] = []
    seen: set[str] = set()

    for token in args_tickers:
        token_lower = token.lower().replace(".json", "")
        preset_path = PRESETS_DIR / f"{token_lower}.json"
        if preset_path.exists():
            preset = load_preset(token_lower)
            print(f"  Loaded preset '{preset.name}': {preset.ticker_count} tickers", file=sys.stderr)
            for t in preset.tickers:
                if t not in seen:
                    tickers.append(t)
                    seen.add(t)
        else:
            sym = token.upper()
            if sym not in seen:
                tickers.append(sym)
                seen.add(sym)

    return tickers


def _build_candidate(ticker: str, flow_data: dict, dp: dict) -> dict:
    """Build a scored candidate dict from per-ticker flow + dark pool data."""
    dp_agg = dp["aggregate"]

    alerts = flow_data["alerts"]
    calls = flow_data["calls"]
    puts = flow_data["puts"]
    sweeps = flow_data["sweeps"]
    vol_oi_ratios = flow_data["vol_oi_ratios"]

    # Determine options bias
    if calls > puts * 1.5:
        options_bias = "BULLISH"
    elif puts > calls * 1.5:
        options_bias = "BEARISH"
    else:
        options_bias = "MIXED"

    # Check confluence
    has_confluence = (
        (options_bias == "BULLISH" and dp_agg["direction"] == "ACCUMULATION") or
        (options_bias == "BEARISH" and dp_agg["direction"] == "DISTRIBUTION")
    )

    avg_vol_oi = (
        sum(vol_oi_ratios) / len(vol_oi_ratios)
        if vol_oi_ratios else 0
    )

    score_data = calculate_score(
        dp_strength=dp_agg["strength"],
        dp_sustained=dp["sustained_days"],
        has_confluence=has_confluence,
        vol_oi_ratio=avg_vol_oi,
        sweep_count=sweeps,
        alert_count=alerts,
    )

    return {
        "ticker": ticker,
        "score": score_data["total"],
        "score_breakdown": score_data["weighted"],
        "alerts": alerts,
        "total_premium": flow_data["total_premium"],
        "calls": calls,
        "puts": puts,
        "options_bias": options_bias,
        "sweeps": sweeps,
        "avg_vol_oi": round(avg_vol_oi, 2),
        "sector": flow_data.get("sector", ""),
        "issue_type": flow_data.get("issue_type", ""),
        "dp_direction": dp_agg["direction"],
        "dp_strength": dp_agg["strength"],
        "dp_buy_ratio": dp_agg["buy_ratio"],
        "dp_sustained_days": dp["sustained_days"],
        "dp_total_prints": dp["total_prints"],
        "confluence": has_confluence,
    }


def _aggregate_alerts(alerts: list) -> dict:
    """Aggregate a list of UW flow alerts into per-ticker flow_data dicts."""
    ticker_data = defaultdict(lambda: {
        "alerts": 0, "total_premium": 0, "calls": 0, "puts": 0,
        "sweeps": 0, "vol_oi_ratios": [], "sector": "", "marketcap": 0,
        "underlying_price": 0, "issue_type": ""
    })

    for a in alerts:
        ticker = a.get("ticker", "")
        if not ticker:
            continue

        prem = float(a.get("total_premium") or 0)
        opt_type = (a.get("type") or "").upper()
        vol_oi = float(a.get("volume_oi_ratio") or 0)

        ticker_data[ticker]["alerts"] += 1
        ticker_data[ticker]["total_premium"] += prem
        if opt_type == "CALL":
            ticker_data[ticker]["calls"] += 1
        elif opt_type == "PUT":
            ticker_data[ticker]["puts"] += 1
        if a.get("has_sweep"):
            ticker_data[ticker]["sweeps"] += 1
        if vol_oi > 0:
            ticker_data[ticker]["vol_oi_ratios"].append(vol_oi)
        ticker_data[ticker]["sector"] = a.get("sector") or ""
        ticker_data[ticker]["marketcap"] = a.get("marketcap") or 0
        ticker_data[ticker]["underlying_price"] = a.get("underlying_price") or 0
        ticker_data[ticker]["issue_type"] = a.get("issue_type") or ""

    return dict(ticker_data)


def discover_targeted(tickers: list, dp_days: int = 3,
                      min_premium: int = 50000, top: int = 20) -> dict:
    """
    Discover edge signals for an explicit list of tickers.

    Unlike the market-wide scan, this fetches per-ticker flow alerts and dark
    pool data for every ticker in the list — no watchlist filtering, no
    market-wide flow scan.
    """
    print(f"Scanning {len(tickers)} tickers (targeted mode)...", file=sys.stderr)

    candidates = []
    total = len(tickers)

    with UWClient() as client:
        def _process_targeted(ticker):
            try:
                resp = client.get_flow_alerts(ticker=ticker, min_premium=min_premium, limit=50)
                alerts = resp.get("data", [])
            except UWAPIError:
                alerts = []

            flow_data = _aggregate_alerts(alerts).get(ticker, {
                "alerts": 0, "total_premium": 0, "calls": 0, "puts": 0,
                "sweeps": 0, "vol_oi_ratios": [], "sector": "", "issue_type": ""
            })

            dp = fetch_darkpool_multi(ticker, days=dp_days, _client=client)
            return _build_candidate(ticker, flow_data, dp)

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = {pool.submit(_process_targeted, t): t for t in tickers}
            done = 0
            for future in as_completed(futures):
                done += 1
                ticker = futures[future]
                candidate = future.result()
                print(f"  [{done}/{total}] {ticker}... Score: {candidate['score']}", file=sys.stderr)
                candidates.append(candidate)

    candidates.sort(key=lambda x: x["score"], reverse=True)

    return {
        "discovery_time": datetime.now().isoformat(),
        "mode": "targeted",
        "tickers_scanned": len(tickers),
        "scoring_weights": WEIGHTS,
        "candidates_found": len(candidates),
        "candidates": candidates[:top],
    }


def discover(min_premium: int = 500000, min_alerts: int = 1,
             dp_days: int = 3, exclude_indices: bool = True,
             tickers: list = None, top: int = 20) -> dict:
    """
    Discover new trading candidates.

    If *tickers* is provided, runs targeted mode: per-ticker flow + DP scan.
    Otherwise runs market-wide mode: fetch all flow alerts → aggregate → DP.

    Premium is a FILTER (min threshold), not a scoring component.
    Score is based on edge quality: DP strength, sustained direction, confluence.
    """
    # --- Targeted mode ---
    if tickers:
        return discover_targeted(
            tickers, dp_days=dp_days, min_premium=min_premium, top=top,
        )

    # --- Market-wide mode (original behavior) ---
    print("Fetching market-wide options flow...", file=sys.stderr)

    with UWClient() as client:
        alerts = fetch_options_flow(min_premium=min_premium, limit=200, _client=client)

        if not alerts:
            return {
                "discovery_time": datetime.now().isoformat(),
                "mode": "market-wide",
                "scoring_weights": WEIGHTS,
                "alerts_analyzed": 0,
                "candidates_found": 0,
                "candidates": []
            }

        print(f"  Found {len(alerts)} alerts (>= ${min_premium/1000:.0f}K premium)", file=sys.stderr)

        existing = get_existing_tickers()
        index_symbols = {"SPX", "SPXW", "NDX", "RUT", "VIX", "DJX", "OEX", "XSP"}

        # Aggregate options flow by ticker
        all_flow = _aggregate_alerts(alerts)

        # Filter: exclude existing watchlist/portfolio, indices, low-alert
        tickers_to_check = [
            t for t in all_flow.keys()
            if t not in existing
            and all_flow[t]["alerts"] >= min_alerts
            and not (exclude_indices and t in index_symbols)
        ]

        print(f"  Checking {len(tickers_to_check)} candidates with DP data ({dp_days} days)...", file=sys.stderr)

        candidates = []
        total = len(tickers_to_check)

        def _process_candidate(ticker):
            dp = fetch_darkpool_multi(ticker, days=dp_days, _client=client)
            return _build_candidate(ticker, all_flow[ticker], dp)

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = {pool.submit(_process_candidate, t): t for t in tickers_to_check}
            done = 0
            for future in as_completed(futures):
                done += 1
                ticker = futures[future]
                candidate = future.result()
                print(f"    [{done}/{total}] {ticker}... Score: {candidate['score']}", file=sys.stderr)
                candidates.append(candidate)

    # Sort by score descending
    candidates.sort(key=lambda x: x["score"], reverse=True)

    return {
        "discovery_time": datetime.now().isoformat(),
        "mode": "market-wide",
        "scoring_weights": WEIGHTS,
        "alerts_analyzed": len(alerts),
        "candidates_found": len(candidates),
        "candidates": candidates[:top]
    }


def main():
    p = argparse.ArgumentParser(
        description="Discover new trading candidates (normalized 0-100 scoring)",
        epilog="Examples:\n"
               "  python3 discover.py                         # market-wide scan\n"
               "  python3 discover.py AAPL MSFT NVDA          # scan specific tickers\n"
               "  python3 discover.py ndx100                  # scan NASDAQ 100 preset\n"
               "  python3 discover.py ndx100-semiconductors   # scan a sub-preset\n"
               "  python3 discover.py ndx100 WULF CRWV        # mix preset + tickers\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("tickers", nargs="*",
                   help="Tickers and/or preset names (e.g. AAPL ndx100). "
                        "Omit for market-wide scan.")
    p.add_argument("--min-premium", type=int, default=None,
                   help="Minimum premium filter (default $500k market-wide, $50k targeted)")
    p.add_argument("--min-alerts", type=int, default=1,
                   help="Minimum alerts per ticker in market-wide mode (default 1)")
    p.add_argument("--dp-days", type=int, default=3,
                   help="Days of dark pool data to check (default 3)")
    p.add_argument("--top", type=int, default=20,
                   help="Number of top candidates to return (default 20)")
    p.add_argument("--include-indices", action="store_true",
                   help="Include index options (SPX, etc) in market-wide mode")
    args = p.parse_args()

    # Resolve tickers/presets if any were given
    resolved = resolve_tickers(args.tickers) if args.tickers else None

    # Default min-premium depends on mode
    if args.min_premium is not None:
        min_prem = args.min_premium
    else:
        min_prem = 50000 if resolved else 500000

    result = discover(
        min_premium=min_prem,
        min_alerts=args.min_alerts,
        dp_days=args.dp_days,
        exclude_indices=not args.include_indices,
        tickers=resolved,
        top=args.top,
    )

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
