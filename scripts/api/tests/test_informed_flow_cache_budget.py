"""PERF-023: GET /informed-flow/{ticker} must not spawn on a fresh cache hit.

The flow-analysis panel polls every 10 minutes. FastAPI still ran
fetch_informed_flow.py (new interpreter, UW session, three UW GETs) on
every GET, even when data/informed_flow/{TICKER}.json was written seconds
earlier. Congress / insider / 13F cadence is the 30-minute open window in
serviceHealthWindows. A stamped cache inside that TTL must return the file
and spawn 0 times; a stale or unstamped cache still goes live.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from scripts.api import server


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "test_mode", False)
    monkeypatch.setattr(server, "_INFORMED_FLOW_DIR", tmp_path)
    return TestClient(server.app, raise_server_exceptions=False)


def _payload(*, scan_time: str | None) -> dict:
    body = {
        "ticker": "AAPL",
        "congress_trades": [{"person": "X", "side": "BUY"}],
        "insider_trades": [],
        "institutional_summary": {"ownership_pct": 1.2},
        "missing": False,
    }
    if scan_time is not None:
        body["scan_time"] = scan_time
    return body


def _write_cache(tmp_path, payload: dict) -> None:
    (tmp_path / "AAPL.json").write_text(json.dumps(payload))


def test_fresh_informed_flow_cache_spawns_zero_subprocesses(client, monkeypatch, tmp_path):
    payload = _payload(
        scan_time=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    _write_cache(tmp_path, payload)
    runner = AsyncMock()
    monkeypatch.setattr(server, "run_script", runner)

    response = client.get("/informed-flow/aapl")

    assert response.status_code == 200, response.text
    assert response.json()["ticker"] == "AAPL"
    assert response.json()["congress_trades"] == payload["congress_trades"]
    assert runner.await_count == 0, (
        f"subprocess spawns {runner.await_count} "
        "(want 0 on a fresh informed-flow cache hit)"
    )


def test_stale_informed_flow_cache_still_goes_live(client, monkeypatch, tmp_path):
    stale = datetime.now(timezone.utc) - timedelta(hours=2)
    _write_cache(
        tmp_path,
        _payload(scan_time=stale.isoformat().replace("+00:00", "Z")),
    )
    live = _payload(
        scan_time=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    runner = AsyncMock(
        return_value=server.ScriptResult(ok=True, data=live, error=None, exit_code=0)
    )
    monkeypatch.setattr(server, "run_script", runner)

    response = client.get("/informed-flow/aapl")

    assert response.status_code == 200, response.text
    assert response.json() == live
    runner.assert_awaited_once_with(
        "fetch_informed_flow.py", ["AAPL", "--json"], timeout=60
    )
