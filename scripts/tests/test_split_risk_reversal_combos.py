"""Same-expiry legs that pair into two risk reversals list as separate positions."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ib_sync import collapse_positions


def _leg(strike: float, position: int, right: str, entry: float = 1_000) -> dict:
    return {
        "account_id": "U1",
        "symbol": "PLTR",
        "secType": "OPT",
        "right": right,
        "strike": strike,
        "position": position,
        "expiry": "2026-10-16",
        "entry_cost": entry,
        "avgCost": entry / abs(position),
        "marketPrice": 1.0,
        "marketValue": 500,
        "basis_source": "ib",
    }


def _pltr_legs():
    return [
        _leg(175, 40, "C"),
        _leg(180, 25, "C"),
        _leg(160, -25, "P"),
        _leg(170, -20, "P"),
    ]


def test_two_stacked_risk_reversals_split_by_matching_contract_counts():
    positions = collapse_positions(_pltr_legs())

    assert [p["structure"] for p in positions] == [
        "Ratio Risk Reversal 40x20 (P$170/C$175)",
        "Risk Reversal (P$160/C$180)",
    ]
    assert [p["contracts"] for p in positions] == [40, 25]
    assert all(len(p["legs"]) == 2 and p["risk_profile"] == "undefined" for p in positions)
    assert [p["id"] for p in positions] == [1, 2]


def test_split_is_leg_order_invariant():
    legs = list(reversed(_pltr_legs()))

    assert len(collapse_positions(legs)) == 2


def test_bearish_stacked_reverse_risk_reversals_split():
    legs = [_leg(160, 10, "P"), _leg(150, 30, "P"), _leg(180, -10, "C"), _leg(190, -30, "C")]

    positions = collapse_positions(legs)

    assert sorted(p["structure"] for p in positions) == [
        "Reverse Risk Reversal (P$150/C$190)",
        "Reverse Risk Reversal (P$160/C$180)",
    ]


def test_ambiguous_equal_sized_pairs_stay_one_combo():
    legs = [_leg(175, 10, "C"), _leg(180, 10, "C"), _leg(160, -10, "P"), _leg(170, -10, "P")]

    [position] = collapse_positions(legs)

    assert len(position["legs"]) == 4


def test_no_matching_counts_stays_one_combo():
    legs = [_leg(175, 40, "C"), _leg(180, 25, "C"), _leg(160, -30, "P"), _leg(170, -20, "P")]

    [position] = collapse_positions(legs)

    assert len(position["legs"]) == 4


def test_iron_condor_shape_is_not_split():
    legs = [_leg(90, 10, "P"), _leg(100, -10, "P"), _leg(155, -10, "C"), _leg(170, 10, "C")]

    [position] = collapse_positions(legs)

    assert len(position["legs"]) == 4
