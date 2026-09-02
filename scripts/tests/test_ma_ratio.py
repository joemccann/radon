"""MA RATIO — SPX percent of members above their 50-day SMA over percent above
their 200-day SMA. Red tests against docs/indicators/ma-ratio.md.

Fixture facts are derived by INSPECTING the checked-in fixture directly:
  - fixtures/ma_ratio_member_closes_sample.json — real Yahoo v8 daily closes
    (range=1y, 252 sessions per symbol, captured 2026-09-02) for four S&P 500
    members (AAPL, MSFT, XOM, JNJ) plus the ^GSPC overlay symbol. Every SMA
    expectation below is RECOMPUTED from the fixture's own closes with a naive
    independent implementation, never mental arithmetic.
"""

from __future__ import annotations

import inspect
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pytest

from ma_ratio_scan import (
    MIN_ELIGIBLE_FRACTION,
    MIN_LATEST_COVERAGE,
    MIN_SESSIONS,
    SERVICE,
    SWEEP_BUDGET_S,
    ZONE_HIGH,
    ZONE_LOW,
    aggregate_ma_ratio,
    attach_spx_series,
    build_output,
    compute_ratio,
    persist_result,
    sma_flags_series,
)

FIXTURES = Path(__file__).parent / "fixtures"
MIGRATION = Path(__file__).parents[1] / "db" / "migrations" / "0068_ma_ratio.sql"

FIXTURE = json.loads((FIXTURES / "ma_ratio_member_closes_sample.json").read_text())
CLOSES: dict[str, dict[str, float]] = FIXTURE["closes"]
MEMBERS = ["AAPL", "MSFT", "XOM", "JNJ"]
SPX_SYMBOL = "^GSPC"

# Window-relative dates only for freshness-adjacent assertions.
_TODAY = date.today()
SCAN_TIME = datetime.now(timezone.utc).isoformat()


def _naive_sma_above(closes_by_date: dict[str, float], window: int) -> Optional[bool]:
    """Independent recomputation: is the LAST close strictly above its SMA?"""
    values = [closes_by_date[d] for d in sorted(closes_by_date)]
    if len(values) < window:
        return None
    sma = sum(values[-window:]) / window
    return values[-1] > sma


def _member_flags() -> dict[str, tuple[list[str], list[Optional[bool]], list[Optional[bool]]]]:
    return {m: sma_flags_series(CLOSES[m]) for m in MEMBERS}


def _sessions() -> list[str]:
    return sorted({d for m in MEMBERS for d in CLOSES[m]})


def _fixture_rows() -> list[dict]:
    return aggregate_ma_ratio(_member_flags(), _sessions(), member_count=len(MEMBERS))


class TestConstants:
    def test_zone_is_the_confirmed_quarter_to_half_band(self):
        assert ZONE_LOW == 0.25
        assert ZONE_HIGH == 0.5

    def test_gates(self):
        assert MIN_SESSIONS == 30
        assert MIN_LATEST_COVERAGE == 0.80
        assert MIN_ELIGIBLE_FRACTION == 0.80


class TestSmaFlagsSeries:
    def test_latest_session_flags_match_a_naive_recompute_per_member(self):
        for member in MEMBERS + [SPX_SYMBOL]:
            dates, above50, above200 = sma_flags_series(CLOSES[member])
            assert dates == sorted(CLOSES[member])
            assert above50[-1] == _naive_sma_above(CLOSES[member], 50)
            assert above200[-1] == _naive_sma_above(CLOSES[member], 200)

    def test_fixture_capture_spot_values(self):
        # Pinned from the capture run (2026-09-02): every fixture symbol closed
        # 2026-09-01 above BOTH its 50d and 200d SMA (e.g. AAPL 325.13 vs
        # SMA50 312.9392 / SMA200 283.0684). Recomputed here, not asserted blind.
        for member in MEMBERS:
            _dates, above50, above200 = sma_flags_series(CLOSES[member])
            assert above50[-1] is True
            assert above200[-1] is True

    def test_flags_are_none_until_the_window_fills(self):
        _dates, above50, above200 = sma_flags_series(CLOSES["AAPL"])
        assert above50[48] is None
        assert above50[49] is not None
        assert above200[198] is None
        assert above200[199] is not None

    def test_close_exactly_on_its_sma_is_not_above(self):
        # Strict inequality pinned by the spec: a constant series has
        # close == SMA on every session, which must never count as above.
        flat = {f"2026-01-{i:02d}": 100.0 for i in range(1, 29)}
        flat.update({f"2026-02-{i:02d}": 100.0 for i in range(1, 29)})
        _dates, above50, _above200 = sma_flags_series(flat)
        assert above50[-1] is False


class TestComputeRatio:
    def test_plain_division(self):
        assert compute_ratio(46.5, 64.6) == pytest.approx(46.5 / 64.6)

    def test_zero_denominator_guard_returns_none(self):
        assert compute_ratio(0.0, 0.0) is None
        assert compute_ratio(12.0, 0.0) is None


class TestAggregate:
    def test_latest_fixture_row_is_a_full_sweep_above_both(self):
        rows = _fixture_rows()
        latest = rows[-1]
        assert latest["date"] == _sessions()[-1]
        assert latest["eligible_50"] == 4
        assert latest["eligible_200"] == 4
        assert latest["count_above_50"] == 4
        assert latest["count_above_200"] == 4
        assert latest["pct_above_50"] == pytest.approx(100.0)
        assert latest["pct_above_200"] == pytest.approx(100.0)
        assert latest["ratio"] == pytest.approx(1.0)

    def test_rows_start_when_the_200_window_fills_for_enough_members(self):
        # 252 fixture sessions per member; the 200-close window fills on
        # session index 199, so 252 - 200 + 1 = 53 rows are emitted.
        rows = _fixture_rows()
        assert len(rows) == 53
        assert rows[0]["date"] == _sessions()[199]

    def test_rows_are_ascending_and_ratio_consistent(self):
        rows = _fixture_rows()
        assert [r["date"] for r in rows] == sorted(r["date"] for r in rows)
        for row in rows:
            expected = compute_ratio(row["pct_above_50"], row["pct_above_200"])
            if expected is None:
                assert row["ratio"] is None
            else:
                assert row["ratio"] == pytest.approx(expected)

    def test_carry_forward_holds_a_members_last_state(self):
        # A member missing the latest session still counts at its previous
        # state (bpi aggregate convention). Reuse real fixture closes so both
        # 50 and 200 windows genuinely fill.
        full = dict(CLOSES["AAPL"])
        dates = sorted(full)
        lagger = {d: full[d] for d in dates[:-1]}  # missing the last session
        flags = {
            "FULL": sma_flags_series(full),
            "LAG": sma_flags_series(lagger),
        }
        rows = aggregate_ma_ratio(flags, dates, member_count=2)
        latest = rows[-1]
        assert latest["date"] == dates[-1]
        assert latest["eligible_50"] == 2  # LAG carried forward, still counted
        assert latest["eligible_200"] == 2

    def test_zero_denominator_session_stores_null_ratio(self):
        # 210 strictly falling sessions: every close is below both SMAs once
        # the windows fill, so pct_above_200 == 0 -> ratio None.
        start = date(2025, 1, 1)
        falling = {
            (start + timedelta(days=i)).isoformat(): 1000.0 - i for i in range(210)
        }
        flags = {"F1": sma_flags_series(falling), "F2": sma_flags_series(falling)}
        rows = aggregate_ma_ratio(flags, sorted(falling), member_count=2)
        assert rows, "expected at least one aggregated session"
        latest = rows[-1]
        assert latest["pct_above_200"] == pytest.approx(0.0)
        assert latest["ratio"] is None

    def test_insufficient_eligible_members_emits_no_row(self):
        # With member_count=5 and only 4 eligible members, 4 < 0.8 * 5 is
        # false at exactly 4.0 -- the floor is >=, so use 6 to force a gap.
        rows = aggregate_ma_ratio(_member_flags(), _sessions(), member_count=6)
        assert rows == []


class TestAttachSpxSeries:
    def test_overlay_close_joins_by_date_and_missing_dates_are_null(self):
        rows = [
            {"date": d, "pct_above_50": 100.0, "pct_above_200": 100.0, "ratio": 1.0}
            for d in _sessions()[-3:]
        ]
        spx = {d: c for d, c in CLOSES[SPX_SYMBOL].items()}
        out = attach_spx_series(rows, spx)
        assert out[-1]["spx_close"] == CLOSES[SPX_SYMBOL][_sessions()[-1]]
        out_missing = attach_spx_series(rows, {})
        assert all(r["spx_close"] is None for r in out_missing)


def _build_payload(rows=None, member_series=None):
    rows = _fixture_rows() if rows is None else rows
    member_series = _member_flags() if member_series is None else member_series
    return build_output(
        rows=attach_spx_series(rows, CLOSES[SPX_SYMBOL]),
        member_flags=member_series,
        member_count=len(MEMBERS),
        scan_time=SCAN_TIME,
        source={"constituents": "cache", "constituents_count": len(MEMBERS),
                "member_close_fetches": {"yahoo": 4, "stored": 0}},
    )


class TestBuildOutput:
    def test_payload_contract_keys(self):
        payload = _build_payload()
        assert {"schema_version", "scan_time", "data_date", "source", "zone",
                "current", "series", "missing"} <= set(payload)
        assert payload["schema_version"] == 1
        assert payload["missing"] is False
        assert payload["scan_time"] == SCAN_TIME
        assert payload["zone"] == {"low": ZONE_LOW, "high": ZONE_HIGH}
        assert payload["data_date"] == _sessions()[-1]

    def test_current_mirrors_the_latest_row_with_spx_close(self):
        current = _build_payload()["current"]
        assert current["date"] == _sessions()[-1]
        assert current["ratio"] == pytest.approx(1.0)
        assert current["spx_close"] == CLOSES[SPX_SYMBOL][_sessions()[-1]]

    def test_series_rows_carry_only_the_chart_fields(self):
        series = _build_payload()["series"]
        assert len(series) == 53
        assert set(series[-1]) == {"date", "pct_above_50", "pct_above_200", "ratio", "spx_close"}

    def test_too_few_sessions_is_missing(self):
        payload = _build_payload(rows=_fixture_rows()[: MIN_SESSIONS - 1])
        assert payload["missing"] is True
        assert payload["reason"] == "insufficient_history"

    def test_stale_latest_coverage_is_missing(self):
        # Fewer than 80% of members reporting the latest aggregated session
        # fresh means a truncated sweep (bpi R-224): refuse to publish.
        stale_members = {
            m: sma_flags_series({d: c for d, c in CLOSES[m].items() if d != _sessions()[-1]})
            for m in MEMBERS
        }
        payload = _build_payload(member_series=stale_members)
        assert payload["missing"] is True
        assert payload["reason"] == "insufficient_coverage"


@pytest.fixture()
def persist_calls(monkeypatch, tmp_path):
    import ma_ratio_scan as mrs

    calls: list[tuple] = []
    monkeypatch.setattr(mrs, "MA_RATIO_JSON", tmp_path / "ma_ratio.json")
    monkeypatch.setattr(mrs.writer, "ensure_no_replica_for_writers", lambda: calls.append(("guard",)))
    monkeypatch.setattr(
        mrs.writer,
        "upsert_ma_ratio_rows",
        lambda rows, recorded_at: calls.append(("rows", len(rows))),
    )
    monkeypatch.setattr(
        mrs.writer,
        "upsert_scan_snapshot",
        lambda service, scan_time, payload: calls.append(("snapshot", service)),
    )
    monkeypatch.setattr(
        mrs.writer,
        "record_service_health",
        lambda service, state, finished_at=None: calls.append(("health", service, state)),
    )
    return calls


class TestPersistResult:
    def test_writes_in_order_with_heartbeat(self, persist_calls):
        import ma_ratio_scan as mrs

        payload = _build_payload()
        persist_result(payload, payload["series"])
        assert persist_calls == [
            ("guard",),
            ("rows", 53),
            ("snapshot", "ma-ratio"),
            ("health", "ma-ratio", "ok"),
        ]
        fallback = json.loads(mrs.MA_RATIO_JSON.read_text())
        assert fallback["data_date"] == _sessions()[-1]

    def test_service_name_is_kebab_case(self):
        assert SERVICE == "ma-ratio"


class TestRun:
    def test_run_aggregates_the_sweep_and_persists(self, persist_calls, monkeypatch):
        import ma_ratio_scan as mrs

        monkeypatch.setattr(
            mrs, "resolve_spx_constituents", lambda: (list(MEMBERS), "cache")
        )
        monkeypatch.setattr(
            mrs,
            "ensure_member_history",
            lambda members, backfill, no_db, sweep_deadline: (
                {m: CLOSES[m] for m in members if m in CLOSES},
                {"yahoo": len(members), "stored": 0},
            ),
        )
        payload = mrs.run()
        assert payload["missing"] is False
        assert payload["data_date"] == _sessions()[-1]
        assert ("rows", 53) in persist_calls
        assert persist_calls.index(("rows", 53)) < persist_calls.index(("snapshot", "ma-ratio"))
        assert ("health", "ma-ratio", "ok") in persist_calls

    def test_run_with_a_gated_payload_persists_nothing(self, persist_calls, monkeypatch):
        import ma_ratio_scan as mrs

        # A PARTIALLY truncated sweep (three of four members missing the
        # latest session) fails the latest-coverage gate (bpi R-224); nothing
        # may be cached or persisted.
        last = _sessions()[-1]
        stale = {
            m: {d: c for d, c in CLOSES[m].items() if d != last} for m in MEMBERS[:3]
        }
        stale[MEMBERS[3]] = dict(CLOSES[MEMBERS[3]])
        monkeypatch.setattr(
            mrs, "resolve_spx_constituents", lambda: (list(MEMBERS), "cache")
        )
        monkeypatch.setattr(
            mrs,
            "ensure_member_history",
            lambda members, backfill, no_db, sweep_deadline: (
                {**stale, SPX_SYMBOL: CLOSES[SPX_SYMBOL]},
                {"yahoo": len(members), "stored": 0},
            ),
        )
        payload = mrs.run()
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

    def test_migration_registers_version_68(self, db):
        assert [r[0] for r in db.execute("SELECT version FROM schema_migrations")] == [68]

    def test_migration_is_rerunnable(self, db):
        db.executescript(MIGRATION.read_text())
        assert [r[0] for r in db.execute("SELECT version FROM schema_migrations")] == [68]

    def test_schema_columns_and_date_primary_key(self, db):
        info = list(db.execute("PRAGMA table_info(ma_ratio_history)"))
        assert [r[1] for r in info] == [
            "date", "pct_above_50", "pct_above_200", "ratio", "spx_close", "recorded_at",
        ]
        pk_by_column = {r[1]: r[5] for r in info}
        assert pk_by_column["date"] == 1
        nullable = {r[1]: r[3] == 0 for r in info}  # notnull flag == 0 -> nullable
        assert nullable["ratio"] is True
        assert nullable["spx_close"] is True

    def test_date_desc_index_exists(self, db):
        names = {r[1] for r in db.execute("PRAGMA index_list(ma_ratio_history)")}
        assert "idx_ma_ratio_history_date_desc" in names

    def test_upsert_is_idempotent_per_date_and_roundtrips_null_ratio(self, db, monkeypatch):
        from db import writer

        monkeypatch.setattr(writer, "get_db", lambda: db)
        d = (_TODAY - timedelta(days=1)).isoformat()
        row = {"date": d, "pct_above_50": 46.5, "pct_above_200": 64.6,
               "ratio": 46.5 / 64.6, "spx_close": 7631.47}
        writer.upsert_ma_ratio_rows([row], recorded_at="r1")
        writer.upsert_ma_ratio_rows(
            [{**row, "pct_above_50": 0.0, "pct_above_200": 0.0, "ratio": None,
              "spx_close": None}],
            recorded_at="r2",
        )
        rows = list(
            db.execute(
                "SELECT date, pct_above_50, pct_above_200, ratio, spx_close, recorded_at"
                " FROM ma_ratio_history"
            )
        )
        assert rows == [(d, 0.0, 0.0, None, None, "r2")]

    def test_writer_arity(self):
        from db import writer

        parameters = list(inspect.signature(writer.upsert_ma_ratio_rows).parameters)
        assert parameters == ["rows", "recorded_at"]


class TestSweepBudget:
    def test_sweep_budget_fits_inside_unit_start_timeout(self):
        # SPX-only sweep: budget + one in-flight fetch must nest inside the
        # unit's TimeoutStartSec (divyield precedent; do not cargo-cult bpi's
        # three-index 6900s budget).
        from bpi_scan import FETCH_TIMEOUT_S

        service = (
            Path(__file__).resolve().parents[2]
            / "cloud" / "services" / "radon-ma-ratio.service"
        )
        timeout_line = next(
            line for line in service.read_text().splitlines()
            if line.startswith("TimeoutStartSec=")
        )
        unit_timeout = int(timeout_line.split("=", 1)[1])
        assert SWEEP_BUDGET_S + FETCH_TIMEOUT_S <= unit_timeout
