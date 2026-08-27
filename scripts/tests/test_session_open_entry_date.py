"""A position opened during the current session must carry today's entry_date.

2026-08-27: META Long Call $575 2026-08-28 25x was opened today, but the same
contract had been traded (and closed) on 2026-08-26 as the short leg of a
spread. `read_journal_entry_date_maps` keeps the EARLIEST date per contract, so
entry_date resolved to 2026-08-26, `isSameDay` missed, and Today P&L fell back
to yesterday's close: -$9,425 against a total P&L of -$1,750.

The session's own fills are the authority on this. When the net signed session
qty for a contract equals the live position, the contract was flat at the
session open — every unit held now was filled today, so entry_date is today and
yesterday's close is not a baseline for it.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import ib_sync  # noqa: E402

TODAY_ET = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def _position(*, position=25, avg_cost=555.0, strike=575.0, right="C", expiry="20260828"):
    contract = SimpleNamespace(
        symbol="META",
        secType="OPT",
        strike=strike,
        right=right,
        conId=77001,
        lastTradeDateOrContractMonth=expiry,
        multiplier="100",
        currency="USD",
    )
    return SimpleNamespace(contract=contract, position=position, avgCost=avg_cost, account="U123")


def _fill(*, side, shares, price=5.55, when=None, strike=575.0, expiry="20260828", right="C"):
    contract = SimpleNamespace(
        symbol="META",
        secType="OPT",
        strike=strike,
        right=right,
        lastTradeDateOrContractMonth=expiry,
    )
    execution = SimpleNamespace(
        side=side,
        shares=shares,
        avgPrice=price,
        price=price,
        time=when or datetime.now(ZoneInfo("America/New_York")).replace(hour=11, minute=0, second=0, microsecond=0),
    )
    return SimpleNamespace(contract=contract, execution=execution)


def _fetch(fills, position):
    client = SimpleNamespace(get_positions=lambda: [position])
    return ib_sync.fetch_positions(
        client, fill_basis_lookup=ib_sync.build_fill_basis(fills)
    )[0]


def test_session_fills_covering_live_qty_stamp_todays_date():
    leg = _fetch([_fill(side="BOT", shares=25)], _position())
    assert leg["session_fill_date"] == TODAY_ET


def test_same_day_open_then_partial_close_is_still_same_day():
    """Bought 25, sold 10 — the remaining 15 was still all filled today."""
    fills = [_fill(side="BOT", shares=25), _fill(side="SLD", shares=10, price=4.90)]
    leg = _fetch(fills, _position(position=15))
    assert leg["session_fill_date"] == TODAY_ET


def test_overnight_inventory_is_not_session_dated():
    """Held 25 overnight, added 10 today: session net 10 != live 35."""
    leg = _fetch([_fill(side="BOT", shares=10)], _position(position=35))
    assert leg["session_fill_date"] is None


def test_no_session_fills_leaves_date_unresolved():
    leg = _fetch([], _position())
    assert leg["session_fill_date"] is None


def test_collapse_promotes_session_date_only_when_every_leg_has_one():
    def _leg(session_fill_date, strike, right):
        return {
            "account_id": "U123", "symbol": "META", "secType": "OPT", "position": 25,
            "avgCost": 555.0, "ibAvgCost": 555.0, "basis_source": "session_fills",
            "entry_cost": 13875.0, "expiry": "2026-08-28", "strike": strike, "right": right,
            "structure": "Long Call", "conId": 1, "currency": "USD", "multiplier": 100.0,
            "contract": None, "session_fill_date": session_fill_date,
        }

    both = ib_sync.collapse_positions([_leg(TODAY_ET, 575.0, "C"), _leg(TODAY_ET, 580.0, "C")])
    assert both[0]["session_fill_date"] == TODAY_ET

    mixed = ib_sync.collapse_positions([_leg(TODAY_ET, 575.0, "C"), _leg(None, 580.0, "C")])
    assert mixed[0]["session_fill_date"] is None


def _collapsed(session_fill_date):
    return [{
        "id": 1, "ticker": "META", "structure": "Long Call $575.0",
        "structure_type": "Long Call", "risk_profile": "defined", "expiry": "2026-08-28",
        "contracts": 25, "direction": "LONG", "entry_cost": 13875.0, "max_risk": 13875.0,
        "market_value": 12125.0, "market_price_is_calculated": False, "ib_daily_pnl": None,
        "session_fill_date": session_fill_date,
        "legs": [{
            "direction": "LONG", "contracts": 25, "type": "Call", "strike": 575.0,
            "entry_cost": 13875.0, "avg_cost": 555.0, "market_price": 4.85,
            "market_value": 12125.0, "market_price_is_calculated": False,
        }],
        "kelly_optimal": None, "target": None, "stop": None,
    }]


def test_reopened_contract_does_not_inherit_prior_round_trip_date():
    """The 575C traded on 2026-08-26 as a spread leg and was flat overnight.
    Today's reopen must not inherit that journal date."""
    with patch.multiple(
        ib_sync,
        read_journal_entry_date_maps=lambda: (
            {"META|Long Call $575.0": "2026-08-26"},
            {"META|2026-08-28|C|575.0": "2026-08-26"},
        ),
        read_latest_portfolio_snapshot=lambda: None,
    ):
        result = ib_sync.convert_to_portfolio_format(
            {"NetLiquidation": 1_000_000}, _collapsed(TODAY_ET), {}
        )
    assert result["positions"][0]["entry_date"] == TODAY_ET


def test_without_session_coverage_the_journal_date_still_wins():
    with patch.multiple(
        ib_sync,
        read_journal_entry_date_maps=lambda: ({}, {"META|2026-08-28|C|575.0": "2026-08-26"}),
        read_latest_portfolio_snapshot=lambda: None,
    ):
        result = ib_sync.convert_to_portfolio_format(
            {"NetLiquidation": 1_000_000}, _collapsed(None), {}
        )
    assert result["positions"][0]["entry_date"] == "2026-08-26"
