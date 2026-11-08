#!/usr/bin/env python3
"""Audit 2026-11-03 §1 #5: the stop leg was placed as a ``LimitOrder`` at
the stop price. A SELL limit below the market is marketable, so the
"protective stop" closed the position on placement. The stop leg is now a
``StopOrder`` (``StopLimitOrder`` when the journal carries a limit) whose
trigger is the stop price; the target stays a ``LimitOrder``.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from monitor_daemon.handlers.exit_orders import ExitOrdersHandler
from test_exit_orders import FakeJournalDb
from test_exit_orders_guard_durability import _confirmed_trade, _open_sell_trade
from test_exit_orders_oca import _trade


def _run(db, *, mid=5.00, open_orders=()):
    """Real ib_insync order classes: the test reads the order on the wire."""
    with patch("monitor_daemon.handlers.exit_orders.IBClient") as mock_cls, \
         patch("monitor_daemon.handlers.exit_orders.Option"):
        client = MagicMock()
        mock_cls.return_value = client
        contract = MagicMock()
        contract.localSymbol = "GOOG  260417C00315000"
        contract.conId = 606060
        client.qualify_contracts.return_value = [contract]
        quote = MagicMock()
        quote.bid, quote.ask, quote.halted = mid - 0.10, mid + 0.10, 0
        client.get_quote.return_value = quote
        client.get_open_orders.return_value = list(open_orders)
        client.place_order.side_effect = lambda *_a, **_k: _confirmed_trade()
        result = ExitOrdersHandler(db=db).execute()
    return result, client


def _placed_orders(client):
    return [call.args[1] for call in client.place_order.call_args_list]


def test_the_stop_leg_is_a_stop_order_triggered_at_the_stop_price():
    db = FakeJournalDb([_trade(stop=4.00)])

    result, client = _run(db)

    (order,) = _placed_orders(client)
    assert order.orderType == "STP", f"stop leg sent as {order.orderType}: {result}"
    assert order.action == "SELL"
    assert order.auxPrice == 4.00
    assert order.tif == "GTC"
    assert order.ocaGroup == "radon-exit-trade-8"


def test_a_stop_leg_with_a_journal_limit_is_a_stop_limit():
    trade = _trade(stop=4.00)
    trade["exit_orders"]["stop"]["limit_price"] = 3.80
    db = FakeJournalDb([trade])

    _result, client = _run(db)

    (order,) = _placed_orders(client)
    assert order.orderType == "STP LMT"
    assert order.auxPrice == 4.00
    assert order.lmtPrice == 3.80


def test_the_target_leg_stays_a_limit_order():
    db = FakeJournalDb([_trade(target=6.00)])

    _result, client = _run(db)

    (order,) = _placed_orders(client)
    assert order.orderType == "LMT"
    assert order.lmtPrice == 6.00


def test_a_working_stop_order_is_adopted_by_its_trigger_price():
    """IB reports a STP order's trigger in auxPrice and an unset lmtPrice.
    Keying adoption on lmtPrice alone never matched it, so the next cycle
    placed a second stop beside the live one."""
    db = FakeJournalDb([_trade(stop=4.00)])
    working = _open_sell_trade(limit_price=1.7976931348623157e308, quantity=44)
    working.order.orderType = "STP"
    working.order.auxPrice = 4.00

    result, client = _run(db, open_orders=[working])

    client.place_order.assert_not_called()
    assert result.get("orders_adopted") == 1, result
    assert db.trades["trade-8"]["exit_orders"]["stop"]["status"] == "PLACED"
