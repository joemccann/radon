"""gex-scan stale page eeef09595a01893b4e31abd5cbda352e (2026-10-09 18:40Z).

gex-scan's only writer is the 15-minute radon-refresh driver, and it runs
last, after cri and vcg. R-422 copied vcg-scan's 15-minute open window.
vcg has its own 5-minute timer, so that window is three missed cycles.
On gex it is zero missed cycles: one deploy stop-clean (SIGTERM one
second into gex, green marker 73s later) leaves the 18:16:14 heartbeat
in place, and the 18:40 intraday check pages "silent for 23m (window
15m) market open".

The driver row data-refresh already uses 35 minutes, which tolerates
two missed 15-minute fires. A silence of one missed fire must stay
healthy. Two missed fires (past that window) must still page P1.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

# Last gex_scan.py complete line before the killed 18:30 cycle.
LAST_OK = datetime(2026, 10, 9, 18, 16, 14, tzinfo=timezone.utc)
# Watchdog intraday ticks that bookend the page. 18:35 is the first
# check past the 15-minute window; 18:40 is the hysteresis fire.
FIRST_CHECK = datetime(2026, 10, 9, 18, 35, 0, tzinfo=timezone.utc)
PAGE_AT = datetime(2026, 10, 9, 18, 40, 0, tzinfo=timezone.utc)
# Two missed fires (18:30 and 18:45) and the following watchdog tick
# after the 35-minute driver budget. 18:55 records, 19:00 fires.
PAST_WINDOW = datetime(2026, 10, 9, 18, 55, 0, tzinfo=timezone.utc)
PAST_WINDOW_FIRE = datetime(2026, 10, 9, 19, 0, 0, tzinfo=timezone.utc)


def _seed(db_conn, updated_at: datetime) -> None:
    db_conn.execute(
        """
        INSERT OR REPLACE INTO service_health
          (service, state, last_attempt_started_at, last_attempt_finished_at,
           last_error, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "gex-scan",
            "ok",
            None,
            updated_at.isoformat().replace("+00:00", "Z"),
            None,
            updated_at.isoformat().replace("+00:00", "Z"),
        ),
    )
    db_conn.commit()


def test_one_missed_refresh_cycle_does_not_page(db_conn):
    """23m after a successful scan, with the next cycle killed by deploy.

    Age at 18:40 is 23m46s. That is one 15-minute fire, not an outage.
    """
    from watchdog import check

    _seed(db_conn, LAST_OK)
    first = check.check_service(
        service="gex-scan", kind="stale", now=FIRST_CHECK, market_state="open",
    )
    page = check.check_service(
        service="gex-scan", kind="stale", now=PAGE_AT, market_state="open",
    )
    assert first.status == "healthy", first.message
    assert first.fired is False
    assert page.status == "healthy", page.message
    assert page.fired is False
    assert (PAGE_AT - LAST_OK) == timedelta(minutes=23, seconds=46)


def test_two_missed_refresh_cycles_still_page_p1(db_conn):
    """Past the 35-minute driver budget, silence is a real outage."""
    from watchdog import check

    _seed(db_conn, LAST_OK)
    first = check.check_service(
        service="gex-scan", kind="stale", now=PAST_WINDOW, market_state="open",
    )
    fired = check.check_service(
        service="gex-scan", kind="stale", now=PAST_WINDOW_FIRE, market_state="open",
    )
    assert first.status == "stale"
    assert first.fired is False
    assert fired.status == "stale"
    assert fired.fired is True
    assert fired.severity == "P1"
    assert "silent for" in fired.message
    assert "market open" in fired.message


def test_gex_open_window_matches_the_15_minute_driver_not_vcg():
    """vcg's 15-minute window assumes a 5-minute timer. gex has none."""
    from watchdog.services import SCHEDULED_SERVICES

    gex = SCHEDULED_SERVICES["gex-scan"]["open"]
    driver = SCHEDULED_SERVICES["data-refresh"]["open"]
    vcg = SCHEDULED_SERVICES["vcg-scan"]["open"]
    assert gex == driver
    assert driver == 35 * 60
    assert gex > vcg
    # One missed 15-minute fire plus the observed ~24s of this page's age
    # must sit inside the window. Two missed fires (30 min) plus the
    # 5-minute watchdog tick must sit outside it.
    assert 23 * 60 + 46 < gex
    assert 38 * 60 + 46 > gex
