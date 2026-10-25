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
    yield
    app_preferences.clear_snapshot_for_tests()


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


class TestAdmissionExemptionIsReadmittedEvenIfTheStoreMoves:
    """DS-2026-10-25-01: the transmit-time recheck re-read the store and
    skipped the broker when the re-read no longer showed a close."""

    def test_a_store_close_that_vanishes_before_transmit_still_meets_the_broker(self, monkeypatch):
        client = _client(broker_shares=0)
        empty = {**_stale_snapshot_holding(100), "positions": []}
        reads = {"connected": False}
        client.connect = MagicMock(side_effect=lambda *a, **k: reads.update(connected=True))
        monkeypatch.setattr(
            bankroll_guard,
            "_load_latest_snapshot",
            lambda: empty if reads["connected"] else _stale_snapshot_holding(100),
        )

        result = _place(_sell(100), client)

        assert result["status"] == "error", result
        client.place_order.assert_not_called()


SPXW_PUT = SimpleNamespace(symbol="SPX", secType="OPT", conId=7, right="P", strike=5000.0,
                           lastTradeDateOrContractMonth="20261120", tradingClass="SPXW")
SPX_PUT = SimpleNamespace(symbol="SPX", secType="OPT", conId=8, right="P", strike=5000.0,
                          lastTradeDateOrContractMonth="20261120", tradingClass="SPX")


class TestTheBrokerCloseMatchesTheContractNotItsCoordinates:
    """DS-2026-10-25-03: a holding in one trading class exempted an order in a
    sibling class with the same symbol, right, strike and expiry."""

    def _client(self):
        client = MagicMock()
        client.get_positions.return_value = [SimpleNamespace(contract=SPXW_PUT, position=10.0)]
        client.get_open_orders.return_value = []
        return client

    def test_a_sibling_class_holding_is_not_held(self):
        held, _ = bankroll_guard.read_broker_book(self._client(), contract=SPX_PUT)

        assert held == {}

    def test_the_held_contract_itself_is_held(self):
        held, _ = bankroll_guard.read_broker_book(self._client(), contract=SPXW_PUT)

        assert held == {("SPX", "P", 5000.0, "20261120"): 10.0}

    def test_a_combo_matches_through_its_leg_con_ids(self):
        bag = SimpleNamespace(secType="BAG", comboLegs=[SimpleNamespace(conId=8), SimpleNamespace(conId=9)])

        held, _ = bankroll_guard.read_broker_book(self._client(), contract=bag)

        assert held == {}


class TestAModifyDoesNotReserveAgainstItself:
    """DS-2026-10-25-02: the modify's own resting remainder is replaced, not added."""

    def test_the_excluded_order_reserves_nothing(self):
        own = _resting_sell(100)
        own.order = SimpleNamespace(action="SELL", permId=55, orderId=3)
        client = MagicMock()
        client.get_positions.return_value = [SimpleNamespace(contract=STOCK, position=100.0)]
        client.get_open_orders.return_value = [own]

        _, resting = bankroll_guard.read_broker_book(client, exclude=own)

        assert resting == {}


class TestAModifyMeetsTheBankrollCap:
    """DS-2026-10-25-02: a modify retransmits, so with the all-paths
    preference on it is held under the same 2.5% cap as a placement."""

    CALL = SimpleNamespace(symbol="SLV", secType="OPT", conId=21, right="C", strike=26.0,
                           lastTradeDateOrContractMonth="20261120")

    @pytest.fixture(autouse=True)
    def _fresh(self, monkeypatch):
        fresh = {**_stale_snapshot_holding(0), "last_sync": NOW.isoformat(), "positions": []}
        monkeypatch.setattr(bankroll_guard, "_load_latest_snapshot", lambda: fresh)
        monkeypatch.setitem(
            sys.modules, "trading_halt",
            type("T", (), {"is_trading_halted": staticmethod(lambda: False),
                           "get_halt_state": staticmethod(lambda: {"reason": "test"})}),
        )

    def _working(self, quantity, action="BUY"):
        trade = MagicMock(name="trade")
        trade.order.orderId = 10
        trade.order.permId = 12345
        trade.order.orderType = "LMT"
        trade.order.action = action
        trade.order.lmtPrice = 3.20
        trade.order.totalQuantity = quantity
        trade.order.clientId = 0
        trade.orderStatus.status = "Submitted"
        trade.orderStatus.remaining = float(quantity)
        trade.contract = self.CALL
        return trade

    def _client(self, working, confirmed, held=0.0):
        client = MagicMock(name="IBClient")
        client.get_open_orders.side_effect = [[working]] * 2 + [[confirmed]] * 10
        client.get_positions.return_value = (
            [SimpleNamespace(contract=self.CALL, position=held)] if held else []
        )
        client.ib.client.clientId = 0
        return client

    def _modify(self, client, new_quantity):
        from ib_order_manage import modify_order

        with pytest.raises(SystemExit) as exc:
            modify_order(client, 10, 12345, None, "127.0.0.1", 4001, new_quantity=new_quantity)
        return exc.value.code

    def test_an_over_cap_resize_never_retransmits(self, capsys):
        client = self._client(self._working(1), self._working(50))

        code = self._modify(client, 50)

        assert code == 1
        client.place_order.assert_not_called()
        assert "2.5%" in capsys.readouterr().out

    def test_a_within_cap_resize_still_retransmits(self):
        client = self._client(self._working(1), self._working(2))

        assert self._modify(client, 2) == 0
        client.place_order.assert_called_once()

    def test_a_close_is_not_reserved_against_itself(self):
        client = self._client(self._working(10, "SELL"), self._working(5, "SELL"), held=10.0)

        assert self._modify(client, 5) == 0
        client.place_order.assert_called_once()
