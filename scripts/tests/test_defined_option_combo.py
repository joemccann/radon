"""Regression coverage for bounded option combos beyond named two-leg spreads."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ib_sync import collapse_positions, detect_structure_type


def _arm_leg(*, right: str, strike: float, position: int, entry: float, mark: float) -> dict:
    return {
        "account_id": "U1",
        "symbol": "ARM",
        "secType": "OPT",
        "right": right,
        "strike": strike,
        "position": position,
        "expiry": "2026-09-18",
        "entry_cost": entry,
        "avgCost": entry / abs(position),
        "marketPrice": mark / abs(position) / 100,
        "marketValue": mark,
        "basis_source": "ib",
    }


ARM_LEGS = [
    _arm_leg(right="C", strike=260, position=-10, entry=8_000, mark=8_600),
    _arm_leg(right="C", strike=270, position=10, entry=10_380, mark=5_530),
    _arm_leg(right="P", strike=220, position=10, entry=7_030, mark=1_680),
]


def test_protected_short_call_plus_long_put_is_defined_risk():
    structure, risk = detect_structure_type(ARM_LEGS)

    assert structure == "Combo (3 legs)"
    assert risk == "defined"


def test_collapsed_defined_combo_has_exact_expiry_max_risk():
    [position] = collapse_positions(ARM_LEGS)

    assert position["risk_profile"] == "defined"
    assert position["max_risk"] == pytest.approx(19_410)


def test_defined_combo_detection_is_leg_order_invariant():
    assert detect_structure_type(list(reversed(ARM_LEGS))) == (
        "Combo (3 legs)",
        "defined",
    )


def test_extra_long_with_uncovered_short_call_stays_undefined():
    legs = [
        _arm_leg(right="C", strike=260, position=-20, entry=16_000, mark=17_200),
        _arm_leg(right="C", strike=270, position=10, entry=10_380, mark=5_530),
        _arm_leg(right="P", strike=220, position=10, entry=7_030, mark=1_680),
    ]

    _, risk = detect_structure_type(legs)

    assert risk == "complex"
