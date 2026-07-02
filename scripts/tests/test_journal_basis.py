#!/usr/bin/env python3
"""Regression tests for journal-derived open basis in ib_sync."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import ib_sync  # noqa: E402
from clients.journal_basis import (  # noqa: E402
    compute_open_basis_for_ticker,
    prior_net_qty_for_contract,
)


class _FakeCursor:
    """Mirrors the REAL libsql_experimental cursor (0.0.55): rows come back
    via fetchall(); there is NO .rows attribute. The old _FakeResult exposed
    .rows and let `result.rows` ship green while raising AttributeError on
    every production lookup (CTA-01)."""

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
    # Driver-faithful row shape: libsql fetchall() returns plain TUPLES in
    # SELECT order (payload, filled_at, written_at) — not dicts (CTA-01
    # layer 2: name-based access read every real row as empty).
    return (json.dumps(payload), filled_at, filled_at)


def _aaoi_rows() -> list[dict]:
    return [
        _journal_row(
            {
                "ticker": "AAOI",
                "action": "BUY_OPTION",
                "contracts": 50,
                "total_cost": 119038.46,
                "right": "C",
                "strike": 200,
                "expiry": "20260717",
            },
            "2026-05-19T10:01:00Z",
        ),
        _journal_row(
            {
                "ticker": "AAOI",
                "action": "BUY_OPTION",
                "contracts": 25,
                "total_cost": 59519.23,
                "right": "C",
                "strike": 200,
                "expiry": "20260717",
            },
            "2026-05-20T13:45:00Z",
        ),
        _journal_row(
            {
                "ticker": "AAOI",
                "action": "SELL_TO_OPEN",
                "contracts": 50,
                "total_cost": 119041.07,
                "right": "P",
                "strike": 150,
                "expiry": "20260717",
            },
            "2026-05-19T10:01:01Z",
        ),
        _journal_row(
            {
                "ticker": "AAOI",
                "action": "SELL_TO_OPEN",
                "contracts": 25,
                "total_cost": 59520.54,
                "right": "P",
                "strike": 150,
                "expiry": "20260717",
            },
            "2026-05-20T13:45:01Z",
        ),
        # Daily importer currently mislabels closes as SELL_TO_OPEN / BUY_OPTION.
        _journal_row(
            {
                "ticker": "AAOI",
                "action": "SELL_TO_OPEN",
                "contracts": 25,
                "total_cost": 35000.00,
                "right": "C",
                "strike": 200,
                "expiry": "20260717",
            },
            "2026-05-21T14:00:00Z",
        ),
        _journal_row(
            {
                "ticker": "AAOI",
                "action": "BUY_OPTION",
                "contracts": 25,
                "total_cost": 25000.00,
                "right": "P",
                "strike": 150,
                "expiry": "20260717",
            },
            "2026-05-21T14:00:01Z",
        ),
        _journal_row(
            {
                "ticker": "AAOI",
                "action": "SELL_TO_OPEN",
                "contracts": 25,
                "total_cost": 50000.00,
                "right": "C",
                "strike": 200,
                "expiry": "20260717",
            },
            "2026-05-21T15:10:00Z",
        ),
        _journal_row(
            {
                "ticker": "AAOI",
                "action": "BUY_OPTION",
                "contracts": 25,
                "total_cost": 25000.00,
                "right": "P",
                "strike": 150,
                "expiry": "20260717",
            },
            "2026-05-21T15:10:01Z",
        ),
    ]


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


def test_compute_open_basis_for_ticker_matches_remaining_aaoi_risk_reversal_basis():
    db = _FakeDb(_aaoi_rows())

    basis = compute_open_basis_for_ticker(db, "AAOI")

    assert db.calls, "expected a journal query"
    assert basis["AAOI|20260717|C|200.0"] == pytest.approx(59519.23, abs=0.01)
    assert basis["AAOI|20260717|P|150.0"] == pytest.approx(59520.54, abs=0.01)

    net_entry_cost = basis["AAOI|20260717|C|200.0"] - basis["AAOI|20260717|P|150.0"]
    avg_entry_per_contract = net_entry_cost / (25 * 100)

    assert avg_entry_per_contract == pytest.approx(0.0, abs=0.01)
    assert abs(net_entry_cost) < 5


class TestPriorNetQtyCutoff:
    """Retroactive-backfill time bound (2026-07-02 mislabel incident).

    Without a cutoff, prior_net_qty_for_contract sums ALL journal rows, so
    backfilling a 2026-06-25 opening sell while the 2026-06-26 closing buys
    already sat in the journal read prior qty +5 and flipped the label to
    SELL_OPTION. ``before`` bounds the scan to rows strictly earlier than
    the fill's DATE (10-char prefix): journal filled_at is date-only, so
    same-day rows are ambiguous and excluded by design — prior-day rows
    count, same-day-and-later rows do not.
    """

    _META_CONTRACT = {
        "ticker": "META",
        "sec_type": "OPT",
        "strike": 625.0,
        "right": "C",
        "expiry": "20260626",
    }

    def _closing_buy_row(self, filled_at: str) -> tuple:
        return _journal_row(
            {
                "ticker": "META",
                "action": "BUY_OPTION",
                "contracts": 5,
                "total_cost": 870.0,
                "right": "C",
                "strike": 625.0,
                "expiry": "20260626",
            },
            filled_at,
        )

    def test_no_cutoff_default_sums_all_rows_unchanged(self):
        db = _FakeDb([self._closing_buy_row("2026-06-26")])

        net = prior_net_qty_for_contract(db, **self._META_CONTRACT)

        assert net == pytest.approx(5.0)

    def test_cutoff_excludes_rows_filled_after_the_fill_date(self):
        db = _FakeDb([self._closing_buy_row("2026-06-26")])

        net = prior_net_qty_for_contract(
            db, before="2026-06-25T19:59:31Z", **self._META_CONTRACT
        )

        assert net == pytest.approx(0.0)

    def test_cutoff_includes_prior_day_date_only_rows(self):
        db = _FakeDb([self._closing_buy_row("2026-06-24")])

        net = prior_net_qty_for_contract(
            db, before="2026-06-25T19:59:31Z", **self._META_CONTRACT
        )

        assert net == pytest.approx(5.0)

    def test_cutoff_excludes_ambiguous_same_day_date_only_rows(self):
        # A date-only row on the fill's own day carries no intra-day order;
        # it is EXCLUDED — same-day sequencing is the backfill run's in-run
        # prior_state accumulation's job, not the DB seed's.
        db = _FakeDb([self._closing_buy_row("2026-06-25")])

        net = prior_net_qty_for_contract(
            db, before="2026-06-25T19:59:31Z", **self._META_CONTRACT
        )

        assert net == pytest.approx(0.0)

    def test_cutoff_mixed_rows_counts_only_strictly_earlier_days(self):
        db = _FakeDb(
            [
                self._closing_buy_row("2026-06-23"),
                self._closing_buy_row("2026-06-25"),
                self._closing_buy_row("2026-06-26"),
            ]
        )

        net = prior_net_qty_for_contract(
            db, before="2026-06-25T19:59:31Z", **self._META_CONTRACT
        )

        assert net == pytest.approx(5.0)

    def test_cutoff_excludes_rows_with_no_timestamp_at_all(self):
        # COALESCE(filled_at, written_at) is NULL: the row cannot be proven
        # prior to the fill, so a bounded scan leaves it out.
        db = _FakeDb([self._closing_buy_row(None)])

        net = prior_net_qty_for_contract(
            db, before="2026-06-25T19:59:31Z", **self._META_CONTRACT
        )

        assert net == pytest.approx(0.0)


def test_fetch_positions_and_collapse_positions_use_journal_basis_for_combo_entry_cost():
    journal_basis_lookup = {
        "AAOI|20260717|C|200.0": 59519.23,
        "AAOI|20260717|P|150.0": 59520.54,
    }
    client = SimpleNamespace(
        get_positions=lambda: [
            _make_position(
                symbol="AAOI",
                sec_type="OPT",
                position=25,
                avg_cost=2730.50,
                strike=200,
                right="C",
                expiry="20260717",
            ),
            _make_position(
                symbol="AAOI",
                sec_type="OPT",
                position=-25,
                avg_cost=2596.50,
                strike=150,
                right="P",
                expiry="20260717",
            ),
        ]
    )

    positions = ib_sync.fetch_positions(client, journal_basis_lookup=journal_basis_lookup)

    assert positions[0]["entry_cost"] == pytest.approx(59519.23, abs=0.01)
    assert positions[0]["avgCost"] == pytest.approx(2380.7692, abs=0.0001)
    assert positions[0]["ibAvgCost"] == pytest.approx(2730.50, abs=0.0001)
    assert positions[1]["entry_cost"] == pytest.approx(59520.54, abs=0.01)
    assert positions[1]["avgCost"] == pytest.approx(2380.8216, abs=0.0001)
    assert positions[1]["ibAvgCost"] == pytest.approx(2596.50, abs=0.0001)

    collapsed = ib_sync.collapse_positions(positions)
    combo = collapsed[0]

    assert combo["ticker"] == "AAOI"
    assert combo["contracts"] == 25
    assert combo["entry_cost"] == pytest.approx(-1.31, abs=0.01)
    assert combo["legs"][0]["ib_avg_cost"] == pytest.approx(2730.50, abs=0.0001)
    assert combo["legs"][1]["ib_avg_cost"] == pytest.approx(2596.50, abs=0.0001)
