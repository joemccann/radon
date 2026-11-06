"""journal_rehydrate labels buckets through the shared classifier (§1 #11)."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from journal_rehydrate import _prior_state_index, rehydrate_from_executions
from trade_blotter.models import Execution, SecurityType, Side


def _exec(exec_id, side, qty, *, sec_type=SecurityType.STOCK, strike=None, right=None, expiry=None):
    return Execution(
        exec_id=exec_id,
        time=datetime(2026, 11, 2, 10, 0, 0),
        symbol="AAOI",
        sec_type=sec_type,
        side=side,
        quantity=Decimal(qty),
        price=Decimal("10"),
        commission=Decimal("1"),
        strike=Decimal(str(strike)) if strike else None,
        right=right,
        expiry=expiry,
        codes="",
    )


def _only_row(executions, existing=None):
    payload, imported, _skipped, _latest = rehydrate_from_executions(
        executions, existing or {"trades": []}
    )
    assert imported == 1
    return payload["trades"][-1]


def test_stock_short_sale_bucket_is_sell_to_open():
    row = _only_row([_exec("9001", Side.SELL, 100)])
    assert row["action"] == "SELL_TO_OPEN"
    assert row["structure"].startswith("Short")


def test_stock_cover_bucket_is_buy_to_close():
    prior = {"trades": [{"ticker": "AAOI", "action": "SELL_TO_OPEN", "shares": 100,
                         "structure": "Short Stock (STK)", "date": "2026-10-01"}]}
    row = _only_row([_exec("9002", Side.BUY, 100)], prior)
    assert row["action"] == "BUY_TO_CLOSE"


def test_option_flip_bucket_records_residual():
    prior = {"trades": [{"ticker": "AAOI", "action": "BUY_OPTION", "contracts": 2,
                         "strike": 40.0, "right": "C", "expiry": "20261218",
                         "structure": "Long Call $40", "date": "2026-10-01"}]}
    row = _only_row(
        [_exec("9003", Side.SELL, 5, sec_type=SecurityType.OPTION, strike=40, right="C", expiry="20261218")],
        prior,
    )
    assert row["action"] == "SELL_OPTION"
    assert row["flip_open_qty"] == 3


def test_closed_round_trip_does_not_move_prior_state():
    trades = [
        {"ticker": "AAOI", "action": "BUY", "shares": 100, "structure": "Long Stock (STK)", "date": "2026-10-01"},
        {"ticker": "AAOI", "action": "CLOSED", "shares": 50, "structure": "Closed Stock (STK)", "date": "2026-10-02"},
    ]
    assert _prior_state_index(trades) == {"AAOI|STK": 100.0}
