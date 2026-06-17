"""Tests for the autonomous orders-sync background loop (CTA-02 / push-flood fix).

The loop must:
- Call _orders_sync_tick (and thus the subprocess sync) on a market-hours tick.
- Skip off-hours ticks entirely.
- Skip ticks when test_mode is set.
- Skip ticks when the pool has no live connections.
- Never raise — exceptions must be swallowed so a single failure cannot
  kill the task.
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api import server as srv
from api.subprocess import ScriptResult


# ---------------------------------------------------------------------------
# _orders_sync_tick unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orders_sync_tick_skips_when_test_mode():
    """tick() is a no-op when test_mode is True — never spawns a subprocess."""
    with patch.object(srv, "test_mode", True):
        with patch.object(srv, "_run_ib_script_with_recovery", new=AsyncMock()) as mock_run:
            await srv._orders_sync_tick()
            mock_run.assert_not_called()


@pytest.mark.asyncio
async def test_orders_sync_tick_skips_when_market_closed():
    """tick() is a no-op outside 09:30-16:00 ET weekdays."""
    with patch.object(srv, "test_mode", False):
        with patch.object(srv, "_is_market_open_now_et", return_value=False):
            with patch.object(srv, "_run_ib_script_with_recovery", new=AsyncMock()) as mock_run:
                await srv._orders_sync_tick()
                mock_run.assert_not_called()


@pytest.mark.asyncio
async def test_orders_sync_tick_skips_when_pool_disconnected():
    """tick() is a no-op when no pool connection is live (gateway unreachable /
    awaiting 2FA)."""
    with patch.object(srv, "test_mode", False):
        with patch.object(srv, "_is_market_open_now_et", return_value=True):
            with patch.object(srv, "_pool_has_any_connection", return_value=False):
                with patch.object(srv, "_run_ib_script_with_recovery", new=AsyncMock()) as mock_run:
                    await srv._orders_sync_tick()
                    mock_run.assert_not_called()


@pytest.mark.asyncio
async def test_orders_sync_tick_runs_sync_during_market_hours():
    """tick() calls ib_orders.py --sync via the recovery-aware helper when all
    guards pass."""
    ok_result = ScriptResult(ok=True, data={})

    with patch.object(srv, "test_mode", False):
        with patch.object(srv, "_is_market_open_now_et", return_value=True):
            with patch.object(srv, "_pool_has_any_connection", return_value=True):
                with patch.object(
                    srv,
                    "_run_ib_script_with_recovery",
                    new=AsyncMock(return_value=ok_result),
                ) as mock_run:
                    await srv._orders_sync_tick()
                    mock_run.assert_called_once()
                    call_args = mock_run.call_args
                    # First positional arg: script name
                    assert call_args[0][0] == "ib_orders.py"
                    # Args list contains --sync
                    assert "--sync" in call_args[0][1]
                    # raw=True so service_cycle heartbeat inside the script works
                    assert call_args[1].get("raw") is True


@pytest.mark.asyncio
async def test_orders_sync_tick_logs_warning_on_failure():
    """tick() logs a warning but does NOT raise when the subprocess fails."""
    fail_result = ScriptResult(ok=False, error="ECONNREFUSED")

    with patch.object(srv, "test_mode", False):
        with patch.object(srv, "_is_market_open_now_et", return_value=True):
            with patch.object(srv, "_pool_has_any_connection", return_value=True):
                with patch.object(
                    srv,
                    "_run_ib_script_with_recovery",
                    new=AsyncMock(return_value=fail_result),
                ):
                    # Must not raise
                    await srv._orders_sync_tick()


# ---------------------------------------------------------------------------
# _orders_sync_loop unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orders_sync_loop_calls_tick_after_interval():
    """The loop sleeps first, then fires tick; one iteration is enough to prove
    the cadence wiring."""
    ticks: list[int] = []

    async def fake_tick() -> None:
        ticks.append(1)
        # Cancel the outer loop after the first tick so the test terminates
        raise asyncio.CancelledError

    with patch.object(srv, "_orders_sync_tick", new=fake_tick):
        with pytest.raises(asyncio.CancelledError):
            await srv._orders_sync_loop(interval=0.01)

    assert len(ticks) == 1


@pytest.mark.asyncio
async def test_orders_sync_loop_swallows_exceptions_and_continues():
    """An exception inside tick() must not kill the loop — it should log and
    continue. The loop fires at least two ticks to prove it keeps going."""
    ticks: list[int] = []

    async def flaky_tick() -> None:
        ticks.append(1)
        if len(ticks) == 1:
            raise RuntimeError("transient network error")
        if len(ticks) >= 2:
            raise asyncio.CancelledError

    with patch.object(srv, "_orders_sync_tick", new=flaky_tick):
        with pytest.raises(asyncio.CancelledError):
            await srv._orders_sync_loop(interval=0.01)

    assert len(ticks) == 2


# ---------------------------------------------------------------------------
# Lifespan integration — loop is started for non-test-mode deployments
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orders_sync_loop_task_created_in_lifespan():
    """The lifespan startup creates the orders-sync loop task alongside the
    existing recovery heartbeat and journal reconciliation tasks."""
    os.environ.pop("RADON_API_TEST_MODE", None)

    created_tasks: list[str] = []

    real_create_task = asyncio.create_task

    def spy_create_task(coro, **kwargs):
        created_tasks.append(getattr(coro, "__name__", repr(coro)))
        return real_create_task(coro, **kwargs)

    fake_pool = AsyncMock()
    fake_pool.connect_all = AsyncMock(return_value={})
    fake_pool.disconnect_all = AsyncMock(return_value=None)

    with (
        patch.object(srv, "IBPool", return_value=fake_pool),
        patch.object(srv, "ensure_ib_gateway", new=AsyncMock(return_value={"status": "ok"})),
        patch.object(
            srv, "_warm_journal_reconciliation_on_startup", new=AsyncMock(return_value=None)
        ),
        patch("asyncio.create_task", side_effect=spy_create_task),
    ):
        async with srv.lifespan(srv.app):
            pass

    assert "_orders_sync_loop" in created_tasks, (
        f"Expected '_orders_sync_loop' task; tasks created: {created_tasks}"
    )
