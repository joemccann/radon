"""Lifespan must not block port-binding on IB pool connection.

Regression test for the deploy-rollback failure we hit when IB Gateway
sits in awaiting_2fa: `ib_pool.connect_all()` takes ~80s waiting for
client timeouts, and if `lifespan()` awaits it, uvicorn never binds
port 8321 until the pool gives up. The deploy script's 45s health
window then fires a rollback on a perfectly good commit.

The fix: kick `connect_all()` off as a background task so lifespan
yields immediately. Routes that need the pool already check
`pool.is_connected`, and `/health` reports pool + auth state so
operators can see "pool connecting" without us blocking the listener.
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, patch

import pytest

# Import server up-front so patch() can resolve attributes on it.
from api import server as srv  # noqa: E402


@pytest.mark.asyncio
async def test_lifespan_yields_before_ib_pool_finishes_connecting():
    """If `ib_pool.connect_all()` takes 5s, lifespan must still yield in <500ms.

    Implementation: patch IBPool so connect_all sleeps for 5s. Wrap the
    lifespan startup in asyncio.wait_for(..., timeout=1.0). If lifespan
    awaits connect_all, the 1s timeout fires and the test fails.
    """
    os.environ.pop("RADON_API_TEST_MODE", None)

    async def slow_connect_impl():
        await asyncio.sleep(5)

    slow_connect = AsyncMock(side_effect=slow_connect_impl)
    fake_pool_instance = AsyncMock()
    fake_pool_instance.connect_all = slow_connect
    fake_pool_instance.disconnect_all = AsyncMock(return_value=None)

    with (
        patch.object(srv, "test_mode", False),
        patch.object(srv, "IBPool", return_value=fake_pool_instance),
        patch.object(
            srv,
            "ensure_ib_gateway",
            new=AsyncMock(return_value={"status": "already_running"}),
        ),
        patch.object(
            srv,
            "_warm_journal_reconciliation_on_startup",
            new=AsyncMock(return_value=None),
        ),
    ):
        app, lifespan = srv.app, srv.lifespan

        async def enter_lifespan() -> None:
            async with lifespan(app):
                # Yield control once so the background connect task created
                # during startup gets a turn and reaches its first await
                # (registering the connect_all() call). Without this, the
                # context-manager body completes before the scheduled task
                # ever runs. If lifespan AWAITED connect_all instead of
                # backgrounding it, the 5s sleep would blow the 1s timeout.
                await asyncio.sleep(0)

        await asyncio.wait_for(enter_lifespan(), timeout=1.0)

        slow_connect.assert_called_once()


@pytest.mark.asyncio
async def test_lifespan_exposes_pool_on_app_state_before_connect_completes():
    """`app.state.ib_pool` must be set before yield so routes can find it.

    Background-task connect doesn't help if routes can't reach the pool
    instance until it's fully connected.
    """
    os.environ.pop("RADON_API_TEST_MODE", None)

    pool_ready = asyncio.Event()

    async def slow_connect():
        await pool_ready.wait()
        return {"sync": True}

    fake_pool_instance = AsyncMock()
    fake_pool_instance.connect_all = slow_connect
    fake_pool_instance.disconnect_all = AsyncMock(return_value=None)

    with (
        patch.object(srv, "test_mode", False),
        patch.object(srv, "IBPool", return_value=fake_pool_instance),
        patch.object(
            srv,
            "ensure_ib_gateway",
            new=AsyncMock(return_value={"status": "already_running"}),
        ),
        patch.object(
            srv,
            "_warm_journal_reconciliation_on_startup",
            new=AsyncMock(return_value=None),
        ),
    ):
        app, lifespan = srv.app, srv.lifespan

        async with lifespan(app):
            assert app.state.ib_pool is fake_pool_instance
            pool_ready.set()
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_lifespan_cancels_and_joins_background_pool_connect():
    os.environ.pop("RADON_API_TEST_MODE", None)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def blocked_connect():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    fake_pool_instance = AsyncMock()
    fake_pool_instance.connect_all = blocked_connect
    fake_pool_instance.disconnect_all = AsyncMock(return_value=None)

    with (
        patch.object(srv, "test_mode", False),
        patch.object(srv, "IBPool", return_value=fake_pool_instance),
        patch.object(
            srv,
            "ensure_ib_gateway",
            new=AsyncMock(return_value={"status": "already_running"}),
        ),
        patch.object(
            srv,
            "_warm_journal_reconciliation_on_startup",
            new=AsyncMock(return_value=None),
        ),
    ):
        async with srv.lifespan(srv.app):
            await started.wait()

    assert cancelled.is_set()
    fake_pool_instance.disconnect_all.assert_awaited_once()
