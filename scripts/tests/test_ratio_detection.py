"""TDD tests for ratio position detection in ib_sync.py.

Bug: A 1x2 risk reversal (e.g., 25 short puts + 50 long calls) is displayed as
a plain "Risk Reversal" with no indication that it's a ratio structure. The user
needs to see "Ratio Risk Reversal" with the ratio notation (e.g., "1x2") to
understand the position's actual risk profile.

Fix: detect_structure_type() must check whether leg contract counts differ.
When they do, prefix with "Ratio" and format_structure_description() must
include the NxM ratio notation.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from ib_sync import detect_structure_type, format_structure_description


class TestRatioDetection:
    """Ratio structures: legs with different contract counts."""

    # ── Risk Reversals ──

    def test_equal_contracts_is_plain_risk_reversal(self):
        """50 short puts + 50 long calls = standard Risk Reversal (not ratio)."""
        legs = [
            {'secType': 'OPT', 'right': 'P', 'position': -50, 'strike': 85.0},
            {'secType': 'OPT', 'right': 'C', 'position': 50, 'strike': 115.0},
        ]
        structure, risk = detect_structure_type(legs)
        assert structure == "Risk Reversal"
        assert "Ratio" not in structure

    def test_1x2_risk_reversal_detected(self):
        """25 short puts + 50 long calls = Ratio Risk Reversal."""
        legs = [
            {'secType': 'OPT', 'right': 'P', 'position': -25, 'strike': 85.0},
            {'secType': 'OPT', 'right': 'C', 'position': 50, 'strike': 115.0},
        ]
        structure, risk = detect_structure_type(legs)
        assert structure == "Ratio Risk Reversal"
        assert risk == "undefined"

    def test_2x1_reverse_risk_reversal_detected(self):
        """50 long puts + 25 short calls = Ratio Reverse Risk Reversal."""
        legs = [
            {'secType': 'OPT', 'right': 'P', 'position': 50, 'strike': 85.0},
            {'secType': 'OPT', 'right': 'C', 'position': -25, 'strike': 115.0},
        ]
        structure, risk = detect_structure_type(legs)
        assert structure == "Ratio Reverse Risk Reversal"
        assert risk == "undefined"

    # ── Format descriptions with ratio ──

    def test_ratio_risk_reversal_description_includes_ratio(self):
        """Description shows NxM ratio + strikes."""
        legs = [
            {'secType': 'OPT', 'right': 'P', 'position': -25, 'strike': 85.0},
            {'secType': 'OPT', 'right': 'C', 'position': 50, 'strike': 115.0},
        ]
        desc = format_structure_description("Ratio Risk Reversal", legs)
        assert "50x25" in desc
        assert "P$85.0" in desc
        assert "C$115.0" in desc

    def test_ratio_risk_reversal_description_uses_actual_leg_counts_in_long_short_order(self):
        """Risk reversal labels should follow the displayed long/short leg order, not strike sort or reduced GCD form."""
        legs = [
            {'secType': 'OPT', 'right': 'P', 'position': -10, 'strike': 400.0},
            {'secType': 'OPT', 'right': 'C', 'position': 75, 'strike': 410.0},
        ]
        desc = format_structure_description("Ratio Risk Reversal", legs)
        assert "75x10" in desc
        assert "2x15" not in desc

    def test_plain_risk_reversal_description_no_ratio(self):
        """Standard risk reversal description has no ratio prefix."""
        legs = [
            {'secType': 'OPT', 'right': 'P', 'position': -50, 'strike': 85.0},
            {'secType': 'OPT', 'right': 'C', 'position': 50, 'strike': 115.0},
        ]
        desc = format_structure_description("Risk Reversal", legs)
        assert "x" not in desc.split("$")[0]  # no ratio notation before strikes
        assert "P$85.0" in desc
        assert "C$115.0" in desc

    # ── Vertical spread ratios ──

    def test_ratio_call_spread_detected(self):
        """25 long calls + 50 short calls at different strikes = Ratio Bull Call Spread."""
        legs = [
            {'secType': 'OPT', 'right': 'C', 'position': 25, 'strike': 100.0},
            {'secType': 'OPT', 'right': 'C', 'position': -50, 'strike': 110.0},
        ]
        structure, risk = detect_structure_type(legs)
        assert "Ratio" in structure
        assert "Call Spread" in structure

    def test_ratio_put_spread_detected(self):
        """50 long puts + 25 short puts = Ratio Bear Put Spread."""
        legs = [
            {'secType': 'OPT', 'right': 'P', 'position': 50, 'strike': 110.0},
            {'secType': 'OPT', 'right': 'P', 'position': -25, 'strike': 100.0},
        ]
        structure, risk = detect_structure_type(legs)
        assert "Ratio" in structure
        assert "Put Spread" in structure

    def test_equal_contract_spread_is_not_ratio(self):
        """50 long calls + 50 short calls = plain Bull Call Spread."""
        legs = [
            {'secType': 'OPT', 'right': 'C', 'position': 50, 'strike': 100.0},
            {'secType': 'OPT', 'right': 'C', 'position': -50, 'strike': 110.0},
        ]
        structure, _ = detect_structure_type(legs)
        assert "Ratio" not in structure
        assert structure == "Bull Call Spread"

    # ── Ratio description for spreads ──

    def test_ratio_spread_description_includes_ratio(self):
        """Ratio spread description shows NxM ratio."""
        legs = [
            {'secType': 'OPT', 'right': 'C', 'position': 25, 'strike': 100.0},
            {'secType': 'OPT', 'right': 'C', 'position': -50, 'strike': 110.0},
        ]
        desc = format_structure_description("Ratio Bull Call Spread", legs)
        assert "1x2" in desc
        assert "$100.0/$110.0" in desc

    # ── Synthetics (same strike, different counts = ratio synthetic) ──

    def test_ratio_synthetic_detected(self):
        """25 short puts + 50 long calls at same strike = Ratio Synthetic Long."""
        legs = [
            {'secType': 'OPT', 'right': 'P', 'position': -25, 'strike': 100.0},
            {'secType': 'OPT', 'right': 'C', 'position': 50, 'strike': 100.0},
        ]
        structure, risk = detect_structure_type(legs)
        assert "Ratio" in structure
        assert "Synthetic" in structure
        assert risk == "undefined"

    def test_equal_synthetic_not_ratio(self):
        """50 short puts + 50 long calls at same strike = plain Synthetic Long."""
        legs = [
            {'secType': 'OPT', 'right': 'P', 'position': -50, 'strike': 100.0},
            {'secType': 'OPT', 'right': 'C', 'position': 50, 'strike': 100.0},
        ]
        structure, _ = detect_structure_type(legs)
        assert structure == "Synthetic Long"
        assert "Ratio" not in structure
