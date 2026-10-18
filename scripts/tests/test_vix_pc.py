"""VIX P/C indicator — 5-session cumulative VIX put/call tests.

Spec: docs/indicators/vix-pc.md.

Ground truth is read from the checked-in UW fixture, never from the
network and never from Turso:

  fixtures/vix_pc_sample.json — 500 VIX options-volume rows
  (2024-10-18 .. 2026-10-16) captured 2026-10-18 from
  GET /api/stock/VIX/options-volume?limit=500.

Fixture-derived expectations were derived by inspecting the fixture
with a scratch script (2026-10-18), NOT by calling the functions under
test.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE: dict[str, Any] = json.loads((FIXTURES / "vix_pc_sample.json").read_text())
MIGRATION = Path(__file__).parents[1] / "db" / "migrations" / "0097_vix_pc.sql"

RAW_ROWS: list[dict[str, Any]] = SAMPLE["data"]

# ── fixture-derived pins (scratch-derived 2026-10-18) ─────────────
PIN_BAR_COUNT = 500
PIN_FIRST_DATE = "2024-10-18"
PIN_LAST_DATE = "2026-10-16"
PIN_LAST_PUT = 573686
PIN_LAST_CALL = 1729988
PIN_LAST_DAILY = 0.33161270482801036
PIN_LAST5_PUT_SUM = 1290323
PIN_LAST5_CALL_SUM = 4046818
PIN_LAST_CUM5 = 0.3188487844029556
PIN_CUM5_COUNT = 496
PIN_SERIES_MIN = 0.2417297884911582
PIN_SERIES_MIN_DATE = "2026-10-08"
PIN_SERIES_MAX = 1.6973206456466932
PIN_SERIES_P10 = 0.3435408949777816
PIN_SERIES_P90 = 0.6760256318262985
PIN_LAST_PERCENTILE = 4.032258064516129  # 20 / 496 * 100
PIN_OCT8_PUT_SUM = 863665
PIN_OCT8_CALL_SUM = 3572853
PIN_JAN2025_COUNT = 449


def _mod():
    """Import the module under test lazily so the red phase fails per-test."""
    import fetch_vix_pc  # type: ignore[import-not-found]

    return fetch_vix_pc


def _sorted_raw() -> list[dict[str, Any]]:
    return sorted(
        (
            {
                "date": row["date"],
                "put_volume": int(row["put_volume"]),
                "call_volume": int(row["call_volume"]),
            }
            for row in RAW_ROWS
        ),
        key=lambda row: row["date"],
    )


# ── fixture integrity ─────────────────────────────────────────────


class TestFixtureIntegrity:
    def test_uw_fixture_shape(self):
        assert SAMPLE["ticker"] == "VIX"
        assert SAMPLE["n"] == PIN_BAR_COUNT
        assert SAMPLE["oldest"] == PIN_FIRST_DATE
        assert SAMPLE["newest"] == PIN_LAST_DATE
        assert len(RAW_ROWS) == PIN_BAR_COUNT
        first = RAW_ROWS[0]
        assert first["date"] == PIN_LAST_DATE
        assert first["put_volume"] == PIN_LAST_PUT
        assert first["call_volume"] == PIN_LAST_CALL

    def test_covers_january_2025(self):
        dates = {row["date"] for row in RAW_ROWS}
        assert sum(1 for d in dates if d >= "2025-01-01") == PIN_JAN2025_COUNT
        assert "2025-01-02" in dates


# ── pure math ─────────────────────────────────────────────────────


class TestNamedConstants:
    def test_module_constants_match_the_spec(self):
        mod = _mod()
        assert mod.WINDOW == 5
        assert mod.MIN_OBSERVATIONS == 5
        assert mod.TICKER == "VIX"
        assert mod.UW_LIMIT == 500
        assert mod.BAND_PCTL_LOW == 10.0
        assert mod.BAND_PCTL_HIGH == 90.0


class TestDailyRatio:
    def test_worked_example_one_nominal(self):
        mod = _mod()
        # 50 / 100 = 0.5 exactly
        assert mod.daily_ratio(50, 100) == pytest.approx(0.5, abs=1e-12)

    def test_zero_call_volume_is_none_not_zero(self):
        mod = _mod()
        assert mod.daily_ratio(10, 0) is None
        assert mod.daily_ratio(10, -1) is None


class TestCum5Ratio:
    def test_worked_example_one_nominal(self):
        mod = _mod()
        rows = [
            {"put_volume": 10, "call_volume": 100},
            {"put_volume": 20, "call_volume": 100},
            {"put_volume": 30, "call_volume": 100},
            {"put_volume": 40, "call_volume": 100},
            {"put_volume": 50, "call_volume": 100},
        ]
        # 150 / 500 = 0.3 exactly
        assert mod.cum5_ratio(rows) == pytest.approx(0.3, abs=1e-12)

    def test_zero_call_sum_is_none(self):
        mod = _mod()
        rows = [{"put_volume": 1, "call_volume": 0} for _ in range(5)]
        assert mod.cum5_ratio(rows) is None

    def test_short_window_is_none(self):
        mod = _mod()
        assert mod.cum5_ratio([{"put_volume": 1, "call_volume": 1}] * 4) is None


class TestPercentileValue:
    def test_linear_interpolation_on_even_span(self):
        mod = _mod()
        # n=5, p=50 -> k=2, exact median
        assert mod.percentile_value([1.0, 2.0, 3.0, 4.0, 5.0], 50) == pytest.approx(3.0, abs=1e-12)

    def test_empty_is_none(self):
        mod = _mod()
        assert mod.percentile_value([], 10) is None


class TestClassifyRegime:
    def test_band_boundaries_are_strict(self):
        mod = _mod()
        assert mod.classify_regime(0.0) == "CAUTIOUS"
        assert mod.classify_regime(9.999) == "CAUTIOUS"
        assert mod.classify_regime(10.0) == "NORMAL"
        assert mod.classify_regime(89.999) == "NORMAL"
        assert mod.classify_regime(90.0) == "COMPLACENT"
        assert mod.classify_regime(100.0) == "COMPLACENT"

    def test_null_percentile_has_no_regime(self):
        mod = _mod()
        assert mod.classify_regime(None) is None


class TestComputeSeries:
    def test_first_four_sessions_have_no_cum5(self):
        mod = _mod()
        rows = [
            {"date": "2025-01-02", "put_volume": 10, "call_volume": 100},
            {"date": "2025-01-03", "put_volume": 20, "call_volume": 100},
            {"date": "2025-01-06", "put_volume": 30, "call_volume": 100},
            {"date": "2025-01-07", "put_volume": 40, "call_volume": 100},
        ]
        series = mod.compute_series(rows)
        assert len(series) == 4
        assert all(row["cum5_ratio"] is None for row in series)
        assert series[-1]["daily_ratio"] == pytest.approx(0.4, abs=1e-12)

    def test_fifth_session_emits_the_worked_example(self):
        mod = _mod()
        rows = [
            {"date": "2025-01-02", "put_volume": 10, "call_volume": 100},
            {"date": "2025-01-03", "put_volume": 20, "call_volume": 100},
            {"date": "2025-01-06", "put_volume": 30, "call_volume": 100},
            {"date": "2025-01-07", "put_volume": 40, "call_volume": 100},
            {"date": "2025-01-08", "put_volume": 50, "call_volume": 100},
        ]
        series = mod.compute_series(rows)
        assert series[-1]["cum5_ratio"] == pytest.approx(0.3, abs=1e-12)
        assert series[-1]["daily_ratio"] == pytest.approx(0.5, abs=1e-12)

    def test_calibration_pin_against_the_uw_fixture(self):
        mod = _mod()
        series = mod.compute_series(_sorted_raw())
        assert len(series) == PIN_BAR_COUNT
        latest = series[-1]
        assert latest["date"] == PIN_LAST_DATE
        assert latest["put_volume"] == PIN_LAST_PUT
        assert latest["call_volume"] == PIN_LAST_CALL
        assert latest["daily_ratio"] == pytest.approx(PIN_LAST_DAILY, abs=1e-9)
        assert latest["cum5_ratio"] == pytest.approx(PIN_LAST_CUM5, abs=1e-9)
        oct8 = next(row for row in series if row["date"] == PIN_SERIES_MIN_DATE)
        assert oct8["cum5_ratio"] == pytest.approx(PIN_SERIES_MIN, abs=1e-9)
        valid = [row["cum5_ratio"] for row in series if row["cum5_ratio"] is not None]
        assert len(valid) == PIN_CUM5_COUNT
        assert min(valid) == pytest.approx(PIN_SERIES_MIN, abs=1e-9)
        assert max(valid) == pytest.approx(PIN_SERIES_MAX, abs=1e-9)


class TestParseUwRows:
    def test_maps_the_fixture_and_sorts_ascending(self):
        mod = _mod()
        rows = mod.parse_uw_rows({"data": RAW_ROWS})
        assert [row["date"] for row in rows] == sorted(row["date"] for row in rows)
        assert rows[0]["date"] == PIN_FIRST_DATE
        assert rows[-1]["date"] == PIN_LAST_DATE
        assert rows[-1]["put_volume"] == PIN_LAST_PUT
        assert all(row["source"] == "uw" for row in rows)

    def test_malformed_rows_are_skipped(self):
        mod = _mod()
        rows = mod.parse_uw_rows(
            {
                "data": [
                    {"date": "2026-10-16", "put_volume": 10, "call_volume": 20},
                    {"date": "2026-10-15", "put_volume": "nope", "call_volume": 20},
                    {"date": None, "put_volume": 1, "call_volume": 1},
                    {"put_volume": 1, "call_volume": 1},
                ]
            }
        )
        assert [row["date"] for row in rows] == ["2026-10-16"]


class TestBuildCurrent:
    def test_fixture_latest_is_cautious_at_the_4th_percentile(self):
        mod = _mod()
        series = mod.compute_series(_sorted_raw())
        current = mod.build_current(series)
        assert current["date"] == PIN_LAST_DATE
        assert current["cum5_ratio"] == pytest.approx(PIN_LAST_CUM5, abs=1e-9)
        assert current["percentile"] == pytest.approx(PIN_LAST_PERCENTILE, abs=1e-9)
        assert current["regime"] == "CAUTIOUS"
        assert current["series_min"] == pytest.approx(PIN_SERIES_MIN, abs=1e-9)
        assert current["series_min_date"] == PIN_SERIES_MIN_DATE
        assert current["series_p10"] == pytest.approx(PIN_SERIES_P10, abs=1e-9)
        assert current["series_p90"] == pytest.approx(PIN_SERIES_P90, abs=1e-9)
        assert current["is_series_low"] is False

    def test_oct_8_is_the_multi_year_low(self):
        mod = _mod()
        series = [row for row in mod.compute_series(_sorted_raw()) if row["date"] <= PIN_SERIES_MIN_DATE]
        current = mod.build_current(series)
        assert current["date"] == PIN_SERIES_MIN_DATE
        assert current["cum5_ratio"] == pytest.approx(PIN_SERIES_MIN, abs=1e-9)
        assert current["is_series_low"] is True
        assert current["percentile"] == pytest.approx(0.0, abs=1e-12)
        assert current["regime"] == "CAUTIOUS"


# ── storage ───────────────────────────────────────────────────────


class _RecordingConnection:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self.statements: list[tuple[str, tuple]] = []
        self.commits = 0

    def execute(self, sql: str, params: tuple = ()):
        self.statements.append((sql, tuple(params)))
        return self._conn.execute(sql, params)

    def executemany(self, *_args, **_kwargs):
        raise AssertionError("executemany is one Hrana round-trip per row")

    def commit(self):
        self.commits += 1


@pytest.fixture
def vix_pc_db(monkeypatch: pytest.MonkeyPatch):
    assert MIGRATION.exists(), "scripts/db/migrations/0097_vix_pc.sql must exist"
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)"
    )
    conn.executescript(MIGRATION.read_text())
    recording = _RecordingConnection(conn)
    from db import writer as writer_mod

    monkeypatch.setattr(writer_mod, "get_db", lambda: recording)
    try:
        yield writer_mod, recording, conn
    finally:
        conn.close()


class TestVixPcStorage:
    def test_migration_registers_version_97(self):
        assert MIGRATION.exists(), "scripts/db/migrations/0097_vix_pc.sql must exist"
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        conn.executescript(MIGRATION.read_text())
        assert conn.execute(
            "SELECT version FROM schema_migrations WHERE version = 97"
        ).fetchone() == (97,)

    def test_table_columns_pk_and_index(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        conn.executescript(MIGRATION.read_text())
        info = conn.execute("PRAGMA table_info(vix_pc_history)").fetchall()
        columns = {row[1]: row for row in info}
        assert set(columns) == {
            "date",
            "put_volume",
            "call_volume",
            "daily_ratio",
            "cum5_ratio",
            "source",
            "recorded_at",
        }
        assert columns["date"][2] == "TEXT" and columns["date"][5] == 1
        assert columns["put_volume"][3] == 1
        assert columns["call_volume"][3] == 1
        assert columns["daily_ratio"][3] == 0
        assert columns["cum5_ratio"][3] == 0
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(vix_pc_history)")}
        assert "idx_vix_pc_history_date" in indexes

    def test_upsert_writes_rows_and_is_idempotent_on_date(self, vix_pc_db):
        writer_mod, _recording, conn = vix_pc_db
        rows = [
            {
                "date": "2026-10-15",
                "put_volume": 218214,
                "call_volume": 606968,
                "daily_ratio": 0.36,
                "cum5_ratio": 0.32,
                "source": "uw",
            },
            {
                "date": "2026-10-16",
                "put_volume": 573686,
                "call_volume": 1729988,
                "daily_ratio": 0.3316,
                "cum5_ratio": 0.3188,
                "source": "uw",
            },
        ]
        writer_mod.upsert_vix_pc_rows(rows, recorded_at="2026-10-18T02:00:00Z")
        writer_mod.upsert_vix_pc_rows(
            [{**rows[1], "cum5_ratio": PIN_LAST_CUM5}],
            recorded_at="2026-10-18T02:05:00Z",
        )
        stored = conn.execute(
            "SELECT date, put_volume, call_volume, cum5_ratio, recorded_at "
            "FROM vix_pc_history ORDER BY date"
        ).fetchall()
        assert len(stored) == 2
        assert stored[1][0] == "2026-10-16"
        assert stored[1][3] == pytest.approx(PIN_LAST_CUM5)
        assert stored[1][4] == "2026-10-18T02:05:00Z"

    def test_upsert_persists_null_cum5(self, vix_pc_db):
        writer_mod, _recording, conn = vix_pc_db
        writer_mod.upsert_vix_pc_rows(
            [
                {
                    "date": "2024-10-18",
                    "put_volume": 1,
                    "call_volume": 1,
                    "daily_ratio": 1.0,
                    "cum5_ratio": None,
                    "source": "uw",
                }
            ],
            recorded_at="2026-10-18T02:00:00Z",
        )
        assert conn.execute("SELECT cum5_ratio FROM vix_pc_history").fetchone() == (None,)

    def test_upsert_batches_at_400_rows_and_never_executemany(self, vix_pc_db):
        writer_mod, recording, conn = vix_pc_db
        rows = [
            {
                "date": (date(2020, 1, 1) + timedelta(days=i)).isoformat(),
                "put_volume": 10 + i,
                "call_volume": 20 + i,
                "daily_ratio": 0.5,
                "cum5_ratio": 0.4,
                "source": "uw",
            }
            for i in range(900)
        ]
        writer_mod.upsert_vix_pc_rows(rows, recorded_at="2026-10-18T02:00:00Z")
        assert len(recording.statements) == 3
        assert [sql.count("(?, ?, ?, ?, ?, ?, ?)") for sql, _ in recording.statements] == [
            400,
            400,
            100,
        ]
        assert all("ON CONFLICT(date) DO UPDATE" in sql for sql, _ in recording.statements)
        assert conn.execute("SELECT COUNT(*) FROM vix_pc_history").fetchone()[0] == 900

    def test_empty_rows_write_nothing(self, vix_pc_db):
        writer_mod, recording, _conn = vix_pc_db
        writer_mod.upsert_vix_pc_rows([], recorded_at="2026-10-18T02:00:00Z")
        assert recording.statements == []


# ── job orchestration ─────────────────────────────────────────────


class _FakeWriter:
    def __init__(self):
        self.replica_guards = 0
        self.vix_pc_rows: list[tuple[list[dict[str, Any]], Optional[str]]] = []
        self.snapshots: list[tuple[str, str, dict[str, Any]]] = []
        self.health: list[tuple[str, str, Optional[dict[str, Any]]]] = []

    def ensure_no_replica_for_writers(self):
        self.replica_guards += 1

    def upsert_vix_pc_rows(self, rows, recorded_at=None):
        self.vix_pc_rows.append((list(rows), recorded_at))

    def upsert_scan_snapshot(self, service, scan_time, payload):
        self.snapshots.append((service, scan_time, payload))

    def record_service_health(self, service, status, finished_at=None, **kwargs):
        self.health.append((service, status, kwargs.get("error")))


class _StubUw:
    def __init__(self, payload: Optional[dict[str, Any]] = None, exc: Optional[Exception] = None):
        self.payload = payload or {"data": RAW_ROWS}
        self.exc = exc
        self.calls = 0

    def __call__(self) -> dict[str, Any]:
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.payload


NOW_RUN = datetime(2026, 10, 18, 2, 0, tzinfo=timezone.utc)


@pytest.fixture
def job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    mod = _mod()
    monkeypatch.setattr(mod, "VIX_PC_JSON", tmp_path / "vix-pc.json")
    fake = _FakeWriter()
    monkeypatch.setattr(mod, "writer", fake)
    monkeypatch.setattr(mod, "load_history", lambda: [])
    return mod, fake, tmp_path


class TestRunHappyPath:
    def test_uw_run_writes_rows_snapshot_and_heartbeat(self, job):
        mod, fake, _tmp = job
        payload = mod.run(uw_fetch=_StubUw(), now=NOW_RUN)
        assert payload["status"] == "ok"
        assert payload["source"] == "uw"
        assert payload["as_of"] == PIN_LAST_DATE
        assert payload["data_date"] == PIN_LAST_DATE
        assert payload["window"] == 5
        assert payload["count"] == PIN_BAR_COUNT
        assert payload["cum5_count"] == PIN_CUM5_COUNT
        assert payload["current"]["cum5_ratio"] == pytest.approx(PIN_LAST_CUM5, abs=1e-9)
        assert payload["current"]["percentile"] == pytest.approx(PIN_LAST_PERCENTILE, abs=1e-9)
        assert payload["current"]["regime"] == "CAUTIOUS"
        assert payload["scan_time"].endswith("Z")
        assert fake.replica_guards >= 1
        assert [snap[0] for snap in fake.snapshots] == ["vix-pc"]
        assert fake.health == [("vix-pc", "ok", None)]
        assert len(fake.vix_pc_rows) == 1
        assert len(fake.vix_pc_rows[0][0]) == PIN_BAR_COUNT

    def test_unchanged_source_heartbeats_without_row_upserts(self, job, monkeypatch):
        mod, fake, _tmp = job
        history = [
            {
                "date": row["date"],
                "put_volume": row["put_volume"],
                "call_volume": row["call_volume"],
                "source": "uw",
            }
            for row in _sorted_raw()
        ]
        monkeypatch.setattr(mod, "load_history", lambda: history)
        payload = mod.run(uw_fetch=_StubUw(), now=NOW_RUN)
        assert payload["status"] == "ok"
        assert fake.vix_pc_rows == []
        assert [snap[0] for snap in fake.snapshots] == ["vix-pc"]
        assert fake.health == [("vix-pc", "ok", None)]

    def test_uw_down_with_cache_is_stale_source(self, job, tmp_path):
        mod, fake, _ = job
        cached = {
            "scan_time": "2026-10-17T02:00:00Z",
            "status": "ok",
            "source": "uw",
            "as_of": PIN_LAST_DATE,
            "data_date": PIN_LAST_DATE,
            "count": 2,
            "series": [
                {
                    "date": "2026-10-15",
                    "put_volume": 1,
                    "call_volume": 2,
                    "daily_ratio": 0.5,
                    "cum5_ratio": None,
                }
            ],
            "current": {"date": "2026-10-15", "cum5_ratio": None},
            "stats": None,
        }
        (tmp_path / "vix-pc.json").write_text(json.dumps(cached))
        payload = mod.run(uw_fetch=_StubUw(exc=RuntimeError("uw down")), now=NOW_RUN)
        assert payload["status"] == "stale_source"
        assert fake.vix_pc_rows == []
        assert fake.health[0][0] == "vix-pc"
        assert fake.health[0][1] == "error"

    def test_uw_down_without_cache_raises(self, job):
        mod, _fake, _tmp = job
        with pytest.raises(RuntimeError):
            mod.run(uw_fetch=_StubUw(exc=RuntimeError("uw down")), now=NOW_RUN)
