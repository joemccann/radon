#!/usr/bin/env python3
"""
Fetch analyst ratings and rating changes for tickers.

Data Source Priority (IB → UW only — Yahoo/yfinance removed 2026-06-01):
  1. Interactive Brokers (reqFundamentalData 'RESC') - Most reliable, requires subscription
  2. Unusual Whales (GET /api/screener/analysts) - Aggregated per-firm consensus, targets, history

When neither yields ratings, serve the last-known cached consensus, else a
clean "unavailable" state — never the old developer-facing yfinance error.

API Reference: docs/unusual_whales_api.md for UW endpoint details
Full Spec: docs/unusual_whales_api_spec.yaml

Usage:
    python3 scripts/fetch_analyst_ratings.py AAPL MSFT NVDA
    python3 scripts/fetch_analyst_ratings.py --watchlist
    python3 scripts/fetch_analyst_ratings.py --portfolio
    python3 scripts/fetch_analyst_ratings.py --all
    python3 scripts/fetch_analyst_ratings.py --changes-only  # Only show recent changes
    python3 scripts/fetch_analyst_ratings.py --source uw     # Force Unusual Whales
"""

import json
import sys
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple
import argparse

# Pacing between non-IB (UW) fetches to avoid bursting toward the UW daily limit
REQUEST_DELAY = 1.5  # seconds between requests
MAX_RETRIES = 2
RETRY_DELAY = 3  # seconds between retries
CACHE_TTL_HOURS = 4  # Use cached data if less than this old

from clients.ib_client import (
    IBClient,
    CLIENT_IDS,
    DEFAULT_HOST as IB_HOST,
    DEFAULT_GATEWAY_PORT as IB_PORT,
)
from clients.uw_client import UWClient

IB_CLIENT_ID = CLIENT_IDS["fetch_analyst_ratings"]

# Load .env so TURSO_DB_URL resolves for the dual-write to analyst_ratings.
_PROJECT_DIR = Path(__file__).resolve().parent.parent
try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]
    load_dotenv(_PROJECT_DIR / ".env")
    load_dotenv(_PROJECT_DIR / "web" / ".env")
except Exception:
    pass

# File paths
DATA_DIR = Path(__file__).parent.parent / "data"
WATCHLIST_FILE = DATA_DIR / "watchlist.json"
PORTFOLIO_FILE = DATA_DIR / "portfolio.json"
RATINGS_CACHE_FILE = DATA_DIR / "analyst_ratings_cache.json"


def load_json(path: Path) -> dict:
    """Load JSON file."""
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def save_json(path: Path, data: dict) -> None:
    """Save JSON file."""
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def get_watchlist_tickers() -> list:
    """Extract all tickers from watchlist."""
    data = load_json(WATCHLIST_FILE)
    tickers = set()
    
    # Main tickers
    for item in data.get("tickers", []):
        tickers.add(item.get("ticker", "").upper())
    
    # Subcategory tickers
    for subcat in data.get("subcategories", {}).values():
        for item in subcat.get("tickers", []):
            tickers.add(item.get("ticker", "").upper())
    
    return sorted([t for t in tickers if t])


def get_portfolio_tickers() -> list:
    """Extract all tickers from portfolio."""
    data = load_json(PORTFOLIO_FILE)
    tickers = set()
    
    for pos in data.get("positions", []):
        ticker = pos.get("ticker", "").upper()
        if ticker:
            tickers.add(ticker)
    
    return sorted(list(tickers))


def get_cached_rating(ticker: str, allow_stale: bool = False) -> Optional[dict]:
    """Get cached rating. Fresh within CACHE_TTL_HOURS.

    With allow_stale=True, return the last cached rating that has real ratings
    regardless of age — a graceful fallback when IB + UW are both unavailable
    (e.g. a UW daily rate-limit window) so the panel shows the last-known
    consensus instead of a blank/error.
    """
    cache = load_json(RATINGS_CACHE_FILE)
    cached = cache.get("ratings", {}).get(ticker.upper())
    if not cached:
        return None

    if allow_stale and cached.get("ratings") and not cached.get("error"):
        cached["from_cache"] = True
        cached["stale"] = True
        return cached

    if cached.get("fetched_at"):
        try:
            fetched = datetime.fromisoformat(cached["fetched_at"])
            if datetime.now() - fetched < timedelta(hours=CACHE_TTL_HOURS):
                cached["from_cache"] = True
                return cached
        except (ValueError, TypeError):
            pass
    return None


# =============================================================================
# IB Data Source (Primary)
# =============================================================================

def connect_ib(port: int = IB_PORT) -> Tuple[any, bool]:
    """Try to connect to IB. Returns (client_instance, success)."""
    try:
        client = IBClient()
        client.connect(host=IB_HOST, port=port, client_id=IB_CLIENT_ID, timeout=5)
        return client, True
    except ImportError:
        return None, False
    except Exception as e:
        return None, False


def fetch_from_ib(client: IBClient, ticker: str) -> Optional[dict]:
    """
    Fetch analyst ratings from Interactive Brokers using RESC fundamental data.
    Returns parsed ratings dict or None if unavailable.

    Note: Requires IB fundamentals data subscription. Error 10358 means
    "Fundamentals data is not allowed" - subscription not active.
    """
    try:
        from ib_insync import Stock

        contract = Stock(ticker, 'SMART', 'USD')
        client.qualify_contracts(contract)

        # Request analyst estimates (RESC = Reuters Estimates)
        # This may fail with error 10358 if fundamentals subscription not active
        xml_data = client.ib.reqFundamentalData(contract, 'RESC')
        
        if not xml_data:
            return None
        
        # Parse XML response
        root = ET.fromstring(xml_data)
        
        result = {
            "ticker": ticker.upper(),
            "fetched_at": datetime.now().isoformat(),
            "source": "IB",
            "ratings": {},
            "recommendation": None,
            "target_price": {},
            "recent_changes": [],
            "error": None
        }
        
        # Parse consensus recommendations
        # IB RESC format contains <ConsRecommendation> elements
        consensus = root.find(".//ConsRecommendation")
        if consensus is not None:
            result["recommendation_mean"] = float(consensus.get("value", 0))
            # IB uses 1-5 scale: 1=Strong Buy, 5=Strong Sell
            mean = result["recommendation_mean"]
            if mean <= 1.5:
                result["recommendation"] = "strong_buy"
            elif mean <= 2.5:
                result["recommendation"] = "buy"
            elif mean <= 3.5:
                result["recommendation"] = "hold"
            elif mean <= 4.5:
                result["recommendation"] = "sell"
            else:
                result["recommendation"] = "strong_sell"
        
        # Parse individual analyst ratings
        ratings_elem = root.find(".//Ratings")
        if ratings_elem is not None:
            buy_count = 0
            hold_count = 0
            sell_count = 0
            
            for rating in ratings_elem.findall("Rating"):
                rating_type = rating.get("type", "").lower()
                count = int(rating.get("count", 0))
                
                if "buy" in rating_type or "outperform" in rating_type:
                    buy_count += count
                elif "hold" in rating_type or "neutral" in rating_type:
                    hold_count += count
                elif "sell" in rating_type or "underperform" in rating_type:
                    sell_count += count
            
            total = buy_count + hold_count + sell_count
            if total > 0:
                result["ratings"] = {
                    "strong_buy": 0,  # IB doesn't always split these
                    "buy": buy_count,
                    "hold": hold_count,
                    "sell": sell_count,
                    "strong_sell": 0,
                    "total": total,
                    "buy_pct": round(buy_count / total * 100, 1),
                    "sell_pct": round(sell_count / total * 100, 1)
                }
        
        # Parse target prices
        target_elem = root.find(".//TargetPrice")
        if target_elem is not None:
            result["target_price"] = {
                "mean": float(target_elem.get("mean", 0)) or None,
                "high": float(target_elem.get("high", 0)) or None,
                "low": float(target_elem.get("low", 0)) or None,
            }
        
        # Parse recent rating changes
        changes_elem = root.find(".//RatingChanges")
        if changes_elem is not None:
            for change in changes_elem.findall("Change"):
                result["recent_changes"].append({
                    "date": change.get("date", ""),
                    "firm": change.get("broker", "Unknown"),
                    "from_grade": change.get("fromRating", ""),
                    "to_grade": change.get("toRating", ""),
                    "action": change.get("action", "")
                })
            if result["recent_changes"]:
                result["has_recent_changes"] = True
        
        # Calculate target upside
        current_price = None
        price_elem = root.find(".//Price")
        if price_elem is not None:
            current_price = float(price_elem.get("value", 0))
        
        if current_price and result["target_price"].get("mean"):
            target = result["target_price"]["mean"]
            result["target_upside_pct"] = round(((target - current_price) / current_price) * 100, 1)
            result["target_price"]["current"] = current_price
        
        return result
        
    except Exception as e:
        return None


# =============================================================================
# Unusual Whales Data Source (Priority 2)
# =============================================================================

def fetch_from_uw(ticker: str) -> Optional[dict]:
    """
    Fetch analyst ratings from Unusual Whales screener endpoint.
    Aggregates individual analyst actions into a consensus view.
    
    Endpoint: GET /api/screener/analysts?ticker={TICKER}&limit=100
    Returns individual analyst actions (maintained, upgraded, etc.)
    which we aggregate into buy/hold/sell counts.
    """
    try:
        with UWClient() as uw:
            data = uw._get("screener/analysts", params={"ticker": ticker.upper(), "limit": 100})
            entries = data.get("data", [])
    except Exception as e:
        # Surface the reason (e.g. UW daily rate-limit) so the caller can show a
        # helpful unavailable message instead of swallowing it to a bare None.
        return {"ticker": ticker.upper(), "source": "uw", "ratings": None, "error": str(e)}

    if not entries:
        return None

    # Filter to this ticker only (safety check)
    entries = [e for e in entries if e.get("ticker", "").upper() == ticker.upper()]
    if not entries:
        return None

    result = {
        "ticker": ticker.upper(),
        "fetched_at": datetime.now().isoformat(),
        "source": "uw",
        "ratings": None,
        "recommendation": None,
        "target_price": None,
        "recent_changes": [],
        "upgrade_downgrade_history": [],
        "error": None,
        "from_cache": False,
    }

    # --- Aggregate most recent rating per firm (deduplicate) ---
    latest_by_firm = {}
    for e in entries:
        firm = e.get("firm", "Unknown")
        ts = e.get("timestamp", "")
        if firm not in latest_by_firm or ts > latest_by_firm[firm].get("timestamp", ""):
            latest_by_firm[firm] = e

    # --- Count buy / hold / sell from latest per-firm recommendation ---
    buy_count = 0
    hold_count = 0
    sell_count = 0
    targets = []

    for firm, e in latest_by_firm.items():
        rec = (e.get("recommendation") or "").lower()
        if rec in ("buy", "strong buy", "overweight", "outperform", "positive"):
            buy_count += 1
        elif rec in ("hold", "neutral", "equal-weight", "market perform", "peer perform", "sector perform", "in-line"):
            hold_count += 1
        elif rec in ("sell", "strong sell", "underweight", "underperform", "negative", "reduce"):
            sell_count += 1
        else:
            # Unknown recommendation — try to infer
            if "buy" in rec:
                buy_count += 1
            elif "sell" in rec or "under" in rec:
                sell_count += 1
            elif "hold" in rec or "neutral" in rec or "perform" in rec or "weight" in rec:
                hold_count += 1

        # Collect target prices
        try:
            t = float(e.get("target", 0))
            if t > 0:
                targets.append(t)
        except (ValueError, TypeError):
            pass

    total = buy_count + hold_count + sell_count

    if total > 0:
        result["ratings"] = {
            "strong_buy": 0,
            "buy": buy_count,
            "hold": hold_count,
            "sell": sell_count,
            "strong_sell": 0,
            "total": total,
            "buy_pct": round(buy_count / total * 100, 1),
            "sell_pct": round(sell_count / total * 100, 1),
        }

        # Derive recommendation string
        buy_pct = result["ratings"]["buy_pct"]
        if buy_pct >= 70:
            result["recommendation"] = "buy"
        elif buy_pct >= 50:
            result["recommendation"] = "hold"  # leaning buy
        else:
            result["recommendation"] = "hold"

    # --- Target prices ---
    if targets:
        # Try to get current price from entries or estimate
        result["target_price"] = {
            "mean": round(sum(targets) / len(targets), 2),
            "high": round(max(targets), 2),
            "low": round(min(targets), 2),
            "median": round(sorted(targets)[len(targets) // 2], 2),
            "count": len(targets),
        }

    # --- Analyst count ---
    result["analyst_count"] = total

    # --- Build upgrade/downgrade history (most recent 10) ---
    for e in entries[:10]:
        action = (e.get("action") or "").lower()
        result["upgrade_downgrade_history"].append({
            "date": (e.get("timestamp") or "")[:10],
            "firm": e.get("firm", "Unknown"),
            "to_grade": e.get("recommendation", ""),
            "from_grade": "",
            "action": action,
        })
        if action in ("upgraded", "downgraded", "initiated"):
            result["has_recent_changes"] = True

    # --- Detect recent changes ---
    recent_actions = [e.get("action", "").lower() for e in entries[:10]]
    upgrades = sum(1 for a in recent_actions if a in ("upgraded",))
    downgrades = sum(1 for a in recent_actions if a in ("downgraded",))
    if upgrades > downgrades:
        result["recent_changes"] = [{"category": "upgrades", "change": upgrades - downgrades}]
        result["has_recent_changes"] = True
    elif downgrades > upgrades:
        result["recent_changes"] = [{"category": "downgrades", "change": downgrades - upgrades}]
        result["has_recent_changes"] = True

    return result


# =============================================================================
# Main Fetch Function with Priority
# =============================================================================

def fetch_analyst_ratings(ticker: str, use_cache: bool = True, force_source: str = None, client=None) -> dict:
    """
    Fetch analyst ratings with data source priority (IB → UW only — no Yahoo):
    1. Interactive Brokers (if connected, requires Reuters subscription)
    2. Unusual Whales (/api/screener/analysts)

    When neither source yields ratings (IB unsubscribed AND UW rate-limited /
    down), fall back to the last-known cached consensus if present, else return
    a clean unavailable state — never a developer-facing error.
    """
    ticker = ticker.upper()

    # Check cache first (fresh within TTL)
    if use_cache and not force_source:
        cached = get_cached_rating(ticker)
        if cached and not cached.get("error"):
            return cached

    last_error = None

    # Priority 1: IB (unless forced to UW)
    if force_source != "uw" and client is not None:
        result = fetch_from_ib(client, ticker)
        if result and not result.get("error"):
            return result
        if result and result.get("error"):
            last_error = result.get("error")

    # Priority 2: Unusual Whales
    result = fetch_from_uw(ticker)
    if result and not result.get("error") and result.get("ratings"):
        return result
    if result and result.get("error"):
        last_error = result.get("error")

    # No Yahoo/yfinance fallback (per data-source policy: IB → UW only). Serve the
    # last-known cached consensus if we have one, else a clean unavailable state.
    if use_cache:
        stale = get_cached_rating(ticker, allow_stale=True)
        if stale:
            return stale

    return {
        "ticker": ticker,
        "fetched_at": datetime.now().isoformat(),
        "source": "none",
        "ratings": None,
        "recommendation": None,
        "target_price": None,
        "recent_changes": [],
        "error": _unavailable_message(last_error),
    }


def _unavailable_message(last_error: Optional[str]) -> str:
    """Operator-/user-readable reason ratings are unavailable from IB + UW."""
    low = (last_error or "").lower()
    if any(token in low for token in ("rate limit", "request limit", "daily request", "429")):
        return "Analyst ratings unavailable: Unusual Whales daily request limit reached (resets after the close)."
    return "Analyst ratings unavailable from IB / Unusual Whales right now."


# =============================================================================
# Signal Calculation
# =============================================================================

def calculate_rating_signal(ratings_data: dict) -> dict:
    """Calculate a trading signal based on analyst ratings."""
    signal = {
        "direction": "NEUTRAL",
        "strength": 0,
        "confidence": "LOW",
        "changes_signal": None,
        "notes": []
    }
    
    if ratings_data.get("error") or not ratings_data.get("ratings"):
        signal["notes"].append("No ratings data available")
        return signal
    
    ratings = ratings_data["ratings"]
    total = ratings.get("total", 0)
    
    if total == 0:
        signal["notes"].append("No analyst coverage")
        return signal
    
    buy_pct = ratings.get("buy_pct", 0)
    sell_pct = ratings.get("sell_pct", 0)
    
    # Direction based on buy/sell ratio
    if buy_pct >= 70:
        signal["direction"] = "BULLISH"
        signal["strength"] = min(100, buy_pct)
    elif buy_pct >= 50:
        signal["direction"] = "LEAN_BULLISH"
        signal["strength"] = buy_pct
    elif sell_pct >= 50:
        signal["direction"] = "LEAN_BEARISH"
        signal["strength"] = sell_pct
    elif sell_pct >= 70:
        signal["direction"] = "BEARISH"
        signal["strength"] = min(100, sell_pct)
    else:
        signal["direction"] = "NEUTRAL"
        signal["strength"] = 50
    
    # Confidence based on analyst count
    if total >= 20:
        signal["confidence"] = "HIGH"
    elif total >= 10:
        signal["confidence"] = "MEDIUM"
    else:
        signal["confidence"] = "LOW"
    
    # Analyze recent changes
    if ratings_data.get("has_recent_changes"):
        changes = ratings_data.get("recent_changes", [])
        upgrades = sum(c["change"] for c in changes if c.get("category") in ["strongBuy", "buy"] and c.get("change", 0) > 0)
        downgrades = sum(abs(c.get("change", 0)) for c in changes if c.get("category") in ["sell", "strongSell"] and c.get("change", 0) > 0)
        
        if upgrades > downgrades:
            signal["changes_signal"] = "UPGRADING"
            signal["notes"].append(f"Net upgrades: +{upgrades - downgrades}")
        elif downgrades > upgrades:
            signal["changes_signal"] = "DOWNGRADING"
            signal["notes"].append(f"Net downgrades: -{downgrades - upgrades}")
    
    # Target price analysis
    upside = ratings_data.get("target_upside_pct")
    if upside:
        if upside > 20:
            signal["notes"].append(f"Target upside: {upside}% (Bullish)")
        elif upside < -10:
            signal["notes"].append(f"Target downside: {upside}% (Bearish)")
        else:
            signal["notes"].append(f"Target: {upside}%")
    
    return signal


# =============================================================================
# Output Formatting
# =============================================================================

def format_ratings_table(results: list, changes_only: bool = False) -> str:
    """Format results as a readable table."""
    lines = []
    
    if changes_only:
        results = [r for r in results if r.get("has_recent_changes") or r.get("upgrade_downgrade_history")]
    
    if not results:
        return "No analyst rating changes found.\n"
    
    # Header
    lines.append("\n" + "="*95)
    lines.append("ANALYST RATINGS REPORT")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("="*95)
    
    # Summary table
    lines.append(f"\n{'Ticker':<8} {'Src':<5} {'Rec':<12} {'Buy%':>6} {'Hold%':>6} {'Sell%':>6} {'#':>4} {'Target':>8} {'Upside':>8} {'Signal':<12}")
    lines.append("-"*95)
    
    for r in results:
        ticker = r.get("ticker", "???")
        source = r.get("source", "?")[:4]
        
        if r.get("error"):
            lines.append(f"{ticker:<8} {source:<5} ERROR: {r['error'][:65]}")
            continue
        
        ratings = r.get("ratings", {})
        rec = r.get("recommendation", "N/A")
        if rec:
            rec = rec[:11]
        
        buy_pct = ratings.get("buy_pct", 0)
        hold_pct = round(ratings.get("hold", 0) / ratings.get("total", 1) * 100, 1) if ratings.get("total") else 0
        sell_pct = ratings.get("sell_pct", 0)
        count = ratings.get("total", 0)
        
        target = r.get("target_price", {})
        if isinstance(target, dict):
            target = target.get("mean")
        target_str = f"${target:.0f}" if target else "N/A"
        
        upside = r.get("target_upside_pct")
        upside_str = f"{upside:+.1f}%" if upside else "N/A"
        
        signal = calculate_rating_signal(r)
        signal_str = signal["direction"]
        if signal.get("changes_signal"):
            signal_str += f" ({signal['changes_signal'][:3]})"
        
        cache_indicator = "©" if r.get("from_cache") else ""
        
        lines.append(f"{ticker:<8} {source:<4}{cache_indicator} {rec:<12} {buy_pct:>5.1f}% {hold_pct:>5.1f}% {sell_pct:>5.1f}% {count:>4} {target_str:>8} {upside_str:>8} {signal_str:<12}")
    
    # Recent changes section
    has_changes = any(r.get("has_recent_changes") or r.get("upgrade_downgrade_history") for r in results)
    if has_changes:
        lines.append("\n" + "-"*95)
        lines.append("RECENT RATING CHANGES")
        lines.append("-"*95)
        
        for r in results:
            ticker = r.get("ticker")
            
            if r.get("has_recent_changes") and r.get("recent_changes"):
                changes = r.get("recent_changes", [])
                # Check if these are category changes or firm changes
                if changes and "category" in changes[0]:
                    lines.append(f"\n{ticker} - Rating Distribution Changes:")
                    for change in changes:
                        direction = "↑" if change.get("change", 0) > 0 else "↓"
                        if "previous" in change and "current" in change:
                            lines.append(f"  {change['category']}: {change['previous']} → {change['current']} ({direction}{abs(change.get('change', 0))})")
                        else:
                            lines.append(f"  {change['category']}: {direction}{abs(change.get('change', 0))}")
                else:
                    lines.append(f"\n{ticker} - Analyst Actions:")
                    for h in changes[-5:]:
                        action = h.get("action", "").upper()
                        arrow = "↑" if action in ["UP", "UPGRADE", "INITIATED"] else "↓" if action in ["DOWN", "DOWNGRADE"] else "→"
                        lines.append(f"  {h.get('date', 'N/A')} {h.get('firm', 'Unknown')[:20]:<20} {arrow} {h.get('from_grade', 'N/A')} → {h.get('to_grade', 'N/A')}")
            
            history = r.get("upgrade_downgrade_history", [])
            if history and not r.get("recent_changes"):
                lines.append(f"\n{ticker} - Recent Analyst Actions:")
                for h in history[-5:]:
                    action = h.get("action", "").upper()
                    arrow = "↑" if action in ["UP", "UPGRADE", "INITIATED"] else "↓" if action in ["DOWN", "DOWNGRADE"] else "→"
                    lines.append(f"  {h['date']} {h['firm'][:20]:<20} {arrow} {h.get('from_grade', 'N/A')} → {h.get('to_grade', 'N/A')}")
    
    lines.append("\n" + "="*95)
    lines.append("Sources: IB = Interactive Brokers, uw = Unusual Whales, © = cached")
    
    return "\n".join(lines)


def update_watchlist_with_ratings(tickers: list, ratings_data: dict) -> None:
    """Update watchlist.json with analyst ratings data."""
    watchlist = load_json(WATCHLIST_FILE)
    
    for item in watchlist.get("tickers", []):
        ticker = item.get("ticker", "").upper()
        if ticker in ratings_data:
            r = ratings_data[ticker]
            signal = calculate_rating_signal(r)
            item["analyst_ratings"] = {
                "source": r.get("source", "unknown"),
                "recommendation": r.get("recommendation"),
                "buy_pct": r.get("ratings", {}).get("buy_pct"),
                "analyst_count": r.get("ratings", {}).get("total", 0),
                "target_upside_pct": r.get("target_upside_pct"),
                "signal": signal["direction"],
                "changes_signal": signal.get("changes_signal"),
                "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M")
            }
    
    save_json(WATCHLIST_FILE, watchlist)


def main():
    parser = argparse.ArgumentParser(description="Fetch analyst ratings for tickers")
    parser.add_argument("tickers", nargs="*", help="Ticker symbols to check")
    parser.add_argument("--watchlist", action="store_true", help="Check all watchlist tickers")
    parser.add_argument("--portfolio", action="store_true", help="Check all portfolio tickers")
    parser.add_argument("--all", action="store_true", help="Check both watchlist and portfolio")
    parser.add_argument("--changes-only", action="store_true", help="Only show tickers with recent changes")
    parser.add_argument("--update-watchlist", action="store_true", help="Update watchlist.json with ratings")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    parser.add_argument("--no-cache", action="store_true", help="Bypass cache and fetch fresh data")
    parser.add_argument("--source", choices=["ib", "uw"], help="Force specific data source (IB or UW)")
    parser.add_argument("--port", type=int, default=IB_PORT, help=f"IB Gateway/TWS port (default: {IB_PORT})")
    
    args = parser.parse_args()
    
    # Collect tickers
    tickers = set()
    
    if args.tickers:
        tickers.update(t.upper() for t in args.tickers)
    
    if args.watchlist or args.all:
        tickers.update(get_watchlist_tickers())
    
    if args.portfolio or args.all:
        tickers.update(get_portfolio_tickers())
    
    if not tickers:
        print("Error: No tickers specified. Use --watchlist, --portfolio, --all, or provide ticker symbols.", file=sys.stderr)
        sys.exit(1)
    
    # Try to connect to IB
    client = None
    ib_connected = False
    ib_port = args.port

    if args.source != "uw":
        print(f"Attempting IB connection on port {ib_port}...", file=sys.stderr, end=" ")
        client, ib_connected = connect_ib(port=ib_port)
        if ib_connected:
            print("Connected ✓", file=sys.stderr)
        else:
            print("Not available, falling back to UW", file=sys.stderr)
    else:
        print("Using Unusual Whales (forced)", file=sys.stderr)

    # Fetch ratings
    results = []
    ratings_dict = {}

    print(f"Fetching analyst ratings for {len(tickers)} tickers...", file=sys.stderr)

    for i, ticker in enumerate(sorted(tickers)):
        print(f"  {ticker}...", file=sys.stderr, end=" ", flush=True)

        data = fetch_analyst_ratings(
            ticker,
            use_cache=not args.no_cache,
            force_source=args.source,
            client=client,
        )
        results.append(data)
        ratings_dict[ticker] = data

        source = data.get("source", "?")
        cached = " (cached)" if data.get("from_cache") else ""
        print(f"{source}{cached}", file=sys.stderr)

        # Gentle pacing between UW calls when not on IB (avoid bursts toward the
        # UW daily request limit).
        if not ib_connected and i < len(tickers) - 1:
            time.sleep(REQUEST_DELAY)

    # Disconnect IB
    if client and ib_connected:
        try:
            client.disconnect()
        except Exception:
            pass

    # Output
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(format_ratings_table(results, changes_only=args.changes_only))
    
    # Update watchlist if requested
    if args.update_watchlist:
        update_watchlist_with_ratings(list(tickers), ratings_dict)
        print("\nWatchlist updated with analyst ratings.", file=sys.stderr)
    
    # Save to cache
    cache = load_json(RATINGS_CACHE_FILE)
    if "ratings" not in cache:
        cache["ratings"] = {}
    cache["last_updated"] = datetime.now().isoformat()
    for ticker, data in ratings_dict.items():
        if not data.get("error"):
            cache["ratings"][ticker] = data
    save_json(RATINGS_CACHE_FILE, cache)

    # Phase 3 dual-write — best-effort. service_cycle (DUR-14) heartbeats
    # ok on clean exit and error (+ retry embargo) on failure, re-raising
    # into the non-fatal except below.
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from db.service_cycle import service_cycle
        from db.writer import upsert_analyst_ratings
        fetched_at = cache["last_updated"]
        with service_cycle("analyst-ratings", market_hours_class="on-demand") as cycle:
            cycle.finished_at = fetched_at
            for ticker, data in ratings_dict.items():
                if not data.get("error"):
                    upsert_analyst_ratings(ticker, fetched_at, data)
    except Exception as exc:  # noqa: BLE001
        print(f"  Warning: analyst_ratings db dual-write failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
