#!/usr/bin/env python3
"""
Ticker validation and metadata fetching.
Validates ticker exists and retrieves company info from live source.
"""

import json
import sys
from datetime import datetime

try:
    import yfinance as yf
except ImportError:
    print(json.dumps({"error": "yfinance not installed. Run: pip install yfinance"}, indent=2))
    sys.exit(1)


def fetch_ticker_info(ticker: str) -> dict:
    """
    Fetch ticker metadata using yfinance.
    Returns verified company info or error state.
    """
    ticker = ticker.upper().strip()
    
    result = {
        "ticker": ticker,
        "fetched_at": datetime.now().isoformat(),
        "verified": False,
        "company_name": None,
        "sector": None,
        "industry": None,
        "market_cap": None,
        "avg_volume": None,
        "current_price": None,
        "options_available": False,
        "error": None
    }
    
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        
        # Check if we got valid data (yfinance returns empty/minimal dict for invalid tickers)
        if not info or info.get("regularMarketPrice") is None:
            # Try to get history as fallback validation
            hist = stock.history(period="1d")
            if hist.empty:
                result["error"] = f"Ticker '{ticker}' not found or no data available"
                return result
        
        result["verified"] = True
        result["company_name"] = info.get("longName") or info.get("shortName") or ticker
        result["sector"] = info.get("sector")
        result["industry"] = info.get("industry")
        result["market_cap"] = info.get("marketCap")
        result["avg_volume"] = info.get("averageVolume") or info.get("averageDailyVolume10Day")
        result["current_price"] = info.get("regularMarketPrice") or info.get("currentPrice")
        result["currency"] = info.get("currency")
        result["exchange"] = info.get("exchange")
        result["quote_type"] = info.get("quoteType")
        
        # Check for options availability
        try:
            options_dates = stock.options
            result["options_available"] = len(options_dates) > 0
            result["options_expirations"] = list(options_dates)[:5] if options_dates else []
        except Exception:
            result["options_available"] = False
            result["options_expirations"] = []
        
        # Flag potential liquidity issues
        avg_vol = result["avg_volume"] or 0
        if avg_vol < 100000:
            result["liquidity_warning"] = "LOW - Avg volume <100k"
        elif avg_vol < 500000:
            result["liquidity_warning"] = "MODERATE - Avg volume 100k-500k"
        elif avg_vol < 1000000:
            result["liquidity_warning"] = None
        else:
            result["liquidity_warning"] = None
            result["liquidity_note"] = "HIGH - Avg volume >1M"
            
    except Exception as e:
        result["error"] = f"Error fetching data: {str(e)}"
    
    return result


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: fetch_ticker.py <TICKER>"}, indent=2))
        sys.exit(1)
    
    ticker = sys.argv[1]
    result = fetch_ticker_info(ticker)
    print(json.dumps(result, indent=2))
    
    # Exit with error code if not verified
    if not result["verified"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
