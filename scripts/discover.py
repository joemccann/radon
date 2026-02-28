#!/usr/bin/env python3
"""
Discover new trading candidates via market-wide flow analysis.
Finds tickers with unusual options flow and validates with dark pool data.

Scoring: Normalized 0-100 scale based on edge quality, not dollar size.

Requires UW_TOKEN environment variable.
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

BASE_URL = "https://api.unusualwhales.com/api"
WATCHLIST = Path(__file__).parent.parent / "data" / "watchlist.json"
PORTFOLIO = Path(__file__).parent.parent / "data" / "portfolio.json"

# Market holidays 2026
MARKET_HOLIDAYS_2026 = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03",
    "2026-05-25", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
}

# Scoring weights (must sum to 100)
WEIGHTS = {
    "dp_strength": 30,      # Dark pool flow strength (0-100)
    "dp_sustained": 20,     # Consecutive days same direction
    "confluence": 20,       # Options + DP alignment
    "vol_oi": 15,           # Unusual volume/OI ratio
    "sweeps": 15,           # Sweep trades (urgency)
}


def _get_token() -> str:
    token = os.environ.get("UW_TOKEN")
    if not token:
        print(json.dumps({"error": "UW_TOKEN not set"}), file=sys.stderr)
        sys.exit(1)
    return token


def _api_get(path: str) -> dict:
    req = Request(f"{BASE_URL}{path}", headers={
        "Accept": "application/json",
        "Authorization": f"Bearer {_get_token()}",
        "User-Agent": "convex-scavenger/1.0",
    })
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        return {"error": f"HTTP {e.code}"}
    except URLError as e:
        return {"error": str(e)}


def is_market_open(date: datetime) -> bool:
    if date.weekday() >= 5:
        return False
    return date.strftime("%Y-%m-%d") not in MARKET_HOLIDAYS_2026


def get_last_n_trading_days(n: int) -> list:
    days = []
    current = datetime.now()
    if not is_market_open(current) or current.hour < 16:
        current -= timedelta(days=1)
    while len(days) < n:
        if is_market_open(current):
            days.append(current.strftime("%Y-%m-%d"))
        current -= timedelta(days=1)
    return days


def get_existing_tickers() -> set:
    """Get tickers already in watchlist or portfolio."""
    tickers = set()
    if WATCHLIST.exists():
        with open(WATCHLIST) as f:
            data = json.load(f)
            tickers.update(t["ticker"] for t in data.get("tickers", []))
    if PORTFOLIO.exists():
        with open(PORTFOLIO) as f:
            data = json.load(f)
            tickers.update(p["ticker"] for p in data.get("positions", []))
    return tickers


def fetch_options_flow(min_premium: int = 500000, limit: int = 100) -> list:
    """Fetch market-wide options flow alerts."""
    resp = _api_get(f"/option-trades/flow-alerts?min_premium={min_premium}&limit={limit}")
    return resp.get("data", [])


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


def fetch_darkpool_multi(ticker: str, days: int = 3) -> dict:
    """Fetch multiple days of dark pool data for sustained direction analysis."""
    trading_days = get_last_n_trading_days(days)
    
    daily_results = []
    all_trades = []
    
    for date in trading_days:
        resp = _api_get(f"/darkpool/{ticker}?date={date}")
        trades = resp.get("data", [])
        if isinstance(trades, list):
            day_analysis = analyze_darkpool_day(trades)
            day_analysis["date"] = date
            daily_results.append(day_analysis)
            all_trades.extend(trades)
    
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


def discover(min_premium: int = 500000, min_alerts: int = 1,
             dp_days: int = 3, exclude_indices: bool = True) -> dict:
    """
    Discover new trading candidates from market-wide flow.
    
    Premium is a FILTER (min threshold), not a scoring component.
    Score is based on edge quality: DP strength, sustained direction, confluence.
    """
    print("Fetching market-wide options flow...", file=sys.stderr)
    alerts = fetch_options_flow(min_premium=min_premium, limit=200)
    
    if not alerts:
        return {"error": "No flow alerts found", "candidates": []}
    
    print(f"  Found {len(alerts)} alerts (>= ${min_premium/1000:.0f}K premium)", file=sys.stderr)
    
    existing = get_existing_tickers()
    index_symbols = {"SPX", "SPXW", "NDX", "RUT", "VIX", "DJX", "OEX", "XSP"}
    
    # Aggregate options flow by ticker
    ticker_data = defaultdict(lambda: {
        "alerts": 0, "total_premium": 0, "calls": 0, "puts": 0,
        "sweeps": 0, "vol_oi_ratios": [], "sector": "", "marketcap": 0,
        "underlying_price": 0, "issue_type": ""
    })
    
    for a in alerts:
        ticker = a.get("ticker", "")
        if not ticker or (exclude_indices and ticker in index_symbols):
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
    
    # Filter and score candidates
    candidates = []
    tickers_to_check = [t for t in ticker_data.keys() if t not in existing and ticker_data[t]["alerts"] >= min_alerts]
    
    print(f"  Checking {len(tickers_to_check)} candidates with DP data ({dp_days} days)...", file=sys.stderr)
    
    for i, ticker in enumerate(tickers_to_check, 1):
        data = ticker_data[ticker]
        print(f"    [{i}/{len(tickers_to_check)}] {ticker}...", file=sys.stderr, end=" ", flush=True)
        
        # Fetch dark pool data
        dp = fetch_darkpool_multi(ticker, days=dp_days)
        dp_agg = dp["aggregate"]
        
        # Determine options bias
        if data["calls"] > data["puts"] * 1.5:
            options_bias = "BULLISH"
        elif data["puts"] > data["calls"] * 1.5:
            options_bias = "BEARISH"
        else:
            options_bias = "MIXED"
        
        # Check confluence
        has_confluence = (
            (options_bias == "BULLISH" and dp_agg["direction"] == "ACCUMULATION") or
            (options_bias == "BEARISH" and dp_agg["direction"] == "DISTRIBUTION")
        )
        
        # Average vol/OI ratio
        avg_vol_oi = sum(data["vol_oi_ratios"]) / len(data["vol_oi_ratios"]) if data["vol_oi_ratios"] else 0
        
        # Calculate normalized score
        score_data = calculate_score(
            dp_strength=dp_agg["strength"],
            dp_sustained=dp["sustained_days"],
            has_confluence=has_confluence,
            vol_oi_ratio=avg_vol_oi,
            sweep_count=data["sweeps"],
            alert_count=data["alerts"]
        )
        
        print(f"Score: {score_data['total']}", file=sys.stderr)
        
        candidate = {
            "ticker": ticker,
            "score": score_data["total"],
            "score_breakdown": score_data["weighted"],
            "alerts": data["alerts"],
            "total_premium": data["total_premium"],
            "calls": data["calls"],
            "puts": data["puts"],
            "options_bias": options_bias,
            "sweeps": data["sweeps"],
            "avg_vol_oi": round(avg_vol_oi, 2),
            "sector": data["sector"],
            "issue_type": data["issue_type"],
            "dp_direction": dp_agg["direction"],
            "dp_strength": dp_agg["strength"],
            "dp_buy_ratio": dp_agg["buy_ratio"],
            "dp_sustained_days": dp["sustained_days"],
            "dp_total_prints": dp["total_prints"],
            "confluence": has_confluence,
        }
        
        candidates.append(candidate)
    
    # Sort by score descending
    candidates.sort(key=lambda x: x["score"], reverse=True)
    
    return {
        "discovery_time": datetime.now().isoformat(),
        "scoring_weights": WEIGHTS,
        "alerts_analyzed": len(alerts),
        "candidates_found": len(candidates),
        "candidates": candidates[:20]
    }


def main():
    p = argparse.ArgumentParser(description="Discover new trading candidates (normalized 0-100 scoring)")
    p.add_argument("--min-premium", type=int, default=500000,
                   help="Minimum premium filter (default $500k)")
    p.add_argument("--min-alerts", type=int, default=1,
                   help="Minimum alerts per ticker (default 1)")
    p.add_argument("--dp-days", type=int, default=3,
                   help="Days of dark pool data to check (default 3)")
    p.add_argument("--include-indices", action="store_true",
                   help="Include index options (SPX, etc)")
    args = p.parse_args()
    
    result = discover(
        min_premium=args.min_premium,
        min_alerts=args.min_alerts,
        dp_days=args.dp_days,
        exclude_indices=not args.include_indices
    )
    
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
