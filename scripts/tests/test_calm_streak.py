"""CALM STREAK: consecutive SPX sessions without a >1% intraday band.
Red tests against docs/indicators/calm-streak.md.

Fixture: fixtures/calm_streak_cboe_spx_sample.json, a trimmed real capture of the
Cboe _SPX.json historical file (2026-09-15). Two contiguous slices: 1989-11-01..
1990-03-30 and the last 120 sessions through 2026-09-14. Streak expectations are
recomputed from the fixture with an independent naive loop, and the two capture
facts (27 on 2026-09-11, 28 on 2026-09-14) were verified against the full file.
"""

from __future__ import annotations

import inspect
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from fetch_calm_streak import (
    SERIES_START,
    SERVICE,
    THRESHOLD_PCT,
    WINDOW_END,
    WINDOW_START,
    build_output,
    completed_sessions,
    compute_stats,
    compute_streaks,
    parse_cboe_history,
    persist_result,
    rows_to_upsert,
    weekly_peaks,
)

FIXTURES = Path(__file__).parent / "fixtures"
MIGRATION = Path(__file__).parents[1] / "db" / "migrations" / "0076_calm_streak.sql"
RAW = json.loads((FIXTURES / "calm_streak_cboe_spx_sample.json").read_text())
PARSED = parse_cboe_history(RAW)
LATE = [r for r in PARSED if r["date"] >= "2026-01-01"]
EARLY = [r for r in PARSED if r["date"] < "2000-01-01"]
SCAN_TIME = datetime.now(timezone.utc).isoformat()
_TODAY = date.today()


def _naive_streaks(rows):
    out = []
    streak = 0
    for prev, cur in zip(rows, rows[1:]):
        band = 100.0 * (cur["high"] - cur["low"]) / prev["close"]
        streak = 0 if band > 1.0 else streak + 1
        out.append((cur["date"], streak))
    return out


def _row(d, streak, close=100.0, band=0.5):
    return {"date": d, "open": close, "high": close, "low": close, "close": close,
            "band_pct": band, "streak": streak}


class TestConstants:
    def test_definition_constants(self):
        assert SERVICE == "calm-streak"
        assert THRESHOLD_PCT == 1.0
        assert SERIES_START == "1985-01-01"
        assert (WINDOW_START, WINDOW_END) == ("1996-01-01", "2016-12-31")


class TestParse:
    def test_rows_are_floats_ascending_and_complete(self):
        assert len(PARSED) == len(RAW["data"])
        assert [r["date"] for r in PARSED] == sorted(r["date"] for r in PARSED)
        last = PARSED[-1]
        assert last["date"] == "2026-09-14"
        assert last["high"] == pytest.approx(7647.99)
        assert last["low"] == pytest.approx(7592.28)
        assert last["close"] == pytest.approx(7619.98)

    def test_zero_open_becomes_none_and_bad_rows_drop(self):
        payload = {"data": [
            {"date": "1976-01-02", "open": "0.000000", "high": "91.0", "low": "90.0", "close": "90.5"},
            {"date": "1976-01-05", "open": "90.5", "high": "0.0", "low": "90.0", "close": "90.9"},
            {"date": "1976-01-01", "open": "89.0", "high": "90.0", "low": "89.0", "close": "89.5"},
        ]}
        rows = parse_cboe_history(payload)
        assert [r["date"] for r in rows] == ["1976-01-01", "1976-01-02"]
        assert rows[1]["open"] is None


class TestCompletedSessions:
    def test_drops_rows_after_the_last_completed_session(self):
        rows = completed_sessions(LATE, "2026-09-11")
        assert rows[-1]["date"] == "2026-09-11"
        assert all(r["date"] <= "2026-09-11" for r in rows)


class TestComputeStreaks:
    def test_matches_a_naive_recompute_on_both_slices(self):
        for slice_ in (EARLY, LATE):
            got = [(r["date"], r["streak"]) for r in compute_streaks(slice_)]
            assert got == _naive_streaks(slice_)

    def test_capture_facts(self):
        by_date = {r["date"]: r["streak"] for r in compute_streaks(LATE)}
        assert by_date["2026-09-11"] == 27
        assert by_date["2026-09-14"] == 28

    def test_band_pct_uses_prior_close(self):
        rows = compute_streaks(LATE)
        last, prev = LATE[-1], LATE[-2]
        expected = 100.0 * (last["high"] - last["low"]) / prev["close"]
        assert rows[-1]["band_pct"] == pytest.approx(expected, abs=1e-4)

    def test_exactly_one_percent_does_not_break_the_streak(self):
        rows = [
            {"date": "2026-01-02", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0},
            {"date": "2026-01-05", "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0},
            {"date": "2026-01-06", "open": 100.0, "high": 101.0, "low": 100.0, "close": 100.0},
            {"date": "2026-01-07", "open": 100.0, "high": 101.01, "low": 100.0, "close": 100.0},
        ]
        assert [r["streak"] for r in compute_streaks(rows)] == [1, 2, 0]


class TestWeeklyPeaks:
    def test_one_row_per_iso_week_with_max_streak_and_last_close(self):
        rows = [
            _row("2026-09-08", 5, close=1.0), _row("2026-09-09", 7, close=2.0),
            _row("2026-09-10", 0, close=3.0), _row("2026-09-14", 1, close=4.0),
        ]
        assert weekly_peaks(rows) == [
            {"date": "2026-09-10", "streak": 7, "close": 3.0},
            {"date": "2026-09-14", "streak": 1, "close": 4.0},
        ]


class TestStats:
    def test_max_window_and_percentile(self):
        rows = [
            _row("1990-01-02", 30), _row("2000-01-03", 20), _row("2014-06-23", 26),
            _row("2017-03-20", 64), _row("2026-09-14", 28),
        ]
        stats = compute_stats(rows)
        assert stats["max"] == {"streak": 64, "date": "2017-03-20"}
        assert stats["window"] == {"start": WINDOW_START, "end": WINDOW_END,
                                   "streak": 26, "date": "2014-06-23"}
        # sessions with a strictly lower streak than 28: 20, 26 -> 2 of 5.
        assert stats["percentile"] == pytest.approx(40.0)


class TestBuildOutput:
    def test_payload_contract(self):
        rows = compute_streaks(LATE)
        payload = build_output(rows, scan_time=SCAN_TIME,
                               source_last_modified="Tue, 15 Sep 2026 13:02:41 GMT")
        assert {"schema_version", "scan_time", "data_date", "source_last_modified",
                "source", "threshold_pct", "current", "stats", "series", "missing"} <= set(payload)
        assert payload["missing"] is False
        assert payload["data_date"] == "2026-09-14"
        assert payload["source"]["name"] == "cboe"
        assert payload["current"]["streak"] == 28
        assert set(payload["current"]) == {"date", "streak", "band_pct", "close"}
        assert set(payload["series"][-1]) == {"date", "streak", "close"}
        assert payload["series"] == weekly_peaks(rows)

    def test_series_starts_at_series_start(self):
        rows = [_row("1984-12-31", 3), _row("1985-01-02", 4), _row("1985-01-03", 5)]
        payload = build_output(rows, scan_time=SCAN_TIME, source_last_modified=None)
        assert payload["series"][0]["date"] >= SERIES_START

    def test_too_little_history_is_missing(self):
        payload = build_output([_row("2026-09-14", 1)], scan_time=SCAN_TIME,
                               source_last_modified=None)
        assert payload["missing"] is True
        assert payload["reason"] == "insufficient_history"


class TestRowsToUpsert:
    def test_backfills_from_series_start_when_table_empty(self):
        rows = [_row("1984-12-31", 1), _row("1985-01-02", 2), _row("2026-09-14", 3)]
        assert [r["date"] for r in rows_to_upsert(rows, None)] == ["1985-01-02", "2026-09-14"]

    def test_only_new_sessions_after_latest_stored(self):
        rows = [_row("2026-09-10", 1), _row("2026-09-11", 2), _row("2026-09-14", 3)]
        assert [r["date"] for r in rows_to_upsert(rows, "2026-09-11")] == ["2026-09-14"]


@pytest.fixture()
def writes(monkeypatch, tmp_path):
    import fetch_calm_streak as mod

    calls: list[tuple] = []
    monkeypatch.setattr(mod, "CALM_STREAK_JSON", tmp_path / "calm_streak.json")
    monkeypatch.setattr(mod.writer, "ensure_no_replica_for_writers", lambda: calls.append(("guard",)))
    monkeypatch.setattr(mod.writer, "upsert_calm_streak_rows",
                        lambda rows, recorded_at: calls.append(("rows", len(rows))))
    monkeypatch.setattr(mod.writer, "upsert_scan_snapshot",
                        lambda service, scan_time, payload: calls.append(("snapshot", service, scan_time)))
    monkeypatch.setattr(mod.writer, "record_service_health",
                        lambda service, state, finished_at=None, **_: calls.append(("health", service, state)))
    return calls


class TestPersistResult:
    def test_write_order_with_heartbeat_and_json(self, writes):
        import fetch_calm_streak as mod

        rows = compute_streaks(LATE)
        payload = build_output(rows, scan_time=SCAN_TIME, source_last_modified=None)
        persist_result(payload, rows)
        assert writes == [("guard",), ("rows", len(rows)),
                          ("snapshot", "calm-streak", SCAN_TIME), ("health", "calm-streak", "ok")]
        assert json.loads(mod.CALM_STREAK_JSON.read_text())["data_date"] == "2026-09-14"

    def test_empty_rows_skip_the_row_upsert(self, writes):
        payload = build_output(compute_streaks(LATE), scan_time=SCAN_TIME, source_last_modified=None)
        persist_result(payload, [])
        assert ("rows", 0) not in writes
        assert writes[-1] == ("health", "calm-streak", "ok")


class TestRun:
    def test_changed_source_writes_only_new_rows(self, writes, monkeypatch):
        import fetch_calm_streak as mod

        monkeypatch.setattr(mod, "_read_json_cache", lambda: None)
        monkeypatch.setattr(mod, "_fetch_source",
                            lambda if_modified_since: (RAW, "Tue, 15 Sep 2026 13:02:41 GMT"))
        monkeypatch.setattr(mod, "_latest_stored_date", lambda: "2026-09-10")
        now = datetime(2026, 9, 15, 14, 31, tzinfo=timezone.utc)
        payload = mod.run(now=now)
        assert payload["missing"] is False
        assert payload["data_date"] == "2026-09-14"
        assert payload["source_last_modified"] == "Tue, 15 Sep 2026 13:02:41 GMT"
        assert ("rows", 2) in writes  # 2026-09-11 and 2026-09-14

    def test_intraday_run_excludes_the_in_progress_session(self, writes, monkeypatch):
        import fetch_calm_streak as mod

        monkeypatch.setattr(mod, "_read_json_cache", lambda: None)
        monkeypatch.setattr(mod, "_fetch_source", lambda if_modified_since: (RAW, None))
        monkeypatch.setattr(mod, "_latest_stored_date", lambda: None)
        # 2026-09-14 15:00 ET: the 09-14 session is not complete yet.
        now = datetime(2026, 9, 14, 19, 0, tzinfo=timezone.utc)
        assert mod.run(now=now)["data_date"] == "2026-09-11"

    def test_304_heartbeats_the_cached_payload_without_rows(self, writes, monkeypatch):
        import fetch_calm_streak as mod

        cached = build_output(compute_streaks(LATE), scan_time="2026-09-14T02:40:00+00:00",
                              source_last_modified="Mon, 14 Sep 2026 13:00:00 GMT")
        seen = {}
        monkeypatch.setattr(mod, "_read_json_cache", lambda: cached)

        def fetch(if_modified_since):
            seen["ims"] = if_modified_since
            return None, if_modified_since

        monkeypatch.setattr(mod, "_fetch_source", fetch)
        monkeypatch.setattr(mod, "_latest_stored_date",
                            lambda: pytest.fail("304 path must not touch history rows"))
        payload = mod.run(now=datetime.now(timezone.utc))
        assert seen["ims"] == "Mon, 14 Sep 2026 13:00:00 GMT"
        assert payload["scan_time"] != cached["scan_time"]
        assert [c[0] for c in writes] == ["guard", "snapshot", "health"]


class TestHeartbeatTimeout:
    def test_main_exits_zero_when_the_ok_heartbeat_times_out(
        self, writes, monkeypatch, capsys,
    ):
        """Page 4d8870f0ead060af734f223e7e2c1f03 (2026-10-09 14:30Z): the
        Cboe cycle finished, then record_service_health('ok') raised
        HranaHttpError 'TimeoutError: The read operation timed out'.
        The bare call aborted before the JSON mirror and main() exited 1
        (Result=exit-code, NRestarts=0). Sibling vol-cone logged the same
        stall as non-fatal. The heartbeat is telemetry. A committed cycle
        must exit 0 and still write the JSON fallback."""
        from db.hrana_http import HranaHttpError
        import fetch_calm_streak as mod

        monkeypatch.setattr(mod, "_read_json_cache", lambda: None)
        monkeypatch.setattr(
            mod, "_fetch_source",
            lambda if_modified_since: (RAW, "Tue, 15 Sep 2026 13:02:41 GMT"),
        )
        monkeypatch.setattr(mod, "_latest_stored_date", lambda: "2026-09-10")
        monkeypatch.setattr(mod, "last_completed_session_date", lambda now: "2026-09-14")

        def boom(*_a, **_k):
            raise HranaHttpError("TimeoutError: The read operation timed out")

        monkeypatch.setattr(mod.writer, "record_service_health", boom)

        assert mod.main([]) == 0
        assert ("rows", 2) in writes
        assert ("snapshot", "calm-streak") in [(c[0], c[1]) for c in writes if c[0] == "snapshot"]
        fallback = json.loads(mod.CALM_STREAK_JSON.read_text())
        assert fallback["data_date"] == "2026-09-14"
        assert fallback["missing"] is False
        err = capsys.readouterr().err
        assert "service_health heartbeat failed" in err
        assert "TimeoutError: The read operation timed out" in err
        assert "run failed" not in err

    def test_row_upsert_failure_still_fails_the_oneshot(self, writes, monkeypatch, capsys):
        """A timeout on the history upsert is the cycle failing, not
        telemetry. main() must still exit 1 and skip the JSON mirror.
        That is the shadowed-migration exit, not this heartbeat stall."""
        import fetch_calm_streak as mod

        monkeypatch.setattr(mod, "_read_json_cache", lambda: None)
        monkeypatch.setattr(mod, "_fetch_source", lambda if_modified_since: (RAW, None))
        monkeypatch.setattr(mod, "_latest_stored_date", lambda: "2026-09-10")
        monkeypatch.setattr(mod, "last_completed_session_date", lambda now: "2026-09-14")

        def boom(*_a, **_k):
            raise TimeoutError("The read operation timed out")

        monkeypatch.setattr(mod.writer, "upsert_calm_streak_rows", boom)

        assert mod.main([]) == 1
        assert not mod.CALM_STREAK_JSON.exists()
        err = capsys.readouterr().err
        assert "run failed" in err
        assert "heartbeat failed" not in err


class _EmptyCboeBody:
    """HTTP 200 application/json with a zero-length body.

    Live shape on 2026-10-10: cdn.cboe.com 307s to cdn-api.cboe.com, and
    _SPX.json is an empty object (etag of the empty file). json.load
    raises Expecting value: line 1 column 1 (char 0).
    """

    status = 200

    def __init__(self):
        self.headers = {
            "Content-Type": "application/json",
            "Content-Length": "0",
            "Last-Modified": "Sat, 10 Oct 2026 12:04:00 GMT",
        }

    def read(self, _n=-1):
        return b""

    def geturl(self):
        return "https://cdn-api.cboe.com/api/global/delayed_quotes/charts/historical/_SPX.json"

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class TestEmptySourceBody:
    def test_empty_cboe_200_keeps_last_good_and_exits_zero(
        self, writes, monkeypatch, capsys,
    ):
        """Page 2fbaca4f629c975ab1fe0dbe5bc6f825 (2026-10-10 14:30Z).

        The 02:40 cycle had already stored Friday 2026-10-09 (streak 14).
        The 14:30 cycle's Cboe GET followed the 307 and got HTTP 200
        content-length 0. json.load raised, main() printed run failed,
        and the oneshot exited 1 (Result=exit-code, NRestarts=0). An
        empty body must keep that snapshot, skip both heartbeats, and
        exit 0 so the unit does not latch until the next timer.
        """
        import fetch_calm_streak as mod

        cached = build_output(
            compute_streaks(LATE),
            scan_time="2026-10-10T02:40:53+00:00",
            source_last_modified="Sat, 10 Oct 2026 02:02:01 GMT",
        )
        cached["data_date"] = "2026-10-09"
        cached["current"]["streak"] = 14
        text = json.dumps(cached)
        mod.CALM_STREAK_JSON.write_text(text)
        monkeypatch.setattr(mod, "urlopen", lambda *_a, **_k: _EmptyCboeBody())

        assert mod.main([]) == 0
        err = capsys.readouterr().err
        assert "source body unusable; keeping last-good" in err
        assert "Expecting value: line 1 column 1 (char 0)" in err
        assert "run failed" not in err
        assert writes == []
        assert mod.CALM_STREAK_JSON.read_text() == text

    def test_empty_body_without_a_cache_still_fails(self, writes, monkeypatch, capsys):
        import fetch_calm_streak as mod

        monkeypatch.setattr(mod, "_read_json_cache", lambda: None)
        monkeypatch.setattr(mod, "urlopen", lambda *_a, **_k: _EmptyCboeBody())

        assert mod.main([]) == 1
        err = capsys.readouterr().err
        assert "run failed" in err
        assert "keeping last-good" not in err
        assert ("health", "calm-streak", "error") in writes
        assert not mod.CALM_STREAK_JSON.exists()

    def test_http_error_still_fails_the_oneshot(self, writes, monkeypatch, capsys):
        import fetch_calm_streak as mod
        from urllib.error import HTTPError

        cached = build_output(
            compute_streaks(LATE),
            scan_time="2026-10-10T02:40:53+00:00",
            source_last_modified="Sat, 10 Oct 2026 02:02:01 GMT",
        )
        mod.CALM_STREAK_JSON.write_text(json.dumps(cached))

        def boom(*_a, **_k):
            raise HTTPError(mod.CBOE_SPX_URL, 503, "unavailable", hdrs=None, fp=None)

        monkeypatch.setattr(mod, "urlopen", boom)

        assert mod.main([]) == 1
        err = capsys.readouterr().err
        assert "run failed" in err
        assert "keeping last-good" not in err
        assert ("health", "calm-streak", "error") in writes


class TestStorage:
    @pytest.fixture()
    def db(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT);")
        conn.executescript(MIGRATION.read_text())
        yield conn
        conn.close()

    def test_migration_registers_version_76_and_reruns(self, db):
        db.executescript(MIGRATION.read_text())
        assert [r[0] for r in db.execute("SELECT version FROM schema_migrations")] == [76]

    def test_schema(self, db):
        info = list(db.execute("PRAGMA table_info(calm_streak_history)"))
        assert [r[1] for r in info] == ["date", "open", "high", "low", "close",
                                        "band_pct", "streak", "recorded_at"]
        assert {r[1]: r[5] for r in info}["date"] == 1
        assert {r[1]: r[3] for r in info}["open"] == 0  # nullable
        names = {r[1] for r in db.execute("PRAGMA index_list(calm_streak_history)")}
        assert "idx_calm_streak_history_date_desc" in names

    def test_upsert_is_idempotent_per_date(self, db, monkeypatch):
        from db import writer

        monkeypatch.setattr(writer, "get_db", lambda: db)
        d = (_TODAY - timedelta(days=1)).isoformat()
        row = {"date": d, "open": None, "high": 101.0, "low": 100.0, "close": 100.5,
               "band_pct": 0.99, "streak": 3}
        writer.upsert_calm_streak_rows([row], recorded_at="r1")
        writer.upsert_calm_streak_rows([{**row, "streak": 4}], recorded_at="r2")
        assert list(db.execute("SELECT date, open, streak, recorded_at FROM calm_streak_history")) == [
            (d, None, 4, "r2")
        ]

    def test_writer_arity(self):
        from db import writer

        assert list(inspect.signature(writer.upsert_calm_streak_rows).parameters) == ["rows", "recorded_at"]
