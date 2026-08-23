"""Tests for discover.py outer ticker-level parallelization."""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch, call

import pytest

from clients.uw_client import UWRateLimitError, UWAPIError
from discover import discover, discover_targeted


# ── Helpers ──────────────────────────────────────────────────────────

def _mock_dp_result():
    return {
        "aggregate": {
            "buy_ratio": 0.7,
            "direction": "ACCUMULATION",
            "strength": 60.0,
            "prints": 100,
        },
        "daily": [],
        "sustained_days": 1,
        "total_prints": 100,
    }


def _mock_flow_alerts_response(ticker):
    return {
        "data": [
            {
                "ticker": ticker,
                "total_premium": "100000",
                "type": "CALL",
                "has_sweep": True,
                "volume_oi_ratio": "2.5",
                "sector": "Tech",
                "marketcap": 1e11,
                "underlying_price": 150,
                "issue_type": "Common Stock",
            }
        ]
    }


# ── Targeted mode concurrency ────────────────────────────────────────

class TestDiscoverTargetedParallel:
    """Verify discover_targeted uses ThreadPoolExecutor for tickers."""

    def test_multiple_tickers_use_thread_pool(self):
        """discover_targeted should process tickers via ThreadPoolExecutor."""
        tickers = [f"T{i}" for i in range(8)]
        thread_ids = set()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        def mock_flow_alerts(**kwargs):
            thread_ids.add(threading.current_thread().ident)
            time.sleep(0.02)
            t = kwargs.get("ticker", "UNK")
            return _mock_flow_alerts_response(t)

        mock_client.get_flow_alerts.side_effect = mock_flow_alerts

        with patch("discover.UWClient", return_value=mock_client), \
             patch("discover.fetch_darkpool_multi", return_value=_mock_dp_result()):
            result = discover_targeted(tickers)

        assert len(thread_ids) > 1, (
            f"Expected multiple threads but saw {len(thread_ids)}. "
            "discover_targeted is still sequential."
        )
        assert result["tickers_scanned"] == 8

    def test_results_collected_from_all_tickers(self):
        """All tickers should produce candidates."""
        tickers = ["AAPL", "GOOG", "MSFT"]
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        def mock_flow_alerts(**kwargs):
            t = kwargs.get("ticker", "UNK")
            return _mock_flow_alerts_response(t)

        mock_client.get_flow_alerts.side_effect = mock_flow_alerts

        with patch("discover.UWClient", return_value=mock_client), \
             patch("discover.fetch_darkpool_multi", return_value=_mock_dp_result()):
            result = discover_targeted(tickers, top=50)

        candidate_tickers = {c["ticker"] for c in result["candidates"]}
        assert candidate_tickers == {"AAPL", "GOOG", "MSFT"}


# ── Error handling ───────────────────────────────────────────────────

class TestDiscoverTargetedErrorHandling:
    """Per-ticker errors should not crash the batch."""

    def test_rate_limit_error_skips_ticker(self):
        """UWRateLimitError on one ticker doesn't crash the batch."""
        tickers = ["AAPL", "FAIL", "GOOG"]
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        call_count = {"value": 0}

        def mock_flow_alerts(**kwargs):
            call_count["value"] += 1
            t = kwargs.get("ticker", "UNK")
            if t == "FAIL":
                raise UWRateLimitError("rate limited", status_code=429)
            return _mock_flow_alerts_response(t)

        mock_client.get_flow_alerts.side_effect = mock_flow_alerts

        with patch("discover.UWClient", return_value=mock_client), \
             patch("discover.fetch_darkpool_multi", return_value=_mock_dp_result()):
            result = discover_targeted(tickers, top=50)

        # At least the non-failing tickers should produce candidates
        candidate_tickers = {c["ticker"] for c in result["candidates"]}
        assert "AAPL" in candidate_tickers
        assert "GOOG" in candidate_tickers

    def test_general_exception_caught_per_ticker(self):
        """A generic exception on one ticker doesn't crash the whole scan."""
        tickers = ["AAPL", "BOOM", "GOOG"]
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        def mock_flow_alerts(**kwargs):
            t = kwargs.get("ticker", "UNK")
            if t == "BOOM":
                raise RuntimeError("unexpected crash")
            return _mock_flow_alerts_response(t)

        mock_client.get_flow_alerts.side_effect = mock_flow_alerts

        with patch("discover.UWClient", return_value=mock_client), \
             patch("discover.fetch_darkpool_multi", return_value=_mock_dp_result()):
            result = discover_targeted(tickers, top=50)

        candidate_tickers = {c["ticker"] for c in result["candidates"]}
        assert "AAPL" in candidate_tickers
        assert "GOOG" in candidate_tickers


# ── Market-wide mode concurrency ─────────────────────────────────────

class TestDiscoverMarketWideParallel:
    """Market-wide mode already uses ThreadPoolExecutor; verify it still works."""

    def test_market_wide_processes_candidates_concurrently(self):
        """Market-wide mode should use ThreadPoolExecutor for DP checks."""
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        thread_ids = set()

        # Simulate flow alerts for 5 tickers
        alerts = []
        for t in ["AAA", "BBB", "CCC", "DDD", "EEE"]:
            alerts.append({
                "ticker": t,
                "total_premium": "600000",
                "type": "CALL",
                "has_sweep": True,
                "volume_oi_ratio": "2.0",
                "sector": "Tech",
                "marketcap": 1e11,
                "underlying_price": 100,
                "issue_type": "Common Stock",
            })

        mock_client.get_flow_alerts.return_value = {"data": alerts}

        def track_dp(ticker, days=3, _client=None, **_kwargs):
            thread_ids.add(threading.current_thread().ident)
            time.sleep(0.02)
            return _mock_dp_result()

        with patch("discover.UWClient", return_value=mock_client), \
             patch("discover.get_existing_tickers", return_value=set()), \
             patch("discover.fetch_darkpool_multi", side_effect=track_dp):
            result = discover(min_premium=500000, top=50)

        assert len(thread_ids) > 1, "Market-wide mode should use multiple threads"
        assert result["candidates_found"] == 5


# ── max_workers parameter ────────────────────────────────────────────

class TestDiscoverMaxWorkers:
    """Verify max_workers is configurable for discover_targeted."""

    def test_max_workers_parameter_used(self):
        """discover_targeted should accept and use max_workers parameter."""
        tickers = ["AAPL", "GOOG"]
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        def mock_flow_alerts(**kwargs):
            t = kwargs.get("ticker", "UNK")
            return _mock_flow_alerts_response(t)

        mock_client.get_flow_alerts.side_effect = mock_flow_alerts

        with patch("discover.UWClient", return_value=mock_client), \
             patch("discover.fetch_darkpool_multi", return_value=_mock_dp_result()), \
             patch("discover.ThreadPoolExecutor", wraps=ThreadPoolExecutor) as mock_exec:
            result = discover_targeted(tickers, max_workers=7)

        # The inner ThreadPoolExecutor for the outer ticker loop should use max_workers=7
        # (There may be nested calls from fetch_darkpool_multi but we patched that out)
        calls = [c for c in mock_exec.call_args_list if c[1].get("max_workers") == 7]
        assert len(calls) >= 1, "discover_targeted should pass max_workers to ThreadPoolExecutor"
