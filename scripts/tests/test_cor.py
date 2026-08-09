"""COR indicator — SPX implied correlation (Cboe COR1M/3M/6M/1Y) tests.

Ground-truth values are read from checked-in fixtures:
  - fixtures/cor_sample_1m.csv — full Cboe COR1M_History.csv (2006-01-03..2026-08-07)
  - fixtures/cor_sample_3m.csv — COR3M (15 scattered gap dates vs the others)
  - fixtures/cor_sample_6m.csv — COR6M
  - fixtures/cor_sample_1y.csv — COR1Y
Expected numbers below were derived by inspecting the fixtures directly
(2026-08-09), not computed by hand. Spec: docs/indicators/cor.md.
"""
import json
import sqlite3
from pathlib import Path

import pytest

from fetch_cor import (
    _build_current,
    compute_stats,
    merge_series,
    parse_index_csv,
)

FIXTURES = Path(__file__).parent / "fixtures"
COR_CSV = {
    tenor: (FIXTURES / f"cor_sample_{tenor[3:].lower()}.csv").read_text()
    for tenor in ("cor1m", "cor3m", "cor6m", "cor1y")
}
MIGRATION = Path(__file__).parents[1] / "db" / "migrations" / "0039_cor.sql"


# ── Cboe CSV parsing ──────────────────────────────────────────────


class TestParseIndexCsv:
    def test_cor1m_fixture_row_count_and_bounds(self):
        rows = parse_index_csv(COR_CSV["cor1m"])
        assert len(rows) == 5181
        assert rows[0] == {"date": "2006-01-03", "value": 23.5}
        assert rows[-1] == {"date": "2026-08-07", "value": 7.38}

    def test_cor6m_fixture_row_count_and_bounds(self):
        rows = parse_index_csv(COR_CSV["cor6m"])
        assert len(rows) == 5181
        assert rows[0] == {"date": "2006-01-03", "value": 38.21}
        assert rows[-1] == {"date": "2026-08-07", "value": 12.16}

    def test_rows_are_ascending_iso_dates(self):
        dates = [r["date"] for r in parse_index_csv(COR_CSV["cor3m"])]
        assert dates == sorted(dates)
        assert all(len(d) == 10 and d[4] == "-" and d[7] == "-" for d in dates)

    def test_malformed_rows_are_skipped(self):
        text = "DATE,OPEN,HIGH,LOW,CLOSE\n08/07/2026,1,1,1,7.38\nnot-a-date,1,1,1,1\n08/08/2026,1,1,1,\n"
        assert parse_index_csv(text) == [{"date": "2026-08-07", "value": 7.38}]


# ── merge_series ──────────────────────────────────────────────────


def _rows(pairs):
    return [{"date": d, "value": v} for d, v in pairs]


class TestMergeSeries:
    def test_union_of_dates_with_nulls_for_missing_tenor(self):
        series = merge_series(
            {
                "cor1m": _rows([("2026-08-06", 6.62), ("2026-08-07", 7.38)]),
                "cor1y": _rows([("2026-08-07", 14.19)]),
            }
        )
        assert series == [
            {"date": "2026-08-06", "cor1m": 6.62, "cor1y": None},
            {"date": "2026-08-07", "cor1m": 7.38, "cor1y": 14.19},
        ]

    def test_fixture_union_count_and_cor3m_gap(self):
        series = merge_series({k: parse_index_csv(v) for k, v in COR_CSV.items()})
        assert len(series) == 5181
        by_date = {row["date"]: row for row in series}
        # COR3M is missing 2006-05-18 while the other tenors have it.
        gap = by_date["2006-05-18"]
        assert gap["cor3m"] is None
        assert gap["cor1m"] is not None
        first, last = series[0], series[-1]
        assert first["date"] == "2006-01-03"
        assert (first["cor1m"], first["cor3m"], first["cor6m"], first["cor1y"]) == (
            23.5, 31.34, 38.21, 42.83,
        )
        assert last["date"] == "2026-08-07"
        assert (last["cor1m"], last["cor3m"], last["cor6m"], last["cor1y"]) == (
            7.38, 10.48, 12.16, 14.19,
        )


# ── stats + percentile ────────────────────────────────────────────


class TestComputeStats:
    def test_fixture_stats_per_tenor(self):
        series = merge_series({k: parse_index_csv(v) for k, v in COR_CSV.items()})
        stats = compute_stats(series)
        assert stats["cor1m"]["high"] == pytest.approx(96.59)
        assert stats["cor1m"]["low"] == pytest.approx(2.93)
        assert stats["cor1m"]["avg"] == pytest.approx(37.198579, abs=1e-5)
        assert stats["cor1m"]["stddev"] == pytest.approx(17.867378, abs=1e-5)
        assert stats["cor1m"]["percentile"] == pytest.approx(0.010037, abs=1e-5)
        assert stats["cor6m"]["high"] == pytest.approx(86.35)
        assert stats["cor6m"]["low"] == pytest.approx(9.67)
        assert stats["cor6m"]["avg"] == pytest.approx(44.342579, abs=1e-5)
        assert stats["cor6m"]["stddev"] == pytest.approx(15.781472, abs=1e-5)
        assert stats["cor6m"]["percentile"] == pytest.approx(0.0083, abs=1e-5)
        assert stats["cor1y"]["percentile"] == pytest.approx(0.007721, abs=1e-5)

    def test_percentile_is_strictly_below_share(self):
        series = merge_series({"cor1m": _rows([("d1", 5.0), ("d2", 5.0), ("d3", 5.0)])})
        assert compute_stats(series)["cor1m"]["percentile"] == 0.0

    def test_all_null_tenor_yields_none(self):
        series = merge_series(
            {"cor1m": _rows([("d1", 5.0)]), "cor6m": _rows([("d2", 6.0)])}
        )
        assert compute_stats(series)["cor1y"] is None


# ── current card ──────────────────────────────────────────────────


class TestBuildCurrent:
    def test_fixture_current_values_and_changes(self):
        series = merge_series({k: parse_index_csv(v) for k, v in COR_CSV.items()})
        current = _build_current(series)
        assert current["date"] == "2026-08-07"
        assert current["cor1m"] == 7.38
        assert current["cor6m"] == 12.16
        assert current["term_spread"] == pytest.approx(6.81)
        assert current["change_1d"]["cor1m"] == pytest.approx(0.76)
        assert current["change_1d"]["cor3m"] == pytest.approx(1.07)
        assert current["change_1d"]["cor6m"] == pytest.approx(0.98)
        assert current["change_1d"]["cor1y"] == pytest.approx(0.74)

    def test_change_1d_skips_null_gaps(self):
        series = merge_series(
            {
                "cor1m": _rows([("d1", 10.0), ("d2", 11.0), ("d3", 12.5)]),
                "cor3m": _rows([("d1", 20.0), ("d3", 22.0)]),  # d2 gap
            }
        )
        current = _build_current(series)
        assert current["change_1d"]["cor1m"] == pytest.approx(1.5)
        assert current["change_1d"]["cor3m"] == pytest.approx(2.0)

    def test_single_row_has_null_changes_and_empty_series_none(self):
        assert _build_current([]) is None
        current = _build_current(merge_series({"cor1m": _rows([("d1", 10.0)])}))
        assert current["change_1d"]["cor1m"] is None
        assert current["term_spread"] is None


# ── run(): four-file conditional-GET fast path ────────────────────


class _StubClient:
    """fetch_history(symbol, if_modified_since) stub keyed per symbol."""

    def __init__(self, results):
        self._results = results
        self.seen_if_modified_since = {}

    def fetch_history(self, symbol, if_modified_since=None):
        self.seen_if_modified_since[symbol] = if_modified_since
        return self._results[symbol]


STAMPS = {
    "COR1M": "Fri, 07 Aug 2026 22:31:04 GMT",
    "COR3M": "Fri, 07 Aug 2026 22:31:10 GMT",
    "COR6M": "Fri, 07 Aug 2026 22:31:16 GMT",
    "COR1Y": "Fri, 07 Aug 2026 22:31:22 GMT",
}


class TestRunConditionalGet:
    @pytest.fixture(autouse=True)
    def _isolate_caches(self, tmp_path, monkeypatch):
        import fetch_cor as mod

        monkeypatch.setattr(mod, "COR_JSON", tmp_path / "cor.json")
        self.db_writes = []
        monkeypatch.setattr(
            mod, "_write_db_cache",
            lambda payload, scan_time, rows_changed: self.db_writes.append(rows_changed),
        )
        self.mod = mod

    def test_all_unchanged_reuses_cached_payload_without_row_writes(self):
        cached = {
            "scan_time": "old",
            "source_last_modified": {k.lower(): v for k, v in STAMPS.items()},
            "count": 5181, "series": [], "current": {}, "stats": {},
        }
        self.mod.COR_JSON.write_text(json.dumps(cached))

        client = _StubClient({sym: (None, stamp) for sym, stamp in STAMPS.items()})
        payload = self.mod.run(client=client)

        assert client.seen_if_modified_since == STAMPS
        assert payload["count"] == 5181
        assert payload["scan_time"] != "old"  # heartbeat still advances
        assert self.db_writes == [False]  # snapshot only, no row upserts

    def test_changed_source_rebuilds_and_writes_rows(self):
        client = _StubClient(
            {sym: (COR_CSV[sym.lower()], STAMPS[sym]) for sym in STAMPS}
        )
        payload = self.mod.run(client=client)

        assert client.seen_if_modified_since == {sym: None for sym in STAMPS}
        assert payload["count"] == 5181
        assert payload["current"]["date"] == "2026-08-07"
        assert payload["current"]["cor6m"] == 12.16
        assert payload["stats"]["cor6m"]["percentile"] == pytest.approx(0.0083, abs=1e-5)
        assert payload["source_last_modified"] == {
            k.lower(): v for k, v in STAMPS.items()
        }
        assert self.db_writes == [True]


# ── Migration + upsert (sqlite3 stand-in for libsql) ──────────────


class TestCorStorage:
    def _db(self):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)")
        db.executescript(MIGRATION.read_text())
        return db

    def test_migration_applies_and_registers_version(self):
        db = self._db()
        assert db.execute("SELECT version FROM schema_migrations").fetchone()[0] == 39
        cols = {r[1] for r in db.execute("PRAGMA table_info(cor_history)")}
        assert {"date", "cor1m", "cor3m", "cor6m", "cor1y", "recorded_at"} <= cols

    def test_upsert_is_idempotent_per_date(self):
        from db import writer

        db = self._db()
        args1 = ("2026-08-07", 7.38, 10.48, 12.16, 14.19, "2026-08-10T02:20:00Z")
        args2 = ("2026-08-07", 7.38, 10.48, 12.17, 14.19, "2026-08-11T02:20:00Z")
        db.execute(writer.COR_UPSERT_SQL, args1)
        db.execute(writer.COR_UPSERT_SQL, args2)
        rows = db.execute("SELECT date, cor6m FROM cor_history").fetchall()
        assert rows == [("2026-08-07", 12.17)]
