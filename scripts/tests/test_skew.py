"""SKEW indicator — SPX 1M 25-delta put/call IV ratio tests.

Ground truth is the checked-in real UW greeks response:
  fixtures/skew_uw_sample.json — SPX chain, expiry 2026-09-18, as-of 2026-08-05,
  571 strikes. Expected values below were derived by interpolating the fixture
  directly (2026-08-05), not computed by hand. Spec: docs/indicators/skew.md.
"""
import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from fetch_skew import (
    compute_change_series,
    compute_stats,
    interpolate_iv_at_delta,
    nearest_monthly_expiry,
    parse_greek_rows,
    third_friday,
)

FIXTURES = Path(__file__).parent / "fixtures"
UW_PAYLOAD = json.loads((FIXTURES / "skew_uw_sample.json").read_text())
MIGRATION = Path(__file__).parents[1] / "db" / "migrations" / "0034_skew.sql"

CALL_25D = 0.12340843535214445
PUT_25D = 0.15956701505157658
RATIO = 1.2929992556526146


# ── Chain parsing + delta interpolation ───────────────────────────


class TestInterpolation:
    def setup_method(self):
        self.rows = parse_greek_rows(UW_PAYLOAD)

    def test_fixture_parses_full_chain(self):
        assert len(self.rows) == 571
        assert self.rows[0]["expiry"] == "2026-09-18"
        assert self.rows[0]["date"] == "2026-08-05"

    def test_call_25d_iv_matches_fixture_derivation(self):
        assert interpolate_iv_at_delta(self.rows, "call", 0.25) == pytest.approx(
            CALL_25D, abs=1e-12
        )

    def test_put_25d_iv_matches_fixture_derivation(self):
        assert interpolate_iv_at_delta(self.rows, "put", -0.25) == pytest.approx(
            PUT_25D, abs=1e-12
        )

    def test_ratio_matches_fixture_derivation(self):
        put = interpolate_iv_at_delta(self.rows, "put", -0.25)
        call = interpolate_iv_at_delta(self.rows, "call", 0.25)
        assert put / call == pytest.approx(RATIO, abs=1e-12)

    def test_exact_delta_hit_short_circuits(self):
        rows = [
            {"call_delta": 0.25, "call_volatility": 0.2},
            {"call_delta": 0.30, "call_volatility": 0.25},
        ]
        assert interpolate_iv_at_delta(rows, "call", 0.25) == pytest.approx(0.2)

    def test_linear_interpolation_between_brackets(self):
        rows = [
            {"call_delta": 0.20, "call_volatility": 0.10},
            {"call_delta": 0.30, "call_volatility": 0.20},
        ]
        assert interpolate_iv_at_delta(rows, "call", 0.25) == pytest.approx(0.15)

    def test_nonpositive_and_missing_ivs_are_dropped(self):
        rows = [
            {"call_delta": 0.20, "call_volatility": 0.10},
            {"call_delta": 0.24, "call_volatility": 0.0},   # zero IV: drop
            {"call_delta": 0.26, "call_volatility": None},  # missing: drop
            {"call_delta": 0.30, "call_volatility": 0.20},
        ]
        assert interpolate_iv_at_delta(rows, "call", 0.25) == pytest.approx(0.15)

    def test_unbracketed_target_returns_none(self):
        rows = [{"call_delta": 0.40, "call_volatility": 0.2}]
        assert interpolate_iv_at_delta(rows, "call", 0.25) is None


# ── Monthly expiry selection ──────────────────────────────────────


class TestExpirySelection:
    def test_third_fridays(self):
        assert third_friday(2026, 8) == date(2026, 8, 21)
        assert third_friday(2026, 9) == date(2026, 9, 18)

    def test_tie_between_candidates_breaks_to_later_expiry(self):
        # 2026-08-05: Aug 21 = 16 DTE, Sep 18 = 44 DTE — both |14| from 30.
        assert nearest_monthly_expiry(date(2026, 8, 5)) == date(2026, 9, 18)

    def test_clear_nearest_wins(self):
        # 2026-08-24: Sep 18 = 25 DTE beats Oct 16 = 53 and (expired) Aug 21.
        assert nearest_monthly_expiry(date(2026, 8, 24)) == date(2026, 9, 18)


# ── Change series + stats ─────────────────────────────────────────


def _row(d, ratio):
    return {
        "date": d, "expiry": "2026-09-18", "dte": 30,
        "put_iv": 0.15, "call_iv": 0.12, "ratio": ratio,
    }


class TestChangeSeries:
    def test_change_is_first_difference_with_null_first_row(self):
        series = compute_change_series([_row("2026-08-01", 1.30), _row("2026-08-04", 1.25)])
        assert series[0]["change"] is None
        assert series[1]["change"] == pytest.approx(-0.05)

    def test_rows_are_sorted_ascending_by_date(self):
        series = compute_change_series([_row("2026-08-04", 1.25), _row("2026-08-01", 1.30)])
        assert [r["date"] for r in series] == ["2026-08-01", "2026-08-04"]


class TestComputeStats:
    def test_stats_over_non_null_changes(self):
        series = compute_change_series(
            [_row("d1", 1.30), _row("d2", 1.34), _row("d3", 1.30), _row("d4", 1.30)]
        )
        stats = compute_stats(series)
        assert stats["high"] == pytest.approx(0.04)
        assert stats["low"] == pytest.approx(-0.04)
        assert stats["avg"] == pytest.approx(0.0)
        # population stddev of [0.04, -0.04, 0.0]
        assert stats["stddev"] == pytest.approx(0.032659863237, abs=1e-9)

    def test_no_changes_yields_none(self):
        assert compute_stats(compute_change_series([_row("d1", 1.30)])) is None


# ── run(): gap-filling incremental + heartbeat fast path ──────────


class _StubClient:
    def __init__(self, chains):
        self._chains = chains  # {(expiry, date): payload}
        self.calls = []

    def fetch_greeks(self, expiry, as_of):
        self.calls.append((expiry, as_of))
        return self._chains.get((expiry, as_of), {"data": []})


class TestRunIncremental:
    @pytest.fixture(autouse=True)
    def _isolate_caches(self, tmp_path, monkeypatch):
        import fetch_skew as mod

        monkeypatch.setattr(mod, "SKEW_JSON", tmp_path / "skew.json")
        self.db_writes = []
        monkeypatch.setattr(
            mod, "_write_db_cache",
            lambda payload, scan_time, rows_changed: self.db_writes.append(rows_changed),
        )
        self.mod = mod

    def _cached(self, series):
        payload = {
            "scan_time": "old", "source": "unusual_whales", "count": len(series),
            "current": series[-1] if series else None, "stats": {}, "series": series,
        }
        self.mod.SKEW_JSON.write_text(json.dumps(payload))
        return payload

    def test_no_missing_sessions_heartbeats_without_fetch_or_row_writes(self):
        series = compute_change_series([_row("2026-08-04", 1.30), _row("2026-08-05", 1.29)])
        self._cached(series)
        client = _StubClient({})
        # Wed 2026-08-05 23:00 UTC: last completed session IS 2026-08-05.
        payload = self.mod.run(
            client=client, now=datetime(2026, 8, 5, 23, 0, tzinfo=timezone.utc)
        )
        assert client.calls == []
        assert payload["scan_time"] != "old"
        assert self.db_writes == [False]

    def test_missing_session_is_fetched_computed_and_upserted(self):
        series = compute_change_series([_row("2026-08-03", 1.31), _row("2026-08-04", 1.30)])
        self._cached(series)
        client = _StubClient({("2026-09-18", "2026-08-05"): UW_PAYLOAD})
        payload = self.mod.run(
            client=client, now=datetime(2026, 8, 5, 23, 0, tzinfo=timezone.utc)
        )
        assert ("2026-09-18", "2026-08-05") in client.calls
        current = payload["current"]
        assert current["date"] == "2026-08-05"
        assert current["ratio"] == pytest.approx(RATIO, abs=1e-9)
        assert current["change"] == pytest.approx(RATIO - 1.30, abs=1e-9)
        assert current["expiry"] == "2026-09-18"
        assert current["dte"] == 44
        assert self.db_writes == [True]


# ── Migration + upsert (sqlite3 stand-in for libsql) ──────────────


class TestSkewStorage:
    def _db(self):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)")
        db.executescript(MIGRATION.read_text())
        return db

    def test_migration_applies_and_registers_version(self):
        db = self._db()
        assert db.execute("SELECT version FROM schema_migrations").fetchone()[0] == 34
        cols = {r[1] for r in db.execute("PRAGMA table_info(skew_history)")}
        assert {"date", "expiry", "dte", "put_iv", "call_iv", "ratio", "change",
                "recorded_at"} <= cols

    def test_upsert_is_idempotent_per_date(self):
        from db import writer

        db = self._db()
        args1 = ("2026-08-05", "2026-09-18", 44, 0.159567, 0.123408, 1.292999, -0.007, "t1")
        args2 = ("2026-08-05", "2026-09-18", 44, 0.159567, 0.123408, 1.293000, -0.007, "t2")
        db.execute(writer.SKEW_UPSERT_SQL, args1)
        db.execute(writer.SKEW_UPSERT_SQL, args2)
        rows = db.execute("SELECT date, ratio FROM skew_history").fetchall()
        assert rows == [("2026-08-05", 1.293)]
