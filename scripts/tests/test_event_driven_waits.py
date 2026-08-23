"""REL-043 / R-089 (P1), R-110, R-111, R-118 (P2) — the event-driven waits
introduced by 1979a0d5 do not wait for the thing they are named for.

R-089 is the money one. `ticker_has_quote` is satisfied by `close` alone.
IB delivers the CLOSE tick (type 9) in the first burst after `reqMktData`,
hundreds of ms before a two-sided book on anything illiquid, and it is the
only prompt tick under `reqMarketDataType(4)`. `wait_until` returns on the
first satisfied poll, so the sync proceeds with bid/ask still NaN,
`_resolve_market_price` falls through to its LAST branch and stamps
`marketPrice` = prior session close during a live session. Nothing in the
web app reads `marketPriceIsCalculated`, so the stale mark renders exactly
like a live one.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clients.ib_client import (
    IBClient,
    UNSET_DOUBLE,
    pnl_daily_is_ready,
    pnl_is_ready,
    ticker_has_quote,
)

NAN = float("nan")


class _Ticker:
    def __init__(self, **kw):
        self.bid = kw.get("bid", NAN)
        self.ask = kw.get("ask", NAN)
        self.last = kw.get("last", NAN)
        self.close = kw.get("close", NAN)

    def marketPrice(self):  # noqa: N802 — ib_insync signature
        if self.bid == self.bid and self.ask == self.ask:
            return (self.bid + self.ask) / 2
        if self.last == self.last:
            return self.last
        return self.close


class TestQuotePredicate:
    def test_close_alone_is_not_a_quote(self):
        assert ticker_has_quote(_Ticker(close=100.0)) is False

    @pytest.mark.parametrize(
        "kw", [{"bid": 104.9, "ask": 105.1}, {"last": 105.0}, {"last": 105.0, "close": 100.0}]
    )
    def test_a_two_sided_book_or_a_last_is_a_quote(self, kw):
        assert ticker_has_quote(_Ticker(**kw)) is True

    def test_a_one_sided_book_is_not_a_quote(self):
        assert ticker_has_quote(_Ticker(bid=104.9)) is False
        assert ticker_has_quote(_Ticker(ask=105.1)) is False


class _FakeIB:
    """Fake-clock ib.sleep so waits are instant and deterministic."""

    def __init__(self, ticker: _Ticker, arrivals: dict[float, dict]):
        self.ticker = ticker
        self.arrivals = arrivals
        self.now = 0.0
        self.connected = True
        self.slept = 0

    def sleep(self, step):
        self.slept += 1
        self.now += step
        for at, values in sorted(self.arrivals.items()):
            if self.now >= at:
                for key, value in values.items():
                    setattr(self.ticker, key, value)

    def isConnected(self):  # noqa: N802
        return self.connected


def _client(fake) -> IBClient:
    client = IBClient()
    client._ib = fake
    return client


class TestQuoteWaitOutlastsTheCloseTick:
    def test_wait_holds_for_the_book_and_does_not_settle_on_close(self):
        """R-089's injection: close at t=0, bid/ask at t=1.2s."""
        ticker = _Ticker(close=100.0)
        fake = _FakeIB(ticker, {1.2: {"bid": 104.9, "ask": 105.1}})
        assert _client(fake).wait_until(lambda: ticker_has_quote(ticker), timeout=3.0)
        assert fake.now >= 1.2
        assert ticker.bid == 104.9

    def test_a_close_only_ticker_burns_the_full_cap_and_reports_false(self):
        ticker = _Ticker(close=100.0)
        fake = _FakeIB(ticker, {})
        assert _client(fake).wait_until(lambda: ticker_has_quote(ticker), timeout=3.0) is False
        assert fake.now == pytest.approx(3.0)


class TestWaitAbortsOnDisconnect:
    def test_socket_drop_mid_wait_does_not_burn_the_cap(self):
        """R-118: the loop never re-checked is_connected(), so a dropped
        socket burned the full cap silently."""
        ticker = _Ticker()
        fake = _FakeIB(ticker, {})

        original = fake.sleep

        def _drop(step):
            original(step)
            if fake.now >= 0.2:
                fake.connected = False

        fake.sleep = _drop
        assert _client(fake).wait_until(lambda: ticker_has_quote(ticker), timeout=10.0) is False
        assert fake.now < 1.0, "wait kept polling a dead socket"


class TestDailyPnlPredicate:
    def test_unrealized_alone_does_not_satisfy_the_daily_wait(self):
        """R-111: Phase 6 consumes dailyPnL only and writes None when unset,
        so waiting on `daily OR unrealized` blanks TODAY'S P&L."""

        class _Pnl:
            dailyPnL = UNSET_DOUBLE
            unrealizedPnL = 1234.5

        assert pnl_is_ready(_Pnl()) is True  # the general predicate is unchanged
        assert pnl_daily_is_ready(_Pnl()) is False

    def test_daily_arriving_late_is_waited_for(self):
        class _Pnl:
            dailyPnL = UNSET_DOUBLE
            unrealizedPnL = 1234.5

        pnl = _Pnl()
        ticker = _Ticker()
        fake = _FakeIB(ticker, {})
        original = fake.sleep

        def _late(step):
            original(step)
            if fake.now >= 1.2:
                pnl.dailyPnL = -910.25

        fake.sleep = _late
        assert _client(fake).wait_until(lambda: pnl_daily_is_ready(pnl), timeout=3.0)
        assert pnl.dailyPnL == -910.25


class TestPositionsReadyIsNotVacuous:
    def test_an_empty_positions_cache_is_not_ready(self):
        """R-118 sharp edge: connectionClosed() empties the cache and
        `all([])` is vacuously True, so a disconnect reported SUCCESS."""

        class _IB:
            def __init__(self):
                self.ended = True

            def positions(self):
                return []

        from clients.ib_client import positions_snapshot_is_ready

        assert positions_snapshot_is_ready(True, []) is False
        assert positions_snapshot_is_ready(False, [object()]) is False


class TestGlobalCancelDrains:
    def test_global_cancel_waits_at_least_one_step(self):
        """R-110: on a freshly-connected master with no openOrder pushes
        yet, openTrades() is empty, the predicate is immediately true, and
        the kill switch returned with reqGlobalCancel still in the
        transport buffer."""
        calls = {"cancel": 0}

        class _IB:
            def __init__(self):
                self.slept = 0
                self.connected = True

            def reqGlobalCancel(self):  # noqa: N802
                calls["cancel"] += 1

            def openTrades(self):  # noqa: N802
                return []

            def sleep(self, _step):
                self.slept += 1

            def isConnected(self):  # noqa: N802
                return self.connected

        fake = _IB()
        client = _client(fake)
        client.global_cancel()

        assert calls["cancel"] == 1
        assert fake.slept >= 1, "kill switch returned without draining a single step"
