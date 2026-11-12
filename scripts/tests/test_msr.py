"""MSR indicator: move surprise ratio (SPX, NDX, RUT) ingestion tests.

Ground truth is read from checked-in full Cboe files captured 2026-11-08:
  fixtures/msr_spx_sample.csv  DATE,SPX           1975-01-02..2026-11-06
  fixtures/msr_vix_sample.csv  DATE,OPEN..CLOSE   1990-01-02..2026-11-06
  fixtures/msr_vxn_sample.csv  DATE,OPEN..CLOSE   2009-09-14..2026-11-06
  fixtures/msr_rvx_sample.csv  DATE,OPEN..CLOSE   2009-09-16..2026-11-06
  fixtures/msr_rut_sample.csv  DATE,RUT           2020-11-27..2026-11-06
Expected numbers were derived by running an independent numpy computation
over the fixtures (2026-11-09) and match the spec pins in
docs/indicators/msr.md, never mental arithmetic. NDX has no Cboe file
(CDN 403), so its ladder tests use stubbed IB/Yahoo closes with
window-relative dates.
"""
from __future__ import annotations

import importlib
import json
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import fetch_msr as msr
from fetch_msr import (
    build_payload,
    compute_msr_rows,
    merge_series,
    parse_close_csv,
    percentile,
    rank_of,
    summarize_index,
)

FIXTURES = Path(__file__).parent / "fixtures"
SPX_CSV = (FIXTURES / "msr_spx_sample.csv").read_text()
VIX_CSV = (FIXTURES / "msr_vix_sample.csv").read_text()
VXN_CSV = (FIXTURES / "msr_vxn_sample.csv").read_text()
RVX_CSV = (FIXTURES / "msr_rvx_sample.csv").read_text()
RUT_CSV = (FIXTURES / "msr_rut_sample.csv").read_text()

MIGRATION = _SCRIPTS_DIR / "db" / "migrations" / "0111_msr.sql"
PRICE_HISTORY_MIGRATION = _SCRIPTS_DIR / "db" / "migrations" / "0029_rv_ratio.sql"

TOL = 1e-6


def _spx_rows():
    return compute_msr_rows(parse_close_csv(SPX_CSV, "SPX"), parse_close_csv(VIX_CSV, "CLOSE"))


def _rut_rows():
    return compute_msr_rows(parse_close_csv(RUT_CSV, "RUT"), parse_close_csv(RVX_CSV, "CLOSE"))


# ── Cboe CSV parsing ──────────────────────────────────────────────


class TestParseCloseCsv:
    def test_spx_fixture_count_and_bounds(self):
        closes = parse_close_csv(SPX_CSV, "SPX")
        assert len(closes) == 13072
        assert closes["1975-01-02"] == 70.23
        assert max(closes) == "2026-11-06"
        assert closes["2026-11-06"] == 7376.86

    def test_vix_fixture_reads_close_column(self):
        closes = parse_close_csv(VIX_CSV, "CLOSE")
        assert len(closes) == 9311
        assert min(closes) == "1990-01-02"
        assert closes["1990-01-02"] == 17.24
        assert closes["2026-11-05"] == 28.18  # CLOSE, not OPEN 17.14 / HIGH 28.36
        assert closes["2026-11-06"] == 25.64

    def test_vxn_and_rvx_fixture_bounds(self):
        vxn = parse_close_csv(VXN_CSV, "CLOSE")
        rvx = parse_close_csv(RVX_CSV, "CLOSE")
        assert (len(vxn), min(vxn), vxn["2009-09-14"]) == (4317, "2009-09-14", 24.93)
        assert (len(rvx), min(rvx), rvx["2009-09-16"]) == (4308, "2009-09-16", 28.86)
        assert vxn["2026-11-06"] == 34.02
        assert rvx["2026-11-06"] == 26.16

    def test_rut_fixture_bounds(self):
        closes = parse_close_csv(RUT_CSV, "RUT")
        assert len(closes) == 1493
        assert closes["2020-11-27"] == 1855.2698
        assert closes["2026-11-06"] == 2754.1054

    def test_keys_are_iso_dates(self):
        keys = list(parse_close_csv(RUT_CSV, "RUT"))
        assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", k) for k in keys)

    def test_skips_header_malformed_and_non_positive_rows(self):
        text = (
            "DATE,SPX\n"
            "11/05/2026,7353.73\n"
            "not-a-date,1\n"
            "11/06/2026,\n"
            "11/07/2026,0\n"
            "11/08/2026,-3\n"
            "11/09/2026,abc\n"
        )
        assert parse_close_csv(text, "SPX") == {"2026-11-05": 7353.73}


# ── MSR math ──────────────────────────────────────────────────────


def _c(pairs):
    return dict(pairs)


class TestComputeMsrRows:
    def test_divides_abs_move_by_prior_vol_close(self):
        rows = compute_msr_rows(
            _c([("2026-11-02", 100.0), ("2026-11-03", 98.0)]),
            _c([("2026-11-02", 20.0), ("2026-11-03", 50.0)]),  # day-t vol must NOT be used
            start="2026-11-01",
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["date"] == "2026-11-03"
        assert row["close"] == 98.0
        assert row["iv_prior"] == 20.0
        assert row["move_pct"] == pytest.approx(-2.0, abs=1e-9)
        assert row["msr"] == pytest.approx(0.1, abs=1e-9)  # |-2.0| / 20

    def test_restricted_to_common_dates(self):
        rows = compute_msr_rows(
            _c([("2026-11-02", 100.0), ("2026-11-03", 101.0), ("2026-11-04", 102.01)]),
            _c([("2026-11-02", 20.0), ("2026-11-04", 10.0)]),  # no 11-03 vol
            start="2026-11-01",
        )
        # prior common session of 11-04 is 11-02 (11-03 is not common)
        assert [r["date"] for r in rows] == ["2026-11-04"]
        assert rows[0]["move_pct"] == pytest.approx(2.01, abs=1e-9)
        assert rows[0]["iv_prior"] == 20.0

    def test_prior_session_may_predate_start(self):
        closes = _c([("2007-11-07", 100.0), ("2007-11-08", 101.0), ("2007-11-09", 100.0)])
        vols = _c([("2007-11-07", 25.0), ("2007-11-08", 20.0), ("2007-11-09", 30.0)])
        rows = compute_msr_rows(closes, vols, start="2007-11-08")
        assert [r["date"] for r in rows] == ["2007-11-08", "2007-11-09"]
        assert rows[0]["iv_prior"] == 25.0
        assert rows[0]["msr"] == pytest.approx(1.0 / 25.0, abs=1e-9)

    def test_start_is_inclusive_and_strict(self):
        closes = _c([("2007-11-06", 99.0), ("2007-11-07", 100.0), ("2007-11-08", 101.0)])
        vols = _c([("2007-11-06", 20.0), ("2007-11-07", 25.0), ("2007-11-08", 20.0)])
        rows = compute_msr_rows(closes, vols, start="2007-11-08")
        assert [r["date"] for r in rows] == ["2007-11-08"]

    def test_default_start_is_gs_chart_start(self):
        assert msr.START_DATE == "2007-11-08"
        assert _spx_rows()[0]["date"] == msr.START_DATE

    def test_empty_inputs_yield_no_rows(self):
        assert compute_msr_rows({}, {"2026-11-03": 20.0}) == []
        assert compute_msr_rows({"2026-11-03": 100.0}, {}) == []

    def test_spx_fixture_bounds(self):
        rows = _spx_rows()
        assert len(rows) == 4779
        assert [r["date"] for r in rows] == sorted(r["date"] for r in rows)
        first = rows[0]
        assert first["date"] == "2007-11-08"
        assert first["close"] == 1474.77
        assert first["iv_prior"] == 26.49  # VIX close 2007-11-07, before START_DATE
        assert first["msr"] == pytest.approx(0.002175, abs=TOL)
        assert first["move_pct"] == pytest.approx(-0.057603, abs=TOL)
        last = rows[-1]
        assert last["date"] == "2026-11-06"
        assert last["close"] == 7376.86
        assert last["iv_prior"] == 28.18
        assert last["move_pct"] == pytest.approx(0.314534, abs=TOL)
        assert last["msr"] == pytest.approx(0.011162, abs=TOL)

    def test_rut_fixture_bounds(self):
        rows = _rut_rows()
        assert len(rows) == 1492
        assert rows[0]["date"] == "2020-11-30"
        assert rows[0]["msr"] == pytest.approx(0.066147, abs=TOL)
        assert rows[-1]["date"] == "2026-11-06"
        assert rows[-1]["msr"] == pytest.approx(0.030719, abs=TOL)


class TestRankAndPercentile:
    def test_rank_one_is_largest(self):
        assert rank_of(0.9, [0.1, 0.9, 0.5]) == 1
        assert rank_of(0.1, [0.1, 0.9, 0.5]) == 3

    def test_ties_share_rank(self):
        values = [0.7, 0.5, 0.5, 0.1]
        assert rank_of(0.5, values) == 2
        assert rank_of(0.1, values) == 4  # 1 + three strictly larger

    def test_percentile_linear_interpolation(self):
        # numpy default: index (n-1)*q
        assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == pytest.approx(2.5)
        assert percentile([4.0, 1.0, 3.0, 2.0], 0.9) == pytest.approx(3.7)
        assert percentile([5.0], 0.99) == pytest.approx(5.0)


# ── summarize_index ───────────────────────────────────────────────


class TestSummarizeIndex:
    def test_spx_fixture_pins(self):
        summary = summarize_index(_spx_rows())
        current = summary["current"]
        assert current["date"] == "2026-11-06"
        assert current["msr"] == pytest.approx(0.011162, abs=TOL)
        assert current["move_pct"] == pytest.approx(0.314534, abs=TOL)
        assert current["close"] == 7376.86
        assert current["iv_prior"] == 28.18
        assert current["rank"] == 3736

        stats = summary["stats"]
        assert stats["count"] == 4779
        assert stats["max"] == pytest.approx(0.262231, abs=TOL)
        assert stats["max_date"] == "2026-11-05"
        assert stats["p50"] == pytest.approx(0.029424, abs=TOL)
        assert stats["p90"] == pytest.approx(0.081531, abs=TOL)
        assert stats["p99"] == pytest.approx(0.149252, abs=TOL)

        week = summary["week_max"]
        assert week["date"] == "2026-11-05"
        assert week["msr"] == pytest.approx(0.262231, abs=TOL)
        assert week["move_pct"] == pytest.approx(-4.355658, abs=TOL)
        assert week["iv_prior"] == 16.61
        assert week["rank"] == 1

    def test_spx_top_ten_order(self):
        top = summarize_index(_spx_rows())["top"]
        assert len(top) == 10
        assert [t["date"] for t in top[:5]] == [
            "2026-11-05", "2008-09-29", "2018-02-05", "2025-04-03", "2020-06-11",
        ]
        assert [t["msr"] for t in top[:5]] == pytest.approx(
            [0.262231, 0.253577, 0.236737, 0.224991, 0.213798], abs=TOL
        )
        assert top[1]["move_pct"] == pytest.approx(-8.809251, abs=TOL)
        assert set(top[0]) == {"date", "msr", "move_pct"}

    def test_rut_fixture_pins(self):
        summary = summarize_index(_rut_rows())
        assert summary["stats"]["count"] == 1492
        assert summary["current"]["date"] == "2026-11-06"
        assert summary["current"]["rank"] == 835
        assert [t["date"] for t in summary["top"][:3]] == ["2023-11-14", "2025-04-03", "2026-11-05"]
        assert [t["msr"] for t in summary["top"][:3]] == pytest.approx(
            [0.263688, 0.258181, 0.225762], abs=TOL
        )
        week = summary["week_max"]
        assert week["date"] == "2026-11-05"
        assert week["msr"] == pytest.approx(0.225762, abs=TOL)
        assert week["rank"] == 3

    def test_week_max_uses_trailing_five_rows_only(self):
        rows = [
            {"date": f"2026-10-{d:02d}", "close": 1.0, "iv_prior": 20.0, "move_pct": m, "msr": v}
            for d, v, m in [(1, 0.9, 9.0), (2, 0.1, 1.0), (5, 0.2, 2.0), (6, 0.4, -4.0),
                            (7, 0.3, 3.0), (8, 0.05, 0.5)]
        ]
        week = summarize_index(rows)["week_max"]
        assert week["date"] == "2026-10-06"
        assert week["rank"] == 2  # 0.9 (outside the window) is larger

    def test_top_ties_break_date_desc(self):
        rows = [
            {"date": "2026-10-01", "close": 1.0, "iv_prior": 20.0, "move_pct": 1.0, "msr": 0.5},
            {"date": "2026-10-02", "close": 1.0, "iv_prior": 20.0, "move_pct": -1.0, "msr": 0.5},
            {"date": "2026-10-05", "close": 1.0, "iv_prior": 20.0, "move_pct": 2.0, "msr": 0.7},
        ]
        top = summarize_index(rows)["top"]
        assert [t["date"] for t in top] == ["2026-10-05", "2026-10-02", "2026-10-01"]

    def test_empty_rows(self):
        summary = summarize_index([])
        assert summary["current"] is None
        assert summary["week_max"] is None
        assert summary["stats"] is None
        assert summary["top"] == []


# ── merge_series / build_payload ──────────────────────────────────


def _row(d, v, m=1.0):
    return {"date": d, "close": 100.0, "iv_prior": 20.0, "move_pct": m, "msr": v}


class TestMergeSeries:
    def test_union_of_dates_with_nulls(self):
        merged = merge_series({
            "SPX": [_row("2026-11-02", 0.1), _row("2026-11-03", 0.2)],
            "NDX": [],
            "RUT": [_row("2026-11-03", 0.3), _row("2026-11-04", 0.4)],
        })
        assert merged == [
            {"date": "2026-11-02", "spx": 0.1, "ndx": None, "rut": None},
            {"date": "2026-11-03", "spx": 0.2, "ndx": None, "rut": 0.3},
            {"date": "2026-11-04", "spx": None, "ndx": None, "rut": 0.4},
        ]

    def test_rounds_msr_to_six_places(self):
        merged = merge_series({"SPX": [_row("2026-11-02", 0.0021745151339064)], "NDX": [], "RUT": []})
        assert merged[0]["spx"] == 0.002175

    def test_missing_index_key_reads_as_null(self):
        merged = merge_series({"SPX": [_row("2026-11-02", 0.1)]})
        assert merged == [{"date": "2026-11-02", "spx": 0.1, "ndx": None, "rut": None}]

    def test_spx_fixture_first_row(self):
        merged = merge_series({"SPX": _spx_rows(), "NDX": [], "RUT": _rut_rows()})
        assert merged[0] == {"date": "2007-11-08", "spx": 0.002175, "ndx": None, "rut": None}
        assert merged[-1]["date"] == "2026-11-06"
        assert merged[-1]["rut"] == 0.030719


STAMPS = {
    "spx": "Sat, 07 Nov 2026 02:11:00 GMT",
    "vix": "Sat, 07 Nov 2026 02:21:00 GMT",
    "vxn": "Fri, 06 Nov 2026 22:31:00 GMT",
    "rvx": "Fri, 06 Nov 2026 22:31:00 GMT",
}


class TestBuildPayload:
    def _payload(self, ndx_rows=None):
        return build_payload(
            scan_time="2026-11-09T03:00:00Z",
            rows_by_index={"SPX": _spx_rows(), "NDX": ndx_rows or [], "RUT": _rut_rows()},
            source_last_modified=STAMPS,
            close_sources={"SPX": ["cboe"], "NDX": [], "RUT": ["cboe"]},
        )

    def test_top_level_contract(self):
        payload = self._payload()
        assert set(payload) == {
            "scan_time", "start_date", "source_last_modified", "close_sources", "indexes", "series",
        }
        assert payload["scan_time"] == "2026-11-09T03:00:00Z"
        assert payload["start_date"] == "2007-11-08"
        assert payload["source_last_modified"] == STAMPS
        assert payload["close_sources"] == {"SPX": ["cboe"], "NDX": [], "RUT": ["cboe"]}
        assert set(payload["indexes"]) == {"SPX", "NDX", "RUT"}

    def test_iv_symbol_mapping(self):
        indexes = self._payload()["indexes"]
        assert indexes["SPX"]["iv_symbol"] == "VIX"
        assert indexes["NDX"]["iv_symbol"] == "VXN"
        assert indexes["RUT"]["iv_symbol"] == "RVX"

    def test_index_summary_keys_and_six_dp_rounding(self):
        spx = self._payload()["indexes"]["SPX"]
        assert set(spx) == {"iv_symbol", "current", "week_max", "stats", "top"}
        assert spx["current"] == {
            "date": "2026-11-06", "msr": 0.011162, "move_pct": 0.314534,
            "close": 7376.86, "iv_prior": 28.18, "rank": 3736,
        }
        assert spx["week_max"] == {
            "date": "2026-11-05", "msr": 0.262231, "move_pct": -4.355658,
            "iv_prior": 16.61, "rank": 1,
        }
        assert spx["stats"] == {
            "count": 4779, "max": 0.262231, "max_date": "2026-11-05",
            "p50": 0.029424, "p90": 0.081531, "p99": 0.149252,
        }
        assert spx["top"][0] == {"date": "2026-11-05", "msr": 0.262231, "move_pct": -4.355658}

    def test_missing_index_emits_null_current_and_empty_top(self):
        ndx = self._payload()["indexes"]["NDX"]
        assert ndx["iv_symbol"] == "VXN"
        assert ndx["current"] is None
        assert ndx["top"] == []

    def test_series_is_merged_and_json_safe(self):
        payload = self._payload()
        assert payload["series"][0] == {"date": "2007-11-08", "spx": 0.002175, "ndx": None, "rut": None}
        assert len(payload["series"]) == 4779  # RUT dates are a subset of SPX dates
        json.dumps(payload, allow_nan=False)


# ── Migration + writer (sqlite3 stand-in for libsql) ──────────────


def _split_statements(sql: str) -> list[str]:
    stripped = "\n".join(re.sub(r"^\s*--.*$", "", line) for line in sql.splitlines())
    return [s.strip() for s in re.split(r";\s*$", stripped, flags=re.MULTILINE) if s.strip()]


def _bootstrap(conn: sqlite3.Connection, *migrations: Path) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    for path in migrations:
        for stmt in _split_statements(path.read_text(encoding="utf-8")):
            conn.execute(stmt)
    conn.commit()


@pytest.fixture
def sqlite_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[sqlite3.Connection]:
    """In-memory sqlite with 0029 (price_history_daily) + 0111 (msr_history),
    wired in as db.client.get_db the way test_rv_ratio_history.py does."""
    conn = sqlite3.connect(":memory:")
    _bootstrap(conn, PRICE_HISTORY_MIGRATION, MIGRATION)

    import db.client as client_mod
    monkeypatch.setattr(client_mod, "_cached", conn, raising=False)
    monkeypatch.setattr(client_mod, "get_db", lambda: conn)
    import db.writer as writer_mod
    importlib.reload(writer_mod)
    try:
        yield conn
    finally:
        conn.close()


class TestMsrStorage:
    def _db(self):
        conn = sqlite3.connect(":memory:")
        _bootstrap(conn, MIGRATION)
        return conn

    def test_migration_registers_version_111(self):
        db = self._db()
        assert db.execute("SELECT version FROM schema_migrations").fetchall() == [(111,)]

    def test_schema_columns_and_primary_key(self):
        db = self._db()
        info = {r[1]: r for r in db.execute("PRAGMA table_info(msr_history)")}
        assert set(info) == {
            "index_symbol", "date", "close", "iv_prior", "move_pct", "msr", "recorded_at",
        }
        pk = sorted((r[5], r[1]) for r in info.values() if r[5] > 0)
        assert pk == [(1, "index_symbol"), (2, "date")]
        assert all(info[c][3] == 1 for c in info)  # every column NOT NULL

    def test_date_index_exists(self):
        db = self._db()
        names = {r[1] for r in db.execute("PRAGMA index_list(msr_history)")}
        assert "idx_msr_history_date" in names

    def test_migration_is_rerunnable(self):
        db = self._db()
        for stmt in _split_statements(MIGRATION.read_text(encoding="utf-8")):
            db.execute(stmt)
        assert db.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 1

    def test_upsert_sql_is_idempotent_per_index_and_date(self):
        from db import writer

        db = self._db()
        db.execute(writer.MSR_UPSERT_SQL,
                   ("SPX", "2026-11-05", 7353.73, 16.61, -4.355658, 0.262231, "2026-11-06T03:00:00Z"))
        db.execute(writer.MSR_UPSERT_SQL,
                   ("SPX", "2026-11-05", 7353.73, 16.61, -4.355658, 0.262232, "2026-11-07T03:00:00Z"))
        db.execute(writer.MSR_UPSERT_SQL,
                   ("RUT", "2026-11-05", 2729.8867, 19.70, -4.447506, 0.225762, "2026-11-07T03:00:00Z"))
        rows = db.execute(
            "SELECT index_symbol, date, msr, recorded_at FROM msr_history ORDER BY index_symbol"
        ).fetchall()
        assert rows == [
            ("RUT", "2026-11-05", 0.225762, "2026-11-07T03:00:00Z"),
            ("SPX", "2026-11-05", 0.262232, "2026-11-07T03:00:00Z"),
        ]

    def test_upsert_msr_rows_writes_full_series_across_chunks(self, sqlite_db):
        import db.writer as writer

        rows = _spx_rows()
        writer.upsert_msr_rows("SPX", rows, "2026-11-09T03:00:00Z")
        writer.upsert_msr_rows("SPX", rows[-3:], "2026-11-10T03:00:00Z")
        count, recorded = sqlite_db.execute(
            "SELECT COUNT(*), MAX(recorded_at) FROM msr_history WHERE index_symbol = 'SPX'"
        ).fetchone()
        assert count == 4779
        assert recorded == "2026-11-10T03:00:00Z"
        last = sqlite_db.execute(
            "SELECT close, iv_prior, msr FROM msr_history WHERE index_symbol='SPX' AND date='2026-11-06'"
        ).fetchone()
        assert last[0] == 7376.86
        assert last[1] == 28.18
        assert last[2] == pytest.approx(0.011162, abs=TOL)

    def test_upsert_msr_rows_noop_on_empty(self, sqlite_db):
        import db.writer as writer

        writer.upsert_msr_rows("NDX", [], "2026-11-09T03:00:00Z")
        assert sqlite_db.execute("SELECT COUNT(*) FROM msr_history").fetchone()[0] == 0


# ── NDX / RUT close ladder (IB → Cboe(RUT) → Yahoo) ───────────────


def _session(days_back: int) -> str:
    """Window-relative session date (today-minus-N), never hardcoded."""
    return (date.today() - timedelta(days=days_back)).isoformat()


LAST_SESSION = _session(1)


def _close_for(day: str) -> float:
    """Deterministic close per date so every tier agrees on overlaps."""
    return 1000.0 + date.fromisoformat(day).toordinal() % 97


def _span(first: str, last: str) -> dict[str, float]:
    start, end = date.fromisoformat(first), date.fromisoformat(last)
    return {
        (start + timedelta(days=i)).isoformat(): _close_for((start + timedelta(days=i)).isoformat())
        for i in range((end - start).days + 1)
    }


def _seed(conn: sqlite3.Connection, symbol: str, closes: dict[str, float], source: str) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO price_history_daily (symbol, date, close, source, fetched_at)"
        " VALUES (?, ?, ?, ?, 'seed')",
        [(symbol, d, c, source) for d, c in closes.items()],
    )
    conn.commit()


def _stored(conn: sqlite3.Connection, symbol: str) -> dict[str, tuple[float, str]]:
    rows = conn.execute(
        "SELECT date, close, source FROM price_history_daily WHERE symbol = ? ORDER BY date", (symbol,)
    ).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


@pytest.fixture
def ladder(monkeypatch: pytest.MonkeyPatch, sqlite_db):
    """Pins the session, trips every fetcher unless a test overrides it."""
    monkeypatch.setattr(msr, "last_completed_session_date", lambda *a, **k: LAST_SESSION)
    monkeypatch.setattr(msr, "_ib_auth_state", lambda: "authenticated")
    calls: list[tuple] = []

    def _trip(name):
        def _boom(*args, **kwargs):
            raise AssertionError(f"{name} must not be called in this test")
        return _boom

    monkeypatch.setattr(msr, "_fetch_ib_daily", _trip("_fetch_ib_daily"))
    monkeypatch.setattr(msr, "_fetch_cboe_daily", _trip("_fetch_cboe_daily"))
    monkeypatch.setattr(msr, "_fetch_yahoo_daily", _trip("_fetch_yahoo_daily"))

    class Ladder:
        db = sqlite_db

        def stub(self, name, result):
            def _fetch(symbol, *args, **kwargs):
                calls.append((name, symbol, args, kwargs))
                return dict(result)
            monkeypatch.setattr(msr, name, _fetch)

        @property
        def calls(self):
            return calls

    return Ladder()


def _ib_durations(calls) -> list[str]:
    out = []
    for name, _symbol, args, kwargs in calls:
        if name == "_fetch_ib_daily":
            out.append(kwargs.get("duration", args[0] if args else None))
    return out


class TestIndexCloseLadder:
    def test_depth_target_constant(self):
        assert msr.INDEX_DEPTH_TARGET == "2009-09-11"

    def test_empty_store_deep_seeds_from_ib(self, ladder):
        deep = _span("2009-09-01", LAST_SESSION)
        ladder.stub("_fetch_ib_daily", deep)
        ladder.stub("_fetch_cboe_daily", {})
        ladder.stub("_fetch_yahoo_daily", {})

        closes, sources, new_count = msr.ensure_index_closes("RUT")

        assert _ib_durations(ladder.calls) == ["20 Y"]
        stored = _stored(ladder.db, "RUT")
        assert len(stored) == len(deep) == new_count
        assert {src for _c, src in stored.values()} == {"ib"}
        assert closes == deep
        assert sources == ["ib"]

    def test_shallow_store_triggers_deep_seed(self, ladder):
        _seed(ladder.db, "NDX", _span(_session(30), LAST_SESSION), "yahoo")
        ladder.stub("_fetch_ib_daily", _span("2009-09-01", LAST_SESSION))
        ladder.stub("_fetch_yahoo_daily", {})

        closes, _sources, new_count = msr.ensure_index_closes("NDX")

        assert _ib_durations(ladder.calls) == ["20 Y"]
        assert min(closes) == "2009-09-01"
        stored = _stored(ladder.db, "NDX")
        # stored rows win: the pre-existing window keeps its yahoo label
        assert stored[LAST_SESSION][1] == "yahoo"
        assert stored["2009-09-01"][1] == "ib"
        assert new_count == len(stored) - 30

    def test_rut_ib_empty_falls_to_cboe_then_yahoo(self, ladder):
        cboe = _span("2020-11-27", LAST_SESSION)
        yahoo = _span("2009-09-01", LAST_SESSION)
        ladder.stub("_fetch_ib_daily", {})
        ladder.stub("_fetch_cboe_daily", cboe)
        ladder.stub("_fetch_yahoo_daily", yahoo)

        closes, sources, _new = msr.ensure_index_closes("RUT")

        order = [c[0] for c in ladder.calls]
        assert order == ["_fetch_ib_daily", "_fetch_cboe_daily", "_fetch_yahoo_daily"]
        stored = _stored(ladder.db, "RUT")
        assert stored["2020-11-27"][1] == "cboe"
        assert stored[LAST_SESSION][1] == "cboe"
        assert stored["2009-09-01"][1] == "yahoo"
        assert stored["2020-11-26"][1] == "yahoo"
        assert sorted(sources) == ["cboe", "yahoo"]
        assert closes == yahoo

    def test_ndx_never_asks_cboe(self, ladder):
        yahoo = _span("2009-09-01", LAST_SESSION)
        ladder.stub("_fetch_ib_daily", {})
        ladder.stub("_fetch_yahoo_daily", yahoo)

        closes, sources, _new = msr.ensure_index_closes("NDX")  # cboe tripwire stays armed

        assert [c[0] for c in ladder.calls] == ["_fetch_ib_daily", "_fetch_yahoo_daily"]
        assert closes == yahoo
        assert sources == ["yahoo"]

    def test_incremental_ib_one_year_first_non_empty_wins(self, ladder):
        _seed(ladder.db, "RUT", _span("2009-09-01", _session(6)), "ib")
        ladder.stub("_fetch_ib_daily", _span(_session(300), LAST_SESSION))

        closes, _sources, new_count = msr.ensure_index_closes("RUT")  # cboe/yahoo trip

        assert _ib_durations(ladder.calls) == ["1 Y"]
        assert max(closes) == LAST_SESSION
        assert new_count == 5

    def test_incremental_ib_empty_rut_uses_cboe(self, ladder):
        _seed(ladder.db, "RUT", _span("2009-09-01", _session(6)), "ib")
        ladder.stub("_fetch_ib_daily", {})
        ladder.stub("_fetch_cboe_daily", _span("2020-11-27", LAST_SESSION))

        closes, _sources, new_count = msr.ensure_index_closes("RUT")  # yahoo trips

        assert max(closes) == LAST_SESSION
        assert new_count == 5
        assert _stored(ladder.db, "RUT")[LAST_SESSION][1] == "cboe"

    def test_incremental_ndx_ib_empty_uses_two_years_of_yahoo(self, ladder):
        _seed(ladder.db, "NDX", _span("2009-09-01", _session(6)), "ib")
        ladder.stub("_fetch_ib_daily", {})
        ladder.stub("_fetch_yahoo_daily", _span(_session(700), LAST_SESSION))

        closes, _sources, new_count = msr.ensure_index_closes("NDX")

        yahoo_calls = [c for c in ladder.calls if c[0] == "_fetch_yahoo_daily"]
        assert len(yahoo_calls) == 1
        _n, _sym, args, kwargs = yahoo_calls[0]
        assert kwargs.get("years", args[0] if args else None) == 2
        assert max(closes) == LAST_SESSION
        assert new_count == 5

    def test_never_stores_bars_after_last_completed_session(self, ladder):
        _seed(ladder.db, "NDX", _span("2009-09-01", _session(6)), "ib")
        ladder.stub("_fetch_ib_daily", _span(_session(300), _session(0)))  # includes today

        closes, _sources, new_count = msr.ensure_index_closes("NDX")

        stored = _stored(ladder.db, "NDX")
        assert _session(0) not in stored
        assert _session(0) not in closes
        assert max(stored) == LAST_SESSION
        assert new_count == 5

    def test_current_store_fetches_nothing(self, ladder):
        _seed(ladder.db, "RUT", _span("2009-09-01", LAST_SESSION), "ib")

        closes, sources, new_count = msr.ensure_index_closes("RUT")  # every fetcher trips

        assert ladder.calls == []
        assert new_count == 0
        assert max(closes) == LAST_SESSION
        assert sources == ["ib"]

    def test_unauthenticated_gateway_skips_ib(self, ladder, monkeypatch):
        monkeypatch.setattr(msr, "_ib_auth_state", lambda: "awaiting_2fa")
        _seed(ladder.db, "NDX", _span("2009-09-01", _session(6)), "ib")
        ladder.stub("_fetch_yahoo_daily", _span(_session(700), LAST_SESSION))

        closes, _sources, new_count = msr.ensure_index_closes("NDX")  # IB tripwire armed

        assert max(closes) == LAST_SESSION
        assert new_count == 5

    def test_unknown_auth_state_still_tries_ib(self, ladder, monkeypatch):
        monkeypatch.setattr(msr, "_ib_auth_state", lambda: None)
        _seed(ladder.db, "NDX", _span("2009-09-01", _session(6)), "ib")
        ladder.stub("_fetch_ib_daily", _span(_session(300), LAST_SESSION))

        msr.ensure_index_closes("NDX")

        assert _ib_durations(ladder.calls) == ["1 Y"]


# ── run(): four-file conditional GET + persistence ────────────────


class _StubClient:
    """CboeClient.fetch_history(symbol, if_modified_since) stub.

    Returns (None, stamp) when the caller's If-Modified-Since equals the
    current stamp and ``unchanged`` is set, else the full text."""

    def __init__(self, texts, stamps, unchanged=False):
        self._texts = texts
        self._stamps = stamps
        self._unchanged = unchanged
        self.seen_if_modified_since: dict[str, list] = {}

    def fetch_history(self, symbol, if_modified_since=None):
        self.seen_if_modified_since.setdefault(symbol, []).append(if_modified_since)
        stamp = self._stamps[symbol.lower()]
        if self._unchanged and if_modified_since == stamp:
            return None, stamp
        return self._texts[symbol], stamp


FULL_TEXTS = {"SPX": SPX_CSV, "VIX": VIX_CSV, "VXN": VXN_CSV, "RVX": RVX_CSV}


class TestRun:
    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        from db import writer

        monkeypatch.setattr(msr, "MSR_JSON", tmp_path / "msr.json")
        self.calls: list[tuple] = []

        def _rec(name):
            return lambda *args, **kwargs: self.calls.append((name, args, kwargs))

        for name in ("upsert_msr_rows",
                     "upsert_scan_snapshot", "record_service_health",
                     "upsert_price_history_rows"):
            monkeypatch.setattr(writer, name, _rec(name))

        self.index_closes = {
            "NDX": ({}, [], 0),
            "RUT": (parse_close_csv(RUT_CSV, "RUT"), ["cboe"], 1493),
        }
        monkeypatch.setattr(msr, "ensure_index_closes", lambda symbol: self.index_closes[symbol])

    def _named(self, name):
        return [c for c in self.calls if c[0] == name]

    def test_full_build_persists_rows_snapshot_and_heartbeat(self):
        client = _StubClient(FULL_TEXTS, STAMPS)
        payload = msr.run(client=client, now=datetime(2026, 11, 9, 3, 0, tzinfo=timezone.utc))

        assert payload["scan_time"] == "2026-11-09T03:00:00Z"
        assert payload["source_last_modified"] == STAMPS
        spx = payload["indexes"]["SPX"]
        assert spx["current"]["rank"] == 3736
        assert spx["stats"]["count"] == 4779
        assert payload["indexes"]["RUT"]["stats"]["count"] == 1492
        assert payload["indexes"]["NDX"]["current"] is None  # run still succeeds
        assert payload["indexes"]["NDX"]["top"] == []
        assert payload["close_sources"]["RUT"] == ["cboe"]
        assert payload["close_sources"]["SPX"] == ["cboe"]

        upserts = {c[1][0]: c[1][1] for c in self._named("upsert_msr_rows")}
        assert len(upserts["SPX"]) == 4779
        assert len(upserts["RUT"]) == 1492
        assert not upserts.get("NDX")
        snapshots = self._named("upsert_scan_snapshot")
        assert len(snapshots) == 1
        assert snapshots[0][1][0] == "msr"
        health = self._named("record_service_health")
        assert health and health[0][1][:2] == ("msr", "ok")
        assert json.loads(msr.MSR_JSON.read_text())["indexes"]["SPX"]["current"]["rank"] == 3736

    def test_all_304_and_no_new_closes_heartbeats_only(self):
        cached = {
            "scan_time": "old",
            "start_date": "2007-11-08",
            "source_last_modified": STAMPS,
            "close_sources": {"SPX": ["cboe"], "NDX": [], "RUT": ["cboe"]},
            "indexes": {"SPX": {"iv_symbol": "VIX", "current": {"rank": 3736}}},
            "series": [],
        }
        msr.MSR_JSON.write_text(json.dumps(cached))
        self.index_closes["RUT"] = (parse_close_csv(RUT_CSV, "RUT"), ["cboe"], 0)
        client = _StubClient(FULL_TEXTS, STAMPS, unchanged=True)

        payload = msr.run(client=client)

        assert {s: v[0] for s, v in client.seen_if_modified_since.items()} == {
            "SPX": STAMPS["spx"], "VIX": STAMPS["vix"], "VXN": STAMPS["vxn"], "RVX": STAMPS["rvx"],
        }
        assert payload["indexes"] == cached["indexes"]
        assert payload["scan_time"] != "old"
        assert self._named("upsert_msr_rows") == []
        assert len(self._named("upsert_scan_snapshot")) == 1
        assert self._named("record_service_health")[0][1][:2] == ("msr", "ok")

    def test_all_304_but_new_index_closes_rebuilds(self):
        cached = {"scan_time": "old", "source_last_modified": STAMPS, "indexes": {}, "series": []}
        msr.MSR_JSON.write_text(json.dumps(cached))
        self.index_closes["RUT"] = (parse_close_csv(RUT_CSV, "RUT"), ["cboe"], 1)
        client = _StubClient(FULL_TEXTS, STAMPS, unchanged=True)

        payload = msr.run(client=client)

        assert payload["indexes"]["SPX"]["stats"]["count"] == 4779
        assert payload["indexes"]["RUT"]["stats"]["count"] == 1492
        assert self._named("upsert_msr_rows")

    def test_empty_spx_series_raises_and_writes_nothing(self):
        texts = {**FULL_TEXTS, "SPX": "DATE,SPX\n"}
        with pytest.raises((RuntimeError, ValueError)):
            msr.run(client=_StubClient(texts, STAMPS))
        assert self._named("upsert_scan_snapshot") == []
        assert self._named("upsert_msr_rows") == []
        assert not msr.MSR_JSON.exists()
