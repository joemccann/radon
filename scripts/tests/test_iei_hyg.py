"""IEI/HYG ratio indicator — red tests written against docs/indicators/iei-hyg.md.

Fixture facts were derived by running the parser over the captured Yahoo
samples (scripts/tests/fixtures/iei_hyg_*_sample.json), never by hand.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from fetch_credit_spread import parse_yahoo_chart
from fetch_iei_hyg import (
    WINDOW_SESSIONS,
    align_series,
    build_output,
    classify_state,
    diff_new_rows,
    extremes_window,
    main,
    merge_series,
    pct_rank,
    persist_result,
    uw_regular_closes,
)

FIXTURES = Path(__file__).parent / "fixtures"
MIGRATION = Path(__file__).parents[1] / "db" / "migrations" / "0053_iei_hyg.sql"

IEI_LAST = 116.41000366210938
HYG_LAST = 79.61000061035156
DXY_LAST = 98.80000305175781
RATIO_LAST = 1.462253520532856
RATIO_MAX = 1.475760927676247
RATIO_PREV = 1.464932210008201


@pytest.fixture(scope="module")
def closes():
    return {
        sym: parse_yahoo_chart((FIXTURES / f"iei_hyg_{sym}_sample.json").read_text())
        for sym in ("iei", "hyg", "dxy")
    }


@pytest.fixture(scope="module")
def series(closes):
    return align_series(closes["iei"], closes["hyg"], closes["dxy"])


class TestAlign:
    def test_inner_joins_iei_and_hyg_and_left_joins_dxy(self, series):
        assert len(series) == 62
        assert series[0]["date"] == "2026-05-26"
        assert series[-1]["date"] == "2026-08-21"
        assert [r["date"] for r in series] == sorted(r["date"] for r in series)

    def test_last_row(self, series):
        last = series[-1]
        assert last["iei_close"] == IEI_LAST
        assert last["hyg_close"] == HYG_LAST
        assert last["dxy_close"] == DXY_LAST
        assert last["ratio"] == pytest.approx(RATIO_LAST)

    def test_dxy_gap_is_null_not_dropped(self):
        iei = {"2026-08-20": 116.0, "2026-08-21": IEI_LAST}
        hyg = {"2026-08-20": 79.5, "2026-08-21": HYG_LAST}
        dxy = {"2026-08-21": DXY_LAST}
        rows = align_series(iei, hyg, dxy)
        assert [r["date"] for r in rows] == ["2026-08-20", "2026-08-21"]
        assert rows[0]["dxy_close"] is None
        assert rows[1]["dxy_close"] == DXY_LAST

    def test_dates_missing_from_either_etf_are_dropped(self):
        rows = align_series({"2026-08-20": 1.0}, {"2026-08-21": 2.0}, {})
        assert rows == []


class TestExtremes:
    def test_window_is_the_trailing_252_sessions_including_latest(self):
        rows = [{"date": f"d{i:04d}", "ratio": float(i)} for i in range(300)]
        window = extremes_window(rows)
        assert len(window) == WINDOW_SESSIONS == 252
        assert window[-1] is rows[-1]
        assert window[0] is rows[300 - 252]

    def test_short_history_uses_everything(self, series):
        assert len(extremes_window(series)) == 62

    def test_fixture_latest_is_the_window_minimum(self, series):
        ratios = [r["ratio"] for r in extremes_window(series)]
        assert min(ratios) == pytest.approx(RATIO_LAST)
        assert max(ratios) == pytest.approx(RATIO_MAX)


class TestClassifyState:
    def test_new_low_is_equality_with_the_window_minimum(self):
        assert classify_state(1.0, 1.0, 2.0) == "new_low"
        assert classify_state(1.0000001, 1.0, 2.0) == "neutral"

    def test_new_high_is_equality_with_the_window_maximum(self):
        assert classify_state(2.0, 1.0, 2.0) == "new_high"
        assert classify_state(1.9999999, 1.0, 2.0) == "neutral"

    def test_degenerate_window_is_neutral(self):
        assert classify_state(1.5, 1.5, 1.5) == "neutral"


class TestPctRank:
    def test_bounds(self):
        assert pct_rank(1.0, 1.0, 2.0) == 0.0
        assert pct_rank(2.0, 1.0, 2.0) == 1.0
        assert pct_rank(1.25, 1.0, 2.0) == pytest.approx(0.25)

    def test_degenerate_window_is_zero(self):
        assert pct_rank(1.5, 1.5, 1.5) == 0.0


class TestBuildOutput:
    def test_fixture_current(self, series):
        payload = build_output(series, scan_time="2026-08-22T21:55:00Z", source="yahoo")
        assert payload["scan_time"] == "2026-08-22T21:55:00Z"
        assert payload["source"] == "yahoo"
        assert payload["count"] == 62
        current = payload["current"]
        assert current["date"] == "2026-08-21"
        assert current["iei_close"] == IEI_LAST
        assert current["hyg_close"] == HYG_LAST
        assert current["dxy_close"] == DXY_LAST
        assert current["ratio"] == pytest.approx(RATIO_LAST)
        assert current["ratio_52w_low"] == pytest.approx(RATIO_LAST)
        assert current["low_date"] == "2026-08-21"
        assert current["ratio_52w_high"] == pytest.approx(RATIO_MAX)
        assert current["high_date"] == "2026-06-26"
        assert current["ratio_pct_rank"] == 0.0
        assert current["window_sessions"] == 62
        assert current["state"] == "new_low"
        assert payload["series"][-1]["ratio"] == pytest.approx(RATIO_LAST)
        assert payload["series"][-2]["ratio"] == pytest.approx(RATIO_PREV)

    def test_empty_series(self):
        payload = build_output([])
        assert payload["count"] == 0
        assert payload["current"] is None
        assert payload["series"] == []
        assert payload["scan_time"].endswith("Z")

    def test_neutral_when_latest_is_inside_the_range(self):
        rows = [
            {"date": "2026-08-19", "iei_close": 100.0, "hyg_close": 50.0, "dxy_close": None, "ratio": 2.0},
            {"date": "2026-08-20", "iei_close": 100.0, "hyg_close": 40.0, "dxy_close": None, "ratio": 2.5},
            {"date": "2026-08-21", "iei_close": 100.0, "hyg_close": 45.0, "dxy_close": 98.0, "ratio": 100.0 / 45.0},
        ]
        current = build_output(rows)["current"]
        assert current["state"] == "neutral"
        assert 0.0 < current["ratio_pct_rank"] < 1.0


class TestUwRegularSession:
    def test_keeps_only_the_regular_session_row_per_date(self):
        rows = [
            {"date": "2026-08-21", "market_time": "pr", "close": 1.0},
            {"date": "2026-08-21", "market_time": "r", "close": 2.0},
            {"date": "2026-08-21", "market_time": "po", "close": 3.0},
            {"date": "2026-08-20", "market_time": "r", "close": 4.0},
        ]
        assert uw_regular_closes(rows) == {"2026-08-21": 2.0, "2026-08-20": 4.0}

    def test_datetime_stamps_are_keyed_by_date(self):
        rows = [{"date": "2026-08-21T20:00:00Z", "market_time": "r", "close": 2.0}]
        assert uw_regular_closes(rows) == {"2026-08-21": 2.0}


class TestMergeDiff:
    def test_fresh_wins_and_diff_includes_dxy(self):
        cached = [{"date": "2026-08-20", "iei_close": 116.0, "hyg_close": 79.5, "dxy_close": None, "ratio": 116.0 / 79.5}]
        fresh = [{"date": "2026-08-20", "iei_close": 116.0, "hyg_close": 79.5, "dxy_close": 98.0, "ratio": 116.0 / 79.5}]
        merged = merge_series(cached, fresh)
        assert merged[0]["dxy_close"] == 98.0
        assert diff_new_rows(cached, merged) == [merged[0]]
        assert diff_new_rows(merged, merged) == []


@pytest.fixture()
def persist_calls(monkeypatch, tmp_path):
    import fetch_iei_hyg as fih

    calls: list[tuple] = []
    monkeypatch.setattr(fih, "IEI_HYG_JSON", tmp_path / "iei_hyg.json")
    monkeypatch.setattr(fih.writer, "ensure_no_replica_for_writers", lambda: calls.append(("guard",)))
    monkeypatch.setattr(fih.writer, "upsert_iei_hyg_rows", lambda rows, recorded_at: calls.append(("rows", len(rows))))
    monkeypatch.setattr(fih.writer, "upsert_scan_snapshot", lambda service, scan_time, payload: calls.append(("snapshot", service)))
    monkeypatch.setattr(
        fih.writer,
        "record_service_health",
        lambda service, state, finished_at=None: calls.append(("health", service, state)),
    )
    return calls


ROW = {"date": "2026-08-21", "iei_close": IEI_LAST, "hyg_close": HYG_LAST, "dxy_close": DXY_LAST, "ratio": RATIO_LAST}


class TestPersistResult:
    def test_refuses_empty_series(self, persist_calls):
        persist_result(build_output([]), [])
        assert persist_calls == []

    def test_changed_rows_write_everything_in_order(self, persist_calls):
        import fetch_iei_hyg as fih

        persist_result(build_output([ROW]), [ROW])
        assert persist_calls == [("guard",), ("rows", 1), ("snapshot", "iei-hyg"), ("health", "iei-hyg", "ok")]
        assert json.loads(fih.IEI_HYG_JSON.read_text())["count"] == 1

    def test_unchanged_day_heartbeats_without_row_upserts(self, persist_calls):
        persist_result(build_output([ROW]), [])
        kinds = [c[0] for c in persist_calls]
        assert "rows" not in kinds
        assert ("snapshot", "iei-hyg") in persist_calls
        assert ("health", "iei-hyg", "ok") in persist_calls


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


class TestStorage:
    @pytest.fixture()
    def db(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT);")
        conn.executescript(MIGRATION.read_text())
        yield conn
        conn.close()

    @pytest.fixture()
    def recording_writer(self, db, monkeypatch):
        """The REAL db.writer with get_db() wired to the in-memory 0053 schema."""
        from db import writer

        recording = _RecordingConnection(db)
        monkeypatch.setattr(writer, "get_db", lambda: recording)
        return writer, recording

    def test_migration_registers_version_53(self, db):
        assert [r[0] for r in db.execute("SELECT version FROM schema_migrations")] == [53]

    def test_schema_columns(self, db):
        cols = [r[1] for r in db.execute("PRAGMA table_info(iei_hyg_history)")]
        assert cols == ["date", "iei_close", "hyg_close", "dxy_close", "recorded_at"]

    def test_upsert_is_idempotent_per_date(self, db, recording_writer):
        writer, recording = recording_writer
        stale = {"date": "2026-08-21", "iei_close": 116.0, "hyg_close": 79.0, "dxy_close": None, "ratio": 116.0 / 79.0}

        writer.upsert_iei_hyg_rows([stale], recorded_at="2026-08-21T21:55:00Z")
        writer.upsert_iei_hyg_rows([ROW], recorded_at="2026-08-22T21:55:00Z")

        rows = list(db.execute("SELECT date, iei_close, hyg_close, dxy_close, recorded_at FROM iei_hyg_history"))
        assert rows == [("2026-08-21", IEI_LAST, HYG_LAST, DXY_LAST, "2026-08-22T21:55:00Z")]
        assert recording.commits == 2
        assert all("ON CONFLICT(date) DO UPDATE" in sql for sql, _ in recording.statements)

    def test_upsert_chunks_many_rows_into_multi_row_inserts(self, db, recording_writer):
        writer, recording = recording_writer
        rows = [
            {"date": f"2025-{m:02d}-{d:02d}", "iei_close": 100.0 + d, "hyg_close": 70.0 + m, "dxy_close": None}
            for m in range(1, 13)
            for d in range(1, 29)
        ]

        writer.upsert_iei_hyg_rows(rows, recorded_at="2026-08-22T21:55:00Z")

        assert db.execute("SELECT COUNT(*) FROM iei_hyg_history").fetchone() == (len(rows),)
        assert len(recording.statements) < len(rows)
        assert db.execute(
            "SELECT iei_close, hyg_close FROM iei_hyg_history WHERE date = '2025-12-28'"
        ).fetchone() == (128.0, 82.0)


class TestCli:
    def test_json_flag_prints_payload_only_to_stdout(self, monkeypatch, capsys, closes):
        import fetch_iei_hyg as fih

        monkeypatch.setattr(fih, "load_cached_series", lambda: [])
        monkeypatch.setattr(
            fih,
            "fetch_closes",
            lambda *a, **k: ({"IEI": closes["iei"], "HYG": closes["hyg"], "DXY": closes["dxy"]}, "yahoo"),
        )
        monkeypatch.setattr(fih, "persist_result", lambda payload, rows: None)
        assert main(["--json"]) == 0
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["count"] == 62
        assert payload["current"]["state"] == "new_low"
