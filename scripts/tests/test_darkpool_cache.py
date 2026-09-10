"""Tests for the persistent (ticker, date) dark-pool cache.

Pins the P0 UW-load reduction: prior (closed) sessions are served from disk and
never re-fetched; today is always fetched live; empty/failed days are not cached.
"""
import json
from datetime import datetime, timedelta

import pytest

import utils.darkpool_cache as dpc

# CACHE_DIR is isolated to a per-test tmp dir by the autouse fixture in conftest.py.


def _yesterday() -> str:
    return (datetime.now(dpc._ET) - timedelta(days=1)).strftime("%Y-%m-%d")


SAMPLE_TRADES = [
    {"price": 100.1, "size": 5000, "premium": 500500},
    {"price": 100.2, "size": 3000, "premium": 300600},
]


# ── is_immutable ────────────────────────────────────────────────────

class TestIsImmutable:
    def test_prior_day_is_immutable(self):
        assert dpc.is_immutable("2020-01-02") is True

    def test_today_is_not_immutable(self):
        assert dpc.is_immutable(dpc._today_et()) is False

    def test_future_day_is_not_immutable(self):
        future = (datetime.now(dpc._ET) + timedelta(days=3)).strftime("%Y-%m-%d")
        assert dpc.is_immutable(future) is False

    def test_empty_is_not_immutable(self):
        assert dpc.is_immutable("") is False


# ── round-trip (prior day) ──────────────────────────────────────────

class TestPriorDayRoundTrip:
    def test_miss_returns_none(self):
        assert dpc.get_cached_darkpool("AAPL", _yesterday()) is None

    def test_set_then_get_prior_day(self):
        date = _yesterday()
        dpc.set_cached_darkpool("AAPL", date, SAMPLE_TRADES)
        got = dpc.get_cached_darkpool("AAPL", date)
        assert got == SAMPLE_TRADES

    def test_keys_are_per_ticker_and_date(self):
        date = _yesterday()
        dpc.set_cached_darkpool("AAPL", date, SAMPLE_TRADES)
        assert dpc.get_cached_darkpool("MSFT", date) is None

    def test_lowercase_ticker_normalised(self):
        date = _yesterday()
        dpc.set_cached_darkpool("aapl", date, SAMPLE_TRADES)
        assert dpc.get_cached_darkpool("AAPL", date) == SAMPLE_TRADES

    def test_persists_metadata(self, tmp_path):
        date = _yesterday()
        dpc.set_cached_darkpool("AAPL", date, SAMPLE_TRADES)
        path = dpc._path("AAPL", date)
        payload = json.loads(path.read_text())
        assert payload["count"] == 2
        assert payload["date"] == date
        assert payload["schema"] == dpc.CACHE_SCHEMA
        assert payload["complete"] is True


class TestCacheSchemaPagination:
    """Pre-pagination disk rows (schema missing / < 2) are single-page
    truncated at UW's 500 limit. Reject them so liquid names re-fetch.
    """

    def test_legacy_schema_miss_forces_refetch(self):
        date = _yesterday()
        path = dpc._path("GLD", date)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "ticker": "GLD",
            "date": date,
            "count": 500,
            "trades": [{"price": 1, "size": 1}] * 500,
            # no schema key — pre-pagination write
        }))
        assert dpc.get_cached_darkpool("GLD", date) is None

    def test_schema_v1_miss(self):
        date = _yesterday()
        path = dpc._path("GLD", date)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "ticker": "GLD",
            "date": date,
            "count": 500,
            "schema": 1,
            "trades": [{"price": 1, "size": 1}] * 500,
        }))
        assert dpc.get_cached_darkpool("GLD", date) is None

    def test_current_schema_hit(self):
        date = _yesterday()
        dpc.set_cached_darkpool("GLD", date, SAMPLE_TRADES)
        assert dpc.get_cached_darkpool("GLD", date) == SAMPLE_TRADES


class TestScoringWalkIsNotAFullDay:
    """SNDK 2026-09-10: discover's 2-page scoring walk (~976 prints) was
    written as schema v2 and then served to /flow-analysis as a complete
    session. Days that actually filled the 40-page cap (~19k) sat next to
    those truncated rows in the same report.
    """

    def _write_v2(self, ticker: str, date: str, n: int, **extra):
        path = dpc._path(ticker, date)
        path.parent.mkdir(parents=True, exist_ok=True)
        trades = [{"price": 1, "size": 1, "premium": 1}] * n
        payload = {
            "ticker": ticker,
            "date": date,
            "count": n,
            "schema": 2,
            "trades": trades,
        }
        payload.update(extra)
        path.write_text(json.dumps(payload))
        return trades

    def test_legacy_two_page_row_is_a_miss_for_flow_consumers(self):
        date = _yesterday()
        self._write_v2("SNDK", date, 976)
        assert dpc.get_cached_darkpool("SNDK", date) is None

    def test_exactly_one_full_page_without_complete_is_a_miss(self):
        date = _yesterday()
        self._write_v2("SNDK", date, 500)
        assert dpc.get_cached_darkpool("SNDK", date) is None

    def test_short_first_page_without_complete_still_hits(self):
        date = _yesterday()
        trades = self._write_v2("SNDK", date, 499)
        assert dpc.get_cached_darkpool("SNDK", date) == trades

    def test_full_cap_legacy_v2_still_hits(self):
        date = _yesterday()
        trades = self._write_v2("SNDK", date, 1001)
        assert dpc.get_cached_darkpool("SNDK", date) == trades

    def test_incomplete_flag_is_a_miss_even_outside_the_band(self):
        date = _yesterday()
        self._write_v2("SNDK", date, 19050, complete=False)
        assert dpc.get_cached_darkpool("SNDK", date) is None

    def test_scoring_may_reuse_an_incomplete_row(self):
        date = _yesterday()
        trades = self._write_v2("SNDK", date, 976, complete=False)
        assert dpc.get_cached_darkpool("SNDK", date, require_complete=False) == trades

    def test_set_incomplete_is_a_miss_for_flow_consumers(self):
        date = _yesterday()
        sample = [{"price": 1, "size": 1}] * 1000
        dpc.set_cached_darkpool("SNDK", date, sample, complete=False)
        assert dpc.get_cached_darkpool("SNDK", date) is None
        assert dpc.get_cached_darkpool("SNDK", date, require_complete=False) == sample


# ── today is never cached ───────────────────────────────────────────

class TestTodayNotCached:
    def test_get_today_returns_none_even_if_file_somehow_exists(self):
        today = dpc._today_et()
        # set is a no-op for today
        dpc.set_cached_darkpool("AAPL", today, SAMPLE_TRADES)
        assert dpc.get_cached_darkpool("AAPL", today) is None

    def test_set_today_writes_nothing(self):
        today = dpc._today_et()
        dpc.set_cached_darkpool("AAPL", today, SAMPLE_TRADES)
        assert not dpc._path("AAPL", today).exists()


# ── empty / invalid payloads are not cached ─────────────────────────

class TestEmptyNotCached:
    def test_empty_list_not_cached(self):
        date = _yesterday()
        dpc.set_cached_darkpool("AAPL", date, [])
        assert not dpc._path("AAPL", date).exists()
        assert dpc.get_cached_darkpool("AAPL", date) is None

    def test_non_list_not_cached(self):
        date = _yesterday()
        dpc.set_cached_darkpool("AAPL", date, None)
        assert dpc.get_cached_darkpool("AAPL", date) is None


# ── integration: fetch_flow only re-fetches mutable (today) days ────

class TestFetchFlowUsesCache:
    """The P0 win: a second fetch_flow run must NOT re-hit UW for prior days."""

    def test_second_run_skips_immutable_days(self, monkeypatch):
        from unittest.mock import patch
        import fetch_flow

        calls = {"run": []}

        def fake_darkpool(ticker, date, _client=None, **_kwargs):
            calls["run"].append(date)
            return [{"price": 1.0, "size": 100, "premium": 100}]

        with patch.object(fetch_flow, "fetch_darkpool", side_effect=fake_darkpool), \
             patch.object(fetch_flow, "fetch_flow_alerts", return_value=[]):
            client = object()  # _do_fetch only passes it through to (mocked) fetchers

            # Run 1: cold cache — prior days are fetched and cached.
            calls["run"] = []
            fetch_flow.fetch_flow("AAPL", lookback_days=3, _client=client, skip_options_flow=True)
            run1 = list(calls["run"])
            run1_prior = [d for d in run1 if dpc.is_immutable(d)]
            assert run1_prior, "expected at least one prior (immutable) day fetched on the cold run"

            # Run 2: warm cache — prior days come from disk, never re-fetched.
            calls["run"] = []
            fetch_flow.fetch_flow("AAPL", lookback_days=3, _client=client, skip_options_flow=True)
            run2 = list(calls["run"])

        # Every fetch on run 2 must be a mutable (today) day — no immutable re-fetch.
        assert all(not dpc.is_immutable(d) for d in run2), (
            f"second run re-fetched immutable days: {[d for d in run2 if dpc.is_immutable(d)]}"
        )
        # And it fetched strictly fewer days than the cold run.
        assert len(run2) < len(run1)

    def test_cache_only_history_skips_cold_immutable_days(self, monkeypatch):
        from unittest.mock import patch
        import fetch_flow

        # Freeze the US session day. CI runners are UTC: after midnight UTC but
        # before the ET date rolls, wall-clock "today" disagrees with ET and
        # used to inject an extra calendar day into the fetch list.
        session_today = "2026-06-10"  # Wednesday
        session_prior = "2026-06-09"  # Tuesday
        session_now = dpc._ET.localize(datetime(2026, 6, 10, 15, 0, 0))
        monkeypatch.setattr(dpc, "_today_et", lambda: session_today)
        monkeypatch.setattr(fetch_flow, "_session_now_et", lambda: session_now)
        calls = []

        def fake_darkpool(ticker, date, _client=None, **_kwargs):
            calls.append(date)
            return [{"price": 1.0, "size": 100, "premium": 100}]

        with patch.object(
            fetch_flow,
            "get_last_n_trading_days",
            return_value=[session_today, session_prior],
        ), patch.object(
            fetch_flow, "fetch_darkpool", side_effect=fake_darkpool
        ), patch.object(fetch_flow, "fetch_flow_alerts", return_value=[]):
            result = fetch_flow.fetch_flow(
                "AAPL",
                lookback_days=2,
                _client=object(),
                skip_options_flow=True,
                fetch_missing_history=False,
            )

        assert calls == [session_today]
        assert result["history_backfill_skipped"] == [session_prior]

    def test_legacy_two_page_cache_is_refetched(self, monkeypatch):
        """A 976-print v2 row (SNDK 2026-09-04 shape) must not satisfy
        fetch_flow — that is how truncated discover walks froze into the
        20-session ticker report.
        """
        from unittest.mock import patch
        import fetch_flow

        session_today = "2026-09-10"
        session_prior = "2026-09-04"
        session_now = dpc._ET.localize(datetime(2026, 9, 10, 11, 23, 0))
        monkeypatch.setattr(dpc, "_today_et", lambda: session_today)
        monkeypatch.setattr(fetch_flow, "_session_now_et", lambda: session_now)

        path = dpc._path("SNDK", session_prior)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "ticker": "SNDK",
            "date": session_prior,
            "count": 976,
            "schema": 2,
            "trades": [{"price": 1, "size": 1, "premium": 1}] * 976,
        }))

        calls = []

        def fake_darkpool(ticker, date, _client=None, **_kwargs):
            calls.append(date)
            return [{"price": 1.0, "size": 100, "premium": 100}]

        with patch.object(
            fetch_flow,
            "get_last_n_trading_days",
            return_value=[session_today, session_prior],
        ), patch.object(
            fetch_flow, "fetch_darkpool", side_effect=fake_darkpool
        ), patch.object(fetch_flow, "fetch_flow_alerts", return_value=[]):
            fetch_flow.fetch_flow(
                "SNDK",
                lookback_days=2,
                _client=object(),
                skip_options_flow=True,
            )

        assert session_prior in calls
