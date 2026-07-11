"""Restart backoff + 2FA verification for IB Gateway.

The Gateway sits at the IBKR Mobile 2FA prompt with the API socket open after
every restart. A "port listening" probe alone falsely reports success — pool
clients connect but `managedAccounts()` is empty until the user approves the
push. These tests pin down two contracts:

  1. After each restart, success is determined by `managedAccounts()` non-empty,
     not by port listening.
  2. Repeated failed restarts back off exponentially (1m → 60m, capped) so we
     don't spam the user with 2FA pushes or trip IB's suspicious-activity flag.

Reset only happens when login is verified — `reset_restart_backoff()` is the
operator escape hatch when 2FA was just approved out-of-band.
"""

import asyncio
import time

import pytest

from scripts.api import ib_gateway


@pytest.fixture(autouse=True)
def _reset_backoff_state(tmp_path, monkeypatch):
    """Each test starts with a clean restart_state.

    Also redirects the cross-process 2FA push lock to a tmp file so we
    never touch the production /var/lib/radon path during tests."""
    monkeypatch.setenv("IB_2FA_LOCK_PATH", str(tmp_path / "ib-2fa-push-lock.json"))
    ib_gateway._restart_state["attempt_count"] = 0
    ib_gateway._restart_state["next_attempt_after"] = 0.0
    ib_gateway._restart_state["last_attempt_at"] = 0.0
    ib_gateway._restart_state["last_outcome"] = None
    ib_gateway._restart_state["last_accounts"] = []
    yield
    ib_gateway._restart_state["attempt_count"] = 0
    ib_gateway._restart_state["next_attempt_after"] = 0.0


def test_backoff_ladder_matches_documented_cadence():
    # Documented in feedback_ib_gateway_2fa_verification.md: 1m, 2m, 5m, 15m, 30m, 60m.
    assert ib_gateway._next_backoff_delay(1) == 60
    assert ib_gateway._next_backoff_delay(2) == 120
    assert ib_gateway._next_backoff_delay(3) == 300
    assert ib_gateway._next_backoff_delay(4) == 900
    assert ib_gateway._next_backoff_delay(5) == 1800
    assert ib_gateway._next_backoff_delay(6) == 3600
    # Caps at 60m no matter how many failures pile up.
    assert ib_gateway._next_backoff_delay(7) == 3600
    assert ib_gateway._next_backoff_delay(20) == 3600


def test_derive_auth_state_unreachable_when_port_down():
    state = ib_gateway._derive_auth_state({"port_listening": False}, pool_status=None)
    assert state == "unreachable"


def test_derive_auth_state_unknown_when_no_pool_visibility():
    state = ib_gateway._derive_auth_state({"port_listening": True}, pool_status=None)
    assert state == "unknown"


def test_derive_auth_state_authenticated_when_pool_has_accounts():
    pool = {
        "sync": {"connected": True, "managed_accounts": ["U1234567"]},
        "orders": {"connected": True, "managed_accounts": []},
    }
    state = ib_gateway._derive_auth_state({"port_listening": True}, pool_status=pool)
    assert state == "authenticated"


def test_derive_auth_state_awaiting_2fa_when_port_up_but_no_accounts():
    # The signature of "Gateway listening but stuck at 2FA prompt": all pool
    # clients are connected (or trying to connect) but managedAccounts is empty.
    pool = {
        "sync": {"connected": True, "managed_accounts": []},
        "orders": {"connected": True, "managed_accounts": []},
    }
    state = ib_gateway._derive_auth_state({"port_listening": True}, pool_status=pool)
    assert state == "awaiting_2fa"


def test_derive_auth_state_awaiting_2fa_when_pool_disconnected_but_port_up():
    pool = {
        "sync": {"connected": False, "managed_accounts": []},
    }
    state = ib_gateway._derive_auth_state({"port_listening": True}, pool_status=pool)
    assert state == "awaiting_2fa"


def test_restart_defers_inside_backoff_window(monkeypatch):
    # Simulate a previous attempt: 1m backoff window still open.
    ib_gateway._restart_state["attempt_count"] = 1
    ib_gateway._restart_state["next_attempt_after"] = time.time() + 30
    ib_gateway._restart_state["last_attempt_at"] = time.time() - 30
    ib_gateway._restart_state["last_outcome"] = "awaiting_2fa"

    async def fail_restart_docker():
        raise AssertionError("restart_ib_gateway must NOT call _restart_docker during backoff")

    monkeypatch.setattr(ib_gateway, "is_cloud_mode", lambda: False)
    monkeypatch.setattr(ib_gateway, "is_docker_mode", lambda: True)
    monkeypatch.setattr(ib_gateway, "_restart_docker", fail_restart_docker)

    result = asyncio.run(ib_gateway.restart_ib_gateway())

    assert result["restarted"] is False
    assert result["deferred"] is True
    assert result["reason"] == "awaiting_backoff"
    assert result["attempt_count"] == 1
    assert 0 < result["next_attempt_in_secs"] <= 30
    assert "2FA" in result["error"]


def test_restart_resets_backoff_when_probe_authenticates(monkeypatch):
    # Pretend a couple of failed attempts already happened.
    ib_gateway._restart_state["attempt_count"] = 2
    ib_gateway._restart_state["next_attempt_after"] = time.time() - 5  # window expired
    ib_gateway._restart_state["last_outcome"] = "awaiting_2fa"

    async def fake_restart_docker():
        return {"restarted": True, "port_listening": True, "gateway_mode": "docker"}

    async def fake_probe(timeout=8.0):
        return (True, ["U1234567"])

    monkeypatch.setattr(ib_gateway, "is_cloud_mode", lambda: False)
    monkeypatch.setattr(ib_gateway, "is_docker_mode", lambda: True)
    monkeypatch.setattr(ib_gateway, "_restart_docker", fake_restart_docker)
    monkeypatch.setattr(ib_gateway, "_probe_authenticated", fake_probe)

    result = asyncio.run(ib_gateway.restart_ib_gateway())

    assert result["restarted"] is True
    assert result["authenticated"] is True
    assert result["auth_state"] == "authenticated"
    assert result["managed_accounts"] == ["U1234567"]
    # Backoff fully reset
    assert ib_gateway._restart_state["attempt_count"] == 0
    assert ib_gateway._restart_state["next_attempt_after"] == 0.0
    assert ib_gateway._restart_state["last_outcome"] == "authenticated"


def test_restart_advances_backoff_when_port_up_but_unauthenticated(monkeypatch):
    # First attempt — backoff state clean.
    async def fake_restart_docker():
        return {"restarted": True, "port_listening": True, "gateway_mode": "docker"}

    async def fake_probe(timeout=8.0):
        return (False, [])  # port up, no accounts → awaiting 2FA

    monkeypatch.setattr(ib_gateway, "is_cloud_mode", lambda: False)
    monkeypatch.setattr(ib_gateway, "is_docker_mode", lambda: True)
    monkeypatch.setattr(ib_gateway, "_restart_docker", fake_restart_docker)
    monkeypatch.setattr(ib_gateway, "_probe_authenticated", fake_probe)

    before = time.time()
    result = asyncio.run(ib_gateway.restart_ib_gateway())

    assert result["restarted"] is True
    assert result["authenticated"] is False
    assert result["auth_state"] == "awaiting_2fa"
    assert result["next_attempt_in_secs"] == 60  # first failure → 1m
    assert ib_gateway._restart_state["attempt_count"] == 1
    assert ib_gateway._restart_state["next_attempt_after"] >= before + 60 - 1
    assert ib_gateway._restart_state["last_outcome"] == "awaiting_2fa"


def test_restart_advances_backoff_when_port_never_comes_up(monkeypatch):
    async def fake_restart_docker():
        return {"restarted": True, "port_listening": False, "gateway_mode": "docker"}

    async def fail_probe(timeout=8.0):
        raise AssertionError("probe should not run when port is down")

    monkeypatch.setattr(ib_gateway, "is_cloud_mode", lambda: False)
    monkeypatch.setattr(ib_gateway, "is_docker_mode", lambda: True)
    monkeypatch.setattr(ib_gateway, "_restart_docker", fake_restart_docker)
    monkeypatch.setattr(ib_gateway, "_probe_authenticated", fail_probe)

    result = asyncio.run(ib_gateway.restart_ib_gateway())

    assert result["restarted"] is True
    assert result["auth_state"] == "unreachable"
    assert result["next_attempt_in_secs"] == 60
    assert ib_gateway._restart_state["attempt_count"] == 1
    assert ib_gateway._restart_state["last_outcome"] == "unreachable"


def test_backoff_grows_geometrically_across_consecutive_failures(monkeypatch):
    # Walk three failed attempts and confirm the ladder advances 1m → 2m → 5m.
    async def fake_restart_docker():
        return {"restarted": True, "port_listening": True, "gateway_mode": "docker"}

    async def fake_probe(timeout=8.0):
        return (False, [])

    monkeypatch.setattr(ib_gateway, "is_cloud_mode", lambda: False)
    monkeypatch.setattr(ib_gateway, "is_docker_mode", lambda: True)
    monkeypatch.setattr(ib_gateway, "_restart_docker", fake_restart_docker)
    monkeypatch.setattr(ib_gateway, "_probe_authenticated", fake_probe)

    expected = [60, 120, 300]
    for index, want in enumerate(expected):
        # Simulate the previous ten-minute lease expiring without resetting the
        # independent failure counter. Active same-holder reentry is correctly
        # refused; this test isolates the mathematical backoff ladder.
        if index:
            ib_gateway.ib_2fa_lock.release_2fa_push_lock(
                expected_holder=ib_gateway.IB_GATEWAY_LOCK_HOLDER
            )
        ib_gateway._restart_state["next_attempt_after"] = 0.0
        result = asyncio.run(ib_gateway.restart_ib_gateway())
        assert result["next_attempt_in_secs"] == want, (
            f"after {ib_gateway._restart_state['attempt_count']} failures, "
            f"expected {want}s backoff, got {result['next_attempt_in_secs']}"
        )


def test_reset_restart_backoff_clears_state():
    ib_gateway._restart_state["attempt_count"] = 3
    ib_gateway._restart_state["next_attempt_after"] = time.time() + 900
    ib_gateway._restart_state["last_outcome"] = "awaiting_2fa"

    result = ib_gateway.reset_restart_backoff()

    assert result["reset"] is True
    assert result["previous"]["attempt_count"] == 3
    assert result["previous"]["last_outcome"] == "awaiting_2fa"
    assert ib_gateway._restart_state["attempt_count"] == 0
    assert ib_gateway._restart_state["next_attempt_after"] == 0.0


def test_cloud_mode_falls_back_to_remote_when_no_pool(monkeypatch):
    """Without a local pool to probe, cloud mode can't tell auth from port alone."""
    async def fake_check_cloud():
        return {
            "port_listening": True,
            "upstream_dead": False,
            "service_state": "reachable",
            "host": "ib-gateway",
            "port": 4001,
            "gateway_mode": "cloud",
        }

    monkeypatch.setattr(ib_gateway, "is_cloud_mode", lambda: True)
    monkeypatch.setattr(ib_gateway, "_check_cloud", fake_check_cloud)

    result = asyncio.run(ib_gateway.check_ib_gateway())

    assert result["auth_state"] == "remote"


def test_cloud_mode_check_returns_unreachable_when_port_down(monkeypatch):
    async def fake_check_cloud():
        return {
            "port_listening": False,
            "upstream_dead": False,
            "service_state": "unreachable",
            "host": "ib-gateway",
            "port": 4001,
            "gateway_mode": "cloud",
        }

    monkeypatch.setattr(ib_gateway, "is_cloud_mode", lambda: True)
    monkeypatch.setattr(ib_gateway, "_check_cloud", fake_check_cloud)

    result = asyncio.run(ib_gateway.check_ib_gateway())

    assert result["auth_state"] == "unreachable"


def test_cloud_mode_uses_pool_when_available_and_authenticated(monkeypatch):
    """When pool_status is provided, cloud mode derives auth from accounts
    instead of falling back to "remote" — the local pool's connections are
    the authoritative signal even when Gateway lives on another host."""
    async def fake_check_cloud():
        return {
            "port_listening": True,
            "upstream_dead": False,
            "service_state": "reachable",
            "host": "ib-gateway",
            "port": 4001,
            "gateway_mode": "cloud",
        }

    monkeypatch.setattr(ib_gateway, "is_cloud_mode", lambda: True)
    monkeypatch.setattr(ib_gateway, "_check_cloud", fake_check_cloud)

    pool = {
        "sync": {"connected": True, "managed_accounts": ["U1234567"]},
        "orders": {"connected": True, "managed_accounts": ["U1234567"]},
    }
    result = asyncio.run(ib_gateway.check_ib_gateway(pool_status=pool))

    assert result["auth_state"] == "authenticated"


def test_cloud_mode_uses_pool_to_detect_awaiting_2fa(monkeypatch):
    async def fake_check_cloud():
        return {
            "port_listening": True,
            "upstream_dead": False,
            "service_state": "reachable",
            "host": "ib-gateway",
            "port": 4001,
            "gateway_mode": "cloud",
        }

    monkeypatch.setattr(ib_gateway, "is_cloud_mode", lambda: True)
    monkeypatch.setattr(ib_gateway, "_check_cloud", fake_check_cloud)

    # Pool reports clients connected but no managed_accounts visible.
    # Classic "Gateway listening, awaiting 2FA approval" signature.
    pool = {
        "sync": {"connected": True, "managed_accounts": []},
        "orders": {"connected": True, "managed_accounts": []},
    }
    result = asyncio.run(ib_gateway.check_ib_gateway(pool_status=pool))

    assert result["auth_state"] == "awaiting_2fa"


def test_compose_dir_can_be_overridden_via_env(monkeypatch, tmp_path):
    """Hetzner runs the IB Gateway container from /home/radon/radon-cloud/,
    not the default <repo>/docker/ib-gateway. IB_GATEWAY_COMPOSE_DIR is the
    knob that lets FastAPI's docker mode point at the actual compose project
    instead of silently treating an unrelated path as authoritative."""
    custom = tmp_path / "custom-compose"
    custom.mkdir()
    monkeypatch.setenv("IB_GATEWAY_COMPOSE_DIR", str(custom))

    # Re-import to pick up the patched env. The module reads COMPOSE_DIR at
    # import time, so a clean import is required.
    import importlib
    import scripts.api.ib_gateway as gw_module
    reloaded = importlib.reload(gw_module)

    try:
        assert reloaded.COMPOSE_DIR == custom
    finally:
        # Restore the canonical module so other tests aren't poisoned by the
        # custom path.
        monkeypatch.delenv("IB_GATEWAY_COMPOSE_DIR", raising=False)
        importlib.reload(gw_module)
