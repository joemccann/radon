"""GET /options/ib-quotes: chat's IB-priced chain, one subprocess per call."""

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


def _result(*, ok=True, data=None, error=None):
    from api.subprocess import ScriptResult

    return ScriptResult(ok=ok, data=data, error=error, exit_code=0 if ok else 1)


def test_ib_quotes_spawns_quote_script_with_normalized_args(client):
    payload = {"ticker": "SPCX", "spot": 158.65, "source": "ib", "expirations": {"2026-10-30": []}}

    async def _stub(*args, **kwargs):
        return _result(data=payload)

    with patch("scripts.api.server._run_ib_script_with_recovery", side_effect=_stub) as run_mock:
        resp = client.get("/options/ib-quotes?symbol=spcx&expiries=2026-10-30,20261120&right=c&wings=4")

    assert resp.status_code == 200
    assert resp.json() == payload
    args, kwargs = run_mock.call_args
    assert args[0] == "ib_option_quotes.py"
    assert args[1] == ["--symbol", "SPCX", "--expiries", "20261030,20261120", "--wings", "4", "--right", "C"]
    from scripts.api import server

    assert kwargs["timeout"] == server._IB_OPTION_QUOTES_TIMEOUT_S


@pytest.mark.parametrize(
    "query",
    [
        "symbol=SP;CX&expiries=20261030",
        "symbol=SPCX&expiries=",
        "symbol=SPCX&expiries=2026-13",
        "symbol=SPCX&expiries=20261030&right=X",
        "symbol=SPCX&expiries=" + ",".join(["20261030"] * 13),
    ],
)
def test_ib_quotes_rejects_bad_input_without_spawning(client, query):
    with patch("scripts.api.server._run_ib_script_with_recovery") as run_mock:
        resp = client.get(f"/options/ib-quotes?{query}")
    assert resp.status_code == 400
    run_mock.assert_not_called()


def test_ib_quotes_maps_timeout_to_504(client):
    async def _stub(*args, **kwargs):
        return _result(ok=False, error="Script timed out after 60s")

    with patch("scripts.api.server._run_ib_script_with_recovery", side_effect=_stub):
        resp = client.get("/options/ib-quotes?symbol=SPCX&expiries=20261030")
    assert resp.status_code == 504


def test_ib_quotes_maps_script_error_envelope_to_502(client):
    async def _stub(*args, **kwargs):
        return _result(data={"error": "Could not qualify SPCX"})

    with patch("scripts.api.server._run_ib_script_with_recovery", side_effect=_stub):
        resp = client.get("/options/ib-quotes?symbol=SPCX&expiries=20261030")
    assert resp.status_code == 502
    assert "Could not qualify" in resp.json()["detail"]
