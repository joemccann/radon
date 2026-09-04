"""
TDD tests for all-long combo detection in ib_sync.py.

Bug: A combo with 2 long calls (e.g., AAOI 25x $105C + 25x $130C) is classified
as risk_profile="complex" instead of "defined". The PortfolioSections component
only renders "defined", "undefined", and "equity" — so "complex" positions are
silently dropped from the UI.

Red/Green workflow:
1. Write failing tests that reproduce the bug → RED
2. Fix detect_structure_type to handle all-long combos → GREEN
3. Fix UI to include "complex" in a fallback bucket → GREEN
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ib_sync import detect_structure_type


def _make_leg(right: str, strike: float, position: int) -> dict:
    return {
        "secType": "OPT",
        "right": right,
        "strike": strike,
        "position": position,
        "symbol": "AAOI",
    }


class TestAllLongComboDetection:
    """Positions with ALL long legs should be classified as defined risk."""

    def test_two_long_calls_different_strikes(self):
        """AAOI: 25x Long $105C + 25x Long $130C = defined risk (all-long combo)."""
        legs = [
            _make_leg("C", 105.0, 25),
            _make_leg("C", 130.0, 25),
        ]
        structure, risk = detect_structure_type(legs)
        assert risk == "defined", f"Expected 'defined' but got '{risk}' for two long calls"
        # Structure name should be descriptive, not "Combo (2 legs)"
        assert "Combo" not in structure or "Long" in structure, \
            f"Structure should indicate it's all-long, got '{structure}'"

    def test_two_long_puts_different_strikes(self):
        """Long $130P + Long $105P = defined risk (all-long combo / long put spread)."""
        legs = [
            _make_leg("P", 130.0, 10),
            _make_leg("P", 105.0, 10),
        ]
        structure, risk = detect_structure_type(legs)
        assert risk == "defined", f"Expected 'defined' but got '{risk}' for two long puts"

    def test_long_call_long_put_different_strikes(self):
        """Long $105C + Long $130P = defined risk (long strangle)."""
        legs = [
            _make_leg("C", 105.0, 5),
            _make_leg("P", 130.0, 5),
        ]
        structure, risk = detect_structure_type(legs)
        assert risk == "defined", f"Expected 'defined' but got '{risk}' for long strangle"

    def test_long_call_long_put_same_strike(self):
        """Long $105C + Long $105P = defined risk (long straddle)."""
        legs = [
            _make_leg("C", 105.0, 5),
            _make_leg("P", 105.0, 5),
        ]
        structure, risk = detect_structure_type(legs)
        assert risk == "defined", f"Expected 'defined' but got '{risk}' for long straddle"

    def test_three_long_calls(self):
        """3 long calls at different strikes = defined risk."""
        legs = [
            _make_leg("C", 100.0, 10),
            _make_leg("C", 110.0, 10),
            _make_leg("C", 120.0, 10),
        ]
        structure, risk = detect_structure_type(legs)
        assert risk == "defined", f"Expected 'defined' but got '{risk}' for three long calls"

    def test_protected_short_call_with_extra_long_put_is_defined(self):
        """A capped call spread plus an extra long put remains defined risk."""
        legs = [
            _make_leg("C", 100.0, 10),   # long
            _make_leg("C", 110.0, -10),   # short
            _make_leg("P", 90.0, 10),     # long
        ]
        _, risk = detect_structure_type(legs)
        assert risk == "defined"

    def test_bull_call_spread_still_works(self):
        """Existing vertical spread detection still works (1 long, 1 short call)."""
        legs = [
            _make_leg("C", 105.0, 25),
            _make_leg("C", 130.0, -25),
        ]
        structure, risk = detect_structure_type(legs)
        assert risk == "defined"
        assert "Bull Call Spread" in structure

    def test_bear_put_spread_still_works(self):
        """Existing vertical spread detection still works (1 long, 1 short put)."""
        legs = [
            _make_leg("P", 130.0, 10),
            _make_leg("P", 105.0, -10),
        ]
        structure, risk = detect_structure_type(legs)
        assert risk == "defined"
        assert "Bear Put Spread" in structure
