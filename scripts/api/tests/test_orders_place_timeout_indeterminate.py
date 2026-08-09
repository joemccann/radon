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
