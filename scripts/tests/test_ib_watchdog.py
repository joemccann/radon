"""Unit tests for scripts/ib_watchdog.py.

Drives `run_cycle` deterministically with mocked /health responses
and verifies the counter increments / resets / restart behavior
without touching systemctl or the network.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ib_watchdog import (  # type: ignore[import-not-found]
    DEFAULT_THRESHOLD_CYCLES,
    GatewayState,
    WatchdogState,
    is_api_hang,
    load_state,
    run_cycle,
    save_state,
)


# --- Fixtures ---------------------------------------------------------------


@pytest.fixture(autouse=True)
def _redirect_2fa_lock_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect the cross-process 2FA push lock to a tmp file so tests
    do not touch the production /var/lib/radon path. The lock check was
    layered into run_cycle in 2026-05-19 incident response."""
    monkeypatch.setenv("IB_2FA_LOCK_PATH", str(tmp_path / "ib-2fa-push-lock.json"))


@pytest.fixture
def state_path(tmp_path: Path) -> Path:
    return tmp_path / "watchdog-state.json"


def _payload(
    *,
    service_state: str = "healthy",
    port_listening: bool = True,
    upstream_dead: bool = False,
    auth_state: str = "authenticated",
) -> dict:
    return {
        "ib_gateway": {
            "service_state": service_state,
            "port_listening": port_listening,
            "upstream_dead": upstream_dead,
            "auth_state": auth_state,
        }
    }


def _drive_cycle(state_path: Path, payload: dict | None, **kwargs):
    """Run a single cycle with the given /health payload mocked.

    ``payload=None`` simulates a probe failure (network error). The DUR-10
    direct gateway probe is pinned to "unknown" (ambiguous) so these tests
    keep exercising the conservative leave-state-alone path; the full
    fallback decision table lives in test_ib_watchdog_dur10.py.
    """

    def fake_fetch(url: str, timeout: float):
        if payload is None:
            return None
        return GatewayState.from_health_payload(payload)

    with (
        patch("ib_watchdog.fetch_health", side_effect=fake_fetch),
        patch("ib_watchdog.trigger_restart", return_value=True) as restart_mock,
        patch("ib_watchdog.record_service_health"),
        patch("ib_watchdog.probe_gateway_direct", return_value="unknown"),
        patch("ib_watchdog.attribute_api_down", return_value="attribution_unavailable"),
    ):
        result = run_cycle(state_path=state_path, dry_run=True, **kwargs)
    return result, restart_mock


# --- is_api_hang ------------------------------------------------------------


class TestIsApiHang:
    def test_healthy_state_is_not_a_hang(self):
        s = GatewayState("healthy", True, False, "authenticated")
        assert is_api_hang(s) is False

    def test_port_down_is_not_a_hang(self):
        # Container restart policy handles this — not our problem.
        s = GatewayState("unhealthy", False, True, "unreachable")
        assert is_api_hang(s) is False

    def test_awaiting_2fa_without_upstream_dead_is_not_a_hang(self):
        # Genuine stuck-2FA: container running + healthy, parked at the
        # prompt. upstream_dead is False; the stuck-2FA path handles it.
        s = GatewayState("unhealthy", True, False, "awaiting_2fa")
        assert is_api_hang(s) is False

    def test_awaiting_2fa_with_upstream_dead_IS_a_hang(self):
        # 2026-06-15 loop fix: a dead JVM acceptor (upstream_dead) whose
        # cached pool auth_state still reads awaiting_2fa is the api-hang,
        # NOT a 2FA problem. upstream_dead overrides auth_state — firing a
        # fresh push does nothing for a dead upstream and looped 15× before.
        s = GatewayState("unhealthy", True, True, "awaiting_2fa")
        assert is_api_hang(s) is True

    def test_port_open_but_upstream_dead_is_the_hang(self):
        s = GatewayState("unhealthy", True, True, "authenticated")
        assert is_api_hang(s) is True

    def test_unknown_auth_with_upstream_dead_still_a_hang(self):
        # Defensive: any non-2FA awaiting_* state with upstream_dead
        # should trip the watchdog. The point is "API not responding."
        s = GatewayState("unhealthy", True, True, "unknown")
        assert is_api_hang(s) is True


# --- run_cycle: healthy paths -----------------------------------------------


class TestHealthyCycle:
    def test_healthy_payload_keeps_counter_at_zero(self, state_path):
        result, restart = _drive_cycle(state_path, _payload())
        assert result.degraded_count == 0
        assert "healthy" in result.last_outcome
        restart.assert_not_called()

    def test_healthy_payload_resets_existing_counter(self, state_path):
        save_state(state_path, WatchdogState(degraded_count=2))
        result, restart = _drive_cycle(state_path, _payload())
        assert result.degraded_count == 0
        restart.assert_not_called()

    def test_awaiting_2fa_resets_api_hang_counter_and_starts_stuck_counter(self, state_path):
        # awaiting_2fa is NOT an api-hang — the api-hang counter must reset.
        # But awaiting_2fa with no push lock and no scheduled retry IS the
        # stuck-2FA failure mode — that counter must start incrementing so
        # the threshold trips a fresh push after 3 cycles.
        # NOTE: a genuine stuck-2FA has upstream_dead=False (container running
        # + healthy, parked at the prompt). upstream_dead=True is the JVM
        # acceptor hang → is_api_hang owns it (2026-06-15 loop fix).
        save_state(state_path, WatchdogState(degraded_count=2))
        result, restart = _drive_cycle(
            state_path,
            _payload(
                service_state="unhealthy",
                upstream_dead=False,
                auth_state="awaiting_2fa",
            ),
        )
        assert result.degraded_count == 0
        assert result.stuck_2fa_count == 1
        restart.assert_not_called()  # threshold is 3, not yet hit


# --- run_cycle: degraded paths ----------------------------------------------


class TestDegradedCycle:
    def _hang_payload(self) -> dict:
        return _payload(
            service_state="unhealthy",
            upstream_dead=True,
            auth_state="authenticated",
        )

    def test_first_hang_increments_to_one(self, state_path):
        result, restart = _drive_cycle(state_path, self._hang_payload())
        assert result.degraded_count == 1
        restart.assert_not_called()

    def test_two_consecutive_hangs_do_not_trigger_restart(self, state_path):
        # 3-cycle threshold: two should still be under.
        save_state(state_path, WatchdogState(degraded_count=1))
        result, restart = _drive_cycle(state_path, self._hang_payload())
        assert result.degraded_count == 2
        restart.assert_not_called()

    def test_third_consecutive_hang_triggers_restart(self, state_path):
        save_state(state_path, WatchdogState(degraded_count=2))
        result, restart = _drive_cycle(state_path, self._hang_payload())
        assert restart.call_count == 1
        # Counter resets so we don't restart again on next cycle
        # while the gateway is mid-restart.
        assert result.degraded_count == 0
        assert "restarted" in result.last_outcome

    def test_custom_threshold_overrides_default(self, state_path):
        save_state(state_path, WatchdogState(degraded_count=0))
        # Threshold 1 = restart on the FIRST hang. Useful as escape
        # hatch / testing only.
        result, restart = _drive_cycle(
            state_path,
            self._hang_payload(),
            threshold=1,
        )
        assert restart.call_count == 1
        assert result.degraded_count == 0

    def test_hang_followed_by_healthy_resets_counter(self, state_path):
        # Cycle 1: hang. Counter -> 1.
        _, _ = _drive_cycle(state_path, self._hang_payload())
        # Cycle 2: healthy. Counter -> 0.
        result, restart = _drive_cycle(state_path, _payload())
        assert result.degraded_count == 0
        restart.assert_not_called()


# --- run_cycle: probe failures ---------------------------------------------


class TestProbeFailure:
    def test_probe_failure_does_not_increment_counter(self, state_path):
        # FastAPI is down / unreachable — we can't tell what state
        # the gateway is in. Conservative: leave counter alone.
        save_state(state_path, WatchdogState(degraded_count=2))
        result, restart = _drive_cycle(state_path, None)
        assert result.degraded_count == 2  # unchanged
        restart.assert_not_called()
        assert result.last_outcome.startswith("probe_unreachable")

    def test_probe_failure_does_not_trigger_restart_even_at_threshold(self, state_path):
        # Belt-and-suspenders: even if we were one cycle from
        # restart, a probe failure should not push us over.
        save_state(state_path, WatchdogState(degraded_count=DEFAULT_THRESHOLD_CYCLES - 1))
        result, restart = _drive_cycle(state_path, None)
        restart.assert_not_called()
        assert result.degraded_count == DEFAULT_THRESHOLD_CYCLES - 1


# --- state persistence -----------------------------------------------------


class TestStatePersistence:
    def test_state_round_trips_through_disk(self, state_path):
        save_state(state_path, WatchdogState(degraded_count=5, last_outcome="x"))
        loaded = load_state(state_path)
        assert loaded.degraded_count == 5
        assert loaded.last_outcome == "x"

    def test_missing_state_file_returns_default(self, state_path):
        assert not state_path.exists()
        s = load_state(state_path)
        assert s.degraded_count == 0
        assert s.last_outcome == "init"

    def test_corrupt_state_file_resets_silently(self, state_path):
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text("{garbage")
        s = load_state(state_path)
        assert s.degraded_count == 0

    def test_save_is_atomic_via_replace(self, state_path):
        # Saving twice should leave only the latest state on disk.
        save_state(state_path, WatchdogState(degraded_count=1))
        save_state(state_path, WatchdogState(degraded_count=2))
        with state_path.open() as fh:
            data = json.load(fh)
        assert data["degraded_count"] == 2
