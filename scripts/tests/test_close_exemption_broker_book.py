"""DS-2026-10-24-02: a close is exempted only against holdings the broker confirms.

The close exemptions measure the order against the latest portfolio snapshot
less resting opposite-side orders. A close that FILLED after the snapshot no
longer rests, yet the snapshot still shows the size it sold, so a second
close of the same size was exempted again and its surplus opened an uncapped
short. The placement funnel re-admits every store-proven close against the
broker's live positions and working orders before it transmits.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import eventkit
import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import bankroll_guard  # noqa: E402
from bankroll_guard import check_bankroll_admission as _real_bankroll_admission  # noqa: E402
from clients.ib_client import IBClient  # noqa: E402

NOW = datetime(2026, 10, 24, 15, 0, tzinfo=timezone.utc)
STOCK = SimpleNamespace(symbol="TQQQ", secType="STK", conId=1)


def _stale_snapshot_holding(shares):
    # NLV stale: only the close exemption can admit a SELL.
    return {
        "last_sync": (NOW - timedelta(minutes=30)).isoformat(),
        "account_summary": {"net_liquidation": 100_000.0},
        "positions": [{"ticker": "TQQQ", "legs": [
            {"type": "Stock", "direction": "LONG", "contracts": shares},
        ]}],
    }


def _sell(quantity=100):
    return {"type": "stock", "symbol": "TQQQ", "action": "SELL", "quantity": quantity,
            "limitPrice": 60.0, "tif": "DAY"}


def _client(broker_shares, resting=()):
    trade = MagicMock()
    trade.order.orderId = 99
    trade.order.permId = 12345
    trade.orderStatus.status = "Submitted"
    trade.orderStatus.whyHeld = ""
    trade.log = []
    client = MagicMock()
    client._ib = MagicMock()
    client._ib.errorEvent = eventkit.Event("errorEvent")
    client.place_order = MagicMock(return_value=trade)
    client.qualify_contracts = MagicMock(return_value=[MagicMock(conId=1)])
    client.get_positions = MagicMock(return_value=(
        [SimpleNamespace(contract=STOCK, position=broker_shares)] if broker_shares else []
    ))
    client.get_open_orders = MagicMock(return_value=list(resting))
    return client


def _resting_sell(quantity):
    return SimpleNamespace(
        contract=STOCK,
        order=SimpleNamespace(action="SELL"),
        orderStatus=SimpleNamespace(status="Submitted", remaining=float(quantity)),
    )


@pytest.fixture(autouse=True)
def _enforced(monkeypatch):
    import app_preferences

    app_preferences.seed_snapshot_for_tests({"RADON_BANKROLL_CAP_ENFORCE_ALL_PATHS": "true"})
    monkeypatch.setattr(bankroll_guard, "check_bankroll_admission", _real_bankroll_admission)
    monkeypatch.setattr(bankroll_guard, "_utcnow", lambda: NOW)
    monkeypatch.setattr(bankroll_guard, "_load_working_orders", lambda: [])
    monkeypatch.setattr(bankroll_guard, "_load_latest_snapshot", lambda: _stale_snapshot_holding(100))


def _place(params, client):
    with patch("ib_place_order.IBClient", return_value=client), \
         patch("ib_place_order.Stock", return_value=MagicMock()), \
         patch("ib_place_order.LimitOrder", return_value=MagicMock()):
        import ib_place_order

        return ib_place_order.place_order(params)


class TestPlacementReadmitsTheCloseAtTheBroker:
    def test_a_close_the_broker_no_longer_holds_never_transmits(self):
        client = _client(broker_shares=0)

        result = _place(_sell(100), client)

        assert result["status"] == "error", result
        client.place_order.assert_not_called()

    def test_a_resting_broker_close_reserves_the_holding(self):
        client = _client(broker_shares=100, resting=[_resting_sell(100)])

        result = _place(_sell(100), client)

        assert result["status"] == "error", result
        client.place_order.assert_not_called()

    def test_an_unreadable_broker_book_proves_no_close(self):
        client = _client(broker_shares=100)
        client.get_positions.side_effect = RuntimeError("socket gone")

        result = _place(_sell(100), client)

        assert result["status"] == "error", result
        client.place_order.assert_not_called()

    def test_a_close_the_broker_confirms_still_transmits(self):
        client = _client(broker_shares=100)

        result = _place(_sell(100), client)

        assert result["status"] == "ok", result
        client.place_order.assert_called_once()


class TestBrokerBook:
    def test_combo_legs_resolve_through_held_con_ids(self):
        put = SimpleNamespace(symbol="SPX", secType="OPT", conId=7, right="P", strike=5000.0,
                              lastTradeDateOrContractMonth="20261120")
        bag = SimpleNamespace(secType="BAG", comboLegs=[
            SimpleNamespace(conId=7, action="BUY", ratio=1),
            SimpleNamespace(conId=8, action="BUY", ratio=1),
        ])
        client = MagicMock()
        client.get_positions.return_value = [SimpleNamespace(contract=put, position=10.0)]
        client.get_open_orders.return_value = [SimpleNamespace(
            contract=bag, order=SimpleNamespace(action="SELL"),
            orderStatus=SimpleNamespace(status="Submitted", remaining=4.0),
        )]

        held, resting = bankroll_guard.read_broker_book(client)

        key = ("SPX", "P", 5000.0, "20261120")
        assert held == {key: 10.0}
        assert resting == {key: (-4.0, 0.0)}


class _EndEvent:
    def __init__(self):
        self._handlers = []

    def __iadd__(self, handler):
        self._handlers.append(handler)
        return self

    def __isub__(self, handler):
        if handler in self._handlers:
            self._handlers.remove(handler)
        return self

    def emit(self):
        for handler in list(self._handlers):
            handler()


def _snapshot_client(mock_ib, *, positions_end, orders_end, open_trades=()):
    """A connected client whose end events fire only when the test says so."""
    mock_ib.isConnected.return_value = True
    mock_ib.errorEvent = eventkit.Event("errorEvent")
    position_end = _EndEvent()
    order_end = _EndEvent()
    mock_ib.positionEndEvent = position_end
    mock_ib.openOrderEndEvent = order_end
    held = SimpleNamespace(contract=STOCK, position=100.0, avgCost=60.0)
    mock_ib.positions.return_value = [held]
    mock_ib.openTrades.return_value = list(open_trades)
    mock_ib.qualifyContracts.return_value = [
        SimpleNamespace(conId=1, symbol="TQQQ", secType="STK")
    ]

    def req_positions():
        if positions_end:
            position_end.emit()

    def req_orders():
        if orders_end:
            order_end.emit()

    mock_ib.reqPositions.side_effect = req_positions
    mock_ib.reqAllOpenOrders.side_effect = req_orders
    client = IBClient()
    return client, mock_ib


class TestBrokerBookNeedsAFinishedSnapshot:
    """REL-331 / R-742. An unfinished snapshot is not proof the holding is free."""

    def _place_through(self, client):
        with patch("ib_place_order.IBClient", return_value=client):
            import ib_place_order

            return ib_place_order.place_order(_sell(100))

    @patch("clients.ib_client.IB")
    def test_open_order_end_timeout_does_not_admit_the_close(self, MockIB):
        client, mock_ib = _snapshot_client(
            MockIB.return_value, positions_end=True, orders_end=False
        )

        result = self._place_through(client)

        assert result["status"] == "error", result
        assert "Connection failed" not in result.get("message", "")
        mock_ib.placeOrder.assert_not_called()

    @patch("clients.ib_client.IB")
    def test_position_end_timeout_does_not_admit_the_close(self, MockIB):
        client, mock_ib = _snapshot_client(
            MockIB.return_value, positions_end=False, orders_end=True
        )

        result = self._place_through(client)

        assert result["status"] == "error", result
        mock_ib.placeOrder.assert_not_called()

    @patch("clients.ib_client.IB")
    def test_a_finished_empty_book_still_admits_the_close(self, MockIB):
        client, mock_ib = _snapshot_client(
            MockIB.return_value, positions_end=True, orders_end=True
        )

        result = self._place_through(client)

        assert result["status"] == "ok", result
        mock_ib.placeOrder.assert_called_once()

    def test_a_resting_bag_with_no_legs_does_not_prove_the_holding_free(self):
        bag = SimpleNamespace(secType="BAG", comboLegs=[])
        client = _client(broker_shares=100, resting=[SimpleNamespace(
            contract=bag,
            order=SimpleNamespace(action="SELL"),
            orderStatus=SimpleNamespace(status="Submitted", remaining=100.0),
        )])

        result = _place(_sell(100), client)

        assert result["status"] == "error", result
        client.place_order.assert_not_called()
