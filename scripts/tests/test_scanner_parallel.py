"""Tests for scanner.py parallel execution via ThreadPoolExecutor."""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from scanner import scan


# ── Helper: build a minimal watchlist ────────────────────────────────

def _make_watchlist(tickers):
    return {"tickers": [{"ticker": t, "sector": "Tech"} for t in tickers]}


def _make_flow_data(direction="ACCUMULATION", strength=60, prints=150):
    return {
        "dark_pool": {
            "aggregate": {
                "flow_direction": direction,
                "flow_strength": strength,
                "dp_buy_ratio": 0.7,
                "num_prints": prints,
            },
            "daily": [
                {"flow_direction": direction, "flow_strength": strength},
            ],
        }
    }


# ── Concurrency ──────────────────────────────────────────────────────

class TestScannerParallelExecution:
    """Verify that scan() processes tickers concurrently via ThreadPoolExecutor."""

    @patch("scanner.get_open_positions", return_value=set())
    def test_multiple_tickers_processed_concurrently(self, mock_positions):
        """Multiple tickers should be processed in parallel, not sequentially."""
        tickers = [f"T{i}" for i in range(10)]
        watchlist_items = _make_watchlist(tickers)["tickers"]

        # Track which threads are used
        thread_ids = set()

        def slow_fetch(ticker, days=5):
            thread_ids.add(threading.current_thread().ident)
            time.sleep(0.05)
            return _make_flow_data()

        with patch("scanner.get_watchlist_items", return_value=watchlist_items), \
             patch("scanner.fetch_flow_data", side_effect=slow_fetch):
            scan(top_n=50)

        # With parallel execution and 10 tickers, we expect multiple threads
        assert len(thread_ids) > 1, (
            f"Expected multiple threads but only saw {len(thread_ids)}. "
            "scan() is still sequential."
        )

    @patch("scanner.get_open_positions", return_value=set())
    def test_results_collected_from_all_tickers(self, mock_positions, capsys):
        """All successful tickers should appear in results."""
        tickers = ["AAPL", "GOOG", "MSFT", "NVDA", "AMD"]
        watchlist_items = _make_watchlist(tickers)["tickers"]

        with patch("scanner.get_watchlist_items", return_value=watchlist_items), \
             patch("scanner.fetch_flow_data", return_value=_make_flow_data()):
            scan(top_n=50)

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["tickers_scanned"] == 5
        result_tickers = {r["ticker"] for r in output["top_signals"]}
        assert result_tickers == set(tickers)


# ── Error handling ───────────────────────────────────────────────────

class TestScannerErrorHandling:
    """Verify per-ticker errors don't crash the batch."""

    @patch("scanner.get_open_positions", return_value=set())
    def test_rate_limit_error_skips_ticker(self, mock_positions, capsys):
        """UWRateLimitError on one ticker should not crash the batch."""
        from clients.uw_client import UWRateLimitError

        tickers = ["AAPL", "FAIL", "GOOG"]
        watchlist_items = _make_watchlist(tickers)["tickers"]

        def fetch_side_effect(ticker, days=5):
            if ticker == "FAIL":
                raise UWRateLimitError("rate limited", status_code=429)
            return _make_flow_data()

        with patch("scanner.get_watchlist_items", return_value=watchlist_items), \
             patch("scanner.fetch_flow_data", side_effect=fetch_side_effect):
            scan(top_n=50)

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        # AAPL and GOOG should succeed; FAIL should be skipped or show error
        assert output["tickers_scanned"] >= 2

    @patch("scanner.get_open_positions", return_value=set())
    def test_general_exception_caught_per_ticker(self, mock_positions, capsys):
        """A generic exception on one ticker should not crash the whole scan."""
        tickers = ["AAPL", "BOOM", "GOOG"]
        watchlist_items = _make_watchlist(tickers)["tickers"]

        def fetch_side_effect(ticker, days=5):
            if ticker == "BOOM":
                raise RuntimeError("unexpected crash")
            return _make_flow_data()

        with patch("scanner.get_watchlist_items", return_value=watchlist_items), \
             patch("scanner.fetch_flow_data", side_effect=fetch_side_effect):
            scan(top_n=50)

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["tickers_scanned"] >= 2


# ── max_workers parameter ────────────────────────────────────────────

class TestScannerMaxWorkers:
    """Verify max_workers parameter is respected."""

    @patch("scanner.get_open_positions", return_value=set())
    def test_max_workers_parameter_passed_to_executor(self, mock_positions, capsys):
        """scan() should accept and pass max_workers to ThreadPoolExecutor."""
        tickers = ["AAPL", "GOOG"]
        watchlist_items = _make_watchlist(tickers)["tickers"]

        with patch("scanner.get_watchlist_items", return_value=watchlist_items), \
             patch("scanner.fetch_flow_data", return_value=_make_flow_data()), \
             patch("scanner.ThreadPoolExecutor", wraps=ThreadPoolExecutor) as mock_executor:
            scan(top_n=50, max_workers=5)

        mock_executor.assert_called_once_with(max_workers=5)
