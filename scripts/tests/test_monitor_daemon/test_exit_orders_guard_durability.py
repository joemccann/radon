#!/usr/bin/env python3
"""REL-003 — durable duplicate-placement guard + broker cross-check
(RELIABILITY_AUDIT.md R-004, compounded by R-012).

The T-010 guard (`_unrecorded_placements`) suppressed re-placement after a
journal-write failure — but only in process memory. A daemon restart
(every deploy) with the row still PENDING re-placed the same live GTC
SELL every cycle. And a crash *between* place_order and the journal
UPDATE armed no guard at all.

Fault injections pinned here:
  1. Guard survives restart: get_state()/set_state() round-trip carries
     the unrecorded-placements map; the restarted handler does not
     re-place while the row still reads PENDING.
  2. Crash-between-place-and-update: a fresh handler (no guard) sees the
     live SELL in IB open orders, adopts it (journal → PLACED with the
     live orderId) instead of placing a duplicate.
  3. Adoption heal-write failure arms the guard + error (no placement).
  4. Open-order cross-check unavailable → placements deferred with error
     (fail-safe: no order reaches IB unverified).
  5. Journal update is verified by read-back: a write that is silently
     lost (concurrent wholesale overwrite, R-012) returns False so the
     guard arms instead of trusting the write.
  6. Control: no matching open order, cross-check healthy → placement
     proceeds exactly as before.
"""

import json
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from monitor_daemon.handlers.exit_orders import ExitOrdersHandler
from monitor_daemon.daemon import MonitorDaemon
from test_exit_orders import FakeJournalDb, _Cursor


GOOG_TRADE = {
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


def _both_legs_trade():
    """GOOG with BOTH exit legs PENDING on the SAME contract.

    T-055: the target and the stop are two SELL limits on one conId; only
    the limit price tells them apart at the broker.
    """
    spec = {
        "symbol": "GOOG",
        "expiry": "20260417",
        "strike": 315,
        "right": "C",
    }
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
                "contract_spec": dict(spec),
            },
            "stop": {
                "price": 14.50,
                "status": "PENDING",
                "order_id": None,
                "contracts": 44,
                "contract_spec": dict(spec),
            },
        },
    }


class FailingUpdateDb(FakeJournalDb):
    """Journal reads work; UPDATE raises (Turso blip)."""

    def execute(self, sql, args=()):
        if sql.strip().upper().startswith("UPDATE"):
            raise RuntimeError("hrana 502: upstream forward failed")
        return super().execute(sql, args)


class LostWriteDb(FakeJournalDb):
    """UPDATE 'succeeds' but the write is silently lost (R-012 race)."""

    def execute(self, sql, args=()):
        if sql.strip().upper().startswith("UPDATE"):
            return _Cursor([])  # accepted, never applied
        return super().execute(sql, args)


def _wire_market(mock_client, con_id=606060):
    contract = MagicMock()
    contract.localSymbol = "GOOG  260417C00315000"
    contract.conId = con_id
    mock_client.qualify_contracts.return_value = [contract]
    ticker = MagicMock()
    ticker.bid = 14.90
    ticker.ask = 15.10
    ticker.halted = 0
    mock_client.get_quote.return_value = ticker
    return contract


def _confirmed_trade(order_id=99, perm_id=777001):
    trade = MagicMock()
    trade.order.orderId = order_id
    trade.order.permId = perm_id
    trade.orderStatus.status = "Submitted"
    return trade


def _open_sell_trade(con_id=606060, order_id=99, local_symbol="GOOG  260417C00315000",
                     limit_price=15.00, oca_group="radon-exit-trade-8",
                     quantity=44):
    """A working SELL this handler placed on trade-8 in an earlier cycle.

    REL-040: real placements now carry the row's OCA group and a readable
    size, so the oversell guard can tell an OCA sibling (cannot co-fill)
    from a foreign working order (can). Pass ``oca_group=None`` for the
    latter.
    """
    trade = MagicMock()
    trade.order.action = "SELL"
    trade.order.orderId = order_id
    trade.order.permId = 777001
    trade.order.lmtPrice = limit_price
    trade.order.ocaGroup = oca_group
    trade.order.totalQuantity = quantity
    trade.orderStatus.filled = 0
    trade.orderStatus.status = "Submitted"
    trade.contract.conId = con_id
    trade.contract.localSymbol = local_symbol
    return trade


def _run(db, handler=None, open_orders=(), open_orders_raises=False,
         place_trade=None):
    with patch("monitor_daemon.handlers.exit_orders.IBClient") as mock_cls, \
         patch("monitor_daemon.handlers.exit_orders.Option"), \
         patch("monitor_daemon.handlers.exit_orders.LimitOrder"):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        _wire_market(mock_client)
        if open_orders_raises:
            mock_client.get_open_orders.side_effect = RuntimeError("reqAllOpenOrders timeout")
        else:
            mock_client.get_open_orders.return_value = list(open_orders)
        mock_client.place_order.return_value = place_trade or _confirmed_trade()
        if handler is None:
            handler = ExitOrdersHandler(db=db)
        result = handler.execute()
        return result, mock_client, handler


def _status(db):
    return db.trades["trade-8"]["exit_orders"]["target"]["status"]


def _leg(db, order_type):
    return db.trades["trade-8"]["exit_orders"][order_type]


class TestGuardSurvivesRestart:
    def test_state_roundtrip_blocks_replacement_after_restart(self):
        """Cycle 1: place ok, journal UPDATE fails → guard armed. Restart
        (new handler + set_state(get_state())). Cycle 2 with the DB still
        failing must NOT place again."""
        db1 = FailingUpdateDb([dict(GOOG_TRADE)])
        result1, client1, handler1 = _run(db1)
        assert client1.place_order.call_count == 1
        assert "error" in result1

        state = handler1.get_state()

        # Simulated restart: brand-new handler restores persisted state.
        db2 = FailingUpdateDb([dict(GOOG_TRADE)])
        handler2 = ExitOrdersHandler(db=db2)
        handler2.set_state(state)
        result2, client2, _ = _run(db2, handler=handler2)
        client2.place_order.assert_not_called()

    def test_get_state_serializes_guard_json_safe(self):
        handler = ExitOrdersHandler(db=FakeJournalDb([]))
        handler._unrecorded_placements[("trade-8", "target")] = 99
        state = handler.get_state()
        # Must survive a JSON round-trip (daemon_state.json).
        restored_raw = json.loads(json.dumps(state))
        handler2 = ExitOrdersHandler(db=FakeJournalDb([]))
        handler2.set_state(restored_raw)
        assert handler2._unrecorded_placements == {("trade-8", "target"): 99}


class TestBrokerCrossCheck:
    def test_live_open_sell_is_adopted_not_duplicated(self):
        """Crash between place and journal-update left a live SELL at IB
        and a PENDING row, and the in-memory guard is gone. The next cycle
        must adopt the live order (journal → PLACED, its orderId) and
        place nothing."""
        db = FakeJournalDb([dict(GOOG_TRADE)])
        result, client, _ = _run(db, open_orders=[_open_sell_trade()])
        client.place_order.assert_not_called()
        assert _status(db) == "PLACED"
        assert db.trades["trade-8"]["exit_orders"]["target"]["order_id"] == 99
        assert result.get("orders_adopted", 0) == 1

    def test_adoption_heal_failure_arms_guard_no_placement(self):
        db = FailingUpdateDb([dict(GOOG_TRADE)])
        result, client, handler = _run(db, open_orders=[_open_sell_trade()])
        client.place_order.assert_not_called()
        assert "error" in result
        assert ("trade-8", "target") in handler._unrecorded_placements

    def test_crosscheck_unavailable_defers_placements(self):
        """If IB open orders can't be fetched, do not place unverified —
        defer the cycle with an error heartbeat."""
        db = FakeJournalDb([dict(GOOG_TRADE)])
        result, client, _ = _run(db, open_orders_raises=True)
        client.place_order.assert_not_called()
        assert "error" in result
        assert _status(db) == "PENDING"

    def test_working_target_is_not_adopted_as_the_stop(self):
        """T-055: both exit legs sit on the SAME contract, so a
        contract-only index hands the working TARGET to the stop
        candidate — the stop is marked PLACED with the target's orderId
        and never reaches IB, leaving the position unprotected once the
        target fills."""
        db = FakeJournalDb([_both_legs_trade()])
        placed = _confirmed_trade(order_id=4242, perm_id=777002)
        result, client, _ = _run(
            db,
            open_orders=[_open_sell_trade(limit_price=15.00)],
            place_trade=placed,
        )

        assert _leg(db, "target")["order_id"] == 99
        assert result.get("orders_adopted", 0) == 1

        assert client.place_order.call_count == 1
        assert _leg(db, "stop")["order_id"] != 99
        assert _leg(db, "stop")["order_id"] == 4242
        assert _leg(db, "stop")["status"] == "PLACED"

    def test_working_stop_is_not_adopted_as_the_target(self):
        """Mirror of T-055: the surviving leg at IB is the STOP; the
        target must still be placed."""
        db = FakeJournalDb([_both_legs_trade()])
        placed = _confirmed_trade(order_id=4243, perm_id=777003)
        result, client, _ = _run(
            db,
            open_orders=[_open_sell_trade(order_id=77, limit_price=14.50)],
            place_trade=placed,
        )

        assert _leg(db, "stop")["order_id"] == 77
        assert result.get("orders_adopted", 0) == 1
        assert client.place_order.call_count == 1
        assert _leg(db, "target")["order_id"] == 4243

    def test_unpriced_working_sell_defers_instead_of_adopting(self):
        """Fail-safe: a working SELL whose limit price is unreadable
        cannot be matched to an exit leg. Neither adopt it (that would
        write a false PLACED) nor place beside it (possible duplicate) —
        defer the leg with the row still PENDING."""
        db = FakeJournalDb([_both_legs_trade()])
        result, client, _ = _run(
            db,
            open_orders=[_open_sell_trade(limit_price=None)],
        )

        client.place_order.assert_not_called()
        assert _leg(db, "target")["status"] == "PENDING"
        assert _leg(db, "stop")["status"] == "PENDING"
        assert result["orders_skipped"] == 2
        assert "error" in result

    def test_no_matching_open_order_places_normally(self):
        """Control: an unrelated open SELL (different conId + symbol) must
        not block placement."""
        db = FakeJournalDb([dict(GOOG_TRADE)])
        unrelated = _open_sell_trade(con_id=111, local_symbol="MU 260918P00090000")
        result, client, _ = _run(db, open_orders=[unrelated])
        assert client.place_order.call_count == 1
        assert _status(db) == "PLACED"
        assert result["orders_placed"] == 1


class TestJournalWriteReadback:
    def test_silently_lost_update_returns_false_and_arms_guard(self):
        """R-012: a concurrent wholesale overwrite can swallow the PLACED
        write. The update must be verified by read-back; a lost write
        arms the guard instead of being trusted."""
        db = LostWriteDb([dict(GOOG_TRADE)])
        result, client, handler = _run(db)
        assert client.place_order.call_count == 1
        assert "error" in result
        assert ("trade-8", "target") in handler._unrecorded_placements


def test_timed_out_exit_worker_cannot_overlap_or_duplicate_sell():
    started = threading.Event()
    release = threading.Event()
    placements = []

    class TimedOutExitHandler:
        name = "exit_orders"
        interval_seconds = 1
        max_runtime_seconds = 0.01

        def run(self):
            placements.append("SELL")
            started.set()
            release.wait(timeout=2)
            return {"status": "ok"}

        def record_cycle_health(self, *_args, **_kwargs):
            return None

    daemon = MonitorDaemon(respect_market_hours=False)
    handler = TimedOutExitHandler()

    first = daemon._run_handler_bounded(handler)
    assert started.is_set()
    assert "timed out" in first["error"]

    second = daemon._run_handler_bounded(handler)
    assert "still running" in second["error"]
    time.sleep(0.02)
    assert placements == ["SELL"]
    release.set()
