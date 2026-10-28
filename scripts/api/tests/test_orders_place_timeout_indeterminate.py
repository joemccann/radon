"""REL-006 — /orders/place timeout = indeterminate, never a generic 502
(RELIABILITY_AUDIT.md R-009).

"Script timed out after 25s" matched none of the connection-refused
patterns, so the operator got a bare timeout with no "check open orders"
warning — and the natural next act was a duplicate re-place. The route
also now injects an orderRef so the identity survives a SIGKILLed
subprocess.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import trading_halt


@pytest.fixture
def trusted_client(monkeypatch, tmp_path):
    from scripts.api import auth, server

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "test_mode", False)
    monkeypatch.setattr(trading_halt, "HALT_FILE", tmp_path / "halt.json")
    server._order_rate_timestamps.clear()
    return TestClient(server.app), server


_ORDER = {
    "type": "stock", "symbol": "AAPL", "action": "BUY",
    "quantity": 1, "limitPrice": 200.0,
}


class TestTimeoutIndeterminate:
    def test_timeout_maps_to_504_order_indeterminate(self, trusted_client, monkeypatch):
        client, server = trusted_client
        timeout_result = SimpleNamespace(
            ok=False, error="Script timed out after 25s", data=None
        )
        monkeypatch.setattr(
            server, "_run_ib_script_with_recovery",
            AsyncMock(return_value=timeout_result),
        )
        resp = client.post("/orders/place", json=_ORDER)
        assert resp.status_code == 504
        detail = resp.json()["detail"]
        assert detail["code"] == "ORDER_INDETERMINATE"
        assert "check open orders" in detail["message"].lower()
        assert detail.get("orderRef", "").startswith("radon-")

    def test_route_injects_order_ref_into_script_payload(self, trusted_client, monkeypatch):
        client, server = trusted_client
        captured = {}

        async def spawn(script, args, timeout=25):
            captured["payload"] = json.loads(args[1])
            return SimpleNamespace(
                ok=True, error=None,
                data={"status": "ok", "orderId": 42, "permId": 420042,
                      "initialStatus": "Submitted", "message": "ok"},
            )

        monkeypatch.setattr(server, "_run_ib_script_with_recovery", spawn)
        resp = client.post("/orders/place", json=_ORDER)
        assert resp.status_code == 200
        assert captured["payload"].get("orderRef", "").startswith("radon-")

    def test_non_timeout_failure_stays_502(self, trusted_client, monkeypatch):
        client, server = trusted_client
        failure = SimpleNamespace(ok=False, error="Connection failed: refused", data=None)
        monkeypatch.setattr(
            server, "_run_ib_script_with_recovery",
            AsyncMock(return_value=failure),
        )
        resp = client.post("/orders/place", json=_ORDER)
        assert resp.status_code == 502


@pytest.mark.asyncio
@pytest.mark.parametrize("process_ok", [True, False])
@pytest.mark.parametrize("broker_status", ["PendingSubmit", "ApiPending", "Unknown"])
async def test_structured_unacknowledged_combo_is_indeterminate(
    monkeypatch, process_ok, broker_status
):
    """A returned JSON result does not turn an unknown broker outcome into rejection."""
    from scripts.api import server

    body = {
        "type": "combo", "symbol": "CBRS", "action": "BUY",
        "quantity": 1, "limitPrice": -0.70, "orderRef": "radon-combo-pending",
        "legs": [
            {"expiry": "20261106", "strike": 120, "right": "C", "action": "BUY", "ratio": 1},
            {"expiry": "20261106", "strike": 115, "right": "P", "action": "SELL", "ratio": 1},
        ],
    }
    outcome = {
        "status": "error", "indeterminate": True,
        "message": "IB did not confirm. CHECK OPEN ORDERS before re-placing.",
        "orderRef": body["orderRef"], "orderId": 17, "permId": 0,
        "initialStatus": broker_status,
    }
    spawn = AsyncMock(return_value=SimpleNamespace(
        ok=process_ok, error=None if process_ok else outcome["message"], data=outcome,
    ))
    audit = AsyncMock()
    monkeypatch.setattr(server, "test_mode", False)
    monkeypatch.setattr(server, "_run_ib_script_with_recovery", spawn)
    monkeypatch.setattr(server, "record_order_event", audit)

    with pytest.raises(HTTPException) as exc:
        await server._orders_place_after_rate_reservation(body)

    assert exc.value.status_code == 504
    assert exc.value.detail == {**outcome, "code": "ORDER_INDETERMINATE"}
    spawn.assert_awaited_once()
    audit.assert_awaited_once()
    event = audit.await_args
    assert event.args == ("indeterminate",)
    assert event.kwargs["order_ref"] == body["orderRef"]
    assert event.kwargs["order_id"] == 17
    assert event.kwargs["perm_id"] == 0
    assert event.kwargs["status"] == broker_status
    assert event.kwargs["detail"] == exc.value.detail


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["cloud", "managed", "restart-success", "restart-failure"])
async def test_recovery_preserves_structured_placement_uncertainty(monkeypatch, mode):
    """Exercise real recovery: a down gateway cannot erase post-transmit identity."""
    from scripts.api import server
    from scripts.api.subprocess import ScriptResult

    body = {**_ORDER, "orderRef": "radon-socket-lost"}
    outcome = {
        "status": "error", "indeterminate": True,
        "orderRef": body["orderRef"], "orderId": 19, "permId": 0,
        "message": "Order transmitted; [Errno 111] Connection refused during confirmation. CHECK OPEN ORDERS.",
    }
    runner = AsyncMock(return_value=ScriptResult(
        ok=False, error=outcome["message"], data=outcome,
    ))
    gateway = AsyncMock(return_value={"port_listening": False, "upstream_dead": True})
    restart = AsyncMock(return_value={
        "restarted": mode == "restart-success",
        "port_listening": mode == "restart-success",
        "error": "gateway unavailable" if mode == "restart-failure" else None,
    })
    audit = AsyncMock()
    monkeypatch.setattr(server, "test_mode", False)
    monkeypatch.setattr(server, "run_script", runner)
    monkeypatch.setattr(server, "record_order_event", audit)
    monkeypatch.setattr(server, "check_ib_gateway", gateway)
    monkeypatch.setattr(server, "restart_ib_gateway", restart)
    monkeypatch.setattr(server, "_working_open_order_perm_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(server, "_pool_has_any_connection", lambda: True)
    monkeypatch.setattr(server, "_ib_last_failure", 0.0)
    monkeypatch.setattr(server, "ib_pool", None)
    monkeypatch.setattr(server, "is_cloud_mode", lambda: mode == "cloud")
    monkeypatch.setattr(server, "is_docker_mode", lambda: False)
    monkeypatch.setattr(server, "is_launchd_mode", lambda: mode == "managed")

    with pytest.raises(HTTPException) as exc:
        await server._orders_place_after_rate_reservation(body)

    # Prove the recovery branch ran before judging its returned outcome.
    runner.assert_awaited_once()
    gateway.assert_awaited_once()
    if mode.startswith("restart-"):
        restart.assert_awaited_once()
    else:
        restart.assert_not_awaited()
    assert exc.value.status_code == 504
    assert exc.value.detail == {**outcome, "code": "ORDER_INDETERMINATE"}
    audit.assert_awaited_once()
    assert audit.await_args.args == ("indeterminate",)
    assert audit.await_args.kwargs["order_id"] == 19
    assert audit.await_args.kwargs["order_ref"] == body["orderRef"]
    assert audit.await_args.kwargs["detail"] == exc.value.detail
