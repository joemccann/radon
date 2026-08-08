"""T-011: the post-restart retry in _run_ib_script_with_recovery must never
re-run the non-idempotent order-placing script.

A connection-flavored failure can surface AFTER placeOrder already reached
IB (the socket died on the way back). Re-running ib_place_order.py then
transmits a second identical live order. Idempotent sync scripts keep the
retry — that is the control case pinning the carve-out's scope.
"""

import asyncio

from scripts.api import server
from scripts.api.subprocess import ScriptResult


def _wire_restartable_gateway(monkeypatch, calls):
    async def fake_run_script(script, args, timeout=30):
        calls.append(script)
        return ScriptResult(ok=False, error="ConnectionRefusedError(61, 'Connect call failed')")

    async def fake_check_ib_gateway():
        return {"port_listening": False, "upstream_dead": False}

    async def fake_restart():
        return {"restarted": True, "port_listening": True}

    monkeypatch.setattr(server, "run_script", fake_run_script)
    monkeypatch.setattr(server, "check_ib_gateway", fake_check_ib_gateway)
    monkeypatch.setattr(server, "restart_ib_gateway", fake_restart)
    monkeypatch.setattr(server, "is_cloud_mode", lambda: False)
    monkeypatch.setattr(server, "is_docker_mode", lambda: False)
    monkeypatch.setattr(server, "is_launchd_mode", lambda: False)
    monkeypatch.setattr(server, "_pool_has_any_connection", lambda: True)
    monkeypatch.setattr(server, "_ib_last_failure", 0.0)
    monkeypatch.setattr(server, "ib_pool", None)


def test_recovery_never_reruns_ib_place_order_after_gateway_restart(monkeypatch):
    calls: list = []
    _wire_restartable_gateway(monkeypatch, calls)

    result = asyncio.run(server._run_ib_script_with_recovery("ib_place_order.py", ["--json"]))

    assert calls == ["ib_place_order.py"], (
        "placement must run exactly once — an automatic re-run after a gateway "
        f"restart can double a live order (got {calls})"
    )
    assert result.ok is False
    # The outcome is INDETERMINATE (the first attempt may have reached IB) —
    # the error must say so and point the operator at open orders.
    assert "not automatically retried" in (result.error or "").lower()
    assert "open orders" in (result.error or "").lower()


def test_recovery_still_retries_idempotent_sync_after_gateway_restart(monkeypatch):
    calls: list = []
    _wire_restartable_gateway(monkeypatch, calls)

    asyncio.run(server._run_ib_script_with_recovery("ib_sync.py", []))

    assert calls == ["ib_sync.py", "ib_sync.py"], (
        f"idempotent scripts keep the post-restart retry (got {calls})"
    )
