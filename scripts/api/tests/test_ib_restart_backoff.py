"""2FA-aware auth state for the remote IB Gateway.

The Gateway sits at the IBKR Mobile 2FA prompt with the API socket open after
every restart. A "port listening" probe alone falsely reports success — pool
clients connect but `managedAccounts()` is empty until the user approves the
push. These tests pin down that auth is derived from `managedAccounts()`
non-empty, not from port listening, and how /health reports it.
"""

import asyncio

import pytest

from scripts.api import ib_gateway


@pytest.fixture(autouse=True)
def _redirect_lock_path(tmp_path, monkeypatch):
    """Redirect the cross-process 2FA push lock to a tmp file so we
    never touch the production /var/lib/radon path during tests."""
    monkeypatch.setenv("IB_2FA_LOCK_PATH", str(tmp_path / "ib-2fa-push-lock.json"))


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

    monkeypatch.setattr(ib_gateway, "_check_cloud", fake_check_cloud)

    result = asyncio.run(ib_gateway.check_ib_gateway())

    assert result["auth_state"] == "remote"


def test_cloud_mode_exposes_restart_backoff_and_push_lock(monkeypatch):
    async def fake_check_cloud():
        return {
            "port_listening": True,
            "upstream_dead": False,
            "service_state": "reachable",
            "gateway_mode": "cloud",
        }

    expected = {"attempt_count": 1, "push_lock": {"remaining_secs": 42}}
    monkeypatch.setattr(ib_gateway, "_check_cloud", fake_check_cloud)
    monkeypatch.setattr(ib_gateway, "restart_backoff_state", lambda: expected)

    result = asyncio.run(ib_gateway.check_ib_gateway())

    assert result["restart_backoff"] == expected


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

    monkeypatch.setattr(ib_gateway, "_check_cloud", fake_check_cloud)

    # Pool reports clients connected but no managed_accounts visible.
    # Classic "Gateway listening, awaiting 2FA approval" signature.
    pool = {
        "sync": {"connected": True, "managed_accounts": []},
        "orders": {"connected": True, "managed_accounts": []},
    }
    result = asyncio.run(ib_gateway.check_ib_gateway(pool_status=pool))

    assert result["auth_state"] == "awaiting_2fa"


def test_cloud_mode_protocol_wedge_overrides_cached_authenticated_pool(monkeypatch):
    """A socat TCP listener cannot make a missing Java API listener healthy.

    The relay outage on 2026-07-27 left Docker/socat accepting :4001 while
    the Gateway's inner listener was absent after failed 2FA. The pool still
    held cached authenticated slots, so the TCP-only cloud check falsely
    reported the whole data plane healthy.
    """
    monkeypatch.setattr(ib_gateway, "_port_listening", lambda: True)
    monkeypatch.setattr(ib_gateway, "_cloud_protocol_responding", lambda: False)

    pool = {
        "sync": {"connected": True, "managed_accounts": ["U1234567"]},
        "orders": {"connected": True, "managed_accounts": ["U1234567"]},
        "data": {"connected": True, "managed_accounts": ["U1234567"]},
    }
    result = asyncio.run(ib_gateway.check_ib_gateway(pool_status=pool))

    assert result["port_listening"] is True
    assert result["upstream_dead"] is True
    assert result["service_state"] == "unhealthy"
    assert result["auth_state"] == "unreachable"


