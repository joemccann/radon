#!/usr/bin/env python3
"""Market-hours adapter for the free-trade and portfolio reports.

Thin delegations to ``utils.market_calendar``, the one holiday-aware source.

Usage:
    from utils.market_hours import is_market_open, get_market_status
"""

from datetime import datetime, time
from typing import Tuple
from zoneinfo import ZoneInfo

from utils.market_calendar import is_market_open_et, last_completed_session_date, market_state

EASTERN = ZoneInfo("America/New_York")
MARKET_CLOSE = time(16, 0)

_CLOSED_LABELS = {
    "weekend": "CLOSED (weekend)",
    "static:holiday": "CLOSED (holiday)",
    "ibkr:closed": "CLOSED (holiday)",
}


def is_market_open(dt: datetime = None) -> bool:
    return is_market_open_et(dt)


def get_market_status(dt: datetime = None) -> Tuple[bool, str]:
    state = market_state(dt)
    if state["is_open"]:
        return True, "OPEN"
    return False, _CLOSED_LABELS.get(state["reason"], "CLOSED")


def get_last_market_close(dt: datetime = None) -> datetime:
    session = datetime.strptime(last_completed_session_date(dt), "%Y-%m-%d").date()
    return datetime.combine(session, MARKET_CLOSE, tzinfo=EASTERN)


if __name__ == "__main__":
    is_open, status = get_market_status()
    print(f"Market status: {status}")
    print(f"Last market close: {get_last_market_close().strftime('%Y-%m-%d %H:%M %Z')}")
