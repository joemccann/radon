"""Tests for equity /options chain subprocess routing."""

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
    from scripts.api import auth, server

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


def test_options_chain_uses_equity_chain_timeout(client):
    payload = {
        "symbol": "MSFT",
        "expiry": "20260717",
        "exchange": "SMART",
        "strikes": [350.0, 352.5, 355.0],
        "multiplier": "100",
    }

    async def _stub(*args, **kwargs):
        return _fake_script_result(ok=True, data=payload)

    with patch("scripts.api.server._run_ib_script_with_recovery", side_effect=_stub) as run_mock:
        resp = client.get("/options/chain?symbol=MSFT&expiry=20260717")

    assert resp.status_code == 200
    _, kwargs = run_mock.call_args
    from scripts.api import server

    assert kwargs["timeout"] == server._EQUITY_OPTIONS_CHAIN_TIMEOUT_S
    assert kwargs["timeout"] == 30.0


def test_options_expirations_uses_equity_chain_timeout(client):
    payload = {"symbol": "MSFT", "expirations": ["20260717", "20260724"]}

    async def _stub(*args, **kwargs):
        return _fake_script_result(ok=True, data=payload)

    with patch("scripts.api.server.run_script", side_effect=_stub) as run_mock:
        resp = client.get("/options/expirations?symbol=MSFT")

    assert resp.status_code == 200
    _, kwargs = run_mock.call_args
    from scripts.api import server

    assert kwargs["timeout"] == server._EQUITY_OPTIONS_CHAIN_TIMEOUT_S
    assert kwargs["timeout"] == 30.0
