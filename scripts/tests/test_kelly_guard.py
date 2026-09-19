"""D11 order-path Kelly guard. Default off; wire-tested when armed."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

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


class TestD11KellyGuard:
    def test_d11_flag_off_allows_oversized(self, monkeypatch):
        monkeypatch.delenv("RADON_KELLY_ENFORCE_ORDERS", raising=False)
        # width 30 * 100 * 1 = 3000 = 3% of 100k
        assert check_kelly_ticket(_combo(quantity=1, width=30), bankroll=100_000) is None

    def test_d11_opening_combo_3pct_refused_when_armed(self, monkeypatch):
        monkeypatch.setenv("RADON_KELLY_ENFORCE_ORDERS", "1")
        refusal = check_kelly_ticket(_combo(quantity=1, width=30), bankroll=100_000)
        assert refusal is not None
        assert refusal["code"] == "KELLY_CAP_EXCEEDED"

    def test_d11_closing_same_ticket_passes(self, monkeypatch):
        monkeypatch.setenv("RADON_KELLY_ENFORCE_ORDERS", "1")
        params = _combo(quantity=1, width=30)
        params["isClosing"] = True
        assert check_kelly_ticket(params, bankroll=100_000) is None

    def test_d11_unknown_bankroll_refuses(self, monkeypatch):
        monkeypatch.setenv("RADON_KELLY_ENFORCE_ORDERS", "1")
        with patch("kelly_guard._portfolio_bankroll", return_value=None):
            refusal = check_kelly_ticket(_combo(quantity=1, width=5), bankroll=None)
        assert refusal is not None
        assert refusal["code"] == "KELLY_BANKROLL_UNKNOWN"

    def test_d11_stock_order_passes(self, monkeypatch):
        monkeypatch.setenv("RADON_KELLY_ENFORCE_ORDERS", "1")
        stock = {
            "type": "stock",
            "symbol": "AAPL",
            "action": "BUY",
            "quantity": 100,
            "limitPrice": 214.5,
        }
        assert check_kelly_ticket(stock, bankroll=100_000) is None


class TestD11PlaceOrderWire:
    def test_d11_place_order_refuses_before_ib(self, monkeypatch):
        monkeypatch.setenv("RADON_KELLY_ENFORCE_ORDERS", "1")
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
