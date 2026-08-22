"""IV RANK indicator — SPY 1M implied volatility rank tests.

Spec: docs/indicators/ivrank.md.

Ground truth is read from two checked-in fixtures, never from the network
and never from Turso:

  - fixtures/iv_rank_ib_sample.json — 251 SPY OPTION_IMPLIED_VOLATILITY
    daily bars (2025-08-22 .. 2026-08-21) captured live from the cloud
    gateway on 2026-08-22, wrapped in {source, contract, bars} metadata.
  - fixtures/iv_rank_uw_sample.json — the UW /api/stock/SPY/iv-rank rows
    (string-typed fields) plus the /volatility/stats single-day shape.

Fixture-derived expectations were derived by inspecting the fixtures with a
scratch script (2026-08-22), NOT by calling the functions under test:
current iv 0.12201147, window low 0.10542261, high 0.26251674,
rank 10.559822, percentile 11.952191 (30 of 251 strictly below).

Hand-computable expectations carry their arithmetic in a comment.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
IB_SAMPLE: dict[str, Any] = json.loads((FIXTURES / "iv_rank_ib_sample.json").read_text())
UW_SAMPLE: dict[str, Any] = json.loads((FIXTURES / "iv_rank_uw_sample.json").read_text())
MIGRATION = Path(__file__).parents[1] / "db" / "migrations" / "0052_ivrank.sql"

IB_BARS: list[dict[str, Any]] = IB_SAMPLE["bars"]
IB_CLOSES: list[float] = [bar["close"] for bar in IB_BARS]

# ── fixture-derived pins (scratch-derived 2026-08-22) ─────────────
PIN_BAR_COUNT = 251
PIN_FIRST_DATE = "2025-08-22"
PIN_LAST_DATE = "2026-08-21"
PIN_CURRENT_IV = 0.12201147
PIN_WINDOW_LOW = 0.10542261
PIN_WINDOW_HIGH = 0.26251674
PIN_CALIBRATION_RANK = 10.559822     # (cur - low) / (high - low) * 100
PIN_CALIBRATION_PCT = 11.952191      # 30 of 251 strictly below, * 100


def _mod():
    """Import the module under test lazily so the red phase fails per-test."""
    import fetch_ivrank  # type: ignore[import-not-found]

    return fetch_ivrank


def _ib_rows() -> list[dict[str, Any]]:
    """The IB fixture bars as the job's fetcher contract emits them."""
    return [{"date": bar["date"], "iv": bar["close"]} for bar in IB_BARS]


def _synthetic_rows(n: int, iv: float = 0.10) -> list[dict[str, Any]]:
    """n consecutive weekday-agnostic sessions of constant iv."""
    start = date(2020, 1, 1)
    return [
        {"date": (start + timedelta(days=i)).isoformat(), "iv": iv, "source": "ib"}
        for i in range(n)
    ]


# ── fixture integrity ─────────────────────────────────────────────


class TestFixtureIntegrity:
    def test_ib_fixture_shape(self):
        assert IB_SAMPLE["what_to_show"] == "OPTION_IMPLIED_VOLATILITY"
        assert IB_SAMPLE["contract"]["symbol"] == "SPY"
        assert len(IB_BARS) == PIN_BAR_COUNT
        assert IB_BARS[0]["date"] == PIN_FIRST_DATE
        assert IB_BARS[-1]["date"] == PIN_LAST_DATE
        assert IB_BARS[-1]["close"] == pytest.approx(PIN_CURRENT_IV, abs=1e-9)

    def test_uw_fixture_shape(self):
        rows = UW_SAMPLE["iv_rank_response"]["data"]
        assert len(rows) >= 1
        first = rows[0]
        # UW serves strings; the mapper owns the coercion.
        assert isinstance(first["volatility"], str)
        assert isinstance(first["iv_rank_1y"], str)
        assert isinstance(first["date"], str)


# ── pure math ─────────────────────────────────────────────────────


class TestRankWindow:
    def test_worked_example_one_nominal(self):
        mod = _mod()
        window = [0.10, 0.12, 0.20, 0.15, 0.14]
        # (0.14 - 0.10) / (0.20 - 0.10) * 100 = 40.0 exactly
        assert mod.rank_window(window) == pytest.approx(40.0, abs=1e-12)
        # two of five strictly below 0.14 -> 40.0 exactly
        assert mod.pct_window(window) == pytest.approx(40.0, abs=1e-12)

    def test_worked_example_two_current_is_the_max(self):
        mod = _mod()
        window = [0.11, 0.13, 0.18]
        assert mod.rank_window(window) == pytest.approx(100.0, abs=1e-12)
        # 2 / 3 * 100
        assert mod.pct_window(window) == pytest.approx(200.0 / 3.0, abs=1e-9)

    def test_worked_example_three_degenerate_is_none_not_zero(self):
        mod = _mod()
        window = [0.15, 0.15, 0.15]
        assert mod.rank_window(window) is None
        assert mod.pct_window(window) == pytest.approx(0.0, abs=1e-12)

    def test_current_is_the_min(self):
        mod = _mod()
        window = [0.30, 0.20, 0.10]
        assert mod.rank_window(window) == pytest.approx(0.0, abs=1e-12)
        assert mod.pct_window(window) == pytest.approx(0.0, abs=1e-12)

    def test_calibration_pin_against_the_ib_fixture(self):
        mod = _mod()
        assert mod.rank_window(IB_CLOSES) == pytest.approx(PIN_CALIBRATION_RANK, abs=1e-6)
        assert mod.pct_window(IB_CLOSES) == pytest.approx(PIN_CALIBRATION_PCT, abs=1e-6)


class TestNamedConstants:
    def test_module_constants_match_the_spec(self):
        mod = _mod()
        assert mod.RANK_WINDOW == 252
        assert mod.MIN_OBSERVATIONS == 252
        assert mod.BACKFILL_DURATION == "5 Y"
        assert mod.INCREMENTAL_DURATION == "1 M"
        assert mod.RANK_SUPPRESSED_MAX == 20.0
        assert mod.RANK_NORMAL_MAX == 50.0
        assert mod.RANK_ELEVATED_MAX == 80.0


class TestClassifyRegime:
    def test_band_boundaries_are_strict(self):
        mod = _mod()
        assert mod.classify_regime(0.0) == "SUPPRESSED"
        assert mod.classify_regime(19.999) == "SUPPRESSED"
        assert mod.classify_regime(20.0) == "NORMAL"       # boundary belongs up
        assert mod.classify_regime(49.999) == "NORMAL"
        assert mod.classify_regime(50.0) == "ELEVATED"
        assert mod.classify_regime(79.999) == "ELEVATED"
        assert mod.classify_regime(80.0) == "EXTREME"
        assert mod.classify_regime(100.0) == "EXTREME"

    def test_null_rank_has_no_regime(self):
        mod = _mod()
        assert mod.classify_regime(None) is None


class TestComputeSeries:
    def test_min_observations_gate_on_the_251_bar_fixture(self):
        """251 < RANK_WINDOW: every row of a 251-bar history carries nulls."""
        mod = _mod()
        series = mod.compute_series(_ib_rows())
        assert len(series) == PIN_BAR_COUNT
        assert all(row["iv_rank"] is None for row in series)
        assert all(row["iv_pct"] is None for row in series)
        # the raw iv still rides on every row
        assert series[-1]["iv"] == pytest.approx(PIN_CURRENT_IV, abs=1e-9)

    def test_rank_appears_exactly_at_the_window_edge(self):
        """252 flat bars then one at 0.20: row 251 is degenerate (None rank,
        0.0 pct), row 252 ranks 100 with pct 251/252*100."""
        mod = _mod()
        rows = _synthetic_rows(252, iv=0.10) + [
            {"date": "2020-09-09", "iv": 0.20, "source": "ib"}
        ]
        series = mod.compute_series(rows)
        assert series[250]["iv_rank"] is None          # only 251 trailing values
        assert series[251]["iv_rank"] is None          # full window but degenerate
        assert series[251]["iv_pct"] == pytest.approx(0.0, abs=1e-12)
        assert series[252]["iv_rank"] == pytest.approx(100.0, abs=1e-12)
        assert series[252]["iv_pct"] == pytest.approx(251.0 / 252.0 * 100.0, abs=1e-9)

    def test_series_dates_ascend(self):
        mod = _mod()
        series = mod.compute_series(_ib_rows())
        dates = [row["date"] for row in series]
        assert dates == sorted(dates)


class TestMergeHistory:
    def test_fetched_ib_row_wins_over_stored_row_for_the_same_date(self):
        mod = _mod()
        stored = [{"date": "2026-08-21", "iv": 0.99, "source": "uw"}]
        fetched = [{"date": "2026-08-21", "iv": 0.122, "source": "ib"}]
        merged = mod.merge_history(stored, fetched)
        assert merged == [{"date": "2026-08-21", "iv": 0.122, "source": "ib"}]

    def test_uw_row_never_overwrites_a_stored_ib_row(self):
        mod = _mod()
        stored = [{"date": "2026-08-21", "iv": 0.122, "source": "ib"}]
        fetched = [{"date": "2026-08-21", "iv": 0.99, "source": "uw"}]
        merged = mod.merge_history(stored, fetched)
        assert merged == [{"date": "2026-08-21", "iv": 0.122, "source": "ib"}]

    def test_uw_row_fills_a_date_ib_never_served(self):
        mod = _mod()
        stored = [{"date": "2026-08-20", "iv": 0.120, "source": "ib"}]
        fetched = [{"date": "2026-08-21", "iv": 0.125, "source": "uw"}]
        merged = mod.merge_history(stored, fetched)
        assert [row["date"] for row in merged] == ["2026-08-20", "2026-08-21"]
        assert merged[-1]["source"] == "uw"


class TestMapUwRows:
    def test_maps_the_fixture_strings_to_floats(self):
        mod = _mod()
        rows = mod.map_uw_rows(UW_SAMPLE["iv_rank_response"]["data"])
        assert all(isinstance(row["iv"], float) for row in rows)
        assert all(row["source"] == "uw" for row in rows)
        by_date = {row["date"]: row for row in rows}
        assert by_date["2026-08-17"]["iv"] == pytest.approx(0.127, abs=1e-9)

    def test_malformed_rows_are_skipped(self):
        mod = _mod()
        rows = mod.map_uw_rows(
            [
                {"date": "2026-08-17", "volatility": "0.127"},
                {"date": "2026-08-18", "volatility": "not-a-number"},
                {"date": None, "volatility": "0.2"},
                {"volatility": "0.2"},
            ]
        )
        assert [row["date"] for row in rows] == ["2026-08-17"]


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


@pytest.fixture
def ivrank_db(monkeypatch: pytest.MonkeyPatch):
    """In-memory sqlite carrying only the 0052 schema, wired into the writer."""
    assert MIGRATION.exists(), "scripts/db/migrations/0052_ivrank.sql must exist"
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


class TestIvrankStorage:
    def test_migration_registers_version_52(self):
        assert MIGRATION.exists(), "scripts/db/migrations/0052_ivrank.sql must exist"
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        conn.executescript(MIGRATION.read_text())
        assert conn.execute(
            "SELECT version FROM schema_migrations WHERE version = 52"
        ).fetchone() == (52,)

    def test_table_columns_pk_and_index(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        conn.executescript(MIGRATION.read_text())
        info = conn.execute("PRAGMA table_info(ivrank_history)").fetchall()
        columns = {row[1]: row for row in info}
        assert set(columns) == {"date", "iv", "iv_rank", "iv_pct", "source", "recorded_at"}
        assert columns["date"][2] == "TEXT" and columns["date"][5] == 1        # PRIMARY KEY
        assert columns["iv"][2] == "REAL" and columns["iv"][3] == 1            # NOT NULL
        assert columns["iv_rank"][2] == "REAL" and columns["iv_rank"][3] == 0  # nullable
        assert columns["iv_pct"][2] == "REAL" and columns["iv_pct"][3] == 0
        assert columns["source"][2] == "TEXT" and columns["source"][3] == 1
        assert columns["recorded_at"][2] == "TEXT" and columns["recorded_at"][3] == 1
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(ivrank_history)")}
        assert "idx_ivrank_history_date" in indexes

    def test_upsert_writes_rows_and_is_idempotent_on_date(self, ivrank_db):
        writer_mod, _recording, conn = ivrank_db
        rows = [
            {"date": "2026-08-20", "iv": 0.1275, "iv_rank": 13.9, "iv_pct": 15.1, "source": "ib"},
            {"date": "2026-08-21", "iv": 0.1220, "iv_rank": 10.6, "iv_pct": 12.0, "source": "ib"},
        ]
        writer_mod.upsert_ivrank_rows(rows, recorded_at="2026-08-22T22:10:00Z")
        writer_mod.upsert_ivrank_rows(
            [{**rows[1], "iv_rank": 10.559822}], recorded_at="2026-08-23T22:10:00Z"
        )
        stored = conn.execute(
            "SELECT date, iv, iv_rank, iv_pct, source, recorded_at "
            "FROM ivrank_history ORDER BY date"
        ).fetchall()
        assert len(stored) == 2
        assert stored[1][0] == "2026-08-21"
        assert stored[1][2] == pytest.approx(10.559822)
        assert stored[1][5] == "2026-08-23T22:10:00Z"
        assert stored[0][5] == "2026-08-22T22:10:00Z"

    def test_upsert_persists_null_rank(self, ivrank_db):
        writer_mod, _recording, conn = ivrank_db
        writer_mod.upsert_ivrank_rows(
            [{"date": "2021-08-23", "iv": 0.141, "iv_rank": None, "iv_pct": None, "source": "ib"}],
            recorded_at="2026-08-22T22:10:00Z",
        )
        assert conn.execute("SELECT iv_rank, iv_pct FROM ivrank_history").fetchone() == (None, None)

    def test_upsert_batches_at_400_rows_and_never_executemany(self, ivrank_db):
        writer_mod, recording, conn = ivrank_db
        rows = [
            {
                "date": (date(2020, 1, 1) + timedelta(days=i)).isoformat(),
                "iv": 0.10 + i * 0.0001,
                "iv_rank": 50.0,
                "iv_pct": 50.0,
                "source": "ib",
            }
            for i in range(900)
        ]
        writer_mod.upsert_ivrank_rows(rows, recorded_at="2026-08-22T22:10:00Z")
        # 900 rows / 400 per chunk = 3 statements, not 900.
        assert len(recording.statements) == 3
        assert [sql.count("(?, ?, ?, ?, ?, ?)") for sql, _ in recording.statements] == [400, 400, 100]
        assert all("ON CONFLICT(date) DO UPDATE" in sql for sql, _ in recording.statements)
        assert conn.execute("SELECT COUNT(*) FROM ivrank_history").fetchone()[0] == 900

    def test_empty_rows_write_nothing(self, ivrank_db):
        writer_mod, recording, _conn = ivrank_db
        writer_mod.upsert_ivrank_rows([], recorded_at="2026-08-22T22:10:00Z")
        assert recording.statements == []


# ── job orchestration ─────────────────────────────────────────────


class _FakeWriter:
    def __init__(self):
        self.replica_guards = 0
        self.ivrank_rows: list[tuple[list[dict[str, Any]], Optional[str]]] = []
        self.snapshots: list[tuple[str, str, dict[str, Any]]] = []
        self.health: list[tuple[str, str, Optional[dict[str, Any]]]] = []

    def ensure_no_replica_for_writers(self):
        self.replica_guards += 1

    def upsert_ivrank_rows(self, rows, recorded_at=None):
        self.ivrank_rows.append((list(rows), recorded_at))

    def upsert_scan_snapshot(self, service, scan_time, payload):
        self.snapshots.append((service, scan_time, payload))

    def record_service_health(self, service, status, finished_at=None, **kwargs):
        self.health.append((service, status, kwargs.get("error")))


class _StubIb:
    """ib fetch stand-in: (duration) -> [{date, iv}] or raises."""

    def __init__(self, rows: Optional[list[dict[str, Any]]] = None, exc: Optional[Exception] = None):
        self.rows = rows or []
        self.exc = exc
        self.calls: list[str] = []

    def __call__(self, duration: str) -> list[dict[str, Any]]:
        self.calls.append(duration)
        if self.exc is not None:
            raise self.exc
        return [dict(row) for row in self.rows]


class _StubUw:
    """uw fetch stand-in: () -> mapped rows [{date, iv, source:'uw'}] or raises."""

    def __init__(self, rows: Optional[list[dict[str, Any]]] = None, exc: Optional[Exception] = None):
        self.rows = rows or []
        self.exc = exc
        self.calls = 0

    def __call__(self) -> list[dict[str, Any]]:
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return [dict(row) for row in self.rows]


# radon-ivrank.timer fires 22:10 UTC = 18:10 ET, after the close whose
# session this run finalizes.
NOW_RUN = datetime(2026, 8, 21, 22, 10, tzinfo=timezone.utc)


@pytest.fixture
def job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """fetch_ivrank with its JSON cache relocated, writer faked, gate open."""
    mod = _mod()
    monkeypatch.setattr(mod, "IVRANK_JSON", tmp_path / "ivrank.json")
    fake = _FakeWriter()
    monkeypatch.setattr(mod, "writer", fake)
    monkeypatch.setattr(mod, "gateway_auth_state", lambda: "authenticated")
    monkeypatch.setattr(mod, "load_history", lambda: [])
    return mod, fake, tmp_path


class TestRunHappyPath:
    def test_ib_run_computes_writes_rows_snapshot_and_heartbeat(self, job):
        mod, fake, _tmp = job
        ib = _StubIb(_ib_rows())
        payload = mod.run(ib_fetch=ib, uw_fetch=_StubUw(), uw_check=lambda: None, now=NOW_RUN)

        assert ib.calls == [mod.INCREMENTAL_DURATION]
        assert payload["status"] == "ok"
        assert payload["source"] == "ib"
        assert payload["as_of"] == PIN_LAST_DATE
        assert payload["rank_window"] == 252
        assert payload["count"] == PIN_BAR_COUNT
        assert payload["rank_count"] == 0            # 251 bars < 252: all null
        assert payload["current"]["iv"] == pytest.approx(PIN_CURRENT_IV, abs=1e-9)
        assert payload["current"]["iv_rank"] is None
        assert payload["current"]["regime"] is None
        assert payload["scan_time"].endswith("Z")

        assert fake.replica_guards >= 1
        assert [snap[0] for snap in fake.snapshots] == ["ivrank"]
        assert fake.health == [("ivrank", "ok", None)]
        assert len(fake.ivrank_rows) == 1
        assert len(fake.ivrank_rows[0][0]) == PIN_BAR_COUNT
        assert fake.ivrank_rows[0][1] == payload["scan_time"]

    def test_rank_bearing_run_emits_current_block(self, job, monkeypatch):
        mod, _fake, _tmp = job
        history = _synthetic_rows(252, iv=0.10)
        monkeypatch.setattr(mod, "load_history", lambda: history)
        ib = _StubIb([{"date": "2020-09-09", "iv": 0.20}])
        payload = mod.run(ib_fetch=ib, uw_fetch=_StubUw(), uw_check=lambda: None, now=NOW_RUN)
        cur = payload["current"]
        assert cur["date"] == "2020-09-09"
        assert cur["iv_rank"] == pytest.approx(100.0, abs=1e-9)
        assert cur["regime"] == "EXTREME"
        assert cur["iv_1y_low"] == pytest.approx(0.10, abs=1e-12)
        assert cur["iv_1y_high"] == pytest.approx(0.20, abs=1e-12)
        assert payload["rank_count"] == 1

    def test_backfill_uses_the_backfill_duration(self, job):
        mod, _fake, _tmp = job
        ib = _StubIb(_ib_rows())
        mod.run(ib_fetch=ib, uw_fetch=_StubUw(), uw_check=lambda: None, now=NOW_RUN, backfill=True)
        assert ib.calls == [mod.BACKFILL_DURATION]

    def test_uw_check_result_rides_in_the_payload(self, job):
        mod, _fake, _tmp = job
        payload = mod.run(
            ib_fetch=_StubIb(_ib_rows()),
            uw_fetch=_StubUw(),
            uw_check=lambda: {"date": "2026-08-21", "iv_rank": 10.58},
            now=NOW_RUN,
        )
        assert payload["uw_check"] == {"date": "2026-08-21", "iv_rank": 10.58}

    def test_uw_check_failure_is_non_fatal_and_null(self, job):
        mod, fake, _tmp = job

        def _boom():
            raise RuntimeError("uw stats down")

        payload = mod.run(
            ib_fetch=_StubIb(_ib_rows()), uw_fetch=_StubUw(), uw_check=_boom, now=NOW_RUN
        )
        assert payload["uw_check"] is None
        assert fake.health == [("ivrank", "ok", None)]


class TestUnchangedSource:
    def test_unchanged_rows_skip_the_row_upsert_but_heartbeat(self, job, monkeypatch):
        mod, fake, _tmp = job
        history = [{**row, "source": "ib"} for row in _ib_rows()]
        monkeypatch.setattr(mod, "load_history", lambda: history)
        # IB restates the same tail: nothing changed.
        tail = [{"date": row["date"], "iv": row["iv"]} for row in history[-22:]]
        payload = mod.run(ib_fetch=_StubIb(tail), uw_fetch=_StubUw(), uw_check=lambda: None, now=NOW_RUN)
        assert payload["status"] == "ok"
        assert fake.ivrank_rows == []                       # no row writes
        assert [snap[0] for snap in fake.snapshots] == ["ivrank"]   # snapshot still lands
        assert fake.health == [("ivrank", "ok", None)]              # heartbeat still beats


class TestSourceLadder:
    def test_unauthenticated_gateway_skips_ib_and_uses_uw(self, job, monkeypatch):
        mod, fake, _tmp = job
        monkeypatch.setattr(mod, "gateway_auth_state", lambda: "2fa_pending")
        ib = _StubIb(_ib_rows())
        uw = _StubUw([{"date": "2026-08-21", "iv": 0.125, "source": "uw"}])
        payload = mod.run(ib_fetch=ib, uw_fetch=uw, uw_check=lambda: None, now=NOW_RUN)
        assert ib.calls == []                     # the socket was never attempted
        assert uw.calls == 1
        assert payload["source"] == "uw"
        assert payload["status"] == "degraded_uw"
        assert fake.health == [("ivrank", "ok", None)]

    def test_ib_error_falls_back_to_uw(self, job):
        mod, _fake, _tmp = job
        ib = _StubIb(exc=TimeoutError("gateway hang"))
        uw = _StubUw([{"date": "2026-08-21", "iv": 0.125, "source": "uw"}])
        payload = mod.run(ib_fetch=ib, uw_fetch=uw, uw_check=lambda: None, now=NOW_RUN)
        assert ib.calls == [mod.INCREMENTAL_DURATION]
        assert payload["source"] == "uw"
        assert payload["status"] == "degraded_uw"

    def test_unknown_health_state_still_attempts_ib(self, job, monkeypatch):
        mod, _fake, _tmp = job
        monkeypatch.setattr(mod, "gateway_auth_state", lambda: None)
        ib = _StubIb(_ib_rows())
        payload = mod.run(ib_fetch=ib, uw_fetch=_StubUw(), uw_check=lambda: None, now=NOW_RUN)
        assert ib.calls == [mod.INCREMENTAL_DURATION]
        assert payload["source"] == "ib"

    def test_backfill_without_ib_is_a_hard_error(self, job, monkeypatch):
        mod, _fake, _tmp = job
        monkeypatch.setattr(mod, "gateway_auth_state", lambda: "2fa_pending")
        with pytest.raises(Exception):
            mod.run(
                ib_fetch=_StubIb(), uw_fetch=_StubUw(), uw_check=lambda: None,
                now=NOW_RUN, backfill=True,
            )


class TestBothFeedsDown:
    def test_cached_payload_is_reserved_with_stale_source_and_error_heartbeat(
        self, job, monkeypatch
    ):
        mod, fake, tmp_path = job
        cached = {
            "scan_time": "2026-08-20T22:10:00Z",
            "status": "ok",
            "source": "ib",
            "as_of": "2026-08-20",
            "series": [{"date": "2026-08-20", "iv": 0.1275, "iv_rank": None, "iv_pct": None}],
        }
        (tmp_path / "ivrank.json").write_text(json.dumps(cached))
        payload = mod.run(
            ib_fetch=_StubIb(exc=TimeoutError("down")),
            uw_fetch=_StubUw(exc=RuntimeError("down")),
            uw_check=lambda: None,
            now=NOW_RUN,
        )
        assert payload["status"] == "stale_source"
        assert payload["as_of"] == "2026-08-20"
        assert payload["scan_time"] != cached["scan_time"]      # fresh stamp
        assert fake.ivrank_rows == []
        assert [snap[0] for snap in fake.snapshots] == ["ivrank"]
        # this writer being unable to reach EITHER feed pages
        assert len(fake.health) == 1
        assert fake.health[0][0] == "ivrank"
        assert fake.health[0][1] == "error"

    def test_no_cache_raises_never_caches_empty(self, job):
        mod, fake, _tmp = job
        with pytest.raises(Exception):
            mod.run(
                ib_fetch=_StubIb(exc=TimeoutError("down")),
                uw_fetch=_StubUw(exc=RuntimeError("down")),
                uw_check=lambda: None,
                now=NOW_RUN,
            )
        assert fake.snapshots == []
        assert fake.ivrank_rows == []


class TestJsonFallback:
    def test_payload_is_mirrored_to_disk_atomically(self, job):
        mod, _fake, tmp_path = job
        payload = mod.run(
            ib_fetch=_StubIb(_ib_rows()), uw_fetch=_StubUw(), uw_check=lambda: None, now=NOW_RUN
        )
        on_disk = json.loads((tmp_path / "ivrank.json").read_text())
        assert on_disk["scan_time"] == payload["scan_time"]
        assert on_disk["count"] == PIN_BAR_COUNT
        assert not (tmp_path / "ivrank.json.tmp").exists()
