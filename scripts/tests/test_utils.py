"""Tests for scripts/utils/ shared utility modules."""
import json
import os
import pytest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock


# ══════════════════════════════════════════════════════════════════════
# UW API utils
# ══════════════════════════════════════════════════════════════════════

class TestGetUwToken:
    def test_returns_token_when_set(self):
        from utils.uw_api import get_uw_token
        with patch.dict(os.environ, {"UW_TOKEN": "test-token-123"}):
            assert get_uw_token() == "test-token-123"

    def test_raises_when_missing(self):
        from utils.uw_api import get_uw_token
        with patch.dict(os.environ, {}, clear=True):
            # Remove UW_TOKEN if present
            env = os.environ.copy()
            env.pop("UW_TOKEN", None)
            with patch.dict(os.environ, env, clear=True):
                with pytest.raises(ValueError, match="UW_TOKEN"):
                    get_uw_token()


class TestUwBaseUrl:
    def test_base_url_constant(self):
        from utils.uw_api import UW_BASE_URL
        assert UW_BASE_URL == "https://api.unusualwhales.com/api"


class TestUwApiGet:
    @patch("utils.uw_api.requests.get")
    def test_successful_request(self, mock_get):
        from utils.uw_api import uw_api_get
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": [{"ticker": "AAPL"}]}
        mock_get.return_value = mock_response

        with patch.dict(os.environ, {"UW_TOKEN": "test-token"}):
            result = uw_api_get("stock/AAPL/info")

        assert result == {"data": [{"ticker": "AAPL"}]}
        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert "https://api.unusualwhales.com/api/stock/AAPL/info" == call_args[0][0]
        assert "Bearer test-token" in call_args[1]["headers"]["Authorization"]

    @patch("utils.uw_api.requests.get")
    def test_with_params(self, mock_get):
        from utils.uw_api import uw_api_get
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": []}
        mock_get.return_value = mock_response

        with patch.dict(os.environ, {"UW_TOKEN": "test-token"}):
            uw_api_get("darkpool/AAPL", params={"date": "2026-03-01"})

        call_args = mock_get.call_args
        assert call_args[1]["params"] == {"date": "2026-03-01"}

    @patch("utils.uw_api.requests.get")
    def test_http_error_returns_error_dict(self, mock_get):
        from utils.uw_api import uw_api_get
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = Exception("404 Not Found")
        mock_get.return_value = mock_response

        with patch.dict(os.environ, {"UW_TOKEN": "test-token"}):
            result = uw_api_get("stock/INVALID/info")

        assert "error" in result

    @patch("utils.uw_api.requests.get")
    def test_connection_error_returns_error_dict(self, mock_get):
        from utils.uw_api import uw_api_get
        import requests as req
        mock_get.side_effect = req.ConnectionError("Connection refused")

        with patch.dict(os.environ, {"UW_TOKEN": "test-token"}):
            result = uw_api_get("stock/AAPL/info")

        assert "error" in result


# ══════════════════════════════════════════════════════════════════════
# Market Calendar utils
# ══════════════════════════════════════════════════════════════════════

class TestLoadHolidays:
    def test_returns_set_of_strings(self):
        from utils.market_calendar import load_holidays
        holidays = load_holidays(2026)
        assert isinstance(holidays, set)
        assert all(isinstance(h, str) for h in holidays)

    def test_2026_holidays_include_known_dates(self):
        from utils.market_calendar import load_holidays
        holidays = load_holidays(2026)
        assert "2026-01-01" in holidays  # New Year's
        assert "2026-01-19" in holidays  # MLK Day
        assert "2026-12-25" in holidays  # Christmas

    def test_defaults_to_current_year(self):
        from utils.market_calendar import load_holidays
        holidays = load_holidays()
        assert isinstance(holidays, set)
        assert len(holidays) > 0

    def test_unknown_year_returns_empty(self):
        from utils.market_calendar import load_holidays
        holidays = load_holidays(1999)
        assert holidays == set()


class TestIsMarketOpen:
    def test_weekday_during_hours(self):
        from utils.market_calendar import is_market_open
        # Tuesday 2026-03-03 at 10:00 AM ET
        dt = datetime(2026, 3, 3, 10, 0)
        assert is_market_open(dt) is True

    def test_weekend_closed(self):
        from utils.market_calendar import is_market_open
        # Saturday
        dt = datetime(2026, 3, 7, 12, 0)
        assert is_market_open(dt) is False

    def test_sunday_closed(self):
        from utils.market_calendar import is_market_open
        dt = datetime(2026, 3, 8, 12, 0)
        assert is_market_open(dt) is False

    def test_holiday_closed(self):
        from utils.market_calendar import is_market_open
        # MLK Day 2026
        dt = datetime(2026, 1, 19, 12, 0)
        assert is_market_open(dt) is False

    def test_before_930_closed(self):
        from utils.market_calendar import is_market_open
        # Weekday at 9:00 AM
        dt = datetime(2026, 3, 3, 9, 0)
        assert is_market_open(dt) is False

    def test_after_4pm_closed(self):
        from utils.market_calendar import is_market_open
        # Weekday at 4:01 PM
        dt = datetime(2026, 3, 3, 16, 1)
        assert is_market_open(dt) is False

    def test_exactly_930_open(self):
        from utils.market_calendar import is_market_open
        dt = datetime(2026, 3, 3, 9, 30)
        assert is_market_open(dt) is True

    def test_exactly_4pm_open(self):
        from utils.market_calendar import is_market_open
        dt = datetime(2026, 3, 3, 16, 0)
        assert is_market_open(dt) is True

    def test_defaults_to_now(self):
        from utils.market_calendar import is_market_open
        # Should not raise, returns a bool
        result = is_market_open()
        assert isinstance(result, bool)


class TestGetLastNTradingDays:
    def test_returns_correct_count(self):
        from utils.market_calendar import get_last_n_trading_days
        # Wednesday at 17:00 (after market close)
        dt = datetime(2026, 3, 4, 17, 0)
        days = get_last_n_trading_days(3, from_date=dt)
        assert len(days) == 3

    def test_skips_weekends(self):
        from utils.market_calendar import get_last_n_trading_days
        # Monday at 10am (before close) — should start from previous Friday
        dt = datetime(2026, 3, 2, 10, 0)
        days = get_last_n_trading_days(1, from_date=dt)
        assert days[0] == "2026-02-27"  # Friday

    def test_skips_holidays(self):
        from utils.market_calendar import get_last_n_trading_days
        # Day after MLK Day (Tuesday 2026-01-20 at 10am)
        dt = datetime(2026, 1, 20, 10, 0)
        days = get_last_n_trading_days(2, from_date=dt)
        assert "2026-01-19" not in days
        assert len(days) == 2

    def test_returns_strings(self):
        from utils.market_calendar import get_last_n_trading_days
        dt = datetime(2026, 3, 4, 17, 0)
        days = get_last_n_trading_days(2, from_date=dt)
        assert all(isinstance(d, str) for d in days)

    def test_defaults_from_date(self):
        from utils.market_calendar import get_last_n_trading_days
        days = get_last_n_trading_days(1)
        assert len(days) == 1


# ══════════════════════════════════════════════════════════════════════
# IB Connection utils
# ══════════════════════════════════════════════════════════════════════

class TestClientIds:
    def test_registry_has_all_entries(self):
        from utils.ib_connection import CLIENT_IDS
        expected_keys = {
            "ib_order_manage", "ib_sync", "ib_order", "ib_orders",
            "ib_execute", "ib_fill_monitor", "exit_order_service",
            "ib_reconcile", "fetch_analyst_ratings", "ib_realtime_server"
        }
        assert set(CLIENT_IDS.keys()) == expected_keys

    def test_nonzero_ids_are_unique(self):
        """Non-master client IDs must be unique to avoid conflicts."""
        from utils.ib_connection import CLIENT_IDS
        nonzero_ids = [v for v in CLIENT_IDS.values() if v != 0]
        assert len(nonzero_ids) == len(set(nonzero_ids)), "Non-zero client IDs must be unique"
    
    def test_master_client_scripts(self):
        """Scripts that need full order control should use clientId=0."""
        from utils.ib_connection import CLIENT_IDS
        master_scripts = [k for k, v in CLIENT_IDS.items() if v == 0]
        # These scripts need master client for cancel/modify operations
        assert "ib_order_manage" in master_scripts
        assert "ib_sync" in master_scripts
        assert "ib_reconcile" in master_scripts

    def test_specific_ids(self):
        from utils.ib_connection import CLIENT_IDS
        # Master client (0) for full order control
        assert CLIENT_IDS["ib_order_manage"] == 0
        assert CLIENT_IDS["ib_sync"] == 0
        assert CLIENT_IDS["ib_reconcile"] == 0
        # Dedicated IDs for concurrent connections
        assert CLIENT_IDS["ib_orders"] == 11
        assert CLIENT_IDS["ib_order"] == 2
        assert CLIENT_IDS["ib_execute"] == 25
        assert CLIENT_IDS["ib_fill_monitor"] == 52
        assert CLIENT_IDS["exit_order_service"] == 60
        assert CLIENT_IDS["fetch_analyst_ratings"] == 99
        assert CLIENT_IDS["ib_realtime_server"] == 100


class TestIbDefaults:
    def test_default_host(self):
        from utils.ib_connection import DEFAULT_HOST
        # DEFAULT_HOST comes from env/dotenv — verify it's a non-empty string
        assert isinstance(DEFAULT_HOST, str) and len(DEFAULT_HOST) > 0

    def test_default_gateway_port(self):
        from utils.ib_connection import DEFAULT_GATEWAY_PORT
        assert DEFAULT_GATEWAY_PORT == 4001

    def test_default_tws_port(self):
        from utils.ib_connection import DEFAULT_TWS_PORT
        assert DEFAULT_TWS_PORT == 7497


class TestConnectIb:
    @patch("utils.ib_connection.IB")
    def test_connects_with_registry_id(self, MockIB):
        from utils.ib_connection import connect_ib
        mock_ib = MagicMock()
        MockIB.return_value = mock_ib

        result = connect_ib("ib_sync")

        # ib_sync uses master client (0) for full order visibility
        from utils.ib_connection import DEFAULT_HOST as _DH
        mock_ib.connect.assert_called_once_with(
            _DH, 4001, clientId=0, timeout=10
        )
        assert result is mock_ib

    @patch("utils.ib_connection.IB")
    def test_custom_host_and_port(self, MockIB):
        from utils.ib_connection import connect_ib
        mock_ib = MagicMock()
        MockIB.return_value = mock_ib

        result = connect_ib("ib_sync", host="192.168.1.1", port=7497)

        mock_ib.connect.assert_called_once_with(
            "192.168.1.1", 7497, clientId=0, timeout=10
        )

    @patch("utils.ib_connection.IB")
    def test_custom_client_id_override(self, MockIB):
        from utils.ib_connection import connect_ib
        mock_ib = MagicMock()
        MockIB.return_value = mock_ib

        result = connect_ib("ib_sync", client_id=999)

        from utils.ib_connection import DEFAULT_HOST as _DH
        mock_ib.connect.assert_called_once_with(
            _DH, 4001, clientId=999, timeout=10
        )

    @patch("utils.ib_connection.IB")
    def test_custom_timeout(self, MockIB):
        from utils.ib_connection import connect_ib
        mock_ib = MagicMock()
        MockIB.return_value = mock_ib

        result = connect_ib("ib_sync", timeout=30)

        from utils.ib_connection import DEFAULT_HOST as _DH
        mock_ib.connect.assert_called_once_with(
            _DH, 4001, clientId=0, timeout=30
        )

    @patch("utils.ib_connection.IB")
    def test_unknown_client_raises(self, MockIB):
        from utils.ib_connection import connect_ib
        with pytest.raises(ValueError, match="Unknown client name"):
            connect_ib("nonexistent_script")

    @patch("utils.ib_connection.IB")
    def test_connection_failure_propagates(self, MockIB):
        from utils.ib_connection import connect_ib
        mock_ib = MagicMock()
        mock_ib.connect.side_effect = ConnectionRefusedError("Connection refused")
        MockIB.return_value = mock_ib

        with pytest.raises(ConnectionRefusedError):
            connect_ib("ib_sync")
