"""FastAPI POST /orders/whatif — read-only IB margin preview endpoint.

Anonymous-caller denial is already covered by the auto-discovering
test_route_authz_matrix (whatif is a non-exempt route). These tests cover the
test-mode happy path, a mocked live success, and IB-error → 502 propagation.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture
def trusted_client(monkeypatch):
    from scripts.api import auth, server

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    return TestClient(server.app)


_COMBO = {
    "type": "combo",
    "symbol": "AVGO",
    "action": "BUY",
    "quantity": 1,
    "limitPrice": -1.17,
    "tif": "DAY",
    "legs": [
        {"action": "SELL", "right": "C", "strike": 1250, "expiry": "20260626", "ratio": 2},
        {"action": "BUY", "right": "C", "strike": 1200, "expiry": "20260626", "ratio": 1},
    ],
}


def test_whatif_test_mode_returns_synthetic_margin(trusted_client, monkeypatch):
    from scripts.api import server

    monkeypatch.setattr(server, "test_mode", True)
    res = trusted_client.post("/orders/whatif", json=_COMBO)
    assert res.status_code == 200
    body = res.json()
    assert body["whatIf"] is True
    assert body["source"] == "ib"
    assert isinstance(body["initMargin"], (int, float))


def test_whatif_live_success_returns_margin(trusted_client, monkeypatch):
    from scripts.api import server

    monkeypatch.setattr(server, "test_mode", False)

    async def fake_recovery(*_a, **_k):
        return SimpleNamespace(
            ok=True,
            data={"status": "ok", "whatIf": True, "initMargin": 4250.0, "source": "ib"},
            error=None,
        )

    monkeypatch.setattr(server, "_run_ib_script_with_recovery", fake_recovery)
    res = trusted_client.post("/orders/whatif", json=_COMBO)
    assert res.status_code == 200
    assert res.json()["initMargin"] == 4250.0


def test_whatif_ib_error_propagates_502(trusted_client, monkeypatch):
    from scripts.api import server

    monkeypatch.setattr(server, "test_mode", False)

    async def fake_recovery(*_a, **_k):
        return SimpleNamespace(
            ok=True,
            data={"status": "error", "message": "IB what-if timed out — awaiting 2FA"},
            error=None,
        )

    monkeypatch.setattr(server, "_run_ib_script_with_recovery", fake_recovery)
    res = trusted_client.post("/orders/whatif", json=_COMBO)
    assert res.status_code == 502
