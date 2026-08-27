"""Operator ticker flow-report must not instant-502 when the general lane is full.

2026-08-27: `/flow-analysis/JOBY` showed ANALYZING plus
`Radon API 502: Subprocess capacity exhausted` while `/health/lite` stayed
up and informed-flow still rendered. POST `/flow-analysis/{ticker}` runs
`flow_report.py` on the general lane (cap 3). Scan storms and the sibling
`GET /informed-flow/{ticker}` spawn pin the lane; fail-fast 502 is the
wrong operator outcome — peer slots often free in seconds (same class as
`orders-sync-capacity-shed-stale` / `flow-refresh-capacity-502`).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from api.subprocess import ScriptResult  # noqa: E402


@pytest.fixture(autouse=True)
def localhost_bypass(monkeypatch):
    from scripts.api import server, auth

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    yield


@pytest.fixture(autouse=True)
def force_live_mode(monkeypatch):
    from scripts.api import server

    monkeypatch.setattr(server, "test_mode", False)
    yield


@pytest.fixture
def client():
    from scripts.api.server import app

    return TestClient(app)


def _shed() -> ScriptResult:
    return ScriptResult(ok=False, error="Subprocess capacity exhausted")


def _ok_report(ticker: str = "JOBY") -> ScriptResult:
    return ScriptResult(
        ok=True,
        data={
            "ticker": ticker,
            "analysis": {"num_prints": 12},
            "dark_pool": {"daily": [{"date": "2026-08-26", "num_prints": 12}]},
        },
    )


def test_flow_report_retries_capacity_shed_then_succeeds(client):
    """First claim refused, a later claim admits — ANALYZE must return 200."""
    calls = {"n": 0}

    async def flaky(script, args=None, timeout=30, **_kwargs):
        assert script == "flow_report.py"
        calls["n"] += 1
        if calls["n"] == 1:
            return _shed()
        return _ok_report()

    with patch("scripts.api.server.run_script", side_effect=flaky):
        with patch("scripts.api.server.asyncio.sleep", new=AsyncMock()) as slept:
            resp = client.post("/flow-analysis/JOBY")

    assert resp.status_code == 200, resp.text
    assert resp.json()["ticker"] == "JOBY"
    assert calls["n"] == 2
    slept.assert_awaited()


def test_flow_report_persistent_capacity_shed_still_502(client):
    """Hour-long scans can outlast the retry budget. Still 502, still no spawn."""
    from scripts.api import server

    async def always_shed(script, args=None, timeout=30, **_kwargs):
        return _shed()

    with patch("scripts.api.server.run_script", side_effect=always_shed) as run:
        with patch("scripts.api.server.asyncio.sleep", new=AsyncMock()):
            resp = client.post("/flow-analysis/JOBY")

    assert resp.status_code == 502
    assert "capacity exhausted" in resp.json()["detail"].lower()
    assert run.await_count == 1 + server.FLOW_REPORT_SHED_RETRIES


def test_flow_report_real_script_failure_does_not_retry(client):
    """UW/script 502 is not a shed. Do not burn the retry budget on it."""
    async def boom(script, args=None, timeout=30, **_kwargs):
        return ScriptResult(ok=False, error="Script exited with code 1")

    with patch("scripts.api.server.run_script", side_effect=boom) as run:
        with patch("scripts.api.server.asyncio.sleep", new=AsyncMock()) as slept:
            resp = client.post("/flow-analysis/JOBY")

    assert resp.status_code == 502
    assert "exited with code 1" in resp.json()["detail"]
    assert run.await_count == 1
    slept.assert_not_awaited()
