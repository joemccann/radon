#!/usr/bin/env python3
"""Scan watchlist for flow signals. Runs daily."""
import json, subprocess, sys
from pathlib import Path

WATCHLIST = Path("data/watchlist.json")

def scan():
    if not WATCHLIST.exists():
        print(json.dumps({"error": "No watchlist.json found. Create one first."}))
        return

    with open(WATCHLIST) as f:
        watchlist = json.load(f)

    results = []
    for item in watchlist.get("tickers", []):
        ticker = item["ticker"]
        # Call fetch_flow for each ticker
        try:
            out = subprocess.check_output(
                ["python3", "scripts/fetch_flow.py", ticker],
                text=True, timeout=30
            )
            flow = json.loads(out)
            results.append({**item, "flow_data": flow})
        except Exception as e:
            results.append({**item, "error": str(e)})

    print(json.dumps({"scan_results": results}, indent=2))

if __name__ == "__main__":
    scan()
