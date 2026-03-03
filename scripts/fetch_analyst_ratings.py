#!/usr/bin/env python3
"""
Fetch analyst ratings and rating changes for tickers.

Data Source Priority:
  1. Interactive Brokers (reqFundamentalData 'RESC') - Most reliable, requires subscription
  2. Unusual Whales (GET /api/screener/analysts) - Has ratings data (see docs/unusual_whales_api.md)
  3. Yahoo Finance - Fallback (rate limited)
  
API Reference: docs/unusual_whales_api.md for UW endpoint details
Full Spec: docs/unusual_whales_api_spec.yaml

Usage:
    python3 scripts/fetch_analyst_ratings.py AAPL MSFT NVDA
    python3 scripts/fetch_analyst_ratings.py --watchlist
    python3 scripts/fetch_analyst_ratings.py --portfolio
    python3 scripts/fetch_analyst_ratings.py --all
    python3 scripts/fetch_analyst_ratings.py --changes-only  # Only show recent changes
    python3 scripts/fetch_analyst_ratings.py --source yahoo  # Force Yahoo Finance
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

# Rate limiting for Yahoo Finance (last resort)
REQUEST_DELAY = 1.5  # seconds between requests
MAX_RETRIES = 2
RETRY_DELAY = 3  # seconds between retries
CACHE_TTL_HOURS = 4  # Use cached data if less than this old

# IB connection settings
IB_HOST = "127.0.0.1"
IB_PORT = 4001  # Default to Gateway
IB_CLIENT_ID = 99  # Use different client ID to avoid conflicts

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


def get_cached_rating(ticker: str) -> Optional[dict]:
    """Get cached rating if still fresh."""
    cache = load_json(RATINGS_CACHE_FILE)
    cached = cache.get("ratings", {}).get(ticker.upper())
    
    if cached and cached.get("fetched_at"):
        try:
            fetched = datetime.fromisoformat(cached["fetched_at"])
            if datetime.now() - fetched < timedelta(hours=CACHE_TTL_HOURS):
                cached["from_cache"] = True
                return cached
        except:
            pass
    return None


# =============================================================================
# IB Data Source (Primary)
# =============================================================================

def connect_ib(port: int = IB_PORT) -> Tuple[any, bool]:
    """Try to connect to IB. Returns (ib_instance, success)."""
    try:
        from ib_insync import IB, Stock
        ib = IB()
        ib.connect(IB_HOST, port, clientId=IB_CLIENT_ID, timeout=5)
        return ib, True
    except ImportError:
        return None, False
    except Exception as e:
        return None, False


def fetch_from_ib(ib, ticker: str) -> Optional[dict]:
    """
    Fetch analyst ratings from Interactive Brokers using RESC fundamental data.
    Returns parsed ratings dict or None if unavailable.
    
    Note: Requires IB fundamentals data subscription. Error 10358 means 
    "Fundamentals data is not allowed" - subscription not active.
    """
    try:
        from ib_insync import Stock
        
        contract = Stock(ticker, 'SMART', 'USD')
        ib.qualifyContracts(contract)
        
        # Request analyst estimates (RESC = Reuters Estimates)
        # This may fail with error 10358 if fundamentals subscription not active
        xml_data = ib.reqFundamentalData(contract, 'RESC')
        
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
# Yahoo Finance Data Source (Fallback)
# =============================================================================

def fetch_from_yahoo(ticker: str) -> dict:
    """
    Fetch analyst ratings from Yahoo Finance (fallback source).
    """
    try:
        import yfinance as yf
    except ImportError:
        return {
            "ticker": ticker.upper(),
            "fetched_at": datetime.now().isoformat(),
            "source": "yahoo",
            "error": "yfinance not installed. Run: pip install yfinance"
        }
    
    result = {
        "ticker": ticker.upper(),
        "fetched_at": datetime.now().isoformat(),
        "source": "yahoo",
        "ratings": None,
        "recommendation": None,
        "target_price": None,
        "recent_changes": [],
        "error": None,
        "from_cache": False
    }
    
    for attempt in range(MAX_RETRIES):
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            
            # Check for rate limit response
            if not info or len(info) < 5:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY * (attempt + 1))
                    continue
                result["error"] = "Yahoo Finance rate limited or no data"
                return result
            
            break  # Success
        except Exception as e:
            if "rate" in str(e).lower() or "429" in str(e):
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY * (attempt + 1))
                    continue
            result["error"] = str(e)
            return result
    
    try:
        # Get recommendation data
        result["recommendation"] = info.get("recommendationKey", "N/A")
        result["recommendation_mean"] = info.get("recommendationMean")
        result["analyst_count"] = info.get("numberOfAnalystOpinions", 0)
        
        # Target prices
        result["target_price"] = {
            "current": info.get("currentPrice"),
            "mean": info.get("targetMeanPrice"),
            "high": info.get("targetHighPrice"),
            "low": info.get("targetLowPrice"),
            "median": info.get("targetMedianPrice")
        }
        
        # Calculate upside/downside
        current = info.get("currentPrice")
        target_mean = info.get("targetMeanPrice")
        if current and target_mean:
            result["target_upside_pct"] = round(((target_mean - current) / current) * 100, 1)
        
        # Get recommendations breakdown
        try:
            recs = stock.recommendations
            if recs is not None and not recs.empty:
                recent = recs.tail(1).iloc[0].to_dict() if len(recs) > 0 else {}
                
                result["ratings"] = {
                    "strong_buy": int(recent.get("strongBuy", 0)),
                    "buy": int(recent.get("buy", 0)),
                    "hold": int(recent.get("hold", 0)),
                    "sell": int(recent.get("sell", 0)),
                    "strong_sell": int(recent.get("strongSell", 0))
                }
                
                total = sum(result["ratings"].values())
                if total > 0:
                    result["ratings"]["total"] = total
                    result["ratings"]["buy_pct"] = round(
                        (result["ratings"]["strong_buy"] + result["ratings"]["buy"]) / total * 100, 1
                    )
                    result["ratings"]["sell_pct"] = round(
                        (result["ratings"]["sell"] + result["ratings"]["strong_sell"]) / total * 100, 1
                    )
                
                # Detect changes
                if len(recs) >= 2:
                    prev = recs.iloc[-2].to_dict()
                    curr = recs.iloc[-1].to_dict()
                    
                    changes = []
                    for field in ["strongBuy", "buy", "hold", "sell", "strongSell"]:
                        prev_val = int(prev.get(field, 0))
                        curr_val = int(curr.get(field, 0))
                        if prev_val != curr_val:
                            changes.append({
                                "category": field,
                                "previous": prev_val,
                                "current": curr_val,
                                "change": curr_val - prev_val
                            })
                    
                    if changes:
                        result["recent_changes"] = changes
                        result["has_recent_changes"] = True
                    else:
                        result["has_recent_changes"] = False
                        
        except Exception as e:
            result["recommendations_error"] = str(e)
        
        # Get upgrade/downgrade history
        try:
            upgrades = stock.upgrades_downgrades
            if upgrades is not None and not upgrades.empty:
                recent_upgrades = []
                
                for idx, row in upgrades.tail(10).iterrows():
                    try:
                        if hasattr(idx, 'to_pydatetime'):
                            date = idx.to_pydatetime()
                        else:
                            date = datetime.now()
                        
                        recent_upgrades.append({
                            "date": date.strftime("%Y-%m-%d") if hasattr(date, 'strftime') else str(idx),
                            "firm": row.get("Firm", "Unknown"),
                            "to_grade": row.get("ToGrade", ""),
                            "from_grade": row.get("FromGrade", ""),
                            "action": row.get("Action", "")
                        })
                    except:
                        continue
                
                result["upgrade_downgrade_history"] = list(reversed(recent_upgrades))
                if recent_upgrades:
                    result["has_recent_changes"] = True
        except Exception as e:
            result["upgrades_error"] = str(e)
            
    except Exception as e:
        result["error"] = str(e)
    
    return result


# =============================================================================
# Main Fetch Function with Priority
# =============================================================================

def fetch_analyst_ratings(ticker: str, use_cache: bool = True, force_source: str = None, ib=None) -> dict:
    """
    Fetch analyst ratings with data source priority:
    1. Interactive Brokers (if connected)
    2. Yahoo Finance (fallback)
    """
    ticker = ticker.upper()
    
    # Check cache first
    if use_cache and not force_source:
        cached = get_cached_rating(ticker)
        if cached and not cached.get("error"):
            return cached
    
    # Try IB first (unless forced to Yahoo)
    if force_source != "yahoo" and ib is not None:
        result = fetch_from_ib(ib, ticker)
        if result and not result.get("error"):
            return result
    
    # Fallback to Yahoo Finance
    return fetch_from_yahoo(ticker)


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
                        lines.append(f"  {change['category']}: {change['previous']} → {change['current']} ({direction}{abs(change.get('change', 0))})")
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
    lines.append("Sources: IB = Interactive Brokers, yaho = Yahoo Finance, © = cached")
    
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
    parser.add_argument("--source", choices=["ib", "yahoo"], help="Force specific data source")
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
    ib = None
    ib_connected = False
    ib_port = args.port
    
    if args.source != "yahoo":
        print(f"Attempting IB connection on port {ib_port}...", file=sys.stderr, end=" ")
        ib, ib_connected = connect_ib(port=ib_port)
        if ib_connected:
            print("Connected ✓", file=sys.stderr)
        else:
            print("Not available, using Yahoo Finance", file=sys.stderr)
    else:
        print("Using Yahoo Finance (forced)", file=sys.stderr)
    
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
            ib=ib
        )
        results.append(data)
        ratings_dict[ticker] = data
        
        source = data.get("source", "?")
        cached = " (cached)" if data.get("from_cache") else ""
        print(f"{source}{cached}", file=sys.stderr)
        
        # Rate limiting for Yahoo
        if not ib_connected and i < len(tickers) - 1:
            time.sleep(REQUEST_DELAY)
    
    # Disconnect IB
    if ib and ib_connected:
        try:
            ib.disconnect()
        except:
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


if __name__ == "__main__":
    main()
