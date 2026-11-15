"""Digest line 2026-11-14: "[P3] discover — silent for 24d (window 4d) — market closed".

discover's window is 4d open and closed, so the market state changed
nothing about the verdict, yet the suffix read as an excuse for a 24-day
outage. The label belongs only on services whose window depends on it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

NOW = datetime(2026, 11, 14, 15, 0, 0, tzinfo=timezone.utc)  # Saturday
LAST_OK = datetime(2026, 10, 21, 16, 0, 54, tzinfo=timezone.utc)


def _seed(db_conn, service: str, updated_at: datetime) -> None:
    stamp = updated_at.isoformat().replace("+00:00", "Z")
    db_conn.execute(
        """
        INSERT OR REPLACE INTO service_health
          (service, state, last_attempt_started_at, last_attempt_finished_at,
           last_error, updated_at)
        VALUES (?, 'ok', NULL, ?, NULL, ?)
        """,
        (service, stamp, stamp),
    )
    db_conn.commit()


def test_market_independent_window_omits_the_market_label(db_conn):
    from watchdog import check
    from watchdog.services import SCHEDULED_SERVICES

    window = SCHEDULED_SERVICES["discover"]
    assert window["open"] == window["closed"]
    _seed(db_conn, "discover", LAST_OK)
    outcome = check.check_service(
        service="discover", kind="stale", now=NOW, market_state="closed",
    )
    assert outcome.status == "stale"
    assert outcome.message == "silent for 23d (window 4d)"
    assert "market" not in outcome.message


def test_market_dependent_window_keeps_the_market_label(db_conn):
    from watchdog import check
    from watchdog.services import SCHEDULED_SERVICES

    window = SCHEDULED_SERVICES["gex-scan"]
    assert window["open"] != window["closed"]
    _seed(db_conn, "gex-scan", NOW - timedelta(days=5))
    outcome = check.check_service(
        service="gex-scan", kind="stale", now=NOW, market_state="closed",
    )
    assert outcome.status == "stale"
    assert outcome.message.endswith("— market closed")
