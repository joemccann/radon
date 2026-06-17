"""Core check logic — per-service decision pipeline.

Each tested path:

 - happy: ok + fresh → no failure recorded.
 - stale: ok + past window → records stale failure (severity ~ market state).
 - error: state == 'error' → records error failure regardless of timestamp.
 - dormant: never-seen service (no row) → status="dormant", never fires.
 - active ack: returns SKIPPED without writing cooldown rows.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone


def _seed_service_health(db_conn, service: str, state: str, updated_at: datetime, error: dict | None = None):
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


class TestCheckOutcomes:
    def test_fresh_ok_row_is_healthy(self, db_conn):
        from watchdog import check

        now = datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc)
        _seed_service_health(db_conn, "vcg-scan", "ok", now - timedelta(minutes=2))
        outcome = check.check_service(
            service="vcg-scan",
            kind="stale",
            now=now,
            market_state="open",
        )
        assert outcome.status == "healthy"
        assert outcome.fired is False

    def test_stale_ok_row_records_failure(self, db_conn):
        from watchdog import check

        now = datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc)
        _seed_service_health(db_conn, "vcg-scan", "ok", now - timedelta(minutes=30))
        outcome = check.check_service(
            service="vcg-scan",
            kind="stale",
            now=now,
            market_state="open",
        )
        assert outcome.status == "stale"
        assert outcome.fired is False  # first failure, hysteresis blocks

    def test_two_consecutive_stale_checks_fire(self, db_conn):
        from watchdog import check

        now = datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc)
        _seed_service_health(db_conn, "vcg-scan", "ok", now - timedelta(minutes=30))
        check.check_service(service="vcg-scan", kind="stale", now=now, market_state="open")
        outcome = check.check_service(
            service="vcg-scan",
            kind="stale",
            now=now + timedelta(minutes=5),
            market_state="open",
        )
        assert outcome.fired is True

    def test_error_state_records_error_failure(self, db_conn):
        from watchdog import check

        now = datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc)
        _seed_service_health(
            db_conn,
            "cash-flow-sync",
            "error",
            now - timedelta(minutes=1),
            error={"message": "Flex rate limit"},
        )
        check.check_service(service="cash-flow-sync", kind="error", now=now, market_state="closed")
        outcome = check.check_service(
            service="cash-flow-sync",
            kind="error",
            now=now,
            market_state="closed",
        )
        assert outcome.status == "error"
        assert outcome.fired is True
        assert "Flex rate limit" in outcome.message

    def test_never_activated_service_returns_dormant_not_stale(self, db_conn):
        """A service with NO service_health row has never been activated.
        It must NOT fire a page — the operator knows it is dormant.
        Calling it twice confirms hysteresis is never incremented either.
        """
        from watchdog import check

        now = datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc)
        # No row seeded for newsfeed-scraper.
        outcome1 = check.check_service(
            service="newsfeed-scraper", kind="stale", now=now, market_state="open"
        )
        outcome2 = check.check_service(
            service="newsfeed-scraper",
            kind="stale",
            now=now + timedelta(minutes=5),
            market_state="open",
        )
        assert outcome1.status == "dormant"
        assert outcome1.fired is False
        assert outcome2.status == "dormant"
        assert outcome2.fired is False

    def test_previously_healthy_service_past_window_still_fires(self, db_conn):
        """A service that wrote a healthy row and then went past its window
        MUST still fire — this is a genuine staleness incident.
        """
        from watchdog import check

        now = datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc)
        # Seed a row 30 minutes old; vcg-scan open window is 15 min.
        _seed_service_health(db_conn, "vcg-scan", "ok", now - timedelta(minutes=30))
        check.check_service(service="vcg-scan", kind="stale", now=now, market_state="open")
        outcome = check.check_service(
            service="vcg-scan",
            kind="stale",
            now=now + timedelta(minutes=5),
            market_state="open",
        )
        assert outcome.status == "stale"
        assert outcome.fired is True

    def test_dormant_services_llm_token_index_and_preset_rebalance(self, db_conn):
        """llm-token-index and preset-rebalance have never written a row
        in production (no ARTIFICIAL_ANALYSIS_API_KEY; monitor daemon
        handler not activated). They must never fire a page.
        """
        from watchdog import check

        now = datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc)
        for service in ("llm-token-index", "preset-rebalance"):
            # Run twice to confirm hysteresis cannot accumulate either.
            o1 = check.check_service(
                service=service, kind="stale", now=now, market_state="closed"
            )
            o2 = check.check_service(
                service=service,
                kind="stale",
                now=now + timedelta(hours=1),
                market_state="closed",
            )
            assert o1.status == "dormant", f"{service}: expected dormant, got {o1.status}"
            assert o1.fired is False, f"{service}: fired on first check"
            assert o2.status == "dormant", f"{service}: expected dormant on 2nd check"
            assert o2.fired is False, f"{service}: fired on 2nd check"

    def test_active_ack_silences_and_skips_cooldown(self, db_conn):
        from watchdog import ack, check

        now = datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc)
        _seed_service_health(db_conn, "vcg-scan", "ok", now - timedelta(minutes=30))
        ack.add_ack(service="vcg-scan", hours=4, now=now)
        outcome = check.check_service(
            service="vcg-scan",
            kind="stale",
            now=now,
            market_state="open",
        )
        assert outcome.status == "acked"
        assert outcome.fired is False
        # Cooldown table should be untouched.
        rows = db_conn.execute(
            "SELECT * FROM watchdog_cooldowns WHERE service='vcg-scan'"
        ).fetchall()
        assert rows == []


class TestSeverityMapping:
    def test_market_hours_intraday_stale_is_p1(self, db_conn):
        from watchdog import check

        now = datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc)
        _seed_service_health(db_conn, "vcg-scan", "ok", now - timedelta(minutes=30))
        check.check_service(service="vcg-scan", kind="stale", now=now, market_state="open")
        outcome = check.check_service(
            service="vcg-scan",
            kind="stale",
            now=now + timedelta(minutes=5),
            market_state="open",
        )
        assert outcome.severity == "P1"

    def test_continuous_stale_is_p3(self, db_conn):
        from watchdog import check

        # replica-watchdog uses a 24h window because it's event-driven —
        # it only writes service_health when it heals. Seed 25h old so
        # the stale path trips for the severity test.
        now = datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc)
        _seed_service_health(db_conn, "replica-watchdog", "ok", now - timedelta(hours=25))
        check.check_service(service="replica-watchdog", kind="stale", now=now, market_state="closed")
        outcome = check.check_service(
            service="replica-watchdog",
            kind="stale",
            now=now + timedelta(minutes=5),
            market_state="closed",
        )
        assert outcome.severity == "P3"

    def test_error_state_is_p2(self, db_conn):
        from watchdog import check

        now = datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc)
        _seed_service_health(db_conn, "cash-flow-sync", "error", now)
        check.check_service(service="cash-flow-sync", kind="error", now=now, market_state="closed")
        outcome = check.check_service(
            service="cash-flow-sync",
            kind="error",
            now=now,
            market_state="closed",
        )
        assert outcome.severity == "P2"
