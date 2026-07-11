"""Regression tests for the 2026-07-05 watchdog 2FA push storm.

Incident: IBC AutoRestartTime=09:05 UTC fired on the day IB's weekly session
token had expired -> full login -> 2FA push at 02:05 PT (operator asleep, push
expired, gateway parked at ``auth_state=awaiting_2fa`` with a dead upstream).
The watchdog's hang classifier saw ``port=True upstream_dead=True
auth=awaiting_2fa``, called it an api-hang, and restarted the gateway — a cold
start whose FULL login minted a NEW push that invalidated the prior one. The
ladder re-armed every 3 fresh cycles (degraded_count resets to 0 on every
restart), so it looped every ~6 min: 48 restarts / 48 pushes 09:32->14:12 UTC.

The three fixes these tests pin down:
  1. ``awaiting_2fa`` NEVER counts toward the api-hang ladder: the watchdog
     stands down (no restart regardless of push-lock state, hang counter
     resets so a pre-2FA streak can't resume post-approval), emits ONE
     service_health alert per awaiting-2FA episode, and resumes normal
     monitoring only after auth leaves ``awaiting_2fa``.
  2. Bounded remediation on the api-hang ladder itself: consecutive
     watchdog-initiated restarts back off exponentially (6 min base,
     doubling, 1h cap) with a hard cap of 3 without an intervening
     recovery to ``authenticated``; past the cap it alerts and stands down.
     Attempt state persists in the state file (oneshot process model).
  3. IBC AutoRestartTime is disabled entirely because it cannot acquire the
     shared lease. The only default quiet window covers server-session rollover.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import ib_watchdog  # type: ignore[import-not-found]
from ib_watchdog import (  # type: ignore[import-not-found]
    GatewayState,
    WatchdogState,
    is_api_hang,
    is_stuck_awaiting_2fa,
    load_state,
    quiet_window_active,
    run_cycle,
    save_state,
)
from utils import ib_2fa_lock


@pytest.fixture(autouse=True)
def _redirect_2fa_lock_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IB_2FA_LOCK_PATH", str(tmp_path / "ib-2fa-push-lock.json"))


@pytest.fixture(autouse=True)
def _assume_market_open(monkeypatch: pytest.MonkeyPatch) -> None:
    # These tests exercise restart LOGIC; pin the market-hours gate open so a
    # weekend/off-hours run can't freeze restarts and mask the assertions.
    monkeypatch.setattr("ib_watchdog.data_plane_window_active", lambda *a, **k: True)


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


def _parked_awaiting_2fa() -> dict:
    """The incident signature: gateway parked at the 2FA push prompt after a
    full login — port up (socat), upstream dead, auth awaiting_2fa."""
    return _payload(
        service_state="unhealthy", upstream_dead=True, auth_state="awaiting_2fa"
    )


def _authenticated_hang() -> dict:
    """The genuine JVM acceptor hang the api-hang ladder exists for."""
    return _payload(
        service_state="unhealthy", upstream_dead=True, auth_state="authenticated"
    )


def _drive(state_path: Path, payload: dict | None, *, clock=None, **kwargs):
    def fake_fetch(url: str, timeout: float):
        if payload is None:
            return None
        return GatewayState.from_health_payload(payload)

    health_mock = MagicMock()
    with (
        patch("ib_watchdog.fetch_health", side_effect=fake_fetch),
        patch("ib_watchdog.trigger_restart", return_value=True) as restart_mock,
        patch("ib_watchdog.record_service_health", health_mock),
        patch("ib_watchdog.probe_gateway_direct", return_value="unknown"),
        patch("ib_watchdog.attribute_api_down", return_value="attribution_unavailable"),
    ):
        if clock is not None:
            kwargs["clock"] = clock
        result = run_cycle(state_path=state_path, dry_run=True, **kwargs)
    return result, restart_mock, health_mock


# --- FIX 1: awaiting_2fa stands the watchdog down -----------------------------


class TestAwaiting2faStandDown:
    def test_classifier_awaiting_2fa_is_neither_hang_nor_stuck(self):
        # 2026-07-05 policy reversal of the 2026-06-15 override: awaiting_2fa
        # with a dead upstream belongs to NO restart ladder. The stand-down
        # owns it (restarting mints a NEW push and invalidates the pending
        # one); the stuck-2FA path still requires upstream_dead=False so the
        # 2026-06-15 dead-JVM misclassification stays fixed too.
        s = GatewayState("unhealthy", True, True, "awaiting_2fa")
        assert is_api_hang(s) is False
        assert is_stuck_awaiting_2fa(s) is False

    def test_stand_down_resets_hang_counter_and_never_restarts(self, state_path):
        save_state(state_path, WatchdogState(degraded_count=2))
        result, restart, _ = _drive(state_path, _parked_awaiting_2fa())
        restart.assert_not_called()
        assert result.degraded_count == 0
        assert result.stuck_2fa_count == 0
        assert result.last_outcome == "standing_down_awaiting_2fa"

    def test_incident_many_ticks_zero_restarts_error_heartbeat_every_cycle(self, state_path):
        """Model the incident tail: the push has EXPIRED (no live lock), the
        gateway sits at awaiting_2fa with a dead upstream for hours of 60s
        oneshot ticks. The watchdog must fire ZERO restarts while refreshing
        the error heartbeat every cycle so a live watchdog is not classified
        as a stale writer. Notification deduplication belongs downstream."""
        ib_2fa_lock.acquire_2fa_push_lock(
            "scripts.api.ib_gateway.restart_ib_gateway",
            ttl_secs=1,
            reason="expired push from the 02:05 PT full login",
            now=1_000.0,  # long expired by the time the cycles run
        )
        t = [100_000.0]

        def clock():
            return t[0]

        total_restarts = 0
        alerts = []
        for _ in range(48):
            _, restart, health = _drive(
                state_path, _parked_awaiting_2fa(), clock=clock
            )
            total_restarts += restart.call_count
            alerts.extend(health.call_args_list)
            t[0] += 60
        assert total_restarts == 0
        assert len(alerts) == 48, alerts
        for args, kwargs in alerts:
            assert args[0] == "error"
            message = kwargs.get("error_message") or (
                args[1] if len(args) > 1 else ""
            )
            assert "2FA" in message

    def test_alert_rearms_after_auth_leaves_awaiting_2fa(self, state_path):
        # Episode 1: one alert.
        _, _, health1 = _drive(state_path, _parked_awaiting_2fa())
        assert health1.call_count == 1
        # Auth resolves -> episode over.
        _drive(state_path, _payload(auth_state="authenticated"))
        # Episode 2: a fresh awaiting-2FA observation alerts again.
        _, _, health2 = _drive(state_path, _parked_awaiting_2fa())
        assert any(args[0] == "error" for args, _ in health2.call_args_list)

    def test_pre_2fa_hang_streak_cannot_resume_after_resolution(self, state_path):
        # Two authenticated-hang cycles, then the gateway parks at 2FA. The
        # streak must RESET — the first post-resolution hang cycle starts a
        # fresh episode at 1, not restart immediately at 3.
        _drive(state_path, _authenticated_hang())
        _drive(state_path, _authenticated_hang())
        assert load_state(state_path).degraded_count == 2
        _drive(state_path, _parked_awaiting_2fa())
        assert load_state(state_path).degraded_count == 0
        result, restart, _ = _drive(state_path, _authenticated_hang())
        restart.assert_not_called()
        assert result.degraded_count == 1

    def test_stand_down_state_round_trips_the_state_file(self, state_path):
        save_state(
            state_path,
            WatchdogState(awaiting_2fa_alerted=True, api_hang_restart_count=2),
        )
        loaded = load_state(state_path)
        assert loaded.awaiting_2fa_alerted is True
        assert loaded.api_hang_restart_count == 2


# --- FIX 2: bounded api-hang remediation --------------------------------------


class TestApiHangBoundedRemediation:
    def test_backoff_blocks_immediate_second_restart(self, state_path):
        now = 200_000.0
        save_state(
            state_path,
            WatchdogState(
                degraded_count=2,
                api_hang_restart_count=1,
                last_restart_at=now - 60,  # 60s ago < 360s base backoff
            ),
        )
        result, restart, _ = _drive(
            state_path, _authenticated_hang(), clock=lambda: now
        )
        restart.assert_not_called()
        assert result.last_outcome.startswith("api_hang_backoff")
        # Held at threshold so the first post-backoff cycle acts immediately.
        assert result.degraded_count == 3

    def test_second_restart_allowed_after_backoff_elapsed(self, state_path):
        now = 200_000.0
        save_state(
            state_path,
            WatchdogState(
                degraded_count=2,
                api_hang_restart_count=1,
                last_restart_at=now - 400,  # > 360s base backoff
            ),
        )
        result, restart, _ = _drive(
            state_path, _authenticated_hang(), clock=lambda: now
        )
        restart.assert_called_once()
        assert result.api_hang_restart_count == 2

    def test_backoff_doubles_before_third_restart(self, state_path):
        now = 200_000.0
        save_state(
            state_path,
            WatchdogState(
                degraded_count=2,
                api_hang_restart_count=2,
                last_restart_at=now - 400,  # < 720s doubled backoff
            ),
        )
        result, restart, _ = _drive(
            state_path, _authenticated_hang(), clock=lambda: now
        )
        restart.assert_not_called()
        assert result.last_outcome.startswith("api_hang_backoff")

        save_state(
            state_path,
            WatchdogState(
                degraded_count=2,
                api_hang_restart_count=2,
                last_restart_at=now - 800,  # > 720s doubled backoff
            ),
        )
        result, restart, _ = _drive(
            state_path, _authenticated_hang(), clock=lambda: now
        )
        restart.assert_called_once()
        assert result.api_hang_restart_count == 3

    def test_cap_of_three_then_alert_and_stand_down(self, state_path):
        now = 200_000.0
        save_state(
            state_path,
            WatchdogState(
                degraded_count=2,
                api_hang_restart_count=3,
                last_restart_at=now - 100_000,  # backoff long elapsed — cap rules
            ),
        )
        result, restart, health = _drive(
            state_path, _authenticated_hang(), clock=lambda: now
        )
        restart.assert_not_called()
        assert result.last_outcome.startswith("api_hang_restart_cap")
        assert any(args[0] == "error" for args, _ in health.call_args_list)

    def test_cap_resets_on_recovery_to_authenticated(self, state_path):
        save_state(state_path, WatchdogState(api_hang_restart_count=3))
        result, _, _ = _drive(state_path, _payload(auth_state="authenticated"))
        assert result.api_hang_restart_count == 0

    def test_incident_cadence_capped_at_three_restarts(self, state_path):
        """The incident cadence, re-run against the genuine api-hang the
        ladder exists for: an unbroken authenticated dead-upstream across
        ~4h of 60s oneshot ticks (state persisted between ticks, fresh
        run_cycle each — the oneshot process model). Backoff spaces the
        restarts out (6 min, then 12 min) and the hard cap stops everything
        at 3 — never the ~48 the fixed 6-min cadence produced."""
        t = [1_000_000.0]

        def clock():
            return t[0]

        total_restarts = 0
        for _ in range(240):
            _, restart, _ = _drive(
                state_path, _authenticated_hang(), clock=clock
            )
            total_restarts += restart.call_count
            t[0] += 60
        assert total_restarts == 3, total_restarts
        assert load_state(state_path).api_hang_restart_count == 3

    def test_recovery_re_arms_the_ladder(self, state_path):
        # Capped out, then the gateway recovers to authenticated: the next
        # hang episode gets a fresh set of 3 bounded restarts.
        save_state(
            state_path,
            WatchdogState(api_hang_restart_count=3, degraded_count=0),
        )
        _drive(state_path, _payload(auth_state="authenticated"))
        assert load_state(state_path).api_hang_restart_count == 0
        for _ in range(2):
            _drive(state_path, _authenticated_hang(), clock=lambda: 300_000.0)
        result, restart, _ = _drive(
            state_path, _authenticated_hang(), clock=lambda: 300_000.0
        )
        restart.assert_called_once()
        assert result.api_hang_restart_count == 1


# --- FIX 3: unmanaged IBC schedule removed -----------------------------------


def _utc(hour: int, minute: int) -> datetime:
    return datetime(2026, 7, 5, hour, minute, tzinfo=timezone.utc)


class TestQuietWindowExcludesRemovedIbcRestart:
    @pytest.fixture(autouse=True)
    def _default_windows(self, monkeypatch):
        # conftest neutralizes the windows for determinism; pin the shipped
        # default spec back explicitly.
        monkeypatch.setenv(
            ib_watchdog.QUIET_WINDOWS_ENV, ib_watchdog.DEFAULT_QUIET_WINDOWS_UTC
        )

    def test_default_spec_is_session_rollover_only(self):
        assert ib_watchdog.DEFAULT_QUIET_WINDOWS_UTC == "23:40-00:15"

    def test_removed_auto_restart_time_is_not_suppressed(self):
        assert quiet_window_active(_utc(22, 35)) is False
        assert quiet_window_active(_utc(22, 30)) is False
        assert quiet_window_active(_utc(23, 4)) is False

    def test_session_rollover_window_kept(self):
        assert quiet_window_active(_utc(23, 45)) is True
        assert quiet_window_active(_utc(0, 10)) is True

    def test_old_morning_window_is_gone(self):
        # 09:05 UTC = 02:05 PT — the slot that stacked pushes overnight.
        assert quiet_window_active(_utc(9, 5)) is False
        assert quiet_window_active(_utc(9, 15)) is False

    def test_window_edges(self):
        assert quiet_window_active(_utc(22, 29)) is False
        assert quiet_window_active(_utc(23, 5)) is False
        assert quiet_window_active(_utc(23, 20)) is False
