#!/usr/bin/env python3
"""
Scan watchlist for dark pool flow signals.
Ranks tickers by flow strength and filters for actionable signals.

API Reference: docs/unusual_whales_api.md
Full Spec: docs/unusual_whales_api_spec.yaml

Uses fetch_flow.py internally which calls:
  - GET /api/darkpool/{ticker} - Dark pool flow data
"""
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from clients.uw_client import UWRateLimitError
from fetch_flow import fetch_flow as fetch_flow_module

logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
WATCHLIST = PROJECT_DIR / "data" / "watchlist.json"
PORTFOLIO = PROJECT_DIR / "data" / "portfolio.json"

def get_open_positions():
    """Get list of tickers with open positions."""
    if not PORTFOLIO.exists():
        return set()
    with open(PORTFOLIO) as f:
        portfolio = json.load(f)
    return {p["ticker"] for p in portfolio.get("positions", [])}

def fetch_flow_data(ticker: str, days: int = 5) -> dict:
    """Fetch flow data for a single ticker via direct import."""
    try:
        return fetch_flow_module(ticker, days)
    except Exception as e:
        return {"error": str(e)}

# Keep old name as alias so existing call sites work
fetch_flow = fetch_flow_data

def analyze_signal(flow_data: dict) -> dict:
    """Extract key metrics from flow data."""
    if "error" in flow_data:
        return {"score": -1, "signal": "ERROR", "error": flow_data["error"]}

    dp = flow_data.get("dark_pool", {})
    agg = dp.get("aggregate", {})
    daily = dp.get("daily", [])

    direction = agg.get("flow_direction", "UNKNOWN")
    strength = agg.get("flow_strength", 0)
    buy_ratio = agg.get("dp_buy_ratio")
    num_prints = agg.get("num_prints", 0)

    # Check for sustained direction (3+ consecutive days)
    sustained = 0
    if daily:
        current_dir = daily[0].get("flow_direction")
        for d in daily[1:]:
            if d.get("flow_direction") == current_dir and current_dir in ("ACCUMULATION", "DISTRIBUTION"):
                sustained += 1
            else:
                break

    # Check most recent day's direction and strength
    recent_dir = daily[0].get("flow_direction") if daily else "UNKNOWN"
    recent_strength = daily[0].get("flow_strength", 0) if daily else 0

    # Score: higher = more actionable
    # Base score from aggregate strength
    score = strength

    # Bonus for sustained direction
    if sustained >= 2:
        score += 20
    if sustained >= 4:
        score += 20

    # Bonus if recent day confirms aggregate
    if recent_dir == direction and recent_strength > 50:
        score += 15

    # Penalty if recent day contradicts aggregate
    if recent_dir != direction and recent_dir in ("ACCUMULATION", "DISTRIBUTION"):
        score -= 30

    # Penalty for low print count (statistically unreliable)
    if num_prints < 50:
        score -= 20
    elif num_prints < 100:
        score -= 10

    # Determine signal quality
    if score >= 60 and direction in ("ACCUMULATION", "DISTRIBUTION"):
        signal = "STRONG"
    elif score >= 40 and direction in ("ACCUMULATION", "DISTRIBUTION"):
        signal = "MODERATE"
    elif direction in ("ACCUMULATION", "DISTRIBUTION"):
        signal = "WEAK"
    else:
        signal = "NONE"

    return {
        "score": round(score, 1),
        "signal": signal,
        "direction": direction,
        "strength": strength,
        "buy_ratio": buy_ratio,
        "num_prints": num_prints,
        "sustained_days": sustained + 1 if sustained > 0 else 0,
        "recent_direction": recent_dir,
        "recent_strength": recent_strength,
    }

def _process_ticker(item: dict, client=None) -> dict:
    """Process a single ticker: fetch flow and analyze signal.

    Returns a result dict or None on error.
    Designed to run inside a ThreadPoolExecutor worker.
    
    Args:
        item: Watchlist item with 'ticker' key
        client: Optional shared UWClient (passed via functools.partial)
    """
    ticker = item["ticker"]
    try:
        # Pass client to fetch_flow if provided
        # Use 3 days for scanning (faster) - full 5 days used in evaluate.py
        # Skip options flow - scanner only uses darkpool data for ranking
        flow = fetch_flow_module(ticker, lookback_days=3, _client=client, skip_options_flow=True)
        analysis = analyze_signal(flow)
        return {
            "ticker": ticker,
            "sector": item.get("sector", "Unknown"),
            **analysis
        }
    except UWRateLimitError:
        logger.warning("Rate limited on %s — skipping", ticker)
        print(f"  {ticker} - SKIP (rate limited)", file=sys.stderr)
        return None
    except Exception as exc:
        logger.warning("Error processing %s: %s", ticker, exc)
        print(f"  {ticker} - ERROR ({exc})", file=sys.stderr)
        return None


def scan(top_n: int = 20, min_score: float = 0, max_workers: int = 5):
    """Scan all watchlist tickers and rank by signal strength.

    Uses ThreadPoolExecutor to process tickers concurrently.

    Args:
        top_n: Number of top signals to return.
        min_score: Minimum score threshold.
        max_workers: Maximum concurrent workers (default 15).
    """
    if not WATCHLIST.exists():
        print(json.dumps({"error": "No watchlist.json found"}))
        return

    with open(WATCHLIST) as f:
        watchlist = json.load(f)

    open_positions = get_open_positions()
    tickers = watchlist.get("tickers", [])

    # Filter out open positions before dispatching to workers
    items_to_scan = [
        item for item in tickers
        if item["ticker"] not in open_positions
    ]
    skipped = len(tickers) - len(items_to_scan)
    if skipped:
        print(f"Skipping {skipped} tickers with open positions", file=sys.stderr)

    print(f"Scanning {len(items_to_scan)} tickers ({max_workers} workers)...", file=sys.stderr)

    results = []
    # Import here to avoid circular import
    from clients.uw_client import UWClient
    from functools import partial
    
    # Use shared UWClient for all workers (requests.Session is thread-safe)
    with UWClient() as client:
        process_with_client = partial(_process_ticker, client=client)
        
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(process_with_client, item): item for item in items_to_scan}
            done = 0
            for future in as_completed(futures):
                done += 1
                item = futures[future]
                ticker = item["ticker"]
                try:
                    result = future.result()
                except Exception as exc:
                    logger.warning("Unhandled error for %s: %s", ticker, exc)
                    print(f"  [{done}/{len(items_to_scan)}] {ticker} - ERROR ({exc})", file=sys.stderr)
                    continue
                if result is not None:
                    print(f"  [{done}/{len(items_to_scan)}] {ticker}... {result['signal']} ({result['score']})", file=sys.stderr)
                    results.append(result)

    # Sort by score descending
    results.sort(key=lambda x: x["score"], reverse=True)

    # Filter by min_score and take top_n
    filtered = [r for r in results if r["score"] >= min_score][:top_n]

    output = {
        "scan_time": datetime.now().isoformat(),
        "tickers_scanned": len(results),
        "signals_found": len([r for r in results if r["signal"] in ("STRONG", "MODERATE")]),
        "top_signals": filtered
    }

    print(json.dumps(output, indent=2))

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Scan watchlist for flow signals")
    p.add_argument("--top", type=int, default=20, help="Number of top signals to show")
    p.add_argument("--min-score", type=float, default=0, help="Minimum score threshold")
    p.add_argument("--workers", type=int, default=15, help="Max concurrent workers (default 15)")
    args = p.parse_args()

    scan(top_n=args.top, min_score=args.min_score, max_workers=args.workers)
