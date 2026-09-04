"""ATS venue-share regime (Equibles /off-exchange-volume + /short-volume).

Fixture-driven, no live network. Weeks are window-relative (today minus N
Mondays) so nothing rots in CI. Expected numbers are stated as the arithmetic
that produces them, not as opaque constants.
"""
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from fetch_equibles_ats_venue_share import (
    ACCUMULATION_Z,
    CONSOLIDATED_FROM_PRICES,
    MIN_CONSOLIDATED_DAYS,
    MIN_HISTORY_WEEKS,
    MIN_SHORT_VOLUME_DAYS,
    Z_WINDOW_WEEKS,
    attach_zscores,
    build_payload,
    build_ticker_series,
    classify,
    derive_week_metrics,
    payload_has_data,
    week_start,
    weekly_consolidated_volume,
    weekly_short_volume,
)

MIGRATION = Path(__file__).parents[1] / "db" / "migrations" / "0041_equibles_ats_venue_share.sql"


# Fixed anchor Monday. Anchoring to the CURRENT in-progress week emitted
# five consecutive days from this week's Monday, i.e. future-dated rows on any
# Mon-Thu run, while run() caps the fetch at end_date = now.date().
ANCHOR_MONDAY = date(2026, 8, 31)


def mondays(count: int) -> list[str]:
    """The `count` Mondays ending at the fixed ANCHOR_MONDAY, ascending."""
    assert ANCHOR_MONDAY.weekday() == 0
    return [
        (ANCHOR_MONDAY - timedelta(weeks=count - 1 - i)).isoformat()
        for i in range(count)
    ]


def off_exchange_row(week: str, *, ats_volume=4_000_000.0, ats_trades=8_000.0,
                     non_ats_volume=6_000_000.0, non_ats_trades=30_000.0) -> dict:
    return {
        "weekStartDate": week,
        "atsVolume": ats_volume,
        "atsTradeCount": ats_trades,
        "nonAtsOtcVolume": non_ats_volume,
        "nonAtsOtcTradeCount": non_ats_trades,
        "totalOffExchangeVolume": ats_volume + non_ats_volume,
    }


def short_volume_week(week: str, *, daily_total=5_000_000.0, daily_short=2_000_000.0,
                      days=5) -> list[dict]:
    start = date.fromisoformat(week)
    return [
        {
            "date": (start + timedelta(days=offset)).isoformat(),
            "shortVolume": daily_short,
            "shortExemptVolume": 0.0,
            "totalVolume": daily_total,
        }
        for offset in range(days)
    ]


def price_week(week: str, *, daily_volume=25_000_000.0, days=5) -> list[dict]:
    """Daily /prices rows - the CONSOLIDATED tape, unlike short-volume totals."""
    start = date.fromisoformat(week)
    return [
        {
            "date": (start + timedelta(days=offset)).isoformat(),
            "close": 100.0,
            "volume": daily_volume,
        }
        for offset in range(days)
    ]


# ── week bucketing ────────────────────────────────────────────────


class TestWeekStart:
    def test_any_weekday_maps_to_its_monday(self):
        monday = date.fromisoformat(mondays(1)[0])
        for offset in range(7):
            assert week_start((monday + timedelta(days=offset)).isoformat()) == monday.isoformat()

    def test_monday_is_its_own_week_start(self):
        monday = mondays(1)[0]
        assert week_start(monday) == monday

    def test_unparseable_dates_yield_none(self):
        assert week_start("not-a-date") is None
        assert week_start(None) is None
        assert week_start("") is None


class TestWeeklyShortVolume:
    def test_daily_rows_aggregate_into_weekly_share(self):
        week = mondays(1)[0]
        weekly = weekly_short_volume(short_volume_week(week))
        assert weekly[week]["days"] == 5
        assert weekly[week]["total_volume"] == 25_000_000.0
        assert weekly[week]["short_volume"] == 10_000_000.0
        assert weekly[week]["short_volume_pct"] == pytest.approx(40.0)

    def test_partial_week_coverage_withholds_the_share(self):
        week = mondays(1)[0]
        weekly = weekly_short_volume(short_volume_week(week, days=MIN_SHORT_VOLUME_DAYS - 1))
        assert weekly[week]["days"] == MIN_SHORT_VOLUME_DAYS - 1
        assert weekly[week]["short_volume_pct"] is None
        assert weekly[week]["total_volume"] is None

    def test_rows_spanning_weeks_are_bucketed_separately(self):
        week_a, week_b = mondays(2)
        weekly = weekly_short_volume(short_volume_week(week_a) + short_volume_week(week_b))
        assert sorted(weekly) == sorted([week_a, week_b])

    def test_malformed_rows_are_skipped(self):
        weekly = weekly_short_volume([{"date": "nope", "totalVolume": 1}, {"totalVolume": 1}])
        assert weekly == {}


# ── per-week derived metrics ──────────────────────────────────────


class TestWeeklyConsolidatedVolume:
    """The /prices daily bars ARE the consolidated tape (verified live
    2026-08-13: AAPL off-exchange/consolidated = 39.9%, NVDA = 48.2%, both
    inside the plausible band; against short-volume totalVolume the same
    figures give 127% and 116%, which is impossible)."""

    def test_daily_bars_fold_into_a_weekly_total(self):
        week = mondays(1)[0]
        weekly = weekly_consolidated_volume(price_week(week, daily_volume=20_000_000.0))
        assert weekly[week]["consolidated_volume"] == 100_000_000.0
        assert weekly[week]["days"] == 5

    def test_undercovered_week_withholds_the_total(self):
        """A partial week understates the denominator, which would inflate the
        share - withhold rather than publish a wrong number."""
        week = mondays(1)[0]
        weekly = weekly_consolidated_volume(
            price_week(week, days=MIN_CONSOLIDATED_DAYS - 1)
        )
        assert weekly[week]["consolidated_volume"] is None
        assert weekly[week]["days"] == MIN_CONSOLIDATED_DAYS - 1

    def test_rows_without_a_usable_date_are_ignored(self):
        assert weekly_consolidated_volume([{"date": "nope", "volume": 1}, {"volume": 2}]) == {}

    def test_missing_volume_does_not_crash(self):
        week = mondays(1)[0]
        rows = price_week(week)
        rows[0].pop("volume")
        weekly = weekly_consolidated_volume(rows)
        assert weekly[week]["days"] == 5


class TestOffExchangeShare:
    def test_share_is_computed_against_the_consolidated_tape(self):
        week = mondays(1)[0]
        # 10M off-exchange against 100M consolidated -> 10%
        consolidated = weekly_consolidated_volume(price_week(week, daily_volume=20_000_000.0))[week]
        metrics = derive_week_metrics(
            off_exchange_row(week, ats_volume=4_000_000.0, non_ats_volume=6_000_000.0),
            None,
            consolidated,
        )
        assert metrics["off_exchange_share_pct"] == pytest.approx(10.0)
        assert metrics["consolidated_volume"] == 100_000_000.0
        assert metrics["consolidated_volume_source"] == CONSOLIDATED_FROM_PRICES

    def test_share_stays_null_without_a_consolidated_week(self):
        week = mondays(1)[0]
        metrics = derive_week_metrics(off_exchange_row(week), None, None)
        assert metrics["off_exchange_share_pct"] is None
        assert metrics["consolidated_volume_source"] is None

    def test_undercovered_consolidated_week_yields_no_share(self):
        week = mondays(1)[0]
        consolidated = weekly_consolidated_volume(
            price_week(week, days=MIN_CONSOLIDATED_DAYS - 1)
        )[week]
        metrics = derive_week_metrics(off_exchange_row(week), None, consolidated)
        assert metrics["off_exchange_share_pct"] is None

    def test_short_volume_total_is_never_used_as_the_denominator(self):
        """Regression: short-volume totalVolume is itself off-exchange, so
        substituting it reports a share above 100%."""
        week = mondays(1)[0]
        short_week = weekly_short_volume(short_volume_week(week, daily_total=1_000_000.0))[week]
        metrics = derive_week_metrics(off_exchange_row(week), short_week, None)
        assert metrics["off_exchange_share_pct"] is None


class TestDeriveWeekMetrics:
    def test_print_sizes_and_ats_venue_share(self):
        week = mondays(1)[0]
        short_week = weekly_short_volume(short_volume_week(week))[week]
        metrics = derive_week_metrics(off_exchange_row(week), short_week)

        assert metrics["week_start_date"] == week
        assert metrics["avg_ats_print_size"] == pytest.approx(500.0)  # 4,000,000 / 8,000
        assert metrics["avg_non_ats_print_size"] == pytest.approx(200.0)  # 6,000,000 / 30,000
        assert metrics["ats_share_pct"] == pytest.approx(40.0)  # 4m of 10m off-exchange
        assert metrics["short_volume_pct"] == pytest.approx(40.0)

    def test_short_volume_share_is_recomputed_not_read_from_the_percent_field(self):
        """Equibles sends shortVolumePercent already in percent, so the shared
        client's fraction registry scales it a second time (42.37 -> 4236.67).
        The raw volumes are unambiguous and are the only input used."""
        week = mondays(1)[0]
        daily = short_volume_week(week)
        for row in daily:
            row["shortVolumePct"] = 4236.67  # the double-scaled value, ignored

        weekly = weekly_short_volume(daily)[week]
        metrics = derive_week_metrics(off_exchange_row(week), weekly)
        assert metrics["short_volume_pct"] == pytest.approx(40.0)

    def test_off_exchange_share_stays_null_without_a_consolidated_denominator(self):
        """The daily file's totalVolume is off-exchange only (live-verified),
        so it must never stand in as the all-venue denominator."""
        week = mondays(1)[0]
        short_week = weekly_short_volume(short_volume_week(week))[week]
        metrics = derive_week_metrics(off_exchange_row(week), short_week)

        assert metrics["off_exchange_share_pct"] is None
        assert metrics["consolidated_volume"] is None
        assert metrics["consolidated_volume_source"] is None

    def test_a_payload_carried_consolidated_volume_is_used_when_present(self):
        week = mondays(1)[0]
        row = {**off_exchange_row(week), "consolidatedVolume": 50_000_000.0}
        metrics = derive_week_metrics(row, None)

        assert metrics["consolidated_volume"] == 50_000_000.0
        assert metrics["consolidated_volume_source"] == "off_exchange_payload"
        assert metrics["off_exchange_share_pct"] == pytest.approx(20.0)  # 10m / 50m

    def test_missing_short_volume_week_leaves_its_share_null_without_raising(self):
        week = mondays(1)[0]
        metrics = derive_week_metrics(off_exchange_row(week), None)
        assert metrics["short_volume_pct"] is None
        assert metrics["ats_share_pct"] == pytest.approx(40.0)

    def test_zero_trade_counts_do_not_divide(self):
        week = mondays(1)[0]
        metrics = derive_week_metrics(
            off_exchange_row(week, ats_trades=0.0, non_ats_trades=0.0), None
        )
        assert metrics["avg_ats_print_size"] is None
        assert metrics["avg_non_ats_print_size"] is None

    def test_renamed_or_absent_upstream_fields_are_tolerated(self):
        week = mondays(1)[0]
        metrics = derive_week_metrics({"weekStart": week, "atsVolume": 1_000.0}, None)
        assert metrics["week_start_date"] == week
        assert metrics["ats_volume"] == 1_000.0
        assert metrics["ats_trade_count"] is None
        assert metrics["total_off_exchange_volume"] is None
        assert metrics["avg_ats_print_size"] is None

    def test_rows_without_a_resolvable_week_are_rejected(self):
        assert derive_week_metrics({"atsVolume": 1.0}, None) is None


# ── trailing z-scores ─────────────────────────────────────────────


def flat_series(weeks: list[str], value: float, *, key="ats_share_pct") -> list[dict]:
    return [{"week_start_date": w, key: value, "avg_ats_print_size": value} for w in weeks]


class TestAttachZscores:
    def test_thin_history_yields_null_z(self):
        weeks = mondays(MIN_HISTORY_WEEKS - 1)
        rows = attach_zscores(flat_series(weeks, 30.0))
        assert all(row["ats_share_z"] is None for row in rows)

    def test_a_spike_against_a_stable_history_scores_high(self):
        weeks = mondays(MIN_HISTORY_WEEKS + 1)
        rows = flat_series(weeks, 30.0)
        for index, row in enumerate(rows[:-1]):
            row["ats_share_pct"] = 30.0 + (1.0 if index % 2 else -1.0)
        rows[-1]["ats_share_pct"] = 34.0

        scored = attach_zscores(rows)
        assert scored[-1]["ats_share_z"] == pytest.approx(4.0)  # (34-30)/1.0

    def test_zero_variance_history_yields_null_z(self):
        weeks = mondays(MIN_HISTORY_WEEKS + 1)
        scored = attach_zscores(flat_series(weeks, 30.0))
        assert scored[-1]["ats_share_z"] is None

    def test_window_is_bounded_to_the_trailing_year(self):
        weeks = mondays(Z_WINDOW_WEEKS + 18)
        rows = flat_series(weeks, 10.0)
        for row in rows[:17]:
            row["ats_share_pct"] = 100.0  # older than the trailing window

        scored = attach_zscores(rows)
        assert scored[-1]["ats_share_z"] is None  # window sees only the flat 10.0s

    def test_null_observations_are_skipped_not_zero_filled(self):
        weeks = mondays(MIN_HISTORY_WEEKS + 2)
        rows = flat_series(weeks, 30.0)
        for index, row in enumerate(rows[:-1]):
            row["ats_share_pct"] = 30.0 + (1.0 if index % 2 else -1.0)
        rows[0]["ats_share_pct"] = None
        rows[-1]["ats_share_pct"] = 34.0

        scored = attach_zscores(rows)
        assert scored[0]["ats_share_z"] is None
        assert scored[-1]["ats_share_z"] is not None


# ── classification ────────────────────────────────────────────────


class TestClassify:
    def test_both_metrics_elevated_reads_accumulation(self):
        assert classify(ACCUMULATION_Z, ACCUMULATION_Z, MIN_HISTORY_WEEKS) == (
            "accumulation",
            None,
        )

    def test_both_metrics_depressed_reads_distribution(self):
        assert classify(-2.0, -1.5, MIN_HISTORY_WEEKS) == ("distribution", None)

    def test_either_alone_is_noise(self):
        assert classify(3.0, 0.2, MIN_HISTORY_WEEKS)[0] == "neutral"
        assert classify(0.2, 3.0, MIN_HISTORY_WEEKS)[0] == "neutral"
        assert classify(3.0, -2.0, MIN_HISTORY_WEEKS)[0] == "neutral"

    def test_thin_history_is_neutral_with_a_reason(self):
        verdict, reason = classify(None, None, 4)
        assert verdict == "neutral"
        assert "4" in reason and str(MIN_HISTORY_WEEKS) in reason

    def test_flat_history_is_neutral_with_a_distinct_reason(self):
        verdict, reason = classify(None, 1.4, MIN_HISTORY_WEEKS + 5)
        assert verdict == "neutral"
        assert reason is not None
        assert "insufficient history" not in reason


# ── series assembly ───────────────────────────────────────────────


class TestBuildTickerSeries:
    def _rows(self, count: int):
        weeks = mondays(count)
        off_rows = [off_exchange_row(week) for week in weeks]
        short_rows = [row for week in weeks for row in short_volume_week(week)]
        return weeks, off_rows, short_rows

    def test_series_is_ascending_and_fully_derived(self):
        weeks, off_rows, short_rows = self._rows(MIN_HISTORY_WEEKS + 6)
        series = build_ticker_series(list(reversed(off_rows)), short_rows)

        assert [row["week_start_date"] for row in series] == weeks
        assert series[-1]["ats_share_pct"] == pytest.approx(40.0)
        assert series[-1]["avg_ats_print_size"] == pytest.approx(500.0)
        assert series[-1]["classification"] == "neutral"
        assert series[-1]["classification_reason"] is not None  # flat history, no z

    def test_a_joint_surge_classifies_as_accumulation(self):
        weeks, off_rows, short_rows = self._rows(MIN_HISTORY_WEEKS + 6)
        for index, row in enumerate(off_rows[:-1]):
            wobble = 1.02 if index % 2 else 0.98
            row["atsVolume"] *= wobble
            row["totalOffExchangeVolume"] = row["atsVolume"] + row["nonAtsOtcVolume"]
        surge = off_rows[-1]
        surge["atsVolume"] = 12_000_000.0
        surge["atsTradeCount"] = 8_000.0
        surge["totalOffExchangeVolume"] = surge["atsVolume"] + surge["nonAtsOtcVolume"]

        series = build_ticker_series(off_rows, short_rows)
        assert series[-1]["ats_share_z"] > ACCUMULATION_Z
        assert series[-1]["avg_ats_print_size_z"] > ACCUMULATION_Z
        assert series[-1]["classification"] == "accumulation"
        assert series[-1]["classification_reason"] is None

    def test_empty_input_yields_an_empty_series(self):
        assert build_ticker_series([], []) == []


# ── payload ───────────────────────────────────────────────────────


class TestBuildPayload:
    def test_payload_exposes_latest_week_per_ticker(self):
        weeks = mondays(3)
        series = {
            "AAPL": [
                {"week_start_date": w, "ats_share_pct": 40.0,
                 "ats_share_z": 2.0, "avg_ats_print_size_z": 2.0,
                 "classification": "accumulation", "classification_reason": None}
                for w in weeks
            ],
            "MSFT": [
                {"week_start_date": weeks[0], "ats_share_pct": 20.0,
                 "ats_share_z": None, "avg_ats_print_size_z": None,
                 "classification": "neutral", "classification_reason": "thin"}
            ],
        }
        payload = build_payload(series, scan_time="2026-08-11T00:00:00Z", errors=[])

        assert payload["count"] == 2
        assert payload["tickers"] == ["AAPL", "MSFT"]
        assert payload["week_start_date"] == weeks[-1]
        current = {row["ticker"]: row for row in payload["current"]}
        assert current["AAPL"]["week_start_date"] == weeks[-1]
        assert current["MSFT"]["classification"] == "neutral"
        assert payload_has_data(payload) is True

    def test_current_ranks_the_strongest_accumulation_first(self):
        weeks = mondays(1)
        series = {
            "QUIET": [{"week_start_date": weeks[0], "ats_share_z": 0.1,
                       "avg_ats_print_size_z": 0.1, "classification": "neutral"}],
            "LOUD": [{"week_start_date": weeks[0], "ats_share_z": 2.6,
                      "avg_ats_print_size_z": 2.2, "classification": "accumulation"}],
        }
        payload = build_payload(series, scan_time="2026-08-11T00:00:00Z", errors=[])
        assert [row["ticker"] for row in payload["current"]] == ["LOUD", "QUIET"]

    def test_errors_are_reported_without_faking_data(self):
        payload = build_payload({}, scan_time="2026-08-11T00:00:00Z",
                                errors=[{"ticker": "AAPL", "error": "rate_limited"}])
        assert payload["current"] == []
        assert payload["errors"][0]["ticker"] == "AAPL"
        assert payload_has_data(payload) is False


# ── run(): orchestration, budget, and empty-result gating ─────────


class _StubPaged:
    def __init__(self, rows):
        self.rows = rows
        self.pages = 1
        self.requests_used = 1
        self.has_more = False


class _StubClient:
    """Implements only the surface the fetcher uses: paginate + endpoints."""

    def __init__(self, off_rows_by_ticker, short_rows_by_ticker, failures=None):
        self._off = off_rows_by_ticker
        self._short = short_rows_by_ticker
        self._failures = failures or {}
        self.request_count = 0
        self.rate_limit_remaining = 99_000
        self.calls = []

    def paginate(self, fetcher, *args, **kwargs):
        ticker = args[0]
        self.calls.append((fetcher.__name__, ticker))
        if ticker in self._failures:
            raise self._failures[ticker]
        self.request_count += 1
        source = self._off if fetcher.__name__ == "get_off_exchange_volume" else self._short
        return _StubPaged(list(source.get(ticker, [])))

    def get_off_exchange_volume(self, ticker, **kwargs):  # resolved by name only
        raise AssertionError("paginate owns the HTTP calls")

    def get_short_volume(self, ticker, **kwargs):
        raise AssertionError("paginate owns the HTTP calls")

    def close(self):
        pass


class TestRun:
    @pytest.fixture(autouse=True)
    def _isolate_caches(self, tmp_path, monkeypatch):
        import fetch_equibles_ats_venue_share as mod

        monkeypatch.setattr(mod, "ATS_VENUE_SHARE_JSON", tmp_path / "ats_venue_share.json")
        self.db_writes = []
        self.health = []
        monkeypatch.setattr(
            mod, "_write_db_cache",
            lambda payload, scan_time: self.db_writes.append(payload),
        )
        monkeypatch.setattr(
            mod, "_record_health",
            lambda state, scan_time, error=None: self.health.append(state),
        )
        self.mod = mod

    def _client(self, tickers, failures=None):
        weeks = mondays(MIN_HISTORY_WEEKS + 4)
        off = {t: [off_exchange_row(w) for w in weeks] for t in tickers}
        short = {t: [r for w in weeks for r in short_volume_week(w)] for t in tickers}
        return _StubClient(off, short, failures=failures)

    def test_run_builds_writes_and_returns_the_payload(self):
        client = self._client(["AAPL", "MSFT"])
        payload = self.mod.run(client=client, tickers=["AAPL", "MSFT"])

        assert payload["count"] == 2
        assert payload["errors"] == []
        assert len(self.db_writes) == 1
        assert self.health == ["ok"]
        assert json.loads(self.mod.ATS_VENUE_SHARE_JSON.read_text())["count"] == 2

    def test_one_ticker_failing_does_not_abort_the_cycle(self):
        from clients.equibles_client import EquiblesNotFoundError

        client = self._client(["AAPL", "ZZZZ"], failures={"ZZZZ": EquiblesNotFoundError("nope")})
        payload = self.mod.run(client=client, tickers=["AAPL", "ZZZZ"])

        assert payload["count"] == 1
        assert payload["errors"][0]["ticker"] == "ZZZZ"
        assert self.health == ["ok"]
        assert len(self.db_writes) == 1

    def test_an_all_empty_cycle_is_never_cached(self):
        client = _StubClient({}, {})
        payload = self.mod.run(client=client, tickers=["AAPL"])

        assert payload_has_data(payload) is False
        assert self.db_writes == []
        assert self.health == ["error"]
        assert not self.mod.ATS_VENUE_SHARE_JSON.exists()

    def test_budget_is_two_paginated_walks_per_ticker(self):
        client = self._client(["AAPL"])
        self.mod.run(client=client, tickers=["AAPL"])
        assert client.calls == [
            ("get_off_exchange_volume", "AAPL"),
            ("get_short_volume", "AAPL"),
        ]


class TestSweepBudget:
    """2026-09-01: radon-equibles-ats Result=timeout after TimeoutStartSec=900
    (09:16:04Z → 09:31:04Z, NRestarts=0, CPU 734ms). Equibles HTTPS tarpitted
    the shared Session (sibling short-crowding still do_poll on :443 minutes
    later). No process wall-clock budget, so systemd SIGTERM'd the oneshot
    with no service_health row and the unit re-pages P1 every cycle (timeout
    is not on the exit-code latch). Next timer is a week out."""

    @pytest.fixture(autouse=True)
    def _isolate_caches(self, tmp_path, monkeypatch):
        import fetch_equibles_ats_venue_share as mod

        monkeypatch.setattr(mod, "ATS_VENUE_SHARE_JSON", tmp_path / "ats_venue_share.json")
        self.db_writes = []
        self.health = []
        monkeypatch.setattr(
            mod, "_write_db_cache",
            lambda payload, scan_time: self.db_writes.append(payload),
        )
        monkeypatch.setattr(
            mod, "_record_health",
            lambda state, scan_time, error=None: self.health.append(
                {"state": state, "error": error}
            ),
        )
        self.mod = mod

    def test_tarpitted_equibles_stops_inside_the_wall_clock_budget(self, monkeypatch):
        import time

        monkeypatch.setattr(self.mod, "SWEEP_BUDGET_S", 0.2, raising=False)
        monkeypatch.setattr(self.mod, "TICKER_FETCH_BUDGET_S", 0.15, raising=False)

        def hang(_client, ticker, _start, _end):
            time.sleep(0.5)
            raise AssertionError(f"tarpit returned for {ticker}")

        monkeypatch.setattr(self.mod, "_fetch_ticker", hang)
        tickers = [f"T{i:02d}" for i in range(8)]
        client = _StubClient({}, {})
        started = time.monotonic()
        payload = self.mod.run(client=client, tickers=tickers)
        elapsed = time.monotonic() - started

        # A bare for-loop over 8×0.5s sleeps would take ~4s and still be
        # running when systemd SIGTERM'd at 900s. The budget must cut this
        # off well under a second and still reach a health write.
        assert elapsed < 1.0
        assert self.db_writes == []
        assert self.health and self.health[0]["state"] == "error"
        assert payload.get("partial") is True or not payload_has_data(payload)

    def test_tickers_finished_before_the_deadline_are_kept(self, monkeypatch):
        monkeypatch.setattr(self.mod, "SWEEP_BUDGET_S", 30.0, raising=False)
        weeks = mondays(MIN_HISTORY_WEEKS + 4)
        off = {t: [off_exchange_row(w) for w in weeks] for t in ("AAPL", "MSFT")}
        short = {t: [r for w in weeks for r in short_volume_week(w)] for t in ("AAPL", "MSFT")}
        client = _StubClient(off, short)
        payload = self.mod.run(client=client, tickers=["AAPL", "MSFT"])
        assert payload["count"] == 2
        assert self.health[-1]["state"] == "ok"
        assert len(self.db_writes) == 1

    def test_sweep_budget_fits_inside_unit_start_timeout(self):
        service = (
            Path(__file__).resolve().parents[2]
            / "cloud"
            / "services"
            / "radon-equibles-ats.service"
        )
        timeout_line = next(
            line for line in service.read_text().splitlines()
            if line.startswith("TimeoutStartSec=")
        )
        unit_timeout = int(timeout_line.split("=", 1)[1])
        from fetch_equibles_ats_venue_share import SWEEP_BUDGET_S, TICKER_FETCH_BUDGET_S

        assert SWEEP_BUDGET_S + TICKER_FETCH_BUDGET_S <= unit_timeout


# ── migration + upsert (sqlite3 stand-in for libsql) ───────────────


class TestAtsVenueShareStorage:
    def _db(self):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)")
        db.executescript(MIGRATION.read_text())
        return db

    def test_migration_applies_and_registers_version(self):
        db = self._db()
        assert db.execute("SELECT version FROM schema_migrations").fetchone()[0] == 41
        columns = {r[1] for r in db.execute("PRAGMA table_info(equibles_ats_venue_share)")}
        assert {
            "ticker", "week_start_date", "ats_volume", "ats_trade_count",
            "non_ats_otc_volume", "non_ats_otc_trade_count", "total_off_exchange_volume",
            "consolidated_volume", "consolidated_volume_source", "off_exchange_share_pct",
            "avg_ats_print_size", "avg_non_ats_print_size", "ats_share_pct", "ats_share_z",
            "avg_ats_print_size_z", "short_volume_pct", "classification",
            "classification_reason", "recorded_at",
        } <= columns

    def test_query_indices_exist_for_both_read_shapes(self):
        db = self._db()
        indices = {r[1] for r in db.execute("PRAGMA index_list(equibles_ats_venue_share)")}
        assert "idx_equibles_ats_venue_share_ticker_week" in indices
        assert "idx_equibles_ats_venue_share_week" in indices

    def test_upsert_is_idempotent_per_ticker_week(self):
        from fetch_equibles_ats_venue_share import ats_venue_share_upsert

        db = self._db()
        week = mondays(1)[0]
        rows = [{
            "ticker": "AAPL", "week_start_date": week, "ats_volume": 1.0,
            "ats_trade_count": 2.0, "non_ats_otc_volume": 3.0,
            "non_ats_otc_trade_count": 4.0, "total_off_exchange_volume": 4.0,
            "consolidated_volume": 10.0, "consolidated_volume_source": "off_exchange_payload",
            "ats_share_pct": 40.0, "avg_ats_print_size": 0.5,
            "avg_non_ats_print_size": 0.75, "ats_share_z": None,
            "avg_ats_print_size_z": None, "short_volume_pct": 12.5,
            "classification": "neutral", "classification_reason": "thin",
        }]
        sql, params = ats_venue_share_upsert(rows, "2026-08-11T00:00:00Z")
        db.execute(sql, params)

        revised = [{**rows[0], "ats_share_pct": 41.5, "classification": "accumulation"}]
        sql, params = ats_venue_share_upsert(revised, "2026-08-12T00:00:00Z")
        db.execute(sql, params)

        stored = db.execute(
            "SELECT ticker, ats_share_pct, classification, recorded_at "
            "FROM equibles_ats_venue_share"
        ).fetchall()
        assert stored == [("AAPL", 41.5, "accumulation", "2026-08-12T00:00:00Z")]

    def test_multi_row_chunk_writes_every_row(self):
        from fetch_equibles_ats_venue_share import ats_venue_share_upsert

        db = self._db()
        weeks = mondays(3)
        rows = [
            {"ticker": ticker, "week_start_date": week, "classification": "neutral"}
            for ticker in ("AAPL", "MSFT")
            for week in weeks
        ]
        sql, params = ats_venue_share_upsert(rows, "2026-08-11T00:00:00Z")
        db.execute(sql, params)
        assert db.execute("SELECT COUNT(*) FROM equibles_ats_venue_share").fetchone()[0] == 6


# ── silent-failure hole: construction must heartbeat ──────────────


class TestConstructionFailureIsReported:
    """A rejected/absent key raises at EquiblesClient() — BEFORE any fetch.

    That construction used to sit outside the health-reporting block, so the
    oneshot died with no `service_health` row at all: not even an error row.
    `equibles-ats-venue-share` is registered for freshness
    (scripts/watchdog/services.py, web/lib/serviceHealthWindows.ts), so a row
    that never arrives is silent rather than stale and nothing alerts.
    """

    @pytest.fixture(autouse=True)
    def _capture_health(self, tmp_path, monkeypatch):
        import fetch_equibles_ats_venue_share as mod

        monkeypatch.setattr(mod, "ATS_VENUE_SHARE_JSON", tmp_path / "ats_venue_share.json")
        self.health = []
        monkeypatch.setattr(
            mod, "_record_health",
            lambda state, scan_time, error=None: self.health.append(state),
        )
        self.mod = mod

    def test_run_heartbeats_error_when_the_key_is_absent(self, monkeypatch):
        from clients.equibles_client import EquiblesAuthError

        monkeypatch.delenv("EQUIBLES_API_KEY", raising=False)
        with pytest.raises(EquiblesAuthError):
            self.mod.run(tickers=["AAPL"])
        assert self.health == ["error"]


class TestNextAttemptAt:
    """The timer is weekly (Tue 09:15 UTC). Every error heartbeat must say when
    the next attempt actually lands, or the watchdog re-pages the same weekly
    writer on every cycle for seven days."""

    @pytest.fixture(autouse=True)
    def _capture_health(self, tmp_path, monkeypatch):
        import fetch_equibles_ats_venue_share as mod

        monkeypatch.setattr(mod, "ATS_VENUE_SHARE_JSON", tmp_path / "ats_venue_share.json")
        self.db_writes = []
        self.health = []
        monkeypatch.setattr(
            mod, "_write_db_cache",
            lambda payload, scan_time: self.db_writes.append(payload),
        )
        monkeypatch.setattr(
            mod, "_record_health",
            lambda state, scan_time, error=None: self.health.append(
                {"state": state, "error": error}
            ),
        )
        self.mod = mod

    def _client(self, tickers, failures=None):
        weeks = mondays(MIN_HISTORY_WEEKS + 4)
        off = {t: [off_exchange_row(w) for w in weeks] for t in tickers}
        short = {t: [r for w in weeks for r in short_volume_week(w)] for t in tickers}
        return _StubClient(off, short, failures=failures)

    def _expected(self, now):
        stamp = now.replace(hour=9, minute=15, second=0, microsecond=0)
        while stamp <= now or stamp.weekday() != 1:
            stamp += timedelta(days=1)
        return stamp.isoformat().replace("+00:00", "Z")

    def _errors(self):
        return [h["error"] for h in self.health if h["state"] == "error"]

    def test_next_scheduled_run_is_the_coming_tuesday_0915_utc(self):
        anchor = datetime.now(timezone.utc).replace(microsecond=0)
        tuesday = anchor + timedelta(days=(1 - anchor.weekday()) % 7)
        for now in (
            anchor,
            tuesday.replace(hour=9, minute=0),
            tuesday.replace(hour=9, minute=30),
        ):
            assert self.mod._next_scheduled_run(now) == self._expected(now)

    def test_empty_cycle_error_carries_next_attempt_and_codes(self):
        from clients.equibles_client import EquiblesNotFoundError

        now = datetime.now(timezone.utc)
        client = _StubClient(
            {}, {}, failures={"ZZZZ": EquiblesNotFoundError("nope", 404, "not_found")}
        )
        self.mod.run(client=client, tickers=["AAPL", "ZZZZ"], now=now)

        error = self._errors()[0]
        assert error["message"] == "no ticker produced a series"
        assert error["next_attempt_at"] == self._expected(now)
        assert "not_found" in error["codes"]

    def test_wedged_client_empty_cycle_reports_timeout_and_budget_codes(self, monkeypatch):
        now = datetime.now(timezone.utc)

        def wedged(_client, ticker, _start, _end, timeout_s=0.0):
            raise TimeoutError(f"{ticker}: fetch exceeded {timeout_s:.0f}s wall-clock")

        monkeypatch.setattr(self.mod, "_fetch_ticker_bounded", wedged)
        client = _StubClient({}, {})
        self.mod.run(client=client, tickers=["AAPL", "MSFT", "NVDA"], now=now)

        error = self._errors()[0]
        assert error["message"] == "no ticker produced a series"
        assert error["next_attempt_at"] == self._expected(now)
        assert error["codes"] == ["budget", "timeout"]

    def test_partial_cycle_error_carries_next_attempt(self):
        from clients.equibles_client import EquiblesNotFoundError

        now = datetime.now(timezone.utc)
        universe = ["AAPL", "A", "B", "C", "D"]
        failures = {t: EquiblesNotFoundError("nope") for t in universe[1:]}
        client = self._client(universe, failures=failures)
        payload = self.mod.run(client=client, tickers=universe, now=now)

        assert payload["partial"] is True
        error = self._errors()[0]
        assert error["message"].startswith("partial cycle:")
        assert error["next_attempt_at"] == self._expected(now)

    def test_dropped_tail_error_carries_next_attempt(self, monkeypatch):
        now = datetime.now(timezone.utc)
        universe = ["AAPL", "MSFT", "NVDA", "AMD", "ZZZZ"]
        client = self._client(universe)
        real = self.mod._fetch_ticker_bounded

        def wedge_last(client_, ticker, start_s, end_s, timeout_s=0.0):
            if ticker == "ZZZZ":
                raise TimeoutError(f"{ticker}: fetch exceeded {timeout_s:.0f}s wall-clock")
            return real(client_, ticker, start_s, end_s, timeout_s=timeout_s)

        monkeypatch.setattr(self.mod, "_fetch_ticker_bounded", wedge_last)
        self.mod.run(client=client, tickers=universe, now=now)

        error = self._errors()[0]
        assert error["message"].startswith("dropped tail:")
        assert error["next_attempt_at"] == self._expected(now)

    def test_aborted_cycle_error_carries_next_attempt(self):
        from clients.equibles_client import EquiblesAuthError

        now = datetime.now(timezone.utc)
        client = self._client(["AAPL"], failures={"AAPL": EquiblesAuthError("rejected")})
        with pytest.raises(EquiblesAuthError):
            self.mod.run(client=client, tickers=["AAPL"], now=now)

        error = self._errors()[0]
        assert error["message"].startswith("cycle aborted:")
        assert error["next_attempt_at"] == self._expected(now)
