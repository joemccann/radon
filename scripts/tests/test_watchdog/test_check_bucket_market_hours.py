"""The intraday bucket — only fires alerts when the market is open.

The systemd timer is already gated to Mon-Fri 13:00-21:00 UTC, but the
Python check also enforces this so a stray manual run / cron drift
doesn't fire P1 alerts overnight.
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


class TestIntradayBucketGate:
    def test_market_open_runs_checks(self, db_conn):
        from watchdog import check

        # 15:00 UTC on a Wednesday → ET 10:00 → market open.
        now = datetime(2026, 5, 13, 15, 0, tzinfo=timezone.utc)
        _seed(db_conn, "vcg-scan", "ok", now - timedelta(minutes=2))
        report = check.check_bucket(bucket="intraday", now=now)
        assert report.ran is True
        # All four scheduled intraday services should appear in the report.
        services_checked = {r.service for r in report.outcomes}
        assert "vcg-scan" in services_checked
        assert "cri-scan" in services_checked
        assert "orders-sync" in services_checked
        assert "portfolio-sync" in services_checked

    def test_market_closed_no_op(self, db_conn):
        from watchdog import check

        # 02:00 UTC on a Sunday → market firmly closed in any tz.
        now = datetime(2026, 5, 10, 2, 0, tzinfo=timezone.utc)
        _seed(db_conn, "vcg-scan", "ok", now - timedelta(hours=20))
        report = check.check_bucket(bucket="intraday", now=now)
        assert report.ran is False
        assert report.outcomes == []

    def test_market_open_stale_triggers_eventually(self, db_conn):
        from watchdog import check

        now = datetime(2026, 5, 13, 15, 0, tzinfo=timezone.utc)
        _seed(db_conn, "vcg-scan", "ok", now - timedelta(minutes=30))
        # Seed the other intraday services as fresh so we don't get noise.
        for other in ("cri-scan", "orders-sync", "portfolio-sync"):
            _seed(db_conn, other, "ok", now - timedelta(minutes=2))

        # First run — vcg stale but hysteresis blocks.
        report1 = check.check_bucket(bucket="intraday", now=now)
        fired1 = [o for o in report1.outcomes if o.fired]
        assert fired1 == []

        # Second run 5 min later — vcg still stale, hysteresis trips.
        report2 = check.check_bucket(bucket="intraday", now=now + timedelta(minutes=5))
        fired2 = [o for o in report2.outcomes if o.fired]
        assert len(fired2) == 1
        assert fired2[0].service == "vcg-scan"


class TestIntradayOpenBellGraceAndWeekend:
    """A RTH-only scanner (cri-scan) that was legitimately silent over the
    weekend must NOT fire 'silent for 2d — market open' at the 9:30 bell, but a
    genuine intra-session stall must still trip. June 2026 is EDT (ET = UTC-4)."""

    FRI_CLOSE = datetime(2026, 6, 12, 20, 0, tzinfo=timezone.utc)  # Fri 16:00 ET

    def _seed_others_fresh(self, db_conn, now):
        for other in ("vcg-scan", "orders-sync", "portfolio-sync"):
            _seed(db_conn, other, "ok", now - timedelta(minutes=2))

    def test_cri_scan_graced_at_open_bell(self, db_conn):
        from watchdog import check
        # Mon 09:35 ET — 5 min after the bell, before cri-scan's first RTH run.
        now = datetime(2026, 6, 15, 13, 35, tzinfo=timezone.utc)
        _seed(db_conn, "cri-scan", "ok", self.FRI_CLOSE)  # last wrote Friday
        self._seed_others_fresh(db_conn, now)
        report = check.check_bucket(bucket="intraday", now=now)
        cri = next(o for o in report.outcomes if o.service == "cri-scan")
        assert cri.status == "healthy", cri.message

    def test_cri_scan_stale_after_grace_window(self, db_conn):
        from watchdog import check
        # Mon 10:30 ET — 60 min into RTH, past the 35m grace; a still-silent
        # scanner is a real failure and must trip.
        now = datetime(2026, 6, 15, 14, 30, tzinfo=timezone.utc)
        _seed(db_conn, "cri-scan", "ok", self.FRI_CLOSE)
        self._seed_others_fresh(db_conn, now)
        report = check.check_bucket(bucket="intraday", now=now)
        cri = next(o for o in report.outcomes if o.service == "cri-scan")
        assert cri.status == "stale", cri.message

    def test_cri_scan_not_stale_premarket_extended_weekend(self, db_conn):
        from watchdog import check
        # Mon 08:00 ET (extended) — last write Friday is ~64h old; the 3d closed
        # window covers the weekend gap (would have fired with the old 1d window).
        now = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
        _seed(db_conn, "cri-scan", "ok", self.FRI_CLOSE)
        self._seed_others_fresh(db_conn, now)
        report = check.check_bucket(bucket="intraday", now=now)
        cri = next(o for o in report.outcomes if o.service == "cri-scan")
        assert cri.status == "healthy", cri.message


class TestContinuousBucketAlwaysRuns:
    def test_continuous_bucket_runs_off_hours(self, db_conn):
        from watchdog import check

        # 03:00 UTC Sunday — should still run continuous checks.
        now = datetime(2026, 5, 10, 3, 0, tzinfo=timezone.utc)
        _seed(db_conn, "newsfeed-scraper", "ok", now - timedelta(minutes=1))
        report = check.check_bucket(bucket="continuous", now=now)
        assert report.ran is True
        services = {r.service for r in report.outcomes}
        assert "newsfeed-scraper" in services
        assert "replica-watchdog" in services
