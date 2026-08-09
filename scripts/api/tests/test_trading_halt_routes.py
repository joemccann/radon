"""REL-004 — kill-switch routes: halt/resume/status, order-route refusals,
cancel-all, and the one-shot /trading/kill (RELIABILITY_AUDIT.md R-001)."""

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
    monkeypatch.setattr(trading_halt, "HALT_FILE", tmp_path / "trading_halt.json")
    return TestClient(server.app), server


_STOCK_ORDER = {
    "type": "stock",
    "symbol": "AAPL",
    "action": "BUY",
    "quantity": 1,
    "limitPrice": 200.0,
    "tif": "DAY",
}


class TestOrderRoutesRefuseWhenHalted:
    def test_place_refuses_409_without_spawning(self, trusted_client, monkeypatch):
        client, server = trusted_client
        trading_halt.set_halt(reason="drill", actor="test")
        spawn = AsyncMock(side_effect=AssertionError("subprocess must not spawn"))
        monkeypatch.setattr(server, "_run_ib_script_with_recovery", spawn)

        resp = client.post("/orders/place", json=_STOCK_ORDER)
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["code"] == "TRADING_HALTED"
        assert "drill" in detail["reason"]
        spawn.assert_not_called()

    def test_modify_refuses_409(self, trusted_client, monkeypatch):
        client, server = trusted_client
        trading_halt.set_halt(reason="drill", actor="test")
        spawn = AsyncMock(side_effect=AssertionError("subprocess must not spawn"))
        monkeypatch.setattr(server, "run_script", spawn)

        resp = client.post(
            "/orders/modify",
            json={"orderId": 1, "limitPrice": 1.0},
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "TRADING_HALTED"

    def test_place_allowed_when_not_halted(self, trusted_client, monkeypatch):
        client, server = trusted_client
        ok = SimpleNamespace(
            ok=True,
            error=None,
            data={"status": "ok", "orderId": 42, "permId": 420042,
                  "initialStatus": "Submitted", "message": "ok"},
        )
        monkeypatch.setattr(
            server, "_run_ib_script_with_recovery", AsyncMock(return_value=ok)
        )
        resp = client.post("/orders/place", json=_STOCK_ORDER)
        assert resp.status_code == 200


class TestHaltLifecycleRoutes:
    def test_halt_status_resume_roundtrip(self, trusted_client):
        client, _ = trusted_client

        status = client.get("/trading/status").json()
        assert status["halted"] is False

        resp = client.post("/trading/halt", json={"reason": "manual drill"})
        assert resp.status_code == 200
        assert resp.json()["halted"] is True

        status = client.get("/trading/status").json()
        assert status["halted"] is True
        assert status["reason"] == "manual drill"

        resp = client.post("/trading/resume")
        assert resp.status_code == 200
        assert client.get("/trading/status").json()["halted"] is False


class TestCancelAll:
    def test_cancel_all_requires_confirm(self, trusted_client, monkeypatch):
        client, server = trusted_client
        spawn = AsyncMock(side_effect=AssertionError("must not run without confirm"))
        monkeypatch.setattr(server, "run_script", spawn)
        resp = client.post("/orders/cancel-all", json={})
        assert resp.status_code == 400
        spawn.assert_not_called()

    def test_cancel_all_runs_script_with_confirm(self, trusted_client, monkeypatch):
        client, server = trusted_client
        ok = SimpleNamespace(
            ok=True, error=None,
            data={"status": "ok", "open_before": 3, "remaining": 0},
        )
        spawn = AsyncMock(return_value=ok)
        monkeypatch.setattr(server, "run_script", spawn)

        resp = client.post("/orders/cancel-all", json={"confirm": True})
        assert resp.status_code == 200
        assert resp.json()["remaining"] == 0
        assert spawn.call_args[0][0] == "ib_cancel_all.py"

    def test_kill_halts_then_cancels(self, trusted_client, monkeypatch):
        """The one-action kill switch: sets the halt flag FIRST (no new
        orders can race in), then mass-cancels working orders."""
        client, server = trusted_client
        ok = SimpleNamespace(
            ok=True, error=None,
            data={"status": "ok", "open_before": 2, "remaining": 0},
        )

        halted_at_cancel_time = {}

        async def spawn(script, *a, **k):
            halted_at_cancel_time["value"] = trading_halt.is_trading_halted()
            return ok

        monkeypatch.setattr(server, "run_script", spawn)

        resp = client.post("/trading/kill", json={"reason": "emergency"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["halted"] is True
        assert body["cancel"]["remaining"] == 0
        assert halted_at_cancel_time["value"] is True
        assert trading_halt.is_trading_halted() is True
