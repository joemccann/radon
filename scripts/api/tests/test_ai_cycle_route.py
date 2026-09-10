"""Cache-only route: bounded concurrent reads, visible failures, no collection."""

from __future__ import annotations

import asyncio
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from scripts.api.routes import ai_cycle as route


def test_snapshot_deadlines_leave_headroom_between_layers():
    from scripts.ai_cycle.store import _SNAPSHOT_PERSIST_DEADLINE_SECONDS

    assert _SNAPSHOT_PERSIST_DEADLINE_SECONDS >= 480
    assert route._READ_DEADLINE_SECONDS == 35


@pytest.fixture(autouse=True)
def reset_cache(monkeypatch):
    monkeypatch.setattr(route, "_cached", None)
    monkeypatch.setattr(route, "_cached_at", 0.0)
    monkeypatch.setattr(route, "_inflight", None)


def client():
    app = FastAPI()
    app.include_router(route.router)
    return TestClient(app)


def test_empty_snapshot_is_successful_and_read_only(monkeypatch):
    payload = {"version": 1, "indicators": [], "shadow": {"state": "insufficient_evidence"}}
    read = Mock(return_value=payload)
    monkeypatch.setattr(route, "_read_snapshot", read)
    with client() as c:
        response = c.get("/ai-cycle")
        assert response.status_code == 200
        assert response.json() == payload
        assert "no-store" in response.headers["cache-control"]
        assert c.get("/ai-cycle").json() == payload
    read.assert_called_once_with()


def test_storage_failure_is_not_an_empty_success_or_cached(monkeypatch):
    read = Mock(side_effect=RuntimeError("https://private.example?token=secret"))
    monkeypatch.setattr(route, "_read_snapshot", read)
    with client() as c:
        for _ in range(2):
            response = c.get("/ai-cycle")
            assert response.status_code == 503
            assert "secret" not in response.text
            assert "no-store" in response.headers["cache-control"]
    assert read.call_count == 2


def test_expired_success_does_not_hide_a_failed_refresh(monkeypatch):
    monkeypatch.setattr(route, "_cached", {"version": 1})
    monkeypatch.setattr(route, "_cached_at", -1000)
    monkeypatch.setattr(route, "_read_snapshot", Mock(side_effect=TimeoutError))
    with client() as c:
        assert c.get("/ai-cycle").status_code == 503


def test_concurrent_readers_share_one_task(monkeypatch):
    calls = []

    async def load():
        calls.append(True)
        await asyncio.sleep(0.01)
        return {"version": 1}

    monkeypatch.setattr(route, "_load", load)

    async def run():
        from fastapi import Response

        return await asyncio.gather(*(route.ai_cycle(Response()) for _ in range(5)))

    assert asyncio.run(run()) == [{"version": 1}] * 5
    assert len(calls) == 1
