"""An option IB reports as untraded today cannot keep a cached last as its mark.

Live 2026-11-06: IWO 2026-12-18 380C showed bid -1, ask 2.40, volume 0 and an IB
mark of 0.8927, while a cached last of 1.95 held the row at $58,500 against IB's
$26,780 and left the Unrealized P&L rows $33.5k away from the account figure.
"""

from types import SimpleNamespace
from unittest.mock import Mock

from ib_sync import _broker_marks, _stamp_position_price
from option_session_mark import poll_contract_mark

TODAY = "2026-11-06"
IWO_KEY = "IWO_20261218_380_C"
CACHED = {"last": 1.95, "bid": 0.25, "ask": 1.0, "checked_on": TODAY}


def _poll(**overrides):
    kwargs = dict(
        sec_type="OPT",
        market_price=None,
        bid=None,
        ask=2.4,
        close=1.69,
        trade=None,
        session_mark=CACHED,
        today=TODAY,
        volume=0,
        broker_mark=0.8927,
    )
    kwargs.update(overrides)
    return poll_contract_mark(**kwargs)


def test_zero_volume_without_a_book_uses_broker_mark():
    assert _poll() == (0.8927, True, None)


def test_a_print_today_keeps_the_session_last():
    price, calculated, _ = _poll(volume=5)
    assert (price, calculated) == (1.95, False)


def test_unknown_volume_keeps_the_session_last():
    assert _poll(volume=None)[0] == 1.95


def test_nan_volume_keeps_the_session_last():
    assert _poll(volume=float("nan"))[0] == 1.95


def test_two_sided_book_outranks_broker_mark():
    price, calculated, _ = _poll(bid=0.25, ask=1.0)
    assert (price, calculated) == (0.625, True)


def test_missing_broker_mark_keeps_the_session_last():
    assert _poll(broker_mark=None)[0] == 1.95


def test_zero_broker_mark_is_not_a_price():
    assert _poll(broker_mark=0.0)[0] == 1.95


def test_no_history_request_when_broker_mark_applies():
    def fetch():
        raise AssertionError("a broker mark needs no history")

    stale = {"last": 1.95, "checked_on": "2026-09-20"}
    assert _poll(session_mark=stale, fetch_history=fetch)[0] == 0.8927


def test_stock_ignores_broker_mark():
    assert _poll(sec_type="STK", market_price=52.66, bid=52.6, ask=52.7)[0] == 52.66


def test_stamp_reads_ticker_volume_and_broker_mark_by_con_id():
    ticker = SimpleNamespace(
        marketPrice=lambda: float("nan"),
        bid=-1.0,
        ask=2.4,
        close=1.69,
        last=float("nan"),
        volume=0.0,
    )
    pos = dict(
        secType="OPT",
        symbol="IWO",
        expiry="20261218",
        strike=380,
        right="C",
        position=300,
        contract=SimpleNamespace(symbol="IWO", conId=42),
    )
    marks = {IWO_KEY: dict(CACHED)}
    dirty = _stamp_position_price(pos, ticker, marks, Mock(), TODAY, broker_marks={42: 0.8927})
    assert dirty is False
    assert pos["marketPrice"] == 0.8927
    assert pos["marketValue"] == 26781.0
    assert pos["marketPriceIsCalculated"] is True


def test_broker_marks_map_portfolio_items_by_con_id():
    client = Mock()
    client.ib.portfolio.return_value = [
        SimpleNamespace(contract=SimpleNamespace(conId=42), marketPrice=0.8927),
        SimpleNamespace(contract=SimpleNamespace(conId=7), marketPrice=52.66),
    ]
    assert _broker_marks(client) == {42: 0.8927, 7: 52.66}


def test_broker_marks_failure_leaves_the_existing_mark_chain():
    client = Mock()
    client.ib.portfolio.side_effect = ConnectionError("gateway down")
    assert _broker_marks(client) == {}
