"""Pool recovery ladder + auth transition tracker (audit §3.2, §2 #7).

* The ladder's only memory was in-process, and the escalation it bounds is
  ``os._exit``. Every restart wiped it, so a pool that stays wedged exited
  radon-api every 3-4 minutes forever, below systemd's StartLimitBurst. The
  self-exit is now recorded in a state file that survives the exit; a second
  escalation inside the window stands down instead of exiting again.
* restart_ib_gateway wrote the transition tracker directly; it now goes
  through the one transition entrypoint.
* unreachable -> authenticated is a recovery edge too: heal + lease release.
* IBPool._connected was written six times and never read.
"""
from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

import scripts.api.server as server
from scripts.api import ib_gateway, ib_pool


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "POOL_RECOVERY_STATE_FILE", tmp_path / "pool-recovery.json")
    monkeypatch.setitem(server._pool_recovery_state, "in_progress", False)
    monkeypatch.setitem(server._pool_recovery_state, "last_attempt_at", 0.0)
    monkeypatch.setitem(server._pool_recovery_state, "consecutive_failures", 0)
    monkeypatch.setattr(server, "test_mode", False)
    monkeypatch.setattr(server, "ib_pool", object())
    monkeypatch.setenv("IB_2FA_LOCK_PATH", str(tmp_path / "lock.json"))

    async def never_recovers(pool, *a, **k):
        return False

    monkeypatch.setattr(server, "recover_stuck_pool", never_recovers)
    import api.ib_gateway as ibg

    monkeypatch.setattr(ibg, "_pool_disconnected_roles", lambda pool: ["data"])
    monkeypatch.setattr(ibg, "_pool_has_connected_accounted_slot", lambda pool, roles=None: False)

    async def authenticated(timeout=8.0):
        return (True, ["U1"])

    monkeypatch.setattr(ibg, "_probe_authenticated", authenticated)


def _exits(monkeypatch):
    exits = {"n": 0}
    monkeypatch.setattr(server, "_restart_radon_api_self", lambda: exits.__setitem__("n", exits["n"] + 1))
    return exits


def _episode():
    for _ in range(server.POOL_RECOVERY_MAX_BEFORE_RESTART):
        server._pool_recovery_state["last_attempt_at"] = 0.0
        asyncio.run(server._recover_stuck_pool_guarded())


def test_second_episode_after_a_self_exit_stands_down(monkeypatch):
    exits = _exits(monkeypatch)
    _episode()
    assert exits["n"] == 1
    assert server.POOL_RECOVERY_STATE_FILE.exists()

    # os._exit wiped the in-memory ladder; the state file did not.
    server._pool_recovery_state["consecutive_failures"] = 0
    _episode()
    assert exits["n"] == 1, "a second escalation inside the window must not exit again"


def test_self_exit_allowed_again_after_the_window(monkeypatch):
    exits = _exits(monkeypatch)
    old = time.time() - server.POOL_RECOVERY_SELF_EXIT_WINDOW_SECS - 60
    server.POOL_RECOVERY_STATE_FILE.write_text(json.dumps({"last_self_exit_at": old}))
    _episode()
    assert exits["n"] == 1


def test_unreadable_state_file_fails_safe(monkeypatch):
    exits = _exits(monkeypatch)
    server.POOL_RECOVERY_STATE_FILE.write_text("{not json")
    _episode()
    assert exits["n"] == 0


def test_unwritable_state_file_refuses_to_exit(monkeypatch, tmp_path):
    exits = _exits(monkeypatch)
    monkeypatch.setattr(server, "POOL_RECOVERY_STATE_FILE", tmp_path / "missing-dir" / "x.json")
    _episode()
    assert exits["n"] == 0


@pytest.mark.parametrize("failures, last_exit, expected", [
    (0, None, "none"),
    (2, None, "none"),
    (3, None, "self_exit"),
    (3, 1000.0, "stand_down"),
    (3, 1000.0 - 7200.0, "self_exit"),
])
def test_escalation_transition_is_one_pure_function(failures, last_exit, expected):
    assert server._pool_escalation(failures, last_exit, now=1000.0) is server.PoolEscalation(expected)


class TestAuthTracker:
    @pytest.fixture(autouse=True)
    def _clean(self):
        ib_gateway._auth_transition_state["previous_auth_state"] = None
        ib_gateway._restart_state.update(attempt_count=0, next_attempt_after=0.0, last_outcome=None)
        yield
        ib_gateway._auth_transition_state["previous_auth_state"] = None
        ib_gateway._restart_state.update(attempt_count=0, next_attempt_after=0.0, last_outcome=None)

    def test_unreachable_to_authenticated_heals_and_releases(self, monkeypatch):
        heal = AsyncMock(return_value=[])
        release = MagicMock()
        monkeypatch.setattr(ib_gateway, "_heal_ib_dependent_service_health", heal)
        monkeypatch.setattr(ib_gateway, "_release_2fa_push_lock_bounded", release)
        pool = MagicMock()
        pool.status.return_value = {"sync": {"connected": False, "managed_accounts": []}}
        pool.reconnect_all = AsyncMock()

        asyncio.run(ib_gateway.handle_auth_state_transition("unreachable", pool))
        fired = asyncio.run(ib_gateway.handle_auth_state_transition("authenticated", pool))

        assert fired is True
        heal.assert_awaited_once()
        release.assert_called_once_with(expected_holder=ib_gateway.IB_GATEWAY_ENSURE_LOCK_HOLDER)
        pool.reconnect_all.assert_awaited_once()

    @pytest.mark.parametrize("port_up, probe, expected", [
        (False, (False, []), "unreachable"),
        (True, (False, []), "awaiting_2fa"),
    ])
    def test_restart_routes_its_observation_through_the_transition(
        self, monkeypatch, port_up, probe, expected,
    ):
        seen = []

        async def record(state, pool, **kwargs):
            seen.append(state)
            return False

        async def fake_restart_docker():
            return {"restarted": True, "port_listening": port_up}

        async def fake_probe(timeout=8.0):
            return probe

        monkeypatch.setattr(ib_gateway, "GATEWAY_MODE", "docker")
        monkeypatch.setattr(ib_gateway, "_app_role_refusal", lambda action: None)
        monkeypatch.setattr(ib_gateway, "_restart_docker", fake_restart_docker)
        monkeypatch.setattr(ib_gateway, "_probe_authenticated", fake_probe)
        monkeypatch.setattr(ib_gateway, "handle_auth_state_transition", record)

        asyncio.run(ib_gateway.restart_ib_gateway())

        assert seen == [expected]
        assert ib_gateway._auth_transition_state["previous_auth_state"] is None, (
            "restart_ib_gateway must not write the tracker itself"
        )


def test_ib_pool_has_one_connected_truth():
    pool = ib_pool.IBPool()
    assert not hasattr(pool, "_connected")
    assert all(pool.is_connected(role) is False for role in ib_pool.POOL_ROLES)
