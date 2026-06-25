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

    def test_cache_only_history_skips_cold_immutable_days(self):
        from unittest.mock import patch
        import fetch_flow

        today = dpc._today_et()
        prior = _yesterday()
        calls = []

        def fake_darkpool(ticker, date, _client=None, **_kwargs):
            calls.append(date)
            return [{"price": 1.0, "size": 100, "premium": 100}]

        with patch.object(fetch_flow, "get_last_n_trading_days", return_value=[today, prior]), \
             patch.object(fetch_flow, "fetch_darkpool", side_effect=fake_darkpool), \
             patch.object(fetch_flow, "fetch_flow_alerts", return_value=[]):
            result = fetch_flow.fetch_flow(
                "AAPL",
                lookback_days=2,
                _client=object(),
                skip_options_flow=True,
                fetch_missing_history=False,
            )

        assert calls == [today]
        assert result["history_backfill_skipped"] == [prior]
