"""REL-018 (R-007): automated gateway restarts must inventory working orders.

The auto-restart branch of _run_ib_script_with_recovery restarts the Gateway
blind to working orders — including immediately after a failed placement it
acknowledges "may have reached IB". Before restarting it must snapshot
working orders from Turso ``open_orders`` (cheap read, no IB call) and
(a) log "restarting gateway with N working orders: [permIds]", and
(b) when the failed script is ib_place_order.py AND working orders exist,
carry that inventory in the returned error. Zero orders → unchanged behavior.
"""

import asyncio
import logging

from scripts.api import server
from scripts.api.subprocess import ScriptResult


def _wire_auto_restart(monkeypatch, calls, open_order_rows):
    async def fake_run_script(script, args, timeout=30):
        calls.append(script)
        return ScriptResult(ok=False, error="ConnectionRefusedError(61, 'Connect call failed')")

    async def fake_check_ib_gateway(*args, **kwargs):
        return {"port_listening": False, "upstream_dead": False}

    async def fake_restart():
        return {"restarted": True, "port_listening": True}

    def fake_hrana_execute(sql, params=()):
        assert "open_orders" in sql
        return open_order_rows

    monkeypatch.setattr(server, "run_script", fake_run_script)
    monkeypatch.setattr(server, "check_ib_gateway", fake_check_ib_gateway)
    monkeypatch.setattr(server, "restart_ib_gateway", fake_restart)
    monkeypatch.setattr(server.db_http, "hrana_execute", fake_hrana_execute)
    monkeypatch.setattr(server, "is_cloud_mode", lambda: False)
    monkeypatch.setattr(server, "is_docker_mode", lambda: False)
    monkeypatch.setattr(server, "is_launchd_mode", lambda: False)
    monkeypatch.setattr(server, "_pool_has_any_connection", lambda: True)
    monkeypatch.setattr(server, "_ib_last_failure", 0.0)
    monkeypatch.setattr(server, "ib_pool", None)


def test_restart_logs_inventory_and_error_carries_it_for_placement(monkeypatch, caplog):
    calls: list = []
    _wire_auto_restart(monkeypatch, calls, [[111222333], [444555666]])
    caplog.set_level(logging.WARNING, logger="radon.api")

    result = asyncio.run(
        server._run_ib_script_with_recovery("ib_place_order.py", ["--json"])
    )

    inventory_logs = [
        r.getMessage() for r in caplog.records
        if "restarting gateway with" in r.getMessage()
    ]
    assert len(inventory_logs) == 1, (
        "restart must log a working-order inventory line before restarting"
    )
    assert "2 working orders" in inventory_logs[0]
    assert "111222333" in inventory_logs[0]
    assert "444555666" in inventory_logs[0]

    assert result.ok is False
    assert "2 working orders" in (result.error or "")
    assert "111222333" in (result.error or "")
    assert "444555666" in (result.error or "")


def test_restart_with_zero_working_orders_is_unchanged(monkeypatch, caplog):
    calls: list = []
    _wire_auto_restart(monkeypatch, calls, [])
    caplog.set_level(logging.WARNING, logger="radon.api")

    result = asyncio.run(
        server._run_ib_script_with_recovery("ib_place_order.py", ["--json"])
    )

    assert not any(
        "restarting gateway with" in r.getMessage() for r in caplog.records
    ), "no inventory line when no working orders exist"
    assert result.ok is False
    assert "working orders" not in (result.error or "")
    assert "not automatically retried" in (result.error or "").lower()


def test_idempotent_sync_restart_logs_inventory_and_keeps_retry(monkeypatch, caplog):
    calls: list = []
    _wire_auto_restart(monkeypatch, calls, [[111222333]])
    caplog.set_level(logging.WARNING, logger="radon.api")

    asyncio.run(server._run_ib_script_with_recovery("ib_sync.py", []))

    assert any(
        "restarting gateway with 1 working orders" in r.getMessage()
        for r in caplog.records
    )
    assert calls == ["ib_sync.py", "ib_sync.py"], (
        "inventory logging must not change the idempotent retry behavior"
    )
