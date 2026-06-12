#!/usr/bin/env python3
"""
Fetch latest tweets from X accounts and extract ticker sentiment.
Updates the watchlist with bullish/bearish signals.

Uses agent-browser CLI for local browser automation.

Usage:
    python3 scripts/fetch_x_watchlist.py --account USERNAME
    python3 scripts/fetch_x_watchlist.py --account USERNAME --hours 48
    python3 scripts/fetch_x_watchlist.py --hours 48         # Look back 48 hours
"""

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Phase 4 wiring: load env + ensure scripts/ on sys.path so db.writer
# imports work when invoked as a subprocess. Bypass embedded replica.
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]
    load_dotenv(_PROJECT_DIR / ".env")
    load_dotenv(_PROJECT_DIR / "web" / ".env")
except Exception:
    pass

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent

# Known ticker patterns
COMMON_TICKERS = {
    # Semiconductors
    "ON", "POWI", "SLAB", "ALGM", "VECO", "PLAB", "RMBS", "LASR", "MPWR", "COHR",
    "NVDA", "AMD", "INTC", "MU", "AVGO", "QCOM", "TSM", "ASML", "LRCX", "AMAT",
    "KLAC", "MRVL", "ADI", "TXN", "NXPI", "SWKS", "QRVO", "MCHP", "WOLF", "CRUS",
    # Mag 7
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "TSLA",
    # Popular
    "SPY", "QQQ", "IWM", "DIA", "VIX", "UVXY", "SQQQ", "TQQQ",
    "PLTR", "SOFI", "COIN", "HOOD", "RBLX", "SNOW", "NET", "CRWD",
}

# Words to exclude from ticker detection
EXCLUDE_WORDS = {
    'THE', 'AND', 'FOR', 'ARE', 'BUT', 'NOT', 'YOU', 'ALL', 'CAN', 'HER', 'WAS',
    'ONE', 'OUR', 'OUT', 'DAY', 'HAD', 'HAS', 'HIS', 'HOW', 'ITS', 'MAY', 'NEW',
    'NOW', 'OLD', 'SEE', 'WAY', 'WHO', 'DID', 'GET', 'LET', 'PUT', 'SAY', 'SHE',
    'TOO', 'USE', 'IMO', 'BTW', 'FYI', 'LOL', 'OMG', 'CEO', 'CFO', 'CTO', 'COO',
    'IPO', 'ETF', 'ATH', 'ATL', 'EPS', 'GDP', 'CPI', 'PPI', 'FED', 'SEC', 'FDA',
    'USA', 'USD', 'EUR', 'GBP', 'JPY', 'BTC', 'ETH', 'NFT', 'API', 'AI', 'ML',
    'DD', 'TA', 'FA', 'RSI', 'EMA', 'SMA', 'MACD', 'PE', 'PS', 'PB', 'EV', 'PT',
    'SI', 'IV', 'HV', 'OI', 'ATM', 'OTM', 'ITM', 'DTE', 'PM', 'AM', 'EST', 'PST',
    'UTC', 'UK', 'EU', 'US', 'CA', 'MX', 'JP', 'CN', 'HK', 'DE', 'FR', 'GO', 'UP',
    'SO', 'IF', 'BE', 'AT', 'BY', 'OR', 'AN', 'AS', 'DO', 'IS', 'IT', 'NO', 'TO',
    'WE', 'HE', 'IN',
}


def extract_tickers(text: str) -> List[str]:
    """Extract potential tickers from text using regex."""
    tickers = set()
    
    # Pattern 1: $TICKER format (most reliable)
    cashtags = re.findall(r'\$([A-Z]{1,5})\b', text.upper())
    tickers.update(cashtags)
    
    # Pattern 2: Known tickers mentioned without $
    text_upper = text.upper()
    for ticker in COMMON_TICKERS:
        if re.search(rf'\b{ticker}\b', text_upper):
            tickers.add(ticker)
    
    # Filter out excluded words (but keep if it was a cashtag)
    result = set()
    for t in tickers:
        if t in cashtags:
            result.add(t)  # Always keep cashtags
        elif t not in EXCLUDE_WORDS:
            result.add(t)
    
    return list(result)


def analyze_sentiment(text: str, ticker: str) -> Tuple[str, str]:
    """
    Simple rule-based sentiment analysis.
    Returns (sentiment, confidence)
    """
    text_lower = text.lower()
    
    # Strong bullish signals
    bullish_strong = [
        'buying', 'bought', 'long', 'calls', 'moon', 'rocket', '🚀', '📈',
        'bullish', 'breakout', 'accumulating', 'loading', 'adding', 'love',
        'undervalued', 'cheap', 'opportunity', 'upside', 'target raised',
        'great entry', 'strong', 'ripping', 'flying', 'running',
    ]
    
    # Strong bearish signals  
    bearish_strong = [
        'selling', 'sold', 'short', 'puts', 'dump', 'crash', '📉', '🔻',
        'bearish', 'breakdown', 'distributing', 'trimming', 'reducing',
        'overvalued', 'expensive', 'downside', 'target cut', 'avoid',
        'weak', 'taking profits', 'exit', 'closing', 'ugly',
    ]
    
    # Weak bullish signals
    bullish_weak = [
        'like', 'watching', 'interesting', 'potential', 'could run',
        'support', 'holding', 'bounce', 'recovery', 'oversold',
    ]
    
    # Weak bearish signals
    bearish_weak = [
        'caution', 'careful', 'risk', 'resistance', 'struggling',
        'overbought', 'extended', 'stretched', 'toppy',
    ]
    
    bullish_score = 0
    bearish_score = 0
    
    for signal in bullish_strong:
        if signal in text_lower:
            bullish_score += 2
    
    for signal in bearish_strong:
        if signal in text_lower:
            bearish_score += 2
            
    for signal in bullish_weak:
        if signal in text_lower:
            bullish_score += 1
            
    for signal in bearish_weak:
        if signal in text_lower:
            bearish_score += 1
    
    # Determine sentiment
    if bullish_score > bearish_score + 1:
        sentiment = "BULLISH"
        confidence = "HIGH" if bullish_score >= 4 else "MEDIUM" if bullish_score >= 2 else "LOW"
    elif bearish_score > bullish_score + 1:
        sentiment = "BEARISH"
        confidence = "HIGH" if bearish_score >= 4 else "MEDIUM" if bearish_score >= 2 else "LOW"
    else:
        sentiment = "NEUTRAL"
        confidence = "LOW"
    
    return sentiment, confidence


def run_agent_browser(command: str, session: str = "xscan", timeout: int = 30) -> Optional[str]:
    """Run an agent-browser command and return output."""
    import shlex
    cmd_parts = ["agent-browser"] + shlex.split(command) + ["--session", session]
    try:
        result = subprocess.run(
            cmd_parts,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        if result.returncode != 0 and result.stderr:
            # Only print errors if they're significant
            if "error" in result.stderr.lower():
                print(f"  ⚠️ {result.stderr.strip()[:100]}")
        return result.stdout
    except subprocess.TimeoutExpired:
        print(f"  ⚠️ Command timed out")
        return None
    except Exception as e:
        print(f"  ✗ Error: {e}")
        return None


def fetch_tweets_agent_browser(account: str, hours: int = 24) -> List[Dict]:
    """
    Fetch tweets using agent-browser CLI.
    """
    tweets = []
    session = f"xscan_{account}"
    
    print(f"🌐 Opening X profile for @{account}...")
    
    # Open the X profile
    open_result = run_agent_browser(f'open "https://x.com/{account}"', session, timeout=30)
    if open_result is None:
        return tweets
    
    # Wait for page to load
    print("  Waiting for page load...")
    time.sleep(5)
    
    # Get page snapshot with interactive mode to see elements
    print("  Taking snapshot...")
    snapshot = run_agent_browser('snapshot -i -c', session, timeout=20)
    
    if not snapshot:
        print("  ✗ Failed to get snapshot")
        run_agent_browser('close', session)
        return tweets
    
    # Scroll down to load more tweets
    print("  Scrolling to load more content...")
    run_agent_browser('press End', session, timeout=10)
    time.sleep(2)
    run_agent_browser('press End', session, timeout=10)
    time.sleep(2)
    
    # Get another snapshot after scrolling
    snapshot2 = run_agent_browser('snapshot -i -c', session, timeout=20)
    if snapshot2:
        snapshot = snapshot + "\n" + snapshot2
    
    # Close browser
    print("  Closing browser...")
    run_agent_browser('close', session, timeout=10)
    
    # Parse tweets from snapshot
    print("  Parsing tweets...")
    
    # Look for tweet-like content in the snapshot
    # Tweets typically have text content followed by engagement metrics
    lines = snapshot.split('\n')
    
    current_text = []
    for line in lines:
        line = line.strip()
        
        # Skip empty lines and obvious UI elements
        if not line or len(line) < 10:
            continue
        if line.startswith('[') or line.startswith('@e'):
            continue
        if any(x in line.lower() for x in ['cookie', 'sign up', 'log in', 'privacy', 'terms']):
            continue
        
        # Check if this line contains tickers
        tickers = extract_tickers(line)
        if tickers and len(line) > 20:
            tweets.append({
                'text': line,
                'timestamp': 'recent',
                'tickers': tickers
            })
    
    # Deduplicate tweets
    seen = set()
    unique_tweets = []
    for tweet in tweets:
        text_key = tweet['text'][:50]
        if text_key not in seen:
            seen.add(text_key)
            unique_tweets.append(tweet)
    
    return unique_tweets


def update_watchlist(account: str, tweets: List[Dict], watchlist_path: str = "data/watchlist.json") -> Tuple[List[str], List[str]]:
    """Update the watchlist with tickers from the scan."""
    watchlist_file = PROJECT_ROOT / watchlist_path
    
    # Load existing watchlist
    if watchlist_file.exists():
        with open(watchlist_file, 'r') as f:
            watchlist = json.load(f)
    else:
        watchlist = {"last_updated": "", "tickers": [], "subcategories": {}}
    
    # Ensure subcategories exists
    if "subcategories" not in watchlist:
        watchlist["subcategories"] = {}
    
    # Get or create the account subcategory
    account_key = f"@{account}"
    if account_key not in watchlist["subcategories"]:
        watchlist["subcategories"][account_key] = {
            "source": f"https://x.com/{account}",
            "added": datetime.now().strftime("%Y-%m-%d"),
            "description": f"Tickers from X account @{account}",
            "tickers": []
        }
    
    subcategory = watchlist["subcategories"][account_key]
    existing_tickers = {t["ticker"]: t for t in subcategory["tickers"]}
    
    new_tickers = []
    updated_tickers = []
    
    # Process each tweet
    for tweet in tweets:
        for ticker in tweet.get('tickers', []):
            ticker = ticker.upper()
            sentiment, confidence = analyze_sentiment(tweet['text'], ticker)
            
            if ticker in existing_tickers:
                # Update existing ticker
                t = existing_tickers[ticker]
                if "sentiment_history" not in t:
                    t["sentiment_history"] = []
                t["sentiment_history"].append({
                    "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "sentiment": sentiment,
                    "confidence": confidence,
                    "tweet": tweet['text'][:100] + "..." if len(tweet['text']) > 100 else tweet['text']
                })
                t["sentiment"] = sentiment
                t["confidence"] = confidence
                t["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                if ticker not in updated_tickers:
                    updated_tickers.append(ticker)
            else:
                # Add new ticker
                new_entry = {
                    "ticker": ticker,
                    "sector": "Unknown",
                    "signal": "UNSCANNED",
                    "sentiment": sentiment,
                    "confidence": confidence,
                    "added": datetime.now().strftime("%Y-%m-%d"),
                    "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "notes": f"From @{account}",
                    "sentiment_history": [{
                        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "sentiment": sentiment,
                        "confidence": confidence,
                        "tweet": tweet['text'][:100] + "..." if len(tweet['text']) > 100 else tweet['text']
                    }]
                }
                subcategory["tickers"].append(new_entry)
                existing_tickers[ticker] = new_entry
                new_tickers.append(ticker)
    
    # Update last scan time
    subcategory["last_scan"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    subcategory["last_scan_tweets"] = len(tweets)
    
    # Update main watchlist timestamp
    watchlist["last_updated"] = datetime.now().strftime("%Y-%m-%dT%H:%M")
    
    # Save watchlist
    with open(watchlist_file, 'w') as f:
        json.dump(watchlist, f, indent=2)

    # Phase 4 dual-write — mirror per-ticker rows into the watchlist table.
    # The disk JSON remains the canonical blob; the table lets per-row
    # queries on /scanner et al. work without parsing the 590KB file.
    _dual_write_watchlist_to_db(account, subcategory, new_tickers + updated_tickers)

    return new_tickers, updated_tickers


def _dual_write_watchlist_to_db(account: str, subcategory: Dict, touched_tickers: List[str]) -> None:
    """Best-effort upsert of per-ticker rows into the watchlist table.

    Only writes the tickers that changed in this scan (touched_tickers)
    to keep the cloud chatter minimal — the disk JSON is the full
    snapshot of record. Errors are logged + swallowed so a Turso outage
    doesn't break the scan loop.
    """
    if not touched_tickers:
        return
    try:
        from db.writer import upsert_watchlist_ticker
    except ImportError:
        return
    try:
        for entry in subcategory.get("tickers", []):
            ticker = entry.get("ticker")
            if not ticker or ticker not in touched_tickers:
                continue
            upsert_watchlist_ticker(
                ticker,
                sector=entry.get("sector") or subcategory.get("sector"),
                source=f"@{account}",
                payload=entry,
            )
    except Exception as exc:  # noqa: BLE001
        print(f"  Warning: watchlist db dual-write failed: {exc}")


def print_summary(account: str, tweets: List[Dict]):
    """Print a summary of the scan results."""
    print(f"\n{'='*60}")
    print(f"X SCAN RESULTS: @{account}")
    print(f"{'='*60}")
    print(f"Tweets with tickers: {len(tweets)}")
    
    if not tweets:
        print("No tweets with tickers found.")
        return
    
    # Aggregate by sentiment
    bullish = []
    bearish = []
    neutral = []
    
    for tweet in tweets:
        for ticker in tweet.get('tickers', []):
            sentiment, confidence = analyze_sentiment(tweet['text'], ticker)
            entry = f"{ticker} ({confidence[0]})"
            if sentiment == "BULLISH":
                bullish.append(entry)
            elif sentiment == "BEARISH":
                bearish.append(entry)
            else:
                neutral.append(entry)
    
    print(f"\n📈 BULLISH: {', '.join(sorted(set(bullish))) if bullish else 'None'}")
    print(f"📉 BEARISH: {', '.join(sorted(set(bearish))) if bearish else 'None'}")
    print(f"➖ NEUTRAL: {', '.join(sorted(set(neutral))) if neutral else 'None'}")
    
    print(f"\n{'─'*60}")
    print("TWEETS WITH TICKERS:")
    print(f"{'─'*60}")
    
    for i, tweet in enumerate(tweets[:10], 1):
        print(f"\n[{i}] {tweet.get('timestamp', 'unknown')}")
        text = tweet['text'][:150] + '...' if len(tweet['text']) > 150 else tweet['text']
        print(f"    {text}")
        tickers_str = ", ".join(tweet.get('tickers', []))
        print(f"    Tickers: {tickers_str}")


def main():
    parser = argparse.ArgumentParser(description="Fetch X account tweets and extract ticker sentiment")
    parser.add_argument("--account", "-a", required=True,
                        help="X account to scan (without @)")
    parser.add_argument("--hours", "-t", type=int, default=24,
                        help="Hours to look back (default: 24)")
    parser.add_argument("--no-update", action="store_true",
                        help="Don't update watchlist, just display results")
    parser.add_argument("--test", action="store_true",
                        help="Test with sample data")
    
    args = parser.parse_args()
    
    tweets = []
    
    if args.test:
        # Test with sample tweets
        print("🧪 Running in test mode with sample data...")
        sample_tweets = [
            "$ALGM looking weak, distribution pattern forming. Avoiding.",
            "Loading up on $MPWR here, great entry point 🚀",
            "$SLAB breakout incoming, accumulating calls",
            "Taking profits on $RMBS, extended here",
            "$ON semiconductor cycle turning, watching closely",
        ]
        for text in sample_tweets:
            tickers = extract_tickers(text)
            if tickers:
                tweets.append({
                    'text': text,
                    'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M"),
                    'tickers': tickers
                })
    else:
        # Use agent-browser for real scraping
        tweets = fetch_tweets_agent_browser(args.account, args.hours)
    
    # Print summary
    print_summary(args.account, tweets)
    
    # Update watchlist
    if not args.no_update and tweets:
        new_tickers, updated_tickers = update_watchlist(args.account, tweets)
        
        print(f"\n{'='*60}")
        print("WATCHLIST UPDATED")
        print(f"{'='*60}")
        if new_tickers:
            print(f"✓ New tickers added: {', '.join(new_tickers)}")
        if updated_tickers:
            print(f"✓ Tickers updated: {', '.join(updated_tickers)}")
        if not new_tickers and not updated_tickers:
            print("  No changes to watchlist")


if __name__ == "__main__":
    main()
