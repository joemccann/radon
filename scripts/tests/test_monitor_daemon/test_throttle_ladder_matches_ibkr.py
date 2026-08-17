"""The backoff ladder must model IBKR's actual rate limit.

IBKR's published limit, from the 1018 row of their error table
(https://www.ibkrguides.com/clientportal/performanceandstatements/flex3error.htm):

    "Too many requests have been made from this token. Please try again
     shortly.  Limited to one request per second, 10 requests per minute
     (per token)."

That is a ONE MINUTE window. IBKR documents no daily, multi-day or weekly
cooldown anywhere.

The ladder was 24h -> 48h -> 72h -> 168h: a model of a constraint roughly three
orders of magnitude harsher than the one that exists. Combined with the
classifier bug that treated 1001 and 1019 as throttles (fixed in 436dcdc1), a
statement that was seconds from ready could put the sync to sleep for a day, and
a few transient failures could walk it to a week.

The corrected ladder still escalates -- a second 1018 sixty seconds after the
first means something is making sustained requests, and this repo has a
known uncontrolled consumer in portfolio_performance.py -- but it escalates in
minutes, and it is bounded by an hour rather than a week.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from monitor_daemon.handlers import _throttle_backoff as tb  # noqa: E402

NOW = datetime(2026, 8, 17, 21, 0, 0, tzinfo=timezone.utc)

# IBKR's documented window, in seconds.
IBKR_WINDOW_SECS = 60


def test_the_first_backoff_is_on_the_order_of_ibkrs_window():
    """A 1018 clears in a minute. The first wait must be minutes, not a day."""
    first = tb.THROTTLE_EMBARGO_SECS[0]
    assert first >= IBKR_WINDOW_SECS, "must wait at least the documented window"
    assert first <= 5 * 60, f"first backoff {first}s models a limit IBKR does not impose"


def test_the_cap_is_an_hour_not_a_week():
    cap = tb.THROTTLE_EMBARGO_SECS[-1]
    assert cap <= 60 * 60, f"cap {cap}s is longer than any documented IBKR cooldown"
    # 168h was the old cap. It must be gone.
    assert cap != 168 * 60 * 60


def test_the_ladder_still_escalates():
    """Sustained 1018s mean a real consumer problem, so keep escalating -- just
    in minutes rather than days."""
    secs = tb.THROTTLE_EMBARGO_SECS
    assert len(secs) >= 3
    assert list(secs) == sorted(secs), "the ladder must be monotonically increasing"
    assert secs[0] < secs[-1]


def test_a_full_ladder_walk_costs_under_two_hours():
    """The whole point. Walking every rung used to cost 24+48+72+168 = 312 HOURS
    before the cap even engaged."""
    total = sum(tb.THROTTLE_EMBARGO_SECS)
    assert total <= 2 * 60 * 60, f"a full walk is {total / 3600:.1f}h"
    # The superseded ladder, pinned so it cannot come back.
    assert total != (24 + 48 + 72 + 168) * 3600


def test_recording_a_throttle_sets_a_minutes_scale_embargo():
    state = tb.record_throttle({}, now_utc=NOW)
    blocked = datetime.fromisoformat(state["blocked_until"])
    waited = (blocked - NOW).total_seconds()
    assert IBKR_WINDOW_SECS <= waited <= 5 * 60


def test_a_success_still_clears_the_counter():
    state = tb.record_throttle({}, now_utc=NOW)
    state = tb.record_throttle(state, now_utc=NOW)
    assert state["throttle_count"] == 2
    cleared = tb.record_success(state)
    assert cleared.get("throttle_count", 0) == 0
    assert not cleared.get("blocked_until")
