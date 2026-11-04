"""PERF-043: GET /event-odds/{ticker} must not spawn on a fresh cache hit.

The overlay is request-driven (no VPS timer). FastAPI still ran
fetch_event_odds.py (new interpreter, Polymarket session, 500-market list,
plus an options-skew fetch) on every GET, even when
data/event_odds/{TICKER}.json was written minutes earlier. Polymarket
midpoints do not move intra-minute; the open-hours window in
serviceHealthWindows is 7 hours. A stamped cache inside that TTL must
return the file and spawn 0 times; a stale or unstamped cache still goes
live.
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
    monkeypatch.setattr(server, "_EVENT_ODDS_DIR", tmp_path)
    return TestClient(server.app, raise_server_exceptions=False)


def _payload(*, scan_time: str | None) -> dict:
    body = {
        "ticker": "SPX",
        "overlays": [
            {
                "question": "Will the Fed cut rates?",
                "market_ticker": "SPX",
                "polymarket_prob": 0.62,
                "options_prob": 0.55,
                "divergence": 0.07,
            }
        ],
        "missing": False,
    }
    if scan_time is not None:
        body["scan_time"] = scan_time
    return body


def _write_cache(tmp_path, payload: dict) -> None:
    (tmp_path / "SPX.json").write_text(json.dumps(payload))


def test_fresh_event_odds_cache_spawns_zero_subprocesses(client, monkeypatch, tmp_path):
    payload = _payload(
        scan_time=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    _write_cache(tmp_path, payload)
    runner = AsyncMock()
    monkeypatch.setattr(server, "run_script", runner)

    response = client.get("/event-odds/spx")

    assert response.status_code == 200, response.text
    assert response.json()["ticker"] == "SPX"
    assert response.json()["overlays"] == payload["overlays"]
    assert runner.await_count == 0, (
        f"subprocess spawns {runner.await_count} "
        "(want 0 on a fresh event-odds cache hit)"
    )


def test_stale_event_odds_cache_still_goes_live(client, monkeypatch, tmp_path):
    stale = datetime.now(timezone.utc) - timedelta(hours=8)
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

    response = client.get("/event-odds/spx")

    assert response.status_code == 200, response.text
    assert response.json() == live
    runner.assert_awaited_once_with(
        "fetch_event_odds.py", ["SPX", "--json"], timeout=60
    )
