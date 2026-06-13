#!/usr/bin/env python3
"""Basis-completeness guard for ib_sync.fetch_positions.

When the journal's net qty for a contract disagrees with the IB-reported
position qty, the journal open-basis is INCOMPLETE (missing fills) and must
NOT override IB's avgCost — doing so collapsed MU 1050 C P&L to -128% off a
half-sized basis (only 5 of 10 contracts in the journal). The guard keeps
IB avgCost whenever |journal_net_qty| != |position_qty| and warns.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import ib_sync  # noqa: E402


class _FakeCursor:
    """Mirrors the real libsql cursor: rows via fetchall(), no .rows."""

    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeDb:
    def __init__(self, rows):
        self._rows = rows
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return _FakeCursor(self._rows)


def _journal_row(payload: dict, filled_at: str) -> tuple:
    # Driver-faithful row shape: (payload, filled_at, written_at) tuple.
    return (json.dumps(payload), filled_at, filled_at)


def _make_position(
    *,
    symbol: str,
    sec_type: str,
    position: int,
    avg_cost: float,
    strike: float,
    right: str,
    expiry: str,
):
    contract = SimpleNamespace(
        symbol=symbol,
        secType=sec_type,
        strike=strike,
        right=right,
        conId=1000 + int(strike),
        lastTradeDateOrContractMonth=expiry,
    )
    return SimpleNamespace(contract=contract, position=position, avgCost=avg_cost)


# ── MU 1050 C: journal has only 5 of 10 short contracts (the reported bug). ──
_MU_KEY = "MU|20260717|C|1050.0"
_MU_INCOMPLETE_BASIS = 47504.4862  # only the 5 @ $95 fill made the journal
_MU_IB_AVG_COST = 10209.088136     # per-contract, true ~$102.09/share


def _mu_position():
    return _make_position(
        symbol="MU",
        sec_type="OPT",
        position=-10,          # IB truth: 10 short
        avg_cost=_MU_IB_AVG_COST,
        strike=1050,
        right="C",
        expiry="20260717",
    )


def _mu_incomplete_journal_row(open_basis=None):
    payload = {
        "ticker": "MU",
        "action": "SELL_TO_OPEN",
        "contracts": 5,        # only 5 of the 10 short contracts
        "total_cost": _MU_INCOMPLETE_BASIS,
        "right": "C",
        "strike": 1050,
        "expiry": "20260717",
    }
    if open_basis is not None:
        payload["open_basis"] = open_basis
    return _journal_row(payload, "2026-06-10T14:00:00Z")


def test_incomplete_journal_recompute_path_keeps_ib_avg_cost(capsys):
    """RED: journal net qty (5) != position qty (10) -> keep IB avgCost.

    Recompute path: no persisted open_basis, basis recomputed from fills.
    """
    db = _FakeDb([_mu_incomplete_journal_row(open_basis=None)])
    client = SimpleNamespace(get_positions=lambda: [_mu_position()])

    lookup = ib_sync.build_journal_basis_lookup(client, db=db)
    positions = ib_sync.fetch_positions(client, journal_basis_lookup=lookup)

    pos = positions[0]
    assert pos["avgCost"] == pytest.approx(_MU_IB_AVG_COST, abs=0.0001)
    assert pos["ibAvgCost"] == pytest.approx(_MU_IB_AVG_COST, abs=0.0001)
    # entry_cost stays IB-derived: |avgCost * position| = true total basis.
    assert pos["entry_cost"] == pytest.approx(102090.88, abs=0.5)

    warning = capsys.readouterr().out
    assert _MU_KEY in warning
    assert "journal" in warning.lower()


def test_incomplete_journal_persisted_path_keeps_ib_avg_cost(capsys):
    """RED: persisted open_basis from an incomplete row set is also rejected."""
    db = _FakeDb([_mu_incomplete_journal_row(open_basis=_MU_INCOMPLETE_BASIS)])
    client = SimpleNamespace(get_positions=lambda: [_mu_position()])

    lookup = ib_sync.build_journal_basis_lookup(client, db=db)
    positions = ib_sync.fetch_positions(client, journal_basis_lookup=lookup)

    pos = positions[0]
    assert pos["avgCost"] == pytest.approx(_MU_IB_AVG_COST, abs=0.0001)
    assert pos["entry_cost"] == pytest.approx(102090.88, abs=0.5)

    warning = capsys.readouterr().out
    assert _MU_KEY in warning


def test_matching_qty_still_applies_journal_override():
    """GREEN: journal net qty == position qty -> journal basis WINS (no regression)."""
    rows = [
        _journal_row(
            {
                "ticker": "USAX",
                "action": "SELL_TO_OPEN",
                "contracts": 10,
                "total_cost": 12345.67,
                "right": "C",
                "strike": 43,
                "expiry": "20260717",
            },
            "2026-06-01T14:00:00Z",
        )
    ]
    db = _FakeDb(rows)
    position = _make_position(
        symbol="USAX",
        sec_type="OPT",
        position=-10,
        avg_cost=999.99,  # drifted IB VWAP that journal should override
        strike=43,
        right="C",
        expiry="20260717",
    )
    client = SimpleNamespace(get_positions=lambda: [position])

    lookup = ib_sync.build_journal_basis_lookup(client, db=db)
    positions = ib_sync.fetch_positions(client, journal_basis_lookup=lookup)

    pos = positions[0]
    assert pos["entry_cost"] == pytest.approx(12345.67, abs=0.01)
    assert pos["avgCost"] == pytest.approx(12345.67 / 10, abs=0.0001)
    assert pos["ibAvgCost"] == pytest.approx(999.99, abs=0.0001)


def test_flat_position_unaffected():
    """A flat (qty 0) position never triggers the override or the guard."""
    db = _FakeDb([])
    position = _make_position(
        symbol="NVDA",
        sec_type="OPT",
        position=0,
        avg_cost=500.0,
        strike=100,
        right="C",
        expiry="20260717",
    )
    client = SimpleNamespace(get_positions=lambda: [position])

    lookup = ib_sync.build_journal_basis_lookup(client, db=db)
    positions = ib_sync.fetch_positions(client, journal_basis_lookup=lookup)

    pos = positions[0]
    assert pos["avgCost"] == pytest.approx(500.0, abs=0.0001)
    assert pos["entry_cost"] == pytest.approx(0.0, abs=0.0001)


def test_legacy_call_without_net_qty_map_preserves_override():
    """Back-compat: callers passing only journal_basis_lookup still get the
    override (the dict-only path predates the guard map)."""
    journal_basis_lookup = {"AAOI|20260717|C|200.0": 59519.23}
    position = _make_position(
        symbol="AAOI",
        sec_type="OPT",
        position=25,
        avg_cost=2730.50,
        strike=200,
        right="C",
        expiry="20260717",
    )
    client = SimpleNamespace(get_positions=lambda: [position])

    positions = ib_sync.fetch_positions(
        client, journal_basis_lookup=journal_basis_lookup
    )

    assert positions[0]["entry_cost"] == pytest.approx(59519.23, abs=0.01)


def test_negative_journal_basis_treated_as_absolute_value():
    """M20 killer: entry_cost must be abs(basis) — a negative stored basis must
    not propagate as a negative entry cost.

    Arithmetic (first principles, no test fixtures):
      journal_basis_lookup["SPY|20260717|C|500.0"] = -24000.0  (hypothetical negative)
      position_size = 10 (LONG 10 contracts)
      journal_net_qty = 10  → basis_is_complete = True (|10| == |10|)

      Correct:   entry_cost = abs(-24000.0) = 24000.0
                 avg_cost   = 24000.0 / abs(10) = 2400.0

      If abs() were removed:
                 entry_cost = -24000.0  (WRONG — negative entry cost is nonsensical)
                 avg_cost   = -24000.0 / 10 = -2400.0 (WRONG)
    """
    # Pass a negative basis directly via the lookup dict (bypasses DB, exercises
    # the abs() guard in ib_sync.fetch_positions line 727).
    journal_basis_lookup = {"SPY|20260717|C|500.0": -24000.0}

    from ib_sync import _BasisLookup, _with_net_qty

    enriched = _with_net_qty(journal_basis_lookup, {"SPY|20260717|C|500.0": 10.0})

    position = _make_position(
        symbol="SPY",
        sec_type="OPT",
        position=10,
        avg_cost=2730.50,
        strike=500,
        right="C",
        expiry="20260717",
    )
    client = SimpleNamespace(get_positions=lambda: [position])

    positions = ib_sync.fetch_positions(client, journal_basis_lookup=enriched)
    pos = positions[0]

    # entry_cost = abs(-24000.0) = 24000.0
    assert pos["entry_cost"] == pytest.approx(24000.0, abs=0.01), (
        "entry_cost must be positive (abs of journal basis); "
        "negative basis storage must not leak to position cost"
    )
    # avg_cost = 24000.0 / abs(10) = 2400.0
    assert pos["avgCost"] == pytest.approx(2400.0, abs=0.0001), (
        "avg_cost = abs(basis) / abs(position_size) = 24000 / 10 = 2400.0"
    )
    # ibAvgCost must preserve the original IB value unchanged
    assert pos["ibAvgCost"] == pytest.approx(2730.50, abs=0.0001)


def test_short_position_with_negative_journal_basis_absolute_valued():
    """M20 killer (short leg variant): short position basis from lookup must also
    use abs() so entry_cost stays positive.

    Arithmetic (first principles):
      journal_basis_lookup["XSP|20261219|P|400.0"] = -8500.0  (negative stored)
      position_size = -5  (SHORT 5 puts)
      journal_net_qty = -5  → |−5| == |−5| → basis_is_complete = True

      Correct:   entry_cost = abs(-8500.0) = 8500.0
                 avg_cost   = 8500.0 / abs(-5) = 1700.0

      If abs() removed from entry_cost:
                 entry_cost = -8500.0   (WRONG)
                 avg_cost   = -8500.0 / 5 = -1700.0  (WRONG)
    """
    from ib_sync import _with_net_qty

    journal_basis_lookup_raw = {"XSP|20261219|P|400.0": -8500.0}
    enriched = _with_net_qty(journal_basis_lookup_raw, {"XSP|20261219|P|400.0": -5.0})

    position = _make_position(
        symbol="XSP",
        sec_type="OPT",
        position=-5,
        avg_cost=1850.0,
        strike=400,
        right="P",
        expiry="20261219",
    )
    client = SimpleNamespace(get_positions=lambda: [position])

    positions = ib_sync.fetch_positions(client, journal_basis_lookup=enriched)
    pos = positions[0]

    # entry_cost = abs(-8500.0) = 8500.0
    assert pos["entry_cost"] == pytest.approx(8500.0, abs=0.01), (
        "entry_cost = abs(basis) = abs(-8500) = 8500.0; "
        "removing abs() yields -8500 which is nonsensical"
    )
    # avg_cost = 8500.0 / abs(-5) = 1700.0
    assert pos["avgCost"] == pytest.approx(1700.0, abs=0.0001), (
        "avg_cost = abs(basis) / abs(position_size) = 8500 / 5 = 1700.0"
    )
