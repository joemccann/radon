"""RSI OVERSOLD — SPX percent of members with Wilder RSI(14) below 30.
Red tests against docs/indicators/rsi-oversold.md.

Fixture facts are derived by INSPECTING the checked-in ma-ratio fixture:
  - fixtures/ma_ratio_member_closes_sample.json — real Yahoo v8 daily closes
    (range=1y, 252 sessions per symbol, captured 2026-09-02) for four S&P 500
    members (AAPL, MSFT, XOM, JNJ) plus the ^GSPC overlay symbol. Every RSI
    expectation below is RECOMPUTED from the fixture's own closes with a naive
    independent Wilder implementation, never mental arithmetic.
"""

from __future__ import annotations

import inspect
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pytest

from rsi_oversold_scan import (
    MIN_ELIGIBLE_FRACTION,
    MIN_LATEST_COVERAGE,
    MIN_SESSIONS,
    OVERSOLD_RSI,
    RSI_PERIOD,
    SERVICE,
    SWEEP_BUDGET_S,
    THRESHOLD_PCT,
    aggregate_rsi_oversold,
    build_output,
    classify_state,
    detect_cross_up,
    highest_since,
    is_oversold,
    persist_result,
    rsi_series,
)

FIXTURES = Path(__file__).parent / "fixtures"
MIGRATION = Path(__file__).parents[1] / "db" / "migrations" / "0092_rsi_oversold.sql"

FIXTURE = json.loads((FIXTURES / "ma_ratio_member_closes_sample.json").read_text())
CLOSES: dict[str, dict[str, float]] = FIXTURE["closes"]
MEMBERS = ["AAPL", "MSFT", "XOM", "JNJ"]
SPX_SYMBOL = "^GSPC"

_TODAY = date.today()
SCAN_TIME = datetime.now(timezone.utc).isoformat()


def _naive_wilder_rsi(closes_by_date: dict[str, float], period: int = 14) -> list[Optional[float]]:
    """Independent Wilder RSI: seed = mean of first ``period`` gains/losses,
    then avg = (prev*(period-1) + x) / period. First value after period+1 closes.
    avg_loss == 0 -> 100, or 50 when both averages are 0."""
    values = [closes_by_date[d] for d in sorted(closes_by_date)]
    n = len(values)
    out: list[Optional[float]] = [None] * n
    if n < period + 1:
        return out

    def rsi(avg_gain: float, avg_loss: float) -> float:
        if avg_loss == 0 and avg_gain == 0:
            return 50.0
        if avg_loss == 0:
            return 100.0
        return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))

    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        change = values[i] - values[i - 1]
        if change > 0:
            gains += change
        else:
            losses -= change
    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = rsi(avg_gain, avg_loss)
    for i in range(period + 1, n):
        change = values[i] - values[i - 1]
        gain = change if change > 0 else 0.0
        loss = -change if change < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = rsi(avg_gain, avg_loss)
    return out


def _member_series() -> dict[str, tuple[list[str], list[Optional[float]]]]:
    return {m: rsi_series(CLOSES[m]) for m in MEMBERS}


def _sessions() -> list[str]:
    return sorted({d for m in MEMBERS for d in CLOSES[m]})


def _fixture_rows() -> list[dict]:
    return aggregate_rsi_oversold(_member_series(), _sessions(), member_count=len(MEMBERS))


def _rising(n: int, start: date = date(2025, 1, 1)) -> dict[str, float]:
    return {(start + timedelta(days=i)).isoformat(): 100.0 + i for i in range(n)}


def _falling(n: int, start: date = date(2025, 1, 1)) -> dict[str, float]:
    return {(start + timedelta(days=i)).isoformat(): 1000.0 - i for i in range(n)}


def _flat(n: int, start: date = date(2025, 1, 1), px: float = 100.0) -> dict[str, float]:
    return {(start + timedelta(days=i)).isoformat(): px for i in range(n)}


class TestConstants:
    def test_threshold_and_rsi_pins(self):
        assert THRESHOLD_PCT == 10.0
        assert OVERSOLD_RSI == 30.0
        assert RSI_PERIOD == 14

    def test_gates(self):
        assert MIN_SESSIONS == 30
        assert MIN_LATEST_COVERAGE == 0.80
        assert MIN_ELIGIBLE_FRACTION == 0.80


class TestWilderRsi:
    def test_latest_matches_naive_recompute_per_member(self):
        for member in MEMBERS + [SPX_SYMBOL]:
            dates, values = rsi_series(CLOSES[member])
            naive = _naive_wilder_rsi(CLOSES[member])
            assert dates == sorted(CLOSES[member])
            assert values[-1] == pytest.approx(naive[-1])
            first = next(i for i, v in enumerate(values) if v is not None)
            assert first == 14

    def test_first_value_appears_after_fifteen_closes(self):
        dates, values = rsi_series(CLOSES["AAPL"])
        assert values[13] is None
        assert values[14] is not None
        assert values[14] == pytest.approx(_naive_wilder_rsi(CLOSES["AAPL"])[14])

    def test_avg_loss_zero_is_rsi_100(self):
        dates, values = rsi_series(_rising(20))
        assert values[-1] == pytest.approx(100.0)

    def test_both_averages_zero_is_rsi_50(self):
        dates, values = rsi_series(_flat(20))
        assert values[-1] == pytest.approx(50.0)

    def test_seed_then_recursion_matches_naive_on_a_mixed_path(self):
        series = _rising(10)
        series.update(_falling(10, start=date(2025, 1, 11)))
        dates, values = rsi_series(series)
        naive = _naive_wilder_rsi(series)
        for got, exp in zip(values, naive):
            if exp is None:
                assert got is None
            else:
                assert got == pytest.approx(exp)


class TestOversoldBoundary:
    def test_rsi_exactly_30_is_not_oversold(self):
        assert is_oversold(30.0) is False
        assert is_oversold(29.999) is True
        assert is_oversold(30.001) is False


class TestStateAndEvents:
    def test_pct_exactly_10_is_normal(self):
        assert classify_state(10.0) == "NORMAL"
        assert classify_state(10.001) == "OVERSOLD CLUSTER"
        assert classify_state(9.999) == "NORMAL"

    def test_cross_up_is_prev_at_or_below_then_strictly_above(self):
        assert detect_cross_up([
            {"pct_below_30": 10.0},
            {"pct_below_30": 10.1},
        ]) is True
        assert detect_cross_up([
            {"pct_below_30": 9.9},
            {"pct_below_30": 10.0},
        ]) is False
        assert detect_cross_up([{"pct_below_30": 12.0}]) is False

    def test_highest_since_is_most_recent_prior_at_or_above_latest(self):
        rows = [
            {"date": "2026-03-10", "pct_below_30": 20.0},
            {"date": "2026-03-11", "pct_below_30": 8.0},
            {"date": "2026-03-12", "pct_below_30": 15.0},
            {"date": "2026-09-28", "pct_below_30": 16.0},
        ]
        assert highest_since(rows) == "2026-03-10"
        assert highest_since(rows[:-1]) == "2026-03-10"
        assert highest_since([{"date": "2026-09-28", "pct_below_30": 5.0}]) is None


class TestAggregate:
    def test_latest_fixture_row_matches_naive_count(self):
        rows = _fixture_rows()
        latest = rows[-1]
        assert latest["date"] == _sessions()[-1]
        naive_over = sum(
            1 for m in MEMBERS
            if (r := _naive_wilder_rsi(CLOSES[m])[-1]) is not None and r < 30
        )
        assert latest["eligible"] == 4
        assert latest["count_below_30"] == naive_over
        assert latest["pct_below_30"] == pytest.approx(100.0 * naive_over / 4)

    def test_rows_start_when_the_rsi_window_fills_for_enough_members(self):
        rows = _fixture_rows()
        assert rows[0]["date"] == _sessions()[14]
        assert len(rows) == len(_sessions()) - 14

    def test_rows_are_ascending_and_pct_consistent(self):
        rows = _fixture_rows()
        assert [r["date"] for r in rows] == sorted(r["date"] for r in rows)
        for row in rows:
            assert row["pct_below_30"] == pytest.approx(
                100.0 * row["count_below_30"] / row["eligible"]
            )

    def test_carry_forward_holds_a_members_last_rsi(self):
        full = dict(CLOSES["AAPL"])
        dates = sorted(full)
        lagger = {d: full[d] for d in dates[:-1]}
        series = {
            "FULL": rsi_series(full),
            "LAG": rsi_series(lagger),
        }
        rows = aggregate_rsi_oversold(series, dates, member_count=2)
        latest = rows[-1]
        assert latest["date"] == dates[-1]
        assert latest["eligible"] == 2

    def test_pct_exactly_10_does_not_emit_cluster_in_the_row(self):
        # 10 members, 1 falling (RSI 0-ish) and 9 rising (RSI 100) -> 10%.
        start = date(2025, 1, 1)
        members = {f"R{i}": rsi_series(_rising(20, start)) for i in range(9)}
        members["F0"] = rsi_series(_falling(20, start))
        sessions = sorted(_rising(20, start))
        rows = aggregate_rsi_oversold(members, sessions, member_count=10)
        latest = rows[-1]
        assert latest["count_below_30"] == 1
        assert latest["eligible"] == 10
        assert latest["pct_below_30"] == pytest.approx(10.0)
        assert classify_state(latest["pct_below_30"]) == "NORMAL"

    def test_insufficient_eligible_members_emits_no_row(self):
        rows = aggregate_rsi_oversold(_member_series(), _sessions(), member_count=6)
        assert rows == []


def _build_payload(rows=None, member_series=None):
    from ma_ratio_scan import attach_spx_series

    rows = _fixture_rows() if rows is None else rows
    member_series = _member_series() if member_series is None else member_series
    return build_output(
        rows=attach_spx_series(rows, CLOSES[SPX_SYMBOL]),
        member_series=member_series,
        member_count=len(MEMBERS),
        scan_time=SCAN_TIME,
        source={"constituents": "cache", "constituents_count": len(MEMBERS),
                "member_close_fetches": {"yahoo": 4, "stored": 0}},
    )


class TestBuildOutput:
    def test_payload_contract_keys(self):
        payload = _build_payload()
        assert {"schema_version", "scan_time", "data_date", "source", "threshold",
                "current", "series", "missing"} <= set(payload)
        assert payload["schema_version"] == 1
        assert payload["missing"] is False
        assert payload["scan_time"] == SCAN_TIME
        assert payload["threshold"] == THRESHOLD_PCT
        assert payload["data_date"] == _sessions()[-1]
        assert {"state", "cross_up", "highest_since"} <= set(payload["current"])

    def test_current_mirrors_the_latest_row_with_spx_close(self):
        current = _build_payload()["current"]
        assert current["date"] == _sessions()[-1]
        assert current["spx_close"] == CLOSES[SPX_SYMBOL][_sessions()[-1]]

    def test_series_rows_carry_the_chart_and_history_fields(self):
        series = _build_payload()["series"]
        assert set(series[-1]) == {
            "date", "pct_below_30", "count_below_30", "eligible", "spx_close",
        }

    def test_too_few_sessions_is_missing(self):
        payload = _build_payload(rows=_fixture_rows()[: MIN_SESSIONS - 1])
        assert payload["missing"] is True
        assert payload["reason"] == "insufficient_history"

    def test_stale_latest_coverage_is_missing(self):
        stale_members = {
            m: rsi_series({d: c for d, c in CLOSES[m].items() if d != _sessions()[-1]})
            for m in MEMBERS
        }
        payload = _build_payload(member_series=stale_members)
        assert payload["missing"] is True
        assert payload["reason"] == "insufficient_coverage"


@pytest.fixture()
def persist_calls(monkeypatch, tmp_path):
    import rsi_oversold_scan as ros

    calls: list[tuple] = []
    monkeypatch.setattr(ros, "RSI_OVERSOLD_JSON", tmp_path / "rsi_oversold.json")
    monkeypatch.setattr(ros.writer, "ensure_no_replica_for_writers", lambda: calls.append(("guard",)))
    monkeypatch.setattr(
        ros.writer,
        "upsert_rsi_oversold_rows",
        lambda rows, recorded_at: calls.append(("rows", len(rows))),
    )
    monkeypatch.setattr(
        ros.writer,
        "upsert_scan_snapshot",
        lambda service, scan_time, payload: calls.append(("snapshot", service)),
    )
    monkeypatch.setattr(
        ros.writer,
        "record_service_health",
        lambda service, state, finished_at=None: calls.append(("health", service, state)),
    )
    return calls


class TestPersistResult:
    def test_writes_in_order_with_heartbeat(self, persist_calls):
        import rsi_oversold_scan as ros

        payload = _build_payload()
        persist_result(payload, payload["series"])
        assert persist_calls == [
            ("guard",),
            ("rows", len(payload["series"])),
            ("snapshot", "rsi-oversold"),
            ("health", "rsi-oversold", "ok"),
        ]
        fallback = json.loads(ros.RSI_OVERSOLD_JSON.read_text())
        assert fallback["data_date"] == _sessions()[-1]

    def test_service_name_is_kebab_case(self):
        assert SERVICE == "rsi-oversold"


class TestHeartbeatTimeout:
    def _stub_sweep(self, monkeypatch, tmp_path):
        import rsi_oversold_scan as ros

        monkeypatch.setattr(ros, "RSI_OVERSOLD_JSON", tmp_path / "rsi_oversold.json")
        monkeypatch.setattr(
            ros, "resolve_spx_constituents", lambda: (list(MEMBERS), "cache")
        )
        monkeypatch.setattr(
            ros,
            "ensure_member_history",
            lambda members, backfill, no_db, sweep_deadline: (
                {m: CLOSES[m] for m in members if m in CLOSES},
                {"yahoo": 0, "stored": len(members)},
            ),
        )
        monkeypatch.setattr(
            ros,
            "fetch_spx_overlay_closes",
            lambda yahoo, **kwargs: (dict(yahoo), "yahoo"),
        )
        monkeypatch.setattr(ros.writer, "ensure_no_replica_for_writers", lambda: None)
        monkeypatch.setattr(
            ros.writer, "upsert_scan_snapshot", lambda service, scan_time, payload: None
        )
        return ros

    def test_main_exits_zero_when_the_ok_heartbeat_times_out(
        self, monkeypatch, tmp_path,
    ):
        from db.hrana_http import HranaHttpError

        ros = self._stub_sweep(monkeypatch, tmp_path)
        written: list[int] = []
        monkeypatch.setattr(
            ros.writer,
            "upsert_rsi_oversold_rows",
            lambda rows, recorded_at=None: written.append(len(rows)),
        )

        def boom(*_a, **_k):
            raise HranaHttpError("TimeoutError: The read operation timed out")

        monkeypatch.setattr(ros.writer, "record_service_health", boom)

        assert ros.main([]) == 0
        assert written == [len(_fixture_rows())]
        fallback = json.loads((tmp_path / "rsi_oversold.json").read_text())
        assert fallback["data_date"] == _sessions()[-1]
        assert fallback["missing"] is False

    def test_row_upsert_failure_still_fails_the_oneshot(self, monkeypatch, tmp_path):
        ros = self._stub_sweep(monkeypatch, tmp_path)
        health: list[tuple] = []

        def boom(*_a, **_k):
            raise TimeoutError("The read operation timed out")

        monkeypatch.setattr(ros.writer, "upsert_rsi_oversold_rows", boom)
        monkeypatch.setattr(
            ros.writer,
            "record_service_health",
            lambda service, state, **kwargs: health.append((service, state)),
        )

        assert ros.main([]) == 1
        assert not (tmp_path / "rsi_oversold.json").exists()
        assert health == [("rsi-oversold", "error")]


class TestRun:
    def test_run_aggregates_the_sweep_and_persists(self, persist_calls, monkeypatch):
        import rsi_oversold_scan as ros

        monkeypatch.setattr(
            ros, "resolve_spx_constituents", lambda: (list(MEMBERS), "cache")
        )
        monkeypatch.setattr(
            ros,
            "ensure_member_history",
            lambda members, backfill, no_db, sweep_deadline: (
                {m: CLOSES[m] for m in members if m in CLOSES},
                {"yahoo": len(members), "stored": 0},
            ),
        )
        payload = ros.run()
        assert payload["missing"] is False
        assert payload["data_date"] == _sessions()[-1]
        n = len(payload["series"])
        assert ("rows", n) in persist_calls
        assert persist_calls.index(("rows", n)) < persist_calls.index(("snapshot", "rsi-oversold"))
        assert ("health", "rsi-oversold", "ok") in persist_calls

    def test_run_with_a_gated_payload_persists_nothing(self, persist_calls, monkeypatch):
        import rsi_oversold_scan as ros

        last = _sessions()[-1]
        stale = {
            m: {d: c for d, c in CLOSES[m].items() if d != last} for m in MEMBERS[:3]
        }
        stale[MEMBERS[3]] = dict(CLOSES[MEMBERS[3]])
        monkeypatch.setattr(
            ros, "resolve_spx_constituents", lambda: (list(MEMBERS), "cache")
        )
        monkeypatch.setattr(
            ros,
            "ensure_member_history",
            lambda members, backfill, no_db, sweep_deadline: (
                {**stale, SPX_SYMBOL: CLOSES[SPX_SYMBOL]},
                {"yahoo": len(members), "stored": 0},
            ),
        )
        payload = ros.run()
        assert payload["missing"] is True
        assert persist_calls == []


class TestStorage:
    @pytest.fixture()
    def db(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT);"
        )
        conn.executescript(MIGRATION.read_text())
        yield conn
        conn.close()

    def test_migration_registers_version_92(self, db):
        assert [r[0] for r in db.execute("SELECT version FROM schema_migrations")] == [92]

    def test_migration_is_rerunnable(self, db):
        db.executescript(MIGRATION.read_text())
        assert [r[0] for r in db.execute("SELECT version FROM schema_migrations")] == [92]

    def test_schema_columns_and_date_primary_key(self, db):
        info = list(db.execute("PRAGMA table_info(rsi_oversold_history)"))
        assert [r[1] for r in info] == [
            "date", "pct_below_30", "count_below_30", "eligible", "spx_close", "recorded_at",
        ]
        pk_by_column = {r[1]: r[5] for r in info}
        assert pk_by_column["date"] == 1
        nullable = {r[1]: r[3] == 0 for r in info}
        assert nullable["spx_close"] is True
        assert nullable["pct_below_30"] is False

    def test_date_desc_index_exists(self, db):
        names = {r[1] for r in db.execute("PRAGMA index_list(rsi_oversold_history)")}
        assert "idx_rsi_oversold_history_date_desc" in names

    def test_upsert_is_idempotent_per_date(self, db, monkeypatch):
        from db import writer

        monkeypatch.setattr(writer, "get_db", lambda: db)
        d = (_TODAY - timedelta(days=1)).isoformat()
        row = {"date": d, "pct_below_30": 12.4, "count_below_30": 62,
               "eligible": 500, "spx_close": 6630.0}
        writer.upsert_rsi_oversold_rows([row], recorded_at="r1")
        writer.upsert_rsi_oversold_rows(
            [{**row, "pct_below_30": 8.0, "count_below_30": 40,
              "eligible": 500, "spx_close": None}],
            recorded_at="r2",
        )
        rows = list(
            db.execute(
                "SELECT date, pct_below_30, count_below_30, eligible, spx_close, recorded_at"
                " FROM rsi_oversold_history"
            )
        )
        assert rows == [(d, 8.0, 40, 500, None, "r2")]

    def test_writer_arity(self):
        from db import writer

        parameters = list(inspect.signature(writer.upsert_rsi_oversold_rows).parameters)
        assert parameters == ["rows", "recorded_at"]


class TestSweepBudget:
    def test_sweep_budget_fits_inside_unit_start_timeout(self):
        from bpi_scan import FETCH_TIMEOUT_S

        service = (
            Path(__file__).resolve().parents[2]
            / "cloud" / "services" / "radon-rsi-oversold.service"
        )
        timeout_line = next(
            line for line in service.read_text().splitlines()
            if line.startswith("TimeoutStartSec=")
        )
        unit_timeout = int(timeout_line.split("=", 1)[1])
        assert SWEEP_BUDGET_S + FETCH_TIMEOUT_S <= unit_timeout
