"""DS-2026-10-23-02: the futures notional cap must not read the request.

``order_notional`` priced a future with whatever ``multiplier`` the request
body carried, and the transport backstop that exists so a new call site
cannot go around typed every non-STK, non-BAG contract as an option. A caller
that composes its own placement body could therefore understate the
multiplier and place a futures order whose true notional is many times the
cap. The trusted multiplier is the one on the qualified broker contract.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import ib_place_order  # noqa: E402
from clients.ib_client import IBClient, IBOrderError  # noqa: E402


def _qualified_future(multiplier="1000"):
    return SimpleNamespace(
        symbol="VIX",
        secType="FUT",
        lastTradeDateOrContractMonth="20261118",
        exchange="CFE",
        currency="USD",
        conId=777000111,
        multiplier=multiplier,
    )


class TestPlacementBoundary:
    def test_understated_multiplier_is_refused(self):
        params = {"symbol": "VIX", "type": "future", "multiplier": 1}
        mismatch = ib_place_order._bind_future_multiplier(params, _qualified_future())
        assert mismatch is not None
        assert "multiplier" in mismatch

    def test_matching_multiplier_is_accepted_and_bound_to_the_contract(self):
        params = {"symbol": "VIX", "type": "future", "multiplier": 1000}
        assert ib_place_order._bind_future_multiplier(params, _qualified_future()) is None
        assert params["multiplier"] == 1000.0

    def test_absent_multiplier_is_taken_from_the_qualified_contract(self):
        params = {"symbol": "VIX", "type": "future"}
        assert ib_place_order._bind_future_multiplier(params, _qualified_future()) is None
        assert params["multiplier"] == 1000.0

    def test_unusable_contract_multiplier_fails_closed(self):
        for value in (None, "", "0", "nope"):
            params = {"symbol": "VIX", "type": "future", "multiplier": 1000}
            mismatch = ib_place_order._bind_future_multiplier(
                params, _qualified_future(multiplier=value)
            )
            assert mismatch is not None, value


class TestTransportBackstop:
    """The last funnel must price a FUT as a future, not as a 100x option."""

    @patch("clients.ib_client.IB")
    def test_oversized_future_notional_is_refused_at_the_funnel(self, MockIB, monkeypatch):
        monkeypatch.setenv("RADON_MAX_ORDER_NOTIONAL", "250000")
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True
        client = IBClient()
        client.connect(client_id=2)

        order = SimpleNamespace(
            action="BUY", totalQuantity=100, orderType="LMT", lmtPrice=20.0
        )
        # 100 x $20 x 1000 = $2,000,000 of true notional.
        with pytest.raises(IBOrderError):
            client.place_order(_qualified_future(), order)
        mock_ib.placeOrder.assert_not_called()

    @patch("clients.ib_client.IB")
    def test_in_bounds_future_still_places(self, MockIB, monkeypatch):
        monkeypatch.setenv("RADON_MAX_ORDER_NOTIONAL", "250000")
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True
        mock_ib.placeOrder.return_value = MagicMock()
        client = IBClient()
        client.connect(client_id=2)

        contract = _qualified_future()
        # 10 x $20 x 1000 = $200,000, inside the cap.
        order = SimpleNamespace(
            action="BUY", totalQuantity=10, orderType="LMT", lmtPrice=20.0
        )
        client.place_order(contract, order)
        mock_ib.placeOrder.assert_called_once_with(contract, order)

    @patch("clients.ib_client.IB")
    def test_future_with_no_contract_multiplier_fails_closed(self, MockIB):
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True
        client = IBClient()
        client.connect(client_id=2)

        order = SimpleNamespace(
            action="BUY", totalQuantity=1, orderType="LMT", lmtPrice=20.0
        )
        with pytest.raises(IBOrderError):
            client.place_order(_qualified_future(multiplier=None), order)
        mock_ib.placeOrder.assert_not_called()


class TestPlaceOrderFunnel:
    """``ib_place_order.place_order`` is the /orders/place subprocess.

    Binding the helper and the transport backstop does not run this funnel.
    Deleting the post-qualify bind leaves both of those green and transmits
    a future whose request multiplier is 1 against a 1000-multiplier contract.
    """

    def test_understated_request_multiplier_never_reaches_the_broker(self, monkeypatch):
        import eventkit

        qualified = _qualified_future("1000")
        trade = MagicMock()
        trade.order = MagicMock(orderId=99, permId=12345)
        trade.orderStatus = MagicMock(status="Submitted", whyHeld="")
        trade.log = []
        client = MagicMock()
        client._ib = MagicMock()
        client._ib.errorEvent = eventkit.Event("errorEvent")
        client.qualify_contracts.return_value = [qualified]
        client.place_order.return_value = trade
        monkeypatch.setattr(ib_place_order, "IBClient", lambda: client)
        monkeypatch.setattr(
            "clients.contract_resolver.resolve_future_contract",
            lambda symbol, expiry: qualified,
        )
        monkeypatch.setattr("kelly_guard.check_kelly_ticket", lambda params: None)

        result = ib_place_order.place_order({
            "type": "future",
            "symbol": "VIX",
            "action": "BUY",
            "quantity": 10,
            "limitPrice": 20.0,
            "expiry": "20261118",
            "multiplier": 1,
        })

        assert result["status"] == "error", result
        assert "multiplier" in result["message"]
        client.place_order.assert_not_called()
