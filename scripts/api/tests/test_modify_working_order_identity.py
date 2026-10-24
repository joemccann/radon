"""DS-2026-10-24-01: `/orders/modify` must validate the order it mutates.

`_find_working_order` returned the first snapshot row matching EITHER id,
while `ib_order_manage.find_trade` resolves permId across every order first.
A body whose orderId names one working order and whose permId names another
had the first row validated and the second one modified.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import trading_halt

SMALL = {"orderId": 7, "permId": 700, "secType": "OPT", "action": "BUY",
         "quantity": 1, "limitPrice": 1.0}
OTHER = {"orderId": 8, "permId": 800, "secType": "OPT", "action": "BUY",
         "quantity": 1, "limitPrice": 1.0}


@pytest.fixture
def trusted_client(monkeypatch, tmp_path):
    from scripts.api import auth, server

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "test_mode", False)
    monkeypatch.setattr(trading_halt, "HALT_FILE", tmp_path / "halt.json")

    async def snapshot():
        return {"open_orders": [SMALL, OTHER]}

    monkeypatch.setattr(server, "_read_orders_snapshot_from_db", snapshot)
    monkeypatch.setattr(server, "record_order_event", AsyncMock())
    return TestClient(server.app), server


def _spawn_ok():
    return AsyncMock(return_value=SimpleNamespace(
        ok=True, error=None, data={"status": "ok", "orderId": 7, "finalStatus": "Submitted"},
    ))


class TestModifyIdentity:
    def test_ids_naming_two_different_orders_are_refused(self, trusted_client, monkeypatch):
        client, server = trusted_client
        spawn = AsyncMock(side_effect=AssertionError("must not spawn"))
        monkeypatch.setattr(server, "_run_ib_script_with_recovery", spawn)

        resp = client.post("/orders/modify", json={
            "orderId": 7, "permId": 800, "newPrice": 1.1,
        })

        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "ORDER_MODIFY_IDENTITY_MISMATCH"
        spawn.assert_not_called()

    def test_perm_id_resolves_before_order_id(self, server_module=None):
        import asyncio
        from scripts.api import server

        async def snapshot():
            return {"open_orders": [SMALL, OTHER]}

        original = server._read_orders_snapshot_from_db
        server._read_orders_snapshot_from_db = snapshot
        try:
            row = asyncio.run(server._find_working_order(0, 800))
        finally:
            server._read_orders_snapshot_from_db = original
        assert row is OTHER

    def test_an_unknown_perm_id_does_not_fall_back_to_order_id(self, trusted_client, monkeypatch):
        client, server = trusted_client
        spawn = AsyncMock(side_effect=AssertionError("must not spawn"))
        monkeypatch.setattr(server, "_run_ib_script_with_recovery", spawn)

        resp = client.post("/orders/modify", json={
            "orderId": 7, "permId": 999, "newPrice": 1.1,
        })

        assert resp.status_code == 422
        spawn.assert_not_called()

    def test_matching_ids_still_modify(self, trusted_client, monkeypatch):
        client, server = trusted_client
        spawn = _spawn_ok()
        monkeypatch.setattr(server, "_run_ib_script_with_recovery", spawn)

        resp = client.post("/orders/modify", json={
            "orderId": 7, "permId": 700, "newPrice": 1.1,
        })

        assert resp.status_code == 200
        args = spawn.call_args.args
        assert args[0] == "ib_order_manage.py"
        assert args[1] == ["modify", "--order-id", "7", "--perm-id", "700", "--new-price", "1.1"]
