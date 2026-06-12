"""Tests for DUR-08: bounded JVM forensic capture on IB Gateway api-hang.

Two surfaces:

1. ``jvm_forensics.capture_jvm_forensics`` — the bounded capture itself
   (pid discovery, kill -3, log/stats/ps snapshots, retention prune).
   All docker access is mocked via an injected ``runner``.

2. The watchdog hook — fires exactly once per hang episode (on the
   0 -> 1 ``degraded_count`` transition), never re-fires while degraded,
   re-arms after recovery, and NEVER blocks or breaks the restart ladder.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

import jvm_forensics
from jvm_forensics import (
    MAX_CAPTURES_KEPT,
    STEP_TIMEOUT_SECS,
    TOTAL_BUDGET_SECS,
    capture_jvm_forensics,
)
from ib_watchdog import GatewayState, run_cycle


# --- Capture-side test doubles ------------------------------------------------


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, secs: float) -> None:
        self.now += secs


class FakeRunner:
    """Records every subprocess invocation; returns canned stdout per command."""

    def __init__(self, clock: FakeClock | None = None, secs_per_call: float = 0.1):
        self.calls: list[tuple[list[str], dict]] = []
        self.clock = clock
        self.secs_per_call = secs_per_call
        self.responses: dict[str, str] = {
            "pgrep": "1770100\n",
            "logs": 'Full thread dump Java HotSpot(TM) 64-Bit Server VM\n"JTS-Main" prio=5\n',
            "stats": "CONTAINER CPU% MEM\nib-gateway 12% 700MiB\n",
            "ps": "USER PID COMMAND\nradon 1 java\n",
        }
        self.fail_matching: str | None = None

    def _classify(self, cmd: list[str]) -> str:
        joined = " ".join(cmd)
        if "pgrep" in joined:
            return "pgrep"
        if "logs" in joined:
            return "logs"
        if "stats" in joined:
            return "stats"
        if "ps" in joined:
            return "ps"
        return "other"

    def __call__(self, cmd, **kwargs):
        self.calls.append((list(cmd), kwargs))
        if self.clock is not None:
            self.clock.advance(self.secs_per_call)
        kind = self._classify(cmd)
        if self.fail_matching and self.fail_matching in " ".join(cmd):
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0))
        return subprocess.CompletedProcess(
            cmd, 0, stdout=self.responses.get(kind, ""), stderr=""
        )

    def commands(self) -> list[str]:
        return [" ".join(c) for c, _ in self.calls]


@pytest.fixture
def out_dir(tmp_path: Path) -> Path:
    return tmp_path / "jvm_forensics"


# --- capture_jvm_forensics ------------------------------------------------------


class TestCaptureHappyPath:
    def test_writes_all_snapshot_files(self, out_dir):
        runner = FakeRunner()
        result = capture_jvm_forensics(output_dir=out_dir, runner=runner, sleeper=lambda s: None)
        assert result.ok is True
        assert result.capture_dir is not None
        names = {p.name for p in result.capture_dir.iterdir()}
        assert {"docker_logs.txt", "docker_stats.txt", "ps_aux.txt", "manifest.json"} <= names
        assert "Full thread dump" in (result.capture_dir / "docker_logs.txt").read_text()

    def test_kill_minus_3_targets_discovered_pid(self, out_dir):
        runner = FakeRunner()
        capture_jvm_forensics(output_dir=out_dir, runner=runner, sleeper=lambda s: None)
        kill_cmds = [c for c in runner.commands() if "kill -3" in c]
        assert len(kill_cmds) == 1
        assert "1770100" in kill_cmds[0]

    def test_manifest_records_step_outcomes(self, out_dir):
        runner = FakeRunner()
        result = capture_jvm_forensics(output_dir=out_dir, runner=runner, sleeper=lambda s: None)
        manifest = json.loads((result.capture_dir / "manifest.json").read_text())
        assert manifest["steps"]["kill_minus_3"] == "ok"
        assert manifest["steps"]["docker_logs"] == "ok"

    def test_every_subprocess_call_is_bounded(self, out_dir):
        runner = FakeRunner()
        capture_jvm_forensics(output_dir=out_dir, runner=runner, sleeper=lambda s: None)
        for cmd, kwargs in runner.calls:
            assert "timeout" in kwargs, f"unbounded subprocess call: {cmd}"
            assert kwargs["timeout"] <= STEP_TIMEOUT_SECS


class TestCaptureFailureModes:
    def test_runner_exception_never_propagates(self, out_dir):
        def exploding_runner(cmd, **kwargs):
            raise OSError("docker binary missing")

        result = capture_jvm_forensics(
            output_dir=out_dir, runner=exploding_runner, sleeper=lambda s: None
        )
        assert result.ok is False

    def test_no_pid_skips_kill_but_still_collects_logs(self, out_dir):
        runner = FakeRunner()
        runner.responses["pgrep"] = ""
        result = capture_jvm_forensics(output_dir=out_dir, runner=runner, sleeper=lambda s: None)
        assert not any("kill -3" in c for c in runner.commands())
        assert any("logs" in c for c in runner.commands())
        assert (result.capture_dir / "docker_logs.txt").exists()

    def test_budget_exhaustion_skips_remaining_steps(self, out_dir):
        clock = FakeClock()
        # Each call burns half the budget: pgrep + kill exhaust it.
        runner = FakeRunner(clock=clock, secs_per_call=TOTAL_BUDGET_SECS / 2)
        result = capture_jvm_forensics(
            output_dir=out_dir,
            runner=runner,
            sleeper=lambda s: clock.advance(s),
            clock=clock,
        )
        manifest = json.loads((result.capture_dir / "manifest.json").read_text())
        assert manifest["steps"]["ps_aux"].startswith("skipped")

    def test_unwritable_output_dir_returns_not_ok(self, tmp_path):
        blocked = tmp_path / "blocked"
        blocked.write_text("a file, not a dir")
        result = capture_jvm_forensics(
            output_dir=blocked / "jvm_forensics", runner=FakeRunner(), sleeper=lambda s: None
        )
        assert result.ok is False


class TestRetentionPrune:
    def test_prunes_to_last_n_captures(self, out_dir):
        out_dir.mkdir(parents=True)
        for i in range(MAX_CAPTURES_KEPT + 5):
            d = out_dir / f"20260601T{i:02d}0000Z"
            d.mkdir()
            (d / "manifest.json").write_text("{}")
        capture_jvm_forensics(output_dir=out_dir, runner=FakeRunner(), sleeper=lambda s: None)
        remaining = sorted(p.name for p in out_dir.iterdir() if p.is_dir())
        assert len(remaining) == MAX_CAPTURES_KEPT
        # The newest capture (the one just taken) must survive.
        assert remaining[-1] >= "20260601T250000Z"

    def test_unrelated_files_are_not_pruned(self, out_dir):
        out_dir.mkdir(parents=True)
        keepme = out_dir / "README.md"
        keepme.write_text("notes")
        capture_jvm_forensics(output_dir=out_dir, runner=FakeRunner(), sleeper=lambda s: None)
        assert keepme.exists()


# --- Watchdog hook --------------------------------------------------------------


def _hang_payload() -> dict:
    return {
        "ib_gateway": {
            "service_state": "unhealthy",
            "port_listening": True,
            "upstream_dead": True,
            "auth_state": "authenticated",
        }
    }


def _healthy_payload() -> dict:
    return {
        "ib_gateway": {
            "service_state": "healthy",
            "port_listening": True,
            "upstream_dead": False,
            "auth_state": "authenticated",
        }
    }


@pytest.fixture(autouse=True)
def _redirect_2fa_lock_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IB_2FA_LOCK_PATH", str(tmp_path / "ib-2fa-push-lock.json"))


@pytest.fixture
def state_path(tmp_path: Path) -> Path:
    return tmp_path / "watchdog-state.json"


def _drive(state_path: Path, payload: dict, capture_mock, **kwargs):
    def fake_fetch(url: str, timeout: float):
        return GatewayState.from_health_payload(payload)

    with (
        patch("ib_watchdog.fetch_health", side_effect=fake_fetch),
        patch("ib_watchdog.trigger_restart", return_value=True) as restart_mock,
        patch("ib_watchdog.record_service_health"),
        patch("jvm_forensics.capture_jvm_forensics", capture_mock),
    ):
        result = run_cycle(state_path=state_path, dry_run=True, **kwargs)
    return result, restart_mock


class TestWatchdogForensicsHook:
    def test_fires_on_first_degraded_cycle_only(self, state_path):
        from unittest.mock import MagicMock

        capture = MagicMock()
        _drive(state_path, _hang_payload(), capture, threshold=5)
        assert capture.call_count == 1
        _drive(state_path, _hang_payload(), capture, threshold=5)
        _drive(state_path, _hang_payload(), capture, threshold=5)
        assert capture.call_count == 1, "must not re-fire while episode is in progress"

    def test_does_not_fire_on_healthy_cycle(self, state_path):
        from unittest.mock import MagicMock

        capture = MagicMock()
        _drive(state_path, _healthy_payload(), capture)
        capture.assert_not_called()

    def test_rearms_after_recovery(self, state_path):
        from unittest.mock import MagicMock

        capture = MagicMock()
        _drive(state_path, _hang_payload(), capture, threshold=5)
        _drive(state_path, _healthy_payload(), capture, threshold=5)
        _drive(state_path, _hang_payload(), capture, threshold=5)
        assert capture.call_count == 2

    def test_capture_failure_never_blocks_restart_ladder(self, state_path):
        from unittest.mock import MagicMock

        capture = MagicMock(side_effect=RuntimeError("forensics exploded"))
        # threshold=1: the SAME cycle that fires the capture must still restart.
        result, restart = _drive(state_path, _hang_payload(), capture, threshold=1)
        restart.assert_called_once()
        assert result.last_outcome == "restarted:ok"

    def test_restart_threshold_unaffected_by_hook(self, state_path):
        from unittest.mock import MagicMock

        capture = MagicMock()
        for _ in range(2):
            _, restart = _drive(state_path, _hang_payload(), capture)
            restart.assert_not_called()
        _, restart = _drive(state_path, _hang_payload(), capture)
        restart.assert_called_once()
