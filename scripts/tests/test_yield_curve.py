"""fetch_yield_curve — 10Y-2Y Treasury spread ingestion (CURVE regime tab).

Fixture: scripts/tests/fixtures/yield_curve_2026_sample.csv is the real Treasury
daily par yield CSV for 2026 captured 2026-08-03 (146 data rows, descending in
the file, 01/02/2026 .. 07/31/2026). Expected values below were derived by
inspecting the fixture directly, not computed by hand.

Spec: docs/indicators/curve.md. DB access is monkeypatched — db.client refuses
real connections under PYTEST_CURRENT_TEST by design.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import timezone
from pathlib import Path

import pytest

import fetch_yield_curve as fyc

FIXTURE = Path(__file__).parent / "fixtures" / "yield_curve_2026_sample.csv"
MIGRATION = (
    Path(__file__).parent.parent / "db" / "migrations" / "0032_yield_curve.sql"
)

FIXTURE_TEXT = FIXTURE.read_text()


# ── parse_treasury_csv ──────────────────────────────────────────────


class TestParseTreasuryCsv:
    def test_parses_fixture_ascending(self):
        rows = fyc.parse_treasury_csv(FIXTURE_TEXT)
        assert len(rows) == 146
        assert rows[0]["date"] == "2026-01-02"
        assert rows[-1]["date"] == "2026-07-31"
        dates = [r["date"] for r in rows]
        assert dates == sorted(dates)

    def test_pins_fixture_endpoint_values(self):
        rows = fyc.parse_treasury_csv(FIXTURE_TEXT)
        first, last = rows[0], rows[-1]
        assert first["y2"] == pytest.approx(3.47)
        assert first["y10"] == pytest.approx(4.19)
        assert last["y2"] == pytest.approx(4.28)
        assert last["y10"] == pytest.approx(4.75)
        assert last["y3m"] == pytest.approx(3.83)

    def test_dates_are_iso(self):
        rows = fyc.parse_treasury_csv(FIXTURE_TEXT)
        for row in rows:
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["date"])

    def test_parses_by_header_name_not_position(self):
        # 1990-shaped header: 9 tenors, no 1-month column.
        text = (
            'Date,"3 Mo","6 Mo","1 Yr","2 Yr","3 Yr","5 Yr","7 Yr","10 Yr","30 Yr"\n'
            "12/31/1990,6.81,6.89,7.05,7.15,7.38,7.68,7.94,8.08,8.26\n"
            "01/02/1990,7.83,7.89,7.81,7.87,7.90,7.87,7.98,7.94,8.00\n"
        )
        rows = fyc.parse_treasury_csv(text)
        assert [r["date"] for r in rows] == ["1990-01-02", "1990-12-31"]
        assert rows[-1]["y2"] == pytest.approx(7.15)
        assert rows[-1]["y10"] == pytest.approx(8.08)
        assert rows[-1]["y3m"] == pytest.approx(6.81)

    def test_absent_tenor_column_reads_none(self):
        text = 'Date,"2 Yr","10 Yr"\n07/31/2026,4.28,4.75\n'
        rows = fyc.parse_treasury_csv(text)
        assert rows == [
            {"date": "2026-07-31", "y3m": None, "y2": 4.28, "y10": 4.75}
        ]

    def test_empty_cell_reads_none(self):
        # 2006-style mid-year gap: trailing empty cell for a tenor.
        text = 'Date,"3 Mo","2 Yr","10 Yr","30 Yr"\n01/31/2006,4.47,4.54,4.53,\n'
        rows = fyc.parse_treasury_csv(text)
        assert rows[0]["y2"] == pytest.approx(4.54)

    def test_rows_missing_y2_or_y10_are_skipped(self):
        text = (
            'Date,"2 Yr","10 Yr"\n'
            "07/31/2026,4.28,4.75\n"
            "07/30/2026,,4.68\n"
            "07/29/2026,4.21,\n"
        )
        rows = fyc.parse_treasury_csv(text)
        assert [r["date"] for r in rows] == ["2026-07-31"]


# ── build_series / merge_series / diff_new_rows ─────────────────────


def _raw(date, y2, y10, y3m=None):
    return {"date": date, "y3m": y3m, "y2": y2, "y10": y10}


class TestBuildSeries:
    def test_computes_spread_and_attaches_spx(self):
        rows = [_raw("2026-07-30", 4.23, 4.68), _raw("2026-07-31", 4.28, 4.75)]
        spx = {"2026-07-31": 7585.17}
        series = fyc.build_series(rows, spx)
        assert series[-1]["spread"] == pytest.approx(0.47)
        assert series[-1]["spx_close"] == pytest.approx(7585.17)
        assert series[0]["spx_close"] is None

    def test_no_spx_closes_leaves_nulls(self):
        series = fyc.build_series([_raw("2026-07-31", 4.28, 4.75)], None)
        assert series[0]["spx_close"] is None
        assert series[0]["spread"] == pytest.approx(0.47)


def _pt(date, y2, y10, spx=None):
    return {
        "date": date,
        "y3m": None,
        "y2": y2,
        "y10": y10,
        "spread": y10 - y2,
        "spx_close": spx,
    }


class TestMergeAndDiff:
    def test_fresh_rows_win_and_backfill_is_kept(self):
        cached = [_pt("1990-01-02", 7.87, 7.94), _pt("2026-07-30", 4.20, 4.60)]
        fresh = [_pt("2026-07-30", 4.23, 4.68), _pt("2026-07-31", 4.28, 4.75)]
        merged = fyc.merge_series(cached, fresh)
        assert [p["date"] for p in merged] == [
            "1990-01-02",
            "2026-07-30",
            "2026-07-31",
        ]
        by_date = {p["date"]: p for p in merged}
        assert by_date["2026-07-30"]["y10"] == pytest.approx(4.68)

    def test_diff_reports_new_and_changed_rows_only(self):
        cached = [_pt("2026-07-30", 4.23, 4.68)]
        same = fyc.diff_new_rows(cached, [_pt("2026-07-30", 4.23, 4.68)])
        assert same == []
        changed = fyc.diff_new_rows(
            cached,
            [_pt("2026-07-30", 4.23, 4.69), _pt("2026-07-31", 4.28, 4.75)],
        )
        assert sorted(p["date"] for p in changed) == [
            "2026-07-30",
            "2026-07-31",
        ]


# ── build_output ────────────────────────────────────────────────────


class TestBuildOutput:
    def test_payload_contract(self):
        series = [_pt("2026-07-30", 4.23, 4.68), _pt("2026-07-31", 4.28, 4.75)]
        payload = fyc.build_output(series)
        assert set(payload.keys()) == {
            "scan_time",
            "source",
            "count",
            "current",
            "series",
        }
        assert payload["source"] == "treasury"
        assert payload["count"] == 2
        assert payload["current"]["date"] == "2026-07-31"
        assert payload["current"]["spread"] == pytest.approx(0.47)
        assert payload["series"] == series

    def test_scan_time_is_tz_aware_utc(self):
        payload = fyc.build_output([_pt("2026-07-31", 4.28, 4.75)])
        from datetime import datetime

        parsed = datetime.fromisoformat(payload["scan_time"].replace("Z", "+00:00"))
        assert parsed.tzinfo is not None
        assert parsed.utcoffset() == timezone.utc.utcoffset(None)


# ── persist_result ──────────────────────────────────────────────────


@pytest.fixture()
def persist_calls(monkeypatch, tmp_path):
    calls: list[tuple] = []
    monkeypatch.setattr(fyc, "YIELD_CURVE_JSON", tmp_path / "yield_curve.json")
    monkeypatch.setattr(
        fyc.writer,
        "ensure_no_replica_for_writers",
        lambda: calls.append(("guard",)),
    )
    monkeypatch.setattr(
        fyc.writer,
        "upsert_yield_curve_rows",
        lambda rows, recorded_at: calls.append(("rows", len(rows))),
    )
    monkeypatch.setattr(
        fyc.writer,
        "upsert_scan_snapshot",
        lambda service, scan_time, payload: calls.append(("snapshot", service)),
    )
    monkeypatch.setattr(
        fyc.writer,
        "record_service_health",
        lambda service, state, finished_at=None: calls.append(
            ("health", service, state)
        ),
    )
    return calls


def _payload(series):
    return fyc.build_output(series)


class TestPersistResult:
    def test_refuses_empty_series(self, persist_calls):
        fyc.persist_result(_payload([]), [])
        assert persist_calls == []
        assert not fyc.YIELD_CURVE_JSON.exists()

    def test_changed_rows_write_everything_in_order(self, persist_calls):
        rows = [_pt("2026-07-31", 4.28, 4.75)]
        fyc.persist_result(_payload(rows), rows)
        assert persist_calls == [
            ("guard",),
            ("rows", 1),
            ("snapshot", "yield-curve"),
            ("health", "yield-curve", "ok"),
        ]
        assert fyc.YIELD_CURVE_JSON.exists()

    def test_unchanged_day_heartbeats_without_row_upserts(self, persist_calls):
        rows = [_pt("2026-07-31", 4.28, 4.75)]
        fyc.persist_result(_payload(rows), [])
        kinds = [c[0] for c in persist_calls]
        assert "rows" not in kinds
        assert ("snapshot", "yield-curve") in persist_calls
        assert ("health", "yield-curve", "ok") in persist_calls


# ── migration 0032 + writer upsert ──────────────────────────────────


class TestYieldCurveStorage:
    @pytest.fixture()
    def db(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT);"
        )
        conn.executescript(MIGRATION.read_text())
        yield conn
        conn.close()

    def test_migration_registers_version_32(self, db):
        versions = [r[0] for r in db.execute("SELECT version FROM schema_migrations")]
        assert versions == [32]

    def test_schema_columns(self, db):
        cols = [r[1] for r in db.execute("PRAGMA table_info(yield_curve_history)")]
        assert cols == ["date", "y3m", "y2", "y10", "spread", "recorded_at"]

    def test_upsert_is_idempotent_per_date(self, db):
        from db import writer

        args_old = ("2026-07-31", 3.83, 4.20, 4.60, 0.40, "2026-07-31T22:30:00Z")
        args_new = ("2026-07-31", 3.83, 4.28, 4.75, 0.47, "2026-08-01T22:30:00Z")
        db.execute(writer.YIELD_CURVE_UPSERT_SQL, args_old)
        db.execute(writer.YIELD_CURVE_UPSERT_SQL, args_new)
        rows = list(
            db.execute("SELECT date, y2, y10, spread FROM yield_curve_history")
        )
        assert rows == [("2026-07-31", 4.28, 4.75, 0.47)]
