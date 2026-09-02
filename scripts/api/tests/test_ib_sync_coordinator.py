"""Portfolio/orders refreshes share one subprocess and one resulting snapshot."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from scripts.api import server
from scripts.api.subprocess import ScriptResult


@pytest.fixture(autouse=True)
def _fresh_coordinator(monkeypatch):
    monkeypatch.setattr(
        server,
        "_ib_sync_coordinator",
        server._IBSyncCoordinator(min_age_secs=60),
    )


@pytest.mark.asyncio
async def test_concurrent_portfolio_route_and_background_share_one_run(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0
    payload = {"bankroll": 125_000, "positions": [{"ticker": "SPY"}]}

    async def run(*args, **kwargs):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return ScriptResult(ok=True, data=payload)

    monkeypatch.setattr(server, "_run_ib_script_with_recovery", run)

    background = asyncio.create_task(server._bg_sync_via_subprocess())
    await started.wait()
    explicit = asyncio.create_task(server.portfolio_sync())
    await asyncio.sleep(0)
    release.set()

    _ignored, explicit_payload = await asyncio.gather(background, explicit)
    assert calls == 1
    assert explicit_payload == payload


@pytest.mark.asyncio
async def test_recent_portfolio_success_obeys_minimum_age(monkeypatch):
    calls = 0
    payload = {"bankroll": 125_000, "positions": []}

    async def run(*args, **kwargs):
        nonlocal calls
        calls += 1
        return ScriptResult(ok=True, data=payload)

    monkeypatch.setattr(server, "_run_ib_script_with_recovery", run)

    assert await server.portfolio_sync() == payload
    assert await server.portfolio_sync() == payload
    assert calls == 1


@pytest.mark.asyncio
async def test_failed_portfolio_sync_is_not_cached(monkeypatch):
    calls = 0

    async def run(*args, **kwargs):
        nonlocal calls
        calls += 1
        return ScriptResult(ok=False, error="IB unavailable")

    monkeypatch.setattr(server, "_run_ib_script_with_recovery", run)

    for _ in range(2):
        with pytest.raises(HTTPException):
            await server.portfolio_sync()
    assert calls == 2


@pytest.mark.asyncio
async def test_orders_tick_and_route_share_run_and_snapshot(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()
    run_calls = 0
    read_calls = 0
    snapshot = {
        "last_sync": "2026-07-10T20:00:00Z",
        "open_orders": [{"permId": 1}],
        "executed_orders": [],
        "open_count": 1,
        "executed_count": 0,
    }

    async def run(*args, **kwargs):
        nonlocal run_calls
        run_calls += 1
        started.set()
        await release.wait()
        return ScriptResult(ok=True, data={})

    async def read_snapshot():
        nonlocal read_calls
        read_calls += 1
        return snapshot

    monkeypatch.setattr(server, "test_mode", False)
    monkeypatch.setattr(server, "_is_orders_session_live_now_et", lambda: True)
    monkeypatch.setattr(server, "_pool_has_any_connection", lambda: True)
    monkeypatch.setattr(server, "_run_ib_script_with_recovery", run)
    monkeypatch.setattr(server, "_read_orders_snapshot_from_db", read_snapshot)

    tick = asyncio.create_task(server._orders_sync_tick())
    await started.wait()
    route = asyncio.create_task(server.orders_refresh())
    await asyncio.sleep(0)
    release.set()

    _ignored, route_payload = await asyncio.gather(tick, route)
    assert run_calls == 1
    assert read_calls == 1
    assert route_payload == snapshot
