from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


@pytest.fixture
def trusted_client(monkeypatch, tmp_path):
    from scripts.api import auth, server
    import trading_halt

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "test_mode", False)
    monkeypatch.setattr(trading_halt, "HALT_FILE", tmp_path / "halt.json")
    return TestClient(server.app)


def _replacement():
    return {
        "type": "combo", "symbol": "SPX", "action": "BUY", "quantity": 1,
        "limitPrice": 1.0,
        "legs": [
            {"expiry": "20260918", "strike": 7000, "right": "C", "action": "BUY", "ratio": 1},
            {"expiry": "20260918", "strike": 7050, "right": "C", "action": "SELL", "ratio": 1},
        ],
    }


def test_replace_preflight_runs_before_any_cancellation(trusted_client, monkeypatch):
    from scripts.api import server

    preflight = AsyncMock(side_effect=HTTPException(status_code=502, detail="what-if rejected"))
    cancel = AsyncMock()
    monkeypatch.setattr(server, "orders_whatif", preflight)
    monkeypatch.setattr(server, "orders_cancel", cancel)

    response = trusted_client.post("/orders/replace", json={
        "cancelOrders": [{"orderId": 10}],
        "replaceOrder": _replacement(),
    })

    assert response.status_code == 502
    cancel.assert_not_awaited()


def test_replace_preserves_indeterminate_status_and_cancel_audit(trusted_client, monkeypatch):
    from scripts.api import server

    monkeypatch.setattr(server, "orders_whatif", AsyncMock(return_value={"status": "ok"}))
    monkeypatch.setattr(server, "orders_cancel", AsyncMock(return_value={"finalStatus": "Cancelled"}))
    monkeypatch.setattr(
        server,
        "orders_place",
        AsyncMock(side_effect=HTTPException(status_code=504, detail={"code": "ORDER_INDETERMINATE"})),
    )

    response = trusted_client.post("/orders/replace", json={
        "cancelOrders": [{"orderId": 10}],
        "replaceOrder": _replacement(),
    })

    assert response.status_code == 504
    detail = response.json()["detail"]
    assert detail["code"] == "REPLACE_INDETERMINATE"
    assert detail["cancelled"] == [{"orderId": 10, "permId": None, "status": "Cancelled"}]
    assert detail["replacementOrderRef"].startswith("radon-replace-")
