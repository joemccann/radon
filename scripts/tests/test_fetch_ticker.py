"""Tests for fetch_ticker.py — ticker validation and caching."""
import json
import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock

from clients.uw_client import UWNotFoundError, UWAPIError

from fetch_ticker import (
    is_market_open,
    get_last_n_trading_days,
    load_cache,
    save_cache,
    get_cached_ticker,
    cache_ticker,
    fetch_ticker_info,
)


# ── Cache operations ────────────────────────────────────────────────

class TestCacheOperations:
    def test_load_cache_missing_file(self, tmp_path):
        with patch("fetch_ticker.CACHE_FILE", tmp_path / "missing.json"):
            result = load_cache()
            assert result == {"last_updated": None, "tickers": {}}

    def test_load_cache_corrupt_json(self, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not json")
        with patch("fetch_ticker.CACHE_FILE", bad_file):
            result = load_cache()
            assert result == {"last_updated": None, "tickers": {}}

    def test_save_and_load_roundtrip(self, tmp_path):
        cache_file = tmp_path / "ticker_cache.json"
        with patch("fetch_ticker.CACHE_FILE", cache_file):
            cache_ticker("AAPL", "Apple Inc.", "Technology")
            result = get_cached_ticker("AAPL")
            assert result is not None
            assert result["company_name"] == "Apple Inc."
            assert result["sector"] == "Technology"

    def test_get_cached_ticker_not_found(self, tmp_path):
        cache_file = tmp_path / "empty.json"
        cache_file.write_text(json.dumps({"last_updated": None, "tickers": {}}))
        with patch("fetch_ticker.CACHE_FILE", cache_file):
            assert get_cached_ticker("FAKE") is None

    def test_cache_ticker_uppercases(self, tmp_path):
        cache_file = tmp_path / "ticker_cache.json"
        with patch("fetch_ticker.CACHE_FILE", cache_file):
            cache_ticker("aapl", "Apple Inc.")
            assert list(load_cache()["tickers"]) == ["AAPL"]
            assert get_cached_ticker("aapl")["company_name"] == "Apple Inc."


# ── Market calendar ─────────────────────────────────────────────────

class TestMarketCalendar:
    def test_weekday(self):
        assert is_market_open(datetime(2026, 3, 3)) is True

    def test_weekend(self):
        assert is_market_open(datetime(2026, 3, 7)) is False

    def test_holiday(self):
        assert is_market_open(datetime(2026, 1, 19)) is False

    def test_trading_days_count(self):
        dt = datetime(2026, 3, 4, 17, 0)
        days = get_last_n_trading_days(3, dt)
        assert len(days) == 3


# ── fetch_ticker_info ───────────────────────────────────────────────

class TestFetchTickerInfo:
    @patch("fetch_ticker.UWClient")
    @patch("fetch_ticker.get_cached_ticker", return_value=None)
    def test_ticker_not_found_404(self, mock_cache, MockUWClient):
        mock_client = MagicMock()
        MockUWClient.return_value.__enter__ = MagicMock(return_value=mock_client)
        MockUWClient.return_value.__exit__ = MagicMock(return_value=False)
        mock_client.get_darkpool_flow.side_effect = UWNotFoundError("Not Found", status_code=404)
        result = fetch_ticker_info("FAKE")
        assert result["verified"] is False
        assert "not found" in result["error"].lower()

    @patch("fetch_ticker.UWClient")
    @patch("fetch_ticker.get_cached_ticker", return_value=None)
    def test_valid_ticker_with_dp_data(self, mock_cache, MockUWClient):
        mock_client = MagicMock()
        MockUWClient.return_value.__enter__ = MagicMock(return_value=mock_client)
        MockUWClient.return_value.__exit__ = MagicMock(return_value=False)
        mock_client.get_darkpool_flow.return_value = {"data": [
            {"size": "1000", "price": "150", "premium": "150000", "canceled": False},
        ]}
        mock_client.get_flow_alerts.return_value = {"data": [{"id": 1}]}
        result = fetch_ticker_info("AAPL")
        assert result["verified"] is True
        assert result["current_price"] == 150.0
        assert result["options_available"] is True

    @patch("fetch_ticker.UWClient")
    @patch("fetch_ticker.get_cached_ticker", return_value=None)
    def test_no_dp_activity(self, mock_cache, MockUWClient):
        mock_client = MagicMock()
        MockUWClient.return_value.__enter__ = MagicMock(return_value=mock_client)
        MockUWClient.return_value.__exit__ = MagicMock(return_value=False)
        mock_client.get_darkpool_flow.return_value = {"data": []}
        result = fetch_ticker_info("ILLIQUID")
        assert result["verified"] is False
        assert "No dark pool activity" in result["error"]

    @patch("fetch_ticker.UWClient")
    @patch("fetch_ticker.get_cached_ticker", return_value=None)
    def test_liquidity_low(self, mock_cache, MockUWClient):
        mock_client = MagicMock()
        MockUWClient.return_value.__enter__ = MagicMock(return_value=mock_client)
        MockUWClient.return_value.__exit__ = MagicMock(return_value=False)
        # 3 trading days, 5000 total volume -> avg < 10000
        trades = [{"size": "5000", "price": "10", "premium": "50000", "canceled": False}]
        mock_client.get_darkpool_flow.return_value = {"data": trades}
        mock_client.get_flow_alerts.return_value = {"data": []}
        result = fetch_ticker_info("ILLIQ")
        assert result["verified"] is True
        assert "LOW" in result.get("liquidity_warning", "")

    @patch("fetch_ticker.UWClient")
    @patch("fetch_ticker.get_cached_ticker", return_value=None)
    def test_liquidity_high(self, mock_cache, MockUWClient):
        mock_client = MagicMock()
        MockUWClient.return_value.__enter__ = MagicMock(return_value=mock_client)
        MockUWClient.return_value.__exit__ = MagicMock(return_value=False)
        trades = [{"size": "500000", "price": "100", "premium": "50000000", "canceled": False}]
        mock_client.get_darkpool_flow.return_value = {"data": trades}
        mock_client.get_flow_alerts.return_value = {"data": []}
        result = fetch_ticker_info("SPY")
        assert result["verified"] is True
        assert result.get("liquidity_warning") is None
        assert result.get("liquidity_note") == "HIGH - Active dark pool trading"
