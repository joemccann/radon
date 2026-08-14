"""BPI scan — aggregation math, gating, persistence discipline (plan §4/§7).

Window-relative dates only; no network; writers/heartbeat monkeypatched
(db.client refuses real connections under PYTEST_CURRENT_TEST anyway).
"""
from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import bpi_scan as bpi
from clients.index_constituents import normalize_ticker


def _sessions(n: int) -> list[str]:
    """Trailing n weekday session dates ending today-ish, ascending."""
    out: list[str] = []
    day = date.today()
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day -= timedelta(days=1)
    return sorted(out)


def _flat_series(sessions: list[str], state) -> tuple[list[str], list]:
    return sessions, [state] * len(sessions)


class TestAggregateBpi:
    def test_bpi_math_small_member_set(self):
        sessions = _sessions(3)
        member_series = {
            "AAA": _flat_series(sessions, "buy"),
            "BBB": _flat_series(sessions, "buy"),
            "CCC": _flat_series(sessions, "sell"),
        }
        rows = bpi.aggregate_bpi(member_series, sessions)
        assert [r["date"] for r in rows] == sessions
        latest = rows[-1]
        assert latest["members"] == 3
        assert latest["bullish"] == 2
        assert latest["bpi"] == pytest.approx(66.67, abs=0.01)

    def test_unresolved_members_excluded_from_denominator(self):
        sessions = _sessions(2)
        member_series = {
            "AAA": _flat_series(sessions, "buy"),
            "UNRESOLVED": _flat_series(sessions, None),
        }
        rows = bpi.aggregate_bpi(member_series, sessions)
        assert rows[-1] == {
            "date": sessions[-1], "bpi": 100.0, "members": 1, "bullish": 1,
        }

    def test_all_unresolved_yields_no_rows(self):
        sessions = _sessions(2)
        rows = bpi.aggregate_bpi({"AAA": _flat_series(sessions, None)}, sessions)
        assert rows == []

    def test_carry_forward_on_missing_member_day(self):
        sessions = _sessions(4)
        # Member data stops after the second session on a buy signal: the
        # last two sessions must still count it as bullish.
        truncated = (sessions[:2], ["sell", "buy"])
        member_series = {
            "GAPPY": truncated,
            "STEADY": _flat_series(sessions, "sell"),
        }
        rows = bpi.aggregate_bpi(member_series, sessions)
        assert [r["bullish"] for r in rows] == [0, 1, 1, 1]
        assert all(r["members"] == 2 for r in rows)

    def test_member_not_yet_listed_stays_out_until_first_signal(self):
        sessions = _sessions(3)
        late = (sessions[2:], ["buy"])
        rows = bpi.aggregate_bpi(
            {"LATE": late, "STEADY": _flat_series(sessions, "sell")}, sessions
        )
        assert [r["members"] for r in rows] == [1, 1, 2]


class TestSignalHelpers:
    def test_member_signal_series_aligns_dates_and_states(self):
        sessions = _sessions(40)
        closes = {d: 100.0 + i for i, d in enumerate(sessions)}
        dates, states = bpi.member_signal_series(closes)
        assert dates == sessions
        assert len(states) == len(dates)

    def test_classify_state_thresholds(self):
        assert bpi.classify_state(30.0) == "OVERSOLD"
        assert bpi.classify_state(30.01) == "NEUTRAL"
        assert bpi.classify_state(70.0) == "OVERBOUGHT"
        assert bpi.classify_state(69.99) == "NEUTRAL"

    def test_cross_up_30_detection(self):
        up = [{"date": "a", "bpi": 25.0}, {"date": "b", "bpi": 35.0}]
        flat = [{"date": "a", "bpi": 35.0}, {"date": "b", "bpi": 40.0}]
        down = [{"date": "a", "bpi": 35.0}, {"date": "b", "bpi": 25.0}]
        exact = [{"date": "a", "bpi": 29.9}, {"date": "b", "bpi": 30.0}]
        assert bpi.detect_cross_up_30(up) is True
        assert bpi.detect_cross_up_30(flat) is False
        assert bpi.detect_cross_up_30(down) is False
        assert bpi.detect_cross_up_30(exact) is True
        assert bpi.detect_cross_up_30([{"date": "a", "bpi": 10.0}]) is False


def _payload(member_series, member_count, index_symbol="NDX"):
    return bpi.build_index_payload(
        index_symbol=index_symbol,
        member_series=member_series,
        member_count=member_count,
        taken_at="2099-01-01T00:00:00Z",
        sources={"constituents": "seed", "member_close_fetches": {"yahoo": 0, "stored": 0}},
    )


class TestBuildIndexPayload:
    def test_full_payload_contract(self):
        sessions = _sessions(40)
        member_series = {
            "AAA": _flat_series(sessions, "buy"),
            "BBB": _flat_series(sessions, "sell"),
            "CCC": _flat_series(sessions, "sell"),
            "DDD": _flat_series(sessions, "sell"),
        }
        payload = _payload(member_series, member_count=4)
        assert payload["missing"] is False
        assert payload["schema_version"] == 1
        assert payload["index_symbol"] == "NDX"
        assert payload["index_name"] == "Nasdaq-100"
        assert payload["as_of_session"] == sessions[-1]
        assert payload["bpi"] == 25.0
        assert payload["members"] == 4
        assert payload["bullish"] == 1
        assert payload["state"] == "OVERSOLD"
        assert payload["cross_up_30"] is False
        assert payload["thresholds"] == {"oversold": 30, "overbought": 70}
        assert len(payload["history"]) == len(sessions)
        assert payload["history"][-1] == {"date": sessions[-1], "bpi": 25.0}
        assert payload["sources"]["constituents"] == "seed"

    def test_below_min_sessions_is_missing(self):
        sessions = _sessions(bpi.MIN_SESSIONS - 1)
        payload = _payload({"AAA": _flat_series(sessions, "buy")}, member_count=1)
        assert payload["missing"] is True
        assert payload["reason"] == "insufficient_history"
        assert payload["index_symbol"] == "NDX"

    def test_below_latest_coverage_is_missing(self):
        sessions = _sessions(40)
        member_series = {
            "AAA": _flat_series(sessions, "buy"),
            "BBB": _flat_series(sessions, "sell"),
            "CCC": _flat_series(sessions, "sell"),
        }
        # 3 of 10 members resolved on the latest session — under the 80% gate.
        payload = _payload(member_series, member_count=10)
        assert payload["missing"] is True
        assert payload["reason"] == "insufficient_coverage"

    def test_history_trimmed_to_trailing_sessions(self):
        sessions = _sessions(bpi.HISTORY_SESSIONS + 20)
        payload = _payload({"AAA": _flat_series(sessions, "buy")}, member_count=1)
        assert len(payload["history"]) == bpi.HISTORY_SESSIONS
        assert payload["history"][-1]["date"] == sessions[-1]


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Isolate run_scan: record persistence + heartbeat, temp data dir."""
    calls: dict = {"persist": [], "cycles": 0}

    @contextmanager
    def fake_cycle(no_db):
        calls["cycles"] += 1
        yield None

    monkeypatch.setattr(bpi, "_persist_index", lambda idx, p: calls["persist"].append(idx))
    monkeypatch.setattr(bpi, "_heartbeat_cycle", fake_cycle)
    monkeypatch.setenv("BPI_DATA_DIR", str(tmp_path))
    calls["data_dir"] = tmp_path
    return calls


class TestYahooSparkParse:
    """Nightly incremental goes batch-first: the v8 spark endpoint serves up
    to ~100 symbols per request anonymously (the per-symbol chart endpoint
    was tarpitted to ~60s/request on 2026-07-27 — 415 SPX fetches took 52
    minutes and blew the unit's start budget). Parser is pure. A null LAST
    close is recovered from spark meta the same way parse_yahoo_chart
    recovers regularMarketPrice when regularMarketTime maps to that last
    bar's session; mid-series nulls stay dropped. Members still missing
    the last completed session fall back to the per-symbol chart path."""

    def _payload(self):
        day_23, day_24, day_27 = 1784813400, 1784899800, 1785159000
        close_27 = 1785182400  # 2026-07-27 20:00 UTC close
        return {
            "AAPL": {"symbol": "AAPL", "timestamp": [day_23, day_27], "close": [321.66, 333.5]},
            "MSFT": {
                "symbol": "MSFT",
                "timestamp": [day_23, day_24, day_27],
                "close": [381.58, 380.0, None],
                "meta": {
                    "regularMarketTime": close_27,
                    "regularMarketPrice": 428.17,
                },
            },
        }

    def test_parses_dates_and_recovers_null_last_from_meta(self):
        bars = bpi.parse_yahoo_spark(self._payload())
        assert bars["AAPL"] == {"2026-07-23": 321.66, "2026-07-27": 333.5}
        assert bars["MSFT"] == {
            "2026-07-23": 381.58,
            "2026-07-24": 380.0,
            "2026-07-27": 428.17,
        }

    def test_mid_series_null_stays_dropped(self):
        day1, day2, day3 = 1784727000, 1784813400, 1784899800
        payload = {
            "NVDA": {
                "symbol": "NVDA",
                "timestamp": [day1, day2, day3],
                "close": [325.89, None, 330.0],
                "meta": {
                    "regularMarketTime": 1784923200,
                    "regularMarketPrice": 999.0,
                },
            }
        }
        bars = bpi.parse_yahoo_spark(payload)
        assert "2026-07-23" not in bars["NVDA"]
        assert bars["NVDA"]["2026-07-24"] == 330.0
        assert 999.0 not in bars["NVDA"].values()

    def test_chunks_respect_spark_symbol_cap(self):
        # Empirical endpoint cap: >20 symbols per request → HTTP 400 (the
        # 2026-07-27 run 400'd every 100-symbol chunk, each tarpitted to 60s).
        assert bpi.SPARK_BATCH_SIZE <= 20

    def test_chart_fallback_for_members_batch_left_stale(self):
        bars = bpi.parse_yahoo_spark(self._payload())
        stragglers = bpi.members_still_stale(
            ["AAPL", "MSFT", "TSLA"], bars, last_complete="2026-07-27"
        )
        assert stragglers == ["TSLA"]


class TestYahooChartParse:
    """Yahoo intermittently serves the just-closed session's candle with
    close:null while meta.regularMarketPrice carries the official close
    (2026-07-24 NDX repro — all mega-caps null a day later). The parser
    recovers the final bar from meta ONLY when regularMarketTime maps to
    the same UTC session date; anything else stays dropped."""

    def _result(self, ts_closes, meta):
        return {
            "timestamp": [t for t, _ in ts_closes],
            "indicators": {"quote": [{"close": [c for _, c in ts_closes]}]},
            "meta": meta,
        }

    def test_recovers_null_last_bar_from_meta_same_session(self):
        day1, day2 = 1784813400, 1784899800  # 2026-07-23, 2026-07-24 13:30 UTC opens
        close_time = 1784923200              # 2026-07-24 20:00 UTC close
        result = self._result(
            [(day1, 321.66), (day2, None)],
            {"regularMarketTime": close_time, "regularMarketPrice": 333.02},
        )
        bars = bpi.parse_yahoo_chart(result)
        assert bars["2026-07-23"] == 321.66
        assert bars["2026-07-24"] == 333.02

    def test_mid_series_null_stays_dropped(self):
        day1, day2, day3 = 1784727000, 1784813400, 1784899800
        result = self._result(
            [(day1, 325.89), (day2, None), (day3, 330.0)],
            {"regularMarketTime": 1784923200, "regularMarketPrice": 999.0},
        )
        bars = bpi.parse_yahoo_chart(result)
        assert "2026-07-23" not in bars
        assert bars["2026-07-24"] == 330.0

    def test_no_recovery_when_meta_is_a_different_session(self):
        day2 = 1784899800  # 2026-07-24
        stale_meta_time = 1784813400  # 2026-07-23
        result = self._result(
            [(day2, None)],
            {"regularMarketTime": stale_meta_time, "regularMarketPrice": 999.0},
        )
        assert "2026-07-24" not in bpi.parse_yahoo_chart(result)


class TestStaleFlag:
    """The payload must self-report when its latest aggregated session lags
    the last completed ET session (Yahoo candle lag) so callers and the
    catch-up timer pass can see the series has not converged to latest."""

    def test_stale_true_when_latest_session_lags(self, monkeypatch):
        sessions = _sessions(40)
        member_series = {m: _flat_series(sessions, "buy") for m in "ABCDE"}
        monkeypatch.setattr(bpi, "last_completed_session_date", lambda: "2099-12-31")
        payload = _payload(member_series, 5)
        assert payload["stale"] is True

    def test_stale_false_when_current(self, monkeypatch):
        sessions = _sessions(40)
        member_series = {m: _flat_series(sessions, "buy") for m in "ABCDE"}
        monkeypatch.setattr(bpi, "last_completed_session_date", lambda: sessions[-1])
        payload = _payload(member_series, 5)
        assert payload["stale"] is False


class TestPersistenceGating:
    def test_missing_payload_skips_writers_but_heartbeats(self, recorded, monkeypatch):
        missing = {"missing": True, "index_symbol": "NDX", "reason": "insufficient_coverage"}
        monkeypatch.setattr(bpi, "scan_index", lambda idx, **kw: dict(missing))
        envelope = bpi.run_scan(["NDX"], backfill=False, no_db=False)
        assert recorded["persist"] == []
        assert recorded["cycles"] == 1
        assert envelope["indices"]["NDX"]["missing"] is True
        assert not (recorded["data_dir"] / "bpi.json").exists()

    def test_good_payload_persists_and_mirrors(self, recorded, monkeypatch):
        sessions = _sessions(40)
        good = _payload({"AAA": _flat_series(sessions, "buy")}, member_count=1)
        monkeypatch.setattr(bpi, "scan_index", lambda idx, **kw: dict(good))
        envelope = bpi.run_scan(["NDX"], backfill=False, no_db=False)
        assert recorded["persist"] == ["NDX"]
        mirror = json.loads((recorded["data_dir"] / "bpi.json").read_text())
        assert mirror["indices"]["NDX"]["bpi"] == 100.0
        assert envelope["generated_at"]

    def test_no_db_skips_persistence_but_still_mirrors(self, recorded, monkeypatch):
        sessions = _sessions(40)
        good = _payload({"AAA": _flat_series(sessions, "buy")}, member_count=1)
        monkeypatch.setattr(bpi, "scan_index", lambda idx, **kw: dict(good))
        bpi.run_scan(["NDX"], backfill=False, no_db=True)
        assert recorded["persist"] == []
        assert (recorded["data_dir"] / "bpi.json").exists()

    def test_missing_run_leaves_existing_mirror_untouched(self, recorded, monkeypatch):
        existing = {"generated_at": "old", "indices": {"SPX": {"bpi": 55.0}}}
        (recorded["data_dir"] / "bpi.json").write_text(json.dumps(existing))
        missing = {"missing": True, "index_symbol": "NDX", "reason": "insufficient_history"}
        monkeypatch.setattr(bpi, "scan_index", lambda idx, **kw: dict(missing))
        bpi.run_scan(["NDX"], backfill=False, no_db=False)
        assert json.loads((recorded["data_dir"] / "bpi.json").read_text()) == existing


class TestCliContract:
    def test_stdout_is_single_json_document(self, recorded, monkeypatch, capsys):
        sessions = _sessions(40)
        good = _payload({"AAA": _flat_series(sessions, "buy")}, member_count=1)
        monkeypatch.setattr(bpi, "scan_index", lambda idx, **kw: dict(good))
        monkeypatch.setattr(sys, "argv", ["bpi_scan.py", "--index", "NDX", "--no-db"])
        bpi.main()
        out = capsys.readouterr().out
        envelope = json.loads(out)
        assert set(envelope["indices"]) == {"NDX"}

    def test_index_all_expands_to_three(self, recorded, monkeypatch, capsys):
        seen: list[str] = []

        def fake_scan(idx, **kw):
            seen.append(idx)
            return {"missing": True, "index_symbol": idx, "reason": "insufficient_history"}

        monkeypatch.setattr(bpi, "scan_index", fake_scan)
        monkeypatch.setattr(sys, "argv", ["bpi_scan.py", "--no-db"])
        bpi.main()
        assert seen == ["NDX", "SPX", "RUT"]
        json.loads(capsys.readouterr().out)

    def test_invalid_index_exits_2(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["bpi_scan.py", "--index", "DAX"])
        with pytest.raises(SystemExit):
            bpi.main()


class TestConstituentNormalization:
    def test_brk_dot_b_normalizes_to_dash(self):
        assert normalize_ticker("BRK.B") == "BRK-B"


# ── writer family vs migration 0031 (test_rv_ratio_writer pattern) ──

_MIGRATION = _SCRIPTS_DIR / "db" / "migrations" / "0031_bpi.sql"


def _split_statements(sql: str) -> list[str]:
    import re

    stripped = "\n".join(re.sub(r"^\s*--.*$", "", line) for line in sql.splitlines())
    return [s.strip() for s in re.split(r";\s*$", stripped, flags=re.MULTILINE) if s.strip()]


@pytest.fixture
def db_with_schema(monkeypatch: pytest.MonkeyPatch):
    import sqlite3

    # db.client imports libsql_experimental at module load; absent locally
    # the fixture cannot patch it (CI/VPS installs it and runs these).
    pytest.importorskip("libsql_experimental")
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    for stmt in _split_statements(_MIGRATION.read_text(encoding="utf-8")):
        conn.execute(stmt)
    conn.commit()

    import db.client as client_mod
    monkeypatch.setattr(client_mod, "_cached", conn, raising=False)
    monkeypatch.setattr(client_mod, "get_db", lambda: conn)

    import importlib
    import db.writer as writer_mod
    importlib.reload(writer_mod)

    try:
        yield conn
    finally:
        conn.close()


class TestBpiWriters:
    def _rows(self, n: int) -> list[dict]:
        sessions = _sessions(n)
        return [
            {"date": d, "bpi": 50.0 + i, "members": 100, "bullish": 50 + i}
            for i, d in enumerate(sessions)
        ]

    def test_history_upsert_is_chunked_and_idempotent(self, db_with_schema):
        from db import writer

        rows = self._rows(450)  # forces two multi-row INSERT chunks
        writer.upsert_bpi_history_rows("NDX", rows)
        writer.upsert_bpi_history_rows("NDX", rows)  # OR REPLACE — no dupes
        count = db_with_schema.execute(
            "SELECT COUNT(*) FROM bpi_history WHERE index_symbol='NDX'"
        ).fetchone()[0]
        assert count == 450

    def test_snapshot_single_row_replace(self, db_with_schema):
        from db import writer

        writer.upsert_bpi_snapshot("NDX", "t1", {"bpi": 40.0})
        writer.upsert_bpi_snapshot("NDX", "t2", {"bpi": 41.0})
        rows = db_with_schema.execute(
            "SELECT taken_at, payload FROM bpi_snapshots WHERE index_symbol='NDX'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "t2"
        assert json.loads(rows[0][1])["bpi"] == 41.0
