#!/usr/bin/env python3
"""Analyst ratings CLI with the Robinhood consensus rung: IB -> Robinhood -> UW.

Robinhood's read-only get_equity_analyst_ratings serves buy/hold/sell counts
and targets, sparing a UW call. It carries no per-firm rating-change history,
so --changes-only still goes straight to UW.

This lives apart from fetch_analyst_ratings.py on purpose: evaluate.py (a
Four Gates file) imports that module, and nothing in a gate file's import
closure may reference Robinhood (tests/test_rh_crowding.py).

Usage: same flags as fetch_analyst_ratings.py.
    python3 scripts/fetch_analyst_ratings_rh.py META --json
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import fetch_analyst_ratings


def _to_float(value) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def fetch_from_rh(ticker: str) -> Optional[dict]:
    """Consensus from Robinhood (read-only MCP). None on miss/unconfigured.

    Carries no upgrade/downgrade history: callers that need it go to UW.
    """
    try:
        from clients.robinhood_client import fetch_robinhood_analyst_ratings
    except ImportError:
        return None
    raw = fetch_robinhood_analyst_ratings(ticker)
    if not raw:
        return None
    try:
        buy = int(raw.get("num_buy_ratings") or 0)
        hold = int(raw.get("num_hold_ratings") or 0)
        sell = int(raw.get("num_sell_ratings") or 0)
    except (TypeError, ValueError):
        return None
    total = buy + hold + sell
    if total <= 0:
        return None
    buy_pct = round(buy / total * 100, 1)
    target = None
    mean = _to_float(raw.get("mean_price_target"))
    if mean is not None:
        high = _to_float(raw.get("high_price_target"))
        low = _to_float(raw.get("low_price_target"))
        target = {
            "mean": round(mean, 2),
            "high": round(high, 2) if high is not None else None,
            "low": round(low, 2) if low is not None else None,
            "median": None,
            "count": total,
        }
    return {
        "ticker": ticker.upper(),
        "fetched_at": datetime.now().isoformat(),
        "source": "rh",
        "ratings": {
            "strong_buy": 0,
            "buy": buy,
            "hold": hold,
            "sell": sell,
            "strong_sell": 0,
            "total": total,
            "buy_pct": buy_pct,
            "sell_pct": round(sell / total * 100, 1),
        },
        "recommendation": "buy" if buy_pct >= 70 else "hold",
        "target_price": target,
        "analyst_count": total,
        "recent_changes": [],
        "upgrade_downgrade_history": [],
        "error": None,
        "from_cache": False,
    }


if __name__ == "__main__":
    fetch_analyst_ratings.main(consensus_rung=fetch_from_rh)
