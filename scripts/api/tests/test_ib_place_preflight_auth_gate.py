"""REL-017 (R-008): placement pre-flight must refuse when auth_state is not
"authenticated".

awaiting_2fa keeps the Gateway API socket open, so the port_listening /
upstream_dead pre-flight passes and ib_place_order.py burns its 25s
subprocess timeout before returning an indeterminate error. For order
placement the pre-flight must refuse fast — no subprocess spawn — with an
operator-readable error naming the auth state. Idempotent scripts keep the
existing port-level pre-flight only.
"""

import asyncio

from scripts.api import server
from scripts.api.subprocess import ScriptResult


def _wire_open_socket_gateway(monkeypatch, calls, auth_state):
    async def fake_run_script(script, args, timeout=30):
        calls.append(script)
        return ScriptResult(ok=True, data={})

    async def fake_check_ib_gateway(*args, **kwargs):
        return {
            "port_listening": True,
            "upstream_dead": False,
            "auth_state": auth_state,
        }

    monkeypatch.setattr(server, "run_script", fake_run_script)
    monkeypatch.setattr(server, "check_ib_gateway", fake_check_ib_gateway)
    monkeypatch.setattr(server, "_pool_has_any_connection", lambda: False)
    monkeypatch.setattr(server, "_ib_last_failure", 0.0)
    monkeypatch.setattr(server, "ib_pool", None)


def test_awaiting_2fa_refuses_placement_without_spawning(monkeypatch):
    calls: list = []
    _wire_open_socket_gateway(monkeypatch, calls, "awaiting_2fa")

    result = asyncio.run(
        server._run_ib_script_with_recovery("ib_place_order.py", ["--json"])
    )

    assert calls == [], (
        "pre-flight must refuse before spawning the placement subprocess "
        f"(spawned {calls})"
    )
    assert result.ok is False
    assert "awaiting_2fa" in (result.error or ""), (
        "refusal must name the auth state for the operator"
    )
    assert "authenticated" in (result.error or "")


def test_authenticated_placement_proceeds(monkeypatch):
    calls: list = []
    _wire_open_socket_gateway(monkeypatch, calls, "authenticated")

    result = asyncio.run(
        server._run_ib_script_with_recovery("ib_place_order.py", ["--json"])
    )

    assert calls == ["ib_place_order.py"]
    assert result.ok is True


def test_awaiting_2fa_does_not_block_idempotent_sync(monkeypatch):
    calls: list = []
    _wire_open_socket_gateway(monkeypatch, calls, "awaiting_2fa")

    asyncio.run(server._run_ib_script_with_recovery("ib_sync.py", []))

    assert calls == ["ib_sync.py"], (
        "non-placement scripts keep the existing port-level pre-flight only"
    )
