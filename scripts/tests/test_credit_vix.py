"""CREDIT/VIX indicator — red tests written against docs/indicators/credit-vix.md.

Fixture facts were derived by running parse_yahoo_chart over the captured
Yahoo samples (scripts/tests/fixtures/credit_vix_{shy,hyg,vix}_sample.json),
never by hand. VIX Cboe parse reuses vix_history_sample.csv.
"""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
import pytest

from fetch_credit_spread import parse_yahoo_chart
from fetch_credit_vix import (
    EQUITY_TICKERS,
    GAP_THRESHOLD,
    HYG_SYMBOL,
    NO_SOURCE,
    SERVICE,
    SHY_SYMBOL,
    STATE_ALIGNED,
    STATE_CREDIT_WIDE,
    STATE_VIX_WIDE,
    VIX_SYMBOL,
    WINDOW_SESSIONS,
    align_series,
    build_output,
    classify_state,
    diff_new_rows,
    extremes_window,
    fetch_equity_closes,
    fetch_vix_closes,
    main,
    merge_series,
    pct_rank,
    persist_result,
    session_metrics,
    widest_since,
    z_gap,
)
from fetch_vixts import parse_index_csv

FIXTURES = Path(__file__).parent / "fixtures"
MIGRATION = Path(__file__).parents[1] / "db" / "migrations" / "0093_credit_vix.sql"

SHY_LAST = 81.16000366210938
HYG_LAST = 77.36000061035156
VIX_LAST = 16.040000915527344
SPREAD_LAST = SHY_LAST - HYG_LAST  # 3.8000030517578125
SPREAD_PREV = 3.5699996948242188


@pytest.fixture(scope="module")
def closes():
    return {
        "shy": parse_yahoo_chart((FIXTURES / "credit_vix_shy_sample.json").read_text()),
        "hyg": parse_yahoo_chart((FIXTURES / "credit_vix_hyg_sample.json").read_text()),
        "vix": parse_yahoo_chart((FIXTURES / "credit_vix_vix_sample.json").read_text()),
    }


@pytest.fixture(scope="module")
def series(closes):
    return align_series(closes["shy"], closes["hyg"], closes["vix"])


def _naive_pct_rank(value, low, high):
    if high == low:
        return 0.0
    return (value - low) / (high - low)


def _naive_metrics(rows, window=WINDOW_SESSIONS):
    out = []
    for i, row in enumerate(rows):
        w = rows[max(0, i + 1 - window) : i + 1]
        spreads = [r["spread"] for r in w]
        vixes = [r["vix_close"] for r in w]
        s_lo, s_hi = min(spreads), max(spreads)
        v_lo, v_hi = min(vixes), max(vixes)
        rs = _naive_pct_rank(row["spread"], s_lo, s_hi)
        rv = _naive_pct_rank(row["vix_close"], v_lo, v_hi)
        out.append({**row, "rank_spread": rs, "rank_vix": rv, "gap": rs - rv})
    return out


class TestAlign:
    def test_inner_joins_all_three_legs(self, series):
        assert len(series) == 88
        assert series[0]["date"] == "2026-05-26"
        assert series[-1]["date"] == "2026-09-29"
        assert [r["date"] for r in series] == sorted(r["date"] for r in series)

    def test_last_row(self, series):
        last = series[-1]
        assert last["shy_close"] == SHY_LAST
        assert last["hyg_close"] == HYG_LAST
        assert last["vix_close"] == VIX_LAST
        assert last["spread"] == pytest.approx(SPREAD_LAST)

    def test_dates_missing_from_any_leg_are_dropped(self):
        rows = align_series(
            {"2026-09-28": 80.0, "2026-09-29": SHY_LAST},
            {"2026-09-29": HYG_LAST},
            {"2026-09-28": 16.0, "2026-09-29": VIX_LAST},
        )
        assert [r["date"] for r in rows] == ["2026-09-29"]

    def test_spread_is_shy_minus_hyg(self):
        rows = align_series({"2026-09-29": 81.0}, {"2026-09-29": 77.0}, {"2026-09-29": 16.0})
        assert rows[0]["spread"] == pytest.approx(4.0)


class TestExtremes:
    def test_window_is_the_trailing_252_sessions_including_latest(self):
        rows = [{"date": f"d{i:04d}", "spread": float(i)} for i in range(300)]
        window = extremes_window(rows)
        assert len(window) == WINDOW_SESSIONS == 252
        assert window[-1] is rows[-1]
        assert window[0] is rows[300 - 252]

    def test_short_history_uses_everything(self, series):
        assert len(extremes_window(series)) == 88


class TestPctRank:
    def test_bounds(self):
        assert pct_rank(1.0, 1.0, 2.0) == 0.0
        assert pct_rank(2.0, 1.0, 2.0) == 1.0
        assert pct_rank(1.25, 1.0, 2.0) == pytest.approx(0.25)

    def test_degenerate_window_is_zero(self):
        """A1: high == low → 0.0 so gap stays defined (brief, not iei-hyg R-162)."""
        assert pct_rank(1.5, 1.5, 1.5) == 0.0


class TestGapState:
    def test_credit_wide_at_exactly_threshold(self):
        assert classify_state(GAP_THRESHOLD) == STATE_CREDIT_WIDE
        assert classify_state(GAP_THRESHOLD + 1e-12) == STATE_CREDIT_WIDE

    def test_vix_wide_at_exactly_negative_threshold(self):
        assert classify_state(-GAP_THRESHOLD) == STATE_VIX_WIDE
        assert classify_state(-GAP_THRESHOLD - 1e-12) == STATE_VIX_WIDE

    def test_aligned_inside_the_band(self):
        assert classify_state(0.499999) == STATE_ALIGNED
        assert classify_state(-0.499999) == STATE_ALIGNED
        assert classify_state(0.0) == STATE_ALIGNED


class TestWidestSince:
    def test_most_recent_prior_session_at_or_above_latest_gap(self):
        series = [
            {"date": "2026-09-25", "shy_close": 80.0, "hyg_close": 77.0, "vix_close": 16.0, "spread": 3.0},
            {"date": "2026-09-26", "shy_close": 81.0, "hyg_close": 77.0, "vix_close": 16.0, "spread": 4.0},
            {"date": "2026-09-27", "shy_close": 80.5, "hyg_close": 77.0, "vix_close": 16.0, "spread": 3.5},
            {"date": "2026-09-28", "shy_close": 80.2, "hyg_close": 77.0, "vix_close": 20.0, "spread": 3.2},
            {"date": "2026-09-29", "shy_close": 81.0, "hyg_close": 77.0, "vix_close": 16.0, "spread": 4.0},
        ]
        metrics = session_metrics(series)
        naive = _naive_metrics(series)
        assert metrics[-1]["gap"] == pytest.approx(naive[-1]["gap"])
        assert widest_since(metrics) == "2026-09-26"

    def test_null_when_latest_is_the_widest(self):
        # Constant early prints keep prior gaps at 0.0 (high == low). A unique
        # high spread + low VIX on the last session is the first gap of 1.0.
        series = [
            {"date": f"2026-09-{d:02d}", "shy_close": 80.0, "hyg_close": 77.5,
             "vix_close": 20.0, "spread": 2.5}
            for d in range(20, 29)
        ]
        series.append({
            "date": "2026-09-29", "shy_close": 81.5, "hyg_close": 77.5,
            "vix_close": 14.0, "spread": 4.0,
        })
        metrics = session_metrics(series)
        assert metrics[-1]["gap"] == pytest.approx(1.0)
        assert widest_since(metrics) is None


class TestBuildOutput:
    def test_fixture_current_matches_naive(self, series):
        payload = build_output(series, scan_time="2026-09-30T22:25:00Z", source="yahoo")
        naive = _naive_metrics(series)
        last = naive[-1]
        current = payload["current"]
        assert payload["scan_time"] == "2026-09-30T22:25:00Z"
        assert payload["source"] == "yahoo"
        assert payload["count"] == 88
        assert current["date"] == "2026-09-29"
        assert current["spread"] == pytest.approx(SPREAD_LAST)
        assert current["vix_close"] == VIX_LAST
        assert current["rank_spread"] == pytest.approx(last["rank_spread"])
        assert current["rank_vix"] == pytest.approx(last["rank_vix"])
        assert current["gap"] == pytest.approx(last["gap"])
        assert current["state"] == classify_state(last["gap"])
        assert current["window_sessions"] == 88
        assert payload["series"][-1]["spread"] == pytest.approx(SPREAD_LAST)
        assert payload["series"][-2]["spread"] == pytest.approx(SPREAD_PREV)

    def test_empty_series(self):
        payload = build_output([])
        assert payload["count"] == 0
        assert payload["current"] is None
        assert payload["series"] == []
        assert payload["scan_time"].endswith("Z")

    def test_short_window_still_publishes_gap(self):
        rows = [
            {"date": "2026-09-27", "shy_close": 80.0, "hyg_close": 78.0, "vix_close": 20.0, "spread": 2.0},
            {"date": "2026-09-28", "shy_close": 80.0, "hyg_close": 77.5, "vix_close": 18.0, "spread": 2.5},
            {"date": "2026-09-29", "shy_close": 81.0, "hyg_close": 77.0, "vix_close": 16.0, "spread": 4.0},
        ]
        current = build_output(rows)["current"]
        naive = _naive_metrics(rows)[-1]
        assert current["window_sessions"] == 3
        assert current["gap"] == pytest.approx(naive["gap"])
        assert current["state"] == classify_state(naive["gap"])


class TestZGap:
    def test_population_std_and_not_in_payload(self, series):
        window = extremes_window(series)
        spreads = [r["spread"] for r in window]
        vixes = [r["vix_close"] for r in window]
        last = series[-1]
        zg = z_gap(last["spread"], last["vix_close"], spreads, vixes)
        mean_s = sum(spreads) / len(spreads)
        mean_v = sum(vixes) / len(vixes)
        var_s = sum((x - mean_s) ** 2 for x in spreads) / len(spreads)
        var_v = sum((x - mean_v) ** 2 for x in vixes) / len(vixes)
        expected = (last["spread"] - mean_s) / math.sqrt(var_s) - (last["vix_close"] - mean_v) / math.sqrt(var_v)
        assert zg == pytest.approx(expected)
        payload = build_output(series)
        assert "z_gap" not in payload["current"]


class TestMergeDiff:
    def test_fresh_wins_and_diff_includes_vix(self):
        cached = [{"date": "2026-09-28", "shy_close": 80.0, "hyg_close": 77.0, "vix_close": 16.0, "spread": 3.0}]
        fresh = [{"date": "2026-09-28", "shy_close": 80.0, "hyg_close": 77.0, "vix_close": 16.1, "spread": 3.0}]
        merged = merge_series(cached, fresh)
        assert merged[0]["vix_close"] == 16.1
        assert diff_new_rows(cached, merged) == [merged[0]]
        assert diff_new_rows(merged, merged) == []


@pytest.fixture()
def persist_calls(monkeypatch, tmp_path):
    import fetch_credit_vix as fcv

    calls: list[tuple] = []
    monkeypatch.setattr(fcv, "CREDIT_VIX_JSON", tmp_path / "credit_vix.json")
    monkeypatch.setattr(fcv.writer, "ensure_no_replica_for_writers", lambda: calls.append(("guard",)))
    monkeypatch.setattr(fcv.writer, "upsert_credit_vix_rows", lambda rows, recorded_at: calls.append(("rows", len(rows))))
    monkeypatch.setattr(fcv.writer, "upsert_scan_snapshot", lambda service, scan_time, payload: calls.append(("snapshot", service)))
    monkeypatch.setattr(
        fcv.writer,
        "record_service_health",
        lambda service, state, finished_at=None, error=None: calls.append(("health", service, state)),
    )
    return calls


ROW = {
    "date": "2026-09-29",
    "shy_close": SHY_LAST,
    "hyg_close": HYG_LAST,
    "vix_close": VIX_LAST,
    "spread": SPREAD_LAST,
}


class TestPersistResult:
    def test_empty_series_heartbeats_error_instead_of_going_silent(self, persist_calls):
        persist_result(build_output([]), [])
        assert "rows" not in [c[0] for c in persist_calls]
        assert ("snapshot", SERVICE) not in persist_calls
        assert ("health", SERVICE, "error") in persist_calls

    def test_changed_rows_write_everything_in_order(self, persist_calls):
        import fetch_credit_vix as fcv

        persist_result(build_output([ROW]), [ROW])
        assert persist_calls == [
            ("guard",),
            ("rows", 1),
            ("snapshot", SERVICE),
            ("health", SERVICE, "ok"),
        ]
        assert json.loads(fcv.CREDIT_VIX_JSON.read_text())["count"] == 1

    def test_unchanged_day_heartbeats_without_row_upserts(self, persist_calls):
        persist_result(build_output([ROW]), [])
        kinds = [c[0] for c in persist_calls]
        assert "rows" not in kinds
        assert ("snapshot", SERVICE) in persist_calls
        assert ("health", SERVICE, "ok") in persist_calls


CACHED_SCAN_TIME = "2026-09-28T22:25:00Z"


class TestAllSourcesDown:
    def test_cached_series_is_reserved_as_stale_source_with_error_heartbeat(self, persist_calls, monkeypatch):
        import fetch_credit_vix as fcv

        fcv.CREDIT_VIX_JSON.write_text(json.dumps(build_output([ROW], scan_time=CACHED_SCAN_TIME, source="yahoo")))
        monkeypatch.setattr(fcv, "fetch_equity_closes", lambda *a, **k: ({}, NO_SOURCE, {}))
        monkeypatch.setattr(fcv, "fetch_vix_closes", lambda **k: ({}, NO_SOURCE))

        payload = fcv.run()

        assert ("health", SERVICE, "error") in persist_calls
        assert payload["status"] == "stale_source"
        assert payload["current"]["date"] == ROW["date"]
        assert "rows" not in [c[0] for c in persist_calls]
        assert ("snapshot", SERVICE) in persist_calls

    def test_no_cache_heartbeats_error_before_it_raises(self, persist_calls, monkeypatch):
        import fetch_credit_vix as fcv

        monkeypatch.setattr(fcv, "fetch_equity_closes", lambda *a, **k: ({}, NO_SOURCE, {}))
        monkeypatch.setattr(fcv, "fetch_vix_closes", lambda **k: ({}, NO_SOURCE))

        with pytest.raises(RuntimeError):
            fcv.run()
        assert ("health", SERVICE, "error") in persist_calls

    def test_no_db_skips_persist_on_stale_cache(self, persist_calls, monkeypatch, tmp_path):
        import fetch_credit_vix as fcv

        fcv.CREDIT_VIX_JSON.write_text(json.dumps(build_output([ROW], scan_time=CACHED_SCAN_TIME, source="yahoo")))
        monkeypatch.setattr(fcv, "fetch_equity_closes", lambda *a, **k: ({}, NO_SOURCE, {}))
        monkeypatch.setattr(fcv, "fetch_vix_closes", lambda **k: ({}, NO_SOURCE))

        payload = fcv.run(no_db=True)
        assert payload["status"] == "stale_source"
        assert persist_calls == []


class TestVixNeverThroughEquityCascade:
    def test_fetch_equity_closes_refuses_vix(self):
        with pytest.raises(ValueError, match="VIX"):
            fetch_equity_closes([VIX_SYMBOL], fetch_ib=lambda t: {}, fetch_uw=lambda t: {}, fetch_yahoo=lambda t: {})

    def test_fetch_vix_closes_does_not_call_iei_hyg(self, monkeypatch):
        import fetch_credit_vix as fcv

        def _explode(*_a, **_k):
            raise AssertionError("equity cascade must not see VIX")

        monkeypatch.setattr(fcv, "fetch_iei_hyg_closes", _explode)
        closes, source = fetch_vix_closes(
            fetch_ib=lambda: {},
            fetch_cboe=lambda: {"2026-09-29": 16.04},
            fetch_yahoo=lambda: {},
        )
        assert closes == {"2026-09-29": 16.04}
        assert source == "cboe"

    def test_vix_ladder_is_ib_then_cboe_then_yahoo(self):
        cboe_calls: list = []
        yahoo_calls: list = []
        closes, source = fetch_vix_closes(
            fetch_ib=lambda: {"2026-09-29": 16.0},
            fetch_cboe=lambda: cboe_calls.append(True) or {},
            fetch_yahoo=lambda: yahoo_calls.append(True) or {},
        )
        assert source == "ib"
        assert cboe_calls == []
        assert yahoo_calls == []
        assert closes == {"2026-09-29": 16.0}

    def test_cboe_fills_when_ib_is_empty(self):
        yahoo_calls: list = []
        closes, source = fetch_vix_closes(
            fetch_ib=lambda: {},
            fetch_cboe=lambda: {"2026-09-29": 16.04},
            fetch_yahoo=lambda: yahoo_calls.append(True) or {},
        )
        assert source == "cboe"
        assert yahoo_calls == []


class TestEquityCascadeImportsIeiHyg:
    def test_per_ticker_sources_map(self):
        closes, source, sources = fetch_equity_closes(
            fetch_ib=lambda tickers: {SHY_SYMBOL: {"2026-09-29": SHY_LAST}},
            fetch_uw=lambda tickers: {},
            fetch_rh=lambda tickers: {},
            fetch_yahoo=lambda tickers: {HYG_SYMBOL: {"2026-09-29": HYG_LAST}},
        )
        assert source == "ib+yahoo"
        assert sources == {SHY_SYMBOL: "ib", HYG_SYMBOL: "yahoo"}
        assert sorted(closes) == sorted(EQUITY_TICKERS)

    def test_ib_covering_both_never_calls_yahoo(self):
        yahoo_calls: list = []
        _, source, _ = fetch_equity_closes(
            fetch_ib=lambda tickers: {t: {"2026-09-29": 1.0} for t in tickers},
            fetch_uw=lambda tickers: {},
            fetch_yahoo=lambda tickers: yahoo_calls.append(list(tickers)) or {},
        )
        assert source == "ib"
        assert yahoo_calls == []


class TestCboeFixtureParse:
    def test_existing_vix_history_sample_parses_close(self):
        text = (FIXTURES / "vix_history_sample.csv").read_text()
        rows = parse_index_csv(text, "CLOSE")
        by_date = {r["date"]: r["value"] for r in rows}
        assert by_date["2026-08-14"] == pytest.approx(14.25)
        assert "1990-01-02" in by_date


class _RecordingConnection:
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
        from db import writer

        recording = _RecordingConnection(db)
        monkeypatch.setattr(writer, "get_db", lambda: recording)
        return writer, recording

    def test_migration_registers_version_93(self, db):
        assert [r[0] for r in db.execute("SELECT version FROM schema_migrations")] == [93]

    def test_schema_columns(self, db):
        cols = [r[1] for r in db.execute("PRAGMA table_info(credit_vix_history)")]
        assert cols == ["date", "shy_close", "hyg_close", "vix_close", "recorded_at"]

    def test_upsert_is_idempotent_per_date(self, db, recording_writer):
        writer, recording = recording_writer
        stale = {"date": "2026-09-29", "shy_close": 80.0, "hyg_close": 77.0, "vix_close": 15.0}

        writer.upsert_credit_vix_rows([stale], recorded_at="2026-09-29T22:25:00Z")
        writer.upsert_credit_vix_rows([ROW], recorded_at="2026-09-30T22:25:00Z")

        rows = list(db.execute("SELECT date, shy_close, hyg_close, vix_close, recorded_at FROM credit_vix_history"))
        assert rows == [("2026-09-29", SHY_LAST, HYG_LAST, VIX_LAST, "2026-09-30T22:25:00Z")]
        assert recording.commits == 2
        assert all("ON CONFLICT(date) DO UPDATE" in sql for sql, _ in recording.statements)

    def test_upsert_chunks_many_rows_into_multi_row_inserts(self, db, recording_writer):
        writer, recording = recording_writer
        rows = [
            {"date": f"2025-{m:02d}-{d:02d}", "shy_close": 80.0 + d, "hyg_close": 70.0 + m, "vix_close": 16.0}
            for m in range(1, 13)
            for d in range(1, 29)
        ]

        writer.upsert_credit_vix_rows(rows, recorded_at="2026-09-30T22:25:00Z")

        assert db.execute("SELECT COUNT(*) FROM credit_vix_history").fetchone() == (len(rows),)
        assert len(recording.statements) < len(rows)

    def test_migration_rerun_is_idempotent(self, db):
        db.executescript(MIGRATION.read_text())
        assert [r[0] for r in db.execute("SELECT version FROM schema_migrations")] == [93]


class TestCli:
    def test_json_flag_prints_payload_only_to_stdout(self, monkeypatch, capsys, closes):
        import fetch_credit_vix as fcv

        monkeypatch.setattr(fcv, "load_cached_series", lambda **k: [])
        monkeypatch.setattr(
            fcv,
            "fetch_equity_closes",
            lambda *a, **k: (
                {"SHY": closes["shy"], "HYG": closes["hyg"]},
                "yahoo",
                {"SHY": "yahoo", "HYG": "yahoo"},
            ),
        )
        monkeypatch.setattr(
            fcv,
            "fetch_vix_closes",
            lambda **k: (closes["vix"], "cboe"),
        )
        monkeypatch.setattr(fcv, "persist_result", lambda payload, rows, health_error=None, **_kw: None)
        assert main(["--json"]) == 0
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["count"] == 88
        assert payload["current"]["date"] == "2026-09-29"

    def test_no_db_flag_skips_persist(self, persist_calls, monkeypatch, closes):
        import fetch_credit_vix as fcv

        monkeypatch.setattr(fcv, "load_cached_series", lambda **k: [])
        monkeypatch.setattr(
            fcv,
            "fetch_equity_closes",
            lambda *a, **k: (
                {"SHY": closes["shy"], "HYG": closes["hyg"]},
                "yahoo",
                {"SHY": "yahoo", "HYG": "yahoo"},
            ),
        )
        monkeypatch.setattr(fcv, "fetch_vix_closes", lambda **k: (closes["vix"], "yahoo"))
        assert main(["--no-db", "--json"]) == 0
        assert persist_calls == []


class TestTursoRehydrate:
    def test_load_cached_series_prefers_turso(self, monkeypatch):
        import fetch_credit_vix as fcv

        monkeypatch.setattr(fcv, "_turso_series", lambda: [ROW])
        monkeypatch.setattr(fcv, "_json_series", lambda: [{"date": "2020-01-01"}])
        assert fcv.load_cached_series()[0]["date"] == "2026-09-29"

    def test_load_cached_series_no_db_skips_turso(self, monkeypatch):
        import fetch_credit_vix as fcv

        def _boom():
            raise AssertionError("Turso must not be read under --no-db")

        monkeypatch.setattr(fcv, "_turso_series", _boom)
        monkeypatch.setattr(fcv, "_json_series", lambda: [ROW])
        assert fcv.load_cached_series(no_db=True)[0]["date"] == "2026-09-29"

    def test_window_relative_scan_time(self):
        payload = build_output([ROW])
        assert payload["scan_time"].endswith("Z")
        datetime_ok = payload["scan_time"][:4].isdigit()
        assert datetime_ok
