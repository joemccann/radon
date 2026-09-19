"""D11 order-path Kelly guard. Default off; warn-only when armed."""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import eventkit
import pytest

from kelly import kelly_config
from kelly_guard import check_kelly_ticket


def _combo(*, quantity: int, width: float, debit: bool = True) -> dict:
    low, high = 100.0, 100.0 + width
    return {
        "type": "combo",
        "symbol": "AAPL",
        "action": "BUY",
        "quantity": quantity,
        "limitPrice": 5.0 if debit else -5.0,
        "tif": "DAY",
        "legs": [
            {"expiry": "20260918", "strike": low, "right": "PUT", "action": "BUY", "ratio": 1},
            {"expiry": "20260918", "strike": high, "right": "PUT", "action": "SELL", "ratio": 1},
        ],
    }


def _armed_combo_client():
    trade = MagicMock()
    trade.order = MagicMock(orderId=99, permId=12345)
    trade.orderStatus = MagicMock(status="Submitted", whyHeld="", filled=0)
    trade.log = []
    client = MagicMock()
    client._ib = MagicMock(errorEvent=eventkit.Event("errorEvent"))
    client.place_order = MagicMock(return_value=trade)
    client.qualify_contracts = MagicMock(
        return_value=[MagicMock(conId=1), MagicMock(conId=2)]
    )
    return client


class TestD11KellyGuard:
    def test_d11_flag_off_allows_oversized(self, monkeypatch, caplog):
        monkeypatch.delenv("RADON_KELLY_ENFORCE_ORDERS", raising=False)
        with caplog.at_level(logging.WARNING, logger="kelly_guard"):
            # width 30 * 100 * 1 = 3000 = 3% of 100k
            assert check_kelly_ticket(_combo(quantity=1, width=30), bankroll=100_000) is None
        assert caplog.records == []

    def test_d11_opening_combo_3pct_warns_when_armed(self, monkeypatch, caplog):
        monkeypatch.setenv("RADON_KELLY_ENFORCE_ORDERS", "1")
        with caplog.at_level(logging.WARNING, logger="kelly_guard"):
            warning = check_kelly_ticket(_combo(quantity=1, width=30), bankroll=100_000)
        assert warning is not None
        assert warning["code"] == "KELLY_CAP_EXCEEDED"
        assert warning["loss"] == pytest.approx(3000.0)
        assert warning["bankroll"] == pytest.approx(100_000.0)
        assert warning["pct"] == pytest.approx(3.0)
        assert "KELLY_CAP_EXCEEDED" in caplog.text
        assert "3.00" in caplog.text or "3.0" in caplog.text
        assert "100000" in caplog.text or "100000.00" in caplog.text

    def test_d11_closing_same_ticket_passes(self, monkeypatch, caplog):
        monkeypatch.setenv("RADON_KELLY_ENFORCE_ORDERS", "1")
        params = _combo(quantity=1, width=30)
        params["isClosing"] = True
        with caplog.at_level(logging.WARNING, logger="kelly_guard"):
            assert check_kelly_ticket(params, bankroll=100_000) is None
        assert caplog.records == []

    def test_d11_unknown_bankroll_warns(self, monkeypatch, caplog):
        monkeypatch.setenv("RADON_KELLY_ENFORCE_ORDERS", "1")
        with patch("kelly_guard._portfolio_bankroll", return_value=None):
            with caplog.at_level(logging.WARNING, logger="kelly_guard"):
                warning = check_kelly_ticket(_combo(quantity=1, width=5), bankroll=None)
        assert warning is not None
        assert warning["code"] == "KELLY_BANKROLL_UNKNOWN"
        assert "KELLY_BANKROLL_UNKNOWN" in caplog.text

    def test_d11_stock_order_passes(self, monkeypatch, caplog):
        monkeypatch.setenv("RADON_KELLY_ENFORCE_ORDERS", "1")
        stock = {
            "type": "stock",
            "symbol": "AAPL",
            "action": "BUY",
            "quantity": 100,
            "limitPrice": 214.5,
        }
        with caplog.at_level(logging.WARNING, logger="kelly_guard"):
            assert check_kelly_ticket(stock, bankroll=100_000) is None
        assert caplog.records == []

    def test_d11_enforce_mode_defaults_to_warn(self, monkeypatch):
        monkeypatch.setenv("RADON_KELLY_ENFORCE_ORDERS", "1")
        monkeypatch.delenv("RADON_KELLY_ENFORCE_MODE", raising=False)
        cfg = kelly_config()
        assert cfg["enforce_orders"] is True
        assert cfg["enforce_mode"] == "warn"

    def test_d11_enforce_mode_invalid_falls_back_to_warn(self, monkeypatch):
        monkeypatch.setenv("RADON_KELLY_ENFORCE_ORDERS", "1")
        monkeypatch.setenv("RADON_KELLY_ENFORCE_MODE", "refuse")
        assert kelly_config()["enforce_mode"] == "warn"


class TestD11PlaceOrderWire:
    def test_d11_place_order_warns_and_calls_ib(self, monkeypatch, caplog):
        monkeypatch.setenv("RADON_KELLY_ENFORCE_ORDERS", "1")
        monkeypatch.delenv("RADON_KELLY_ENFORCE_MODE", raising=False)
        params = _combo(quantity=1, width=30)
        client = _armed_combo_client()
        with patch("ib_place_order.IBClient", return_value=client) as ib_cls, \
             patch("kelly_guard._portfolio_bankroll", return_value=100_000), \
             patch("clients.contract_resolver.resolve_option_contract", return_value=MagicMock()), \
             patch("ib_place_order.LimitOrder", return_value=MagicMock()), \
             caplog.at_level(logging.WARNING, logger="kelly_guard"):
            import ib_place_order
            result = ib_place_order.place_order(params)
        assert result.get("code") != "KELLY_CAP_EXCEEDED"
        assert result.get("status") != "error" or "Kelly" not in str(result.get("message", ""))
        assert result.get("status") == "ok"
        assert result.get("kelly_warning", {}).get("code") == "KELLY_CAP_EXCEEDED"
        assert result["kelly_warning"]["loss"] == pytest.approx(3000.0)
        assert result["kelly_warning"]["bankroll"] == pytest.approx(100_000.0)
        assert result["kelly_warning"]["pct"] == pytest.approx(3.0)
        assert "KELLY_CAP_EXCEEDED" in caplog.text
        ib_cls.assert_called()
        client.place_order.assert_called()

    def test_d11_place_order_blocks_only_when_mode_block(self, monkeypatch):
        monkeypatch.setenv("RADON_KELLY_ENFORCE_ORDERS", "1")
        monkeypatch.setenv("RADON_KELLY_ENFORCE_MODE", "block")
        params = _combo(quantity=1, width=30)
        with patch("ib_place_order.IBClient") as ib_cls, \
             patch("kelly_guard._portfolio_bankroll", return_value=100_000), \
             patch("ib_place_order.Stock", return_value=MagicMock()), \
             patch("ib_place_order.LimitOrder", return_value=MagicMock()):
            import ib_place_order
            result = ib_place_order.place_order(params)
        assert result.get("status") == "error"
        assert result.get("code") == "KELLY_CAP_EXCEEDED"
        ib_cls.assert_not_called()
