"""REL-005 — route-level order limits: fast refusal + order-rate cap
(RELIABILITY_AUDIT.md R-002)."""

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


@pytest.fixture
def trusted_client(monkeypatch, tmp_path):
    from scripts.api import auth, server

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "test_mode", False)
    monkeypatch.setattr(trading_halt, "HALT_FILE", tmp_path / "halt.json")
    server._order_rate_timestamps.clear()
    return TestClient(server.app), server


def _ok_result():
    return SimpleNamespace(
        ok=True, error=None,
        data={"status": "ok", "orderId": 42, "permId": 420042,
              "initialStatus": "Submitted", "message": "ok"},
    )


class TestRouteLimitRefusal:
    def test_oversized_order_422_without_spawn(self, trusted_client, monkeypatch):
        client, server = trusted_client
        monkeypatch.setenv("RADON_MAX_ORDER_QTY", "100")
        spawn = AsyncMock(side_effect=AssertionError("must not spawn"))
        monkeypatch.setattr(server, "_run_ib_script_with_recovery", spawn)

        resp = client.post("/orders/place", json={
            "type": "option", "symbol": "GOOG", "action": "BUY",
            "quantity": 5000, "limitPrice": 40.0,
        })
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "ORDER_QTY_LIMIT"
        spawn.assert_not_called()

    def test_modify_new_quantity_bounded(self, trusted_client, monkeypatch):
        client, server = trusted_client
        monkeypatch.setenv("RADON_MAX_ORDER_QTY", "100")
        spawn = AsyncMock(side_effect=AssertionError("must not spawn"))
        monkeypatch.setattr(server, "_run_ib_script_with_recovery", spawn)

        resp = client.post("/orders/modify", json={
            "orderId": 7, "newQuantity": 10000,
        })
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "ORDER_QTY_LIMIT"
        spawn.assert_not_called()


class TestOrderRateCap:
    def test_burst_past_cap_429(self, trusted_client, monkeypatch):
        client, server = trusted_client
        monkeypatch.setenv("RADON_MAX_ORDERS_PER_MIN", "2")
        monkeypatch.setattr(
            server, "_run_ib_script_with_recovery", AsyncMock(return_value=_ok_result())
        )

        body = {"type": "stock", "symbol": "AAPL", "action": "BUY",
                "quantity": 1, "limitPrice": 200.0}
        assert client.post("/orders/place", json=body).status_code == 200
        assert client.post("/orders/place", json=body).status_code == 200
        third = client.post("/orders/place", json=body)
        assert third.status_code == 429
        assert third.json()["detail"]["code"] == "ORDER_RATE_LIMIT"

    def test_refused_orders_do_not_consume_budget(self, trusted_client, monkeypatch):
        """A 422-refused order must not eat the rate budget."""
        client, server = trusted_client
        monkeypatch.setenv("RADON_MAX_ORDERS_PER_MIN", "1")
        monkeypatch.setenv("RADON_MAX_ORDER_QTY", "100")
        monkeypatch.setattr(
            server, "_run_ib_script_with_recovery", AsyncMock(return_value=_ok_result())
        )

        refused = client.post("/orders/place", json={
            "type": "option", "symbol": "GOOG", "action": "BUY",
            "quantity": 5000, "limitPrice": 1.0,
        })
        assert refused.status_code == 422

        ok = client.post("/orders/place", json={
            "type": "stock", "symbol": "AAPL", "action": "BUY",
            "quantity": 1, "limitPrice": 200.0,
        })
        assert ok.status_code == 200
