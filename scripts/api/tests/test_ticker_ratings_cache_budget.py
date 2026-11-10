"""PERF-051: GET /ticker/ratings must not spawn on a fresh cache hit.

The Ratings tab fetches on every first open. FastAPI still ran
fetch_analyst_ratings_rh.py (new interpreter, IB connect, Robinhood
consensus, UW fallback) on every GET, even when
data/analyst_ratings_cache.json held a row written minutes earlier.
Analyst consensus does not move intra-session; the writer's
CACHE_TTL_HOURS is 4. A stamped cache inside that TTL must return the
row and spawn 0 times; a stale or missing cache still goes live.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from scripts.api import server


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "test_mode", False)
    monkeypatch.setattr(server, "_ANALYST_RATINGS_CACHE", tmp_path / "analyst_ratings_cache.json")
    return TestClient(server.app, raise_server_exceptions=False)


def _row(*, ticker: str = "AMD", fetched_at: str, error: str | None = None) -> dict:
    body = {
        "ticker": ticker,
        "fetched_at": fetched_at,
        "source": "rh",
        "ratings": {
            "strong_buy": 0,
            "buy": 26,
            "hold": 4,
            "sell": 0,
            "strong_sell": 0,
            "total": 30,
            "buy_pct": 86.7,
            "sell_pct": 0.0,
        },
        "recommendation": "buy",
        "target_price": {"mean": 120.0, "high": 150.0, "low": 90.0, "median": None, "count": 30},
        "recent_changes": [],
    }
    if error is not None:
        body["error"] = error
    return body


def _write_cache(path: Path, row: dict) -> None:
    path.write_text(
        json.dumps({"ratings": {row["ticker"]: row}, "last_updated": row["fetched_at"]})
    )


def test_fresh_ratings_cache_spawns_zero_subprocesses(client, monkeypatch, tmp_path):
    row = _row(fetched_at=datetime.now().isoformat())
    _write_cache(tmp_path / "analyst_ratings_cache.json", row)
    runner = AsyncMock()
    monkeypatch.setattr(server, "run_script", runner)

    response = client.get("/ticker/ratings?ticker=amd")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ticker"] == "AMD"
    assert body["recommendation"] == "buy"
    assert body["ratings"]["buy_pct"] == 86.7
    assert runner.await_count == 0, (
        f"subprocess spawns {runner.await_count} "
        "(want 0 on a fresh analyst-ratings cache hit)"
    )


def test_stale_ratings_cache_still_goes_live(client, monkeypatch, tmp_path):
    stale = datetime.now() - timedelta(hours=5)
    _write_cache(
        tmp_path / "analyst_ratings_cache.json",
        _row(fetched_at=stale.isoformat()),
    )
    live = _row(fetched_at=datetime.now().isoformat())
    runner = AsyncMock(
        return_value=server.ScriptResult(ok=True, data=[live], error=None, exit_code=0)
    )
    monkeypatch.setattr(server, "run_script", runner)

    response = client.get("/ticker/ratings?ticker=AMD")

    assert response.status_code == 200, response.text
    assert response.json()["fetched_at"] == live["fetched_at"]
    runner.assert_awaited_once_with(
        "fetch_analyst_ratings_rh.py", ["AMD", "--json"], timeout=60
    )


def test_error_row_in_cache_still_goes_live(client, monkeypatch, tmp_path):
    _write_cache(
        tmp_path / "analyst_ratings_cache.json",
        _row(fetched_at=datetime.now().isoformat(), error="UW token expired"),
    )
    live = _row(fetched_at=datetime.now().isoformat())
    runner = AsyncMock(
        return_value=server.ScriptResult(ok=True, data=[live], error=None, exit_code=0)
    )
    monkeypatch.setattr(server, "run_script", runner)

    response = client.get("/ticker/ratings?ticker=AMD")

    assert response.status_code == 200, response.text
    assert "error" not in response.json()
    runner.assert_awaited_once()
