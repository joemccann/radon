"""STRADDLE indicator — SPX realized vs implied 1-day straddle ratio tests.

Ground-truth values are read from checked-in fixtures:
  - fixtures/spx_history_sample.csv   — full Cboe SPX_History.csv (1975-01-02..2026-08-04)
  - fixtures/vix1d_sample.csv         — full Cboe VIX1D_History.csv (2022-05-13..2026-08-04)
Expected numbers below were derived by inspecting the fixtures directly
(2026-08-05), not computed by hand. Spec: docs/indicators/straddle.md.
"""
import json
import sqlite3
from pathlib import Path

import pytest

from fetch_straddle import (
    compute_series,
    compute_stats,
    implied_straddle_pct,
    parse_index_csv,
)

FIXTURES = Path(__file__).parent / "fixtures"
SPX_CSV = (FIXTURES / "spx_history_sample.csv").read_text()
VIX1D_CSV = (FIXTURES / "vix1d_sample.csv").read_text()
MIGRATION = Path(__file__).parents[1] / "db" / "migrations" / "0033_straddle.sql"


# ── Cboe CSV parsing ──────────────────────────────────────────────


class TestParseIndexCsv:
    def test_spx_fixture_row_count_and_bounds(self):
        rows = parse_index_csv(SPX_CSV, "SPX")
        assert len(rows) == 13005
        assert rows[0] == {"date": "1975-01-02", "value": 70.23}
        assert rows[-1] == {"date": "2026-08-04", "value": 7736.52}

    def test_vix1d_fixture_row_count_and_bounds(self):
        rows = parse_index_csv(VIX1D_CSV, "CLOSE")
        assert len(rows) == 1059
        assert rows[0] == {"date": "2022-05-13", "value": 33.63}
        assert rows[-1] == {"date": "2026-08-04", "value": 13.86}

    def test_rows_are_ascending_iso_dates(self):
        dates = [r["date"] for r in parse_index_csv(VIX1D_CSV, "CLOSE")]
        assert dates == sorted(dates)
        assert all(len(d) == 10 and d[4] == "-" and d[7] == "-" for d in dates)

    def test_malformed_rows_are_skipped(self):
        text = "DATE,SPX\n08/04/2026,7736.52\nnot-a-date,1\n08/05/2026,\n"
        assert parse_index_csv(text, "SPX") == [{"date": "2026-08-04", "value": 7736.52}]


# ── Straddle math ─────────────────────────────────────────────────


class TestImpliedStraddlePct:
    def test_brenner_subrahmanyam_at_vix1d_20(self):
        # sqrt(2/pi) * 0.20 * sqrt(1/252) * 100, derived 2026-08-05
        assert implied_straddle_pct(20.0) == pytest.approx(1.005240058486846)

    def test_scales_linearly_in_vol(self):
        assert implied_straddle_pct(40.0) == pytest.approx(2 * implied_straddle_pct(20.0))


def _rows(pairs):
    return [{"date": d, "value": v} for d, v in pairs]


class TestComputeSeries:
    def test_first_common_date_has_null_ratio(self):
        series = compute_series(
            _rows([("2026-08-01", 100.0), ("2026-08-02", 101.0)]),
            _rows([("2026-08-01", 20.0), ("2026-08-02", 20.0)]),
        )
        assert series[0]["ratio"] is None
        assert series[0] == {
            "date": "2026-08-01", "spx_close": 100.0, "vix1d_close": 20.0, "ratio": None,
        }

    def test_ratio_divides_move_by_prior_close_implied_straddle(self):
        # +1% move against VIX1D 20 at the prior close: 1.0 / 1.005240...
        series = compute_series(
            _rows([("2026-08-01", 100.0), ("2026-08-02", 101.0)]),
            _rows([("2026-08-01", 20.0), ("2026-08-02", 35.0)]),  # day-2 vol must NOT be used
        )
        assert series[1]["ratio"] == pytest.approx(1.0 / 1.005240058486846)

    def test_restricted_to_common_dates(self):
        # SPX has decades of extra history; only VIX1D-covered dates survive.
        series = compute_series(
            _rows([("2016-01-04", 2012.66), ("2026-08-01", 100.0), ("2026-08-02", 101.0)]),
            _rows([("2026-08-01", 20.0), ("2026-08-02", 20.0)]),
        )
        assert [r["date"] for r in series] == ["2026-08-01", "2026-08-02"]

    def test_fixture_series_bounds_and_known_ratios(self):
        series = compute_series(
            parse_index_csv(SPX_CSV, "SPX"), parse_index_csv(VIX1D_CSV, "CLOSE")
        )
        assert len(series) == 1059
        assert series[0]["date"] == "2022-05-13"
        assert series[0]["ratio"] is None
        assert series[1]["date"] == "2022-05-16"
        assert series[1]["ratio"] == pytest.approx(-0.233474, abs=1e-5)
        last = series[-1]
        assert last["date"] == "2026-08-04"
        assert last["spx_close"] == 7736.52
        assert last["vix1d_close"] == 13.86
        assert last["ratio"] == pytest.approx(3.775801, abs=1e-5)


class TestComputeStats:
    def test_fixture_stats(self):
        series = compute_series(
            parse_index_csv(SPX_CSV, "SPX"), parse_index_csv(VIX1D_CSV, "CLOSE")
        )
        stats = compute_stats(series)
        assert stats["high"] == pytest.approx(3.775801, abs=1e-5)
        assert stats["low"] == pytest.approx(-4.968372, abs=1e-5)
        assert stats["avg"] == pytest.approx(0.063255, abs=1e-5)
        assert stats["stddev"] == pytest.approx(1.157428, abs=1e-5)  # population
        assert stats["hit_rate"] == pytest.approx(0.367675, abs=1e-5)

    def test_hit_rate_uses_strict_breakeven_inequality(self):
        series = [
            {"date": "d0", "spx_close": 1, "vix1d_close": 1, "ratio": None},
            {"date": "d1", "spx_close": 1, "vix1d_close": 1, "ratio": 1.0},
            {"date": "d2", "spx_close": 1, "vix1d_close": 1, "ratio": -1.0},
            {"date": "d3", "spx_close": 1, "vix1d_close": 1, "ratio": 1.01},
            {"date": "d4", "spx_close": 1, "vix1d_close": 1, "ratio": -1.01},
        ]
        assert compute_stats(series)["hit_rate"] == pytest.approx(0.5)

    def test_no_ratios_yields_none(self):
        only_first = [{"date": "d0", "spx_close": 1, "vix1d_close": 1, "ratio": None}]
        assert compute_stats(only_first) is None


# ── run(): dual-file conditional-GET fast path ────────────────────


class _StubClient:
    """fetch_history(symbol, if_modified_since) stub keyed per symbol."""

    def __init__(self, results):
        self._results = results
        self.seen_if_modified_since = {}

    def fetch_history(self, symbol, if_modified_since=None):
        self.seen_if_modified_since[symbol] = if_modified_since
        return self._results[symbol]


SPX_STAMP = "Wed, 05 Aug 2026 17:01:43 GMT"
VIX1D_STAMP = "Wed, 05 Aug 2026 18:31:25 GMT"


class TestRunConditionalGet:
    @pytest.fixture(autouse=True)
    def _isolate_caches(self, tmp_path, monkeypatch):
        import fetch_straddle as mod

        monkeypatch.setattr(mod, "STRADDLE_JSON", tmp_path / "straddle.json")
        self.db_writes = []
        monkeypatch.setattr(
            mod, "_write_db_cache",
            lambda payload, scan_time, rows_changed: self.db_writes.append(rows_changed),
        )
        self.mod = mod

    def test_both_unchanged_reuses_cached_payload_without_row_writes(self):
        cached = {
            "scan_time": "old",
            "source_last_modified": {"spx": SPX_STAMP, "vix1d": VIX1D_STAMP},
            "count": 1059, "series": [], "current": {}, "stats": {},
        }
        self.mod.STRADDLE_JSON.write_text(json.dumps(cached))

        client = _StubClient({
            "SPX": (None, SPX_STAMP),
            "VIX1D": (None, VIX1D_STAMP),
        })
        payload = self.mod.run(client=client)

        assert client.seen_if_modified_since == {"SPX": SPX_STAMP, "VIX1D": VIX1D_STAMP}
        assert payload["count"] == 1059
        assert payload["scan_time"] != "old"  # heartbeat still advances
        assert self.db_writes == [False]  # snapshot only, no row upserts

    def test_changed_source_rebuilds_and_writes_rows(self):
        client = _StubClient({
            "SPX": (SPX_CSV, SPX_STAMP),
            "VIX1D": (VIX1D_CSV, VIX1D_STAMP),
        })
        payload = self.mod.run(client=client)

        assert client.seen_if_modified_since == {"SPX": None, "VIX1D": None}
        assert payload["count"] == 1059
        assert payload["current"]["date"] == "2026-08-04"
        assert payload["current"]["ratio"] == pytest.approx(3.775801, abs=1e-5)
        assert payload["current"]["vix1d_prior"] == 9.43
        assert payload["source_last_modified"] == {"spx": SPX_STAMP, "vix1d": VIX1D_STAMP}
        assert self.db_writes == [True]


# ── Migration + upsert (sqlite3 stand-in for libsql) ──────────────


class TestStraddleStorage:
    def _db(self):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)")
        db.executescript(MIGRATION.read_text())
        return db

    def test_migration_applies_and_registers_version(self):
        db = self._db()
        assert db.execute("SELECT version FROM schema_migrations").fetchone()[0] == 33
        cols = {r[1] for r in db.execute("PRAGMA table_info(straddle_history)")}
        assert {"date", "spx_close", "vix1d_close", "ratio", "recorded_at"} <= cols

    def test_upsert_is_idempotent_per_date(self):
        from db import writer

        db = self._db()
        args1 = ("2026-08-04", 7736.52, 13.86, 3.775801, "2026-08-05T02:15:00Z")
        args2 = ("2026-08-04", 7736.52, 13.86, 3.775802, "2026-08-06T02:15:00Z")
        db.execute(writer.STRADDLE_UPSERT_SQL, args1)
        db.execute(writer.STRADDLE_UPSERT_SQL, args2)
        rows = db.execute("SELECT date, ratio FROM straddle_history").fetchall()
        assert rows == [("2026-08-04", 3.775802)]
