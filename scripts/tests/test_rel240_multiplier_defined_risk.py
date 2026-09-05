"""REL-240: multiplier-weighted defined-risk coverage + upper-tail probe."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ib_sync import _defined_option_combo_max_risk, detect_structure_type


def _leg(*, right: str, strike: float, position: int, multiplier: str = "100") -> dict:
    return {
        "account_id": "U1",
        "symbol": "MIX",
        "secType": "OPT",
        "right": right,
        "strike": strike,
        "position": position,
        "multiplier": multiplier,
        "expiry": "2026-09-18",
        "entry_cost": 1_000,
        "avgCost": 1_000 / abs(position),
        "marketPrice": 1.0,
        "marketValue": 100 * position,
        "basis_source": "ib",
    }


def test_equal_count_mixed_multiplier_short_heavy_is_not_defined():
    # Equal contract counts per right, but short leg controls 10x the notional
    # (multiplier 100 vs 10): naked upper-tail exposure, must not be "defined".
    legs = [
        _leg(right="C", strike=110, position=-1, multiplier="100"),
        _leg(right="C", strike=100, position=1, multiplier="10"),
        _leg(right="P", strike=90, position=1, multiplier="100"),
    ]

    _, risk = detect_structure_type(legs)

    assert risk != "defined"


def test_equal_multiplier_coverage_stays_defined():
    legs = [
        _leg(right="C", strike=110, position=-1),
        _leg(right="C", strike=100, position=1),
        _leg(right="P", strike=90, position=1),
    ]

    assert detect_structure_type(legs) == ("Combo (3 legs)", "defined")


def test_max_risk_probes_above_max_strike():
    # Long 1 C100 x10 vs short 1 C110 x100: PnL at every strike point is >= 0,
    # but the slope above max(strikes) is -90/pt. Without an upper-tail probe
    # the function returns 0 risk.
    legs = [
        _leg(right="C", strike=100, position=1, multiplier="10"),
        _leg(right="C", strike=110, position=-1, multiplier="100"),
    ]

    risk = _defined_option_combo_max_risk(legs, total_entry_cost=0.0)

    assert risk is not None
    # Probe at 2 * max(strikes) = 220: long (120*10) - short (110*100) = -9800.
    assert risk == pytest.approx(9_800)
