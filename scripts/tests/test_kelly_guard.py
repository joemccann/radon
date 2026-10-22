"""D11 order-path Kelly guard. Default off; warn-only when armed."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
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


_NOW = datetime(2026, 10, 22, 15, 0, tzinfo=timezone.utc)


def _held_spread_snapshot(*, last_sync: str | None = None) -> dict:
    """Book the _combo(width=30) BUY ticket closes: short 100P, long 130P."""
    return {
        "last_sync": last_sync or _NOW.isoformat(),
        "positions": [{
            "ticker": "AAPL", "structure_type": "Put Spread",
            "legs": [
                {"type": "Put", "direction": "SHORT", "contracts": 1, "strike": 100.0, "expiry": "2026-09-18"},
                {"type": "Put", "direction": "LONG", "contracts": 1, "strike": 130.0, "expiry": "2026-09-18"},
            ],
        }],
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

    def test_d11_verified_reducing_close_passes(self, monkeypatch, caplog):
        monkeypatch.setenv("RADON_KELLY_ENFORCE_ORDERS", "1")
        params = _combo(quantity=1, width=30)
        with patch("bankroll_guard._load_latest_snapshot", return_value=_held_spread_snapshot()), \
             patch("bankroll_guard._utcnow", return_value=_NOW), \
             caplog.at_level(logging.WARNING, logger="kelly_guard"):
            assert check_kelly_ticket(params, bankroll=100_000) is None
        assert caplog.records == []

    @pytest.mark.parametrize("flag", ["isClosing", "is_closing", "closeOut", "reducing"])
    def test_d11_client_close_flag_alone_is_not_exempt(self, monkeypatch, flag):
        monkeypatch.setenv("RADON_KELLY_ENFORCE_ORDERS", "1")
        params = _combo(quantity=1, width=30)
        params[flag] = True
        with patch("bankroll_guard._load_latest_snapshot", return_value={"positions": [], "last_sync": _NOW.isoformat()}), \
             patch("bankroll_guard._utcnow", return_value=_NOW):
            warning = check_kelly_ticket(params, bankroll=100_000)
        assert warning is not None
        assert warning["code"] == "KELLY_CAP_EXCEEDED"

    def test_d11_reducing_close_on_stale_snapshot_is_not_exempt(self, monkeypatch):
        monkeypatch.setenv("RADON_KELLY_ENFORCE_ORDERS", "1")
        params = _combo(quantity=1, width=30)
        params["isClosing"] = True
        stale = _held_spread_snapshot(last_sync=(_NOW - timedelta(hours=1)).isoformat())
        with patch("bankroll_guard._load_latest_snapshot", return_value=stale), \
             patch("bankroll_guard._utcnow", return_value=_NOW):
            warning = check_kelly_ticket(params, bankroll=100_000)
        assert warning is not None
        assert warning["code"] == "KELLY_CAP_EXCEEDED"

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

    def test_d11_place_order_blocks_flagged_opening_order(self, monkeypatch):
        monkeypatch.setenv("RADON_KELLY_ENFORCE_ORDERS", "1")
        monkeypatch.setenv("RADON_KELLY_ENFORCE_MODE", "block")
        params = _combo(quantity=1, width=30)
        params["isClosing"] = True
        with patch("ib_place_order.IBClient") as ib_cls, \
             patch("kelly_guard._portfolio_bankroll", return_value=100_000), \
             patch("bankroll_guard._load_latest_snapshot", return_value=None):
            import ib_place_order
            result = ib_place_order.place_order(params)
        assert result.get("status") == "error"
        assert result.get("code") == "KELLY_CAP_EXCEEDED"
        ib_cls.assert_not_called()

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


def _option(price=50.0):
    return {
        "type": "option", "symbol": "AAPL", "action": "BUY",
        "quantity": 1, "limitPrice": price, "tif": "DAY",
        "expiry": "20261218", "strike": 100, "right": "C",
    }


@pytest.mark.parametrize("price,oversized", [(24.99, False), (25.0, False), (25.01, True), (50.0, True)])
def test_single_option_cap_boundary(monkeypatch, price, oversized):
    monkeypatch.setenv("RADON_KELLY_ENFORCE_ORDERS", "1")
    warning = check_kelly_ticket(_option(price), bankroll=100_000)
    if oversized:
        assert warning["code"] == "KELLY_CAP_EXCEEDED"
        assert warning["loss"] == pytest.approx(price * 100)
        assert warning["pct"] == pytest.approx(price / 10)
    else:
        assert warning is None


def test_single_option_unarmed_is_exempt(monkeypatch, caplog):
    monkeypatch.setenv("RADON_KELLY_ENFORCE_ORDERS", "0")
    assert check_kelly_ticket(_option(), bankroll=100_000) is None
    assert not caplog.records


def test_single_option_flagged_open_is_not_exempt(monkeypatch):
    monkeypatch.setenv("RADON_KELLY_ENFORCE_ORDERS", "1")
    ticket = _option()
    ticket["isClosing"] = True
    with patch("bankroll_guard._load_latest_snapshot", return_value=None):
        warning = check_kelly_ticket(ticket, bankroll=100_000)
    assert warning["code"] == "KELLY_CAP_EXCEEDED"


def test_single_option_verified_sell_to_close_is_exempt(monkeypatch, caplog):
    monkeypatch.setenv("RADON_KELLY_ENFORCE_ORDERS", "1")
    ticket = _option()
    ticket["action"] = "SELL"
    snapshot = {
        "last_sync": _NOW.isoformat(),
        "positions": [{
            "ticker": "AAPL", "structure_type": "Long Call",
            "legs": [{"type": "Call", "direction": "LONG", "contracts": 1,
                      "strike": 100, "expiry": "2026-12-18"}],
        }],
    }
    with patch("bankroll_guard._load_latest_snapshot", return_value=snapshot), \
         patch("bankroll_guard._utcnow", return_value=_NOW), \
         caplog.at_level(logging.WARNING, logger="kelly_guard"):
        assert check_kelly_ticket(ticket, bankroll=100_000) is None
    assert not caplog.records


@pytest.mark.parametrize("mode", ["warn", "block"])
def test_single_option_order_wire(monkeypatch, mode):
    monkeypatch.setenv("RADON_KELLY_ENFORCE_ORDERS", "1")
    monkeypatch.setenv("RADON_KELLY_ENFORCE_MODE", mode)
    client = _armed_combo_client()
    with patch("ib_place_order.IBClient", return_value=client) as ib_cls, \
         patch("kelly_guard._portfolio_bankroll", return_value=100_000), \
         patch("clients.contract_resolver.resolve_option_contract", return_value=MagicMock()), \
         patch("ib_place_order.LimitOrder", return_value=MagicMock()):
        import ib_place_order
        result = ib_place_order.place_order(_option())
    if mode == "block":
        assert result["status"] == "error"
        assert result["code"] == "KELLY_CAP_EXCEEDED"
        ib_cls.assert_not_called()
        client.place_order.assert_not_called()
    else:
        assert result["status"] == "ok"
        assert result["kelly_warning"]["code"] == "KELLY_CAP_EXCEEDED"
        assert result["kelly_warning"]["loss"] == pytest.approx(5000)
        client.place_order.assert_called_once()


_CALENDAR_DEBIT = {
    "type": "combo",
    "symbol": "SPX",
    "action": "BUY",
    "quantity": 400,
    "limitPrice": 5.00,
    "legs": [
        {"expiry": "20271217", "strike": 5000, "right": "C", "action": "BUY", "ratio": 1},
        {"expiry": "20261120", "strike": 5000, "right": "C", "action": "SELL", "ratio": 1},
    ],
}

_CALENDAR_CREDIT = {
    "type": "combo",
    "symbol": "SPX",
    "action": "BUY",
    "quantity": 400,
    "limitPrice": -20.00,
    "legs": [
        {"expiry": "20271217", "strike": 5000, "right": "C", "action": "BUY", "ratio": 1},
        {"expiry": "20261120", "strike": 4999, "right": "C", "action": "SELL", "ratio": 1},
    ],
}


def test_both_gate3_guards_floor_a_debit_combo_at_the_premium_paid():
    """The width/strike margin proxy is zero for a long calendar, but the
    debit paid is fully at risk. Both Gate-3 implementations must price it."""
    import bankroll_guard
    import kelly_guard
    import order_limits

    assert order_limits.combo_max_loss(_CALENDAR_DEBIT) == pytest.approx(0.0)
    assert bankroll_guard.order_max_loss(_CALENDAR_DEBIT) == pytest.approx(200_000.0)
    assert kelly_guard._worst_case_loss(_CALENDAR_DEBIT) == pytest.approx(200_000.0)


def test_a_net_credit_combo_gets_no_spurious_floor_in_either_guard():
    """Nothing was paid, so the premium is not a floor. A mis-signed floor
    would price this $20-credit order at $800,000."""
    import bankroll_guard
    import kelly_guard
    import order_limits

    assert order_limits.combo_max_loss(_CALENDAR_CREDIT) == pytest.approx(0.0)
    assert bankroll_guard.order_max_loss(_CALENDAR_CREDIT) == pytest.approx(0.0)
    assert kelly_guard._worst_case_loss(_CALENDAR_CREDIT) == pytest.approx(0.0)
