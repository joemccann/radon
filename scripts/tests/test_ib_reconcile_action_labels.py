"""ib_reconcile labels groups through the shared classifier (§1 #11).

The prior position is the broker's current position less the session's
net: the realized-P&L heuristic labelled a zero-P&L close as an open, and
wrote stock short sales as ``SHORT`` (which no reader treated as open).
"""
from __future__ import annotations

from datetime import datetime

from ib_reconcile import find_new_trades, group_executions_by_symbol


def _exe(side, shares, *, sec_type="STK", realized_pnl=0, strike=None, expiry=None, right=None):
    return {
        "time": datetime(2026, 11, 2, 10, 0), "symbol": "AAOI", "sec_type": sec_type,
        "side": side, "shares": shares, "price": 10.0, "exchange": "SMART",
        "commission": 1.0, "realized_pnl": realized_pnl, "strike": strike,
        "expiry": expiry, "right": right, "exec_id": f"{side}-{shares}",
    }


def _pos(quantity, *, sec_type="STK", strike=None, expiry=None, right=None):
    return {"symbol": "AAOI", "sec_type": sec_type, "quantity": quantity, "avg_cost": 1.0,
            "strike": strike, "expiry": expiry, "right": right}


def _action(executions, positions):
    (group,) = group_executions_by_symbol(executions, positions).values()
    return group["action"]


def test_stock_short_sale_is_sell_to_open():
    assert _action([_exe("SLD", 100)], [_pos(-100)]) == "SELL_TO_OPEN"


def test_zero_pnl_close_of_a_long_is_a_close():
    assert _action([_exe("SLD", 100)], []) == "SELL"


def test_option_close_without_realized_pnl_is_sell_option():
    opt = dict(sec_type="OPT", strike=40.0, expiry="20261218", right="C")
    assert _action([_exe("SLD", 5, **opt)], []) == "SELL_OPTION"


def test_stock_cover_is_buy_to_close():
    # Flat after buying 100: the buy covered a 100-share short.
    assert _action([_exe("BOT", 100)], []) == "BUY_TO_CLOSE"
    assert _action([_exe("BOT", 100)], [_pos(100)]) == "BUY"


def test_unknown_positions_refuse_to_label():
    assert _action([_exe("SLD", 100)], None) is None
    assert find_new_trades([_exe("SLD", 100)], {"trades": []}, None) == []


def test_flip_carries_residual():
    trades = find_new_trades([_exe("SLD", 150)], {"trades": []}, [_pos(-50)])
    assert trades[0]["action"] == "SELL"
    assert trades[0]["flip_open_qty"] == 50
