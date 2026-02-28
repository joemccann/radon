#!/usr/bin/env python3
"""Fetch dark pool / institutional flow data from Unusual Whales API.

Requires UW_TOKEN environment variable (Unusual Whales API key).
Set it via: export UW_TOKEN="your-api-key"
"""
import argparse, json, os, sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

BASE_URL = "https://api.unusualwhales.com/api"


def _get_token() -> str:
    token = os.environ.get("UW_TOKEN")
    if not token:
        print(json.dumps({"error": "UW_TOKEN environment variable not set"}), file=sys.stderr)
        sys.exit(1)
    return token


def _api_get(path: str, params: Optional[Dict] = None) -> Dict:
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


def fetch_darkpool(ticker: str, date: Optional[str] = None) -> List[Dict]:
    """Fetch dark pool trade prints for a ticker.

    Returns list of individual dark pool transactions with price, size,
    NBBO context, and premium.
    """
    params = {}
    if date:
        params["date"] = date
    resp = _api_get(f"/darkpool/{ticker}", params)
    return resp.get("data", [])


def fetch_flow_alerts(ticker: str, min_premium: int = 50000) -> List[Dict]:
    """Fetch options flow alerts for a ticker.

    Filters for larger trades (default $50k+ premium) that are more likely
    to represent institutional activity.
    """
    params = {
        "ticker_symbol": ticker,
        "min_premium": str(min_premium),
        "limit": "100",
    }
    resp = _api_get("/option-trades/flow-alerts", params)
    return resp.get("data", [])


def analyze_darkpool(trades: List[Dict]) -> Dict:
    """Derive flow signals from raw dark pool prints.

    Compares trade prices to NBBO midpoint to estimate buy/sell pressure.
    Trades above mid → likely buys. Trades below mid → likely sells.
    """
    if not trades:
        return {
            "total_volume": 0,
            "total_premium": 0,
            "buy_volume": 0,
            "sell_volume": 0,
            "dp_buy_ratio": None,
            "flow_direction": "NO_DATA",
            "flow_strength": 0,
            "num_prints": 0,
        }

    total_volume = 0
    total_premium = 0.0
    buy_volume = 0
    sell_volume = 0
    neutral_volume = 0

    for t in trades:
        if t.get("canceled"):
            continue
        size = int(t.get("size", 0))
        price = float(t.get("price", 0))
        premium = float(t.get("premium", 0))
        nbbo_bid = float(t.get("nbbo_bid", 0))
        nbbo_ask = float(t.get("nbbo_ask", 0))

        total_volume += size
        total_premium += premium

        if nbbo_bid > 0 and nbbo_ask > 0:
            mid = (nbbo_bid + nbbo_ask) / 2
            if price >= mid:
                buy_volume += size
            else:
                sell_volume += size
        else:
            neutral_volume += size

    classified = buy_volume + sell_volume
    buy_ratio = round(buy_volume / classified, 4) if classified > 0 else None

    # Flow direction: >55% buy = ACCUMULATION, <45% buy = DISTRIBUTION
    if buy_ratio is None:
        direction = "UNKNOWN"
        strength = 0
    elif buy_ratio >= 0.55:
        direction = "ACCUMULATION"
        strength = round((buy_ratio - 0.5) * 200, 1)  # 0-100 scale
    elif buy_ratio <= 0.45:
        direction = "DISTRIBUTION"
        strength = round((0.5 - buy_ratio) * 200, 1)
    else:
        direction = "NEUTRAL"
        strength = 0

    return {
        "total_volume": total_volume,
        "total_premium": round(total_premium, 2),
        "buy_volume": buy_volume,
        "sell_volume": sell_volume,
        "dp_buy_ratio": buy_ratio,
        "flow_direction": direction,
        "flow_strength": strength,
        "num_prints": len([t for t in trades if not t.get("canceled")]),
    }


def analyze_options_flow(alerts: List[Dict]) -> Dict:
    """Summarize options flow alerts for directional bias."""
    if not alerts:
        return {
            "total_alerts": 0,
            "total_premium": 0,
            "call_premium": 0,
            "put_premium": 0,
            "call_put_ratio": None,
            "bias": "NO_DATA",
        }

    call_premium = 0.0
    put_premium = 0.0

    for a in alerts:
        prem = float(a.get("premium", 0))
        if a.get("is_call"):
            call_premium += prem
        else:
            put_premium += prem

    total = call_premium + put_premium
    cp_ratio = round(call_premium / put_premium, 2) if put_premium > 0 else None

    if cp_ratio is None:
        bias = "ALL_CALLS" if call_premium > 0 else "NO_DATA"
    elif cp_ratio >= 2.0:
        bias = "STRONGLY_BULLISH"
    elif cp_ratio >= 1.2:
        bias = "BULLISH"
    elif cp_ratio <= 0.5:
        bias = "STRONGLY_BEARISH"
    elif cp_ratio <= 0.8:
        bias = "BEARISH"
    else:
        bias = "NEUTRAL"

    return {
        "total_alerts": len(alerts),
        "total_premium": round(total, 2),
        "call_premium": round(call_premium, 2),
        "put_premium": round(put_premium, 2),
        "call_put_ratio": cp_ratio,
        "bias": bias,
    }


def fetch_flow(ticker: str, lookback_days: int = 5) -> Dict:
    """Full flow analysis: dark pool prints + options flow alerts.

    Fetches dark pool data for each of the last N days and aggregates,
    plus recent options flow alerts.
    """
    ticker = ticker.upper()

    # Fetch dark pool data for recent days
    all_dp_trades = []
    daily_signals = []
    today = datetime.now()

    for i in range(lookback_days):
        date = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        trades = fetch_darkpool(ticker, date)
        if isinstance(trades, list):
            day_analysis = analyze_darkpool(trades)
            day_analysis["date"] = date
            daily_signals.append(day_analysis)
            all_dp_trades.extend(trades)

    # Aggregate dark pool analysis
    aggregate_dp = analyze_darkpool(all_dp_trades)

    # Fetch options flow
    flow_alerts = fetch_flow_alerts(ticker)
    options_summary = analyze_options_flow(flow_alerts if isinstance(flow_alerts, list) else [])

    # Combined signal
    dp_dir = aggregate_dp["flow_direction"]
    opt_bias = options_summary["bias"]

    if dp_dir == "ACCUMULATION" and opt_bias in ("BULLISH", "STRONGLY_BULLISH"):
        combined = "STRONG_BULLISH_CONFLUENCE"
    elif dp_dir == "DISTRIBUTION" and opt_bias in ("BEARISH", "STRONGLY_BEARISH"):
        combined = "STRONG_BEARISH_CONFLUENCE"
    elif dp_dir in ("ACCUMULATION", "DISTRIBUTION"):
        combined = f"DP_{dp_dir}_ONLY"
    elif opt_bias not in ("NEUTRAL", "NO_DATA"):
        combined = f"OPTIONS_{opt_bias}_ONLY"
    else:
        combined = "NO_SIGNAL"

    return {
        "ticker": ticker,
        "fetched_at": today.isoformat(),
        "lookback_days": lookback_days,
        "dark_pool": {
            "aggregate": aggregate_dp,
            "daily": daily_signals,
        },
        "options_flow": options_summary,
        "combined_signal": combined,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Fetch dark pool + options flow from Unusual Whales")
    p.add_argument("ticker", help="Stock ticker")
    p.add_argument("--days", type=int, default=5, help="Lookback days for dark pool data (default 5)")
    p.add_argument("--min-premium", type=int, default=50000,
                   help="Min premium filter for options flow alerts (default $50k)")
    args = p.parse_args()

    result = fetch_flow(args.ticker, args.days)
    print(json.dumps(result, indent=2))
