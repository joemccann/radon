"""Event-driven IB waits: return on completion, cap at the old sleep floor.

Replaces fixed ib.sleep(0.5–3) floors. positionEnd + valid avgCost, first
quote/PnL tick, openOrderEnd, and bounded historical requests.
"""

from __future__ import annotations

import asyncio
import math
import time
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from clients.ib_client import (
    IBClient,
    IBTimeoutError,
    is_valid_ib_number,
    ticker_has_quote,
    pnl_is_ready,
)


UNSET = 1.7976931348623157e+308


class FakeEvent:
    def __init__(self):
        self._handlers = []

    def __iadd__(self, handler):
        self._handlers.append(handler)
        return self

    def __isub__(self, handler):
        if handler in self._handlers:
            self._handlers.remove(handler)
        return self

    def emit(self):
        for handler in list(self._handlers):
            handler()


def _connected_client(mock_ib):
    mock_ib.isConnected.return_value = True
    client = IBClient()
    client.connect(client_id=1)
    return client


def test_is_valid_ib_number_rejects_nan_none_and_unset():
    assert is_valid_ib_number(0.0) is True
    assert is_valid_ib_number(-12.5) is True
    assert is_valid_ib_number(None) is False
    assert is_valid_ib_number(float("nan")) is False
    assert is_valid_ib_number(UNSET) is False
    assert is_valid_ib_number("x") is False


def test_ticker_has_quote_accepts_last_or_bid_ask_or_close():
    empty = MagicMock()
    empty.bid = UNSET
    empty.ask = UNSET
    empty.last = UNSET
    empty.close = UNSET
    empty.marketPrice.return_value = math.nan
    assert ticker_has_quote(empty) is False

    last_only = MagicMock()
    last_only.bid = UNSET
    last_only.ask = UNSET
    last_only.last = 150.5
    last_only.close = UNSET
    last_only.marketPrice.return_value = math.nan
    assert ticker_has_quote(last_only) is True


def test_pnl_is_ready_on_daily_or_unrealized():
    blank = MagicMock()
    blank.dailyPnL = math.nan
    blank.unrealizedPnL = UNSET
    assert pnl_is_ready(blank) is False

    daily = MagicMock()
    daily.dailyPnL = 12.0
    daily.unrealizedPnL = math.nan
    assert pnl_is_ready(daily) is True


@patch("clients.ib_client.IB")
def test_wait_until_returns_early_and_does_not_floor(MockIB):
    mock_ib = MockIB.return_value
    client = _connected_client(mock_ib)
    ready = {"ok": False}

    def sleep(seconds):
        ready["ok"] = True
        return None

    mock_ib.sleep.side_effect = sleep
    assert client.wait_until(lambda: ready["ok"], timeout=2.0, poll=0.05) is True
    assert mock_ib.sleep.call_count == 1
    mock_ib.sleep.assert_called_with(0.05)


@patch("clients.ib_client.IB")
def test_wait_until_caps_at_timeout_using_sleep_steps(MockIB):
    mock_ib = MockIB.return_value
    client = _connected_client(mock_ib)
    assert client.wait_until(lambda: False, timeout=0.2, poll=0.05) is False
    assert 1 <= mock_ib.sleep.call_count <= math.ceil(0.2 / 0.05)


@patch("clients.ib_client.IB")
def test_wait_until_is_bounded_by_wall_clock_when_sleep_overruns(MockIB):
    """ib.sleep runs the event loop for AT LEAST ``secs``; a blocked handler
    can stretch each 0.05s step to 0.2s. The cap must be wall-clock, not
    ``ceil(timeout/poll)`` nominal steps (which would be 4 x 0.2s = 0.8s).

    TEST_AUDIT T-182: ``sleep.call_count`` is the assertion that CARRIES that
    contract, not the wall-clock bound. A deadline-capped loop takes ONE step
    here (the first over-running step consumes the whole 0.2s timeout); the
    nominal-step bug takes four. Measured 2026-08-28 against a nominal-step
    build on a runner whose stubbed sleep is exact: elapsed 0.831s, call_count
    4 — ``call_count <= 2`` reds, and any wall-clock ceiling loose enough not to
    flake on the real floor does not. The old ``elapsed < 0.5`` was that squeeze
    from the other side: ``time.sleep(0.2)`` returns in ~0.34s on this macOS
    gate (72% overshoot), so the floor already sat at 67% of the ceiling with
    the box idle. ``elapsed`` is kept only as a coarse hang guard.
    """
    mock_ib = MockIB.return_value
    client = _connected_client(mock_ib)
    mock_ib.sleep.side_effect = lambda seconds: time.sleep(0.2)

    started = time.monotonic()
    assert client.wait_until(lambda: False, timeout=0.2, poll=0.05) is False
    elapsed = time.monotonic() - started

    assert mock_ib.sleep.call_count <= 2, (
        f"wait_until took {mock_ib.sleep.call_count} sleep steps for a 0.2s "
        "timeout whose first step already burned 0.2s: the loop is counting "
        "ceil(timeout/poll) nominal steps instead of honouring the deadline"
    )
    assert elapsed < 1.0, f"wait_until hung: {elapsed:.2f}s"


@patch("clients.ib_client.IB")
def test_get_positions_returns_on_position_end_without_1s_floor(MockIB):
    mock_ib = MockIB.return_value
    ended = FakeEvent()
    mock_ib.positionEndEvent = ended

    pos = MagicMock()
    pos.avgCost = 23.81
    pos.position = 75
    mock_ib.positions.return_value = [pos]

    def req():
        ended.emit()

    mock_ib.reqPositions.side_effect = req
    client = _connected_client(mock_ib)
    result = client.get_positions()

    mock_ib.reqPositions.assert_called_once()
    assert result == [pos]
    mock_ib.sleep.assert_not_called()


@patch("clients.ib_client.IB")
def test_get_positions_keeps_waiting_if_avg_cost_unset_after_end(MockIB):
    mock_ib = MockIB.return_value
    ended = FakeEvent()
    mock_ib.positionEndEvent = ended
    pos = MagicMock()
    pos.avgCost = UNSET
    pos.position = 75
    mock_ib.positions.return_value = [pos]

    def req():
        ended.emit()

    mock_ib.reqPositions.side_effect = req
    client = _connected_client(mock_ib)
    client.get_positions()

    assert mock_ib.sleep.call_count >= 1


@patch("clients.ib_client.IB")
def test_get_quote_snapshot_returns_when_ticker_ready(MockIB):
    mock_ib = MockIB.return_value
    ticker = MagicMock()
    ticker.bid = 150.0
    ticker.ask = 151.0
    ticker.last = 150.5
    ticker.close = 149.5
    ticker.marketPrice.return_value = 150.5
    mock_ib.reqMktData.return_value = ticker

    client = _connected_client(mock_ib)
    quote = client.get_quote(MagicMock(), snapshot=True)

    assert quote is ticker
    mock_ib.sleep.assert_not_called()


@patch("clients.ib_client.IB")
def test_get_pnl_returns_on_first_valid_tick(MockIB):
    mock_ib = MockIB.return_value
    pnl = MagicMock()
    pnl.dailyPnL = 9.0
    pnl.unrealizedPnL = 1.0
    mock_ib.reqPnL.return_value = pnl

    client = _connected_client(mock_ib)
    assert client.get_pnl("DU1") is pnl
    mock_ib.sleep.assert_not_called()


@patch("clients.ib_client.IB")
def test_get_open_orders_returns_on_open_order_end(MockIB):
    mock_ib = MockIB.return_value
    ended = FakeEvent()
    mock_ib.openOrderEndEvent = ended
    trades = [MagicMock()]
    mock_ib.openTrades.return_value = trades

    def req():
        ended.emit()

    mock_ib.reqAllOpenOrders.side_effect = req
    client = _connected_client(mock_ib)
    assert client.get_open_orders() == trades
    mock_ib.sleep.assert_not_called()


@patch("clients.ib_client.IB")
def test_get_historical_data_uses_bounded_async(MockIB):
    mock_ib = MockIB.return_value
    bar = MagicMock()
    bar.date = datetime(2026, 3, 3)
    bar.close = 151.0

    async def _hist(*_a, **_k):
        return [bar]

    mock_ib.reqHistoricalDataAsync = _hist

    def _run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    mock_ib.run.side_effect = _run
    client = _connected_client(mock_ib)
    bars = client.get_historical_data(MagicMock(), duration="1 D", bar_size="1 hour")

    assert bars == [bar]
    mock_ib.reqHistoricalData.assert_not_called()


def test_wait_for_streaming_data_returns_when_all_ready():
    from ib_sync import wait_for_streaming_data

    ticker = MagicMock()
    ticker.bid = 10.0
    ticker.ask = 10.1
    ticker.last = 10.05
    ticker.close = 10.0
    ticker.marketPrice.return_value = 10.05
    pnl = MagicMock()
    pnl.dailyPnL = 1.0
    pnl.unrealizedPnL = 0.5
    single = MagicMock()
    single.dailyPnL = 0.25

    client = MagicMock()
    client.wait_until.side_effect = lambda pred, timeout: pred()

    assert wait_for_streaming_data(
        client,
        pnl_obj=pnl,
        tickers=[ticker],
        pnl_requests=[({}, single, 1)],
        timeout=2.5,
    ) is True
    client.wait_until.assert_called_once()


def _ready_ticker():
    ticker = MagicMock()
    ticker.bid = 10.0
    ticker.ask = 10.1
    ticker.last = 10.05
    ticker.close = 10.0
    return ticker


def _quoteless_ticker():
    ticker = MagicMock()
    ticker.bid = float("nan")
    ticker.ask = float("nan")
    ticker.last = float("nan")
    ticker.close = float("nan")
    ticker.marketPrice.return_value = float("nan")
    return ticker


def _account_pnl(daily, unrealized):
    pnl = MagicMock()
    pnl.dailyPnL = daily
    pnl.unrealizedPnL = unrealized
    return pnl


def _single_pnl(daily):
    single = MagicMock()
    single.dailyPnL = daily
    return single


def _streaming_ready(pnl_obj, tickers, pnl_requests):
    from ib_sync import wait_for_streaming_data

    client = MagicMock()
    client.wait_until.side_effect = lambda pred, timeout: pred()
    return wait_for_streaming_data(
        client,
        pnl_obj=pnl_obj,
        tickers=tickers,
        pnl_requests=pnl_requests,
        timeout=2.5,
    )


def test_wait_for_streaming_data_not_ready_when_ticker_has_no_quote():
    assert _streaming_ready(
        _account_pnl(1.0, 0.5),
        [_ready_ticker(), _quoteless_ticker()],
        [({}, _single_pnl(0.25), 1)],
    ) is False


def test_wait_for_streaming_data_not_ready_when_account_daily_pnl_missing():
    """Phase 6 reads dailyPnL with no fallback wait; an unrealizedPnL tick
    arriving first must not release the wait."""
    assert _streaming_ready(
        _account_pnl(float("nan"), 1.0),
        [_ready_ticker()],
        [({}, _single_pnl(0.25), 1)],
    ) is False
    assert _streaming_ready(
        _account_pnl(None, 1.0),
        [_ready_ticker()],
        [({}, _single_pnl(0.25), 1)],
    ) is False


def test_wait_for_streaming_data_not_ready_when_single_pnl_daily_missing():
    assert _streaming_ready(
        _account_pnl(1.0, 0.5),
        [_ready_ticker()],
        [({}, _single_pnl(0.25), 1), ({}, _single_pnl(float("nan")), 2)],
    ) is False


@patch("clients.ib_client.IB")
def test_get_historical_data_times_out(MockIB):
    """R-117: ib_insync owns the deadline now, so it is the one that expires.

    Its contract is to send cancelHistoricalData(reqId) and return an EMPTY
    container without raising — the previous stub hung past its own `timeout`
    and returned None, which no real gateway does. The assertion is unchanged:
    a historical request that does not deliver raises IBTimeoutError.
    """
    mock_ib = MockIB.return_value

    async def _expire_like_ib_insync(*_a, **kwargs):
        await asyncio.sleep(kwargs["timeout"])
        return []

    mock_ib.reqHistoricalDataAsync = _expire_like_ib_insync
    mock_ib.run.side_effect = lambda coro: asyncio.new_event_loop().run_until_complete(coro)
    client = _connected_client(mock_ib)

    with pytest.raises(IBTimeoutError, match="timed out"):
        client.get_historical_data(MagicMock(), timeout=0.05)


@patch("clients.ib_client.IB")
def test_get_historical_data_bounds_a_wedged_cancel(MockIB):
    """The outer guard is the backstop for ib_insync's own cancel wedging."""
    mock_ib = MockIB.return_value

    async def _hang(*_a, **_k):
        await asyncio.sleep(30)

    mock_ib.reqHistoricalDataAsync = _hang
    mock_ib.run.side_effect = lambda coro: asyncio.new_event_loop().run_until_complete(coro)
    client = _connected_client(mock_ib)

    with patch("clients.ib_client.HISTORICAL_CANCEL_GRACE_SECS", 0.05):
        with pytest.raises(IBTimeoutError, match="cancel did not return"):
            client.get_historical_data(MagicMock(), timeout=0.05)
