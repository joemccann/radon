"""Portfolio collapse must retain broker identity used by return-capital v2."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ib_sync import build_account_position_vector, collapse_positions  # noqa: E402


def _leg(account: str, con_id: int) -> dict:
    return {
        "account_id": account,
        "symbol": "SPCX",
        "secType": "OPT",
        "position": -10,
        "avgCost": 500.0,
        "entry_cost": 5_000.0,
        "expiry": "2026-08-21",
        "strike": 120.0,
        "right": "P",
        "structure": "Short Put",
        "conId": con_id,
        "currency": "USD",
        "multiplier": 100.0,
        "marketPrice": 4.0,
        "marketValue": 4_000.0,
        "marketPriceIsCalculated": False,
        "ibDailyPnl": 0.0,
    }


def test_same_contract_shape_in_two_accounts_never_collapses_together():
    collapsed = collapse_positions([_leg("U1", 101), _leg("U2", 101)])

    assert len(collapsed) == 2
    assert {position["account_id"] for position in collapsed} == {"U1", "U2"}
    assert all(position["legs"][0]["con_id"] == 101 for position in collapsed)
    assert all(position["legs"][0]["currency"] == "USD" for position in collapsed)
    assert all(position["legs"][0]["multiplier"] == 100 for position in collapsed)


def test_margin_sample_position_vector_is_account_scoped_and_signed():
    positions = [
        {"account_id": "U1", "conId": 101, "position": 10},
        {"account_id": "U1", "conId": 102, "position": -5},
        {"account_id": "U2", "conId": 101, "position": 99},
    ]

    assert build_account_position_vector(positions, "U1") == {"101": 10.0, "102": -5.0}
