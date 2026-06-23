#!/usr/bin/env python3
"""
Fetch tweets from X accounts using xAI's live search API.
Much more reliable than browser scraping.

Usage:
    python3 scripts/fetch_x_xai.py --account USERNAME
    python3 scripts/fetch_x_xai.py --account USERNAME --days 7
    python3 scripts/fetch_x_xai.py --days 7                # Look back 7 days
    python3 scripts/fetch_x_xai.py --dry-run               # Don't update watchlist
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    import urllib.request
    import urllib.error
    HAS_REQUESTS = False

XAI_API_KEY = os.environ.get("XAI_API_KEY")
XAI_BASE_URL = "https://api.x.ai/v1"

# Known ticker patterns
COMMON_TICKERS = {
    # Semiconductors
    "ON", "POWI", "SLAB", "ALGM", "VECO", "PLAB", "RMBS", "LASR", "MPWR", "COHR",
    "NVDA", "AMD", "INTC", "MU", "AVGO", "QCOM", "TSM", "ASML", "LRCX", "AMAT",
    "KLAC", "MRVL", "ADI", "TXN", "NXPI", "SWKS", "QRVO", "MCHP", "WOLF", "CRUS",
    # Mag 7
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "TSLA",
    # ETFs
    "SPY", "QQQ", "IWM", "DIA", "EWY", "EEM", "FXI",
    # Popular
    "PLTR", "SOFI", "COIN", "HOOD", "RBLX", "SNOW", "NET", "CRWD",
    # Bitcoin miners / crypto
    "WULF", "MARA", "RIOT", "CLSK", "CIFR", "IREN", "BITF", "HIVE",
    # Others mentioned in watchlist
    "ALAB", "CRDO", "NBIS", "JOBY", "OKLO", "IONQ", "RGTI",
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
    'GO', 'UP', 'SO', 'IF', 'BE', 'AT', 'BY', 'OR', 'AN', 'AS', 'DO', 'IS', 'IT',
    'NO', 'TO', 'WE', 'HE', 'IN', 'ON', 'OK', 'VS',
}


def extract_tickers(text: str) -> List[str]:
    """Extract potential tickers from text."""
    tickers = set()
    
    # Pattern 1: $TICKER format (most reliable)
    cashtags = re.findall(r'\$([A-Z]{1,5})\b', text.upper())
    tickers.update(cashtags)
    
    # Pattern 2: Known tickers mentioned without $
    text_upper = text.upper()
    for ticker in COMMON_TICKERS:
        if re.search(rf'\b{ticker}\b', text_upper):
            tickers.add(ticker)
    
    # Filter out excluded words (but keep cashtags)
    result = set()
    for t in tickers:
        if t in cashtags:
            result.add(t)
        elif t not in EXCLUDE_WORDS:
            result.add(t)
    
    return list(result)


def analyze_sentiment(text: str, ticker: str) -> Tuple[str, str]:
    """Rule-based sentiment analysis."""
    text_lower = text.lower()
    
    bullish_strong = [
        'buying', 'bought', 'long', 'calls', 'moon', 'rocket', '🚀', '📈',
        'bullish', 'breakout', 'accumulating', 'loading', 'adding', 'love',
        'undervalued', 'cheap', 'opportunity', 'upside', 'target raised',
        'great entry', 'strong buy', 'ripping', 'flying', 'running', 'squeeze',
    ]
    
    bearish_strong = [
        'selling', 'sold', 'short', 'puts', 'dump', 'crash', '📉', '🔻',
        'bearish', 'breakdown', 'distributing', 'trimming', 'reducing',
        'overvalued', 'expensive', 'downside', 'target cut', 'avoid',
        'weak', 'taking profits', 'exit', 'closing', 'ugly', 'warning',
    ]
    
    bullish_weak = [
        'like', 'watching', 'interesting', 'potential', 'could run',
        'support', 'holding', 'bounce', 'recovery', 'oversold',
    ]
    
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


def xai_search(account: str, days: int = 3) -> Dict:
    """
    Search X/Twitter using xAI's Responses API with x_search tool.
    Uses curl subprocess for reliable HTTPS handling.
    """
    import subprocess
    
    if not XAI_API_KEY:
        raise ValueError("XAI_API_KEY environment variable not set")
    
    # Calculate date range
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    
    # Build the request - mention account in prompt (faster than using filters)
    # Note: Using allowed_x_handles filter causes slow pagination; prompting is faster
    query = f"List all stock tickers mentioned by @{account} in the last {days} days. Format each as: $TICKER: Bullish/Bearish - brief reason"
    
    payload = {
        "model": "grok-4-0709",  # Required for x_search tool
        "input": [
            {
                "role": "user",
                "content": query
            }
        ],
        "tools": [
            {
                "type": "x_search"
                # Note: Omitting filters is faster - the model handles account filtering via the prompt
            }
        ],
        "store": False
    }
    
    # Use curl for reliable HTTPS - Python's SSL on this machine has issues
    # Note: xAI x_search can take 2-3 minutes for comprehensive account searches
    cmd = [
        "curl", "-s", "-X", "POST",
        f"{XAI_BASE_URL}/responses",
        "-H", f"Authorization: Bearer {XAI_API_KEY}",
        "-H", "Content-Type: application/json",
        "--max-time", "300",  # 5 minute timeout
        "-d", json.dumps(payload)
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=320)
        if result.returncode == 28:  # curl timeout
            raise RuntimeError("API request timed out (300s). The xAI API may be slow for this account.")
        if result.returncode != 0:
            raise RuntimeError(f"curl failed (code {result.returncode}): {result.stderr}")
        
        if not result.stdout.strip():
            raise RuntimeError("Empty response from API")
            
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Request timed out (300s)")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON response: {e}\nResponse: {result.stdout[:500]}")


def parse_xai_response(response: Dict) -> List[Dict]:
    """Parse xAI response into tweet-like structures."""
    tweets = []
    urls = []
    
    # Get the content from the response
    output = response.get("output", [])
    
    for item in output:
        # Skip tool call entries
        if item.get("type") == "custom_tool_call":
            continue
            
        # Process message content
        content_list = item.get("content", [])
        if isinstance(content_list, str):
            content_list = [{"type": "output_text", "text": content_list}]
        
        for content_item in content_list:
            if content_item.get("type") == "output_text":
                text = content_item.get("text", "")
                
                # Extract URLs from annotations
                annotations = content_item.get("annotations", [])
                for ann in annotations:
                    if ann.get("type") == "url_citation":
                        urls.append(ann.get("url", ""))
                
                # Parse multiple ticker formats from xAI response:
                # Format 1: - **$TICKER** (Sentiment): explanation
                # Format 2: - $TICKER: Sentiment - explanation  
                # Format 3: - $TICKER: Sentiment
                patterns = [
                    # **$TICKER** (Sentiment): explanation
                    re.compile(r'\*\*\\\$([A-Z]{1,5})\*\*\s*\((Bullish|Bearish|Neutral)[^)]*\):\s*(.+?)(?=\n-|\n\n|$)', re.IGNORECASE | re.DOTALL),
                    # $TICKER: Sentiment - explanation or just $TICKER: Sentiment
                    re.compile(r'[-•]\s*\\\$([A-Z]{1,5}):\s*(Bullish|Bearish|Neutral)(?:\s*[-–]\s*(.+?))?(?=\n[-•]|\n\n|$)', re.IGNORECASE | re.DOTALL),
                    # **$TICKER**: Sentiment
                    re.compile(r'\*\*\\\$([A-Z]{1,5})\*\*:\s*(Bullish|Bearish|Neutral)', re.IGNORECASE),
                ]
                
                for pattern in patterns:
                    for match in pattern.finditer(text):
                        ticker = match.group(1).upper()
                        sentiment = match.group(2).upper()
                        explanation = match.group(3).strip() if len(match.groups()) > 2 and match.group(3) else ""
                        
                        tweets.append({
                            'text': f"${ticker}: {explanation}" if explanation else f"${ticker}",
                            'tickers': [ticker],
                            'sentiment_override': sentiment,
                            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M"),
                            'source': 'xai_analysis'
                        })
                
                # Also extract any tickers mentioned in paragraphs
                paragraphs = [p.strip() for p in text.split('\n') if p.strip() and p.startswith('-')]
                for para in paragraphs:
                    # Skip if already parsed as structured ticker
                    if '**$' in para:
                        continue
                    para_tickers = extract_tickers(para)
                    if para_tickers:
                        tweets.append({
                            'text': para,
                            'tickers': para_tickers,
                            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M"),
                            'source': 'xai_search'
                        })
    
    # Deduplicate by ticker
    seen_tickers = set()
    unique_tweets = []
    for tweet in tweets:
        key = tuple(sorted(tweet['tickers']))
        if key not in seen_tickers:
            seen_tickers.add(key)
            unique_tweets.append(tweet)
    
    return unique_tweets


def update_watchlist(account: str, tweets: List[Dict]) -> Tuple[List[str], List[str]]:
    """Update Turso watchlist rows with tickers from the xAI scan."""
    from db.readers import read_watchlist_items
    from db.writer import upsert_watchlist_ticker

    account_key = f"@{account}"
    existing_items = [dict(item) for item in read_watchlist_items()]
    subcategory = {
        "source": f"https://x.com/{account}",
        "added": datetime.now().strftime("%Y-%m-%d"),
        "description": f"Tickers from X account @{account}",
        "tickers": existing_items,
    }
    existing_tickers = {t["ticker"]: t for t in subcategory.get("tickers", []) if t.get("ticker")}
    
    new_tickers = []
    updated_tickers = []
    
    for tweet in tweets:
        for ticker in tweet.get('tickers', []):
            ticker = ticker.upper()
            # Use sentiment from xAI analysis if available, otherwise use rule-based
            if 'sentiment_override' in tweet:
                sentiment = tweet['sentiment_override']
                confidence = "HIGH"  # xAI analysis is high confidence
            else:
                sentiment, confidence = analyze_sentiment(tweet['text'], ticker)
            
            if ticker in existing_tickers:
                t = existing_tickers[ticker]
                if "sentiment_history" not in t:
                    t["sentiment_history"] = []
                t["sentiment_history"].append({
                    "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "sentiment": sentiment,
                    "confidence": confidence,
                    "tweet": tweet['text'][:200] + "..." if len(tweet['text']) > 200 else tweet['text']
                })
                # Keep only last 10 entries
                t["sentiment_history"] = t["sentiment_history"][-10:]
                t["sentiment"] = sentiment
                t["confidence"] = confidence
                t["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                if ticker not in updated_tickers:
                    updated_tickers.append(ticker)
            else:
                new_entry = {
                    "ticker": ticker,
                    "sector": "Unknown",
                    "signal": "UNSCANNED",
                    "sentiment": sentiment,
                    "confidence": confidence,
                    "added": datetime.now().strftime("%Y-%m-%d"),
                    "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "notes": f"From @{account} via xAI search",
                    "sentiment_history": [{
                        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "sentiment": sentiment,
                        "confidence": confidence,
                        "tweet": tweet['text'][:200] + "..." if len(tweet['text']) > 200 else tweet['text']
                    }]
                }
                subcategory["tickers"].append(new_entry)
                existing_tickers[ticker] = new_entry
                new_tickers.append(ticker)
    
    # Update scan metadata
    subcategory["last_scan"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    subcategory["last_scan_method"] = "xai_api"
    subcategory["last_scan_tweets"] = len(tweets)
    
    for entry in subcategory.get("tickers", []):
        ticker = entry.get("ticker")
        if not ticker or ticker not in set(new_tickers + updated_tickers):
            continue
        upsert_watchlist_ticker(
            ticker,
            sector=entry.get("sector") or subcategory.get("sector"),
            source=account_key,
            payload=entry,
        )
    
    return new_tickers, updated_tickers


def print_summary(account: str, tweets: List[Dict], raw_response: Dict = None):
    """Print scan results."""
    print(f"\n{'='*60}")
    print(f"X SCAN RESULTS: @{account} (via xAI API)")
    print(f"{'='*60}")
    print(f"Posts analyzed: {len(tweets)}")
    
    if not tweets:
        print("No posts with tickers found.")
        return
    
    # Aggregate by ticker and sentiment
    ticker_sentiments = {}
    for tweet in tweets:
        for ticker in tweet.get('tickers', []):
            sentiment, confidence = analyze_sentiment(tweet['text'], ticker)
            if ticker not in ticker_sentiments:
                ticker_sentiments[ticker] = {'bullish': 0, 'bearish': 0, 'neutral': 0}
            ticker_sentiments[ticker][sentiment.lower()] += 1
    
    # Classify tickers
    bullish = []
    bearish = []
    neutral = []
    
    for ticker, counts in ticker_sentiments.items():
        if counts['bullish'] > counts['bearish']:
            bullish.append(f"{ticker} ({counts['bullish']})")
        elif counts['bearish'] > counts['bullish']:
            bearish.append(f"{ticker} ({counts['bearish']})")
        else:
            neutral.append(ticker)
    
    print(f"\n📈 BULLISH ({len(bullish)}): {', '.join(sorted(bullish)) if bullish else 'None'}")
    print(f"📉 BEARISH ({len(bearish)}): {', '.join(sorted(bearish)) if bearish else 'None'}")
    print(f"➖ NEUTRAL ({len(neutral)}): {', '.join(sorted(neutral)) if neutral else 'None'}")
    
    print(f"\n{'─'*60}")
    print("RECENT POSTS:")
    print(f"{'─'*60}")
    
    for i, tweet in enumerate(tweets[:10], 1):
        print(f"\n[{i}] Tickers: {', '.join(tweet.get('tickers', []))}")
        text = tweet['text'][:250] + '...' if len(tweet['text']) > 250 else tweet['text']
        # Clean up the text for display
        text = ' '.join(text.split())
        print(f"    {text}")
        sentiment, confidence = analyze_sentiment(tweet['text'], tweet['tickers'][0] if tweet['tickers'] else '')
        print(f"    → {sentiment} ({confidence})")
    
    # Show API usage if available
    if raw_response:
        usage = raw_response.get("usage", {})
        tool_usage = raw_response.get("server_side_tool_usage", {})
        if usage or tool_usage:
            print(f"\n{'─'*60}")
            print("API USAGE:")
            if usage:
                print(f"  Tokens: {usage.get('input_tokens', 0)} in / {usage.get('output_tokens', 0)} out")
            if tool_usage:
                print(f"  X searches: {tool_usage.get('x_search_calls', 0)}")


def main():
    parser = argparse.ArgumentParser(description="Fetch X account tweets via xAI API")
    parser.add_argument("--account", "-a", required=True,
                        help="X account to scan (without @)")
    parser.add_argument("--days", "-d", type=int, default=3,
                        help="Days to look back (default: 3)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Don't update watchlist, just display results")
    parser.add_argument("--json", action="store_true",
                        help="Output raw JSON response")
    parser.add_argument("--timeout", "-t", type=int, default=180,
                        help="API timeout in seconds (default: 180)")
    
    args = parser.parse_args()
    
    if not XAI_API_KEY:
        print("❌ Error: XAI_API_KEY environment variable not set")
        print("   Set it with: export XAI_API_KEY=your-api-key")
        sys.exit(1)
    
    print(f"🔍 Searching X for @{args.account} (last {args.days} days)...")
    print(f"   ⏳ Note: xAI x_search can take 2-3 minutes. Please wait...")
    
    try:
        response = xai_search(args.account, args.days)
        
        if args.json:
            print(json.dumps(response, indent=2))
            return
        
        # Show timing
        created = response.get('created_at', 0)
        completed = response.get('completed_at', 0)
        if created and completed:
            print(f"   ✓ API responded in {completed - created} seconds")
        
        tweets = parse_xai_response(response)
        print_summary(args.account, tweets, response)
        
        if not args.dry_run and tweets:
            new_tickers, updated_tickers = update_watchlist(args.account, tweets)
            
            print(f"\n{'='*60}")
            print("TURSO TICKERS UPDATED")
            print(f"{'='*60}")
            if new_tickers:
                print(f"✓ New tickers added: {', '.join(new_tickers)}")
            if updated_tickers:
                print(f"✓ Tickers updated: {', '.join(updated_tickers)}")
            if not new_tickers and not updated_tickers:
                print("  No changes to watchlist")
        
        # Print for startup protocol parsing
        print(f"\nFound {len(tweets)} posts with tickers")
        
    except RuntimeError as e:
        if "timed out" in str(e).lower():
            print(f"⚠️  API timed out. xAI x_search may be rate-limited.")
            print(f"   Try again in a few minutes, or use: python3 scripts/fetch_x_watchlist.py (browser scraper)")
        else:
            print(f"❌ Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
