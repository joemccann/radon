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
    bracketing_monthly_expiries,
    compute_change_series,
    compute_stats,
    constant_maturity_leg,
    interpolate_iv_at_delta,
    parse_greek_rows,
    third_friday,
)

FIXTURES = Path(__file__).parent / "fixtures"
UW_PAYLOAD = json.loads((FIXTURES / "skew_uw_sample.json").read_text())
UW_NEAR_PAYLOAD = json.loads((FIXTURES / "skew_uw_sample_near.json").read_text())
MIGRATION = Path(__file__).parents[1] / "db" / "migrations" / "0034_skew.sql"

# Far monthly (2026-09-18, 44 DTE as of 2026-08-05)
CALL_25D = 0.12340843535214445
PUT_25D = 0.15956701505157658
RATIO = 1.2929992556526146
# Near monthly (2026-08-21, 16 DTE as of 2026-08-05)
NEAR_CALL_25D = 0.11478760890347106
NEAR_PUT_25D = 0.13628591095888667
# Constant-maturity 30d: w_far = (30-16)/(44-16) = 0.5
CM_CALL_25D = 0.11909802212780776
CM_PUT_25D = 0.14792646300523163
CM_RATIO = 1.242056420101479


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

    def test_bracketing_pair_straddles_the_30d_target(self):
        # 2026-08-05: Aug 21 = 16 DTE (below 30), Sep 18 = 44 DTE (above).
        assert bracketing_monthly_expiries(date(2026, 8, 5)) == (
            date(2026, 8, 21), date(2026, 9, 18),
        )

    def test_after_the_near_roll_the_next_pair_brackets(self):
        # 2026-08-24: Sep 18 = 25 DTE (below 30), Oct 16 = 53 DTE (above).
        assert bracketing_monthly_expiries(date(2026, 8, 24)) == (
            date(2026, 9, 18), date(2026, 10, 16),
        )


class TestConstantMaturity:
    def test_interpolates_linearly_in_dte(self):
        assert constant_maturity_leg(0.10, 16, 0.20, 44) == pytest.approx(0.15)

    def test_fixture_cm_legs_and_ratio(self):
        cm_call = constant_maturity_leg(NEAR_CALL_25D, 16, CALL_25D, 44)
        cm_put = constant_maturity_leg(NEAR_PUT_25D, 16, PUT_25D, 44)
        assert cm_call == pytest.approx(CM_CALL_25D, abs=1e-12)
        assert cm_put == pytest.approx(CM_PUT_25D, abs=1e-12)
        assert cm_put / cm_call == pytest.approx(CM_RATIO, abs=1e-12)

    def test_target_outside_the_bracket_clamps_to_the_edge(self):
        # Both expiries beyond 30d: clamp to the near leg, never extrapolate.
        assert constant_maturity_leg(0.10, 35, 0.20, 63) == pytest.approx(0.10)
        # Both under 30d: clamp to the far leg.
        assert constant_maturity_leg(0.10, 5, 0.20, 20) == pytest.approx(0.20)

    def test_degenerate_equal_dtes_returns_the_near_leg(self):
        assert constant_maturity_leg(0.12, 30, 0.99, 30) == pytest.approx(0.12)


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
        monkeypatch.setattr(mod, "_read_history_rows", lambda: [], raising=False)
        self.monkeypatch = monkeypatch
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

    def test_open_market_fetches_and_publishes_provisional_current_session(self):
        series = compute_change_series(
            [
                _row("2026-08-01", 1.28),
                _row("2026-08-03", 1.31),
                _row("2026-08-04", 1.30),
            ]
        )
        self._cached(series)
        client = _StubClient({
            ("2026-08-21", "2026-08-05"): UW_NEAR_PAYLOAD,
            ("2026-09-18", "2026-08-05"): UW_PAYLOAD,
        })

        # 15:00 UTC = 11:00 ET during EDT, inside regular trading hours.
        payload = self.mod.run(
            client=client, now=datetime(2026, 8, 5, 15, 0, tzinfo=timezone.utc)
        )

        assert ("2026-08-21", "2026-08-05") in client.calls
        assert ("2026-09-18", "2026-08-05") in client.calls
        assert payload["market_status"] == "open"
        assert payload["current"]["date"] == "2026-08-05"
        assert payload["current"]["is_intraday"] is True
        assert payload["current"]["as_of"] == "2026-08-05T15:00:00Z"
        assert payload["current"]["ratio"] == pytest.approx(CM_RATIO, abs=1e-9)
        assert payload["current"]["change"] == pytest.approx(CM_RATIO - 1.30, abs=1e-9)
        assert payload["series"][-1] == payload["current"]
        assert payload["count"] == 4
        # Provisional RTH rows belong in the snapshot only, never durable
        # skew_history, and do not move the completed-session distribution.
        assert payload["stats"] == compute_stats(series)
        assert self.db_writes == [False]

    def test_cached_intraday_row_is_replaced_not_promoted_or_duplicated(self):
        durable = compute_change_series(
            [_row("2026-08-03", 1.31), _row("2026-08-04", 1.30)]
        )
        stale_intraday = {
            **_row("2026-08-05", 1.40),
            "change": 0.10,
            "is_intraday": True,
            "as_of": "2026-08-05T14:00:00Z",
        }
        self._cached([*durable, stale_intraday])
        self.monkeypatch.setattr(self.mod, "_read_history_rows", lambda: durable)
        client = _StubClient({
            ("2026-08-21", "2026-08-05"): UW_NEAR_PAYLOAD,
            ("2026-09-18", "2026-08-05"): UW_PAYLOAD,
        })

        payload = self.mod.run(
            client=client, now=datetime(2026, 8, 5, 16, 0, tzinfo=timezone.utc)
        )

        assert [row["date"] for row in payload["series"]] == [
            "2026-08-03", "2026-08-04", "2026-08-05",
        ]
        assert payload["current"]["ratio"] == pytest.approx(CM_RATIO, abs=1e-9)
        assert payload["current"]["as_of"] == "2026-08-05T16:00:00Z"
        assert self.db_writes == [False]

    def test_post_close_grace_keeps_last_live_snapshot_without_finalizing_early(self):
        durable = compute_change_series(
            [_row("2026-08-03", 1.31), _row("2026-08-04", 1.30)]
        )
        cached_intraday = {
            **_row("2026-08-05", 1.24),
            "change": -0.06,
            "is_intraday": True,
            "as_of": "2026-08-05T19:59:00Z",
        }
        self._cached([*durable, cached_intraday])
        client = _StubClient({})

        # 20:30 UTC = 16:30 ET, after the close but before the established
        # 16:45 ET finalization window. Do not promote an immature close.
        payload = self.mod.run(
            client=client, now=datetime(2026, 8, 5, 20, 30, tzinfo=timezone.utc)
        )

        assert client.calls == []
        assert payload["current"] == cached_intraday
        assert self.db_writes == [False]

    def test_after_close_grace_finalizes_today_and_removes_intraday_marker(self):
        durable = compute_change_series(
            [_row("2026-08-03", 1.31), _row("2026-08-04", 1.30)]
        )
        cached_intraday = {
            **_row("2026-08-05", 1.40),
            "change": 0.10,
            "is_intraday": True,
            "as_of": "2026-08-05T19:59:00Z",
        }
        self._cached([*durable, cached_intraday])
        client = _StubClient({
            ("2026-08-21", "2026-08-05"): UW_NEAR_PAYLOAD,
            ("2026-09-18", "2026-08-05"): UW_PAYLOAD,
        })

        payload = self.mod.run(
            client=client, now=datetime(2026, 8, 5, 20, 46, tzinfo=timezone.utc)
        )

        assert payload["current"]["date"] == "2026-08-05"
        assert payload["current"]["ratio"] == pytest.approx(CM_RATIO, abs=1e-9)
        assert "is_intraday" not in payload["current"]
        assert "as_of" not in payload["current"]
        assert self.db_writes == [True]

    def test_missing_session_fetches_both_brackets_and_upserts_the_cm_row(self):
        series = compute_change_series([_row("2026-08-03", 1.31), _row("2026-08-04", 1.30)])
        self._cached(series)
        client = _StubClient({
            ("2026-08-21", "2026-08-05"): UW_NEAR_PAYLOAD,
            ("2026-09-18", "2026-08-05"): UW_PAYLOAD,
        })
        payload = self.mod.run(
            client=client, now=datetime(2026, 8, 5, 23, 0, tzinfo=timezone.utc)
        )
        assert ("2026-08-21", "2026-08-05") in client.calls
        assert ("2026-09-18", "2026-08-05") in client.calls
        current = payload["current"]
        assert current["date"] == "2026-08-05"
        assert current["ratio"] == pytest.approx(CM_RATIO, abs=1e-9)
        assert current["put_iv"] == pytest.approx(CM_PUT_25D, abs=1e-9)
        assert current["call_iv"] == pytest.approx(CM_CALL_25D, abs=1e-9)
        assert current["change"] == pytest.approx(CM_RATIO - 1.30, abs=1e-9)
        assert current["expiry"] == "2026-08-21"
        assert current["dte"] == 16
        assert current["expiry_far"] == "2026-09-18"
        assert current["dte_far"] == 44
        assert self.db_writes == [True]

    def test_missing_json_cache_rehydrates_base_series_from_turso(self):
        # VPS first run: data/skew.json does not exist but Turso holds the
        # full backfilled history. run() must rebuild on top of the Turso
        # rows, NOT restart the series at the 10-session gap bound and
        # clobber the 731-row snapshot with a 10-row payload (2026-08-06).
        self.monkeypatch.setattr(
            self.mod, "_read_history_rows",
            lambda: [_row("2026-08-01", 1.28), _row("2026-08-04", 1.30)],
        )
        client = _StubClient({
            ("2026-08-21", "2026-08-05"): UW_NEAR_PAYLOAD,
            ("2026-09-18", "2026-08-05"): UW_PAYLOAD,
        })
        payload = self.mod.run(
            client=client, now=datetime(2026, 8, 5, 23, 0, tzinfo=timezone.utc)
        )
        assert payload["count"] == 3
        assert [r["date"] for r in payload["series"]] == [
            "2026-08-01", "2026-08-04", "2026-08-05",
        ]
        assert payload["current"]["ratio"] == pytest.approx(CM_RATIO, abs=1e-9)
        assert self.db_writes == [True]

    def test_stale_short_json_cache_is_unioned_with_turso_history(self):
        # A clobbered/short JSON mirror must not shrink the series when
        # Turso has more history; the union keyed by date wins.
        series = compute_change_series([_row("2026-08-04", 1.30), _row("2026-08-05", 1.29)])
        self._cached(series)
        self.monkeypatch.setattr(
            self.mod, "_read_history_rows",
            lambda: [_row("2026-08-01", 1.28), _row("2026-08-04", 1.30)],
        )
        client = _StubClient({})
        payload = self.mod.run(
            client=client, now=datetime(2026, 8, 5, 23, 0, tzinfo=timezone.utc)
        )
        assert client.calls == []
        assert [r["date"] for r in payload["series"]] == [
            "2026-08-01", "2026-08-04", "2026-08-05",
        ]
        assert self.db_writes == [False]

    def test_single_usable_bracket_falls_back_to_that_leg_alone(self):
        # Near chain empty (holiday-shifted fallbacks also empty): the far
        # monthly alone prices the row rather than dropping the session.
        series = compute_change_series([_row("2026-08-03", 1.31), _row("2026-08-04", 1.30)])
        self._cached(series)
        client = _StubClient({("2026-09-18", "2026-08-05"): UW_PAYLOAD})
        payload = self.mod.run(
            client=client, now=datetime(2026, 8, 5, 23, 0, tzinfo=timezone.utc)
        )
        current = payload["current"]
        assert current["ratio"] == pytest.approx(RATIO, abs=1e-9)
        assert current["expiry"] == "2026-09-18"
        assert current["dte"] == 44
        assert self.db_writes == [True]


class TestCalendarAndSanity:
    def test_backfill_calendar_excludes_2023_and_2024_holidays(self):
        # load_holidays returned an EMPTY set for unconfigured years, so the
        # first backfill fetched Christmas/New Year/Good Friday and UW served
        # garbage chains (25d call IV 60%) that poisoned the change series.
        import fetch_skew as mod

        days = set(mod._trading_days_between("2023-12-20", "2024-04-05"))
        assert "2023-12-25" not in days
        assert "2024-01-01" not in days
        assert "2024-03-29" not in days  # Good Friday 2024
        assert "2023-12-26" in days
        assert "2024-01-02" in days
        assert "2024-03-28" in days

    def test_implausible_ratio_row_is_rejected(self):
        # SPX 25d put/call is structurally put-over-call (~1.0-1.9); a chain
        # pricing it at 0.4 is upstream garbage, not a market event.
        import fetch_skew as mod

        garbage = {"data": [
            {"strike": 6000, "call_delta": 0.20, "call_volatility": 0.55,
             "put_delta": -0.20, "put_volatility": 0.22},
            {"strike": 6100, "call_delta": 0.30, "call_volatility": 0.65,
             "put_delta": -0.30, "put_volatility": 0.26},
        ]}
        client = _StubClient({
            ("2026-08-21", "2026-08-05"): garbage,
            ("2026-09-18", "2026-08-05"): garbage,
        })
        assert mod._fetch_session_row(client, "2026-08-05") is None


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
