"""Tests for /index-options/chain (Phase 3 — VIX/SPX options).

Subprocess-backed since the rewrite — patches `run_script` rather
than `ib_pool`.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture(autouse=True)
def localhost_bypass(monkeypatch):
    from scripts.api import server, auth
    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    yield


@pytest.fixture
def client():
    from scripts.api.server import app
    return TestClient(app)


def _fake_script_result(*, ok=True, data=None, error=None, exit_code=0):
    from api.subprocess import ScriptResult
    return ScriptResult(ok=ok, data=data, error=error, exit_code=exit_code)


def test_chain_returns_payload(client):
    payload = {
        "symbol": "VIX",
        "exchange": "CBOE",
        "tradingClass": "VIX",
        "expirations": ["20260616", "20260721"],
        "contracts": [
            {"conId": 1, "strike": 20.0, "right": "C", "lastTradeDateOrContractMonth": "20260616"},
            {"conId": 2, "strike": 20.0, "right": "P", "lastTradeDateOrContractMonth": "20260616"},
        ],
        "count": 2,
    }

    async def _stub(*args, **kwargs):
        return _fake_script_result(ok=True, data=payload)

    with patch("scripts.api.server.run_script", side_effect=_stub) as run_mock:
        resp = client.get("/index-options/chain?symbol=VIX&expiry=20260616")
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "VIX"
    assert body["tradingClass"] == "VIX"
    assert body["expirations"] == ["20260616", "20260721"]
    args, kwargs = run_mock.call_args
    assert args[0] == "ib_chain.py"
    assert "option" in args[1]
    assert "VIX" in args[1]
    assert kwargs["timeout"] == 24.0


def test_unsupported_symbol_returns_400(client):
    resp = client.get("/index-options/chain?symbol=AAPL")
    assert resp.status_code == 400
    assert "index options not supported" in resp.json()["detail"]


def test_expiry_passed_through(client):
    payload = {"symbol": "VIX", "exchange": "CBOE", "tradingClass": "VIX",
               "expirations": [], "contracts": [], "count": 0}

    async def _stub(*args, **kwargs):
        return _fake_script_result(ok=True, data=payload)

    with patch("scripts.api.server.run_script", side_effect=_stub) as run_mock:
        resp = client.get("/index-options/chain?symbol=VIX&expiry=20260616")
    assert resp.status_code == 200
    args, kwargs = run_mock.call_args
    assert "--expiry" in args[1]
    assert "20260616" in args[1]
    assert kwargs["timeout"] == 24.0


def test_subprocess_failure_returns_502(client):
    async def _stub(*args, **kwargs):
        return _fake_script_result(ok=False, error="boom", exit_code=1)

    with patch("scripts.api.server.run_script", side_effect=_stub) as run_mock:
        resp = client.get("/index-options/chain?symbol=VIX&expiry=20260616")
    assert resp.status_code == 502
    assert run_mock.called


def test_case_insensitive(client):
    payload = {"symbol": "VIX", "exchange": "CBOE", "tradingClass": "VIX",
               "expirations": [], "contracts": [], "count": 0}

    async def _stub(*args, **kwargs):
        return _fake_script_result(ok=True, data=payload)

    with patch("scripts.api.server.run_script", side_effect=_stub) as run_mock:
        resp = client.get("/index-options/chain?symbol=vix&expiry=20260616")
    assert resp.status_code == 200
    args, _ = run_mock.call_args
    assert "VIX" in args[1]


def test_missing_expiry_returns_400_without_spawn(client):
    with patch("scripts.api.server.run_script") as run_mock:
        resp = client.get("/index-options/chain?symbol=VIX")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "expiry is required"
    run_mock.assert_not_called()
    with patch("scripts.api.server.run_script") as blank_mock:
        blank = client.get("/index-options/chain?symbol=VIX&expiry=%20")
    assert blank.status_code == 400
    blank_mock.assert_not_called()
