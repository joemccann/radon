#!/usr/bin/env python3
"""Fetch recent news headlines for a ticker.

Data source priority: UW → Exa (web search) → Yahoo (last resort).

Usage:
    python3 scripts/fetch_news.py AAPL
    python3 scripts/fetch_news.py CRM --json
    python3 scripts/fetch_news.py CRM --days 7
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))


# ── Catalyst keywords that indicate material events ──────────────────────
CATALYST_KEYWORDS = {
    # Corporate actions
    "buyback": "BUYBACK",
    "share repurchase": "BUYBACK",
    "repurchase program": "BUYBACK",
    "stock buyback": "BUYBACK",
    "dividend": "DIVIDEND",
    "special dividend": "DIVIDEND",
    "dividend increase": "DIVIDEND",
    "split": "STOCK_SPLIT",
    "stock split": "STOCK_SPLIT",
    "merger": "M&A",
    "acquisition": "M&A",
    "acquire": "M&A",
    "takeover": "M&A",
    "buyout": "M&A",
    "spinoff": "SPINOFF",
    "spin-off": "SPINOFF",
    "spin off": "SPINOFF",
    # Earnings / guidance
    "earnings": "EARNINGS",
    "earnings beat": "EARNINGS_BEAT",
    "earnings miss": "EARNINGS_MISS",
    "revenue beat": "EARNINGS_BEAT",
    "revenue miss": "EARNINGS_MISS",
    "guidance": "GUIDANCE",
    "raised guidance": "GUIDANCE_UP",
    "lowered guidance": "GUIDANCE_DOWN",
    "raised outlook": "GUIDANCE_UP",
    "lowered outlook": "GUIDANCE_DOWN",
    "outlook": "GUIDANCE",
    # Analyst / rating
    "upgrade": "UPGRADE",
    "downgrade": "DOWNGRADE",
    "price target": "PRICE_TARGET",
    "initiated": "INITIATION",
    "initiate": "INITIATION",
    # Regulatory / legal
    "fda approval": "FDA",
    "fda": "REGULATORY",
    "sec": "REGULATORY",
    "antitrust": "REGULATORY",
    "lawsuit": "LEGAL",
    "settlement": "LEGAL",
    # Product / business
    "partnership": "PARTNERSHIP",
    "contract": "CONTRACT",
    "deal": "DEAL",
    "launch": "PRODUCT_LAUNCH",
    "new product": "PRODUCT_LAUNCH",
    "ai": "AI_CATALYST",
    "artificial intelligence": "AI_CATALYST",
    # Macro
    "tariff": "TARIFF",
    "sanctions": "SANCTIONS",
    "fed": "FED",
    "interest rate": "RATES",
    "rate cut": "RATE_CUT",
    "rate hike": "RATE_HIKE",
}

# Sentiment keywords
BULLISH_KEYWORDS = [
    "buyback", "repurchase", "beat", "raised", "upgrade", "approval",
    "partnership", "record revenue", "all-time high", "strong demand",
    "outperform", "buy rating", "overweight", "raised guidance",
    "raised outlook", "dividend increase", "share repurchase",
    "bullish", "surges", "soars", "rallies", "jumps",
]

BEARISH_KEYWORDS = [
    "miss", "lowered", "downgrade", "lawsuit", "investigation",
    "recall", "layoffs", "restructuring", "weak demand", "underperform",
    "sell rating", "underweight", "lowered guidance", "lowered outlook",
    "warning", "bearish", "plunges", "crashes", "tumbles", "drops",
    "tariff", "sanctions", "antitrust",
]


def classify_headline(headline: str) -> Dict[str, Any]:
    """Classify a headline for catalysts and sentiment.

    Returns dict with:
      catalysts: list of catalyst types found
      sentiment: BULLISH | BEARISH | NEUTRAL
      sentiment_score: -1.0 to +1.0
      is_material: bool (contains a material catalyst)
    """
    lower = headline.lower()

    # Find catalysts
    catalysts = []
    seen_types = set()
    for keyword, cat_type in CATALYST_KEYWORDS.items():
        if keyword in lower and cat_type not in seen_types:
            catalysts.append(cat_type)
            seen_types.add(cat_type)

    # Score sentiment
    bull_count = sum(1 for kw in BULLISH_KEYWORDS if kw in lower)
    bear_count = sum(1 for kw in BEARISH_KEYWORDS if kw in lower)
    total = bull_count + bear_count
    if total == 0:
        sentiment = "NEUTRAL"
        score = 0.0
    elif bull_count > bear_count:
        sentiment = "BULLISH"
        score = min(1.0, bull_count / max(total, 1))
    elif bear_count > bull_count:
        sentiment = "BEARISH"
        score = max(-1.0, -bear_count / max(total, 1))
    else:
        sentiment = "NEUTRAL"
        score = 0.0

    # Material = contains at least one non-analyst, non-macro catalyst
    material_types = {
        "BUYBACK", "DIVIDEND", "STOCK_SPLIT", "M&A", "SPINOFF",
        "EARNINGS", "EARNINGS_BEAT", "EARNINGS_MISS",
        "GUIDANCE", "GUIDANCE_UP", "GUIDANCE_DOWN",
        "FDA", "PRODUCT_LAUNCH", "CONTRACT", "DEAL", "PARTNERSHIP",
    }
    is_material = bool(seen_types & material_types)

    return {
        "catalysts": catalysts,
        "sentiment": sentiment,
        "sentiment_score": round(score, 2),
        "is_material": is_material,
    }


def fetch_news(
    ticker: str,
    days: int = 7,
    limit: int = 20,
) -> Dict[str, Any]:
    """Fetch recent news and classify for catalysts and sentiment.

    Returns dict with:
      ticker, fetched_at, source, headlines (list),
      summary: {total, bullish, bearish, neutral, material_catalysts, sentiment_bias}
    """
    ticker = ticker.upper()
    headlines: List[Dict] = []
    source = "none"

    # ── Source 1: Unusual Whales ─────────────────────────────────────
    try:
        from clients.uw_client import UWClient
        with UWClient() as uw:
            data = uw.get_news_headlines(ticker=ticker, limit=limit)
            items = data.get("data", []) if data else []
            if items:
                source = "unusual_whales"
                for item in items:
                    title = item.get("headline") or item.get("title") or ""
                    pub_date = item.get("published_at") or item.get("date") or ""
                    url = item.get("url") or ""
                    src = item.get("source") or ""
                    is_major = item.get("is_major", False)

                    classification = classify_headline(title)
                    headlines.append({
                        "title": title,
                        "date": pub_date,
                        "source": src,
                        "url": url,
                        "is_major": is_major,
                        **classification,
                    })
    except Exception:
        pass

    # ── Source 2: Yahoo Finance (last resort) ────────────────────────
    if not headlines:
        try:
            import requests
            resp = requests.get(
                f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US",
                timeout=10,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            if resp.status_code == 200:
                import xml.etree.ElementTree as ET
                root = ET.fromstring(resp.text)
                source = "yahoo_finance"
                for item in root.findall(".//item")[:limit]:
                    title = item.findtext("title", "")
                    pub_date = item.findtext("pubDate", "")
                    url = item.findtext("link", "")

                    classification = classify_headline(title)
                    headlines.append({
                        "title": title,
                        "date": pub_date,
                        "source": "yahoo",
                        "url": url,
                        "is_major": False,
                        **classification,
                    })
        except Exception:
            pass

    # ── Filter to recent N days ──────────────────────────────────────
    cutoff = datetime.now() - timedelta(days=days)
    recent = []
    for h in headlines:
        date_str = h.get("date", "")
        try:
            if "T" in date_str:
                dt = datetime.fromisoformat(date_str.replace("Z", "+00:00").split("+")[0])
            else:
                # Try common formats
                for fmt in ["%a, %d %b %Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
                    try:
                        dt = datetime.strptime(date_str.split(" +")[0].split(" -")[0], fmt)
                        break
                    except ValueError:
                        continue
                else:
                    dt = datetime.now()  # Can't parse — include anyway
            if dt >= cutoff:
                recent.append(h)
        except Exception:
            recent.append(h)  # Can't parse date — include anyway

    # ── Build summary ────────────────────────────────────────────────
    bullish = sum(1 for h in recent if h["sentiment"] == "BULLISH")
    bearish = sum(1 for h in recent if h["sentiment"] == "BEARISH")
    neutral = sum(1 for h in recent if h["sentiment"] == "NEUTRAL")
    material = [h for h in recent if h["is_material"]]

    # Aggregate catalyst types
    all_catalysts = {}
    for h in recent:
        for cat in h.get("catalysts", []):
            all_catalysts[cat] = all_catalysts.get(cat, 0) + 1

    # Overall sentiment bias
    if bullish > bearish * 2:
        sentiment_bias = "BULLISH"
    elif bearish > bullish * 2:
        sentiment_bias = "BEARISH"
    elif bullish > bearish:
        sentiment_bias = "LEAN_BULLISH"
    elif bearish > bullish:
        sentiment_bias = "LEAN_BEARISH"
    else:
        sentiment_bias = "NEUTRAL"

    avg_score = (
        sum(h["sentiment_score"] for h in recent) / len(recent)
        if recent else 0.0
    )

    return {
        "ticker": ticker,
        "fetched_at": datetime.now().isoformat(),
        "source": source,
        "lookback_days": days,
        "headlines": recent,
        "summary": {
            "total": len(recent),
            "bullish": bullish,
            "bearish": bearish,
            "neutral": neutral,
            "material_count": len(material),
            "material_catalysts": all_catalysts,
            "sentiment_bias": sentiment_bias,
            "avg_sentiment_score": round(avg_score, 2),
        },
    }


# ── CLI ──────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Fetch news headlines for a ticker")
    parser.add_argument("ticker", help="Ticker symbol")
    parser.add_argument("--days", type=int, default=7, help="Lookback days (default: 7)")
    parser.add_argument("--limit", type=int, default=20, help="Max headlines (default: 20)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    result = fetch_news(args.ticker, days=args.days, limit=args.limit)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        s = result["summary"]
        print(f"NEWS: {result['ticker']} ({result['source']})")
        print(f"  Headlines: {s['total']} ({s['bullish']} bullish, {s['bearish']} bearish, {s['neutral']} neutral)")
        print(f"  Sentiment: {s['sentiment_bias']} (avg score: {s['avg_sentiment_score']:.2f})")
        if s['material_catalysts']:
            print(f"  Material Catalysts: {', '.join(f'{k}({v})' for k, v in s['material_catalysts'].items())}")
        print()
        for h in result["headlines"]:
            cat_str = f" [{', '.join(h['catalysts'])}]" if h['catalysts'] else ""
            major = " ⚡MAJOR" if h.get("is_major") else ""
            print(f"  {h['sentiment']:8s} {h['date'][:10]:10s} {h['title'][:80]}{cat_str}{major}")


if __name__ == "__main__":
    main()
