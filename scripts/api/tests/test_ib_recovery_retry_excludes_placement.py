"""T-011: _run_ib_script_with_recovery must never re-run the
non-idempotent order-placing script.

A connection-flavored failure can surface AFTER placeOrder already reached
IB (the socket died on the way back). Re-running ib_place_order.py then
transmits a second identical live order. Runtime recovery never restarts the
Gateway (its owner does), so no script is retried: placement and the
idempotent sync control case both run exactly once.
"""

import asyncio

import pytest

from scripts.api import server
from scripts.api.subprocess import ScriptResult


def _wire_down_gateway(monkeypatch, calls):
    async def fake_run_script(script, args, timeout=30):
        calls.append(script)
        return ScriptResult(ok=False, error="ConnectionRefusedError(61, 'Connect call failed')")

    async def fake_check_ib_gateway():
        return {"port_listening": False, "upstream_dead": False}

    async def fail_restart(*args, **kwargs):
        raise AssertionError("runtime recovery must not restart the Gateway")

    monkeypatch.setattr(server, "run_script", fake_run_script)
    monkeypatch.setattr(server, "check_ib_gateway", fake_check_ib_gateway)
    monkeypatch.setattr(server, "restart_ib_gateway", fail_restart)
    monkeypatch.setattr(server, "_pool_has_any_connection", lambda: True)
    monkeypatch.setattr(server, "_ib_last_failure", 0.0)
    monkeypatch.setattr(server, "ib_pool", None)


@pytest.mark.parametrize("script, args", [
    ("ib_place_order.py", ["--json"]),
    ("ib_sync.py", []),
])
def test_recovery_runs_each_script_exactly_once(monkeypatch, script, args):
    calls: list = []
    _wire_down_gateway(monkeypatch, calls)

    result = asyncio.run(server._run_ib_script_with_recovery(script, args))

    assert calls == [script], (
        "a re-run after a connection failure can double a live order "
        f"(got {calls})"
    )
    assert result.ok is False
    assert "remote host" in (result.error or "").lower()
