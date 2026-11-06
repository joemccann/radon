#!/usr/bin/env python3
"""Audit 2026-11-03 §1 #5 / §2.2: exit_orders and bankroll_guard each kept a
private copy of the IB working-status set, and exit_orders' copy omitted
``PendingCancel``. A PendingCancel SELL can still fill, so leaving it out of
the oversell guard let a second full-size exit through beside it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from utils import ib_order_status
from monitor_daemon.handlers.exit_orders import ExitOrdersHandler
import bankroll_guard
from test_exit_orders import FakeJournalDb
from test_exit_orders_guard_durability import _open_sell_trade
from test_exit_orders_oca import _run, _trade


def test_exit_orders_working_set_is_the_shared_vocabulary():
    assert ExitOrdersHandler._ACTIVE_ORDER_STATUSES == ib_order_status.WORKING
    assert ExitOrdersHandler._TERMINAL_FAILED_STATUSES == ib_order_status.TERMINAL_FAILED


def test_bankroll_guard_resting_set_is_the_shared_vocabulary():
    assert bankroll_guard._RESTING_STATUSES == frozenset(
        status.upper() for status in ib_order_status.WORKING
    )


def test_a_pending_cancel_sell_still_counts_against_the_held_size():
    db = FakeJournalDb([_trade(stop=3.00, held=44)])
    working = _open_sell_trade(limit_price=7.00, oca_group=None, quantity=44)
    working.orderStatus.status = ib_order_status.PENDING_CANCEL

    result, client, _ = _run(db, open_orders=[working])

    client.place_order.assert_not_called()
    reasons = {s.get("reason") for s in result.get("skipped", [])}
    assert "oversell_guard" in reasons, result
