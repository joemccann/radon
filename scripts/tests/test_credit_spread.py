"""Credit-equity divergence — parser, align, regime, and storage tests.

Ground-truth values are read from checked-in Yahoo chart fixtures:
  - fixtures/credit_spread_hyg_sample.json  — HYG daily closes (2024-01-02..2026-08-20)
  - fixtures/credit_spread_spx_sample.json  — ^GSPC daily closes (same window)
Expected numbers were derived by inspecting the fixtures (2026-08-21), not
computed by hand.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from fetch_credit_spread import (
    CREDIT_IB_HISTORY_CLIENT_IDS,
    HYG_SYMBOL,
    LOOKBACK_SESSIONS,
    NEAR_HIGH_RATIO,
    SPX_SYMBOL,
    align_series,
    build_output,
    classify_regime,
    combine_source,
    diff_new_rows,
    fetch_closes,
    fetch_ib_closes,
    is_near_high,
    lookback_return,
    lookback_window,
    merge_series,
    parse_yahoo_chart,
    persist_result,
)

FIXTURES = Path(__file__).parent / "fixtures"
HYG_JSON = (FIXTURES / "credit_spread_hyg_sample.json").read_text()
SPX_JSON = (FIXTURES / "credit_spread_spx_sample.json").read_text()
MIGRATION = Path(__file__).parents[1] / "db" / "migrations" / "0051_credit_spread.sql"

# Fixture pins (inspected 2026-08-21).
HYG_FIRST = 77.12999725341797
HYG_LAST = 79.55999755859375
SPX_FIRST = 4742.830078125
SPX_LAST = 7641.16015625
WIN_START = "2025-12-15"
HYG_ANCHOR = 80.61000061035156
SPX_ANCHOR = 6816.509765625
HYG_RET = HYG_LAST / HYG_ANCHOR - 1
SPX_RET = SPX_LAST / SPX_ANCHOR - 1
SPX_WIN_MAX = 7798.990234375


class TestParseYahooChart:
    def test_hyg_row_count_and_pins(self):
        hyg = parse_yahoo_chart(HYG_JSON)
        assert hyg["2024-01-02"] == pytest.approx(HYG_FIRST)
        assert hyg["2026-08-20"] == pytest.approx(HYG_LAST)
        assert len(hyg) == 659

    def test_spx_row_count_and_pins(self):
        spx = parse_yahoo_chart(SPX_JSON)
        assert spx["2024-01-02"] == pytest.approx(SPX_FIRST)
        assert spx["2026-08-20"] == pytest.approx(SPX_LAST)
        assert len(spx) == 658

    def test_skips_null_closes(self):
        payload = {
            "chart": {
                "result": [
                    {
                        "timestamp": [1704153600, 1704240000],
                        "indicators": {"quote": [{"close": [None, 77.5]}]},
                    }
                ]
            }
        }
        parsed = parse_yahoo_chart(json.dumps(payload))
        assert list(parsed.values()) == [77.5]


class TestAlignAndLookback:
    def test_inner_join_is_658_common_sessions(self):
        aligned = align_series(parse_yahoo_chart(HYG_JSON), parse_yahoo_chart(SPX_JSON))
        assert len(aligned) == 658
        assert aligned[0]["date"] == "2024-01-02"
        assert aligned[-1]["date"] == "2026-08-20"
        assert aligned[0]["hyg_close"] == pytest.approx(HYG_FIRST)
        assert aligned[0]["spx_close"] == pytest.approx(SPX_FIRST)
        assert aligned[-1]["hyg_close"] == pytest.approx(HYG_LAST)
        assert aligned[-1]["spx_close"] == pytest.approx(SPX_LAST)
        dates = [r["date"] for r in aligned]
        assert dates == sorted(dates)

    def test_168_session_window_and_returns(self):
        aligned = align_series(parse_yahoo_chart(HYG_JSON), parse_yahoo_chart(SPX_JSON))
        window = lookback_window(aligned, LOOKBACK_SESSIONS)
        assert len(window) == 168
        assert window[0]["date"] == WIN_START
        assert window[0]["hyg_close"] == pytest.approx(HYG_ANCHOR)
        assert window[0]["spx_close"] == pytest.approx(SPX_ANCHOR)
        assert lookback_return(window, "hyg_close") == pytest.approx(HYG_RET)
        assert lookback_return(window, "spx_close") == pytest.approx(SPX_RET)
        assert classify_regime(SPX_RET, HYG_RET) == "divergent"

    def test_near_high_pins_097_vs_098(self):
        assert SPX_LAST / SPX_WIN_MAX == pytest.approx(0.9797627547436404)
        assert is_near_high(SPX_LAST, SPX_WIN_MAX, 0.97) is True
        assert is_near_high(SPX_LAST, SPX_WIN_MAX, 0.98) is False
        assert NEAR_HIGH_RATIO == 0.97
        assert is_near_high(SPX_LAST, SPX_WIN_MAX) is True


class TestClassifyRegime:
    def test_divergent_requires_strict_signs(self):
        assert classify_regime(0.01, -0.01) == "divergent"
        assert classify_regime(0.01, 0.0) == "coupled"
        assert classify_regime(0.0, -0.01) == "coupled"

    def test_other_quadrants(self):
        assert classify_regime(0.01, 0.01) == "coupled"
        assert classify_regime(-0.01, -0.01) == "risk-off"
        assert classify_regime(-0.01, 0.01) == "credit-lead"

    def test_missing_returns_have_no_regime(self):
        """REL-067 / R-161: this asserted `"coupled"`, which is ALSO the
        benign risk-on label. `lookback_return` returns None for a window
        shorter than 2 rows, so a first run after a cache wipe reported
        "credit and equities in agreement" — the reassuring label as the
        failure mode of an indicator that exists to surface `divergent`."""
        assert classify_regime(None, -0.01) is None
        assert classify_regime(0.01, None) is None
        assert classify_regime(0.0, 0.0) == "coupled"  # a REAL flat pair


class TestMergeDiff:
    def test_fresh_wins_per_date(self):
        cached = [{"date": "2026-08-19", "hyg_close": 1.0, "spx_close": 2.0}]
        fresh = [
            {"date": "2026-08-19", "hyg_close": 1.1, "spx_close": 2.1},
            {"date": "2026-08-20", "hyg_close": 1.2, "spx_close": 2.2},
        ]
        merged = merge_series(cached, fresh)
        assert [r["date"] for r in merged] == ["2026-08-19", "2026-08-20"]
        assert merged[0]["hyg_close"] == 1.1

    def test_diff_ignores_identical_rows(self):
        row = {"date": "2026-08-20", "hyg_close": HYG_LAST, "spx_close": SPX_LAST}
        assert diff_new_rows([row], [row]) == []
        changed = {**row, "hyg_close": HYG_LAST + 0.01}
        assert diff_new_rows([row], [changed]) == [changed]


class TestBuildOutput:
    def test_payload_contract_from_fixture(self):
        aligned = align_series(parse_yahoo_chart(HYG_JSON), parse_yahoo_chart(SPX_JSON))
        payload = build_output(aligned, source="ib")
        assert payload["source"] == "ib"
        assert payload["count"] == 658
        # R-190 adds source_by_ticker: the collapsed `source` string cannot
        # say WHICH leg fell back to Yahoo.
        assert set(payload.keys()) == {
            "scan_time", "source", "source_by_ticker", "count", "current", "series",
        }
        current = payload["current"]
        assert current["date"] == "2026-08-20"
        assert current["hyg_close"] == pytest.approx(HYG_LAST)
        assert current["spx_close"] == pytest.approx(SPX_LAST)
        assert current["hyg_ret"] == pytest.approx(HYG_RET)
        assert current["spx_ret"] == pytest.approx(SPX_RET)
        assert current["regime"] == "divergent"
        assert current["near_high"] is True
        assert payload["series"][0]["date"] == "2024-01-02"

    def test_scan_time_is_tz_aware_utc(self):
        from datetime import datetime, timezone

        payload = build_output(
            [{"date": "2026-08-20", "hyg_close": HYG_LAST, "spx_close": SPX_LAST}]
        )
        parsed = datetime.fromisoformat(payload["scan_time"].replace("Z", "+00:00"))
        assert parsed.tzinfo is not None
        assert parsed.utcoffset() == timezone.utc.utcoffset(None)


@pytest.fixture()
def persist_calls(monkeypatch, tmp_path):
    import fetch_credit_spread as fcs

    calls: list[tuple] = []
    monkeypatch.setattr(fcs, "CREDIT_SPREAD_JSON", tmp_path / "credit_spread.json")
    monkeypatch.setattr(
        fcs.writer,
        "ensure_no_replica_for_writers",
        lambda: calls.append(("guard",)),
    )
    monkeypatch.setattr(
        fcs.writer,
        "upsert_credit_spread_rows",
        lambda rows, recorded_at: calls.append(("rows", len(rows))),
    )
    monkeypatch.setattr(
        fcs.writer,
        "upsert_scan_snapshot",
        lambda service, scan_time, payload: calls.append(("snapshot", service)),
    )
    monkeypatch.setattr(
        fcs.writer,
        "record_service_health",
        lambda service, state, finished_at=None, error=None: calls.append(
            ("health", service, state)
        ),
    )
    return calls


class TestPersistResult:
    def test_empty_series_heartbeats_error_instead_of_going_silent(self, persist_calls):
        """No rows, no snapshot — but the cycle still has to leave a row.

        docs/operations.md:187: a row that NEVER appears is worse than a stale
        one. credit-spread carries a 26h watchdog window, so a silent return
        reads as ordinary staleness a day after a hard failure.
        """
        persist_result(build_output([]), [])
        assert "rows" not in [c[0] for c in persist_calls]
        assert ("snapshot", "credit-spread") not in persist_calls
        assert ("health", "credit-spread", "error") in persist_calls
        assert ("health", "credit-spread", "ok") not in persist_calls

    def test_changed_rows_write_everything_in_order(self, persist_calls):
        import fetch_credit_spread as fcs

        rows = [{"date": "2026-08-20", "hyg_close": HYG_LAST, "spx_close": SPX_LAST}]
        persist_result(build_output(rows), rows)
        assert persist_calls == [
            ("guard",),
            ("rows", 1),
            ("snapshot", "credit-spread"),
            ("health", "credit-spread", "ok"),
        ]
        assert fcs.CREDIT_SPREAD_JSON.exists()

    def test_unchanged_day_heartbeats_without_row_upserts(self, persist_calls):
        rows = [{"date": "2026-08-20", "hyg_close": HYG_LAST, "spx_close": SPX_LAST}]
        persist_result(build_output(rows), [])
        kinds = [c[0] for c in persist_calls]
        assert "rows" not in kinds
        assert ("snapshot", "credit-spread") in persist_calls
        assert ("health", "credit-spread", "ok") in persist_calls


CACHED_ROW = {"date": "2026-08-20", "hyg_close": HYG_LAST, "spx_close": SPX_LAST}
CACHED_SCAN_TIME = "2026-08-20T21:45:00Z"


def _stub_all_sources_down(monkeypatch) -> None:
    import fetch_credit_spread as fcs

    for name in ("fetch_ib_closes", "fetch_uw_closes", "fetch_yahoo_closes"):
        monkeypatch.setattr(fcs, name, lambda tickers: {})


class TestAllSourcesDown:
    """IB, UW and Yahoo all empty: the cache is re-served as stale_source and the
    heartbeat is an error so the watchdog pages instead of reading a fresh ok."""

    def test_cached_series_is_reserved_as_stale_source_with_error_heartbeat(
        self, persist_calls, monkeypatch
    ):
        import fetch_credit_spread as fcs

        fcs.CREDIT_SPREAD_JSON.write_text(
            json.dumps(build_output([CACHED_ROW], scan_time=CACHED_SCAN_TIME, source="yahoo"))
        )
        _stub_all_sources_down(monkeypatch)

        payload = fcs.run()

        assert ("health", "credit-spread", "error") in persist_calls
        assert ("health", "credit-spread", "ok") not in persist_calls
        assert payload["status"] == "stale_source"
        assert payload["count"] == 1
        assert payload["current"]["date"] == CACHED_ROW["date"]
        assert payload["scan_time"] != CACHED_SCAN_TIME
        assert "rows" not in [c[0] for c in persist_calls]
        assert ("snapshot", "credit-spread") in persist_calls
        assert json.loads(fcs.CREDIT_SPREAD_JSON.read_text())["status"] == "stale_source"

    def test_no_cache_heartbeats_error_before_it_raises(self, persist_calls, monkeypatch):
        """Raise loudly AND leave a row — the raise is the contract, silence is not.

        Every source down with no cache is a hard outage. Dying before any
        `record_service_health` call leaves the 26h watchdog window looking at
        a service that merely went stale (docs/operations.md:187).
        """
        import fetch_credit_spread as fcs

        _stub_all_sources_down(monkeypatch)

        with pytest.raises(RuntimeError):
            fcs.run()
        assert ("health", "credit-spread", "error") in persist_calls
        assert ("health", "credit-spread", "ok") not in persist_calls

    def test_unchanged_day_with_a_live_source_is_still_ok(self, persist_calls, monkeypatch):
        import fetch_credit_spread as fcs

        fcs.CREDIT_SPREAD_JSON.write_text(
            json.dumps(build_output([CACHED_ROW], scan_time=CACHED_SCAN_TIME, source="ib"))
        )
        monkeypatch.setattr(
            fcs,
            "fetch_ib_closes",
            lambda tickers: {
                HYG_SYMBOL: {CACHED_ROW["date"]: HYG_LAST},
                SPX_SYMBOL: {CACHED_ROW["date"]: SPX_LAST},
            },
        )

        payload = fcs.run()

        assert "status" not in payload
        assert "rows" not in [c[0] for c in persist_calls]
        assert ("health", "credit-spread", "ok") in persist_calls


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


class TestCreditSpreadStorage:
    @pytest.fixture()
    def db(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT);"
        )
        conn.executescript(MIGRATION.read_text())
        yield conn
        conn.close()

    @pytest.fixture()
    def recording_writer(self, db, monkeypatch):
        """The REAL db.writer with get_db() wired to the in-memory 0051 schema."""
        from db import writer

        recording = _RecordingConnection(db)
        monkeypatch.setattr(writer, "get_db", lambda: recording)
        return writer, recording

    def test_migration_registers_version_51(self, db):
        versions = [r[0] for r in db.execute("SELECT version FROM schema_migrations")]
        assert versions == [51]

    def test_schema_columns(self, db):
        cols = [r[1] for r in db.execute("PRAGMA table_info(credit_spread_history)")]
        assert cols == ["date", "hyg_close", "spx_close", "recorded_at"]

    def test_upsert_is_idempotent_per_date(self, db, recording_writer):
        writer, recording = recording_writer
        stale = {"date": "2026-08-20", "hyg_close": 79.0, "spx_close": 7600.0}
        fresh = {"date": "2026-08-20", "hyg_close": HYG_LAST, "spx_close": SPX_LAST}

        writer.upsert_credit_spread_rows([stale], recorded_at="2026-08-20T21:45:00Z")
        writer.upsert_credit_spread_rows([fresh], recorded_at="2026-08-21T21:45:00Z")

        rows = list(
            db.execute(
                "SELECT date, hyg_close, spx_close, recorded_at FROM credit_spread_history"
            )
        )
        assert rows == [("2026-08-20", HYG_LAST, SPX_LAST, "2026-08-21T21:45:00Z")]
        assert recording.commits == 2
        assert all(
            "ON CONFLICT(date) DO UPDATE" in sql for sql, _ in recording.statements
        )

    def test_upsert_chunks_many_rows_into_multi_row_inserts(self, db, recording_writer):
        writer, recording = recording_writer
        rows = [
            {"date": f"2025-{m:02d}-{d:02d}", "hyg_close": 70.0 + m, "spx_close": 6000.0 + d}
            for m in range(1, 13)
            for d in range(1, 29)
        ]

        writer.upsert_credit_spread_rows(rows, recorded_at="2026-08-21T21:45:00Z")

        assert db.execute("SELECT COUNT(*) FROM credit_spread_history").fetchone() == (
            len(rows),
        )
        assert len(recording.statements) < len(rows)
        assert db.execute(
            "SELECT hyg_close, spx_close FROM credit_spread_history WHERE date = '2025-12-28'"
        ).fetchone() == (82.0, 6028.0)


HYG_BARS = {"2026-08-19": 79.4, "2026-08-20": HYG_LAST}
SPX_BARS = {"2026-08-19": 7600.0, "2026-08-20": SPX_LAST}


class TestCombineSource:
    def test_single_source(self):
        assert combine_source({"HYG": "ib", "SPX": "ib"}) == "ib"

    def test_mixed_sources_are_sorted_and_joined(self):
        assert combine_source({"HYG": "ib", "SPX": "yahoo"}) == "ib+yahoo"


class TestFetchClosesCascade:
    def test_ib_both_never_calls_uw_or_yahoo(self):
        uw_calls: list = []
        yahoo_calls: list = []

        closes, sources = fetch_closes(
            fetch_ib=lambda tickers: {HYG_SYMBOL: HYG_BARS, SPX_SYMBOL: SPX_BARS},
            fetch_uw=lambda tickers: uw_calls.append(tickers) or {},
            fetch_yahoo=lambda tickers: yahoo_calls.append(tickers) or {},
        )

        assert closes == {HYG_SYMBOL: HYG_BARS, SPX_SYMBOL: SPX_BARS}
        assert sources == {HYG_SYMBOL: "ib", SPX_SYMBOL: "ib"}
        assert combine_source(sources) == "ib"
        assert uw_calls == []
        assert yahoo_calls == []

    def test_uw_fills_when_ib_empty_and_skips_yahoo(self):
        yahoo_calls: list = []

        closes, sources = fetch_closes(
            fetch_ib=lambda tickers: {},
            fetch_uw=lambda tickers: {HYG_SYMBOL: HYG_BARS, SPX_SYMBOL: SPX_BARS},
            fetch_yahoo=lambda tickers: yahoo_calls.append(tickers) or {},
        )

        assert sources == {HYG_SYMBOL: "uw", SPX_SYMBOL: "uw"}
        assert combine_source(sources) == "uw"
        assert yahoo_calls == []

    def test_yahoo_is_last_resort(self):
        ib_calls: list = []
        uw_calls: list = []

        closes, sources = fetch_closes(
            fetch_ib=lambda tickers: ib_calls.append(list(tickers)) or {},
            fetch_uw=lambda tickers: uw_calls.append(list(tickers)) or {},
            fetch_yahoo=lambda tickers: {HYG_SYMBOL: HYG_BARS, SPX_SYMBOL: SPX_BARS},
        )

        assert ib_calls == [[HYG_SYMBOL, SPX_SYMBOL]]
        assert uw_calls == [[HYG_SYMBOL, SPX_SYMBOL]]
        assert sources == {HYG_SYMBOL: "yahoo", SPX_SYMBOL: "yahoo"}
        assert combine_source(sources) == "yahoo"

    def test_partial_ib_asks_uw_then_yahoo_only_for_the_gap(self):
        uw_calls: list = []
        yahoo_calls: list = []

        closes, sources = fetch_closes(
            fetch_ib=lambda tickers: {HYG_SYMBOL: HYG_BARS},
            fetch_uw=lambda tickers: uw_calls.append(list(tickers)) or {},
            fetch_yahoo=lambda tickers: yahoo_calls.append(list(tickers))
            or {SPX_SYMBOL: SPX_BARS},
        )

        assert uw_calls == [[SPX_SYMBOL]]
        assert yahoo_calls == [[SPX_SYMBOL]]
        assert sources == {HYG_SYMBOL: "ib", SPX_SYMBOL: "yahoo"}
        assert combine_source(sources) == "ib+yahoo"


class TestFetchIbAuthGate:
    def test_skips_connect_when_awaiting_2fa(self, monkeypatch):
        calls: list[str] = []
        monkeypatch.setattr("fetch_credit_spread._ib_auth_state", lambda: "awaiting_2fa")
        monkeypatch.setattr(
            "fetch_credit_spread._connect_ib_with_retry",
            lambda *_a, **_k: calls.append("connect") or False,
        )

        assert fetch_ib_closes([HYG_SYMBOL, SPX_SYMBOL]) == {}
        assert calls == []


class TestCreditIbClientIds:
    def test_history_ids_are_scanner_range_and_unused_elsewhere(self):
        assert CREDIT_IB_HISTORY_CLIENT_IDS == (56, 69)
        assert all(50 <= cid <= 69 for cid in CREDIT_IB_HISTORY_CLIENT_IDS)
        assert 0 not in CREDIT_IB_HISTORY_CLIENT_IDS
        # CRI 50-61 (55 is portfolio_report), breadth 62-66, RV-ratio 67-68.
        taken = set(range(50, 55)) | {55} | set(range(57, 69))
        assert set(CREDIT_IB_HISTORY_CLIENT_IDS).isdisjoint(taken)
