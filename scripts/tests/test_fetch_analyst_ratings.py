"""Tests for fetch_analyst_ratings.py — signal calculation and data extraction."""
import json
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch

from fetch_analyst_ratings import (
    calculate_rating_signal,
    get_watchlist_tickers,
    get_portfolio_tickers,
    get_cached_rating,
    update_watchlist_with_ratings,
    format_ratings_table,
    CACHE_TTL_HOURS,
    merge_ratings_cache,
)


# ── calculate_rating_signal ─────────────────────────────────────────

class TestCalculateRatingSignal:
    def test_high_buy_pct_bullish(self):
        data = {
            "ratings": {"buy_pct": 80, "sell_pct": 5, "total": 25},
        }
        signal = calculate_rating_signal(data)
        assert signal["direction"] == "BULLISH"
        assert signal["strength"] >= 80

    def test_moderate_buy_lean_bullish(self):
        data = {
            "ratings": {"buy_pct": 55, "sell_pct": 10, "total": 15},
        }
        signal = calculate_rating_signal(data)
        assert signal["direction"] == "LEAN_BULLISH"

    def test_high_sell_pct_lean_bearish(self):
        data = {
            "ratings": {"buy_pct": 20, "sell_pct": 55, "total": 20},
        }
        signal = calculate_rating_signal(data)
        assert signal["direction"] == "LEAN_BEARISH"

    def test_neutral_direction(self):
        data = {
            "ratings": {"buy_pct": 30, "sell_pct": 30, "total": 10},
        }
        signal = calculate_rating_signal(data)
        assert signal["direction"] == "NEUTRAL"

    def test_high_analyst_count_high_confidence(self):
        data = {
            "ratings": {"buy_pct": 70, "sell_pct": 10, "total": 25},
        }
        signal = calculate_rating_signal(data)
        assert signal["confidence"] == "HIGH"

    def test_medium_analyst_count(self):
        data = {
            "ratings": {"buy_pct": 70, "sell_pct": 10, "total": 15},
        }
        signal = calculate_rating_signal(data)
        assert signal["confidence"] == "MEDIUM"

    def test_low_analyst_count_low_confidence(self):
        data = {
            "ratings": {"buy_pct": 70, "sell_pct": 10, "total": 5},
        }
        signal = calculate_rating_signal(data)
        assert signal["confidence"] == "LOW"

    def test_no_ratings_data(self):
        signal = calculate_rating_signal({"error": "no data"})
        assert signal["direction"] == "NEUTRAL"
        assert signal["confidence"] == "LOW"

    def test_zero_total_analysts(self):
        data = {"ratings": {"buy_pct": 0, "sell_pct": 0, "total": 0}}
        signal = calculate_rating_signal(data)
        assert signal["direction"] == "NEUTRAL"

    def test_upgrading_changes_signal(self):
        data = {
            "ratings": {"buy_pct": 70, "sell_pct": 10, "total": 20},
            "has_recent_changes": True,
            "recent_changes": [
                {"category": "buy", "change": 3},
                {"category": "sell", "change": 0},
            ],
        }
        signal = calculate_rating_signal(data)
        assert signal["changes_signal"] == "UPGRADING"

    def test_target_upside_note(self):
        data = {
            "ratings": {"buy_pct": 70, "sell_pct": 10, "total": 20},
            "target_upside_pct": 25.0,
        }
        signal = calculate_rating_signal(data)
        assert any("Bullish" in n for n in signal["notes"])


# ── get_watchlist_tickers ───────────────────────────────────────────

class TestGetWatchlistTickers:
    def test_extracts_tickers_from_db_reader(self):
        with patch("db.readers.read_watchlist_tickers", return_value=["AAPL", "MSFT", "NVDA"]) as reader:
            assert get_watchlist_tickers() == ["AAPL", "MSFT", "NVDA"]
        reader.assert_called_once_with()

    def test_empty_watchlist(self):
        with patch("db.readers.read_watchlist_tickers", return_value=[]):
            assert get_watchlist_tickers() == []


# ── get_portfolio_tickers ───────────────────────────────────────────

class TestGetPortfolioTickers:
    def test_extracts_tickers_from_latest_snapshot_reader(self):
        with patch("db.readers.read_portfolio_positions", return_value=[
            {"ticker": "AAPL"},
            {"ticker": "NVDA"},
            {"ticker": "AAPL"},
        ]) as reader:
            tickers = get_portfolio_tickers()
        reader.assert_called_once_with()
        assert tickers == ["AAPL", "NVDA"]


class TestUpdateWatchlistWithRatings:
    def test_updates_matching_watchlist_rows_in_db(self):
        with patch("db.readers.read_watchlist_items", return_value=[
            {"ticker": "AAPL", "sector": "Technology", "source": "manual"},
            {"ticker": "MSFT", "sector": "Technology", "source": "manual"},
        ]) as reader:
            with patch("db.writer.upsert_watchlist_ticker") as writer:
                update_watchlist_with_ratings(["AAPL"], {
                    "AAPL": {
                        "source": "uw",
                        "recommendation": "buy",
                        "ratings": {"buy_pct": 70, "total": 20},
                        "target_upside_pct": 18.5,
                    }
                })

        reader.assert_called_once_with()
        writer.assert_called_once()
        args, kwargs = writer.call_args
        assert args == ("AAPL",)
        assert kwargs["sector"] == "Technology"
        assert kwargs["source"] == "manual"
        assert kwargs["payload"]["analyst_ratings"]["source"] == "uw"
        assert kwargs["payload"]["analyst_ratings"]["recommendation"] == "buy"


# ── get_cached_rating ───────────────────────────────────────────────

class TestCachedRating:
    def test_concurrent_cache_updates_are_atomic_and_lossless(self, tmp_path):
        from concurrent.futures import ThreadPoolExecutor

        cache_file = tmp_path / "ratings.json"
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [
                pool.submit(
                    merge_ratings_cache,
                    cache_file,
                    {f"T{i}": {"ticker": f"T{i}", "ratings": {"buy": i}}},
                )
                for i in range(20)
            ]
            for future in futures:
                future.result()

        payload = json.loads(cache_file.read_text())
        assert sorted(payload["ratings"]) == sorted(f"T{i}" for i in range(20))

    def test_fresh_cache_returned(self, tmp_path):
        cache_file = tmp_path / "cache.json"
        fresh_time = datetime.now().isoformat()
        cache_file.write_text(json.dumps({
            "ratings": {
                "AAPL": {"fetched_at": fresh_time, "recommendation": "buy"}
            }
        }))
        with patch("fetch_analyst_ratings.RATINGS_CACHE_FILE", cache_file):
            result = get_cached_rating("AAPL")
            assert result is not None
            assert result["from_cache"] is True

    def test_stale_cache_returns_none(self, tmp_path):
        cache_file = tmp_path / "cache.json"
        stale_time = (datetime.now() - timedelta(hours=CACHE_TTL_HOURS + 1)).isoformat()
        cache_file.write_text(json.dumps({
            "ratings": {
                "AAPL": {"fetched_at": stale_time, "recommendation": "buy"}
            }
        }))
        with patch("fetch_analyst_ratings.RATINGS_CACHE_FILE", cache_file):
            result = get_cached_rating("AAPL")
            assert result is None

    def test_missing_ticker_returns_none(self, tmp_path):
        cache_file = tmp_path / "cache.json"
        cache_file.write_text(json.dumps({"ratings": {}}))
        with patch("fetch_analyst_ratings.RATINGS_CACHE_FILE", cache_file):
            assert get_cached_rating("FAKE") is None


# ── format_ratings_table ────────────────────────────────────────────

class TestFormatRatingsTable:
    def test_empty_results(self):
        output = format_ratings_table([])
        # Empty list with changes_only=False still returns header
        assert "ANALYST RATINGS" in output or "No analyst rating changes" in output

    def test_changes_only_filter(self):
        results = [
            {"ticker": "AAPL", "ratings": {"buy_pct": 70, "sell_pct": 5, "total": 20,
             "hold": 5}, "recommendation": "buy", "target_price": {"mean": 200},
             "has_recent_changes": False},
            {"ticker": "NVDA", "ratings": {"buy_pct": 80, "sell_pct": 5, "total": 30,
             "hold": 5}, "recommendation": "buy", "target_price": {"mean": 500},
             "has_recent_changes": True, "recent_changes": [
                 {"category": "buy", "previous": 25, "current": 28, "change": 3}
             ]},
        ]
        output = format_ratings_table(results, changes_only=True)
        assert "NVDA" in output

    def test_no_changes_message(self):
        output = format_ratings_table([], changes_only=True)
        assert "No analyst rating changes" in output
