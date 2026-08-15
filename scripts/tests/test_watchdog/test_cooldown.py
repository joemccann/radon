"""Hysteresis + cooldown tests for the watchdog state layer.

Hysteresis: 1 failure doesn't fire, 2 consecutive failures do, a single
healthy check resets the counter.

Cooldown: once an alert fires, suppress further alerts for the same
(service, severity) for 1h. After 1h, fire again.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


class TestHysteresis:
    def test_first_failure_does_not_fire(self, db_conn):
        from watchdog import cooldown

        decision = cooldown.record_failure_and_decide(
            service="vcg-scan",
            kind="stale",
            now=datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc),
        )
        assert decision.should_fire is False
        assert decision.consecutive_failures == 1

    def test_second_consecutive_failure_fires(self, db_conn):
        from watchdog import cooldown

        now = datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc)
        cooldown.record_failure_and_decide(service="vcg-scan", kind="stale", now=now)
        decision = cooldown.record_failure_and_decide(
            service="vcg-scan",
            kind="stale",
            now=now + timedelta(minutes=5),
        )
        assert decision.should_fire is True
        assert decision.consecutive_failures == 2

    def test_recovery_resets_counter(self, db_conn):
        from watchdog import cooldown

        now = datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc)
        cooldown.record_failure_and_decide(service="vcg-scan", kind="stale", now=now)
        cooldown.record_success(service="vcg-scan", kind="stale")
        decision = cooldown.record_failure_and_decide(
            service="vcg-scan",
            kind="stale",
            now=now + timedelta(minutes=10),
        )
        # Counter is 1 again — single failure post-recovery does NOT fire.
        assert decision.should_fire is False
        assert decision.consecutive_failures == 1

    def test_stale_and_error_counters_independent(self, db_conn):
        from watchdog import cooldown

        now = datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc)
        cooldown.record_failure_and_decide(service="vcg-scan", kind="stale", now=now)
        cooldown.record_failure_and_decide(service="vcg-scan", kind="stale", now=now)
        # Stale fires above; check that the error counter is still at 0.
        decision = cooldown.record_failure_and_decide(
            service="vcg-scan",
            kind="error",
            now=now,
        )
        assert decision.consecutive_failures == 1
        assert decision.should_fire is False


class TestCooldown:
    def test_first_alert_passes_cooldown_check(self, db_conn):
        from watchdog import cooldown

        passed = cooldown.cooldown_allows_fire(
            service="vcg-scan",
            severity="P1",
            now=datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc),
        )
        assert passed is True

    def test_repeat_alert_inside_window_blocked(self, db_conn):
        from watchdog import cooldown

        now = datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc)
        cooldown.mark_notified(service="vcg-scan", severity="P1", now=now)
        passed = cooldown.cooldown_allows_fire(
            service="vcg-scan",
            severity="P1",
            now=now + timedelta(minutes=30),
        )
        assert passed is False

    def test_latched_condition_does_not_rearm_hourly(self, db_conn):
        # 2026-07-28 storm regression: a continuously-latched condition
        # re-armed a fresh unacknowledged P1 emergency every ~65 min for six
        # hours. Without a recovery since the last notification, the 1h
        # window alone must NOT re-arm.
        from watchdog import cooldown

        now = datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc)
        cooldown.mark_notified(service="vcg-scan", severity="P1", now=now)
        passed = cooldown.cooldown_allows_fire(
            service="vcg-scan",
            severity="P1",
            now=now + timedelta(hours=1, minutes=1),
        )
        assert passed is False

    def test_latched_condition_rearms_at_24h_ceiling(self, db_conn):
        from watchdog import cooldown

        now = datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc)
        cooldown.mark_notified(service="vcg-scan", severity="P1", now=now)
        passed = cooldown.cooldown_allows_fire(
            service="vcg-scan",
            severity="P1",
            now=now + timedelta(hours=24, minutes=1),
        )
        assert passed is True

    def test_recovery_rearms_after_the_flap_floor(self, db_conn):
        # A recovery between notifications means the next failure is a NEW
        # condition-transition: one page per transition, past the 1h floor.
        from watchdog import cooldown

        now = datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc)
        cooldown.mark_notified(service="vcg-scan", severity="P1", now=now)
        cooldown.record_success(service="vcg-scan", kind="error")
        passed = cooldown.cooldown_allows_fire(
            service="vcg-scan",
            severity="P1",
            now=now + timedelta(hours=1, minutes=1),
        )
        assert passed is True

    def test_recovery_inside_flap_floor_still_suppressed(self, db_conn):
        from watchdog import cooldown

        now = datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc)
        cooldown.mark_notified(service="vcg-scan", severity="P1", now=now)
        cooldown.record_success(service="vcg-scan", kind="error")
        passed = cooldown.cooldown_allows_fire(
            service="vcg-scan",
            severity="P1",
            now=now + timedelta(minutes=30),
        )
        assert passed is False

    def test_recovery_only_rearms_its_own_service(self, db_conn):
        from watchdog import cooldown

        now = datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc)
        cooldown.mark_notified(service="vcg-scan", severity="P1", now=now)
        cooldown.mark_notified(service="gex-scan", severity="P1", now=now)
        cooldown.record_success(service="vcg-scan", kind="error")
        assert cooldown.cooldown_allows_fire(
            service="gex-scan", severity="P1", now=now + timedelta(hours=1, minutes=1)
        ) is False

    def test_different_severities_have_independent_cooldown(self, db_conn):
        from watchdog import cooldown

        now = datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc)
        cooldown.mark_notified(service="vcg-scan", severity="P1", now=now)
        passed = cooldown.cooldown_allows_fire(
            service="vcg-scan",
            severity="P3",
            now=now + timedelta(minutes=5),
        )
        assert passed is True


class TestPersistence:
    def test_consecutive_failures_persisted_across_calls(self, db_conn):
        from watchdog import cooldown

        now = datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc)
        cooldown.record_failure_and_decide(service="vcg-scan", kind="stale", now=now)
        # Read back via internal helper:
        rows = db_conn.execute(
            "SELECT service, kind, consecutive_failures FROM watchdog_cooldowns"
        ).fetchall()
        assert ("vcg-scan", "stale", 1) in rows

    def test_mark_notified_writes_timestamp(self, db_conn):
        from watchdog import cooldown

        now = datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc)
        cooldown.mark_notified(service="cri-scan", severity="P2", now=now)
        rows = db_conn.execute(
            "SELECT service, kind, last_notified_at FROM watchdog_cooldowns WHERE service='cri-scan'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][2] == _iso(now)


class TestStateStoreIsNeverCommittable:
    """Alert suppression is host-local by design; committing it is a real bug.

    ``state_db.py`` keeps cooldowns and acks off Turso on purpose — a
    data-plane outage must not disable the component reporting it. The cost
    of that choice is a SQLite file sitting in ``data/``. ``data/*.json``
    already covers the sibling JSON state, but not ``.db`` or its WAL
    sidecars, so one host's suppression state was one ``git add`` from
    muting every other host's alerts.
    """

    def _ignored(self, relpath: str) -> bool:
        import subprocess
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[3]
        return (
            subprocess.run(
                ["git", "check-ignore", "-q", relpath], cwd=repo_root
            ).returncode
            == 0
        )

    def test_default_state_path_still_lives_in_data(self):
        from pathlib import Path

        from watchdog.state_db import DEFAULT_PATH

        repo_root = Path(__file__).resolve().parents[3]
        assert DEFAULT_PATH.parent == repo_root / "data", (
            "state moved; repoint the ignore rules below"
        )
        assert DEFAULT_PATH.name == "watchdog_state.db"

    def test_state_db_and_its_wal_sidecars_are_ignored(self):
        # WAL mode (state_db.py sets journal_mode=WAL) writes -wal and -shm
        # alongside the db; ignoring only the .db leaves those committable.
        for name in (
            "watchdog_state.db",
            "watchdog_state.db-wal",
            "watchdog_state.db-shm",
        ):
            assert self._ignored(f"data/{name}"), f"data/{name} must be gitignored"

    def test_runtime_lock_files_are_ignored(self):
        assert self._ignored("data/watchdog_digest_state.json.lock")
