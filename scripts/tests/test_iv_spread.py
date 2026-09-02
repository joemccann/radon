"""IV SPREAD indicator — NDX minus SPX 1M ATM implied volatility spread tests.

Spec: docs/indicators/iv-spread.md.

Ground truth is read from two checked-in fixtures, never from the network
and never from Turso:

  - fixtures/iv_spread_ib_sample.json — the daily `1 M` pull: 22
    OPTION_IMPLIED_VOLATILITY daily bars per leg (SPX/CBOE, NDX/NASDAQ),
    2026-08-04 .. 2026-09-02, captured live from the cloud gateway on
    2026-09-02, wrapped in {source, what_to_show, legs} metadata.
  - fixtures/iv_spread_ib_5y_closes.json — the `5 Y` seed as paired closes
    only: 1253 sessions 2021-09-07 .. 2026-09-02, {date, spx, ndx}.

Fixture-derived expectations were derived by inspecting the fixtures with a
scratch script (2026-09-02), NOT by calling the functions under test. Hand-
computable expectations carry their arithmetic in a comment.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
IB_SAMPLE: dict[str, Any] = json.loads((FIXTURES / "iv_spread_ib_sample.json").read_text())
CLOSES_5Y: dict[str, Any] = json.loads((FIXTURES / "iv_spread_ib_5y_closes.json").read_text())
MIGRATION = Path(__file__).parents[1] / "db" / "migrations" / "0069_iv_spread.sql"

# ── fixture-derived pins (scratch-derived 2026-09-02) ─────────────
PIN_5Y_COUNT = 1253
PIN_5Y_FIRST = "2021-09-07"
PIN_5Y_LAST = "2026-09-02"
PIN_LAST_SPX = 0.12104312
PIN_LAST_NDX = 0.1758578
PIN_LAST_SPREAD = 5.481468          # (0.1758578 - 0.12104312) * 100
PIN_PRIOR_SPREAD = 5.121116         # 2026-09-01
PIN_CHANGE_1D = 0.360352
PIN_HIGH = 12.642458
PIN_HIGH_DATE = "2026-06-23"
PIN_LOW = -3.297135
PIN_LOW_DATE = "2025-04-08"
PIN_MEAN = 5.318448
PIN_STDEV = 1.567474                # sample (n - 1)
PIN_Z_LAST = 0.104002
PIN_PCTILE_LAST = 59.377494         # 744 of 1253 strictly below

PIN_1M_COUNT = 22
PIN_1M_FIRST = "2026-08-04"
PIN_1M_FIRST_SPREAD = 8.992909
PIN_1M_MEAN = 6.613825
PIN_1M_STDEV = 1.083811
PIN_1M_MAX = 8.992909
PIN_1M_MIN = 4.655993


def _mod():
    """Import the module under test lazily so the red phase fails per-test."""
    import fetch_iv_spread  # type: ignore[import-not-found]

    return fetch_iv_spread


def _sample_legs() -> dict[str, list[dict[str, Any]]]:
    """The 1M fixture as the job's injected fetcher contract emits it."""
    return {
        sym: [{"date": bar["date"], "iv": bar["close"]} for bar in leg["bars"]]
        for sym, leg in IB_SAMPLE["legs"].items()
    }


def _closes_rows() -> list[dict[str, Any]]:
    """The 5Y closes as stored-history rows."""
    return [
        {"date": row["date"], "spx_iv": row["spx"], "ndx_iv": row["ndx"]}
        for row in CLOSES_5Y["rows"]
    ]


def _synthetic_rows(n: int, spx: float = 0.12, ndx: float = 0.18) -> list[dict[str, Any]]:
    start = date(2020, 1, 1)
    return [
        {"date": (start + timedelta(days=i)).isoformat(), "spx_iv": spx, "ndx_iv": ndx}
        for i in range(n)
    ]


# ── fixture integrity ─────────────────────────────────────────────


class TestFixtureIntegrity:
    def test_sample_fixture_shape(self):
        assert IB_SAMPLE["what_to_show"] == "OPTION_IMPLIED_VOLATILITY"
        assert set(IB_SAMPLE["legs"]) == {"SPX", "NDX"}
        assert IB_SAMPLE["legs"]["SPX"]["contract"]["exchange"] == "CBOE"
        assert IB_SAMPLE["legs"]["NDX"]["contract"]["exchange"] == "NASDAQ"
        for leg in IB_SAMPLE["legs"].values():
            assert len(leg["bars"]) == PIN_1M_COUNT
            assert leg["bars"][0]["date"] == PIN_1M_FIRST
            assert leg["bars"][-1]["date"] == PIN_5Y_LAST
        assert IB_SAMPLE["legs"]["SPX"]["bars"][-1]["close"] == pytest.approx(PIN_LAST_SPX, abs=1e-9)
        assert IB_SAMPLE["legs"]["NDX"]["bars"][-1]["close"] == pytest.approx(PIN_LAST_NDX, abs=1e-9)

    def test_closes_fixture_shape(self):
        rows = CLOSES_5Y["rows"]
        assert CLOSES_5Y["count"] == PIN_5Y_COUNT == len(rows)
        assert rows[0]["date"] == PIN_5Y_FIRST
        assert rows[-1]["date"] == PIN_5Y_LAST
        dates = [row["date"] for row in rows]
        assert dates == sorted(dates)
        assert len(set(dates)) == len(dates)


# ── pure math ─────────────────────────────────────────────────────


class TestNamedConstants:
    def test_module_constants_match_the_spec(self):
        mod = _mod()
        assert mod.LEGS == (("SPX", "CBOE"), ("NDX", "NASDAQ"))
        assert mod.VOL_POINTS == 100.0
        assert mod.BACKFILL_DURATION == "5 Y"
        assert mod.INCREMENTAL_DURATION == "1 M"
        assert mod.Z_COMPRESSED_MAX == -1.0
        assert mod.Z_NORMAL_MAX == 1.0
        assert mod.Z_ELEVATED_MAX == 2.0
        assert mod.OUTLIER_NEIGHBOR_RATIO == 1.5
        assert mod.SERVICE == "iv-spread"


class TestComputeSpread:
    def test_worked_example_one_nominal(self):
        # (0.18 - 0.12) * 100 = 6.0
        assert _mod().compute_spread(0.12, 0.18) == pytest.approx(6.0, abs=1e-9)

    def test_worked_example_two_the_real_inversion(self):
        # 2025-04-08: (0.41626135 - 0.4492327) * 100 = -3.297135
        assert _mod().compute_spread(0.4492327, 0.41626135) == pytest.approx(-3.297135, abs=1e-6)


class TestStats:
    def test_worked_example_three_short_series(self):
        mod = _mod()
        values = [4.0, 5.0, 6.0, 9.0]
        # mean 6; sample var (4 + 1 + 0 + 9) / 3 = 14/3; stdev sqrt(14/3)
        assert mod.mean_of(values) == pytest.approx(6.0, abs=1e-12)
        assert mod.stdev_of(values) == pytest.approx(2.160247, abs=1e-6)
        assert mod.z_score(9.0, 6.0, 2.160247) == pytest.approx(1.388730, abs=1e-5)
        # 3 of 4 strictly below 9.0; none below 4.0
        assert mod.pct_below(values, 9.0) == pytest.approx(75.0, abs=1e-12)
        assert mod.pct_below(values, 4.0) == pytest.approx(0.0, abs=1e-12)

    def test_stdev_needs_two_values_and_z_never_divides_by_zero(self):
        mod = _mod()
        assert mod.stdev_of([5.0]) is None
        assert mod.stdev_of([]) is None
        assert mod.z_score(5.0, 5.0, None) is None
        assert mod.z_score(5.0, 5.0, 0.0) is None

    def test_calibration_pins_against_the_5y_fixture(self):
        mod = _mod()
        series = mod.compute_series(_closes_rows())
        assert len(series) == PIN_5Y_COUNT
        spreads = [row["spread"] for row in series]
        assert all(v is not None for v in spreads)
        assert spreads[-1] == pytest.approx(PIN_LAST_SPREAD, abs=1e-6)
        stats = mod.compute_stats(series)
        assert stats["count"] == PIN_5Y_COUNT
        assert stats["high"] == pytest.approx(PIN_HIGH, abs=1e-6)
        assert stats["high_date"] == PIN_HIGH_DATE
        assert stats["low"] == pytest.approx(PIN_LOW, abs=1e-6)
        assert stats["low_date"] == PIN_LOW_DATE
        assert stats["mean"] == pytest.approx(PIN_MEAN, abs=1e-6)
        assert stats["stdev"] == pytest.approx(PIN_STDEV, abs=1e-6)
        assert stats["last"] == pytest.approx(PIN_LAST_SPREAD, abs=1e-6)
        current = mod.build_current(series, stats)
        assert current["date"] == PIN_5Y_LAST
        assert current["spx_iv"] == pytest.approx(PIN_LAST_SPX, abs=1e-9)
        assert current["ndx_iv"] == pytest.approx(PIN_LAST_NDX, abs=1e-9)
        assert current["spread"] == pytest.approx(PIN_LAST_SPREAD, abs=1e-6)
        assert current["z_score"] == pytest.approx(PIN_Z_LAST, abs=1e-6)
        assert current["pctile"] == pytest.approx(PIN_PCTILE_LAST, abs=1e-6)
        assert current["change_1d"] == pytest.approx(PIN_CHANGE_1D, abs=1e-6)
        assert current["regime"] == "NORMAL"

    def test_calibration_pins_against_the_1m_sample(self):
        mod = _mod()
        merged = mod.merge_history([], _sample_legs())
        series = mod.compute_series(merged)
        assert len(series) == PIN_1M_COUNT
        assert series[0]["date"] == PIN_1M_FIRST
        assert series[0]["spread"] == pytest.approx(PIN_1M_FIRST_SPREAD, abs=1e-6)
        assert series[-1]["spread"] == pytest.approx(PIN_LAST_SPREAD, abs=1e-6)
        stats = mod.compute_stats(series)
        assert stats["mean"] == pytest.approx(PIN_1M_MEAN, abs=1e-6)
        assert stats["stdev"] == pytest.approx(PIN_1M_STDEV, abs=1e-6)
        assert stats["high"] == pytest.approx(PIN_1M_MAX, abs=1e-6)
        assert stats["low"] == pytest.approx(PIN_1M_MIN, abs=1e-6)

    def test_stats_are_none_on_an_empty_series(self):
        assert _mod().compute_stats([]) is None


class TestClassifyRegime:
    def test_band_boundaries_are_strict(self):
        mod = _mod()
        assert mod.classify_regime(-3.0) == "COMPRESSED"
        assert mod.classify_regime(-1.001) == "COMPRESSED"
        assert mod.classify_regime(-1.0) == "NORMAL"        # boundary belongs up
        assert mod.classify_regime(0.0) == "NORMAL"
        assert mod.classify_regime(0.999) == "NORMAL"
        assert mod.classify_regime(1.0) == "ELEVATED"
        assert mod.classify_regime(1.999) == "ELEVATED"
        assert mod.classify_regime(2.0) == "EXTREME"
        assert mod.classify_regime(4.5) == "EXTREME"

    def test_null_z_has_no_regime(self):
        assert _mod().classify_regime(None) is None


class TestMergeHistory:
    def test_fetched_leg_wins_over_stored_row_for_the_same_date(self):
        mod = _mod()
        stored = [{"date": "2026-09-02", "spx_iv": 0.99, "ndx_iv": 0.98}]
        fetched = {
            "SPX": [{"date": "2026-09-02", "iv": 0.121}],
            "NDX": [{"date": "2026-09-02", "iv": 0.176}],
        }
        merged = mod.merge_history(stored, fetched)
        assert merged == [{"date": "2026-09-02", "spx_iv": 0.121, "ndx_iv": 0.176}]

    def test_one_leg_restated_keeps_the_stored_other_leg(self):
        mod = _mod()
        stored = [{"date": "2026-09-02", "spx_iv": 0.121, "ndx_iv": 0.176}]
        fetched = {"SPX": [{"date": "2026-09-02", "iv": 0.122}], "NDX": []}
        merged = mod.merge_history(stored, fetched)
        assert merged == [{"date": "2026-09-02", "spx_iv": 0.122, "ndx_iv": 0.176}]

    def test_unpaired_date_is_dropped_and_counted(self):
        mod = _mod()
        fetched = {
            "SPX": [{"date": "2026-09-01", "iv": 0.12}, {"date": "2026-09-02", "iv": 0.121}],
            "NDX": [{"date": "2026-09-02", "iv": 0.176}],
        }
        merged = mod.merge_history([], fetched)
        assert [row["date"] for row in merged] == ["2026-09-02"]
        assert mod.count_unpaired([], fetched) == 1

    def test_non_positive_leg_close_drops_the_date(self):
        mod = _mod()
        fetched = {
            "SPX": [{"date": "2026-09-01", "iv": 0.0}, {"date": "2026-09-02", "iv": 0.121}],
            "NDX": [{"date": "2026-09-01", "iv": 0.17}, {"date": "2026-09-02", "iv": 0.176}],
        }
        merged = mod.merge_history([], fetched)
        assert [row["date"] for row in merged] == ["2026-09-02"]

    def test_merged_dates_ascend(self):
        mod = _mod()
        legs = _sample_legs()
        legs = {sym: list(reversed(rows)) for sym, rows in legs.items()}
        merged = mod.merge_history([], legs)
        dates = [row["date"] for row in merged]
        assert dates == sorted(dates)


class TestOutlierGate:
    """A single-session leg close that deviates >50% from BOTH neighbours is
    a bad print, not a vol event. There is no second feed to repair from, so
    the session is excluded (spread null) and reported."""

    def test_5y_fixture_flags_nothing_on_either_leg(self):
        mod = _mod()
        rows = _closes_rows()
        assert mod.detect_outliers(rows, "spx_iv") == []
        assert mod.detect_outliers(rows, "ndx_iv") == []

    def test_the_real_inversion_is_not_flagged(self):
        """2025-04-08 SPX 0.449 between 0.393 and 0.256: 0.449/0.256 = 1.75
        but 0.449/0.393 = 1.14, so only one neighbour qualifies."""
        mod = _mod()
        rows = [
            {"date": "2025-04-07", "spx_iv": 0.39292582, "ndx_iv": 0.41119738},
            {"date": "2025-04-08", "spx_iv": 0.4492327, "ndx_iv": 0.41626135},
            {"date": "2025-04-09", "spx_iv": 0.25584944, "ndx_iv": 0.29315454},
        ]
        assert mod.detect_outliers(rows, "spx_iv") == []
        assert mod.detect_outliers(rows, "ndx_iv") == []

    def test_high_side_needs_both_neighbours(self):
        mod = _mod()
        both = [
            {"date": "d1", "spx_iv": 0.115, "ndx_iv": 0.17},
            {"date": "d2", "spx_iv": 0.244, "ndx_iv": 0.17},   # > 1.5x both sides
            {"date": "d3", "spx_iv": 0.125, "ndx_iv": 0.17},
        ]
        assert mod.detect_outliers(both, "spx_iv") == ["d2"]
        assert mod.detect_outliers(both, "ndx_iv") == []
        one_side = [
            {"date": "d1", "spx_iv": 0.115, "ndx_iv": 0.17},
            {"date": "d2", "spx_iv": 0.244, "ndx_iv": 0.17},
            {"date": "d3", "spx_iv": 0.240, "ndx_iv": 0.17},   # a real regime shift
        ]
        assert mod.detect_outliers(one_side, "spx_iv") == []

    def test_low_side_outlier(self):
        mod = _mod()
        rows = [
            {"date": "d1", "spx_iv": 0.12, "ndx_iv": 0.20},
            {"date": "d2", "spx_iv": 0.12, "ndx_iv": 0.05},    # < both / 1.5
            {"date": "d3", "spx_iv": 0.12, "ndx_iv": 0.21},
        ]
        assert mod.detect_outliers(rows, "ndx_iv") == ["d2"]

    def test_boundary_is_exactly_the_ratio_and_strict(self):
        mod = _mod()
        rows = [
            {"date": "d1", "spx_iv": 0.10, "ndx_iv": 0.2},
            {"date": "d2", "spx_iv": 0.15, "ndx_iv": 0.2},     # == 1.5x, NOT an outlier
            {"date": "d3", "spx_iv": 0.10, "ndx_iv": 0.2},
        ]
        assert mod.detect_outliers(rows, "spx_iv") == []

    def test_edges_without_two_neighbours_are_never_flagged(self):
        mod = _mod()
        rows = [
            {"date": "d1", "spx_iv": 0.50, "ndx_iv": 0.2},
            {"date": "d2", "spx_iv": 0.10, "ndx_iv": 0.2},
        ]
        assert mod.detect_outliers(rows, "spx_iv") == []

    def test_excluded_session_has_null_spread_and_is_reported(self):
        mod = _mod()
        rows = [
            {"date": "d1", "spx_iv": 0.115, "ndx_iv": 0.17},
            {"date": "d2", "spx_iv": 0.244, "ndx_iv": 0.17},
            {"date": "d3", "spx_iv": 0.125, "ndx_iv": 0.17},
        ]
        series = mod.compute_series(rows)
        assert [row["spread"] is None for row in series] == [False, True, False]
        # raw legs survive on the excluded row
        assert series[1]["spx_iv"] == pytest.approx(0.244, abs=1e-12)
        assert mod.excluded_sessions(rows) == [
            {"date": "d2", "leg": "spx_iv", "iv": 0.244, "prev_iv": 0.115, "next_iv": 0.125}
        ]
        stats = mod.compute_stats(series)
        assert stats["count"] == 2                     # nulls skipped


# ── storage ───────────────────────────────────────────────────────


class _RecordingConnection:
    """sqlite3 stand-in for the Hrana client that refuses executemany."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self.statements: list[tuple[str, tuple]] = []
        self.commits = 0

    def execute(self, sql: str, params: tuple = ()):  # noqa: D102
        self.statements.append((sql, tuple(params)))
        return self._conn.execute(sql, params)

    def executemany(self, *_args, **_kwargs):  # noqa: D102
        raise AssertionError("executemany is one Hrana round-trip per row")

    def commit(self):  # noqa: D102
        self.commits += 1


def _schema_conn() -> sqlite3.Connection:
    assert MIGRATION.exists(), "scripts/db/migrations/0069_iv_spread.sql must exist"
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)")
    conn.executescript(MIGRATION.read_text())
    return conn


@pytest.fixture
def iv_spread_db(monkeypatch: pytest.MonkeyPatch):
    """In-memory sqlite carrying only the 0069 schema, wired into the writer."""
    conn = _schema_conn()
    recording = _RecordingConnection(conn)

    from db import writer as writer_mod

    monkeypatch.setattr(writer_mod, "get_db", lambda: recording)
    try:
        yield writer_mod, recording, conn
    finally:
        conn.close()


class TestIvSpreadStorage:
    def test_migration_registers_version_69(self):
        conn = _schema_conn()
        assert conn.execute(
            "SELECT version FROM schema_migrations WHERE version = 69"
        ).fetchone() == (69,)

    def test_table_columns_pk_and_index(self):
        conn = _schema_conn()
        info = conn.execute("PRAGMA table_info(iv_spread_history)").fetchall()
        columns = {row[1]: row for row in info}
        assert set(columns) == {"date", "spx_iv", "ndx_iv", "spread", "recorded_at"}
        assert columns["date"][2] == "TEXT" and columns["date"][5] == 1        # PRIMARY KEY
        assert columns["spx_iv"][2] == "REAL" and columns["spx_iv"][3] == 1    # NOT NULL
        assert columns["ndx_iv"][2] == "REAL" and columns["ndx_iv"][3] == 1
        assert columns["spread"][2] == "REAL" and columns["spread"][3] == 0    # nullable
        assert columns["recorded_at"][2] == "TEXT" and columns["recorded_at"][3] == 1
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(iv_spread_history)")}
        assert "idx_iv_spread_history_date" in indexes

    def test_upsert_writes_rows_and_is_idempotent_on_date(self, iv_spread_db):
        writer_mod, _recording, conn = iv_spread_db
        rows = [
            {"date": "2026-09-01", "spx_iv": 0.1235, "ndx_iv": 0.1747, "spread": 5.121116},
            {"date": "2026-09-02", "spx_iv": 0.12104312, "ndx_iv": 0.1758578, "spread": 5.481468},
        ]
        writer_mod.upsert_iv_spread_rows(rows, recorded_at="2026-09-02T22:15:00Z")
        writer_mod.upsert_iv_spread_rows(
            [{**rows[1], "spread": 5.5}], recorded_at="2026-09-03T22:15:00Z"
        )
        stored = conn.execute(
            "SELECT date, spx_iv, ndx_iv, spread, recorded_at FROM iv_spread_history ORDER BY date"
        ).fetchall()
        assert len(stored) == 2
        assert stored[1][0] == "2026-09-02"
        assert stored[1][3] == pytest.approx(5.5)
        assert stored[1][4] == "2026-09-03T22:15:00Z"
        assert stored[0][4] == "2026-09-02T22:15:00Z"

    def test_upsert_persists_null_spread(self, iv_spread_db):
        writer_mod, _recording, conn = iv_spread_db
        writer_mod.upsert_iv_spread_rows(
            [{"date": "2026-08-17", "spx_iv": 0.2443, "ndx_iv": 0.18, "spread": None}],
            recorded_at="2026-09-02T22:15:00Z",
        )
        assert conn.execute("SELECT spread FROM iv_spread_history").fetchone() == (None,)

    def test_upsert_batches_at_400_rows_and_never_executemany(self, iv_spread_db):
        writer_mod, recording, conn = iv_spread_db
        rows = [
            {
                "date": (date(2020, 1, 1) + timedelta(days=i)).isoformat(),
                "spx_iv": 0.10 + i * 0.0001,
                "ndx_iv": 0.15 + i * 0.0001,
                "spread": 5.0,
            }
            for i in range(900)
        ]
        writer_mod.upsert_iv_spread_rows(rows, recorded_at="2026-09-02T22:15:00Z")
        assert len(recording.statements) == 3
        assert [sql.count("(?, ?, ?, ?, ?)") for sql, _ in recording.statements] == [400, 400, 100]
        assert all("ON CONFLICT(date) DO UPDATE" in sql for sql, _ in recording.statements)
        assert conn.execute("SELECT COUNT(*) FROM iv_spread_history").fetchone()[0] == 900
        assert recording.commits == 1

    def test_empty_rows_write_nothing(self, iv_spread_db):
        writer_mod, recording, _conn = iv_spread_db
        writer_mod.upsert_iv_spread_rows([], recorded_at="2026-09-02T22:15:00Z")
        assert recording.statements == []


# ── job orchestration ─────────────────────────────────────────────


class _FakeWriter:
    def __init__(self, row_exc: Optional[Exception] = None):
        self.replica_guards = 0
        self.rows: list[tuple[list[dict[str, Any]], Optional[str]]] = []
        self.snapshots: list[tuple[str, str, dict[str, Any]]] = []
        self.health: list[tuple[str, str, Optional[dict[str, Any]]]] = []
        self.row_exc = row_exc

    def ensure_no_replica_for_writers(self):
        self.replica_guards += 1

    def upsert_iv_spread_rows(self, rows, recorded_at=None):
        if self.row_exc is not None:
            raise self.row_exc
        self.rows.append((list(rows), recorded_at))

    def upsert_scan_snapshot(self, service, scan_time, payload):
        self.snapshots.append((service, scan_time, payload))

    def record_service_health(self, service, status, finished_at=None, **kwargs):
        self.health.append((service, status, kwargs.get("error")))


class _StubIb:
    """ib fetch stand-in: (duration) -> {"SPX": [{date, iv}], "NDX": [...]} or raises."""

    def __init__(
        self,
        legs: Optional[dict[str, list[dict[str, Any]]]] = None,
        exc: Optional[Exception] = None,
    ):
        self.legs = legs or {"SPX": [], "NDX": []}
        self.exc = exc
        self.calls: list[str] = []

    def __call__(self, duration: str) -> dict[str, list[dict[str, Any]]]:
        self.calls.append(duration)
        if self.exc is not None:
            raise self.exc
        return {sym: [dict(row) for row in rows] for sym, rows in self.legs.items()}


# radon-iv-spread.timer fires 22:15 UTC = 18:15 ET, after the close whose
# session this run finalizes.
NOW_RUN = datetime(2026, 9, 2, 22, 15, tzinfo=timezone.utc)


@pytest.fixture
def job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """fetch_iv_spread with its JSON cache relocated, writer faked, gate open."""
    mod = _mod()
    monkeypatch.setattr(mod, "IV_SPREAD_JSON", tmp_path / "iv_spread.json")
    fake = _FakeWriter()
    monkeypatch.setattr(mod, "writer", fake)
    monkeypatch.setattr(mod, "gateway_auth_state", lambda: "authenticated")
    monkeypatch.setattr(mod, "load_history", lambda: [])
    return mod, fake, tmp_path


class TestRunHappyPath:
    def test_ib_run_computes_writes_rows_snapshot_and_heartbeat(self, job):
        mod, fake, _tmp = job
        ib = _StubIb(_sample_legs())
        payload = mod.run(ib_fetch=ib, now=NOW_RUN)

        assert ib.calls == [mod.INCREMENTAL_DURATION]
        assert payload["status"] == "ok"
        assert payload["source"] == "ib"
        assert payload["as_of"] == PIN_5Y_LAST
        assert payload["count"] == PIN_1M_COUNT
        assert payload["spread_count"] == PIN_1M_COUNT
        assert payload["dropped_unpaired"] == 0
        assert payload["excluded"] == []
        assert payload["current"]["spread"] == pytest.approx(PIN_LAST_SPREAD, abs=1e-6)
        assert payload["current"]["change_1d"] == pytest.approx(PIN_CHANGE_1D, abs=1e-6)
        assert payload["stats"]["mean"] == pytest.approx(PIN_1M_MEAN, abs=1e-6)
        assert payload["scan_time"].endswith("Z")
        assert payload["series"][-1] == {
            "date": PIN_5Y_LAST,
            "spx_iv": pytest.approx(PIN_LAST_SPX, abs=1e-9),
            "ndx_iv": pytest.approx(PIN_LAST_NDX, abs=1e-9),
            "spread": pytest.approx(PIN_LAST_SPREAD, abs=1e-6),
        }

        assert fake.replica_guards >= 1
        assert [snap[0] for snap in fake.snapshots] == ["iv-spread"]
        assert fake.health == [("iv-spread", "ok", None)]
        assert len(fake.rows) == 1
        assert len(fake.rows[0][0]) == PIN_1M_COUNT
        assert fake.rows[0][1] == payload["scan_time"]
        assert set(fake.rows[0][0][0]) >= {"date", "spx_iv", "ndx_iv", "spread"}

    def test_stored_history_is_merged_under_the_daily_tail(self, job, monkeypatch):
        mod, fake, _tmp = job
        monkeypatch.setattr(mod, "load_history", lambda: _closes_rows()[:-5])
        legs = _sample_legs()
        payload = mod.run(ib_fetch=_StubIb(legs), now=NOW_RUN)
        assert payload["count"] == PIN_5Y_COUNT
        assert payload["stats"]["high_date"] == PIN_HIGH_DATE
        assert payload["current"]["z_score"] == pytest.approx(PIN_Z_LAST, abs=1e-6)
        assert payload["current"]["regime"] == "NORMAL"
        # the full recomputed series is what lands in Turso
        assert len(fake.rows[0][0]) == PIN_5Y_COUNT

    def test_backfill_uses_the_backfill_duration(self, job):
        mod, _fake, _tmp = job
        ib = _StubIb(_sample_legs())
        mod.run(ib_fetch=ib, now=NOW_RUN, backfill=True)
        assert ib.calls == [mod.BACKFILL_DURATION]


class TestUnchangedSource:
    def test_unchanged_rows_skip_the_row_upsert_but_heartbeat(self, job, monkeypatch):
        mod, fake, _tmp = job
        history = mod.merge_history([], _sample_legs())
        monkeypatch.setattr(mod, "load_history", lambda: history)
        payload = mod.run(ib_fetch=_StubIb(_sample_legs()), now=NOW_RUN)
        assert payload["status"] == "ok"
        assert fake.rows == []                                      # no row writes
        assert [snap[0] for snap in fake.snapshots] == ["iv-spread"]  # snapshot still lands
        assert fake.health == [("iv-spread", "ok", None)]           # heartbeat still beats


class TestSourceLadder:
    def test_unauthenticated_gateway_skips_ib_and_serves_cache(self, job, monkeypatch):
        mod, fake, tmp_path = job
        monkeypatch.setattr(mod, "gateway_auth_state", lambda: "2fa_pending")
        (tmp_path / "iv_spread.json").write_text(json.dumps({
            "scan_time": "2026-09-01T22:15:00Z", "status": "ok", "source": "ib",
            "as_of": "2026-09-01", "series": [], "current": None, "stats": None,
        }))
        ib = _StubIb(_sample_legs())
        payload = mod.run(ib_fetch=ib, now=NOW_RUN)
        assert ib.calls == []                     # the socket was never attempted
        assert payload["status"] == "stale_source"
        assert fake.health[0][1] == "error"

    def test_unknown_health_state_still_attempts_ib(self, job, monkeypatch):
        mod, _fake, _tmp = job
        monkeypatch.setattr(mod, "gateway_auth_state", lambda: None)
        ib = _StubIb(_sample_legs())
        payload = mod.run(ib_fetch=ib, now=NOW_RUN)
        assert ib.calls == [mod.INCREMENTAL_DURATION]
        assert payload["source"] == "ib"

    def test_one_dead_leg_is_an_ib_failure(self, job):
        mod, fake, _tmp = job
        legs = _sample_legs()
        legs["NDX"] = []
        with pytest.raises(Exception):
            mod.run(ib_fetch=_StubIb(legs), now=NOW_RUN)
        assert fake.snapshots == []
        assert fake.rows == []

    def test_backfill_without_ib_is_a_hard_error(self, job, monkeypatch):
        mod, _fake, _tmp = job
        monkeypatch.setattr(mod, "gateway_auth_state", lambda: "2fa_pending")
        with pytest.raises(Exception):
            mod.run(ib_fetch=_StubIb(_sample_legs()), now=NOW_RUN, backfill=True)


class TestIbDown:
    def test_cached_payload_is_reserved_with_stale_source_and_error_heartbeat(self, job):
        mod, fake, tmp_path = job
        cached = {
            "scan_time": "2026-09-01T22:15:00Z",
            "status": "ok",
            "source": "ib",
            "as_of": "2026-09-01",
            "series": [{"date": "2026-09-01", "spx_iv": 0.1235, "ndx_iv": 0.1747, "spread": 5.121116}],
        }
        (tmp_path / "iv_spread.json").write_text(json.dumps(cached))
        payload = mod.run(ib_fetch=_StubIb(exc=TimeoutError("gateway hang")), now=NOW_RUN)
        assert payload["status"] == "stale_source"
        assert payload["as_of"] == "2026-09-01"
        assert payload["scan_time"] != cached["scan_time"]      # fresh stamp
        assert fake.rows == []
        assert [snap[0] for snap in fake.snapshots] == ["iv-spread"]
        assert len(fake.health) == 1
        assert fake.health[0][0] == "iv-spread"
        assert fake.health[0][1] == "error"
        assert "IB" in fake.health[0][2]["message"]

    def test_no_cache_raises_never_caches_empty(self, job):
        mod, fake, _tmp = job
        with pytest.raises(Exception):
            mod.run(ib_fetch=_StubIb(exc=TimeoutError("down")), now=NOW_RUN)
        assert fake.snapshots == []
        assert fake.rows == []


class TestRowWriteFailureIsVisible:
    def test_row_upsert_failure_folds_into_an_error_heartbeat(self, job, monkeypatch):
        mod, _fake, _tmp = job
        fake = _FakeWriter(row_exc=RuntimeError("hrana 502"))
        monkeypatch.setattr(mod, "writer", fake)
        payload = mod.run(ib_fetch=_StubIb(_sample_legs()), now=NOW_RUN)
        assert payload["status"] == "ok"
        assert [snap[0] for snap in fake.snapshots] == ["iv-spread"]    # snapshot still lands
        assert fake.health[0][1] == "error"
        assert fake.health[0][2]["class"] == "db_write_failed"


class TestJsonFallback:
    def test_payload_is_mirrored_to_disk_atomically(self, job):
        mod, _fake, tmp_path = job
        payload = mod.run(ib_fetch=_StubIb(_sample_legs()), now=NOW_RUN)
        on_disk = json.loads((tmp_path / "iv_spread.json").read_text())
        assert on_disk["scan_time"] == payload["scan_time"]
        assert on_disk["count"] == PIN_1M_COUNT
        assert not (tmp_path / "iv_spread.json.tmp").exists()

    def test_history_rehydrates_from_the_json_fallback(self, job):
        mod, _fake, tmp_path = job
        (tmp_path / "iv_spread.json").write_text(json.dumps({
            "scan_time": "2026-09-01T22:15:00Z",
            "series": [
                {"date": "2026-09-01", "spx_iv": 0.1235, "ndx_iv": 0.1747, "spread": 5.121116},
                {"date": "2026-08-31", "spx_iv": 0.1240, "ndx_iv": 0.1706, "spread": 4.66},
            ],
        }))
        rows = mod._history_from_json()
        assert [row["date"] for row in rows] == ["2026-08-31", "2026-09-01"]
        assert set(rows[0]) == {"date", "spx_iv", "ndx_iv"}
