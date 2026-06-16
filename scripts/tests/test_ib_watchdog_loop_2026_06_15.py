"""Regression tests for the 2026-06-15 watchdog self-heal restart loop.

Incident: the IB Gateway's Java API listener wedged (``upstream_dead=True``,
port still open, auth still cached at a non-authenticated value). A radon-api
restart built a fresh ib_pool that could NOT connect to :4001 (TimeoutError —
dead upstream). The watchdog's ``is_stuck_awaiting_2fa`` MISCLASSIFIED that
never-connecting pool as "stuck awaiting 2FA"; its recovery (restart gateway →
fire a fresh IBKR 2FA push) does nothing for a dead upstream, so it looped
every ~12 min: 15 gateway restarts (each a real 2FA re-auth + an hour-long
Pushover EMERGENCY) across 20:36-23:30.

The four fixes these tests pin down:
  1. ``upstream_dead=True`` is ALWAYS the api-hang, regardless of auth_state —
     never the stuck-2FA path. The two can never both restart in one cycle.
  2. Even a genuine stuck-2FA (``upstream_dead=False``) is capped at <=2
     watchdog gateway restarts per rolling hour, with exponential backoff, so
     it can NEVER loop 15×.
  3. After a watchdog-initiated gateway restart resolves auth back to
     authenticated, radon-api is bounced exactly ONCE to un-stick the ib_pool.
  4. The 2FA push-lock discipline is preserved (no stacking).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import ib_watchdog  # type: ignore[import-not-found]
from ib_watchdog import (  # type: ignore[import-not-found]
    GatewayState,
    WatchdogState,
    is_api_hang,
    is_stuck_awaiting_2fa,
    load_state,
    run_cycle,
    save_state,
)
from utils import ib_2fa_lock


@pytest.fixture(autouse=True)
def _redirect_2fa_lock_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    push_lock_holder: str | None = None,
    next_attempt_in_secs: float = 0.0,
    attempt_count: int = 0,
) -> dict:
    push_lock = (
        {"holder": push_lock_holder, "remaining_secs": 500}
        if push_lock_holder
        else None
    )
    return {
        "ib_gateway": {
            "service_state": service_state,
            "port_listening": port_listening,
            "upstream_dead": upstream_dead,
            "auth_state": auth_state,
            "restart_backoff": {
                "attempt_count": attempt_count,
                "next_attempt_in_secs": next_attempt_in_secs,
                "push_lock": push_lock,
            },
        }
    }


# The dead-upstream pool: the JVM acceptor is wedged, the pool can't connect,
# and its cached auth_state still reads awaiting_2fa — the exact misclassified
# state from 2026-06-15.
def _dead_upstream_awaiting_2fa() -> dict:
    return _payload(
        service_state="unhealthy", upstream_dead=True, auth_state="awaiting_2fa"
    )


def _drive(
    state_path: Path,
    payload: dict | None,
    *,
    restart_ok: bool = True,
    clock=None,
    **kwargs,
):
    def fake_fetch(url: str, timeout: float):
        if payload is None:
            return None
        return GatewayState.from_health_payload(payload)

    with (
        patch("ib_watchdog.fetch_health", side_effect=fake_fetch),
        patch("ib_watchdog.trigger_restart", return_value=restart_ok) as restart_mock,
        patch("ib_watchdog.record_service_health"),
        patch("ib_watchdog.probe_gateway_direct", return_value="unknown"),
        patch("ib_watchdog.attribute_api_down", return_value="attribution_unavailable"),
    ):
        if clock is not None:
            kwargs["clock"] = clock
        result = run_cycle(state_path=state_path, dry_run=True, **kwargs)
    return result, restart_mock


# --- FIX 1: dead upstream is api-hang, never stuck-2FA -----------------------


class TestDeadUpstreamIsApiHangNotStuck2fa:
    def test_classifier_dead_upstream_awaiting_2fa_is_api_hang(self):
        s = GatewayState("unhealthy", True, True, "awaiting_2fa")
        assert is_api_hang(s) is True
        assert is_stuck_awaiting_2fa(s) is False

    def test_classifier_genuine_stuck_2fa_is_not_api_hang(self):
        s = GatewayState("unhealthy", True, False, "awaiting_2fa")
        assert is_api_hang(s) is False
        assert is_stuck_awaiting_2fa(s) is True

    def test_cycle_routes_dead_upstream_to_api_hang_counter(self, state_path):
        # Counter advanced is degraded_count (api-hang), NOT stuck_2fa_count.
        result, restart = _drive(state_path, _dead_upstream_awaiting_2fa())
        assert result.degraded_count == 1
        assert result.stuck_2fa_count == 0
        restart.assert_not_called()  # threshold not yet hit

    def test_dead_upstream_restart_is_via_api_hang_ladder_at_threshold(self, state_path):
        save_state(state_path, WatchdogState(degraded_count=2))
        result, restart = _drive(state_path, _dead_upstream_awaiting_2fa())
        assert restart.call_count == 1
        # api-hang outcome, not stuck_2fa.
        assert "restarted" in result.last_outcome
        assert "stuck_2fa" not in result.last_outcome
        assert result.degraded_count == 0


# --- The loop: sustained dead-upstream must NOT loop -------------------------


class TestSustainedDeadUpstreamDoesNotLoop:
    def test_dead_upstream_drives_at_most_one_gateway_restart_per_episode(self, state_path):
        """The 2026-06-15 signature: dead upstream every cycle. The api-hang
        ladder restarts ONCE at threshold, then resets and re-warms — it does
        NOT fire a gateway restart every single cycle. Over many cycles the
        gateway restart count stays bounded, not 15×."""
        t = [1000.0]

        def clock():
            t[0] += 60
            return t[0]

        total_restarts = 0
        # 20 cycles of unbroken dead-upstream (the incident ran ~3h = ~180
        # cycles, but 20 is enough to prove the per-episode ceiling).
        for _ in range(20):
            _, restart = _drive(
                state_path, _dead_upstream_awaiting_2fa(), clock=clock
            )
            total_restarts += restart.call_count
        # 20 cycles / 3-cycle threshold ≈ 6 restarts MAX — and critically each
        # restart arms the pool-reconnect + resets the counter, never the
        # every-cycle 15× cadence the loop produced.
        assert total_restarts <= 7, total_restarts
        # No stuck-2FA push was ever fired down the misclassified path.
        st = load_state(state_path)
        assert st.stuck_2fa_count == 0


# --- FIX 2: stuck-2FA restart cap (<=2/hour, exponential backoff) ------------


class TestStuck2faRestartCap:
    def _genuine_stuck(self) -> dict:
        return _payload(
            service_state="unhealthy", upstream_dead=False, auth_state="awaiting_2fa"
        )

    def test_two_restarts_allowed_then_third_blocked_within_hour(self, state_path):
        # Manufacture a history of two restarts inside the last hour. A third
        # stuck-2FA threshold hit must be BLOCKED by the cap.
        now = 100_000.0
        save_state(
            state_path,
            WatchdogState(
                stuck_2fa_count=2,  # one more increment hits threshold 3
                stuck_2fa_restart_history=[now - 1800, now - 600],
            ),
        )
        result, restart = _drive(
            state_path, self._genuine_stuck(), clock=lambda: now
        )
        restart.assert_not_called()
        assert result.last_outcome.startswith("stuck_2fa_cap")
        # Counter held at threshold so it acts the instant the cap clears.
        assert result.stuck_2fa_count == 3

    def test_backoff_blocks_second_restart_too_soon(self, state_path):
        # One restart 60s ago; backoff base is 300s → a second is blocked.
        now = 100_000.0
        save_state(
            state_path,
            WatchdogState(
                stuck_2fa_count=2,
                stuck_2fa_restart_history=[now - 60],
            ),
        )
        result, restart = _drive(
            state_path, self._genuine_stuck(), clock=lambda: now
        )
        restart.assert_not_called()
        assert result.last_outcome.startswith("stuck_2fa_backoff")

    def test_second_restart_allowed_after_backoff_elapsed(self, state_path):
        # One restart 400s ago (> 300s base backoff) and only one in the hour
        # → a second restart is permitted.
        now = 100_000.0
        save_state(
            state_path,
            WatchdogState(
                stuck_2fa_count=2,
                stuck_2fa_restart_history=[now - 400],
            ),
        )
        result, restart = _drive(
            state_path, self._genuine_stuck(), clock=lambda: now
        )
        restart.assert_called_once()
        assert "stuck_2fa_push_fired" in result.last_outcome
        assert len(result.stuck_2fa_restart_history) == 2

    def test_sustained_genuine_stuck_2fa_caps_at_two_restarts_per_hour(self, state_path):
        """Drive an unbroken genuine-stuck-2FA condition for an hour of cycles
        and assert the watchdog fires at most 2 gateway restarts — never the
        15× loop. The clock is stable WITHIN a cycle (matching wall-clock
        ``time.time``, which barely moves across a single oneshot) and advances
        60s BETWEEN cycles."""
        now = [1000.0]

        def cycle_clock():
            return now[0]

        restarts = 0
        # 60 cycles spanning exactly one rolling-hour window.
        for _ in range(60):
            _, restart = _drive(
                state_path, self._genuine_stuck(), clock=cycle_clock
            )
            restarts += restart.call_count
            now[0] += 60  # advance one cycle
        assert restarts <= ib_watchdog.STUCK_2FA_MAX_RESTARTS_PER_HOUR, restarts


# --- FIX 3: pool reconnect after recovery ------------------------------------


class TestPoolReconnectAfterRecovery:
    def test_gateway_restart_arms_pending_pool_reconnect(self, state_path):
        save_state(state_path, WatchdogState(degraded_count=2))
        result, _ = _drive(state_path, _dead_upstream_awaiting_2fa())
        assert result.pending_pool_reconnect is True

    def test_authenticated_after_restart_bounces_api_once(self, state_path):
        # Cycle 1: dead upstream at threshold → gateway restart, flag armed.
        save_state(state_path, WatchdogState(degraded_count=2))
        _drive(state_path, _dead_upstream_awaiting_2fa())
        assert load_state(state_path).pending_pool_reconnect is True

        # Cycle 2: auth resolved → exactly one radon-api restart, flag cleared.
        result, restart = _drive(
            state_path, _payload(auth_state="authenticated"),
            api_unit="radon-api.service",
        )
        restart.assert_called_once()
        assert restart.call_args[0][0] == "radon-api.service"
        assert result.pending_pool_reconnect is False
        assert "pool_reconnect" in result.last_outcome

    def test_api_reconnect_is_one_shot_not_a_loop(self, state_path):
        save_state(
            state_path, WatchdogState(pending_pool_reconnect=True)
        )
        # First healthy/authenticated cycle bounces api once.
        _, restart1 = _drive(state_path, _payload(auth_state="authenticated"))
        restart1.assert_called_once()
        # Second healthy cycle must NOT bounce api again — flag is cleared.
        _, restart2 = _drive(state_path, _payload(auth_state="authenticated"))
        restart2.assert_not_called()

    def test_no_api_bounce_while_still_awaiting_2fa(self, state_path):
        # Flag armed but auth not yet resolved: do NOT bounce api prematurely.
        save_state(
            state_path,
            WatchdogState(pending_pool_reconnect=True, stuck_2fa_count=0),
        )
        result, restart = _drive(
            state_path,
            _payload(
                service_state="unhealthy",
                upstream_dead=False,
                auth_state="awaiting_2fa",
                push_lock_holder="scripts.api.ib_gateway.restart_ib_gateway",
            ),
        )
        restart.assert_not_called()
        assert load_state(state_path).pending_pool_reconnect is True


# --- FIX 4: push-lock discipline preserved -----------------------------------


class TestPushLockPreserved:
    def test_dead_upstream_at_threshold_still_respects_push_lock(self, state_path):
        # The api-hang ladder (which now owns dead-upstream) must still defer
        # when another holder has the 2FA push lock — no stacking.
        ib_2fa_lock.acquire_2fa_push_lock(
            "scripts.api.ib_gateway.restart_ib_gateway",
            ttl_secs=600,
            reason="user-initiated",
        )
        save_state(state_path, WatchdogState(degraded_count=2))
        result, restart = _drive(state_path, _dead_upstream_awaiting_2fa())
        restart.assert_not_called()
        assert "2fa_push_in_flight" in result.last_outcome

    def test_pool_reconnect_fires_no_2fa_push(self, state_path):
        # Bouncing radon-api must NOT touch the 2FA push lock — it fires no
        # IBKR push (it restarts the API service, not the gateway).
        assert ib_2fa_lock.check_2fa_push_lock() is None
        save_state(state_path, WatchdogState(pending_pool_reconnect=True))
        _drive(state_path, _payload(auth_state="authenticated"))
        assert ib_2fa_lock.check_2fa_push_lock() is None
