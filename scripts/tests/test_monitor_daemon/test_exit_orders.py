#!/usr/bin/env python3
"""
Tests for Exit Orders handler.

RED/GREEN TDD
"""

import pytest
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from monitor_daemon.handlers.exit_orders import ExitOrdersHandler


def make_mock_client():
    """Create a mock IBClient for exit orders tests."""
    mock_client = MagicMock()
    return mock_client


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class FakeJournalDb:
    def __init__(self, trades):
        self.trades = {f"trade-{trade['id']}": trade for trade in trades}
        self.commits = 0

    def execute(self, sql, args=()):
        if sql.strip().upper().startswith("SELECT"):
            if "WHERE trade_id = ?" in sql:
                trade = self.trades.get(args[0])
                rows = [(args[0], json.dumps(trade))] if trade else []
                return _Cursor(rows)
            return _Cursor([
                (trade_id, json.dumps(trade))
                for trade_id, trade in self.trades.items()
            ])
        if sql.strip().upper().startswith("UPDATE"):
            payload, _written_at, trade_id = args
            self.trades[trade_id] = json.loads(payload)
            return _Cursor([])
        raise AssertionError(f"unexpected SQL: {sql}")

    def commit(self):
        self.commits += 1


class TestExitOrdersInit:
    """Test exit orders handler initialization."""

    def test_has_correct_name(self):
        """Handler has correct name."""
        handler = ExitOrdersHandler()
        assert handler.name == "exit_orders"

    def test_has_5_minute_interval(self):
        """Handler runs every 5 minutes (300 seconds)."""
        handler = ExitOrdersHandler()
        assert handler.interval_seconds == 300

    def test_has_max_gap_threshold(self):
        """Handler has 40% max gap threshold."""
        handler = ExitOrdersHandler()
        assert handler.max_gap_pct == 0.40


class TestExitOrdersLoadPending:
    """Test loading pending orders from trade log."""

    def test_loads_pending_orders_from_journal(self):
        """Handler loads PENDING orders from the Turso journal."""
        db = FakeJournalDb([
            {
                "id": 8,
                "ticker": "GOOG",
                "exit_orders": {
                    "target": {
                        "price": 15.00,
                        "status": "PENDING",
                        "order_id": None
                    }
                },
            }
        ])

        handler = ExitOrdersHandler(db=db)
        pending = handler._load_pending_orders()

        assert len(pending) == 1
        assert pending[0]["ticker"] == "GOOG"
        assert pending[0]["target_price"] == 15.00
        assert pending[0]["journal_trade_id"] == "trade-8"

    def test_skips_already_placed_orders(self):
        """Handler skips orders that are already placed."""
        db = FakeJournalDb([
            {
                "id": 8,
                "ticker": "GOOG",
                "exit_orders": {
                    "target": {
                        "price": 15.00,
                        "status": "PLACED",
                        "order_id": 99
                    }
                },
            }
        ])

        handler = ExitOrdersHandler(db=db)
        pending = handler._load_pending_orders()

        assert len(pending) == 0


class TestExitOrdersGapCheck:
    """Test IB gap validation logic."""

    def test_can_place_within_40_pct(self):
        """Can place order within 40% of current price."""
        handler = ExitOrdersHandler()

        # Current price $10, target $13 = 30% gap
        can_place = handler._can_place_order(current_price=10.00, target_price=13.00)

        assert can_place is True

    def test_cannot_place_beyond_40_pct(self):
        """Cannot place order beyond 40% of current price."""
        handler = ExitOrdersHandler()

        # Current price $6, target $15 = 150% gap
        can_place = handler._can_place_order(current_price=6.00, target_price=15.00)

        assert can_place is False

    def test_edge_case_exactly_40_pct(self):
        """Can place at exactly 40% gap."""
        handler = ExitOrdersHandler()

        # Current price $10, target $14 = 40% gap
        can_place = handler._can_place_order(current_price=10.00, target_price=14.00)

        assert can_place is True


class TestExitOrdersExecute:
    """Test execute method."""

    def test_places_order_when_gap_acceptable(self):
        """Places order when within 40% gap."""
        with patch('monitor_daemon.handlers.exit_orders.IBClient') as mock_cls, \
             patch('monitor_daemon.handlers.exit_orders.Option') as mock_option, \
             patch('monitor_daemon.handlers.exit_orders.LimitOrder') as mock_limit_order:
            mock_client = make_mock_client()
            mock_cls.return_value = mock_client

            # Mock contract qualification
            mock_contract = MagicMock()
            mock_contract.localSymbol = "GOOG  260417C00315000"
            mock_client.qualify_contracts.return_value = [mock_contract]

            # Mock market data showing current price near target
            mock_ticker = MagicMock()
            mock_ticker.bid = 11.90
            mock_ticker.ask = 12.10
            mock_client.get_quote.return_value = mock_ticker

            # Mock order placement
            mock_trade = MagicMock()
            mock_trade.order.orderId = 99
            mock_trade.orderStatus.status = "Submitted"
            mock_client.place_order.return_value = mock_trade

            db = FakeJournalDb([
                {
                    "id": 8,
                    "ticker": "GOOG",
                    "contract": "GOOG  260417C00315000",
                    "structure": "Bull Call Spread",
                    "exit_orders": {
                        "target": {
                            "price": 15.00,
                            "status": "PENDING",
                            "order_id": None,
                            "contracts": 44,
                            "contract_spec": {
                                "symbol": "GOOG",
                                "expiry": "20260417",
                                "strike": 315,
                                "right": "C"
                            }
                        }
                    },
                }
            ])

            handler = ExitOrdersHandler(db=db)
            result = handler.execute()

            assert result["orders_checked"] >= 1

    def test_skips_order_when_gap_too_large(self):
        """Skips order when gap exceeds 40%."""
        with patch('monitor_daemon.handlers.exit_orders.IBClient') as mock_cls, \
             patch('monitor_daemon.handlers.exit_orders.Option') as mock_option:
            mock_client = make_mock_client()
            mock_cls.return_value = mock_client

            # Mock contract qualification
            mock_contract = MagicMock()
            mock_contract.localSymbol = "GOOG  260417C00315000"
            mock_client.qualify_contracts.return_value = [mock_contract]

            # Mock market data showing current price far from target
            mock_ticker = MagicMock()
            mock_ticker.bid = 5.90
            mock_ticker.ask = 6.10
            mock_client.get_quote.return_value = mock_ticker

            db = FakeJournalDb([
                {
                    "id": 8,
                    "ticker": "GOOG",
                    "structure": "Bull Call Spread",
                    "exit_orders": {
                        "target": {
                            "price": 15.00,
                            "status": "PENDING",
                            "order_id": None,
                            "contracts": 44,
                            "contract_spec": {
                                "symbol": "GOOG",
                                "expiry": "20260417",
                                "strike": 315,
                                "right": "C"
                            }
                        }
                    },
                }
            ])

            handler = ExitOrdersHandler(db=db)
            result = handler.execute()

            # Should not place order
            mock_client.place_order.assert_not_called()
            assert result.get("orders_placed", 0) == 0


class TestExitOrdersTradeLogUpdate:
    """Test trade log updates after placing orders."""

    def test_updates_journal_on_placement(self):
        """Updates the Turso journal when order is placed."""
        with patch('monitor_daemon.handlers.exit_orders.IBClient') as mock_cls, \
             patch('monitor_daemon.handlers.exit_orders.Option') as mock_option, \
             patch('monitor_daemon.handlers.exit_orders.LimitOrder') as mock_limit_order:
            mock_client = make_mock_client()
            mock_cls.return_value = mock_client

            mock_contract = MagicMock()
            mock_contract.localSymbol = "GOOG  260417C00315000"
            mock_client.qualify_contracts.return_value = [mock_contract]

            mock_ticker = MagicMock()
            mock_ticker.bid = 11.90
            mock_ticker.ask = 12.10
            mock_client.get_quote.return_value = mock_ticker

            mock_trade = MagicMock()
            mock_trade.order.orderId = 99
            mock_trade.orderStatus.status = "Submitted"
            mock_client.place_order.return_value = mock_trade

            db = FakeJournalDb([
                {
                    "id": 8,
                    "ticker": "GOOG",
                    "structure": "Bull Call Spread",
                    "exit_orders": {
                        "target": {
                            "price": 15.00,
                            "status": "PENDING",
                            "order_id": None,
                            "contracts": 44,
                            "contract_spec": {
                                "symbol": "GOOG",
                                "expiry": "20260417",
                                "strike": 315,
                                "right": "C"
                            }
                        }
                    },
                }
            ])

            handler = ExitOrdersHandler(db=db)
            handler.execute()

            updated = db.trades["trade-8"]
            target_status = updated["exit_orders"]["target"]["status"]
            # When order is within range and placed, status should be PLACED
            assert target_status == "PLACED"
            assert updated["exit_orders"]["target"]["order_id"] == 99
            assert db.commits == 1


def _pending_goog_trade():
    return {
        "id": 8,
        "ticker": "GOOG",
        "structure": "Bull Call Spread",
        "exit_orders": {
            "target": {
                "price": 15.00,
                "status": "PENDING",
                "order_id": None,
                "contracts": 44,
                "contract_spec": {
                    "symbol": "GOOG",
                    "expiry": "20260417",
                    "strike": 315,
                    "right": "C",
                },
            }
        },
    }


def _wire_placeable_ib(mock_cls):
    """Mock IBClient wiring where the GOOG target is placeable (25% gap)."""
    mock_client = make_mock_client()
    mock_cls.return_value = mock_client
    mock_contract = MagicMock()
    mock_contract.localSymbol = "GOOG  260417C00315000"
    mock_client.qualify_contracts.return_value = [mock_contract]
    mock_ticker = MagicMock()
    mock_ticker.bid = 11.90
    mock_ticker.ask = 12.10
    mock_client.get_quote.return_value = mock_ticker
    mock_trade = MagicMock()
    mock_trade.order.orderId = 99
    mock_trade.orderStatus.status = "Submitted"
    mock_client.place_order.return_value = mock_trade
    return mock_client


class _NoTradeIdJournalDb(FakeJournalDb):
    """Journal rows whose ``trade_id`` column is NULL (legacy rows written
    before the column was backfilled) — every leg loads with
    ``journal_trade_id`` None."""

    def execute(self, sql, args=()):
        if sql.strip().upper().startswith("SELECT"):
            return _Cursor([(None, json.dumps(t)) for t in self.trades.values()])
        return _Cursor([])


class FlakyUpdateJournalDb(FakeJournalDb):
    """UPDATEs raise for the first `fail_updates` attempts (Turso outage),
    then succeed — SELECTs keep working throughout, like a real Hrana
    write-path failure."""

    def __init__(self, trades, fail_updates=10**9):
        super().__init__(trades)
        self.fail_updates = fail_updates
        self.update_attempts = 0

    def execute(self, sql, args=()):
        if sql.strip().upper().startswith("UPDATE"):
            self.update_attempts += 1
            if self.update_attempts <= self.fail_updates:
                raise RuntimeError("hrana: stream not found")
        return super().execute(sql, args)


class TestExitOrdersJournalFailureGuard:
    """T-010: a failed post-placement journal write must NOT re-place the
    same live order on the next cycle — the row still says PENDING, but the
    order is already resting at IB."""

    def test_journal_update_failure_does_not_replace_on_next_cycle(self):
        with patch('monitor_daemon.handlers.exit_orders.IBClient') as mock_cls, \
             patch('monitor_daemon.handlers.exit_orders.Option'), \
             patch('monitor_daemon.handlers.exit_orders.LimitOrder'):
            mock_client = _wire_placeable_ib(mock_cls)
            db = FlakyUpdateJournalDb([_pending_goog_trade()])

            handler = ExitOrdersHandler(db=db)
            handler.execute()  # places, journal UPDATE fails, row stays PENDING
            handler.execute()  # same row loads again — must NOT re-place

            assert mock_client.place_order.call_count == 1

    def test_journal_update_failure_surfaces_as_cycle_error(self):
        with patch('monitor_daemon.handlers.exit_orders.IBClient') as mock_cls, \
             patch('monitor_daemon.handlers.exit_orders.Option'), \
             patch('monitor_daemon.handlers.exit_orders.LimitOrder'):
            _wire_placeable_ib(mock_cls)
            db = FlakyUpdateJournalDb([_pending_goog_trade()])

            handler = ExitOrdersHandler(db=db)
            result = handler.execute()

            # BaseHandler records state=error off result["error"] — a swallowed
            # journal failure after a LIVE placement must page, not pass silently.
            assert result.get("error"), "journal-update failure must surface in the cycle result"

    def test_journal_heals_on_later_cycle_without_replacing(self):
        with patch('monitor_daemon.handlers.exit_orders.IBClient') as mock_cls, \
             patch('monitor_daemon.handlers.exit_orders.Option'), \
             patch('monitor_daemon.handlers.exit_orders.LimitOrder'):
            mock_client = _wire_placeable_ib(mock_cls)
            db = FlakyUpdateJournalDb([_pending_goog_trade()], fail_updates=1)

            handler = ExitOrdersHandler(db=db)
            handler.execute()  # placed; first UPDATE fails
            handler.execute()  # heal: journal marked PLACED with the REAL order id

            assert mock_client.place_order.call_count == 1
            updated = db.trades["trade-8"]["exit_orders"]["target"]
            assert updated["status"] == "PLACED"
            assert updated["order_id"] == 99


class TestExitOrdersQuoteStateGates:
    """T-044: which quote states may price an exit.

    ``mid = (bid + ask) / 2 if bid and ask else 0`` needs BOTH sides, so a
    one-sided or empty book yields mid 0 and the order is skipped. A HALT
    is different: IB keeps serving the last two-sided book, so the mid
    still looks priceable and the exit would go out against a pre-halt
    price. ``_is_halted`` gates that.
    """

    @staticmethod
    def _run(*, bid, ask, halted="unset"):
        """Execute one cycle against a placeable GOOG target (25% gap at a
        11.90/12.10 book) with the given quote state."""
        with patch('monitor_daemon.handlers.exit_orders.IBClient') as mock_cls, \
             patch('monitor_daemon.handlers.exit_orders.Option'), \
             patch('monitor_daemon.handlers.exit_orders.LimitOrder'):
            mock_client = _wire_placeable_ib(mock_cls)
            ticker = mock_client.get_quote.return_value
            ticker.bid = bid
            ticker.ask = ask
            if halted == "absent":
                del ticker.halted
            elif halted != "unset":
                ticker.halted = halted

            db = FakeJournalDb([_pending_goog_trade()])
            handler = ExitOrdersHandler(db=db)
            result = handler.execute()
            return mock_client, db, result

    @staticmethod
    def _reasons(result):
        return [entry.get("reason") for entry in result["skipped"]]

    def test_empty_book_skips_as_no_market_data(self):
        """Both sides zero — nothing to price against."""
        client, db, result = self._run(bid=0, ask=0)

        client.place_order.assert_not_called()
        assert result["orders_placed"] == 0
        assert result["orders_skipped"] == 1
        assert self._reasons(result) == ["no_market_data"]
        assert db.trades["trade-8"]["exit_orders"]["target"]["status"] == "PENDING"

    def test_nan_quotes_skip_as_no_market_data(self):
        """ib_insync leaves bid/ask at nan until a tick arrives."""
        client, _db, result = self._run(bid=float("nan"), ask=float("nan"))

        client.place_order.assert_not_called()
        assert self._reasons(result) == ["no_market_data"]

    def test_bid_only_book_cannot_price_a_mid(self):
        """A one-sided book must not be averaged against a zero: (11.90 +
        0) / 2 would place at half the real bid."""
        client, _db, result = self._run(bid=11.90, ask=0)

        client.place_order.assert_not_called()
        assert result["orders_placed"] == 0
        assert self._reasons(result) == ["no_market_data"]

    def test_ask_only_book_cannot_price_a_mid(self):
        client, _db, result = self._run(bid=0, ask=12.10)

        client.place_order.assert_not_called()
        assert result["orders_placed"] == 0
        assert self._reasons(result) == ["no_market_data"]

    def test_negative_bid_is_treated_as_absent(self):
        """IB sends -1 for "no data available" on illiquid options."""
        client, _db, result = self._run(bid=-1, ask=12.10)

        client.place_order.assert_not_called()
        assert self._reasons(result) == ["no_market_data"]

    def test_halted_contract_is_not_priced(self):
        """Ticker.halted == 1 (general halt) with a live-looking book."""
        client, db, result = self._run(bid=11.90, ask=12.10, halted=1.0)

        client.place_order.assert_not_called()
        assert result["orders_placed"] == 0
        assert self._reasons(result) == ["halted"]
        assert db.trades["trade-8"]["exit_orders"]["target"]["status"] == "PENDING", (
            "a halted skip must leave the journal row PENDING for a later cycle"
        )

    def test_volatility_halt_is_not_priced(self):
        """Ticker.halted == 2 (volatility halt)."""
        client, _db, result = self._run(bid=11.90, ask=12.10, halted=2)

        client.place_order.assert_not_called()
        assert self._reasons(result) == ["halted"]

    def test_unhalted_flag_still_places(self):
        """Control: halted == 0 is trading normally."""
        client, db, result = self._run(bid=11.90, ask=12.10, halted=0.0)

        client.place_order.assert_called_once()
        assert result["orders_placed"] == 1
        assert db.trades["trade-8"]["exit_orders"]["target"]["status"] == "PLACED"

    def test_unknown_halt_flag_still_places(self):
        """Control: -1 / nan mean "no halt information", not "halted"."""
        for unknown in (-1.0, float("nan")):
            client, _db, result = self._run(bid=11.90, ask=12.10, halted=unknown)
            assert result["orders_placed"] == 1, f"halted={unknown!r} must not block"
            client.place_order.assert_called_once()

    def test_missing_halt_attribute_still_places(self):
        """Control: a ticker with no ``halted`` field at all (older
        ib_insync, synthetic quote) must not be read as halted."""
        client, _db, result = self._run(bid=11.90, ask=12.10, halted="absent")

        client.place_order.assert_called_once()
        assert result["orders_placed"] == 1

if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestRel185PerLegErrorsAndRefusalLatch:
    """REL-185 (R-518): a limit refusal is not overwritten by a later leg's
    error, and a refused leg is not re-refused every cycle."""

    def test_note_error_accumulates(self):
        result = {}
        ExitOrdersHandler._note_error(result, "limit refused leg 2")
        ExitOrdersHandler._note_error(result, "leg 3 not acknowledged")
        assert result["errors"] == ["limit refused leg 2", "leg 3 not acknowledged"]
        assert "limit refused leg 2" in result["error"]

    @staticmethod
    def _drive_refused_cycles(cycles=2):
        """Drive ``run()`` N times against a placeable GOOG target whose
        order-limit check always refuses. Returns (results, health_states)."""
        violation = {"message": "quantity 44 exceeds the per-order cap"}
        health = []

        with patch('monitor_daemon.handlers.exit_orders.IBClient') as mock_cls, \
             patch('monitor_daemon.handlers.exit_orders.Option'), \
             patch('monitor_daemon.handlers.exit_orders.LimitOrder'), \
             patch('order_limits.check_order_limits', return_value=violation):
            client = _wire_placeable_ib(mock_cls)
            db = FakeJournalDb([_pending_goog_trade()])
            handler = ExitOrdersHandler(db=db)
            handler.service_name = "exit-orders"
            handler.record_cycle_health = (
                lambda state, **kw: health.append((state, kw.get("error")))
            )

            results = [handler.run()["data"] for _ in range(cycles)]
            return client, results, health

    def test_a_refused_leg_is_latched_until_params_change(self):
        """The latch must suppress the re-place: place_order is never
        reached on either cycle, and the second cycle records the skip."""
        client, results, _health = self._drive_refused_cycles()

        client.place_order.assert_not_called()
        assert results[0]["orders_failed"] == 1
        assert results[1]["orders_skipped"] == 1
        assert [e.get("reason") for e in results[1]["skipped"]] == [
            "limit_refused_previously"
        ]

    def test_latched_refusal_still_heartbeats_error(self):
        """T-410: the position is STILL unprotected on cycle two, so the
        skip path must surface an error and heartbeat state=error."""
        _client, results, health = self._drive_refused_cycles()

        assert results[0].get("error"), "first refusal must surface an error"
        assert results[1].get("error"), (
            "a latched refusal leaves the position unprotected; the cycle "
            "must not report a clean result"
        )
        assert [state for state, _err in health] == ["error", "error"]

    def test_two_legs_without_a_journal_id_do_not_share_the_latch(self):
        """T-412: keying the latch on journal_trade_id collapses every
        id-less leg onto (None, "target"), so refusing the first silently
        suppresses an unrelated position's exit order."""
        goog = _pending_goog_trade()
        msft = _pending_goog_trade()
        msft["id"] = 9
        msft["ticker"] = "MSFT"
        msft["exit_orders"]["target"]["contract_spec"] = {
            "symbol": "MSFT", "expiry": "20260417", "strike": 315, "right": "C",
        }

        def _limits(order):
            if order["symbol"] == "GOOG":
                return {"message": "quantity 44 exceeds the per-order cap"}
            return None

        with patch('monitor_daemon.handlers.exit_orders.IBClient') as mock_cls, \
             patch('monitor_daemon.handlers.exit_orders.Option'), \
             patch('monitor_daemon.handlers.exit_orders.LimitOrder'), \
             patch('order_limits.check_order_limits', side_effect=_limits):
            client = _wire_placeable_ib(mock_cls)
            qualified = []
            for sym in ("GOOG", "MSFT"):
                c = MagicMock()
                c.localSymbol = f"{sym}  260417C00315000"
                c.symbol = sym
                qualified.append([c])
            # Option is patched, so qualification order (journal order) is
            # what distinguishes the two legs.
            client.qualify_contracts.side_effect = qualified

            db = _NoTradeIdJournalDb([goog, msft])
            handler = ExitOrdersHandler(db=db)
            result = handler.execute()

        assert result["orders_checked"] == 2
        assert client.place_order.call_count == 1, (
            "the MSFT leg passes the limit check and must still be placed"
        )
        assert client.place_order.call_args[0][0].localSymbol.startswith("MSFT")
        assert result["orders_placed"] == 1
