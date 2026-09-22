#!/usr/bin/env python3
"""F20260917-C05 — the futures contract multiplier drives the notional cap.

The Next.js place route now resolves the multiplier server-side from the
contract definition (a client-supplied value is only accepted when it matches).
These tests pin the Python side of that boundary: ``check_order_limits`` must
fail closed without a valid multiplier and must price the notional with the
multiplier it is given, so an understated multiplier can never widen the cap.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import order_limits


class TestFutureMultiplierBoundsNotional:
    def test_future_without_multiplier_refused(self):
        violation = order_limits.check_order_limits({
            "type": "future", "symbol": "VIX", "action": "BUY",
            "quantity": 1, "limitPrice": 20.0,
        })
        assert violation is not None
        assert violation["code"] == "ORDER_FUTURE_MULTIPLIER"

    def test_future_with_invalid_multiplier_refused(self):
        for bad in (0, -100, "nan", "inf", "abc"):
            violation = order_limits.check_order_limits({
                "type": "future", "symbol": "VIX", "action": "BUY",
                "quantity": 1, "limitPrice": 20.0, "multiplier": bad,
            })
            assert violation is not None, f"multiplier={bad!r} must refuse"
            assert violation["code"] == "ORDER_FUTURE_MULTIPLIER"

    def test_notional_cap_prices_with_the_contract_multiplier(self, monkeypatch):
        """1 × $300 × 1000 = $300k > default $250k cap — refused."""
        violation = order_limits.check_order_limits({
            "type": "future", "symbol": "VIX", "action": "BUY",
            "quantity": 1, "limitPrice": 300.0, "multiplier": 1000,
        })
        assert violation is not None
        assert violation["code"] == "ORDER_NOTIONAL_LIMIT"

    def test_understated_multiplier_would_have_passed(self):
        """The same order priced with the option's 100 slips under the cap —
        exactly why the multiplier must be contract-resolved upstream."""
        assert order_limits.order_notional({
            "type": "future", "quantity": 1,
            "limitPrice": 300.0, "multiplier": 100,
        }) == 30_000.0

    def test_in_bounds_future_allowed(self):
        violation = order_limits.check_order_limits({
            "type": "future", "symbol": "VIX", "action": "BUY",
            "quantity": 1, "limitPrice": 20.0, "multiplier": 1000,
        })
        assert violation is None
