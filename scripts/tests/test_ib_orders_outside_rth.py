"""fetch_open_orders must serialize IB order.outsideRth as a bool."""
from __future__ import annotations

from types import SimpleNamespace


def _stk_trade(*, order_id: int, outside_rth: bool) -> SimpleNamespace:
    return SimpleNamespace(
        contract=SimpleNamespace(
            conId=1,
            symbol="TQQQ",
            secType="STK",
            strike=None,
            right=None,
            lastTradeDateOrContractMonth="",
            currency="USD",
            multiplier=None,
            localSymbol="TQQQ",
            tradingClass="TQQQ",
            comboLegs=None,
        ),
        order=SimpleNamespace(
            orderId=order_id,
            permId=1000 + order_id,
            orderRef="",
            action="BUY",
            orderType="LMT",
            totalQuantity=10,
            lmtPrice=50.0,
            auxPrice=None,
            tif="GTC",
            outsideRth=outside_rth,
        ),
        orderStatus=SimpleNamespace(
            status="Submitted",
            filled=0,
            remaining=10,
            avgFillPrice=0,
        ),
    )


def test_fetch_open_orders_includes_outside_rth_true_and_false():
    import ib_orders

    client = SimpleNamespace(
        get_open_orders=lambda: [
            _stk_trade(order_id=1, outside_rth=True),
            _stk_trade(order_id=2, outside_rth=False),
        ],
    )

    rows = ib_orders.fetch_open_orders(client)

    assert len(rows) == 2
    assert rows[0]["outsideRth"] is True
    assert rows[1]["outsideRth"] is False
    assert rows[0]["contract"]["secType"] == "STK"
    assert rows[1]["contract"]["secType"] == "STK"


def test_fetch_open_orders_defaults_missing_outside_rth_to_false():
    import ib_orders

    trade = _stk_trade(order_id=1, outside_rth=True)
    delattr(trade.order, "outsideRth")
    client = SimpleNamespace(get_open_orders=lambda: [trade])

    rows = ib_orders.fetch_open_orders(client)

    assert rows[0]["outsideRth"] is False
