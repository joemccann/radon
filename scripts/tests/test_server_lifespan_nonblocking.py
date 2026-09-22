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
import contextlib
import os
from unittest.mock import AsyncMock, patch

import pytest

# Import server up-front so patch() can resolve attributes on it.
from api import server as srv  # noqa: E402


@contextlib.contextmanager
def patched_lifespan_deps(fake_pool_instance):
    """Patch every lifespan dependency except the IB pool under test.

    `_warm_knowledge_embedder_on_startup` loads the ~67 MB fastembed ONNX
    model via `asyncio.to_thread`. A thread cannot be cancelled, so the
    lifespan teardown's `gather()` blocks until that load returns — which on
    a cold CI runner means a model download over the network. That is what
    made this module time out non-deterministically. None of these tasks are
    the property under test, so stub them out.
    """
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
        patch.object(
            srv,
            "_warm_knowledge_embedder_on_startup",
            new=AsyncMock(return_value=None),
        ),
        patch.object(srv, "_ib_recovery_heartbeat_loop", new=AsyncMock(return_value=None)),
        patch.object(srv, "_orders_sync_loop", new=AsyncMock(return_value=None)),
    ):
        yield



@pytest.mark.asyncio
async def test_lifespan_yields_before_ib_pool_finishes_connecting():
    """Lifespan must reach its yield while `connect_all()` is still pending.

    Synchronised on events, not on the clock: `connect_all()` announces that
    it started and then parks until the lifespan body releases it. Reaching
    the body at all proves lifespan did not await the connect; asserting the
    connect has not completed there proves it is genuinely still in flight.
    If lifespan AWAITED connect_all, the body would never run and the
    backstop timeout (a deadlock guard, not a latency budget) would fire.
    """
    os.environ.pop("RADON_API_TEST_MODE", None)

    connect_started = asyncio.Event()
    release_connect = asyncio.Event()
    connect_finished = asyncio.Event()

    async def slow_connect_impl():
        connect_started.set()
        await release_connect.wait()
        connect_finished.set()

    slow_connect = AsyncMock(side_effect=slow_connect_impl)
    fake_pool_instance = AsyncMock()
    fake_pool_instance.connect_all = slow_connect
    fake_pool_instance.disconnect_all = AsyncMock(return_value=None)

    with patched_lifespan_deps(fake_pool_instance):
        app, lifespan = srv.app, srv.lifespan

        async def enter_lifespan() -> None:
            async with lifespan(app):
                # The background connect task has been created; wait for it to
                # actually enter connect_all() so the assertion below is about
                # an in-flight connect rather than an unscheduled task.
                await connect_started.wait()
                assert not connect_finished.is_set(), (
                    "lifespan yielded only after connect_all() completed"
                )
                release_connect.set()
                await connect_finished.wait()

        # Deadlock backstop only. If lifespan awaits connect_all, connect_all
        # parks on release_connect (set inside the body that never runs) and
        # nothing would ever complete without this.
        await asyncio.wait_for(enter_lifespan(), timeout=30.0)

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

    with patched_lifespan_deps(fake_pool_instance):
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

    with patched_lifespan_deps(fake_pool_instance):
        async with srv.lifespan(srv.app):
            await started.wait()

    assert cancelled.is_set()
    fake_pool_instance.disconnect_all.assert_awaited_once()
