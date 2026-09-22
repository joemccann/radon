"""NF-1: Gate 3 (2.5% of bankroll per position) enforced server-side.

Bankroll = IB net liquidation from the last portfolio sync's account_summary.
Stale (>15 min), missing, non-finite or <=0 NLV refuses opening orders; a
close-out of a held position is never blocked by this gate.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

import bankroll_guard
from bankroll_guard import check_bankroll_admission

NOW = datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc)


def _snapshot(*, nlv=100_000.0, age_min=1.0, positions=None) -> dict:
    return {
        "last_sync": (NOW - timedelta(minutes=age_min)).isoformat(),
        "account_summary": {"net_liquidation": nlv},
        "positions": positions or [],
    }


def _vertical(quantity: int, width: float) -> dict:
    return {
        "type": "combo",
        "symbol": "AAPL",
        "action": "BUY",
        "quantity": quantity,
        "limitPrice": 1.0,
        "legs": [
            {"expiry": "20261016", "strike": 200.0, "right": "C", "action": "BUY", "ratio": 1},
            {"expiry": "20261016", "strike": 200.0 + width, "right": "C", "action": "SELL", "ratio": 1},
        ],
    }


def _long_call(quantity: int, price: float, action: str = "BUY") -> dict:
    return {
        "type": "option", "symbol": "AAPL", "action": action, "quantity": quantity,
        "limitPrice": price, "expiry": "20261016", "strike": 200.0, "right": "C",
    }


HELD_CALL = {
    "ticker": "AAPL",
    "expiry": "2026-10-16",
    "legs": [{"type": "Call", "strike": 200.0, "direction": "LONG", "contracts": 10}],
}


@pytest.fixture(autouse=True)
def _enforce_on_place_order():
    """place_order enforces Gate 3 only when the operator preference is On."""
    import app_preferences
    app_preferences.seed_snapshot_for_tests({"RADON_BANKROLL_CAP_ENFORCE_ALL_PATHS": "true"})
    yield
    app_preferences.clear_snapshot_for_tests()


def _check(params, snapshot):
    return check_bankroll_admission(params, snapshot=snapshot, now=NOW)


class TestFreshness:
    def test_stale_nlv_refuses_opening_order(self):
        refusal = _check(_long_call(1, 1.0), _snapshot(age_min=16))
        assert refusal["code"] == "BANKROLL_STALE"
        assert "15" in refusal["message"]

    @pytest.mark.parametrize("nlv", [None, "abc", float("nan"), float("inf"), 0, -5])
    def test_missing_or_invalid_nlv_refuses(self, nlv):
        refusal = _check(_long_call(1, 1.0), _snapshot(nlv=nlv))
        assert refusal["code"] == "BANKROLL_UNAVAILABLE"

    def test_no_snapshot_refuses(self):
        assert _check(_long_call(1, 1.0), None)["code"] == "BANKROLL_UNAVAILABLE"

    def test_missing_sync_timestamp_refuses(self):
        snap = _snapshot()
        del snap["last_sync"]
        assert _check(_long_call(1, 1.0), snap)["code"] == "BANKROLL_STALE"


class TestAdmission:
    def test_fresh_under_cap_admits(self):
        # 5-wide vertical, 4 lots at $1 debit: loss = 4 * 100 = $400 (0.4%)
        assert _check(_vertical(4, 5), _snapshot()) is None

    def test_exactly_at_cap_admits(self):
        # 25 lots x $1.00 x 100 = $2,500 = 2.5% of $100k
        assert _check(_long_call(25, 1.0), _snapshot()) is None

    def test_fresh_over_cap_refuses(self):
        # 26 lots x $1.00 x 100 = $2,600 > $2,500
        refusal = _check(_long_call(26, 1.0), _snapshot())
        assert refusal["code"] == "BANKROLL_CAP_EXCEEDED"
        assert "2.5%" in refusal["message"]

    def test_combo_over_cap_refuses(self):
        # 10-wide call vertical SOLD for a $1 credit: loss/unit = $1000 - $100;
        # 3 lots = $2,700 > $2,500
        bear_call = _vertical(3, 10)
        bear_call["limitPrice"] = -1.0
        bear_call["legs"] = [
            {**leg, "action": "SELL" if leg["action"] == "BUY" else "BUY"} for leg in bear_call["legs"]
        ]
        assert _check(bear_call, _snapshot())["code"] == "BANKROLL_CAP_EXCEEDED"

    def test_debit_vertical_risks_premium_not_width(self):
        # Same 10-wide vertical bought for $1: 3 lots risk $300, not $3,000.
        assert _check(_vertical(3, 10), _snapshot()) is None

    def test_undefined_risk_refuses(self):
        refusal = _check(_long_call(1, 1.0, action="SELL"), _snapshot())
        assert refusal["code"] == "BANKROLL_UNDEFINED_RISK"


class TestCloseOut:
    def test_close_out_allowed_with_stale_nlv(self):
        snap = _snapshot(age_min=600, nlv=None, positions=[HELD_CALL])
        assert _check(_long_call(10, 50.0, action="SELL"), snap) is None

    def test_selling_more_than_held_is_not_a_close_out(self):
        snap = _snapshot(age_min=600, positions=[HELD_CALL])
        assert _check(_long_call(11, 1.0, action="SELL"), snap)["code"] == "BANKROLL_STALE"

    def test_client_flag_alone_is_not_a_close_out(self):
        params = _long_call(1, 1.0)
        params["isClosing"] = True
        assert _check(params, _snapshot(age_min=600))["code"] == "BANKROLL_STALE"

    def test_combo_close_out_allowed_with_stale_nlv(self):
        held = {
            "ticker": "AAPL",
            "expiry": "2026-10-16",
            "legs": [
                {"type": "Call", "strike": 200.0, "direction": "LONG", "contracts": 2},
                {"type": "Call", "strike": 210.0, "direction": "SHORT", "contracts": 2},
            ],
        }
        close = _vertical(2, 10)
        close["action"] = "SELL"
        assert _check(close, _snapshot(age_min=600, positions=[held])) is None


class TestWire:
    """The funnel every placement path goes through refuses before connecting to IB."""

    def test_place_order_refuses_stale_before_ib(self, monkeypatch):
        import ib_place_order

        monkeypatch.setattr(bankroll_guard, "_load_latest_snapshot", lambda: _snapshot(age_min=16))
        monkeypatch.setattr(bankroll_guard, "_utcnow", lambda: NOW)
        monkeypatch.setattr(bankroll_guard, "check_bankroll_admission", check_bankroll_admission)
        client_cls = MagicMock()
        monkeypatch.setattr(ib_place_order, "IBClient", client_cls)
        result = ib_place_order.place_order(_long_call(1, 1.0))
        assert result["status"] == "error"
        assert result["code"] == "BANKROLL_STALE"
        client_cls.assert_not_called()

    def test_place_order_admits_fresh_under_cap_to_ib(self, monkeypatch):
        import ib_place_order

        monkeypatch.setattr(bankroll_guard, "_load_latest_snapshot", lambda: _snapshot())
        monkeypatch.setattr(bankroll_guard, "_utcnow", lambda: NOW)
        monkeypatch.setattr(bankroll_guard, "check_bankroll_admission", check_bankroll_admission)
        client_cls = MagicMock()
        client_cls.return_value.connect.side_effect = RuntimeError("stop here")
        monkeypatch.setattr(ib_place_order, "IBClient", client_cls)
        result = ib_place_order.place_order(_long_call(1, 1.0))
        client_cls.return_value.connect.assert_called_once()
        assert result.get("code") != "BANKROLL_STALE"


class TestRepeatedContractCloseOut:
    @pytest.mark.parametrize("envelope,leg_action,direction,price", [
        ("BUY", "SELL", "LONG", 1.0), ("SELL", "BUY", "LONG", 1.0),
        ("BUY", "BUY", "SHORT", 10.0), ("SELL", "SELL", "SHORT", -10.0),
    ])
    @pytest.mark.parametrize("quantity,ratios", [(10, (1, 1)), (4, (2, 1))])
    @pytest.mark.parametrize("age_min,code", [(600, "BANKROLL_STALE"), (1, "BANKROLL_CAP_EXCEEDED")])
    def test_repeated_legs_refuse_before_ib(self, monkeypatch, envelope, leg_action, direction, price, quantity, ratios, age_min, code):
        import ib_place_order
        held = {"ticker": "AAPL", "expiry": "2026-10-16", "legs": [
            {"type": "Put", "strike": 20, "direction": direction, "contracts": 10},
        ]}
        params = {"type": "combo", "symbol": "AAPL", "action": envelope,
                  "quantity": quantity, "limitPrice": price, "legs": [
            {"expiry": "20261016", "strike": 20, "right": "P", "action": leg_action, "ratio": r}
            for r in ratios
        ]}
        monkeypatch.setattr(bankroll_guard, "_load_latest_snapshot", lambda: _snapshot(age_min=age_min, positions=[held]))
        monkeypatch.setattr(bankroll_guard, "_utcnow", lambda: NOW)
        monkeypatch.setattr(bankroll_guard, "check_bankroll_admission", check_bankroll_admission)
        client = MagicMock()
        client.return_value.connect.side_effect = RuntimeError("fake transport must not connect")
        monkeypatch.setattr(ib_place_order, "IBClient", client)
        result = ib_place_order.place_order(params)
        assert result.get("code") == code, result
        client.assert_not_called()

    @pytest.mark.parametrize("quantity,ratios", [(5, (1, 1)), (2, (2, 1))])
    @pytest.mark.parametrize("direction", ["LONG", "SHORT"])
    def test_combined_reduction_admitted(self, quantity, ratios, direction):
        params = _vertical(quantity, 10)
        action = "SELL" if direction == "LONG" else "BUY"
        params["legs"] = [dict(params["legs"][0], action=action, ratio=r) for r in ratios]
        held = {**HELD_CALL, "legs": [dict(HELD_CALL["legs"][0], direction=direction)]}
        assert _check(params, _snapshot(age_min=600, nlv=None, positions=[held])) is None

    @pytest.mark.parametrize("value", [float("nan"), float("inf")])
    def test_nonfinite_quantity_is_not_a_close(self, value):
        assert not bankroll_guard.is_close_out(_long_call(value, 1, "SELL"), _snapshot(positions=[HELD_CALL]))


@pytest.mark.parametrize("envelope,price", [("BUY", 1.0), ("SELL", -1.0)])
def test_long_combo_debit_at_bankroll_cap_remains_admitted(envelope, price):
    params = _vertical(25, 10)
    params.update(action=envelope, limitPrice=price)
    action = "BUY" if envelope == "BUY" else "SELL"
    params["legs"] = [dict(leg, action=action) for leg in params["legs"]]
    assert _check(params, _snapshot()) is None
