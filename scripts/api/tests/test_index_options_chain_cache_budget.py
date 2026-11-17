"""PERF-058: GET /index-options/chain must not spawn on a same-day cache hit.

The VIX/SPX/NDX order ticket fetches listed contracts via
useIndexOptionsChain (cache: no-store) on every symbol/expiry change.
FastAPI still ran ib_chain.py (new interpreter, IB secdef) on every GET,
even when the listed book for that expiry was written earlier the same
ET day. Listed index-option contracts do not change intra-session — the
same same-day rule /futures/chain already uses. A stamped cache with
contracts must return the file and spawn 0 times; a cross-day cache
still goes live.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from scripts.api import server


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "test_mode", False)
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    return TestClient(server.app, raise_server_exceptions=False)


def _today_et_str() -> str:
    return datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def _payload(*, expiry: str = "20260616") -> dict:
    return {
        "symbol": "VIX",
        "exchange": "CBOE",
        "tradingClass": "VIX",
        "expiry": expiry,
        "expirations": [expiry, "20260721"],
        "contracts": [
            {
                "conId": 1,
                "strike": 20.0,
                "right": "C",
                "lastTradeDateOrContractMonth": expiry,
            },
            {
                "conId": 2,
                "strike": 20.0,
                "right": "P",
                "lastTradeDateOrContractMonth": expiry,
            },
        ],
        "count": 2,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "as_of_date": _today_et_str(),
        "stale": False,
    }


def _write_cache(tmp_path, payload: dict, *, expiry: str = "20260616") -> None:
    path = tmp_path / f"index_options_chain_VIX_{expiry}.json"
    path.write_text(json.dumps(payload))


def test_same_day_index_options_cache_spawns_zero_subprocesses(client, monkeypatch, tmp_path):
    cached = _payload()
    _write_cache(tmp_path, cached)
    runner = AsyncMock(
        return_value=server.ScriptResult(ok=True, data=cached, error=None, exit_code=0)
    )
    monkeypatch.setattr(server, "run_script", runner)

    response = client.get("/index-options/chain?symbol=VIX&expiry=20260616")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["symbol"] == "VIX"
    assert body["count"] == 2
    assert body["contracts"][0]["conId"] == 1
    assert runner.await_count == 0, (
        f"subprocess spawns {runner.await_count} "
        "(want 0 on a same-day index-options chain cache hit)"
    )


def test_cross_day_index_options_cache_still_goes_live(client, monkeypatch, tmp_path):
    stale = _payload()
    stale["as_of_date"] = "2000-01-01"
    _write_cache(tmp_path, stale)
    live = _payload()
    runner = AsyncMock(
        return_value=server.ScriptResult(ok=True, data=live, error=None, exit_code=0)
    )
    monkeypatch.setattr(server, "run_script", runner)

    response = client.get("/index-options/chain?symbol=VIX&expiry=20260616")

    assert response.status_code == 200, response.text
    assert runner.await_count == 1, (
        f"subprocess spawns {runner.await_count} "
        "(want 1 on a cross-day index-options chain cache)"
    )
    assert response.json()["stale"] is False
