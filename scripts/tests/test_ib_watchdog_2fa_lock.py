"""``ib_watchdog`` must consult the shared 2FA push lock before
issuing ``systemctl restart radon-ib-gateway.service``.

The bug this guards against (incident 2026-05-19): the user manually
ran ``radon restart`` at ~14:10 UTC. The IB Gateway came up at
``awaiting_2fa`` while the user was approving the 2FA push on their
phone. ~2 minutes later the watchdog detected ``upstream_dead=True``
(the API thread is hung at the IBC 2FA dialog) for the 3rd consecutive
cycle and triggered a SECOND container restart. That second restart
fired a SECOND 2FA push, putting IBKR's backend in a stacked-push
state where every approval was reported "unsuccessful".

These tests pin down the new behavior: while the FastAPI restart path
holds the 2FA push lock (i.e. the user is approving a push fired by
``restart_ib_gateway``), the watchdog MUST NOT trigger another restart
— even if it sees the api-hang signature.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from ib_watchdog import (  # type: ignore[import-not-found]
    GatewayState,
    WatchdogState,
    run_cycle,
    save_state,
)
from utils import ib_2fa_lock


@pytest.fixture(autouse=True)
def _redirect_lock_path(tmp_path, monkeypatch):
    monkeypatch.setenv("IB_2FA_LOCK_PATH", str(tmp_path / "ib-2fa-push-lock.json"))


@pytest.fixture
def state_path(tmp_path: Path) -> Path:
    return tmp_path / "watchdog-state.json"


def _hang_payload() -> dict:
    """The api-hang signature: port up, upstream dead, auth says
    authenticated (cached pool state from before the hang)."""
    return {
        "ib_gateway": {
            "service_state": "unhealthy",
            "port_listening": True,
            "upstream_dead": True,
            "auth_state": "authenticated",
        }
    }


def _drive(state_path: Path, payload: dict | None, **kwargs):
    def fake_fetch(url: str, timeout: float):
        if payload is None:
            return None
        return GatewayState.from_health_payload(payload)

    with (
        patch("ib_watchdog.fetch_health", side_effect=fake_fetch),
        patch("ib_watchdog.trigger_restart", return_value=True) as restart_mock,
        patch("ib_watchdog.record_service_health"),
    ):
        result = run_cycle(state_path=state_path, dry_run=True, **kwargs)
    return result, restart_mock


# --- Lock held → no restart ------------------------------------------------


def test_watchdog_refuses_restart_when_2fa_lock_held(state_path):
    """Watchdog at threshold + another holder mid-2FA-push = NO restart.
    This is the scenario from incident 2026-05-19."""
    ok, _ = ib_2fa_lock.acquire_2fa_push_lock(
        "scripts.api.ib_gateway.restart_ib_gateway",
        ttl_secs=600,
        reason="user-initiated restart, awaiting 2FA approval",
    )
    assert ok is True

    # Drive the watchdog right up to threshold-1 then hit the hang again.
    save_state(state_path, WatchdogState(degraded_count=2))
    result, restart_mock = _drive(state_path, _hang_payload())

    restart_mock.assert_not_called()
    assert "2fa_push_in_flight" in result.last_outcome
    # Counter is NOT reset — the hang is still real, we just can't act on it.
    # Once the lock clears, the watchdog should fire on the next cycle.
    assert result.degraded_count == 3


def test_watchdog_does_not_advance_lock_holder_clock(state_path):
    """The watchdog must NOT touch (acquire/refresh) the lock itself —
    that would let the watchdog stall the FastAPI-held lock indefinitely.
    The watchdog only READS the lock state."""
    holder = "scripts.api.ib_gateway.restart_ib_gateway"
    ok, original = ib_2fa_lock.acquire_2fa_push_lock(holder, ttl_secs=600)
    assert ok is True
    assert original is not None

    save_state(state_path, WatchdogState(degraded_count=2))
    _drive(state_path, _hang_payload())

    held = ib_2fa_lock.check_2fa_push_lock()
    assert held is not None
    assert held.holder == holder
    assert held.expires_at == original.expires_at  # unchanged


# --- Lock free → restart fires (regression check) --------------------------


def test_watchdog_fires_restart_when_no_lock_is_held(state_path):
    """When the lock is free, the original watchdog behavior must remain:
    3 consecutive hang cycles → systemctl restart."""
    assert ib_2fa_lock.check_2fa_push_lock() is None  # baseline
    save_state(state_path, WatchdogState(degraded_count=2))
    result, restart_mock = _drive(state_path, _hang_payload())
    restart_mock.assert_called_once()
    assert "restarted" in result.last_outcome


def test_watchdog_acquires_lock_when_it_triggers_a_restart(state_path):
    """A watchdog-driven restart fires a 2FA push too — the watchdog
    must HOLD the lock during that push so the FastAPI restart path
    (or any subsequent watchdog cycle) doesn't fire another."""
    assert ib_2fa_lock.check_2fa_push_lock() is None
    save_state(state_path, WatchdogState(degraded_count=2))
    _drive(state_path, _hang_payload())

    held = ib_2fa_lock.check_2fa_push_lock()
    assert held is not None
    assert "ib_watchdog" in held.holder


# --- Expired lock should not block a legitimate restart -------------------


def test_expired_lock_does_not_block_watchdog(state_path):
    """If a previous holder crashed and the lock is past TTL, the
    watchdog must proceed (defence in depth)."""
    import time as _time

    ib_2fa_lock.acquire_2fa_push_lock(
        "crashed-fastapi", ttl_secs=1, now=_time.time() - 60
    )

    save_state(state_path, WatchdogState(degraded_count=2))
    _, restart_mock = _drive(state_path, _hang_payload())
    restart_mock.assert_called_once()


# --- Awaiting-2FA path: api-hang counter resets, stuck-2FA counter advances ---


def test_awaiting_2fa_payload_increments_stuck_counter_without_lock(state_path):
    """`auth_state=awaiting_2fa` with no push lock active and no scheduled
    retry is the stuck-2FA failure mode introduced 2026-05-20. A single
    cycle must reset the api-hang counter (different failure mode) and
    increment `stuck_2fa_count` toward the 3-cycle threshold WITHOUT
    acquiring the push lock yet — the threshold gates the lock acquire."""
    # Genuine stuck-2FA: upstream_dead is False (container running + healthy,
    # parked at the prompt). upstream_dead=True is the JVM acceptor hang →
    # is_api_hang owns it (2026-06-15 loop fix).
    payload = {
        "ib_gateway": {
            "service_state": "unhealthy",
            "port_listening": True,
            "upstream_dead": False,
            "auth_state": "awaiting_2fa",
            "restart_backoff": {
                "attempt_count": 0,
                "next_attempt_in_secs": 0,
                "push_lock": None,
            },
        }
    }
    save_state(state_path, WatchdogState(degraded_count=2))
    result, restart_mock = _drive(state_path, payload)

    restart_mock.assert_not_called()  # under threshold
    assert result.degraded_count == 0  # api-hang counter resets
    assert result.stuck_2fa_count == 1  # stuck-2FA counter starts
    assert ib_2fa_lock.check_2fa_push_lock() is None  # no lock yet


def test_stuck_2fa_threshold_fires_fresh_push(state_path):
    """3 consecutive cycles of stuck-awaiting_2fa with no push in flight
    must trigger a restart, which fires a fresh IBKR Mobile push. This is
    the self-heal path that eliminates the human-in-the-loop dependency
    visible to the user as the recurring 'IB connected vs banner degraded'
    contradiction."""
    payload = {
        "ib_gateway": {
            "service_state": "unhealthy",
            "port_listening": True,
            "upstream_dead": False,
            "auth_state": "awaiting_2fa",
            "restart_backoff": {
                "attempt_count": 0,
                "next_attempt_in_secs": 0,
                "push_lock": None,
            },
        }
    }
    assert ib_2fa_lock.check_2fa_push_lock() is None
    save_state(state_path, WatchdogState(stuck_2fa_count=2))
    result, restart_mock = _drive(state_path, payload)

    restart_mock.assert_called_once()
    assert "stuck_2fa_push_fired" in result.last_outcome
    held = ib_2fa_lock.check_2fa_push_lock()
    assert held is not None
    assert "ib_watchdog" in held.holder


def test_stuck_2fa_does_not_fire_when_push_lock_active(state_path):
    """If FastAPI has already fired a push and the user is approving it,
    the watchdog must NOT stack a second push. The push_lock_active flag
    in /health gates the entire stuck-2FA path."""
    payload = {
        "ib_gateway": {
            "service_state": "unhealthy",
            "port_listening": True,
            "upstream_dead": False,
            "auth_state": "awaiting_2fa",
            "restart_backoff": {
                "attempt_count": 1,
                "next_attempt_in_secs": 0,
                "push_lock": {
                    "holder": "scripts.api.ib_gateway.restart_ib_gateway",
                    "expires_at": 9999999999.0,
                    "remaining_secs": 500,
                    "reason": "user-initiated",
                },
            },
        }
    }
    save_state(state_path, WatchdogState(stuck_2fa_count=2))
    result, restart_mock = _drive(state_path, payload)

    restart_mock.assert_not_called()
    assert result.stuck_2fa_count == 2  # frozen, not incremented


def test_stuck_2fa_does_not_fire_when_backoff_scheduled(state_path):
    """If FastAPI has a scheduled retry coming (next_attempt_in_secs > 0),
    the watchdog defers — that scheduled retry will fire the push itself."""
    payload = {
        "ib_gateway": {
            "service_state": "unhealthy",
            "port_listening": True,
            "upstream_dead": False,
            "auth_state": "awaiting_2fa",
            "restart_backoff": {
                "attempt_count": 2,
                "next_attempt_in_secs": 45,
                "push_lock": None,
            },
        }
    }
    save_state(state_path, WatchdogState(stuck_2fa_count=2))
    result, restart_mock = _drive(state_path, payload)

    restart_mock.assert_not_called()
    assert result.stuck_2fa_count == 2  # frozen
