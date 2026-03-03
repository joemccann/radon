#!/usr/bin/env python3
"""
Ticker validation using Unusual Whales API with local caching.
Validates ticker exists by checking for dark pool activity.
Caches company names locally to reduce API calls.

Requires UW_TOKEN environment variable.

API Reference: docs/unusual_whales_api.md
Full Spec: docs/unusual_whales_api_spec.yaml

Key endpoints used:
  - GET /api/darkpool/{ticker} - Validates ticker and returns activity
  - GET /api/stock/{ticker}/info - Company info (if needed)
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

BASE_URL = "https://api.unusualwhales.com/api"
CACHE_FILE = Path(__file__).parent.parent / "data" / "ticker_cache.json"

# US Market holidays for 2026 (NYSE/NASDAQ)
MARKET_HOLIDAYS_2026 = {
    "2026-01-01",  # New Year's Day
    "2026-01-19",  # MLK Day
    "2026-02-16",  # Presidents Day
    "2026-04-03",  # Good Friday
    "2026-05-25",  # Memorial Day
    "2026-07-03",  # Independence Day (observed)
    "2026-09-07",  # Labor Day
    "2026-11-26",  # Thanksgiving
    "2026-12-25",  # Christmas
}


def load_cache() -> dict:
    """Load ticker cache from disk."""
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"last_updated": None, "tickers": {}}


def save_cache(cache: dict) -> None:
    """Save ticker cache to disk."""
    cache["last_updated"] = datetime.now().strftime("%Y-%m-%d")
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2)
    except IOError as e:
        print(f"Warning: Could not save cache: {e}", file=sys.stderr)


def get_cached_ticker(ticker: str):
    """Get ticker info from cache if available."""
    cache = load_cache()
    return cache.get("tickers", {}).get(ticker.upper())


def cache_ticker(ticker: str, company_name: str, sector: str = None) -> None:
    """Add or update a ticker in the cache."""
    cache = load_cache()
    cache["tickers"][ticker.upper()] = {
        "company_name": company_name,
        "sector": sector
    }
    save_cache(cache)


def is_market_open(date: datetime) -> bool:
    """Check if the market is open on a given date."""
    if date.weekday() >= 5:  # Saturday = 5, Sunday = 6
        return False
    date_str = date.strftime("%Y-%m-%d")
    if date_str in MARKET_HOLIDAYS_2026:
        return False
    return True


def get_last_n_trading_days(n: int, from_date: datetime = None) -> list:
    """Get the last N trading days (market open days)."""
    if from_date is None:
        from_date = datetime.now()
    
    trading_days = []
    current = from_date
    
    # Start from yesterday if today's market hasn't closed or it's not a trading day
    if not is_market_open(current) or current.hour < 16:
        current = current - timedelta(days=1)
    
    while len(trading_days) < n:
        if is_market_open(current):
            trading_days.append(current.strftime("%Y-%m-%d"))
        current = current - timedelta(days=1)
        
        # Safety limit
        if len(trading_days) == 0 and (from_date - current).days > 14:
            break
    
    return trading_days


def _get_token() -> str:
    token = os.environ.get("UW_TOKEN")
    if not token:
        print(json.dumps({"error": "UW_TOKEN environment variable not set"}), file=sys.stderr)
        sys.exit(1)
    return token


def _api_get(path: str, params: dict = None) -> dict:
    """Make authenticated GET request to Unusual Whales API."""
    url = f"{BASE_URL}{path}"
    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
        if query:
            url = f"{url}?{query}"
    
    req = Request(url, headers={
        "Accept": "application/json",
        "Authorization": f"Bearer {_get_token()}",
        "User-Agent": "convex-scavenger/1.0",
    })
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        body = e.read().decode() if e.fp else ""
        return {"error": f"HTTP {e.code}: {e.reason}", "detail": body}
    except URLError as e:
        return {"error": f"Connection failed: {e.reason}"}


def fetch_ticker_info(ticker: str) -> dict:
    """
    Validate ticker exists using Unusual Whales dark pool data.
    Checks local cache first for company name/sector.
    If we get DP prints, the ticker is valid and actively traded.
    """
    ticker = ticker.upper().strip()
    now = datetime.now()
    
    # Check cache first
    cached = get_cached_ticker(ticker)
    
    result = {
        "ticker": ticker,
        "fetched_at": now.isoformat(),
        "verified": False,
        "validation_method": "dark_pool_activity",
        "from_cache": cached is not None,
        "company_name": cached.get("company_name") if cached else None,
        "sector": cached.get("sector") if cached else None,
        "industry": None,
        "market_cap": None,
        "avg_volume": None,
        "current_price": None,
        "options_available": False,
        "error": None
    }
    
    # Get last 3 trading days
    trading_days = get_last_n_trading_days(3, now)
    result["trading_days_checked"] = trading_days
    
    if not trading_days:
        result["error"] = "Could not determine recent trading days"
        return result
    
    # Check dark pool data for those trading days
    total_prints = 0
    total_volume = 0
    total_premium = 0.0
    latest_price = None
    
    for date in trading_days:
        resp = _api_get(f"/darkpool/{ticker}", {"date": date})
        
        if "error" in resp:
            if "404" in resp["error"]:
                result["error"] = f"Ticker '{ticker}' not found"
                return result
            # Other errors - continue trying
            continue
        
        data = resp.get("data", [])
        if isinstance(data, list):
            total_prints += len(data)
            for t in data:
                if not t.get("canceled"):
                    total_volume += int(t.get("size", 0))
                    total_premium += float(t.get("premium", 0))
                    if latest_price is None:
                        latest_price = float(t.get("price", 0))
    
    if total_prints == 0:
        result["error"] = f"No dark pool activity found for '{ticker}' (may be invalid or illiquid)"
        return result
    
    # Ticker is valid - we have DP data
    result["verified"] = True
    result["current_price"] = latest_price
    result["dp_prints_3d"] = total_prints
    result["dp_volume_3d"] = total_volume
    result["dp_premium_3d"] = round(total_premium, 2)
    
    # Check options availability via flow alerts
    options_resp = _api_get("/option-trades/flow-alerts", {
        "ticker_symbol": ticker,
        "limit": "10"
    })
    if "error" not in options_resp:
        alerts = options_resp.get("data", [])
        result["options_available"] = len(alerts) > 0 if isinstance(alerts, list) else False
        if result["options_available"] and alerts:
            result["recent_options_activity"] = True
    
    # Liquidity assessment based on DP volume
    num_days = len(trading_days)
    avg_daily_volume = total_volume / num_days if num_days > 0 else 0
    if avg_daily_volume < 10000:
        result["liquidity_warning"] = "LOW - Avg DP volume <10k/day"
    elif avg_daily_volume < 100000:
        result["liquidity_warning"] = "MODERATE"
    else:
        result["liquidity_warning"] = None
        result["liquidity_note"] = "HIGH - Active dark pool trading"
    
    return result


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: fetch_ticker.py <TICKER> [--add-cache NAME SECTOR]"}, indent=2))
        sys.exit(1)
    
    ticker = sys.argv[1]
    
    # Handle cache management commands
    if len(sys.argv) >= 4 and sys.argv[2] == "--add-cache":
        company_name = sys.argv[3]
        sector = sys.argv[4] if len(sys.argv) > 4 else None
        cache_ticker(ticker, company_name, sector)
        print(json.dumps({"status": "cached", "ticker": ticker, "company_name": company_name, "sector": sector}, indent=2))
        sys.exit(0)
    
    result = fetch_ticker_info(ticker)
    print(json.dumps(result, indent=2))
    
    # Exit with error code if not verified
    if not result["verified"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
