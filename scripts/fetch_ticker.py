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

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# Phase 4 wiring: env load before db.* imports.
_PROJECT_DIR = Path(__file__).resolve().parent.parent
try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]
    load_dotenv(_PROJECT_DIR / ".env")
    load_dotenv(_PROJECT_DIR / "web" / ".env")
except Exception:
    pass

from clients.uw_client import UWClient, UWNotFoundError, UWAPIError
from utils.market_calendar import get_last_n_trading_days, load_holidays, _is_trading_day

CACHE_FILE = Path(__file__).parent.parent / "data" / "ticker_cache.json"

# Keep for backward compatibility with existing tests
MARKET_HOLIDAYS_2026 = load_holidays(2026)


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
    """Add or update a ticker in the cache + dual-write to ticker_lookup_cache.

    Phase 4 of the Turso source-of-truth migration. The disk JSON is the
    canonical blob; the table lets per-key TTL'd lookups happen in SQL
    without parsing the whole cache.
    """
    cache = load_cache()
    cache["tickers"][ticker.upper()] = {
        "company_name": company_name,
        "sector": sector
    }
    save_cache(cache)

    _dual_write_ticker_to_db(ticker, company_name, sector)


def _dual_write_ticker_to_db(ticker: str, company_name: str, sector: str = None) -> None:
    """Best-effort upsert of a single ticker into ticker_lookup_cache.

    24h TTL — same as the existing in-process cache contract. Errors are
    logged + swallowed so a Turso outage doesn't block the disk write.
    """
    try:
        from db.writer import upsert_ticker_lookup_cache
    except ImportError:
        return
    try:
        from datetime import timedelta, timezone
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        result = json.dumps({"company_name": company_name, "sector": sector})
        upsert_ticker_lookup_cache(ticker.upper(), result, expires_at)
    except Exception as exc:  # noqa: BLE001
        print(f"  Warning: ticker_lookup_cache db dual-write failed: {exc}", file=sys.stderr)


def is_market_open(date: datetime) -> bool:
    """Check if the market is open on a given date (date-only, no time check).

    Backward-compatible wrapper used by existing tests.
    """
    return _is_trading_day(date)


def fetch_ticker_info(ticker: str) -> dict:
    """
    Validate ticker exists using Unusual Whales stock info API.
    Uses single /api/stock/{ticker}/info call instead of darkpool (faster).
    Checks local cache first for company name/sector.
    """
    ticker = ticker.upper().strip()
    now = datetime.now()

    # Check cache first
    cached = get_cached_ticker(ticker)

    result = {
        "ticker": ticker,
        "fetched_at": now.isoformat(),
        "verified": False,
        "validation_method": "stock_info",
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

    with UWClient() as client:
        # Use stock info endpoint - single fast call
        try:
            info_resp = client.get_stock_info(ticker)
            data = info_resp.get("data", {}) if isinstance(info_resp, dict) else {}
            if not isinstance(data, dict):
                data = {}

            if data:
                # Ticker is valid
                result["verified"] = True
                result["company_name"] = data.get("full_name") or result["company_name"]
                result["sector"] = data.get("sector") or result["sector"]
                result["industry"] = data.get("industry")
                result["market_cap"] = data.get("marketcap")
                result["avg_volume"] = data.get("avg30_volume")
                result["current_price"] = data.get("last") or data.get("price") or result["current_price"]
                result["options_available"] = bool(data.get("has_options", False))
        except UWNotFoundError:
            data = {}
        except UWAPIError as e:
            result["error"] = f"API error: {e}"
            return result

        # Dark-pool enrichment remains the compatibility fallback when stock-info
        # data is missing or incomplete.
        try:
            dp_resp = client.get_darkpool_flow(ticker)
            trades = dp_resp.get("data", []) if isinstance(dp_resp, dict) else []
        except UWNotFoundError:
            result["error"] = f"Ticker '{ticker}' not found"
            return result
        except UWAPIError as e:
            result["error"] = f"API error: {e}"
            return result

        active_trades = [
            trade for trade in trades
            if not trade.get("canceled")
        ]

        if active_trades:
            result["verified"] = True
            first_trade = active_trades[0]
            trade_price = first_trade.get("price")
            if trade_price is not None:
                try:
                    result["current_price"] = float(trade_price)
                except (TypeError, ValueError):
                    pass

            total_volume = sum(float(trade.get("size", 0) or 0) for trade in active_trades)
            trading_days = max(len(get_last_n_trading_days(3, now)), 1)
            avg_dp_volume = total_volume / trading_days
            if avg_dp_volume < 10_000:
                result["liquidity_warning"] = "LOW - Limited dark pool activity"
            else:
                result["liquidity_note"] = "HIGH - Active dark pool trading"

            try:
                flow_resp = client.get_flow_alerts(ticker=ticker, min_premium=50_000, limit=50)
                flow_data = flow_resp.get("data", []) if isinstance(flow_resp, dict) else []
                result["options_available"] = result["options_available"] or bool(flow_data)
            except UWAPIError:
                pass
        elif not data:
            result["error"] = "No dark pool activity"
            return result

        # Cache the company info only when it is real string data.
        if isinstance(result["company_name"], str) and result["company_name"] and not cached:
            cache_ticker(ticker, result["company_name"], result["sector"])

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Validate tickers via Unusual Whales dark pool data with local caching."
    )
    parser.add_argument("ticker", help="Ticker symbol to validate")
    parser.add_argument(
        "--add-cache",
        nargs="+",
        metavar=("NAME", "SECTOR"),
        help="Cache a ticker with company name and optional sector",
    )

    args = parser.parse_args()
    ticker = args.ticker

    if args.add_cache:
        company_name = args.add_cache[0]
        sector = args.add_cache[1] if len(args.add_cache) > 1 else None
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
