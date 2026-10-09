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


def _orders_sync_stubs(monkeypatch, run):
    snapshot = {
        "last_sync": "2026-07-10T20:00:00Z",
        "open_orders": [],
        "executed_orders": [],
        "open_count": 0,
        "executed_count": 0,
    }

    async def read_snapshot():
        return snapshot

    monkeypatch.setattr(server, "test_mode", False)
    monkeypatch.setattr(server, "_run_ib_script_with_recovery", run)
    monkeypatch.setattr(server, "_read_orders_snapshot_from_db", read_snapshot)


async def _yield_until(predicate, turns: int = 20):
    for _ in range(turns):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition not met")


@pytest.mark.asyncio
async def test_fresh_orders_sync_does_not_reuse_a_run_started_earlier(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def run(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await release.wait()
        return ScriptResult(ok=True, data={})

    _orders_sync_stubs(monkeypatch, run)
    old = asyncio.create_task(server._coordinated_orders_sync())
    await started.wait()
    fresh = asyncio.create_task(server._coordinated_orders_sync(fresh=True))
    await _yield_until(lambda: calls == 1 and not fresh.done())
    assert calls == 1
    release.set()
    await asyncio.gather(old, fresh)
    assert calls == 2


@pytest.mark.asyncio
async def test_fresh_ignores_success_that_started_before_the_call(monkeypatch):
    calls = 0

    async def run(*args, **kwargs):
        nonlocal calls
        calls += 1
        return ScriptResult(ok=True, data={})

    _orders_sync_stubs(monkeypatch, run)
    await server._coordinated_orders_sync()
    await server._coordinated_orders_sync(fresh=True)
    assert calls == 2
    await server._coordinated_orders_sync()
    assert calls == 2


@pytest.mark.asyncio
async def test_two_fresh_callers_share_the_sync_started_after_both_arrived(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def run(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await release.wait()
        return ScriptResult(ok=True, data={})

    _orders_sync_stubs(monkeypatch, run)
    old = asyncio.create_task(server._coordinated_orders_sync())
    await started.wait()
    first = asyncio.create_task(server._coordinated_orders_sync(fresh=True))
    second = asyncio.create_task(server._coordinated_orders_sync(fresh=True))
    await _yield_until(lambda: calls == 1 and not first.done() and not second.done())
    release.set()
    await asyncio.gather(old, first, second)
    assert calls == 2


def test_orders_refresh_http_header_sets_fresh(monkeypatch):
    from fastapi.testclient import TestClient

    from scripts.api import auth

    seen: list[bool] = []

    async def sync(*, fresh: bool = False):
        seen.append(fresh)
        return server._IBSyncOutcome(
            result=ScriptResult(ok=True, data={}),
            payload={"status": "ok"},
        )

    monkeypatch.setattr(server, "test_mode", False)
    monkeypatch.setattr(server, "_coordinated_orders_sync", sync)
    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    client = TestClient(server.app)
    fresh = client.post("/orders/refresh", headers={"X-Radon-Orders-Fresh": "1"})
    plain = client.post("/orders/refresh")
    assert fresh.status_code == 200
    assert plain.status_code == 200
    assert seen == [True, False]


@pytest.mark.asyncio
async def test_orders_refresh_fresh_header_reaches_coordinator(monkeypatch):
    seen: list[bool] = []

    async def sync(*, fresh: bool = False):
        seen.append(fresh)
        return server._IBSyncOutcome(
            result=ScriptResult(ok=True, data={}),
            payload={"status": "ok"},
        )

    monkeypatch.setattr(server, "test_mode", False)
    monkeypatch.setattr(server, "_coordinated_orders_sync", sync)
    assert await server.orders_refresh(x_radon_orders_fresh="1") == {"status": "ok"}
    assert await server.orders_refresh() == {"status": "ok"}
    assert seen == [True, False]
