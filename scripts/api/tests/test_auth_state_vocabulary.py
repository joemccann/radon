"""One auth_state vocabulary across FastAPI and the health daemon (audit §2 #9).

FastAPI produces exactly: unreachable, authenticated, awaiting_2fa, unknown,
remote. The daemon checked a never-produced "unauthenticated" and left
"remote" to a fall-through. A /health/lite probe timeout reported
port_listening=false, which the daemon read as a confirmed "down".
"""
from __future__ import annotations

import asyncio

import pytest

from scripts.api import server
from scripts.health_service import probes

gw = server.ib_gateway


def _api(auth_state, *, port=True, upstream=False, service="reachable"):
    return {"radon-api": {"state": "up", "payload": {
        "service_state": service, "auth_state": auth_state,
        "upstream_dead": upstream, "port_listening": port,
    }}}


def test_daemon_maps_exactly_the_produced_vocabulary():
    produced = {state.value for state in gw.AuthState}
    assert produced == {"unreachable", "authenticated", "awaiting_2fa", "unknown", "remote"}
    assert set(probes.AUTH_STATE_COARSE) == produced


@pytest.mark.parametrize("auth_state, expected", [
    ("authenticated", "up"),
    ("awaiting_2fa", "down"),
    ("unreachable", "down"),
    ("unknown", "unknown"),
    # Cloud TCP-only: this host cannot tell auth from a 2FA wait.
    ("remote", "unknown"),
    # Never produced: not evidence of anything.
    ("unauthenticated", "unknown"),
    ("ok", "unknown"),
])
def test_daemon_auth_mapping(auth_state, expected):
    assert probes._nested_api_state(_api(auth_state)) == expected


def test_derived_states_are_members():
    assert gw._derive_auth_state({"port_listening": False}, None) == gw.AuthState.UNREACHABLE.value
    assert gw._derive_auth_state({"port_listening": True}, None) == gw.AuthState.UNKNOWN.value
    assert gw._derive_auth_state(
        {"port_listening": True}, {"sync": {"connected": True, "managed_accounts": ["U1"]}}
    ) == gw.AuthState.AUTHENTICATED.value


def test_lite_probe_timeout_reads_unknown_not_down(monkeypatch):
    async def hangs(*args, **kwargs):
        await asyncio.sleep(60)

    monkeypatch.setattr(server, "check_ib_gateway", hangs)
    monkeypatch.setattr(server, "HEALTH_LITE_GATEWAY_PROBE_TIMEOUT_SECS", 0.05)
    monkeypatch.setattr(server, "ib_pool", None)

    payload = asyncio.run(asyncio.wait_for(server.health_lite(), timeout=2))

    assert payload["auth_state"] == "unknown"
    assert payload["port_listening"] is None
    assert payload["upstream_dead"] is None
    assert probes._nested_api_state(
        {"radon-api": {"state": "up", "payload": payload}}
    ) == "unknown"
