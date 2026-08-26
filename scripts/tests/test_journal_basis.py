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
from clients import journal_basis  # noqa: E402
from clients.journal_basis import (  # noqa: E402
    compute_open_basis_and_net_qty_for_tickers,
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
        self._rows = [
            (f"test-{index:08d}", *row)
            for index, row in enumerate(rows, start=1)
        ]
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        cursor = str(params[0])
        tickers = {str(value) for value in params[1:-1]}
        limit = int(params[-1])
        matches = []
        for row in self._rows:
            trade_id, payload_json, _filled_at, _written_at = row
            payload = json.loads(payload_json)
            ticker = str(payload.get("ticker") or payload.get("symbol") or "").upper()
            if trade_id > cursor and ticker in tickers:
                matches.append(row)
        return _FakeCursor(matches[:limit])


class _CursorPagedDb:
    """Driver-faithful cursor fake for journal trade_id pagination."""

    def __init__(self, rows):
        self._rows = rows
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        assert "trade_id > ?" in sql
        assert "ORDER BY trade_id ASC" in sql
        assert "LIMIT ?" in sql

        cursor = str(params[0])
        tickers = {str(value) for value in params[1:-1]}
        limit = int(params[-1])
        matches = []
        for row in self._rows:
            trade_id, payload_json, _filled_at, _written_at = row
            payload = json.loads(payload_json)
            ticker = str(payload.get("ticker") or payload.get("symbol") or "").upper()
            if trade_id > cursor and ticker in tickers:
                matches.append(row)
        matches.sort(key=lambda row: row[0])
        return _FakeCursor(matches[:limit])


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


def test_build_journal_basis_lookup_batches_all_option_contracts_in_one_query():
    """The live-sync lookup must preserve legacy math without its journal N+1."""
    part_a = "0001de5f.69f22890.01.01"
    part_b = "0001de5f.69f23721.02.01"
    rows = [
        _journal_row(
            {
                "ticker": "WULF",
                "action": "BUY_OPTION",
                "contracts": 8,
                "total_cost": 800.0,
                "right": "C",
                "strike": 17,
                "expiry": "20270115",
                "ib_exec_id": part_a,
            },
            "2026-07-10T10:00:00Z",
        ),
        _journal_row(
            {
                "ticker": "NVDA",
                "action": "SELL_TO_OPEN",
                "contracts": 4,
                "total_cost": 2000.0,
                "open_basis": 1987.65432,
                "right": "P",
                "strike": 100,
                "expiry": "20261218",
                "ib_exec_id": "nvda-open",
            },
            "2026-07-10T10:00:01Z",
        ),
        _journal_row(
            {
                "ticker": "WULF",
                "action": "BUY_OPTION",
                "contracts": 69,
                "total_cost": 6900.0,
                "right": "C",
                "strike": 17,
                "expiry": "20270115",
                "ib_exec_id": part_b,
            },
            "2026-07-10T10:00:02Z",
        ),
        # The rehydrated composite duplicates the first two per-fill rows.
        _journal_row(
            {
                "ticker": "WULF",
                "action": "BUY_OPTION",
                "contracts": 77,
                "total_cost": 7777.0,
                "right": "C",
                "strike": 17,
                "expiry": "20270115",
                "ib_exec_id": f"{part_a}+{part_b}",
            },
            "2026-07-10T10:00:03Z",
        ),
        # A distinct contract proves per-contract net-quantity isolation.
        _journal_row(
            {
                "ticker": "WULF",
                "action": "BUY_OPTION",
                "contracts": 10,
                "total_cost": 1500.0,
                "right": "C",
                "strike": 20,
                "expiry": "20270115",
                "ib_exec_id": "wulf-20-open",
            },
            "2026-07-10T10:00:04Z",
        ),
        # Partial close leaves 52 contracts at the original $100 basis.
        _journal_row(
            {
                "ticker": "WULF",
                "action": "SELL_OPTION",
                "contracts": 25,
                "total_cost": 2500.0,
                "right": "C",
                "strike": 17,
                "expiry": "20270115",
                "ib_exec_id": "wulf-17-close",
            },
            "2026-07-11T10:00:00Z",
        ),
    ]
    positions = [
        _make_position(
            symbol="WULF",
            sec_type="OPT",
            position=52,
            avg_cost=101.0,
            strike=17,
            right="C",
            expiry="20270115",
        ),
        _make_position(
            symbol="WULF",
            sec_type="OPT",
            position=10,
            avg_cost=151.0,
            strike=20,
            right="C",
            expiry="20270115",
        ),
        _make_position(
            symbol="NVDA",
            sec_type="OPT",
            position=-4,
            avg_cost=510.0,
            strike=100,
            right="P",
            expiry="20261218",
        ),
    ]
    client = SimpleNamespace(get_positions=lambda: positions)

    legacy_db = _FakeDb(rows)
    legacy_basis = {}
    for ticker in ("NVDA", "WULF"):
        legacy_basis.update(compute_open_basis_for_ticker(legacy_db, ticker))
    legacy_net_qty = {
        "WULF|20270115|C|17.0": prior_net_qty_for_contract(
            legacy_db,
            ticker="WULF",
            sec_type="OPT",
            strike=17,
            right="C",
            expiry="20270115",
        ),
        "WULF|20270115|C|20.0": prior_net_qty_for_contract(
            legacy_db,
            ticker="WULF",
            sec_type="OPT",
            strike=20,
            right="C",
            expiry="20270115",
        ),
        "NVDA|20261218|P|100.0": prior_net_qty_for_contract(
            legacy_db,
            ticker="NVDA",
            sec_type="OPT",
            strike=100,
            right="P",
            expiry="20261218",
        ),
    }
    assert len(legacy_db.calls) == 5
    assert legacy_basis == {
        "NVDA|20261218|P|100.0": 1987.6543,
        "WULF|20270115|C|17.0": 5200.0,
        "WULF|20270115|C|20.0": 1500.0,
    }
    assert legacy_net_qty == {
        "WULF|20270115|C|17.0": 52.0,
        "WULF|20270115|C|20.0": 10.0,
        "NVDA|20261218|P|100.0": -4.0,
    }

    batched_db = _FakeDb(rows)
    batched = ib_sync.build_journal_basis_lookup(client, db=batched_db)

    assert len(batched_db.calls) == 1
    assert batched_db.calls[0][1] == ("", "NVDA", "WULF", 200)
    expected_bytes = json.dumps(
        {"basis": legacy_basis, "net_qty": legacy_net_qty},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    actual_bytes = json.dumps(
        {"basis": dict(batched), "net_qty": batched.net_qty},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert actual_bytes == expected_bytes


def test_batched_journal_basis_pages_by_trade_id_and_preserves_ordered_bytes(monkeypatch):
    """Each Hrana response is bounded without changing chronological semantics."""
    rows = [
        # trade_id order deliberately disagrees with effective-time order. The
        # close must still be the latest persisted basis after Python sorting.
        (
            "001-close-first-by-id",
            json.dumps({
                "ticker": "WULF",
                "action": "SELL_OPTION",
                "contracts": 5,
                "total_cost": 500.0,
                "open_basis": 500.0,
                "right": "C",
                "strike": 17,
                "expiry": "20270115",
                "ib_exec_id": "wulf-close",
            }),
            "2026-07-11T10:00:00Z",
            "2026-07-11T10:00:01Z",
        ),
        (
            "002-nvda",
            json.dumps({
                "ticker": "NVDA",
                "action": "SELL_TO_OPEN",
                "contracts": 4,
                "total_cost": 2000.0,
                "open_basis": 1987.65432,
                "right": "P",
                "strike": 100,
                "expiry": "20261218",
                "ib_exec_id": "nvda-open",
            }),
            "2026-07-10T10:00:01Z",
            "2026-07-10T10:00:02Z",
        ),
        (
            "003-unrelated",
            json.dumps({
                "ticker": "AAPL",
                "action": "BUY_OPTION",
                "contracts": 99,
                "total_cost": 9900.0,
                "right": "C",
                "strike": 200,
                "expiry": "20270115",
                "ib_exec_id": "unrelated",
            }),
            "2026-07-09T10:00:00Z",
            "2026-07-09T10:00:01Z",
        ),
        (
            "004-wulf-other-contract",
            json.dumps({
                "ticker": "WULF",
                "action": "BUY_OPTION",
                "contracts": 2,
                "total_cost": 300.0,
                "open_basis": 300.0,
                "right": "C",
                "strike": 20,
                "expiry": "20270115",
                "ib_exec_id": "wulf-20-open",
            }),
            "2026-07-10T10:00:02Z",
            "2026-07-10T10:00:03Z",
        ),
        (
            "999-open-last-by-id",
            json.dumps({
                "ticker": "WULF",
                "action": "BUY_OPTION",
                "contracts": 10,
                "total_cost": 1000.0,
                "open_basis": 1000.0,
                "right": "C",
                "strike": 17,
                "expiry": "20270115",
                "ib_exec_id": "wulf-open",
            }),
            "2026-07-10T10:00:00Z",
            "2026-07-10T10:00:01Z",
        ),
    ]
    kwargs = {
        "tickers": ("NVDA", "WULF"),
        "contract_keys": (
            "NVDA|20261218|P|100.0",
            "WULF|20270115|C|17.0",
            "WULF|20270115|C|20.0",
        ),
    }

    monkeypatch.setattr(journal_basis, "_JOURNAL_PAGE_SIZE", 100)
    single_page_db = _CursorPagedDb(rows)
    single_page = compute_open_basis_and_net_qty_for_tickers(single_page_db, **kwargs)

    monkeypatch.setattr(journal_basis, "_JOURNAL_PAGE_SIZE", 2)
    paged_db = _CursorPagedDb(rows)
    paged = compute_open_basis_and_net_qty_for_tickers(paged_db, **kwargs)

    expected_bytes = json.dumps(
        {
            "basis": {
                "NVDA|20261218|P|100.0": 1987.6543,
                "WULF|20270115|C|17.0": 500.0,
                "WULF|20270115|C|20.0": 300.0,
            },
            "net_qty": {
                "NVDA|20261218|P|100.0": -4.0,
                "WULF|20270115|C|17.0": 5.0,
                "WULF|20270115|C|20.0": 2.0,
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    single_page_bytes = json.dumps(
        {"basis": single_page[0], "net_qty": single_page[1]},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    paged_bytes = json.dumps(
        {"basis": paged[0], "net_qty": paged[1]},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()

    assert single_page_bytes == expected_bytes
    assert paged_bytes == expected_bytes
    assert len(single_page_db.calls) == 1
    assert len(paged_db.calls) == 3
    assert paged_db.calls[0][1] == ("", "NVDA", "WULF", 2)
    assert paged_db.calls[1][1] == ("002-nvda", "NVDA", "WULF", 2)
    assert paged_db.calls[2][1] == ("999-open-last-by-id", "NVDA", "WULF", 2)


def test_build_journal_basis_lookup_batch_failure_keeps_ib_fallback(capsys):
    class _FailingDb:
        def __init__(self):
            self.calls = 0

        def execute(self, _sql, _params=()):
            self.calls += 1
            raise RuntimeError("journal unavailable")

    position = _make_position(
        symbol="NVDA",
        sec_type="OPT",
        position=2,
        avg_cost=510.0,
        strike=100,
        right="C",
        expiry="20261218",
    )
    client = SimpleNamespace(get_positions=lambda: [position])
    db = _FailingDb()

    lookup = ib_sync.build_journal_basis_lookup(client, db=db)
    positions = ib_sync.fetch_positions(client, journal_basis_lookup=lookup)

    assert db.calls == 1
    assert dict(lookup) == {}
    assert lookup.net_qty == {}
    assert positions[0]["avgCost"] == pytest.approx(510.0)
    assert positions[0]["entry_cost"] == pytest.approx(1020.0)
    assert "falling back to IB avgCost" in capsys.readouterr().out
