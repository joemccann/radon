"""Tests for fetch_flow.py — dark pool + options flow analysis."""
import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock

from fetch_flow import (
    is_market_open,
    get_last_n_trading_days,
    analyze_darkpool,
    analyze_options_flow,
    MARKET_HOLIDAYS_2026,
)


# ── is_market_open ──────────────────────────────────────────────────

class TestIsMarketOpen:
    def test_weekday_open(self):
        # Tuesday 2026-03-03
        assert is_market_open(datetime(2026, 3, 3)) is True

    def test_saturday_closed(self):
        assert is_market_open(datetime(2026, 3, 7)) is False

    def test_sunday_closed(self):
        assert is_market_open(datetime(2026, 3, 8)) is False

    def test_holiday_closed(self):
        # MLK Day 2026
        assert is_market_open(datetime(2026, 1, 19)) is False

    def test_christmas_closed(self):
        assert is_market_open(datetime(2026, 12, 25)) is False


# ── get_last_n_trading_days ─────────────────────────────────────────

class TestGetLastNTradingDays:
    def test_returns_n_days(self):
        # Wednesday at 17:00 (after close)
        dt = datetime(2026, 3, 4, 17, 0)
        days = get_last_n_trading_days(3, dt)
        assert len(days) == 3

    def test_skips_weekends(self):
        # Monday at 10am (before close) — should start from previous Friday
        dt = datetime(2026, 3, 2, 10, 0)
        days = get_last_n_trading_days(1, dt)
        assert days[0] == "2026-02-27"  # Friday

    def test_skips_holidays(self):
        # Day after MLK Day (Tuesday 2026-01-20 at 10am)
        dt = datetime(2026, 1, 20, 10, 0)
        days = get_last_n_trading_days(2, dt)
        # Should skip MLK Day (Jan 19) and weekend
        assert "2026-01-19" not in days
        assert len(days) == 2


# ── analyze_darkpool ────────────────────────────────────────────────

class TestAnalyzeDarkpool:
    def test_empty_trades_no_data(self):
        result = analyze_darkpool([])
        assert result["flow_direction"] == "NO_DATA"
        assert result["total_volume"] == 0
        assert result["dp_buy_ratio"] is None

    def test_canceled_trades_skipped(self):
        trades = [
            {"size": "1000", "price": "50", "premium": "50000",
             "nbbo_bid": "49", "nbbo_ask": "51", "canceled": True},
        ]
        result = analyze_darkpool(trades)
        assert result["num_prints"] == 0
        assert result["total_volume"] == 0

    def test_strong_buy_accumulation(self):
        # All trades above midpoint -> buy volume
        trades = [
            {"size": "1000", "price": "51", "premium": "51000",
             "nbbo_bid": "49", "nbbo_ask": "51"},
            {"size": "2000", "price": "50.5", "premium": "101000",
             "nbbo_bid": "49", "nbbo_ask": "51"},
        ]
        result = analyze_darkpool(trades)
        assert result["flow_direction"] == "ACCUMULATION"
        assert result["dp_buy_ratio"] == 1.0
        assert result["flow_strength"] == 100.0

    def test_strong_sell_distribution(self):
        # All trades below midpoint -> sell volume
        trades = [
            {"size": "1000", "price": "49", "premium": "49000",
             "nbbo_bid": "49", "nbbo_ask": "51"},
            {"size": "2000", "price": "49.5", "premium": "99000",
             "nbbo_bid": "49", "nbbo_ask": "51"},
        ]
        result = analyze_darkpool(trades)
        assert result["flow_direction"] == "DISTRIBUTION"
        assert result["dp_buy_ratio"] == 0.0
        assert result["flow_strength"] == 100.0

    def test_balanced_neutral(self):
        # Equal buy/sell -> NEUTRAL
        trades = [
            {"size": "1000", "price": "51", "premium": "51000",
             "nbbo_bid": "49", "nbbo_ask": "51"},
            {"size": "1000", "price": "49", "premium": "49000",
             "nbbo_bid": "49", "nbbo_ask": "51"},
        ]
        result = analyze_darkpool(trades)
        assert result["flow_direction"] == "NEUTRAL"
        assert result["dp_buy_ratio"] == 0.5
        # Perfectly balanced -> 0 magnitude.
        assert result["flow_strength"] == 0.0

    def test_neutral_high_lean_strength(self):
        # 54% buy / 46% sell -> classified NEUTRAL, but column should reflect
        # the actual magnitude of the lean rather than collapse to 0.
        # buy_ratio == 0.54  ->  |0.54 - 0.5| * 200 == 8.0
        trades = [
            {"size": "5400", "price": "51", "premium": "275400",
             "nbbo_bid": "49", "nbbo_ask": "51"},
            {"size": "4600", "price": "49", "premium": "225400",
             "nbbo_bid": "49", "nbbo_ask": "51"},
        ]
        result = analyze_darkpool(trades)
        assert result["flow_direction"] == "NEUTRAL"
        assert result["dp_buy_ratio"] == 0.54
        assert result["flow_strength"] == 8.0

    def test_neutral_low_lean_strength(self):
        # 46% buy / 54% sell -> NEUTRAL with bearish lean.
        # buy_ratio == 0.46  ->  |0.46 - 0.5| * 200 == 8.0
        trades = [
            {"size": "4600", "price": "51", "premium": "234600",
             "nbbo_bid": "49", "nbbo_ask": "51"},
            {"size": "5400", "price": "49", "premium": "264600",
             "nbbo_bid": "49", "nbbo_ask": "51"},
        ]
        result = analyze_darkpool(trades)
        assert result["flow_direction"] == "NEUTRAL"
        assert result["dp_buy_ratio"] == 0.46
        assert result["flow_strength"] == 8.0

    def test_no_nbbo_unknown(self):
        trades = [
            {"size": "1000", "price": "50", "premium": "50000",
             "nbbo_bid": "0", "nbbo_ask": "0"},
        ]
        result = analyze_darkpool(trades)
        assert result["flow_direction"] == "UNKNOWN"
        assert result["dp_buy_ratio"] is None

    def test_num_prints_excludes_canceled(self):
        trades = [
            {"size": "1000", "price": "51", "premium": "51000",
             "nbbo_bid": "49", "nbbo_ask": "51"},
            {"size": "500", "price": "49", "premium": "24500",
             "nbbo_bid": "49", "nbbo_ask": "51", "canceled": True},
        ]
        result = analyze_darkpool(trades)
        assert result["num_prints"] == 1


# ── analyze_options_flow ────────────────────────────────────────────

class TestAnalyzeOptionsFlow:
    def test_empty_alerts_no_data(self):
        result = analyze_options_flow([])
        assert result["bias"] == "NO_DATA"
        assert result["total_alerts"] == 0
        assert result["put_call_ratio"] is None
        assert "call_put_ratio" not in result

    def test_all_calls_bias(self):
        alerts = [
            {"premium": "100000", "is_call": True},
            {"premium": "200000", "is_call": True},
        ]
        result = analyze_options_flow(alerts)
        assert result["bias"] == "ALL_CALLS"
        assert result["put_call_ratio"] == 0.0

    def test_all_puts_keeps_ratio_json_safe(self):
        alerts = [
            {"premium": "100000", "is_call": False},
            {"premium": "200000", "is_call": False},
        ]
        result = analyze_options_flow(alerts)
        assert result["bias"] == "STRONGLY_BEARISH"
        assert result["put_call_ratio"] is None

    def test_strongly_bullish(self):
        # Call premium >> Put premium (put/call ratio <= 0.5)
        alerts = [
            {"premium": "300000", "is_call": True},
            {"premium": "100000", "is_call": False},
        ]
        result = analyze_options_flow(alerts)
        assert result["bias"] == "STRONGLY_BULLISH"
        assert result["put_call_ratio"] == 0.33

    def test_strongly_bearish(self):
        # Put premium >> Call premium (put/call ratio >= 2.0)
        alerts = [
            {"premium": "100000", "is_call": True},
            {"premium": "400000", "is_call": False},
        ]
        result = analyze_options_flow(alerts)
        assert result["bias"] == "STRONGLY_BEARISH"
        assert result["put_call_ratio"] == 4.0

    def test_bullish(self):
        # Put/call ratio between 0.5 and 0.83
        alerts = [
            {"premium": "150000", "is_call": True},
            {"premium": "100000", "is_call": False},
        ]
        result = analyze_options_flow(alerts)
        assert result["bias"] == "BULLISH"
        assert result["put_call_ratio"] == 0.67

    def test_bearish(self):
        # Put/call ratio between 1.25 and 2.0
        alerts = [
            {"premium": "70000", "is_call": True},
            {"premium": "100000", "is_call": False},
        ]
        result = analyze_options_flow(alerts)
        assert result["bias"] == "BEARISH"
        assert result["put_call_ratio"] == 1.43

    def test_neutral(self):
        # Put/call ratio between 0.83 and 1.25
        alerts = [
            {"premium": "100000", "is_call": True},
            {"premium": "100000", "is_call": False},
        ]
        result = analyze_options_flow(alerts)
        assert result["bias"] == "NEUTRAL"
        assert result["put_call_ratio"] == 1.0

    def test_real_uw_field_names(self):
        # UW /option-trades/flow-alerts returns total_premium (not premium)
        # and type ("call"/"put") (not is_call). Without this, GOOGL on prod
        # returned total_alerts:100 with bias:NO_DATA.
        alerts = [
            {"total_premium": "300000", "type": "call"},
            {"total_premium": "100000", "type": "put"},
        ]
        result = analyze_options_flow(alerts)
        assert result["total_premium"] == 400000.0
        assert result["call_premium"] == 300000.0
        assert result["put_premium"] == 100000.0
        assert result["bias"] == "STRONGLY_BULLISH"
        assert result["put_call_ratio"] == 0.33
        assert "call_put_ratio" not in result


# ── fetch_flow combined signal ──────────────────────────────────────

class TestFetchFlowCombinedSignal:
    """Test the combined signal logic from fetch_flow() by mocking I/O."""

    @patch("fetch_flow.UWClient")
    @patch("fetch_flow.fetch_flow_alerts", return_value=[])
    @patch("fetch_flow.fetch_darkpool", return_value=[])
    @patch("fetch_flow.get_last_n_trading_days", return_value=["2026-03-02"])
    def test_no_data_no_signal(self, mock_days, mock_dp, mock_flow, MockUWClient):
        mock_client = MagicMock()
        MockUWClient.return_value.__enter__ = MagicMock(return_value=mock_client)
        MockUWClient.return_value.__exit__ = MagicMock(return_value=False)
        from fetch_flow import fetch_flow
        result = fetch_flow("AAPL", lookback_days=1)
        assert result["combined_signal"] == "NO_SIGNAL"

    @patch("fetch_flow.UWClient")
    @patch("fetch_flow.fetch_flow_alerts")
    @patch("fetch_flow.fetch_darkpool")
    @patch("fetch_flow.get_last_n_trading_days", return_value=["2026-03-02"])
    def test_bullish_confluence(self, mock_days, mock_dp, mock_flow, MockUWClient):
        mock_client = MagicMock()
        MockUWClient.return_value.__enter__ = MagicMock(return_value=mock_client)
        MockUWClient.return_value.__exit__ = MagicMock(return_value=False)
        from fetch_flow import fetch_flow
        # DP: strong buy -> ACCUMULATION
        mock_dp.return_value = [
            {"size": "5000", "price": "51", "premium": "255000",
             "nbbo_bid": "49", "nbbo_ask": "51"},
        ]
        # Options: strongly bullish
        mock_flow.return_value = [
            {"premium": "300000", "is_call": True},
            {"premium": "100000", "is_call": False},
        ]
        result = fetch_flow("AAPL", lookback_days=1)
        assert result["combined_signal"] == "STRONG_BULLISH_CONFLUENCE"

    @patch("fetch_flow.UWClient")
    @patch("fetch_flow.fetch_flow_alerts")
    @patch("fetch_flow.fetch_darkpool")
    @patch("fetch_flow.get_last_n_trading_days", return_value=["2026-03-02"])
    def test_dp_only_signal(self, mock_days, mock_dp, mock_flow, MockUWClient):
        mock_client = MagicMock()
        MockUWClient.return_value.__enter__ = MagicMock(return_value=mock_client)
        MockUWClient.return_value.__exit__ = MagicMock(return_value=False)
        from fetch_flow import fetch_flow
        mock_dp.return_value = [
            {"size": "5000", "price": "51", "premium": "255000",
             "nbbo_bid": "49", "nbbo_ask": "51"},
        ]
        mock_flow.return_value = []
        result = fetch_flow("AAPL", lookback_days=1)
        assert result["combined_signal"] == "DP_ACCUMULATION_ONLY"


class TestDarkpoolPagination:
    """UW hard-caps each darkpool response at 500. Liquid names (GLD)
    need older_than cursor walks so Daily Dark Pool History is not stuck
    at 500 prints forever.
    """

    def _trade(self, i: int, executed_at: str) -> dict:
        return {
            "size": 100 + i,
            "price": 50.0,
            "premium": 5000,
            "nbbo_bid": 49.9,
            "nbbo_ask": 50.1,
            "executed_at": executed_at,
            "tracking_id": i,
        }

    def test_single_short_page_one_call(self):
        from fetch_flow import DARKPOOL_PAGE_LIMIT, fetch_darkpool

        mock_client = MagicMock()
        page = [self._trade(i, f"2026-08-06T14:00:{i:02d}Z") for i in range(3)]
        mock_client.get_darkpool_flow.return_value = {"data": page}

        result = fetch_darkpool("GLD", date="2026-08-06", _client=mock_client)

        assert result == page
        assert mock_client.get_darkpool_flow.call_count == 1
        kwargs = mock_client.get_darkpool_flow.call_args.kwargs
        assert kwargs.get("limit") == DARKPOOL_PAGE_LIMIT
        assert kwargs.get("older_than") is None

    def test_full_page_then_partial_walks_older_than(self):
        from fetch_flow import DARKPOOL_PAGE_LIMIT, _darkpool_page_cursor, fetch_darkpool

        # Strictly desc timestamps so cursor is unambiguous.
        page1 = [
            self._trade(i, f"2026-08-06T16:{59 - (i // 60):02d}:{59 - (i % 60):02d}Z")
            for i in range(DARKPOOL_PAGE_LIMIT)
        ]
        oldest_p1 = _darkpool_page_cursor(page1)
        page2 = [
            self._trade(500 + i, f"2026-08-06T12:00:{i:02d}Z") for i in range(42)
        ]
        mock_client = MagicMock()
        mock_client.get_darkpool_flow.side_effect = [
            {"data": page1},
            {"data": page2},
        ]

        result = fetch_darkpool("GLD", date="2026-08-06", _client=mock_client)

        assert len(result) == DARKPOOL_PAGE_LIMIT + 42
        assert mock_client.get_darkpool_flow.call_count == 2
        second = mock_client.get_darkpool_flow.call_args_list[1].kwargs
        assert second["older_than"] == oldest_p1
        assert second["date"] == "2026-08-06"
        assert second["limit"] == DARKPOOL_PAGE_LIMIT

    def test_three_full_pages_then_empty_stops(self):
        from fetch_flow import DARKPOOL_PAGE_LIMIT, _darkpool_page_cursor, fetch_darkpool

        pages = []
        for p in range(3):
            pages.append([
                self._trade(
                    p * DARKPOOL_PAGE_LIMIT + i,
                    f"2026-08-06T{16 - p:02d}:{59 - (i // 60):02d}:{59 - (i % 60):02d}Z",
                )
                for i in range(DARKPOOL_PAGE_LIMIT)
            ])
        mock_client = MagicMock()
        mock_client.get_darkpool_flow.side_effect = [
            {"data": pages[0]},
            {"data": pages[1]},
            {"data": pages[2]},
            {"data": []},
        ]

        result = fetch_darkpool("GLD", date="2026-08-06", _client=mock_client)

        assert len(result) == 3 * DARKPOOL_PAGE_LIMIT
        assert mock_client.get_darkpool_flow.call_count == 4
        assert mock_client.get_darkpool_flow.call_args_list[3].kwargs["older_than"] == (
            _darkpool_page_cursor(pages[2])
        )

    def test_stops_when_cursor_cannot_advance(self):
        """If UW returns a full page without a usable older cursor, stop."""
        from fetch_flow import DARKPOOL_PAGE_LIMIT, fetch_darkpool

        page = [{"size": 1, "price": 1.0, "premium": 1} for _ in range(DARKPOOL_PAGE_LIMIT)]
        mock_client = MagicMock()
        mock_client.get_darkpool_flow.return_value = {"data": page}

        result = fetch_darkpool("GLD", date="2026-08-06", _client=mock_client)

        assert len(result) == DARKPOOL_PAGE_LIMIT
        assert mock_client.get_darkpool_flow.call_count == 1


class TestUWRetryPolicy:
    """Regression for the 2026-05-15 EWY incident: a transient UW
    rate-limit on per-day darkpool calls was silently swallowed and
    converted to []. Three healthy days kept the aggregate positive so
    the cache-write gate at server.py:1026 waved the partial report
    through, leaving a "NO DATA" view on healthy tickers for 600s.

    After the fix:
      - UWNotFoundError → return [] (legit empty)
      - UWRateLimitError / UWServerError → one 2s retry, then re-raise
      - Other UWAPIError → re-raise immediately
    """

    def test_darkpool_not_found_returns_empty_no_retry(self):
        from clients.uw_client import UWNotFoundError
        from fetch_flow import fetch_darkpool

        mock_client = MagicMock()
        mock_client.get_darkpool_flow.side_effect = UWNotFoundError(
            "no data for date", status_code=404
        )

        result = fetch_darkpool("XYZ", date="2026-05-15", _client=mock_client)

        assert result == []
        assert mock_client.get_darkpool_flow.call_count == 1, "no retry on NotFound"

    def test_darkpool_rate_limit_retries_once_then_succeeds(self):
        from clients.uw_client import UWRateLimitError
        from fetch_flow import fetch_darkpool

        mock_client = MagicMock()
        mock_client.get_darkpool_flow.side_effect = [
            UWRateLimitError("429", status_code=429),
            {"data": [{"size": 100, "price": 50.0}]},
        ]

        with patch("fetch_flow.time_module.sleep"):
            result = fetch_darkpool("EWY", date="2026-05-15", _client=mock_client)

        assert result == [{"size": 100, "price": 50.0}]
        assert mock_client.get_darkpool_flow.call_count == 2

    def test_darkpool_rate_limit_can_fail_fast_without_retry(self):
        from clients.uw_client import UWRateLimitError
        from fetch_flow import fetch_darkpool

        mock_client = MagicMock()
        mock_client.get_darkpool_flow.side_effect = UWRateLimitError("429", status_code=429)

        with patch("fetch_flow.time_module.sleep") as sleep_mock, \
             pytest.raises(UWRateLimitError):
            fetch_darkpool("EWY", date="2026-05-15", _client=mock_client, retry_transient=False)

        sleep_mock.assert_not_called()
        assert mock_client.get_darkpool_flow.call_count == 1

    def test_darkpool_rate_limit_persistent_raises(self):
        """If the retry ALSO 429s, propagate. The caller (flow_report)
        will surface this so server.py 502s the POST and the cache is
        never written.
        """
        from clients.uw_client import UWRateLimitError
        from fetch_flow import fetch_darkpool

        mock_client = MagicMock()
        mock_client.get_darkpool_flow.side_effect = UWRateLimitError(
            "429", status_code=429
        )

        with patch("fetch_flow.time_module.sleep"), \
             pytest.raises(UWRateLimitError):
            fetch_darkpool("EWY", date="2026-05-15", _client=mock_client)

    def test_darkpool_server_error_retries_once_then_succeeds(self):
        from clients.uw_client import UWServerError
        from fetch_flow import fetch_darkpool

        mock_client = MagicMock()
        mock_client.get_darkpool_flow.side_effect = [
            UWServerError("503", status_code=503),
            {"data": []},
        ]

        with patch("fetch_flow.time_module.sleep"):
            result = fetch_darkpool("EWY", date="2026-05-15", _client=mock_client)

        assert result == []
        assert mock_client.get_darkpool_flow.call_count == 2

    def test_darkpool_auth_error_raises_immediately_no_retry(self):
        """Auth failures must NOT silently empty the result. Re-raise
        so the operator sees the configuration problem.
        """
        from clients.uw_client import UWAuthError
        from fetch_flow import fetch_darkpool

        mock_client = MagicMock()
        mock_client.get_darkpool_flow.side_effect = UWAuthError(
            "401 token expired", status_code=401
        )

        with pytest.raises(UWAuthError):
            fetch_darkpool("EWY", date="2026-05-15", _client=mock_client)
        assert mock_client.get_darkpool_flow.call_count == 1

    def test_flow_alerts_inherits_same_retry_policy(self):
        from clients.uw_client import UWRateLimitError
        from fetch_flow import fetch_flow_alerts

        mock_client = MagicMock()
        mock_client.get_flow_alerts.side_effect = [
            UWRateLimitError("429", status_code=429),
            {"data": [{"ticker": "AAPL", "created_at": "2026-08-07T15:00:00Z"}]},
        ]

        with patch("fetch_flow.time_module.sleep"):
            result = fetch_flow_alerts("AAPL", _client=mock_client)

        assert result == [{"ticker": "AAPL", "created_at": "2026-08-07T15:00:00Z"}]
        assert mock_client.get_flow_alerts.call_count == 2


class TestFlowAlertsPagination:
    def test_single_short_page(self):
        from fetch_flow import FLOW_ALERTS_PAGE_LIMIT, fetch_flow_alerts

        page = [
            {
                "ticker": "AAPL",
                "option_chain": f"AAPL{i}",
                "created_at": f"2026-08-07T15:00:{i:02d}Z",
                "total_premium": "100000",
            }
            for i in range(3)
        ]
        mock_client = MagicMock()
        mock_client.get_flow_alerts.return_value = {"data": page}

        result = fetch_flow_alerts("AAPL", _client=mock_client)

        assert len(result) == 3
        assert mock_client.get_flow_alerts.call_count == 1
        kwargs = mock_client.get_flow_alerts.call_args.kwargs
        assert kwargs.get("limit") == FLOW_ALERTS_PAGE_LIMIT
        assert kwargs.get("older_than") is None

    def test_full_page_then_partial_walks_older_than(self):
        from fetch_flow import FLOW_ALERTS_PAGE_LIMIT, _flow_alerts_page_cursor, fetch_flow_alerts

        page1 = [
            {
                "ticker": "GLD",
                "option_chain": f"GLD{i}",
                "created_at": f"2026-08-07T18:{59 - (i // 60):02d}:{59 - (i % 60):02d}Z",
                "total_premium": str(100000 + i),
            }
            for i in range(FLOW_ALERTS_PAGE_LIMIT)
        ]
        oldest = _flow_alerts_page_cursor(page1)
        page2 = [
            {
                "ticker": "GLD",
                "option_chain": f"GLDx{i}",
                "created_at": f"2026-08-07T12:00:{i:02d}Z",
                "total_premium": "50000",
            }
            for i in range(12)
        ]
        mock_client = MagicMock()
        mock_client.get_flow_alerts.side_effect = [
            {"data": page1},
            {"data": page2},
        ]

        result = fetch_flow_alerts("GLD", min_premium=50000, _client=mock_client)

        assert len(result) == FLOW_ALERTS_PAGE_LIMIT + 12
        assert mock_client.get_flow_alerts.call_count == 2
        second = mock_client.get_flow_alerts.call_args_list[1].kwargs
        assert second["older_than"] == oldest
        assert second["limit"] == FLOW_ALERTS_PAGE_LIMIT

    def test_market_wide_allows_none_ticker(self):
        from fetch_flow import fetch_flow_alerts

        mock_client = MagicMock()
        mock_client.get_flow_alerts.return_value = {
            "data": [{"ticker": "X", "created_at": "2026-08-07T10:00:00Z", "option_chain": "X1"}],
        }
        result = fetch_flow_alerts(ticker=None, min_premium=500000, _client=mock_client)
        assert len(result) == 1
        assert mock_client.get_flow_alerts.call_args.kwargs.get("ticker") is None
