"""TEST_MODE demo guards on scan-trigger endpoints.

On the demo backend (RADON_API_TEST_MODE=1, no UW_TOKEN / IB) every
scan-trigger endpoint must serve the seeded Turso snapshot instead of
spawning the real scan subprocess — the raw UWAuthError 502 leaked
operator env guidance to demo users (2026-07-03).

Strategy: force ``server.test_mode = True``, patch
``demo_scan.db_http.hrana_execute`` to return a canned payload row, and
patch ``server.run_script`` with a tripwire that fails the test if any
endpoint still reaches the subprocess layer.
"""
from __future__ import annotations

import json
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


@pytest.fixture(autouse=True)
def force_test_mode(monkeypatch):
    from scripts.api import server

    monkeypatch.setattr(server, "test_mode", True)
    # demo_scan refuses DB reads under pytest (the real prod TURSO_DB_URL is
    # in os.environ via server.py load_dotenv); every test here patches
    # hrana_execute, so the override never reaches a real database.
    monkeypatch.setenv("RADON_ALLOW_DB_IN_TESTS", "1")
    yield


@pytest.fixture(autouse=True)
def subprocess_tripwire(monkeypatch):
    from scripts.api import server

    async def _boom(*args, **kwargs):
        raise AssertionError("run_script must not be called in TEST_MODE")

    monkeypatch.setattr(server, "run_script", _boom)
    yield


@pytest.fixture()
def client():
    from scripts.api.server import app

    return TestClient(app)


SNAPSHOT = {"scan_time": "2026-07-02T14:00:00Z", "results": [{"ticker": "NVDA"}]}


def _hrana_returning(payload):
    def _fake(sql, args=(), timeout=None):
        return [(json.dumps(payload),)]

    return _fake


SNAPSHOT_ENDPOINTS = [
    "/scan",
    "/discover",
    "/flow-analysis",
    "/theta-harvester/scan",
    "/strength-confirmation/scan",
    "/leap/scan",
    "/garch-convergence/scan",
    "/vcg/scan",
    "/gex/scan",
    "/gamma-rotation/scan",
    "/regime/scan",
    "/breadth/scan",
]


@pytest.mark.parametrize("path", SNAPSHOT_ENDPOINTS)
def test_scan_endpoints_serve_seeded_snapshot_in_test_mode(client, path):
    from scripts.api import demo_scan

    with patch.object(demo_scan.db_http, "hrana_execute", _hrana_returning(SNAPSHOT)):
        resp = client.post(path)
    assert resp.status_code == 200
    assert resp.json() == SNAPSHOT


@pytest.mark.parametrize("path", SNAPSHOT_ENDPOINTS)
def test_scan_endpoints_fall_back_to_empty_envelope_when_unseeded(client, path):
    from scripts.api import demo_scan

    def _no_rows(sql, args=(), timeout=None):
        return []

    with patch.object(demo_scan.db_http, "hrana_execute", _no_rows):
        resp = client.post(path)
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, dict)
    assert "UW_TOKEN" not in json.dumps(body)


def test_flow_report_ticker_serves_cached_report_in_test_mode(client, tmp_path, monkeypatch):
    from scripts.api import server

    monkeypatch.setattr(server, "_FLOW_REPORTS_DIR", tmp_path)
    (tmp_path / "MU.json").write_text(json.dumps({"ticker": "MU", "analysis": {}}))
    resp = client.post("/flow-analysis/MU")
    assert resp.status_code == 200
    assert resp.json()["ticker"] == "MU"


def test_flow_report_ticker_returns_friendly_demo_error_when_uncached(
    client, tmp_path, monkeypatch
):
    from scripts.api import server

    monkeypatch.setattr(server, "_FLOW_REPORTS_DIR", tmp_path)
    resp = client.post("/flow-analysis/ZZZZ")
    assert resp.status_code == 200
    body = resp.json()
    assert "demo" in body["error"].lower()
    assert "UW_TOKEN" not in body["error"]


@pytest.mark.parametrize("path,body", [
    ("/flow-surprise", {"metric": "flow_strength"}),
    ("/forecast/chronos", {"ticker": "NVDA"}),
])
def test_unsnapshotted_features_return_friendly_demo_error(client, path, body):
    resp = client.post(path, json=body)
    assert resp.status_code == 200
    payload = resp.json()
    assert "demo" in payload["error"].lower()
    assert "UW_TOKEN" not in payload["error"]
