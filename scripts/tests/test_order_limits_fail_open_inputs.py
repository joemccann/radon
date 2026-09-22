#!/usr/bin/env python3
"""RC-D1/D2/D4 — fail-open input handling in the order limit layer.

Three holes in one family: a futures order was notional-bounded with the
option multiplier (10x under-counted for a 1000-multiplier contract); a BAG
carrying a stock leg made `combo_max_loss` return None so the loss cap was
silently skipped; and NaN/unparseable quantity/price coerced to 0/None and
skipped every dollar bound. Bad input must refuse, never skip.
"""

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import order_limits


class TestFuturesMultiplier:
    def test_future_notional_uses_the_supplied_multiplier(self, monkeypatch):
        """10 lots × $30 × 1000 = $300k > default $250k cap."""
        violation = order_limits.check_order_limits({
            "type": "future", "symbol": "VIX", "action": "BUY",
            "quantity": 10, "limitPrice": 30.0, "multiplier": 1000,
        })
        assert violation is not None
        assert violation["code"] == "ORDER_NOTIONAL_LIMIT"

    def test_future_within_cap_is_allowed(self):
        """5 lots × $20 × 1000 = $100k — under the default cap."""
        violation = order_limits.check_order_limits({
            "type": "future", "symbol": "VIX", "action": "BUY",
            "quantity": 5, "limitPrice": 20.0, "multiplier": 1000,
        })
        assert violation is None

    @pytest.mark.parametrize("multiplier", [None, 0, -5, "abc", float("nan")])
    def test_future_without_a_valid_multiplier_fails_closed(self, multiplier):
        params = {
            "type": "future", "symbol": "VIX", "action": "BUY",
            "quantity": 1, "limitPrice": 20.0,
        }
        if multiplier is not None:
            params["multiplier"] = multiplier
        violation = order_limits.check_order_limits(params)
        assert violation is not None, "future with no usable multiplier was not refused"
        assert violation["code"] == "ORDER_FUTURE_MULTIPLIER"


class TestStockLegCombo:
    def test_stock_leg_bag_still_prices_the_short_call(self, monkeypatch):
        """500-lot buy-write shape: the SELL CALL's proxy risk must reach the
        loss cap instead of the STK leg nulling the whole computation."""
        monkeypatch.setenv("RADON_MAX_COMBO_LOSS_DOLLARS", "1000000")
        violation = order_limits.check_order_limits({
            "type": "combo", "symbol": "NVDA", "action": "BUY",
            "quantity": 500, "limitPrice": 1.0,
            "legs": [
                {"sec_type": "STK", "action": "BUY", "ratio": 1},
                {"strike": 200.0, "right": "C", "action": "SELL", "ratio": 1,
                 "expiry": "20261218"},
            ],
        })
        assert violation is not None, (
            "a stock leg silenced the combo max-loss gate"
        )
        assert violation["code"] == "ORDER_MAX_LOSS_LIMIT"

    def test_combo_max_loss_is_not_none_for_a_mixed_bag(self):
        loss = order_limits.combo_max_loss({
            "type": "combo", "action": "BUY", "quantity": 1, "limitPrice": 1.0,
            "legs": [
                {"sec_type": "STK", "action": "BUY", "ratio": 1},
                {"strike": 200.0, "right": "C", "action": "SELL", "ratio": 1},
            ],
        })
        assert loss is not None and loss > 0


class TestUnparseableInputsRefuse:
    @pytest.mark.parametrize("params", [
        {"type": "option", "quantity": float("nan"), "limitPrice": 1.0},
        {"type": "option", "quantity": "abc", "limitPrice": 1.0},
        {"type": "option", "quantity": 10, "limitPrice": "nan"},
        {"type": "option", "quantity": 10, "limitPrice": float("inf")},
        {"type": "stock", "quantity": "1e999", "limitPrice": 5.0},
        {"type": "option", "quantity": 10, "orderType": "STP",
         "stopPrice": float("nan")},
    ])
    def test_non_finite_or_unparseable_is_refused(self, params):
        params = {"symbol": "GOOG", "action": "BUY", **params}
        violation = order_limits.check_order_limits(params)
        assert violation is not None, f"fail-open input passed: {params}"
        assert violation["code"] == "ORDER_INPUT_UNPARSEABLE"

    def test_valid_order_still_passes(self):
        assert order_limits.check_order_limits({
            "type": "option", "symbol": "GOOG", "action": "BUY",
            "quantity": 44, "limitPrice": 12.0,
        }) is None

    def test_absent_optional_fields_stay_allowed(self):
        """Callers legitimately omit limitPrice (market-style previews) —
        absence is not unparseable."""
        assert order_limits.check_order_limits({
            "type": "stock", "symbol": "AAPL", "action": "BUY",
            "quantity": 100,
        }) is None

    def test_nan_strike_makes_a_combo_unpriceable_and_refused(self):
        violation = order_limits.check_order_limits({
            "type": "combo", "symbol": "GLD", "action": "BUY",
            "quantity": 1, "limitPrice": 1.0,
            "legs": [
                {"strike": float("nan"), "right": "C", "action": "SELL", "ratio": 1},
                {"strike": 200.0, "right": "C", "action": "BUY", "ratio": 1},
            ],
        })
        assert violation is not None
        assert violation["code"] == "ORDER_COMBO_STRIKE"
