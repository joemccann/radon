"""Tests for ReplicaWatchdogHandler — WalConflict death-spiral detection + self-heal.

Heal is one atomic operator action (`radon repair-nextjs-replica`) under the
shared deploy lock. Tests mock subprocess.run so the environment never shells
out to journalctl or the operator CLI.

`db.writer` depends on libsql_experimental which isn't installed in the test
environment, so we inject a fake `db.writer` module into sys.modules before
the handler imports it.
"""
from __future__ import annotations

import subprocess
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Make scripts/ importable.
SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _install_fake_db_writer() -> tuple[MagicMock, MagicMock]:
    """Stub `db.writer` so the handler's lazy import works in tests.

    Returns the (record_service_health, _now_iso) MagicMocks so callers
    can assert against them.
    """
    record_mock = MagicMock(name="record_service_health")
    now_iso_mock = MagicMock(name="_now_iso", return_value="2026-05-09T09:00:00Z")

    fake_writer = types.ModuleType("db.writer")
    fake_writer.record_service_health = record_mock  # type: ignore[attr-defined]
    fake_writer._now_iso = now_iso_mock  # type: ignore[attr-defined]

    fake_db_pkg = sys.modules.get("db") or types.ModuleType("db")
    fake_db_pkg.writer = fake_writer  # type: ignore[attr-defined]

    sys.modules["db"] = fake_db_pkg
    sys.modules["db.writer"] = fake_writer
    return record_mock, now_iso_mock


@pytest.fixture(autouse=True)
def fake_db_writer():
    """Reset db.writer mocks for every test."""
    record_mock, now_iso_mock = _install_fake_db_writer()
    yield record_mock, now_iso_mock


import monitor_daemon.handlers.replica_watchdog as replica_watchdog  # noqa: E402

ReplicaWatchdogHandler = replica_watchdog.ReplicaWatchdogHandler


@pytest.fixture(autouse=True)
def existing_replica(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Exercise legacy watchdog behavior only when a replica actually exists."""
    replica_path = tmp_path / "data" / "replica.db"
    replica_path.parent.mkdir()
    replica_path.touch()
    monkeypatch.setattr(replica_watchdog, "REPLICA_PATH", replica_path)
    return replica_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _journal_output(conflict_count: int) -> str:
    """Build fake journalctl output with N WalConflict lines."""
    base_lines = [
        "May 09 09:00:00 hetzner radon-nextjs[123]: ready - started server on 0.0.0.0:3000",
        "May 09 09:00:30 hetzner radon-nextjs[123]: GET /api/health 200",
    ]
    conflict_lines = [
        f"May 09 09:0{i}:30 hetzner radon-nextjs[123]: Error syncing database: WAL frame insert conflict (WalConflict)"
        for i in range(conflict_count)
    ]
    return "\n".join(base_lines + conflict_lines) + "\n"


def _journalctl_result(conflict_count: int) -> MagicMock:
    mock = MagicMock(spec=subprocess.CompletedProcess)
    mock.stdout = _journal_output(conflict_count)
    mock.stderr = ""
    mock.returncode = 0
    return mock


def _ok_result() -> MagicMock:
    mock = MagicMock(spec=subprocess.CompletedProcess)
    mock.stdout = ""
    mock.stderr = ""
    mock.returncode = 0
    return mock


def _is_repair_cmd(cmd) -> bool:
    return (
        list(cmd[:2])
        == [ReplicaWatchdogHandler.OPERATOR_BIN, ReplicaWatchdogHandler.REPAIR_ACTION]
    )


def _build_subprocess_router(conflict_count: int):
    """Return a side_effect function that routes journalctl vs repair calls."""

    def _route(cmd, *args, **kwargs):
        if cmd and cmd[0] == "journalctl":
            return _journalctl_result(conflict_count)
        if _is_repair_cmd(cmd):
            return _ok_result()
        return _ok_result()

    return _route


# ---------------------------------------------------------------------------
# Disabled path.
# ---------------------------------------------------------------------------
class TestDisabled:
    def test_missing_replica_returns_disabled_without_subprocess(
        self, existing_replica: Path
    ) -> None:
        existing_replica.unlink()
        handler = ReplicaWatchdogHandler()

        with patch(
            "monitor_daemon.handlers.replica_watchdog.subprocess.run"
        ) as run_mock:
            result = handler.execute()

        assert result == {"status": "disabled", "reason": "replica_absent"}
        run_mock.assert_not_called()

    def test_missing_replica_does_not_write_service_health(
        self, existing_replica: Path, fake_db_writer
    ) -> None:
        existing_replica.unlink()
        record_mock, now_iso_mock = fake_db_writer

        result = ReplicaWatchdogHandler().run()

        assert result["status"] == "ok"
        assert result["data"] == {
            "status": "disabled",
            "reason": "replica_absent",
        }
        record_mock.assert_not_called()
        now_iso_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Healthy / below-threshold paths.
# ---------------------------------------------------------------------------
class TestHealthy:
    def test_zero_conflicts_returns_healthy(self):
        handler = ReplicaWatchdogHandler()
        with patch(
            "monitor_daemon.handlers.replica_watchdog.subprocess.run",
            side_effect=_build_subprocess_router(0),
        ) as run_mock:
            result = handler.execute()

        assert result == {"status": "healthy", "wal_conflicts_5m": 0}
        # Only the journalctl call should have happened.
        assert run_mock.call_count == 1
        assert run_mock.call_args_list[0][0][0][0] == "journalctl"

    def test_two_conflicts_below_threshold_is_healthy(self):
        """Two conflicts in 5min is noise, not a death spiral."""
        handler = ReplicaWatchdogHandler()
        with patch(
            "monitor_daemon.handlers.replica_watchdog.subprocess.run",
            side_effect=_build_subprocess_router(2),
        ) as run_mock:
            result = handler.execute()

        assert result == {"status": "healthy", "wal_conflicts_5m": 2}
        assert run_mock.call_count == 1

    def test_healthy_path_writes_ok_heartbeat(self, fake_db_writer):
        """Every healthy cycle must heartbeat service_health=ok so the
        dashboard banner doesn't latch the row at stale after 24h of
        quiet (no WalConflicts to heal). Documented in
        feedback_service_health_heartbeat.md.
        """
        record_mock, _ = fake_db_writer
        handler = ReplicaWatchdogHandler()

        with patch(
            "monitor_daemon.handlers.replica_watchdog.subprocess.run",
            side_effect=_build_subprocess_router(0),
        ):
            handler.execute()

        assert record_mock.call_count == 1
        args, kwargs = record_mock.call_args
        assert args == ("replica-watchdog", "ok")
        assert kwargs["error"]["wal_conflicts_5m"] == 0
        assert "heartbeat_at" in kwargs["error"]


# ---------------------------------------------------------------------------
# Self-heal at threshold.
# ---------------------------------------------------------------------------
class TestSelfHeal:
    def test_three_conflicts_triggers_full_heal_sequence(self, fake_db_writer):
        record_mock, _ = fake_db_writer
        handler = ReplicaWatchdogHandler()

        with patch(
            "monitor_daemon.handlers.replica_watchdog.subprocess.run",
            side_effect=_build_subprocess_router(3),
        ) as run_mock:
            result = handler.execute()

        assert result == {"status": "healed", "wal_conflicts_5m": 3}

        # Subprocess call order: journalctl → atomic operator repair.
        commands = [c[0][0] for c in run_mock.call_args_list]
        assert commands[0][0] == "journalctl"
        assert commands[1] == [
            ReplicaWatchdogHandler.OPERATOR_BIN,
            ReplicaWatchdogHandler.REPAIR_ACTION,
        ]
        assert len(commands) == 2, "Expected exactly journalctl + repair"

        # service_health called twice — syncing then ok.
        assert record_mock.call_count == 2
        first_args, first_kwargs = record_mock.call_args_list[0]
        assert first_args == ("replica-watchdog", "syncing")
        assert "started_at" in first_kwargs

        second_args, second_kwargs = record_mock.call_args_list[1]
        assert second_args == ("replica-watchdog", "ok")
        assert "finished_at" in second_kwargs
        assert second_kwargs["error"]["wal_conflicts_observed"] == 3

        # Throttle should now be set.
        assert handler._last_heal_at is not None

    def test_five_conflicts_also_heals(self):
        handler = ReplicaWatchdogHandler()
        with patch(
            "monitor_daemon.handlers.replica_watchdog.subprocess.run",
            side_effect=_build_subprocess_router(5),
        ):
            result = handler.execute()

        assert result["status"] == "healed"
        assert result["wal_conflicts_5m"] == 5


# ---------------------------------------------------------------------------
# Throttle behavior.
# ---------------------------------------------------------------------------
class TestThrottle:
    def test_throttled_when_recent_heal_under_30min(self):
        handler = ReplicaWatchdogHandler()
        # Heal happened 10 minutes ago.
        handler._last_heal_at = datetime.now(timezone.utc) - timedelta(minutes=10)

        with patch(
            "monitor_daemon.handlers.replica_watchdog.subprocess.run",
            side_effect=_build_subprocess_router(5),
        ) as run_mock:
            result = handler.execute()

        assert result["status"] == "throttled"
        assert 590 <= result["since_last_heal_s"] <= 610  # ~10 min
        # journalctl ran but no repair.
        assert run_mock.call_count == 1
        assert not any(_is_repair_cmd(c[0][0]) for c in run_mock.call_args_list)

    def test_heal_runs_again_after_throttle_expires(self):
        handler = ReplicaWatchdogHandler()
        # Last heal was 31 minutes ago — throttle window is 30 min.
        handler._last_heal_at = datetime.now(timezone.utc) - timedelta(minutes=31)

        with patch(
            "monitor_daemon.handlers.replica_watchdog.subprocess.run",
            side_effect=_build_subprocess_router(5),
        ):
            result = handler.execute()

        assert result["status"] == "healed"
        assert result["wal_conflicts_5m"] == 5


# ---------------------------------------------------------------------------
# State persistence.
# ---------------------------------------------------------------------------
class TestStateRoundTrip:
    def test_get_state_includes_last_heal_at(self):
        handler = ReplicaWatchdogHandler()
        ts = datetime(2026, 5, 9, 9, 0, 0, tzinfo=timezone.utc)
        handler._last_heal_at = ts

        state = handler.get_state()

        assert "last_heal_at" in state
        assert state["last_heal_at"] == ts.isoformat()

    def test_get_state_with_no_heal_yet(self):
        handler = ReplicaWatchdogHandler()
        state = handler.get_state()
        assert state["last_heal_at"] is None

    def test_set_state_restores_last_heal_at(self):
        handler = ReplicaWatchdogHandler()
        ts = datetime(2026, 5, 9, 9, 0, 0, tzinfo=timezone.utc)

        handler.set_state({"last_heal_at": ts.isoformat()})

        assert handler._last_heal_at == ts

    def test_set_state_handles_missing_last_heal_at(self):
        handler = ReplicaWatchdogHandler()
        handler._last_heal_at = datetime.now(timezone.utc)

        handler.set_state({"last_heal_at": None})

        assert handler._last_heal_at is None


# ---------------------------------------------------------------------------
# Failure paths.
# ---------------------------------------------------------------------------
class TestHealFailure:
    def test_repair_fails_returns_heal_failed_and_throttle_not_advanced(
        self, fake_db_writer
    ):
        record_mock, _ = fake_db_writer
        handler = ReplicaWatchdogHandler()

        def _route(cmd, *args, **kwargs):
            if cmd and cmd[0] == "journalctl":
                return _journalctl_result(5)
            if _is_repair_cmd(cmd):
                raise subprocess.CalledProcessError(
                    returncode=1, cmd=cmd, stderr="deploy/control lock held"
                )
            return _ok_result()

        with patch(
            "monitor_daemon.handlers.replica_watchdog.subprocess.run",
            side_effect=_route,
        ):
            result = handler.execute()

        assert result["status"] == "heal_failed"
        assert "error" in result
        # Throttle MUST NOT advance — operator wants the next cycle to retry.
        assert handler._last_heal_at is None
        # service_health called with syncing (start) + error (failure).
        states = [c[0][1] for c in record_mock.call_args_list]
        assert "syncing" in states
        assert "error" in states


class TestSubprocessTimeout:
    def test_subprocess_timeout_treated_same_as_call_error(self, fake_db_writer):
        """TimeoutExpired on repair must take the same heal_failed path as CalledProcessError."""
        record_mock, _ = fake_db_writer
        handler = ReplicaWatchdogHandler()

        def _route(cmd, *args, **kwargs):
            if cmd and cmd[0] == "journalctl":
                return _journalctl_result(4)
            if _is_repair_cmd(cmd):
                raise subprocess.TimeoutExpired(
                    cmd=cmd, timeout=ReplicaWatchdogHandler.SUBPROCESS_TIMEOUT
                )
            return _ok_result()

        with patch(
            "monitor_daemon.handlers.replica_watchdog.subprocess.run",
            side_effect=_route,
        ):
            result = handler.execute()

        assert result["status"] == "heal_failed"
        assert "error" in result
        # Throttle stays unadvanced.
        assert handler._last_heal_at is None
        # service_health hit syncing then error, no ok.
        states = [c[0][1] for c in record_mock.call_args_list]
        assert states == ["syncing", "error"]
        error_kwargs = record_mock.call_args_list[1][1]
        assert error_kwargs["error"]["wal_conflicts_observed"] == 4


class TestJournalctlUnavailable:
    @pytest.mark.parametrize(
        "exception",
        [
            subprocess.TimeoutExpired(cmd=["journalctl"], timeout=10),
            FileNotFoundError("journalctl"),
        ],
        ids=["timeout", "not_found"],
    )
    def test_journalctl_unavailable_returns_healthy_with_zero_count(
        self, exception, fake_db_writer, caplog
    ):
        """If journalctl is unreachable we go blind — log a warning, return 0, never heal."""
        record_mock, _ = fake_db_writer
        handler = ReplicaWatchdogHandler()

        def _route(cmd, *args, **kwargs):
            if cmd and cmd[0] == "journalctl":
                raise exception
            return _ok_result()

        with caplog.at_level(
            "WARNING", logger="monitor_daemon.handlers.replica_watchdog"
        ), patch(
            "monitor_daemon.handlers.replica_watchdog.subprocess.run",
            side_effect=_route,
        ) as run_mock:
            result = handler.execute()

        # Healthy with zero count — fail open, don't restart on a blind read.
        assert result == {"status": "healthy", "wal_conflicts_5m": 0}

        # The healthy path heartbeats service_health=ok (single write).
        # Heal-specific writes (syncing → ok with wal_conflicts_observed)
        # must NOT fire.
        assert record_mock.call_count == 1
        args, kwargs = record_mock.call_args
        assert args == ("replica-watchdog", "ok")
        assert "heartbeat_at" in kwargs.get("error", {})
        assert run_mock.call_count == 1
        assert run_mock.call_args_list[0][0][0][0] == "journalctl"
        assert not any(_is_repair_cmd(c[0][0]) for c in run_mock.call_args_list)

        # Warning logged so operators can see the watchdog has gone blind.
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert any("journalctl unavailable" in r.getMessage() for r in warnings)


class TestNaiveIsoStateRestore:
    def test_set_state_with_naive_iso_does_not_crash_throttle_check(
        self, fake_db_writer
    ):
        """Naive ISO timestamps in persisted state must not blow up the throttle subtraction.

        Path (b): set_state coerces naive → UTC so datetime.now(tz=utc) - last_heal_at
        succeeds without TypeError. Patched in replica_watchdog.set_state.
        """
        handler = ReplicaWatchdogHandler()

        # Naive ISO — no offset, no Z. fromisoformat() gives a naive datetime.
        handler.set_state({"last_heal_at": "2026-05-09T08:30:00"})

        # The coercion happened: stored value is now tz-aware UTC.
        assert handler._last_heal_at is not None
        assert handler._last_heal_at.tzinfo is not None
        assert handler._last_heal_at.utcoffset() == timedelta(0)

        # Throttle check must not raise — execute() should complete cleanly.
        with patch(
            "monitor_daemon.handlers.replica_watchdog.subprocess.run",
            side_effect=_build_subprocess_router(5),
        ):
            # Whether this returns "throttled" or "healed" depends on how long ago
            # 2026-05-09T08:30:00Z was relative to wall-clock. The test guarantee is
            # only "no TypeError" — both terminal statuses are fine.
            result = handler.execute()

        assert result["status"] in {"throttled", "healed"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
