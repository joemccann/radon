"""Same-expiry, same-right legs that pair into verticals list as separate spreads."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ib_sync import collapse_positions


def _leg(strike: float, position: int, right: str = "C", entry: float = 1_000) -> dict:
    return {
        "account_id": "U1",
        "symbol": "SPCX",
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


def test_two_stacked_call_spreads_split_into_two_positions():
    legs = [_leg(155, 200), _leg(170, -200), _leg(175, 100), _leg(200, -100)]

    positions = collapse_positions(legs)

    assert [p["structure"] for p in positions] == [
        "Bull Call Spread $155/$170",
        "Bull Call Spread $175/$200",
    ]
    assert [p["contracts"] for p in positions] == [200, 100]
    assert all(len(p["legs"]) == 2 and p["risk_profile"] == "defined" for p in positions)
    assert [p["id"] for p in positions] == [1, 2]


def test_split_is_leg_order_invariant():
    legs = [_leg(200, -100), _leg(155, 200), _leg(175, 100), _leg(170, -200)]

    assert len(collapse_positions(legs)) == 2


def test_butterfly_with_unequal_counts_stays_one_combo():
    legs = [_leg(150, 10), _leg(160, -20), _leg(170, 10)]

    [position] = collapse_positions(legs)

    assert len(position["legs"]) == 3


def test_unpairable_four_leg_group_stays_one_combo():
    legs = [_leg(155, 200), _leg(170, -100), _leg(175, 100), _leg(200, -200)]

    [position] = collapse_positions(legs)

    assert len(position["legs"]) == 4


def test_mixed_rights_are_not_split():
    legs = [_leg(90, 10, "P"), _leg(100, -10, "P"), _leg(155, 10), _leg(170, -10)]

    [position] = collapse_positions(legs)

    assert len(position["legs"]) == 4
