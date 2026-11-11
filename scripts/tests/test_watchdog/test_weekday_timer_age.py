"""Weekday-only timers read fresh across the weekend and Monday pre-run.

radon-cta-sync.timer (cta-sync, menthorq-cta) and radon-leap.timer
(leap-scan) fire Mon..Fri only. Their age is counted on US trading days, so
a Friday run stays fresh until Monday's run and a missed Monday reads stale
on Tuesday. Mirrors web/tests/service-health-weekday-timers.test.ts.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

CTA_SERVICES = ("cta-sync", "menthorq-cta")
CTA_FRI_LAST_RUN = "2026-11-13T21:30:00Z"
LEAP_FRI_RUN = "2026-11-13T15:00:00Z"


def _seed(db_conn, service: str, updated_at: str) -> None:
    db_conn.execute(
        """
        INSERT OR REPLACE INTO service_health
          (service, state, last_attempt_started_at, last_attempt_finished_at,
           last_error, updated_at)
        VALUES (?, 'ok', NULL, ?, ?, ?)
        """,
        (service, updated_at, json.dumps(None), updated_at),
    )
    db_conn.commit()


def _status(db_conn, service: str, updated_at: str, now_iso: str) -> str:
    from watchdog import check

    _seed(db_conn, service, updated_at)
    now = datetime.fromisoformat(now_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    outcome = check.check_service(
        service=service, kind="stale", now=now, market_state=check._market_state_for(now)
    )
    return outcome.status


@pytest.mark.parametrize("service", CTA_SERVICES)
@pytest.mark.parametrize(
    "now_iso",
    [
        "2026-11-14T17:00:00Z",  # Saturday
        "2026-11-16T03:00:00Z",  # Sunday night
        "2026-11-16T10:00:00Z",  # Monday 05:00 ET
        "2026-11-16T18:00:00Z",  # Monday 13:00 ET, before the 13:15 ET first run
    ],
)
def test_cta_rows_fresh_across_weekend_and_monday_pre_run(db_conn, service, now_iso):
    assert _status(db_conn, service, CTA_FRI_LAST_RUN, now_iso) == "healthy"


@pytest.mark.parametrize("service", CTA_SERVICES)
@pytest.mark.parametrize("now_iso", ["2026-11-17T10:00:00Z", "2026-11-17T17:00:00Z"])
def test_cta_rows_stale_tuesday_after_missed_monday(db_conn, service, now_iso):
    assert _status(db_conn, service, CTA_FRI_LAST_RUN, now_iso) == "stale"


@pytest.mark.parametrize("now_iso", ["2026-11-16T10:00:00Z", "2026-11-16T14:45:00Z"])
def test_leap_scan_fresh_monday_before_its_10am_run(db_conn, now_iso):
    assert _status(db_conn, "leap-scan", LEAP_FRI_RUN, now_iso) == "healthy"


@pytest.mark.parametrize("now_iso", ["2026-11-17T10:00:00Z", "2026-11-17T15:30:00Z"])
def test_leap_scan_stale_tuesday_after_missed_monday(db_conn, now_iso):
    assert _status(db_conn, "leap-scan", LEAP_FRI_RUN, now_iso) == "stale"


def test_monday_holiday_is_skipped_like_the_weekend(db_conn):
    assert _status(db_conn, "cta-sync", "2026-01-16T21:30:00Z", "2026-01-20T10:00:00Z") == "healthy"
    assert _status(db_conn, "cta-sync", "2026-01-16T21:30:00Z", "2026-01-21T10:00:00Z") == "stale"


def test_one_trading_day_of_silence_passes_the_window(db_conn):
    assert _status(db_conn, "cta-sync", "2026-11-17T21:30:00Z", "2026-11-18T22:00:00Z") == "healthy"
    assert _status(db_conn, "cta-sync", "2026-11-17T21:30:00Z", "2026-11-18T22:45:00Z") == "stale"


def test_monday_counts_from_et_midnight_across_spring_forward(db_conn):
    assert _status(db_conn, "cta-sync", "2026-03-06T21:30:00Z", "2026-03-09T21:00:00Z") == "healthy"
    assert _status(db_conn, "cta-sync", "2026-03-06T21:30:00Z", "2026-03-09T22:00:00Z") == "stale"


def test_trading_day_age_set_matches_typescript():
    """The TS isStale and the Python pager must age the same rows the same way."""
    import re
    from pathlib import Path

    from watchdog import services

    ts = (Path(__file__).resolve().parents[3] / "web" / "lib" / "serviceHealthWindows.ts").read_text()
    block = re.search(r"TRADING_DAY_AGE_SERVICES = new Set\(\[(.*?)\]\)", ts, re.DOTALL)
    assert block, "TRADING_DAY_AGE_SERVICES not found in serviceHealthWindows.ts"
    ts_names = set(re.findall(r'"([a-z0-9\-]+)"', block.group(1)))
    assert ts_names == set(services.TRADING_DAY_AGE_SERVICES)
    assert set(CTA_SERVICES) | {"leap-scan"} <= ts_names
