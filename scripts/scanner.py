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
import subprocess
import sys
from datetime import datetime
from pathlib import Path

WATCHLIST = Path("data/watchlist.json")
PORTFOLIO = Path("data/portfolio.json")

def get_open_positions():
    """Get list of tickers with open positions."""
    if not PORTFOLIO.exists():
        return set()
    with open(PORTFOLIO) as f:
        portfolio = json.load(f)
    return {p["ticker"] for p in portfolio.get("positions", [])}

def fetch_flow(ticker: str, days: int = 5) -> dict:
    """Fetch flow data for a single ticker."""
    try:
        out = subprocess.check_output(
            ["python3", "scripts/fetch_flow.py", ticker, "--days", str(days)],
            text=True, timeout=60, stderr=subprocess.DEVNULL
        )
        return json.loads(out)
    except subprocess.TimeoutExpired:
        return {"error": "timeout"}
    except Exception as e:
        return {"error": str(e)}

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

def scan(top_n: int = 20, min_score: float = 0):
    """Scan all watchlist tickers and rank by signal strength."""
    if not WATCHLIST.exists():
        print(json.dumps({"error": "No watchlist.json found"}))
        return
    
    with open(WATCHLIST) as f:
        watchlist = json.load(f)
    
    open_positions = get_open_positions()
    tickers = watchlist.get("tickers", [])
    
    print(f"Scanning {len(tickers)} tickers...", file=sys.stderr)
    
    results = []
    for i, item in enumerate(tickers, 1):
        ticker = item["ticker"]
        
        # Skip open positions
        if ticker in open_positions:
            print(f"  [{i}/{len(tickers)}] {ticker} - SKIP (open position)", file=sys.stderr)
            continue
        
        print(f"  [{i}/{len(tickers)}] {ticker}...", file=sys.stderr, end=" ")
        
        flow = fetch_flow(ticker)
        analysis = analyze_signal(flow)
        
        print(f"{analysis['signal']} ({analysis['score']})", file=sys.stderr)
        
        results.append({
            "ticker": ticker,
            "sector": item.get("sector", "Unknown"),
            **analysis
        })
    
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
    args = p.parse_args()
    
    scan(top_n=args.top, min_score=args.min_score)
