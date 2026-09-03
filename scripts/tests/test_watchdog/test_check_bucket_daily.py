"""The daily bucket fires regardless of hour.

cash-flow-sync is written by the sFTP ingest Tue..Sat 07:30 ET; its open-state
window is 3d (a Monday session is ~57h past the Saturday run) and its
closed-state window is 4d (covers the weekend gap plus a holiday day).
flex-token-check has a uniform 25h window.
The watchdog timer fires every hour and we expect the bucket to always run.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone


def _seed(db_conn, service: str, state: str, updated_at: datetime, error: dict | None = None):
    db_conn.execute(
        """
        INSERT OR REPLACE INTO service_health
          (service, state, last_attempt_started_at, last_attempt_finished_at,
           last_error, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            service,
            state,
            None,
            updated_at.isoformat().replace("+00:00", "Z"),
            json.dumps(error) if error else None,
            updated_at.isoformat().replace("+00:00", "Z"),
        ),
    )
    db_conn.commit()


class TestDailyBucket:
    def test_daily_runs_at_midnight(self, db_conn):
        from watchdog import check

        now = datetime(2026, 5, 13, 0, 0, tzinfo=timezone.utc)
        _seed(db_conn, "cash-flow-sync", "ok", now - timedelta(hours=22))
        report = check.check_bucket(bucket="daily", now=now)
        assert report.ran is True

    def test_daily_runs_during_market_hours(self, db_conn):
        from watchdog import check

        now = datetime(2026, 5, 13, 15, 0, tzinfo=timezone.utc)
        _seed(db_conn, "cash-flow-sync", "ok", now - timedelta(hours=22))
        report = check.check_bucket(bucket="daily", now=now)
        assert report.ran is True

    def test_daily_fires_when_cash_flow_silent_for_3_days(self, db_conn):
        from watchdog import check

        # cash-flow-sync is written by the sFTP ingest (Tue..Sat 07:30 ET),
        # so its open-state window is 3 days: a Monday session is legitimately
        # ~57h past the Saturday run. This test runs at 11:00 ET on a
        # Wednesday (market open), so market_state="open" and the 3-day open
        # window applies. Past 73h is stale during market hours.
        now = datetime(2026, 5, 13, 15, 0, tzinfo=timezone.utc)
        _seed(db_conn, "cash-flow-sync", "ok", now - timedelta(hours=73))
        _seed(db_conn, "flex-token-check", "ok", now - timedelta(hours=22))
        check.check_bucket(bucket="daily", now=now)
        report = check.check_bucket(bucket="daily", now=now + timedelta(hours=1))
        fired = [o for o in report.outcomes if o.fired]
        assert any(o.service == "cash-flow-sync" for o in fired)

    def test_cash_flow_sync_not_stale_on_saturday_after_friday_run(self, db_conn):
        """Regression: cash-flow-sync fires at 17:00 ET on trading days only.
        A Friday 17:00 ET run (22:00 UTC) must NOT flip to stale by Saturday
        noon ET (16:00 UTC Saturday) — that's only ~18h, well inside 4d.
        Even Saturday at midnight UTC (~40h after Friday 22:00 UTC) must
        stay fresh. The prior 25h uniform closed window caused a false
        positive here every weekend.
        """
        from watchdog import check

        # Friday May 8 at 22:00 UTC = Friday 6 PM ET. Last sync.
        fri_run = datetime(2026, 5, 8, 22, 0, tzinfo=timezone.utc)
        # Saturday May 9 at 16:00 UTC = Saturday noon ET (~18h later).
        sat_noon = datetime(2026, 5, 9, 16, 0, tzinfo=timezone.utc)
        _seed(db_conn, "cash-flow-sync", "ok", fri_run)
        report = check.check_bucket(bucket="daily", now=sat_noon)
        outcomes = {o.service: o for o in report.outcomes}
        cfs = outcomes.get("cash-flow-sync")
        assert cfs is not None
        assert cfs.status == "healthy", (
            f"cash-flow-sync should be healthy on Saturday noon after a Friday run, "
            f"but got status={cfs.status!r} message={cfs.message!r}"
        )


class TestErrorBucket:
    def test_error_bucket_runs_always(self, db_conn):
        from watchdog import check

        now = datetime(2026, 5, 11, 4, 0, tzinfo=timezone.utc)
        _seed(db_conn, "vcg-scan", "ok", now)
        report = check.check_bucket(bucket="error", now=now)
        assert report.ran is True

    def test_error_bucket_only_alerts_on_error_state(self, db_conn):
        from watchdog import check

        now = datetime(2026, 5, 11, 4, 0, tzinfo=timezone.utc)
        _seed(db_conn, "vcg-scan", "ok", now - timedelta(days=2))
        check.check_bucket(bucket="error", now=now)
        report = check.check_bucket(bucket="error", now=now + timedelta(minutes=5))
        fired = [o for o in report.outcomes if o.fired]
        # vcg-scan is stale but bucket only watches errors → no fire.
        assert fired == []

    def test_error_bucket_fires_on_persistent_error(self, db_conn):
        from watchdog import check

        now = datetime(2026, 5, 11, 4, 0, tzinfo=timezone.utc)
        _seed(
            db_conn,
            "cash-flow-sync",
            "error",
            now,
            error={"message": "Flex throttled"},
        )
        check.check_bucket(bucket="error", now=now)
        report = check.check_bucket(bucket="error", now=now + timedelta(minutes=5))
        fired = [o for o in report.outcomes if o.fired]
        assert any(o.service == "cash-flow-sync" for o in fired)
