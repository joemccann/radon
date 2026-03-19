#!/usr/bin/env python3
"""Fetch dark pool / institutional flow data from Unusual Whales API.

Requires UW_TOKEN environment variable (Unusual Whales API key).
Set it via: export UW_TOKEN="your-api-key"

API Reference: docs/unusual_whales_api.md
Full Spec: docs/unusual_whales_api_spec.yaml

Key endpoints used:
  - GET /api/darkpool/{ticker} - Dark pool trades for a ticker
  - GET /api/option-trades/flow-alerts - Options flow alerts

Intraday Interpolation:
  When evaluating during market hours, today's partial data is interpolated
  to estimate full-day values based on:
  - Time elapsed as % of trading day (6.5 hours)
  - Volume comparison to prior days' averages
  This prevents false "fading" signals from incomplete intraday data.
"""
import argparse, json, sys
from datetime import datetime, time
from typing import Dict, List, Optional, Tuple
import pytz

from clients.uw_client import UWClient, UWAPIError
from utils.market_calendar import (
    get_last_n_trading_days,
    load_holidays,
    _is_trading_day,
)

# Trading day constants
MARKET_OPEN = time(9, 30)  # 9:30 AM ET
MARKET_CLOSE = time(16, 0)  # 4:00 PM ET
TRADING_DAY_MINUTES = 390  # 6.5 hours = 390 minutes
ET = pytz.timezone('America/New_York')

# Keep for backward compatibility with existing tests
MARKET_HOLIDAYS_2026 = load_holidays(2026)


def get_trading_day_progress() -> Tuple[float, bool, str]:
    """Calculate how far through the current trading day we are.
    
    Returns:
        Tuple of (progress_pct, is_market_hours, status_msg)
        - progress_pct: 0.0 to 1.0 (0% to 100% of trading day elapsed)
        - is_market_hours: True if currently during market hours
        - status_msg: Human-readable status
    """
    now_et = datetime.now(ET)
    current_time = now_et.time()
    
    # Check if it's a trading day
    if not _is_trading_day(now_et):
        return 1.0, False, "Market closed (weekend/holiday)"
    
    # Before market open
    if current_time < MARKET_OPEN:
        return 0.0, False, "Pre-market (before 9:30 AM ET)"
    
    # After market close
    if current_time >= MARKET_CLOSE:
        return 1.0, False, "After hours (market closed)"
    
    # During market hours - calculate progress
    market_open_dt = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    elapsed = now_et - market_open_dt
    elapsed_minutes = elapsed.total_seconds() / 60
    progress = min(elapsed_minutes / TRADING_DAY_MINUTES, 1.0)
    
    hours_elapsed = elapsed_minutes / 60
    status = f"Market open ({hours_elapsed:.1f}h elapsed, {progress*100:.0f}% of day)"
    
    return progress, True, status


def interpolate_intraday_flow(
    today_data: Dict,
    prior_days: List[Dict],
    trading_day_progress: float
) -> Dict:
    """Interpolate today's partial data to estimate full-day values.
    
    Uses volume-weighted interpolation based on:
    1. Time elapsed as % of trading day
    2. Today's volume vs prior days' average volume
    
    Args:
        today_data: Today's dark pool analysis (partial if intraday)
        prior_days: List of prior days' full-day analyses
        trading_day_progress: 0.0-1.0 representing % of trading day elapsed
        
    Returns:
        Dict with interpolated values and confidence metrics
    """
    if trading_day_progress >= 1.0:
        # Full day data, no interpolation needed
        return {
            "is_interpolated": False,
            "actual": today_data,
            "interpolated": today_data,
            "confidence": "HIGH",
            "trading_day_progress": 1.0,
            "notes": "Full trading day data"
        }
    
    if trading_day_progress <= 0.0 or not prior_days:
        # No data or no prior days to compare
        return {
            "is_interpolated": False,
            "actual": today_data,
            "interpolated": today_data,
            "confidence": "LOW",
            "trading_day_progress": trading_day_progress,
            "notes": "Insufficient data for interpolation"
        }
    
    # Calculate prior days' average full-day volume
    prior_volumes = [d.get("total_volume", 0) for d in prior_days if d.get("total_volume", 0) > 0]
    avg_prior_volume = sum(prior_volumes) / len(prior_volumes) if prior_volumes else 0
    
    today_volume = today_data.get("total_volume", 0)
    today_buy_volume = today_data.get("buy_volume", 0)
    today_sell_volume = today_data.get("sell_volume", 0)
    
    # Project today's volume to full day based on time elapsed
    if trading_day_progress > 0:
        projected_volume = today_volume / trading_day_progress
        projected_buy = today_buy_volume / trading_day_progress
        projected_sell = today_sell_volume / trading_day_progress
    else:
        projected_volume = avg_prior_volume
        projected_buy = projected_volume * 0.5
        projected_sell = projected_volume * 0.5
    
    # Calculate interpolated buy ratio
    projected_classified = projected_buy + projected_sell
    interpolated_buy_ratio = projected_buy / projected_classified if projected_classified > 0 else None
    
    # Blend with prior days' pattern (weight recent days more heavily if today's data is sparse)
    # More weight to actual data as the day progresses
    actual_weight = trading_day_progress
    prior_weight = 1 - trading_day_progress
    
    # Calculate prior days' average buy ratio (excluding neutral days)
    prior_ratios = [d.get("dp_buy_ratio") for d in prior_days if d.get("dp_buy_ratio") is not None]
    avg_prior_ratio = sum(prior_ratios) / len(prior_ratios) if prior_ratios else 0.5
    
    # Blended estimate: weighted average of today's projected ratio and prior pattern
    if interpolated_buy_ratio is not None:
        blended_ratio = (interpolated_buy_ratio * actual_weight) + (avg_prior_ratio * prior_weight)
    else:
        blended_ratio = avg_prior_ratio
    
    # Determine direction and strength from blended ratio
    if blended_ratio >= 0.55:
        direction = "ACCUMULATION"
        strength = round((blended_ratio - 0.5) * 200, 1)
    elif blended_ratio <= 0.45:
        direction = "DISTRIBUTION"
        strength = round((0.5 - blended_ratio) * 200, 1)
    else:
        direction = "NEUTRAL"
        strength = 0
    
    # Confidence based on how much of the day we have
    if trading_day_progress >= 0.75:
        confidence = "HIGH"
    elif trading_day_progress >= 0.50:
        confidence = "MEDIUM"
    elif trading_day_progress >= 0.25:
        confidence = "LOW"
    else:
        confidence = "VERY_LOW"
    
    # Volume pace comparison
    expected_volume_at_this_point = avg_prior_volume * trading_day_progress
    volume_pace = today_volume / expected_volume_at_this_point if expected_volume_at_this_point > 0 else 1.0
    
    interpolated = {
        "total_volume": round(projected_volume),
        "total_premium": round(today_data.get("total_premium", 0) / trading_day_progress) if trading_day_progress > 0 else 0,
        "buy_volume": round(projected_buy),
        "sell_volume": round(projected_sell),
        "dp_buy_ratio": round(blended_ratio, 4),
        "flow_direction": direction,
        "flow_strength": strength,
        "num_prints": today_data.get("num_prints", 0),
    }
    
    return {
        "is_interpolated": True,
        "actual": today_data,
        "interpolated": interpolated,
        "confidence": confidence,
        "trading_day_progress": round(trading_day_progress, 3),
        "trading_day_pct": f"{trading_day_progress * 100:.1f}%",
        "volume_pace": round(volume_pace, 2),
        "volume_pace_note": f"{'Above' if volume_pace > 1.1 else 'Below' if volume_pace < 0.9 else 'At'} average pace",
        "avg_prior_volume": round(avg_prior_volume),
        "avg_prior_buy_ratio": round(avg_prior_ratio, 4) if prior_ratios else None,
        "blending_weights": {
            "actual_weight": round(actual_weight, 2),
            "prior_weight": round(prior_weight, 2)
        },
        "notes": f"Interpolated from {trading_day_progress*100:.0f}% of trading day. Confidence: {confidence}."
    }


def is_market_open(date: datetime) -> bool:
    """Check if the market is open on a given date (date-only, no time check).

    Backward-compatible wrapper used by existing tests.
    """
    return _is_trading_day(date)


def fetch_darkpool(ticker: str, date: Optional[str] = None, _client: Optional[UWClient] = None) -> List[Dict]:
    """Fetch dark pool trade prints for a ticker.

    Returns list of individual dark pool transactions with price, size,
    NBBO context, and premium.
    """
    def _fetch(client):
        try:
            resp = client.get_darkpool_flow(ticker, date=date)
            return resp.get("data", [])
        except UWAPIError:
            return []

    if _client is not None:
        return _fetch(_client)
    with UWClient() as client:
        return _fetch(client)


def fetch_flow_alerts(
    ticker: str, min_premium: int = 50000, _client: Optional[UWClient] = None
) -> List[Dict]:
    """Fetch options flow alerts for a ticker.

    Filters for larger trades (default $50k+ premium) that are more likely
    to represent institutional activity.
    """
    def _fetch(client):
        try:
            resp = client.get_flow_alerts(ticker=ticker, min_premium=min_premium, limit=100)
            return resp.get("data", [])
        except UWAPIError:
            return []

    if _client is not None:
        return _fetch(_client)
    with UWClient() as client:
        return _fetch(client)


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


def fetch_flow(ticker: str, lookback_days: int = 5, _client: Optional[UWClient] = None, 
               skip_options_flow: bool = False) -> Dict:
    """Full flow analysis: dark pool prints + options flow alerts.

    Fetches dark pool data for each of the last N TRADING days and aggregates,
    plus recent options flow alerts.

    IMPORTANT: Always includes today's date (if it's a trading day) even during
    market hours. ``get_last_n_trading_days`` skips today before 4 PM ET, but
    for evaluations we need today's intraday flow to detect fading signals.
    
    INTRADAY INTERPOLATION: When run during market hours, today's partial data
    is interpolated to estimate full-day values. Both actual and interpolated
    values are returned for transparency.
    
    Args:
        ticker: Stock symbol
        lookback_days: Number of trading days to fetch (default 5)
        _client: Optional UWClient to reuse (avoids connection overhead)
        skip_options_flow: If True, skip flow_alerts API call (saves 1 call/ticker)
    """
    ticker = ticker.upper()

    # Fetch dark pool data for recent TRADING days (skip weekends/holidays)
    all_dp_trades = []
    daily_signals = []
    today = datetime.now()

    trading_days = get_last_n_trading_days(lookback_days, today)

    # Always include today if it's a trading day and not already in the list
    today_str = today.strftime("%Y-%m-%d")
    if _is_trading_day(today) and today_str not in trading_days:
        trading_days.insert(0, today_str)

    # Get trading day progress for interpolation
    trading_day_progress, is_market_hours, market_status = get_trading_day_progress()

    def _do_fetch(client):
        nonlocal all_dp_trades, daily_signals
        for date in trading_days:
            trades = fetch_darkpool(ticker, date, _client=client)
            if isinstance(trades, list):
                day_analysis = analyze_darkpool(trades)
                day_analysis["date"] = date
                daily_signals.append(day_analysis)
                all_dp_trades.extend(trades)
        # Skip flow_alerts if not needed (saves API call for scanning)
        if skip_options_flow:
            return []
        return fetch_flow_alerts(ticker, _client=client)

    # Use provided client or create new one
    if _client is not None:
        flow_alerts = _do_fetch(_client)
    else:
        with UWClient() as client:
            flow_alerts = _do_fetch(client)

    # Aggregate dark pool analysis
    aggregate_dp = analyze_darkpool(all_dp_trades)
    options_summary = analyze_options_flow(flow_alerts if isinstance(flow_alerts, list) else [])

    # Interpolate today's data if we're in market hours with partial data
    today_interpolation = None
    if daily_signals and daily_signals[0].get("date") == today_str:
        today_data = daily_signals[0]
        prior_days = daily_signals[1:] if len(daily_signals) > 1 else []
        today_interpolation = interpolate_intraday_flow(
            today_data, prior_days, trading_day_progress
        )
        
        # Update today's entry with interpolated flag
        daily_signals[0]["is_partial"] = trading_day_progress < 1.0
        daily_signals[0]["trading_day_progress"] = trading_day_progress

    # Calculate aggregate with interpolated today if applicable
    aggregate_interpolated = None
    if today_interpolation and today_interpolation.get("is_interpolated"):
        # Recalculate aggregate using interpolated today values
        interpolated_today = today_interpolation["interpolated"]
        
        # Sum up prior days + interpolated today
        total_volume = interpolated_today["total_volume"]
        total_premium = interpolated_today["total_premium"]
        buy_volume = interpolated_today["buy_volume"]
        sell_volume = interpolated_today["sell_volume"]
        
        for day in daily_signals[1:]:
            total_volume += day.get("total_volume", 0)
            total_premium += day.get("total_premium", 0)
            buy_volume += day.get("buy_volume", 0)
            sell_volume += day.get("sell_volume", 0)
        
        classified = buy_volume + sell_volume
        interp_buy_ratio = round(buy_volume / classified, 4) if classified > 0 else None
        
        if interp_buy_ratio is None:
            interp_direction = "UNKNOWN"
            interp_strength = 0
        elif interp_buy_ratio >= 0.55:
            interp_direction = "ACCUMULATION"
            interp_strength = round((interp_buy_ratio - 0.5) * 200, 1)
        elif interp_buy_ratio <= 0.45:
            interp_direction = "DISTRIBUTION"
            interp_strength = round((0.5 - interp_buy_ratio) * 200, 1)
        else:
            interp_direction = "NEUTRAL"
            interp_strength = 0
        
        aggregate_interpolated = {
            "total_volume": total_volume,
            "total_premium": round(total_premium, 2),
            "buy_volume": buy_volume,
            "sell_volume": sell_volume,
            "dp_buy_ratio": interp_buy_ratio,
            "flow_direction": interp_direction,
            "flow_strength": interp_strength,
            "num_prints": sum(d.get("num_prints", 0) for d in daily_signals),
        }

    # Combined signal (use interpolated if available)
    effective_aggregate = aggregate_interpolated if aggregate_interpolated else aggregate_dp
    dp_dir = effective_aggregate["flow_direction"]
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

    result = {
        "ticker": ticker,
        "fetched_at": today.isoformat(),
        "lookback_trading_days": lookback_days,
        "trading_days_checked": trading_days,
        "market_status": market_status,
        "trading_day_progress": trading_day_progress,
        "dark_pool": {
            "aggregate_actual": aggregate_dp,
            "aggregate": effective_aggregate,  # Use interpolated if available
            "daily": daily_signals,
        },
        "options_flow": options_summary,
        "combined_signal": combined,
    }
    
    # Add interpolation details if applicable
    if today_interpolation:
        result["intraday_interpolation"] = today_interpolation
        if aggregate_interpolated:
            result["dark_pool"]["aggregate_interpolated"] = aggregate_interpolated
    
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Fetch dark pool + options flow from Unusual Whales")
    p.add_argument("ticker", help="Stock ticker")
    p.add_argument("--days", type=int, default=5, help="Lookback trading days for dark pool data (default 5)")
    p.add_argument("--min-premium", type=int, default=50000,
                   help="Min premium filter for options flow alerts (default $50k)")
    args = p.parse_args()

    result = fetch_flow(args.ticker, args.days)
    print(json.dumps(result, indent=2))
