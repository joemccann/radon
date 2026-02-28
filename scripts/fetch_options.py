#!/usr/bin/env python3
"""Fetch options chain data. Customize for your data source."""
import argparse, json, sys

def fetch_options(ticker: str, dte_range: tuple = (20, 45)):
    """
    Replace this with your actual data source:
    - Yahoo Finance (free, delayed)
    - CBOE DataShop
    - Tradier API
    - TD Ameritrade / Schwab API
    - Interactive Brokers TWS API
    """
    # Placeholder — replace with real API calls
    print(json.dumps({
        "ticker": ticker,
        "spot_price": None,
        "iv_rank": None,
        "iv_percentile": None,
        "chains": [],
        "note": "REPLACE WITH REAL DATA SOURCE"
    }, indent=2))

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("ticker", help="Stock ticker")
    p.add_argument("--dte-min", type=int, default=20)
    p.add_argument("--dte-max", type=int, default=45)
    args = p.parse_args()
    fetch_options(args.ticker, (args.dte_min, args.dte_max))
