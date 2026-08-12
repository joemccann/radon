"""STP / STP LMT constructors for ib_place_order.py."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import eventkit

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


def _make_trade(status: str = "Submitted", perm_id: int = 12345) -> MagicMock:
    trade = MagicMock()
    trade.order = MagicMock(orderId=99, permId=perm_id)
    trade.orderStatus = MagicMock(status=status, whyHeld="", filled=0, remaining=100, avgFillPrice=0)
    trade.log = []
    return trade


def _make_client(trade: MagicMock) -> MagicMock:
    client = MagicMock()
    ib_mock = MagicMock()
    ib_mock.errorEvent = eventkit.Event("errorEvent")
    client._ib = ib_mock
    client.place_order = MagicMock(return_value=trade)
    client.qualify_contracts = MagicMock(return_value=[MagicMock(conId=123456)])
    client.sleep = MagicMock()
    client.disconnect = MagicMock()
    return client


def _invoke(params: dict, client: MagicMock):
    with patch("ib_place_order.IBClient", return_value=client), \
         patch("ib_place_order.Stock", return_value=MagicMock()), \
         patch("ib_place_order.LimitOrder", return_value=MagicMock()) as limit_cls, \
         patch("ib_place_order.StopOrder", return_value=MagicMock()) as stop_cls, \
         patch("ib_place_order.StopLimitOrder", return_value=MagicMock()) as stop_lmt_cls:
        import ib_place_order
        result = ib_place_order.place_order(params)
        return result, limit_cls, stop_cls, stop_lmt_cls


_LMT = {
    "type": "stock",
    "symbol": "AAPL",
    "action": "SELL",
    "quantity": 100,
    "limitPrice": 170.0,
    "tif": "GTC",
    "outsideRth": False,
}


class TestStopMarketConstructor:
    def test_stp_builds_stop_order_without_limit_price(self):
        params = {
            "type": "stock",
            "symbol": "AAPL",
            "action": "SELL",
            "quantity": 100,
            "orderType": "STP",
            "stopPrice": 170.0,
            "tif": "GTC",
            "outsideRth": False,
        }
        result, limit_cls, stop_cls, stop_lmt_cls = _invoke(params, _make_client(_make_trade()))
        assert result["status"] == "ok", result
        stop_cls.assert_called_once()
        limit_cls.assert_not_called()
        stop_lmt_cls.assert_not_called()
        kwargs = stop_cls.call_args.kwargs
        assert kwargs["action"] == "SELL"
        assert kwargs["totalQuantity"] == 100
        assert kwargs["stopPrice"] == 170.0
        assert kwargs["tif"] == "GTC"

    def test_stp_missing_stop_price_refused_before_ib(self):
        params = {
            "type": "stock",
            "symbol": "AAPL",
            "action": "SELL",
            "quantity": 100,
            "orderType": "STP",
            "tif": "GTC",
        }
        with patch("ib_place_order.IBClient") as ib_cls, \
             patch("ib_place_order.Stock", return_value=MagicMock()), \
             patch("ib_place_order.LimitOrder", return_value=MagicMock()), \
             patch("ib_place_order.StopOrder", return_value=MagicMock()), \
             patch("ib_place_order.StopLimitOrder", return_value=MagicMock()):
            import ib_place_order
            result = ib_place_order.place_order(params)
        assert result["status"] == "error"
        assert "stop" in result["message"].lower()
        ib_cls.assert_not_called()

    def test_stp_zero_stop_price_refused(self):
        params = {
            "type": "stock",
            "symbol": "AAPL",
            "action": "SELL",
            "quantity": 100,
            "orderType": "STP",
            "stopPrice": 0,
            "tif": "GTC",
        }
        with patch("ib_place_order.IBClient") as ib_cls, \
             patch("ib_place_order.Stock", return_value=MagicMock()), \
             patch("ib_place_order.LimitOrder", return_value=MagicMock()), \
             patch("ib_place_order.StopOrder", return_value=MagicMock()), \
             patch("ib_place_order.StopLimitOrder", return_value=MagicMock()):
            import ib_place_order
            result = ib_place_order.place_order(params)
        assert result["status"] == "error"
        assert "stop" in result["message"].lower()
        ib_cls.assert_not_called()

    def test_stp_success_message_names_stop(self):
        params = {
            "type": "stock",
            "symbol": "AAPL",
            "action": "SELL",
            "quantity": 100,
            "orderType": "STP",
            "stopPrice": 170.0,
            "tif": "GTC",
        }
        result, _, _, _ = _invoke(params, _make_client(_make_trade()))
        assert result["status"] == "ok"
        assert "170" in result["message"]
        assert "stop" in result["message"].lower()


class TestStopLimitConstructor:
    def test_stp_lmt_builds_stop_limit_order(self):
        params = {
            "type": "stock",
            "symbol": "AAPL",
            "action": "SELL",
            "quantity": 100,
            "orderType": "STP LMT",
            "stopPrice": 170.0,
            "limitPrice": 168.0,
            "tif": "GTC",
        }
        result, limit_cls, stop_cls, stop_lmt_cls = _invoke(params, _make_client(_make_trade()))
        assert result["status"] == "ok", result
        stop_lmt_cls.assert_called_once()
        limit_cls.assert_not_called()
        stop_cls.assert_not_called()
        kwargs = stop_lmt_cls.call_args.kwargs
        assert kwargs["lmtPrice"] == 168.0
        assert kwargs["stopPrice"] == 170.0

    def test_combo_stp_lmt_rejected(self):
        params = {
            "type": "combo",
            "symbol": "AAPL",
            "action": "SELL",
            "quantity": 1,
            "orderType": "STP LMT",
            "stopPrice": 1.5,
            "limitPrice": 1.4,
            "tif": "GTC",
            "legs": [
                {"expiry": "20260417", "strike": 100, "right": "C", "action": "BUY", "ratio": 1},
                {"expiry": "20260417", "strike": 110, "right": "C", "action": "SELL", "ratio": 1},
            ],
        }
        with patch("ib_place_order.IBClient") as ib_cls, \
             patch("ib_place_order.Stock", return_value=MagicMock()), \
             patch("ib_place_order.LimitOrder", return_value=MagicMock()), \
             patch("ib_place_order.StopOrder", return_value=MagicMock()), \
             patch("ib_place_order.StopLimitOrder", return_value=MagicMock()):
            import ib_place_order
            result = ib_place_order.place_order(params)
        assert result["status"] == "error"
        assert "combo" in result["message"].lower() or "bag" in result["message"].lower()
        ib_cls.assert_not_called()


class TestLimitUnchanged:
    def test_default_still_limit_order(self):
        result, limit_cls, stop_cls, stop_lmt_cls = _invoke(_LMT, _make_client(_make_trade()))
        assert result["status"] == "ok", result
        limit_cls.assert_called_once()
        stop_cls.assert_not_called()
        stop_lmt_cls.assert_not_called()
