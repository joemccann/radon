"""VIX TS indicator — VIX / VIX3M term-structure ratio tests.

Ground-truth values are read from checked-in fixtures:
  - fixtures/vix_history_sample.csv    — full Cboe VIX_History.csv (1990-01-02..2026-08-14)
  - fixtures/vixts_vix3m_sample.csv    — full Cboe VIX3M_History.csv (2009-09-18..2026-08-26)
  - fixtures/spx_history_sample.csv    — full Cboe SPX_History.csv (1975-01-02..2026-08-04)

Every expected number below was derived by inspecting those fixtures directly
(2026-08-27), never computed by hand. The VIX and SPX fixtures end before the
VIX3M one, which is deliberate: the inner join lands on 2026-08-14 and the last
8 rows carry a null SPX, so the left-join path is exercised against real data.

Spec: docs/indicators/vixts.md.
"""
import json
import sqlite3
from pathlib import Path

import pytest

from fetch_vixts import parse_index_csv
from lib.vixts_math import (
    BACKWARDATION_THRESHOLD,
    FLAT_THRESHOLD,
    MIN_SERIES_ROWS,
    RATIO_SANITY_MAX,
    RATIO_SANITY_MIN,
    STEEP_CONTANGO_THRESHOLD,
    build_current,
    classify_regime,
    compute_stats,
    ensure_plausible_series,
    join_series,
)

FIXTURES = Path(__file__).parent / "fixtures"
VIX_CSV = (FIXTURES / "vix_history_sample.csv").read_text()
VIX3M_CSV = (FIXTURES / "vixts_vix3m_sample.csv").read_text()
SPX_CSV = (FIXTURES / "spx_history_sample.csv").read_text()
MIGRATION = Path(__file__).parents[1] / "db" / "migrations" / "0058_vixts.sql"


def _fixture_series():
    return join_series(
        parse_index_csv(VIX_CSV, "CLOSE"),
        parse_index_csv(VIX3M_CSV, "CLOSE"),
        parse_index_csv(SPX_CSV, "SPX"),
    )


def _rows(pairs):
    return [{"date": d, "value": v} for d, v in pairs]


# ── Cboe CSV parsing ──────────────────────────────────────────────


class TestParseIndexCsv:
    def test_vix3m_fixture_row_count_and_bounds(self):
        rows = parse_index_csv(VIX3M_CSV, "CLOSE")
        assert len(rows) == 4260
        assert rows[0] == {"date": "2009-09-18", "value": 26.54}
        assert rows[-1] == {"date": "2026-08-26", "value": 17.99}

    def test_vix_fixture_row_count_and_bounds(self):
        rows = parse_index_csv(VIX_CSV, "CLOSE")
        assert len(rows) == 9251
        assert rows[0] == {"date": "1990-01-02", "value": 17.24}
        assert rows[-1] == {"date": "2026-08-14", "value": 14.25}

    def test_spx_uses_its_own_value_column_not_close(self):
        # SPX_History.csv is DATE,SPX — reading "CLOSE" would KeyError every row.
        rows = parse_index_csv(SPX_CSV, "SPX")
        assert len(rows) == 13005
        assert rows[-1] == {"date": "2026-08-04", "value": 7736.52}
        assert parse_index_csv(SPX_CSV, "CLOSE") == []

    def test_rows_are_ascending_iso_dates(self):
        dates = [r["date"] for r in parse_index_csv(VIX3M_CSV, "CLOSE")]
        assert dates == sorted(dates)
        assert all(len(d) == 10 and d[4] == "-" and d[7] == "-" for d in dates)

    def test_malformed_rows_are_skipped(self):
        text = "DATE,OPEN,HIGH,LOW,CLOSE\n08/26/2026,1,1,1,17.99\nnot-a-date,1,1,1,2\n08/27/2026,1,1,1,\n"
        assert parse_index_csv(text, "CLOSE") == [{"date": "2026-08-26", "value": 17.99}]


# ── Regime classification ─────────────────────────────────────────


class TestClassifyRegime:
    def test_threshold_constants(self):
        assert BACKWARDATION_THRESHOLD == 1.00
        assert FLAT_THRESHOLD == 0.95
        assert STEEP_CONTANGO_THRESHOLD == 0.80

    @pytest.mark.parametrize(
        "ratio,expected",
        [
            (1.2739, "BACKWARDATION"),
            (1.0000, "BACKWARDATION"),   # boundary: >= is backwardation
            (0.9999, "FLAT"),
            (0.9500, "FLAT"),            # boundary: >= is flat
            (0.9499, "CONTANGO"),
            (0.8455, "CONTANGO"),        # the 2026-08-26 reading
            (0.8000, "CONTANGO"),        # boundary: >= is contango
            (0.7999, "STEEP CONTANGO"),
            (0.7104, "STEEP CONTANGO"),
        ],
    )
    def test_boundaries_are_strict(self, ratio, expected):
        assert classify_regime(ratio) == expected


# ── Join ──────────────────────────────────────────────────────────


class TestJoinSeries:
    def test_fixture_join_bounds_and_count(self):
        series = _fixture_series()
        assert len(series) == 4252
        assert series[0]["date"] == "2009-09-18"
        assert series[-1]["date"] == "2026-08-14"

    def test_first_row_exact(self):
        assert _fixture_series()[0] == {
            "date": "2009-09-18",
            "vix": 23.92,
            "vix3m": 26.54,
            "ratio": 0.9013,
            "spx": 1068.30,
        }

    def test_last_row_exact_with_null_spx_left_join(self):
        # SPX fixture stops 2026-08-04, so the tail carries a null overlay.
        assert _fixture_series()[-1] == {
            "date": "2026-08-14",
            "vix": 14.25,
            "vix3m": 18.46,
            "ratio": 0.7719,
            "spx": None,
        }

    def test_left_join_emits_row_when_spx_absent(self):
        series = _fixture_series()
        missing = [r for r in series if r["spx"] is None]
        assert len(missing) == 8
        assert missing[0]["date"] == "2026-08-05"

    def test_inner_join_drops_dates_missing_either_leg(self):
        # VIX has 1990-2009 history VIX3M does not; those dates never appear.
        assert all(r["date"] >= "2009-09-18" for r in _fixture_series())

    def test_ratio_is_vix_over_vix3m_rounded_to_4dp(self):
        series = join_series(
            _rows([("2026-08-25", 15.45)]),
            _rows([("2026-08-25", 18.21)]),
            _rows([]),
        )
        assert series[0]["ratio"] == 0.8484           # 15.45 / 18.21 = 0.848435...
        assert series[0]["ratio"] == round(15.45 / 18.21, 4)

    def test_non_positive_vix3m_row_raises_and_is_never_divided(self):
        """R-363: this asserted the row was DROPPED, which was the defect.

        Dropping made `ensure_plausible_series`'s `bad_leg` guard unreachable,
        so Cboe publishing zero closes for three days left a silent three-day
        hole that passed every guard and heartbeat `ok`. The original intent —
        never divide by a non-positive VIX3M — is unchanged and is still
        enforced; only the outcome moved from a silent drop to a raise, which
        is what the guard downstream was always written to expect.
        """
        with pytest.raises(ValueError, match="non-positive vix3m"):
            join_series(
                _rows([("2026-08-24", 15.85), ("2026-08-25", 15.45)]),
                _rows([("2026-08-24", 0.0), ("2026-08-25", 18.21)]),
                _rows([]),
            )

    def test_series_is_ascending(self):
        dates = [r["date"] for r in _fixture_series()]
        assert dates == sorted(dates)


# ── current ───────────────────────────────────────────────────────


class TestBuildCurrent:
    def test_current_is_last_row_plus_regime(self):
        assert build_current(_fixture_series()) == {
            "date": "2026-08-14",
            "vix": 14.25,
            "vix3m": 18.46,
            "ratio": 0.7719,
            "regime": "STEEP CONTANGO",
            "spx": None,
        }

    def test_empty_series_has_no_current(self):
        assert build_current([]) is None


# ── stats ─────────────────────────────────────────────────────────


class TestComputeStats:
    def test_fixture_stats(self):
        stats = compute_stats(_fixture_series())
        assert stats["min"] == pytest.approx(0.7104, abs=1e-6)
        assert stats["max"] == pytest.approx(1.3437, abs=1e-6)
        assert stats["mean"] == pytest.approx(0.894398, abs=1e-5)
        assert stats["median"] == pytest.approx(0.8846, abs=1e-5)
        assert stats["days_backwardation"] == 325
        assert stats["pct_backwardation"] == pytest.approx(7.6435, abs=1e-3)
        assert stats["last_backwardation_date"] == "2026-04-07"

    def test_backwardation_count_uses_inclusive_threshold(self):
        series = [
            {"date": "d1", "vix": 1, "vix3m": 1, "ratio": 0.9999, "spx": None},
            {"date": "d2", "vix": 1, "vix3m": 1, "ratio": 1.0000, "spx": None},
            {"date": "d3", "vix": 1, "vix3m": 1, "ratio": 1.0001, "spx": None},
        ]
        stats = compute_stats(series)
        assert stats["days_backwardation"] == 2
        assert stats["last_backwardation_date"] == "d3"

    def test_never_crossed_series_reports_null_last_date(self):
        series = [{"date": "d1", "vix": 1, "vix3m": 2, "ratio": 0.5, "spx": None}]
        stats = compute_stats(series)
        assert stats["days_backwardation"] == 0
        assert stats["pct_backwardation"] == 0.0
        assert stats["last_backwardation_date"] is None

    def test_empty_series_has_no_stats(self):
        assert compute_stats([]) is None


# ── plausibility guard (the only protection: no fallback rung) ────


class TestEnsurePlausibleSeries:
    def test_sanity_constants(self):
        assert MIN_SERIES_ROWS == 2000
        assert RATIO_SANITY_MIN == 0.40
        assert RATIO_SANITY_MAX == 2.50

    def test_real_fixture_series_passes(self):
        ensure_plausible_series(_fixture_series())  # must not raise

    def test_truncated_source_raises(self):
        short = _fixture_series()[:MIN_SERIES_ROWS - 1]
        with pytest.raises(ValueError, match="rows"):
            ensure_plausible_series(short)

    def test_empty_series_raises(self):
        with pytest.raises(ValueError):
            ensure_plausible_series([])

    @pytest.mark.parametrize("bad_ratio", [0.39, 2.51])
    def test_latest_ratio_outside_sanity_band_raises(self, bad_ratio):
        series = _fixture_series()
        corrupted = series[:-1] + [{**series[-1], "ratio": bad_ratio}]
        with pytest.raises(ValueError, match="ratio"):
            ensure_plausible_series(corrupted)

    def test_non_positive_vix3m_raises(self):
        series = _fixture_series()
        corrupted = series[:-1] + [{**series[-1], "vix3m": 0.0}]
        with pytest.raises(ValueError, match="vix3m"):
            ensure_plausible_series(corrupted)


# ── run(): three-file conditional-GET ─────────────────────────────


class _StubClient:
    """fetch_history(symbol, if_modified_since) stub keyed per symbol."""

    def __init__(self, results):
        self._results = results
        self.seen_if_modified_since = {}
        self.calls = []

    def fetch_history(self, symbol, if_modified_since=None):
        self.calls.append(symbol)
        self.seen_if_modified_since[symbol] = if_modified_since
        return self._results[symbol]


VIX_STAMP = "Thu, 27 Aug 2026 01:50:46 GMT"
VIX3M_STAMP = "Wed, 26 Aug 2026 22:00:57 GMT"
SPX_STAMP = "Thu, 27 Aug 2026 00:31:07 GMT"


class TestRunConditionalGet:
    @pytest.fixture(autouse=True)
    def _isolate_caches(self, tmp_path, monkeypatch):
        import fetch_vixts as mod

        monkeypatch.setattr(mod, "VIXTS_JSON", tmp_path / "vixts.json")
        self.db_writes = []
        monkeypatch.setattr(
            mod,
            "_write_db",
            lambda payload, scan_time, *, rows_changed, health_error=None: self.db_writes.append(
                rows_changed
            ),
        )
        self.mod = mod

    def test_all_unchanged_reuses_cached_payload_without_row_writes(self):
        cached = {
            "scan_time": "old",
            "source_last_modified": {
                "vix": VIX_STAMP, "vix3m": VIX3M_STAMP, "spx": SPX_STAMP,
            },
            "data_date": "2026-08-14",
            "count": 4252,
            "series": [], "current": {}, "stats": {},
        }
        self.mod.VIXTS_JSON.write_text(json.dumps(cached))

        client = _StubClient({
            "VIX": (None, VIX_STAMP),
            "VIX3M": (None, VIX3M_STAMP),
            "SPX": (None, SPX_STAMP),
        })
        payload = self.mod.run(client=client)

        assert client.seen_if_modified_since == {
            "VIX": VIX_STAMP, "VIX3M": VIX3M_STAMP, "SPX": SPX_STAMP,
        }
        assert payload["count"] == 4252
        assert payload["scan_time"] != "old"      # heartbeat still advances
        assert self.db_writes == [False]          # snapshot only, no row upserts

    def test_changed_source_rebuilds_and_writes_rows(self):
        client = _StubClient({
            "VIX": (VIX_CSV, VIX_STAMP),
            "VIX3M": (VIX3M_CSV, VIX3M_STAMP),
            "SPX": (SPX_CSV, SPX_STAMP),
        })
        payload = self.mod.run(client=client)

        assert payload["count"] == 4252
        assert payload["data_date"] == "2026-08-14"
        assert payload["current"]["ratio"] == 0.7719
        assert payload["current"]["regime"] == "STEEP CONTANGO"
        assert payload["source_last_modified"] == {
            "vix": VIX_STAMP, "vix3m": VIX3M_STAMP, "spx": SPX_STAMP,
        }
        assert self.db_writes == [True]

    def test_partial_304_refetches_only_the_unchanged_files(self):
        # VIX3M moved; VIX and SPX 304. The rebuild needs all three texts, so
        # the two unchanged files are re-fetched unconditionally.
        client = _StubClient({
            "VIX": (VIX_CSV, VIX_STAMP),
            "VIX3M": (VIX3M_CSV, VIX3M_STAMP),
            "SPX": (SPX_CSV, SPX_STAMP),
        })
        cached = {
            "scan_time": "old",
            "source_last_modified": {"vix": VIX_STAMP, "vix3m": "stale", "spx": SPX_STAMP},
            "count": 1, "series": [], "current": {}, "stats": {},
        }
        self.mod.VIXTS_JSON.write_text(json.dumps(cached))
        payload = self.mod.run(client=client)

        assert payload["count"] == 4252
        assert self.db_writes == [True]

    def test_first_run_with_no_cache_sends_no_if_modified_since(self):
        client = _StubClient({
            "VIX": (VIX_CSV, VIX_STAMP),
            "VIX3M": (VIX3M_CSV, VIX3M_STAMP),
            "SPX": (SPX_CSV, SPX_STAMP),
        })
        self.mod.run(client=client)
        assert client.seen_if_modified_since == {"VIX": None, "VIX3M": None, "SPX": None}

    def test_json_cache_is_written_with_the_payload(self):
        client = _StubClient({
            "VIX": (VIX_CSV, VIX_STAMP),
            "VIX3M": (VIX3M_CSV, VIX3M_STAMP),
            "SPX": (SPX_CSV, SPX_STAMP),
        })
        payload = self.mod.run(client=client)
        on_disk = json.loads(self.mod.VIXTS_JSON.read_text())
        assert on_disk["data_date"] == payload["data_date"]
        assert on_disk["count"] == payload["count"]


# ── _write_db isolation (R-192) ───────────────────────────────────


class _FakeWriter:
    def __init__(self, *, rows_raise=False):
        self.rows_raise = rows_raise
        self.snapshots = []
        self.health = []
        self.rows = []

    def ensure_no_replica_for_writers(self):
        return None

    def upsert_vixts_rows(self, rows, recorded_at=None):
        if self.rows_raise:
            raise RuntimeError("hrana 502")
        self.rows.append((len(rows), recorded_at))

    def upsert_scan_snapshot(self, service, scan_time, payload):
        self.snapshots.append((service, scan_time))

    def record_service_health(self, service, state, *, finished_at=None, error=None):
        self.health.append((service, state, error))


class TestWriteDbIsolation:
    def _run_write(self, monkeypatch, fake, **kwargs):
        import fetch_vixts as mod

        monkeypatch.setattr(mod, "writer", fake)
        mod._write_db({"series": [{"date": "d", "ratio": 1.0}]}, "2026-08-27T02:45:00Z", **kwargs)
        return mod

    def test_service_name_is_kebab_case_key(self):
        import fetch_vixts as mod

        assert mod.SERVICE == "vixts"

    def test_happy_path_writes_rows_snapshot_and_ok_heartbeat(self, monkeypatch):
        fake = _FakeWriter()
        self._run_write(monkeypatch, fake, rows_changed=True)
        assert fake.rows == [(1, "2026-08-27T02:45:00Z")]
        assert fake.snapshots == [("vixts", "2026-08-27T02:45:00Z")]
        assert fake.health == [("vixts", "ok", None)]

    def test_304_path_heartbeats_without_row_upserts(self, monkeypatch):
        fake = _FakeWriter()
        self._run_write(monkeypatch, fake, rows_changed=False)
        assert fake.rows == []
        assert fake.snapshots == [("vixts", "2026-08-27T02:45:00Z")]
        assert fake.health == [("vixts", "ok", None)]

    def test_failed_row_upsert_still_snapshots_and_records_error(self, monkeypatch):
        fake = _FakeWriter(rows_raise=True)
        self._run_write(monkeypatch, fake, rows_changed=True)
        assert fake.snapshots == [("vixts", "2026-08-27T02:45:00Z")]
        service, state, error = fake.health[0]
        assert state == "error"
        assert error["class"] == "db_write_failed"


# ── Migration + upsert (sqlite3 stand-in for libsql) ──────────────


class TestVixTsStorage:
    def _db(self):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)")
        db.executescript(MIGRATION.read_text())
        return db

    def test_migration_applies_and_registers_version_58(self):
        db = self._db()
        assert db.execute("SELECT version FROM schema_migrations").fetchone()[0] == 58
        cols = {r[1] for r in db.execute("PRAGMA table_info(vixts_history)")}
        assert {"date", "vix_close", "vix3m_close", "ratio", "spx_close", "recorded_at"} <= cols

    def test_date_is_the_primary_key(self):
        db = self._db()
        pk = [r[1] for r in db.execute("PRAGMA table_info(vixts_history)") if r[5]]
        assert pk == ["date"]

    def test_spx_close_is_nullable_for_the_left_join(self):
        db = self._db()
        notnull = {r[1]: r[3] for r in db.execute("PRAGMA table_info(vixts_history)")}
        assert notnull["spx_close"] == 0
        assert notnull["vix_close"] == 1
        assert notnull["vix3m_close"] == 1

    def test_descending_date_index_exists(self):
        db = self._db()
        names = {r[1] for r in db.execute("PRAGMA index_list(vixts_history)")}
        assert "idx_vixts_history_date_desc" in names

    def test_upsert_is_idempotent_per_date(self):
        from db import writer

        db = self._db()
        args1 = ("2026-08-14", 14.25, 18.46, 0.7719, None, "2026-08-27T02:45:00Z")
        args2 = ("2026-08-14", 14.25, 18.46, 0.7720, 7700.0, "2026-08-28T02:45:00Z")
        db.execute(writer.VIXTS_UPSERT_SQL, args1)
        db.execute(writer.VIXTS_UPSERT_SQL, args2)
        rows = db.execute("SELECT date, ratio, spx_close FROM vixts_history").fetchall()
        assert rows == [("2026-08-14", 0.7720, 7700.0)]


# ── R-362 / REL-130: a duplicate date must not kill the run mid-history ─────

class TestVixtsUpsertDedupsByDate:
    """SQLite refuses to UPSERT one conflict target twice inside a single
    INSERT, so a doubled row from the SPX left join or a Cboe double-publish
    raised "ON CONFLICT DO UPDATE command does not affect row a second time"
    and killed the run with earlier chunks already committed. The sibling
    `upsert_cash_flow_rows` dedups explicitly for exactly this reason.
    """

    def _sqlite_db(self, tmp_path):
        import sqlite3

        conn = sqlite3.connect(tmp_path / "vixts.db")
        conn.execute(
            "CREATE TABLE vixts_history (date TEXT PRIMARY KEY, vix_close REAL, "
            "vix3m_close REAL, ratio REAL, spx_close REAL, recorded_at TEXT)"
        )
        return conn

    def _row(self, date, ratio):
        return {"date": date, "vix": 18.0, "vix3m": 20.0, "ratio": ratio, "spx": 4000.0}

    def test_the_emitted_statement_carries_one_row_per_date(self, monkeypatch):
        """Asserted at the PARAMETERS, not against a live engine.

        The failure R-362 describes is libsql/older-SQLite raising "ON
        CONFLICT DO UPDATE command does not affect row a second time"; this
        runner's SQLite is 3.53.4, which relaxed that restriction and silently
        accepts the duplicate, so a round trip through `sqlite3` cannot show
        the defect. What the fix guarantees is engine-independent and is what
        is pinned here: the statement the writer emits never names one
        conflict target twice.
        """
        import db.writer as writer

        calls: list[tuple[str, tuple]] = []

        class _Capture:
            def execute(self, sql, params=()):
                calls.append((sql, params))

            def commit(self):
                pass

        monkeypatch.setattr(writer, "get_db", lambda: _Capture())
        writer.upsert_vixts_rows(
            [
                self._row("2026-08-24", 0.90),
                self._row("2026-08-25", 0.91),
                self._row("2026-08-25", 0.92),  # duplicate date, later wins
            ],
            recorded_at="2026-08-26T00:00:00Z",
        )

        assert len(calls) == 1
        sql, params = calls[0]
        # Six binds per row.
        assert len(params) % 6 == 0
        dates = [params[i] for i in range(0, len(params), 6)]
        assert dates == ["2026-08-24", "2026-08-25"], (
            f"one row per conflict target; got {dates}"
        )
        ratios = [params[i + 3] for i in range(0, len(params), 6)]
        assert ratios == [0.90, 0.92], "the LAST row for a date wins"
        assert sql.count("(?, ?, ?, ?, ?, ?)") == 2

    def test_a_clean_series_is_unchanged(self, tmp_path, monkeypatch):
        import db.writer as writer

        conn = self._sqlite_db(tmp_path)
        monkeypatch.setattr(writer, "get_db", lambda: conn)
        writer.upsert_vixts_rows(
            [self._row(f"2026-08-{d:02d}", 0.9) for d in range(1, 6)],
            recorded_at="2026-08-26T00:00:00Z",
        )
        assert conn.execute("SELECT COUNT(*) FROM vixts_history").fetchone()[0] == 5
