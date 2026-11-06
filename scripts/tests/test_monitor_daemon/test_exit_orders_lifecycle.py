#!/usr/bin/env python3
"""Audit 2026-11-03 §1 #5: the exit-order lifecycle as an explicit machine.

``journal.payload.exit_orders.{target,stop}.status`` was PENDING -> PLACED
and nothing else: PLACED was terminal and never reconciled with IB, so a
broker-cancelled or expired GTC left the position unprotected while the
heartbeat said ok. The leg now moves only through ``transition_exit_leg``:

    PENDING  --placed-->    PLACED
    PLACED   --filled-->    FILLED
    PLACED   --cancelled--> CANCELLED
    PLACED   --expired-->   EXPIRED
    CANCELLED|EXPIRED --requeued--> PENDING   (operator re-place path)

and every cycle reconciles PLACED legs against IB open orders + executions.

Also pinned here (B3 / B5):
  * the unrecorded-placement guard is keyed on a non-null journal identity;
    a row with no ``trade_id`` cannot record a placement, so it is refused
    instead of being placed and guarded under ``(None, order_type)``;
  * a failed guard heal surfaces an error instead of ``continue``-ing under
    an ok heartbeat; a guard whose row moved on is dropped;
  * the order-limit refusal latch survives a restart, and clears as soon as
    the limit stops refusing.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from monitor_daemon.handlers.exit_orders import (
    ExitLegEvent,
    ExitLegStatus,
    ExitOrdersHandler,
    InvalidExitLegTransition,
    transition_exit_leg,
)
from test_exit_orders import FakeJournalDb, _NoTradeIdJournalDb, _pending_goog_trade
from test_exit_orders_guard_durability import (
    FailingUpdateDb,
    _confirmed_trade,
    _open_sell_trade,
)

SPEC = {"symbol": "GOOG", "expiry": "20990417", "strike": 315, "right": "C"}
AT = "2026-11-03T15:00:00Z"


# --------------------------------------------------------------- the machine
class TestTransitionTable:
    @pytest.mark.parametrize("start,event,end", [
        ("PENDING", ExitLegEvent.PLACED, "PLACED"),
        ("PLACED", ExitLegEvent.FILLED, "FILLED"),
        ("PLACED", ExitLegEvent.CANCELLED, "CANCELLED"),
        ("PLACED", ExitLegEvent.EXPIRED, "EXPIRED"),
        ("CANCELLED", ExitLegEvent.REQUEUED, "PENDING"),
        ("EXPIRED", ExitLegEvent.REQUEUED, "PENDING"),
    ])
    def test_allowed_transitions(self, start, event, end):
        leg = transition_exit_leg(
            {"status": start, "order_id": 7}, event, at=AT, order_id=99, perm_id=1
        )
        assert leg["status"] == ExitLegStatus(end).value

    @pytest.mark.parametrize("start,event", [
        ("PENDING", ExitLegEvent.FILLED),
        ("PENDING", ExitLegEvent.CANCELLED),
        ("PLACED", ExitLegEvent.PLACED),
        ("PLACED", ExitLegEvent.REQUEUED),
        ("FILLED", ExitLegEvent.REQUEUED),
        ("FILLED", ExitLegEvent.CANCELLED),
        ("PENDING_MANUAL", ExitLegEvent.PLACED),
        (None, ExitLegEvent.PLACED),
    ])
    def test_everything_else_is_refused(self, start, event):
        with pytest.raises(InvalidExitLegTransition):
            transition_exit_leg({"status": start}, event, at=AT, order_id=99)

    def test_placed_requires_an_integer_order_id(self):
        for bad in (None, "99", True):
            with pytest.raises(InvalidExitLegTransition):
                transition_exit_leg(
                    {"status": "PENDING"}, ExitLegEvent.PLACED, at=AT, order_id=bad
                )

    def test_transition_is_pure_and_stamps_the_leg(self):
        leg = {"status": "PENDING", "price": 3.0}
        placed = transition_exit_leg(
            leg, ExitLegEvent.PLACED, at=AT, order_id=99, perm_id=777001
        )
        assert leg == {"status": "PENDING", "price": 3.0}
        assert placed["order_id"] == 99 and placed["perm_id"] == 777001
        assert placed["placed_at"] == AT

        requeued = transition_exit_leg(
            dict(placed, status="CANCELLED"), ExitLegEvent.REQUEUED, at=AT
        )
        assert requeued["order_id"] is None and requeued["previous_order_id"] == 99


# ------------------------------------------------------------- reconciliation
def _leg(status, price, order_id=None, perm_id=None, held=44):
    return {
        "price": price, "status": status, "order_id": order_id,
        "perm_id": perm_id, "contracts": held, "contract_spec": dict(SPEC),
    }


def _row(**legs):
    return {
        "id": 8, "ticker": "GOOG", "structure": "Long Call",
        "contracts": 44, "exit_orders": legs,
    }


def _fill(order_id, perm_id, shares):
    return SimpleNamespace(
        execution=SimpleNamespace(orderId=order_id, permId=perm_id, shares=shares)
    )


def _run(db, *, handler=None, open_orders=(), executions=(), executions_raise=False,
         snapshot_ended=True):
    with patch("monitor_daemon.handlers.exit_orders.IBClient") as mock_cls, \
         patch("monitor_daemon.handlers.exit_orders.Option"):
        client = MagicMock()
        mock_cls.return_value = client
        contract = MagicMock()
        contract.localSymbol = "GOOG  990417C00315000"
        contract.conId = 606060
        client.qualify_contracts.return_value = [contract]
        quote = MagicMock()
        quote.bid, quote.ask, quote.halted = 4.90, 5.10, 0
        client.get_quote.return_value = quote
        client.get_open_orders.return_value = list(open_orders)
        client._open_orders_snapshot_ended = snapshot_ended
        if executions_raise:
            client.get_executions.side_effect = RuntimeError("reqExecutions timeout")
        else:
            client.get_executions.return_value = list(executions)
        client.place_order.side_effect = lambda *_a, **_k: _confirmed_trade()
        handler = handler or ExitOrdersHandler(db=db)
        result = handler.execute()
    return result, client, handler


def _status(db, order_type):
    return db.trades["trade-8"]["exit_orders"][order_type]["status"]


class TestPlacedLegsAreReconciled:
    def test_a_working_order_stays_placed(self):
        db = FakeJournalDb([_row(stop=_leg("PLACED", 4.0, 99, 777001))])
        working = _open_sell_trade(order_id=99, limit_price=4.0)

        result, client, _ = _run(db, open_orders=[working])

        assert _status(db, "stop") == "PLACED"
        assert not result.get("error"), result
        client.place_order.assert_not_called()

    def test_a_broker_cancelled_gtc_is_detected_and_alerted(self):
        db = FakeJournalDb([_row(stop=_leg("PLACED", 4.0, 99, 777001))])

        result, client, _ = _run(db)

        assert _status(db, "stop") == "CANCELLED"
        assert result.get("error"), "an unprotected position must not heartbeat ok"
        assert "#99" in result["error"]
        client.place_order.assert_not_called()

    def test_a_partial_fill_then_cancel_records_the_filled_size(self):
        db = FakeJournalDb([_row(stop=_leg("PLACED", 4.0, 99, 777001))])

        result, _, _ = _run(db, executions=[_fill(99, 777001, 10.0)])

        leg = db.trades["trade-8"]["exit_orders"]["stop"]
        assert leg["status"] == "CANCELLED"
        assert leg["filled_quantity"] == 10.0
        assert result.get("error")

    def test_a_fully_executed_leg_is_filled_without_an_alert(self):
        db = FakeJournalDb([_row(target=_leg("PLACED", 6.0, 99, 777001))])

        result, _, _ = _run(db, executions=[_fill(99, 777001, 44.0)])

        assert _status(db, "target") == "FILLED"
        assert not result.get("error"), result

    def test_an_oca_sibling_cancel_after_a_fill_is_not_an_alert(self):
        db = FakeJournalDb([_row(
            target=_leg("PLACED", 6.0, 98, 777000),
            stop=_leg("PLACED", 4.0, 99, 777001),
        )])

        result, _, _ = _run(db, executions=[_fill(98, 777000, 44.0)])

        assert _status(db, "target") == "FILLED"
        assert _status(db, "stop") == "CANCELLED"
        assert not result.get("error"), result

    def test_a_leg_gone_after_the_option_expired_is_expired(self):
        leg = _leg("PLACED", 4.0, 99, 777001)
        leg["contract_spec"]["expiry"] = "20200117"
        db = FakeJournalDb([_row(stop=leg)])

        result, _, _ = _run(db)

        assert _status(db, "stop") == "EXPIRED"
        assert not result.get("error"), result

    def test_a_perm_id_match_is_required_when_the_leg_has_one(self):
        """orderId is per client id and the handler rotates ids; another
        client's order 99 is not this leg."""
        db = FakeJournalDb([_row(stop=_leg("PLACED", 4.0, 99, 777001))])
        stranger = _open_sell_trade(order_id=99, limit_price=4.0)
        stranger.order.permId = 555

        result, _, _ = _run(db, open_orders=[stranger])

        assert _status(db, "stop") == "CANCELLED"

    def test_unavailable_executions_defer_reconciliation(self):
        db = FakeJournalDb([_row(stop=_leg("PLACED", 4.0, 99, 777001))])

        result, _, _ = _run(db, executions_raise=True)

        assert _status(db, "stop") == "PLACED"
        assert result.get("error")

    def test_an_incomplete_open_order_snapshot_defers_reconciliation(self):
        db = FakeJournalDb([_row(stop=_leg("PLACED", 4.0, 99, 777001))])

        result, client, _ = _run(db, snapshot_ended=False)

        assert _status(db, "stop") == "PLACED"
        assert result.get("error")
        client.get_executions.assert_not_called()

    def test_a_failed_transition_write_is_an_error(self):
        db = FailingUpdateDb([_row(stop=_leg("PLACED", 4.0, 99, 777001))])

        result, _, _ = _run(db)

        assert _status(db, "stop") == "PLACED"
        assert result.get("error")

    def test_the_cancel_alert_heartbeats_error_through_run(self):
        db = FakeJournalDb([_row(stop=_leg("PLACED", 4.0, 99, 777001))])
        handler = ExitOrdersHandler(db=db)
        beats = []
        handler.record_cycle_health = lambda state, **kw: beats.append(state)

        with patch("monitor_daemon.handlers.exit_orders.IBClient") as mock_cls:
            client = MagicMock()
            mock_cls.return_value = client
            client.get_open_orders.return_value = []
            client.get_executions.return_value = []
            handler.run()

        assert beats == ["error"]

    def test_placement_records_the_perm_id(self):
        db = FakeJournalDb([_row(stop=_leg("PENDING", 4.0))])

        _run(db)

        leg = db.trades["trade-8"]["exit_orders"]["stop"]
        assert leg["status"] == "PLACED"
        assert leg["order_id"] == 99 and leg["perm_id"] == 777001


class TestRequeue:
    def test_a_cancelled_leg_can_be_requeued_for_placement(self):
        db = FakeJournalDb([_row(stop=_leg("CANCELLED", 4.0, 99, 777001))])

        assert ExitOrdersHandler(db=db).requeue_exit_leg("trade-8", "stop") is True

        leg = db.trades["trade-8"]["exit_orders"]["stop"]
        assert leg["status"] == "PENDING"
        assert leg["previous_order_id"] == 99

    def test_a_filled_leg_cannot_be_requeued(self):
        db = FakeJournalDb([_row(stop=_leg("FILLED", 4.0, 99, 777001))])

        assert ExitOrdersHandler(db=db).requeue_exit_leg("trade-8", "stop") is False
        assert _status(db, "stop") == "FILLED"


# ------------------------------------------------------------------ B3 guard
class TestGuardIdentity:
    def test_a_row_without_a_journal_id_is_never_placed(self):
        """Its PLACED write cannot land (``WHERE trade_id = NULL`` matches
        nothing), so placing it arms a guard under ``(None, order_type)``
        that suppresses every other id-less row's leg, forever."""
        goog = _pending_goog_trade()
        msft = _pending_goog_trade()
        msft["id"] = 9
        msft["ticker"] = "MSFT"
        db = _NoTradeIdJournalDb([goog, msft])

        result, client, handler = _run(db)

        client.place_order.assert_not_called()
        assert result.get("error")
        reasons = {s.get("reason") for s in result["skipped"]}
        assert reasons == {"no_journal_identity"}
        assert handler._unrecorded_placements == {}

    def test_restore_drops_legacy_null_keyed_guard_entries(self):
        handler = ExitOrdersHandler(db=FakeJournalDb([]))
        handler.set_state({"unrecorded_placements": [
            [None, "target", 98],
            ["trade-8", "target", 99],
        ]})
        assert handler._unrecorded_placements == {
            ("trade-8", "target"): {"order_id": 99, "perm_id": None},
        }

    def test_a_failed_guard_heal_is_an_error_not_a_silent_skip(self):
        db = FailingUpdateDb([_row(target=_leg("PENDING", 5.0))])
        handler = ExitOrdersHandler(db=db)
        handler._unrecorded_placements[("trade-8", "target")] = {
            "order_id": 99, "perm_id": 777001,
        }

        result, client, _ = _run(db, handler=handler)

        client.place_order.assert_not_called()
        assert result.get("error"), "a guarded leg the journal cannot record is unhealthy"
        assert ("trade-8", "target") in handler._unrecorded_placements

    def test_a_guard_whose_row_moved_on_is_dropped(self):
        db = FakeJournalDb([_row(target=_leg("PLACED", 5.0, 99, 777001))])
        handler = ExitOrdersHandler(db=db)
        handler._unrecorded_placements[("trade-8", "target")] = {
            "order_id": 99, "perm_id": 777001,
        }
        working = _open_sell_trade(order_id=99, limit_price=5.0)

        _run(db, handler=handler, open_orders=[working])

        assert handler._unrecorded_placements == {}

    def test_a_healed_guard_is_reconciled_against_the_broker_in_the_same_cycle(self):
        """The guarded order is gone from IB: the heal writes PLACED and the
        reconciliation turns it into an alerted CANCELLED, never a re-place."""
        db = FakeJournalDb([_row(target=_leg("PENDING", 5.0))])
        handler = ExitOrdersHandler(db=db)
        handler._unrecorded_placements[("trade-8", "target")] = {
            "order_id": 99, "perm_id": 777001,
        }

        result, client, _ = _run(db, handler=handler)

        client.place_order.assert_not_called()
        assert handler._unrecorded_placements == {}
        assert _status(db, "target") == "CANCELLED"
        assert result.get("error")

    def test_guard_state_round_trips_through_json(self):
        handler = ExitOrdersHandler(db=FakeJournalDb([]))
        handler._unrecorded_placements[("trade-8", "stop")] = {
            "order_id": 99, "perm_id": 777001,
        }
        restored = ExitOrdersHandler(db=FakeJournalDb([]))
        restored.set_state(json.loads(json.dumps(handler.get_state())))
        assert restored._unrecorded_placements == handler._unrecorded_placements


class TestCorruptLoadSeed:
    def test_seed_rebuilds_the_guard_from_the_rows_oca_group(self):
        """After a corrupt or missing state file the guard is gone. A working
        order of the leg's type in the row's OCA group is this handler's
        unrecorded placement even when its price no longer matches (IB tick
        normalisation): adopt it, never place beside it."""
        db = FakeJournalDb([_row(stop=_leg("PENDING", 4.0))])
        handler = ExitOrdersHandler(db=db)
        handler.seed_state_after_corrupt_load()
        working = _open_sell_trade(order_id=99, limit_price=4.0)
        working.order.orderType = "STP"
        working.order.auxPrice = 3.95
        working.order.ocaGroup = "radon-exit-trade-8"

        result, client, _ = _run(db, handler=handler, open_orders=[working])

        client.place_order.assert_not_called()
        leg = db.trades["trade-8"]["exit_orders"]["stop"]
        assert leg["status"] == "PLACED" and leg["order_id"] == 99


# ------------------------------------------------------------ B5 refusal latch
class TestRefusalLatchDurability:
    VIOLATION = {"message": "quantity 44 exceeds the per-order cap"}

    def _cycle(self, handler, db, limits):
        with patch("order_limits.check_order_limits", side_effect=limits):
            return _run(db, handler=handler)

    def test_the_latch_survives_a_restart(self):
        db = FakeJournalDb([_row(stop=_leg("PENDING", 4.0))])
        first = ExitOrdersHandler(db=db)
        self._cycle(first, db, lambda _o: self.VIOLATION)

        restarted = ExitOrdersHandler(db=db)
        restarted.set_state(json.loads(json.dumps(first.get_state())))
        result, client, _ = self._cycle(restarted, db, lambda _o: self.VIOLATION)

        client.place_order.assert_not_called()
        assert [s["reason"] for s in result["skipped"]] == ["limit_refused_previously"]
        assert result.get("error")

    def test_a_latched_leg_places_once_the_limit_stops_refusing(self):
        """The latch suppresses the repeat report, not the re-check. An
        operator who raises the cap must not wait for the leg's price to
        move (or, now that the latch persists, forever)."""
        db = FakeJournalDb([_row(stop=_leg("PENDING", 4.0))])
        handler = ExitOrdersHandler(db=db)
        self._cycle(handler, db, lambda _o: self.VIOLATION)

        result, client, _ = self._cycle(handler, db, lambda _o: None)

        assert client.place_order.call_count == 1, result
        assert _status(db, "stop") == "PLACED"
