"""``ib_watchdog`` must be UN-HANGABLE: a single cycle always terminates in
bounded time so a hung external call (the libsql commit with no native
timeout was the proven 6h-hang root cause) can never permanently stall the
every-minute oneshot timer.

Two layers of defence, both exercised here:
  1. Every external sub-step the cycle makes (service_health write, 2FA lock
     check/acquire) is wrapped in ``_run_bounded`` and abandoned on timeout.
  2. ``main()`` arms a whole-cycle SIGALRM ceiling (``CYCLE_HARD_TIMEOUT_SECS``)
     that exits the process if any sub-step blocks past it.

Mocks follow the convention in ``test_ib_watchdog.py``: ``fetch_health`` and
``trigger_restart`` are patched at the module level, and we drive ``run_cycle``
deterministically.
"""

from __future__ import annotations

import signal
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

import ib_watchdog  # type: ignore[import-not-found]
from ib_watchdog import (  # type: ignore[import-not-found]
    CYCLE_HARD_TIMEOUT_SECS,
    GatewayState,
    WatchdogState,
    _arm_cycle_alarm,
    _run_bounded,
    _SubStepTimeout,
    run_cycle,
    save_state,
)


@pytest.fixture(autouse=True)
def _redirect_2fa_lock_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IB_2FA_LOCK_PATH", str(tmp_path / "ib-2fa-push-lock.json"))


@pytest.fixture
def state_path(tmp_path: Path) -> Path:
    return tmp_path / "watchdog-state.json"


def _healthy_payload() -> dict:
    return {
        "ib_gateway": {
            "service_state": "healthy",
            "port_listening": True,
            "upstream_dead": False,
            "auth_state": "authenticated",
        }
    }


# --- _run_bounded: the per-sub-step primitive -------------------------------


class TestRunBounded:
    def test_returns_value_when_fast(self):
        assert _run_bounded("fast", 1.0, lambda: 7) == 7

    def test_propagates_exception_from_fn(self):
        def boom():
            raise ValueError("nope")

        with pytest.raises(ValueError, match="nope"):
            _run_bounded("boom", 1.0, boom)

    def test_abandons_a_blocking_call_within_the_timeout(self):
        # A call that blocks far longer than its timeout must be abandoned
        # (raise _SubStepTimeout) quickly — never block for the full sleep.
        started = time.monotonic()
        with pytest.raises(_SubStepTimeout):
            _run_bounded("slow", 0.2, lambda: time.sleep(30))
        elapsed = time.monotonic() - started
        assert elapsed < 5, f"_run_bounded blocked {elapsed:.1f}s, expected ~0.2s"


# --- (a) health-probe path raises/times out -> cycle still EXITS ------------


class TestProbePathNeverHangs:
    def test_probe_failure_records_outcome_and_returns(self, state_path):
        # Simulate the exact incident: the health probe fails (timeout),
        # then the post-probe service_health write hangs. The cycle must
        # still return with a recorded outcome.
        def fake_fetch(url, timeout):
            return None  # probe timed out / unreachable

        def hanging_write(*args, **kwargs):
            time.sleep(30)  # libsql commit stuck on an unreachable backend

        with (
            patch("ib_watchdog.fetch_health", side_effect=fake_fetch),
            patch("ib_watchdog.probe_gateway_direct", return_value="unknown"),
            patch("ib_watchdog.attribute_api_down", return_value="attribution_unavailable"),
            patch("ib_watchdog._write_service_health", side_effect=hanging_write),
            patch.object(ib_watchdog, "SERVICE_HEALTH_WRITE_TIMEOUT_SECS", 0.2),
        ):
            started = time.monotonic()
            result = run_cycle(state_path=state_path, dry_run=True)
            elapsed = time.monotonic() - started

        assert result.last_outcome.startswith("probe_unreachable")
        assert elapsed < 5, f"cycle blocked {elapsed:.1f}s on a hung write"

    def test_service_health_write_timeout_does_not_raise(self, state_path):
        # record_service_health swallows the bounded-timeout and continues.
        def hanging_write(*args, **kwargs):
            time.sleep(30)

        with (
            patch("ib_watchdog._write_service_health", side_effect=hanging_write),
            patch.object(ib_watchdog, "SERVICE_HEALTH_WRITE_TIMEOUT_SECS", 0.2),
        ):
            started = time.monotonic()
            ib_watchdog.record_service_health("ok")  # must not raise / hang
            elapsed = time.monotonic() - started
        assert elapsed < 5


# --- (b) whole-cycle hard timeout aborts + exits ---------------------------


class TestWholeCycleHardTimeout:
    def test_alarm_is_armed_with_the_ceiling(self, monkeypatch):
        # _arm_cycle_alarm must install the abort handler AND schedule a
        # SIGALRM for the module ceiling. We capture the calls rather than
        # actually firing the alarm.
        installed = {}

        def fake_signal(sig, handler):
            installed["handler"] = handler
            installed["sig"] = sig

        def fake_alarm(secs):
            installed["alarm_secs"] = secs
            return 0

        monkeypatch.setattr(signal, "signal", fake_signal)
        monkeypatch.setattr(signal, "alarm", fake_alarm)

        assert _arm_cycle_alarm() is True
        assert installed["sig"] == signal.SIGALRM
        assert installed["handler"] is ib_watchdog._abort_on_cycle_timeout
        assert installed["alarm_secs"] == CYCLE_HARD_TIMEOUT_SECS

    def test_abort_handler_exits_the_process(self, monkeypatch):
        # The handler must EXIT (via os._exit) — not raise and risk being
        # swallowed by a broad except inside the cycle.
        exit_codes = []
        monkeypatch.setattr(ib_watchdog.os, "_exit", lambda code: exit_codes.append(code))
        ib_watchdog._abort_on_cycle_timeout(signal.SIGALRM, None)
        assert exit_codes == [2]

    def test_real_sigalrm_terminates_a_blocking_cycle(self, state_path, monkeypatch):
        # End-to-end: arm a real (very short) SIGALRM, then make the cycle
        # block. The handler must fire and "exit". We swap os._exit for an
        # exception so the test can assert termination without killing pytest.
        monkeypatch.setattr(ib_watchdog, "CYCLE_HARD_TIMEOUT_SECS", 1)

        class _Exited(BaseException):
            pass

        monkeypatch.setattr(
            ib_watchdog.os, "_exit", lambda code: (_ for _ in ()).throw(_Exited())
        )

        def blocking_fetch(url, timeout):
            time.sleep(30)  # block past the 1s ceiling

        armed = _arm_cycle_alarm()
        assert armed is True
        try:
            started = time.monotonic()
            with pytest.raises(_Exited):
                with patch("ib_watchdog.fetch_health", side_effect=blocking_fetch):
                    run_cycle(state_path=state_path, dry_run=True)
            elapsed = time.monotonic() - started
            assert elapsed < 10, f"alarm did not fire promptly ({elapsed:.1f}s)"
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, signal.SIG_DFL)

    def test_arm_returns_false_off_main_thread(self):
        # SIGALRM can only be armed on the main thread; arming from a worker
        # thread must degrade gracefully (sub-step bounds still protect us)
        # instead of crashing the cycle.
        results = []

        def worker():
            results.append(_arm_cycle_alarm())

        t = threading.Thread(target=worker)
        t.start()
        t.join(5)
        assert results == [False]


# --- (c) happy path still completes and records healthy --------------------


class TestHappyPathUnaffected:
    def test_healthy_cycle_records_healthy_and_returns(self, state_path):
        def fake_fetch(url, timeout):
            return GatewayState.from_health_payload(_healthy_payload())

        writes = []

        with (
            patch("ib_watchdog.fetch_health", side_effect=fake_fetch),
            patch("ib_watchdog.trigger_restart", return_value=True) as restart_mock,
            patch(
                "ib_watchdog._write_service_health",
                side_effect=lambda *a, **k: writes.append((a, k)),
            ),
        ):
            result = run_cycle(state_path=state_path, dry_run=True)

        assert result.degraded_count == 0
        assert "healthy" in result.last_outcome
        restart_mock.assert_not_called()
        assert writes, "healthy cycle should still emit a service_health heartbeat"

    def test_main_disarms_alarm_after_a_clean_cycle(self, state_path, monkeypatch):
        # main() must clear the alarm once the cycle finishes so a fast cycle
        # never leaves a stray SIGALRM pending into the next process.
        alarm_calls = []
        monkeypatch.setattr(signal, "alarm", lambda secs: alarm_calls.append(secs) or 0)
        monkeypatch.setattr(signal, "signal", lambda *a, **k: None)

        def fake_fetch(url, timeout):
            return GatewayState.from_health_payload(_healthy_payload())

        with (
            patch("ib_watchdog.fetch_health", side_effect=fake_fetch),
            patch("ib_watchdog.record_service_health"),
        ):
            rc = ib_watchdog.main(
                ["--dry-run", "--state-path", str(state_path)]
            )

        assert rc == 0
        assert CYCLE_HARD_TIMEOUT_SECS in alarm_calls  # armed
        assert alarm_calls[-1] == 0  # disarmed last


def test_watchdog_cannot_resurrect_replica_with_clean_env():
    """Regression: the watchdog unit shipped without
    Environment=RADON_DB_NO_REPLICA=1 while get_db() defaulted to the replica,
    so it ran conn.sync() against a multi-GB replica.db every cycle, hanging
    the oneshot. The fix is structural now — db.client defaults to
    direct-to-cloud and the replica is opt-in only (DUR-07) — so importing the
    watchdog with a completely clean env must leave the replica branch OFF.
    Run in a subprocess so CI-set env values can't mask a regression.
    """
    import os
    import subprocess
    import sys

    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("RADON_DB_NO_REPLICA", "RADON_DB_USE_REPLICA", "PYTEST_CURRENT_TEST")
    }
    code = (
        "import scripts.ib_watchdog;"
        "from scripts.db.client import _replica_opted_in;"
        "print(_replica_opted_in())"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=30,
        cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        env=env,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False", (
        f"expected the replica branch OFF after a clean-env watchdog import, "
        f"got {out.stdout!r}"
    )
