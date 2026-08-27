"""Session-fill remaining inventory must set option avg entry.

2026-08-27: META Long Call $575 25x showed AVG ENTRY $4.69 while Today's
Executed Orders had the same contract OPEN 25 @ $5.55. ib_sync used IB's
lagged avgCost (finite, so the 1s wait returned) and, when the journal
still held a same-sized open lot, treated that stale basis as complete.
Session fills already feed entry_date; they must also replace basis when
FIFO remaining inventory qty equals the live position.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import ib_sync  # noqa: E402


def _position(*, symbol="META", position=25, avg_cost=469.0, strike=575.0, right="C", expiry="20260828"):
    contract = SimpleNamespace(
        symbol=symbol,
        secType="OPT",
        strike=strike,
        right=right,
        conId=77001,
        lastTradeDateOrContractMonth=expiry,
        multiplier="100",
        currency="USD",
    )
    return SimpleNamespace(contract=contract, position=position, avgCost=avg_cost, account="U123")


def _fill(*, side, shares, price, time="2026-08-27T14:49:55", strike=575.0, expiry="20260828", right="C"):
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
        time=datetime.fromisoformat(time),
    )
    return SimpleNamespace(contract=contract, execution=execution)


def test_session_open_overrides_stale_ib_avg_cost():
    """Fresh 25-lot at $5.55; IB still reporting $4.69/share (469/contract)."""
    fills = [_fill(side="BOT", shares=25, price=5.55)]
    lookup = ib_sync.build_fill_basis(fills)
    client = SimpleNamespace(get_positions=lambda: [_position()])

    positions = ib_sync.fetch_positions(client, fill_basis_lookup=lookup)
    pos = positions[0]

    assert pos["entry_cost"] == pytest.approx(13875.0, abs=0.01)
    assert pos["avgCost"] == pytest.approx(555.0, abs=0.0001)
    assert pos["ibAvgCost"] == pytest.approx(469.0, abs=0.0001)
    assert pos["basis_source"] == "session_fills"


def test_session_fills_win_over_stale_same_qty_journal():
    """Journal still shows yesterday's 25-lot at $4.69; today reopened at $5.55."""
    fills = [_fill(side="BOT", shares=25, price=5.55)]
    journal = ib_sync._with_net_qty(
        {"META|20260828|C|575.0": 11725.0},
        {"META|20260828|C|575.0": 25.0},
    )
    client = SimpleNamespace(get_positions=lambda: [_position()])

    positions = ib_sync.fetch_positions(
        client, journal_basis_lookup=journal, fill_basis_lookup=ib_sync.build_fill_basis(fills)
    )
    pos = positions[0]
    assert pos["avgCost"] == pytest.approx(555.0, abs=0.0001)
    assert pos["entry_cost"] == pytest.approx(13875.0, abs=0.01)


def test_close_then_reopen_uses_new_fill_price():
    fills = [
        _fill(side="SLD", shares=25, price=4.69, time="2026-08-27T13:00:00"),
        _fill(side="BOT", shares=25, price=5.55, time="2026-08-27T14:49:55"),
    ]
    client = SimpleNamespace(get_positions=lambda: [_position()])
    pos = ib_sync.fetch_positions(
        client, fill_basis_lookup=ib_sync.build_fill_basis(fills)
    )[0]
    assert pos["avgCost"] == pytest.approx(555.0, abs=0.0001)


def test_partial_session_fill_does_not_override():
    """Overnight 25 + today add 10: session remaining 10 != live 35."""
    fills = [_fill(side="BOT", shares=10, price=5.55)]
    client = SimpleNamespace(get_positions=lambda: [_position(position=35, avg_cost=469.0)])
    pos = ib_sync.fetch_positions(
        client, fill_basis_lookup=ib_sync.build_fill_basis(fills)
    )[0]
    assert pos["avgCost"] == pytest.approx(469.0, abs=0.0001)
    assert pos.get("basis_source") != "session_fills"


def test_no_fills_keeps_journal_override():
    journal = ib_sync._with_net_qty(
        {"META|20260828|C|575.0": 11725.0},
        {"META|20260828|C|575.0": 25.0},
    )
    client = SimpleNamespace(get_positions=lambda: [_position()])
    pos = ib_sync.fetch_positions(client, journal_basis_lookup=journal)[0]
    assert pos["entry_cost"] == pytest.approx(11725.0, abs=0.01)
    assert pos["avgCost"] == pytest.approx(469.0, abs=0.0001)
