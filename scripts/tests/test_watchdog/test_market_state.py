"""Watchdog market-state derivation must treat US holidays as 'closed'.

A weekday holiday classified as 'open' gives market-hours-only writers the tight
open-state freshness window while they're legitimately dormant, flags them stale,
and escalates them to P1 EMERGENCY Pushover pages (the 2026-06-19 Juneteenth
false-page incident).
"""
from __future__ import annotations

from datetime import datetime, timezone

from watchdog import check


def test_juneteenth_weekday_is_closed_not_open():
    # 2026-06-19 is Juneteenth (a Friday) — in scripts/config/market_holidays.json.
    # 13:00 ET == 17:00 UTC (EDT). Pre-fix this returned "open".
    juneteenth_1pm_et = datetime(2026, 6, 19, 17, 0, tzinfo=timezone.utc)
    assert check._market_state_for(juneteenth_1pm_et) == "closed"


def test_ordinary_weekday_rth_still_open():
    # 2026-06-18 is a normal Thursday. 13:00 ET == 17:00 UTC.
    thursday_1pm_et = datetime(2026, 6, 18, 17, 0, tzinfo=timezone.utc)
    assert check._market_state_for(thursday_1pm_et) == "open"


def test_weekend_still_closed():
    # 2026-06-20 is a Saturday.
    saturday = datetime(2026, 6, 20, 17, 0, tzinfo=timezone.utc)
    assert check._market_state_for(saturday) == "closed"
