#!/usr/bin/env python3
"""Report-facing market status for the free-trade and portfolio reports.

Session open/closed is ``utils.market_calendar.is_market_open_et``. This
module only maps that state onto the labels and last-close timestamp the
reports print.

Usage:
    from utils.market_hours import get_market_status, get_last_market_close
"""

from datetime import datetime, time
from typing import Tuple
from zoneinfo import ZoneInfo

from utils.market_calendar import last_completed_session_date, market_state

EASTERN = ZoneInfo("America/New_York")
MARKET_CLOSE = time(16, 0)

_CLOSED_LABELS = {
    "weekend": "CLOSED (weekend)",
    "static:holiday": "CLOSED (holiday)",
    "ibkr:closed": "CLOSED (holiday)",
}


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
