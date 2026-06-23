"""Tests for fetch_x_watchlist.py — ticker extraction and sentiment analysis."""
import pytest
from unittest.mock import patch

from fetch_x_watchlist import (
    extract_tickers,
    analyze_sentiment,
    update_watchlist,
    COMMON_TICKERS,
    EXCLUDE_WORDS,
)


# ── extract_tickers ─────────────────────────────────────────────────

class TestExtractTickers:
    def test_cashtag_extraction(self):
        tickers = extract_tickers("Looking at $AAPL and $MSFT today")
        assert "AAPL" in tickers
        assert "MSFT" in tickers

    def test_known_ticker_without_dollar(self):
        tickers = extract_tickers("NVDA is breaking out")
        assert "NVDA" in tickers

    def test_exclude_common_words(self):
        tickers = extract_tickers("THE CEO said NOT to USE this")
        for word in ["THE", "CEO", "NOT", "USE"]:
            assert word not in tickers

    def test_cashtags_preserved_even_if_excluded(self):
        # $DD is in EXCLUDE_WORDS but with $ it should be kept
        tickers = extract_tickers("$DD looking good today")
        assert "DD" in tickers

    def test_empty_text(self):
        tickers = extract_tickers("")
        assert tickers == []

    def test_no_tickers_in_normal_text(self):
        tickers = extract_tickers("just a normal sentence about nothing")
        assert tickers == []

    def test_multiple_cashtags(self):
        tickers = extract_tickers("$ON $SLAB $ALGM all semi plays")
        assert "ON" in tickers
        assert "SLAB" in tickers
        assert "ALGM" in tickers

    def test_known_ticker_case_insensitive(self):
        tickers = extract_tickers("nvda is running")
        assert "NVDA" in tickers

    def test_mixed_cashtag_and_known(self):
        tickers = extract_tickers("$MPWR and RMBS both looking strong")
        assert "MPWR" in tickers
        assert "RMBS" in tickers


# ── analyze_sentiment ───────────────────────────────────────────────

class TestAnalyzeSentiment:
    def test_strong_bullish(self):
        sentiment, confidence = analyze_sentiment(
            "Buying $NVDA calls, this is going to the moon! Bullish breakout!",
            "NVDA"
        )
        assert sentiment == "BULLISH"
        assert confidence == "HIGH"

    def test_strong_bearish(self):
        sentiment, confidence = analyze_sentiment(
            "Selling puts on AAPL, this is going to crash. Bearish breakdown, ugly chart",
            "AAPL"
        )
        assert sentiment == "BEARISH"
        assert confidence == "HIGH"

    def test_neutral_balanced(self):
        sentiment, confidence = analyze_sentiment(
            "Watching MSFT, could go either way from here",
            "MSFT"
        )
        assert sentiment == "NEUTRAL"
        assert confidence == "LOW"

    def test_weak_bullish(self):
        sentiment, confidence = analyze_sentiment(
            "Interesting potential in NVDA, watching closely for support bounce",
            "NVDA"
        )
        # weak signals only → bullish but low confidence
        assert sentiment == "BULLISH"

    def test_weak_bearish(self):
        sentiment, confidence = analyze_sentiment(
            "Caution on AAPL, overbought and extended here. Struggling with resistance",
            "AAPL"
        )
        assert sentiment == "BEARISH"

    def test_empty_text_neutral(self):
        sentiment, confidence = analyze_sentiment("", "AAPL")
        assert sentiment == "NEUTRAL"
        assert confidence == "LOW"

    def test_medium_confidence_bullish(self):
        sentiment, confidence = analyze_sentiment(
            "Loading up on AAPL here, great entry",
            "AAPL"
        )
        assert sentiment == "BULLISH"
        assert confidence in ("MEDIUM", "HIGH")

    def test_medium_confidence_bearish(self):
        sentiment, confidence = analyze_sentiment(
            "Selling AAPL, taking profits now",
            "AAPL"
        )
        assert sentiment == "BEARISH"
        assert confidence in ("MEDIUM", "HIGH")


class TestUpdateWatchlist:
    def test_update_watchlist_upserts_turso_rows(self):
        tweets = [
            {"text": "Loading $NVDA calls here", "tickers": ["NVDA"]},
            {"text": "Watching MSFT for support", "tickers": ["MSFT"]},
        ]
        with patch("db.readers.read_watchlist_items", return_value=[
            {"ticker": "MSFT", "sector": "Technology", "source": "@old"},
        ]):
            with patch("db.writer.upsert_watchlist_ticker") as writer:
                new_tickers, updated_tickers = update_watchlist("trader", tweets)

        assert new_tickers == ["NVDA"]
        assert updated_tickers == ["MSFT"]
        written = {call.args[0]: call.kwargs for call in writer.call_args_list}
        assert set(written) == {"NVDA", "MSFT"}
        assert written["NVDA"]["source"] == "@trader"
        assert written["MSFT"]["payload"]["sentiment_history"]

    def test_xai_update_watchlist_upserts_turso_rows(self):
        from fetch_x_xai import update_watchlist as update_xai_watchlist

        tweets = [
            {
                "text": "Bullish $AAPL setup",
                "tickers": ["AAPL"],
                "sentiment_override": "BULLISH",
            }
        ]
        with patch("db.readers.read_watchlist_items", return_value=[]):
            with patch("db.writer.upsert_watchlist_ticker") as writer:
                new_tickers, updated_tickers = update_xai_watchlist("xaiuser", tweets)

        assert new_tickers == ["AAPL"]
        assert updated_tickers == []
        writer.assert_called_once()
        assert writer.call_args.args == ("AAPL",)
        assert writer.call_args.kwargs["source"] == "@xaiuser"
        assert writer.call_args.kwargs["payload"]["sentiment"] == "BULLISH"
