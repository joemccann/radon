"""Tests for UWClient — comprehensive Unusual Whales API client.

RED/GREEN TDD: All tests written first, then client implemented.
Uses unittest.mock to avoid any real API calls.
"""
import json
import os
import time

import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from clients.uw_client import (
    UWClient,
    UWAPIError,
    UWAuthError,
    UWRateLimitError,
    UWNotFoundError,
    UWValidationError,
    UWServerError,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def mock_env_token():
    """Provide a mock UW_TOKEN environment variable."""
    with patch.dict(os.environ, {"UW_TOKEN": "test-token-abc123"}):
        yield


@pytest.fixture
def client(mock_env_token):
    """Create a UWClient instance with mocked token."""
    return UWClient()


@pytest.fixture
def mock_session(client):
    """Replace the client's session with a mock."""
    mock = MagicMock()
    client._session = mock
    return mock


def _make_response(status_code=200, json_data=None, reason="OK", headers=None):
    """Helper to create a mock requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.reason = reason
    resp.headers = headers or {}
    resp.json.return_value = json_data or {}
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        from requests.exceptions import HTTPError
        resp.raise_for_status.side_effect = HTTPError(response=resp)
    return resp


# ══════════════════════════════════════════════════════════════════════
# INITIALIZATION & AUTH
# ══════════════════════════════════════════════════════════════════════


class TestClientInit:
    def test_init_with_env_token(self, mock_env_token):
        client = UWClient()
        assert client._token == "test-token-abc123"

    def test_init_with_explicit_token(self):
        client = UWClient(token="my-explicit-token")
        assert client._token == "my-explicit-token"

    def test_init_missing_token_raises(self):
        with patch.dict(os.environ, {}, clear=True):
            # Ensure UW_TOKEN is not set
            os.environ.pop("UW_TOKEN", None)
            with pytest.raises(UWAuthError, match="UW_TOKEN"):
                UWClient()

    def test_default_base_url(self, mock_env_token):
        client = UWClient()
        assert client._base_url == "https://api.unusualwhales.com/api"

    def test_custom_base_url(self, mock_env_token):
        client = UWClient(base_url="https://custom.api.com/api")
        assert client._base_url == "https://custom.api.com/api"

    def test_session_headers(self, client):
        headers = client._session.headers
        assert headers["Authorization"] == "Bearer test-token-abc123"
        assert headers["Accept"] == "application/json"
        assert "radon" in headers["User-Agent"]

    def test_default_timeout(self, client):
        assert client._timeout == 30

    def test_custom_timeout(self, mock_env_token):
        client = UWClient(timeout=60)
        assert client._timeout == 60

    def test_default_max_retries(self, client):
        assert client._max_retries == 3

    def test_custom_max_retries(self, mock_env_token):
        client = UWClient(max_retries=5)
        assert client._max_retries == 5


# ══════════════════════════════════════════════════════════════════════
# REQUEST HANDLING & ERROR HANDLING
# ══════════════════════════════════════════════════════════════════════


class TestRequestHandling:
    def test_get_success(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": [1, 2, 3]})
        result = client._get("stock/AAPL/info")
        assert result == {"data": [1, 2, 3]}
        mock_session.get.assert_called_once()

    def test_get_strips_leading_slash(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client._get("/stock/AAPL/info")
        call_args = mock_session.get.call_args
        assert "/api/stock/AAPL/info" in call_args[0][0]
        assert "/api//stock" not in call_args[0][0]

    def test_get_passes_params(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client._get("darkpool/AAPL", params={"date": "2026-03-01"})
        call_args = mock_session.get.call_args
        assert call_args[1]["params"] == {"date": "2026-03-01"}

    def test_get_uses_timeout(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client._get("stock/AAPL/info")
        call_args = mock_session.get.call_args
        assert call_args[1]["timeout"] == 30

    def test_404_raises_not_found(self, client, mock_session):
        mock_session.get.return_value = _make_response(404, {"message": "Not found"}, reason="Not Found")
        with pytest.raises(UWNotFoundError):
            client._get("stock/INVALID/info")

    def test_401_raises_auth_error(self, client, mock_session):
        mock_session.get.return_value = _make_response(401, {"message": "Unauthorized"}, reason="Unauthorized")
        with pytest.raises(UWAuthError):
            client._get("stock/AAPL/info")

    def test_403_raises_auth_error(self, client, mock_session):
        mock_session.get.return_value = _make_response(403, {"message": "Forbidden"}, reason="Forbidden")
        with pytest.raises(UWAuthError):
            client._get("stock/AAPL/info")

    def test_422_raises_validation_error(self, client, mock_session):
        mock_session.get.return_value = _make_response(422, {"message": "Invalid params"}, reason="Unprocessable")
        with pytest.raises(UWValidationError):
            client._get("stock/AAPL/info")

    def test_500_raises_server_error(self, client, mock_session):
        mock_session.get.return_value = _make_response(500, {}, reason="Internal Server Error")
        with patch("time.sleep"):
            with pytest.raises(UWServerError):
                client._get("stock/AAPL/info")

    def test_generic_4xx_raises_api_error(self, client, mock_session):
        mock_session.get.return_value = _make_response(418, {}, reason="I'm a teapot")
        with pytest.raises(UWAPIError):
            client._get("stock/AAPL/info")


# ══════════════════════════════════════════════════════════════════════
# RETRY LOGIC
# ══════════════════════════════════════════════════════════════════════


class TestRetryLogic:
    def test_retry_on_429(self, client, mock_session):
        """429 responses should be retried up to max_retries times."""
        rate_limit_resp = _make_response(429, {}, reason="Too Many Requests", headers={"Retry-After": "1"})
        success_resp = _make_response(200, {"data": "ok"})
        mock_session.get.side_effect = [rate_limit_resp, success_resp]

        with patch("time.sleep"):
            result = client._get("stock/AAPL/info")
        assert result == {"data": "ok"}
        assert mock_session.get.call_count == 2

    def test_retry_on_500(self, client, mock_session):
        """500 responses should be retried."""
        error_resp = _make_response(500, {}, reason="Internal Server Error")
        success_resp = _make_response(200, {"data": "ok"})
        mock_session.get.side_effect = [error_resp, success_resp]

        with patch("time.sleep"):
            result = client._get("stock/AAPL/info")
        assert result == {"data": "ok"}

    def test_retry_on_502(self, client, mock_session):
        error_resp = _make_response(502, {}, reason="Bad Gateway")
        success_resp = _make_response(200, {"data": "ok"})
        mock_session.get.side_effect = [error_resp, success_resp]

        with patch("time.sleep"):
            result = client._get("stock/AAPL/info")
        assert result == {"data": "ok"}

    def test_retry_on_503(self, client, mock_session):
        error_resp = _make_response(503, {}, reason="Service Unavailable")
        success_resp = _make_response(200, {"data": "ok"})
        mock_session.get.side_effect = [error_resp, success_resp]

        with patch("time.sleep"):
            result = client._get("stock/AAPL/info")
        assert result == {"data": "ok"}

    def test_retry_on_504(self, client, mock_session):
        error_resp = _make_response(504, {}, reason="Gateway Timeout")
        success_resp = _make_response(200, {"data": "ok"})
        mock_session.get.side_effect = [error_resp, success_resp]

        with patch("time.sleep"):
            result = client._get("stock/AAPL/info")
        assert result == {"data": "ok"}

    def test_max_retries_exceeded(self, client, mock_session):
        """After max_retries, the last error should be raised."""
        error_resp = _make_response(500, {}, reason="Internal Server Error")
        mock_session.get.return_value = error_resp

        with patch("time.sleep"):
            with pytest.raises(UWServerError):
                client._get("stock/AAPL/info")
        # 1 initial + 3 retries = 4 total
        assert mock_session.get.call_count == 4

    def test_no_retry_on_4xx_non_429(self, client, mock_session):
        """Non-retryable errors should not be retried."""
        error_resp = _make_response(422, {}, reason="Unprocessable Entity")
        mock_session.get.return_value = error_resp

        with pytest.raises(UWValidationError):
            client._get("stock/AAPL/info")
        assert mock_session.get.call_count == 1

    def test_no_retry_on_401(self, client, mock_session):
        """Auth errors should not be retried."""
        error_resp = _make_response(401, {}, reason="Unauthorized")
        mock_session.get.return_value = error_resp

        with pytest.raises(UWAuthError):
            client._get("stock/AAPL/info")
        assert mock_session.get.call_count == 1

    def test_no_retry_on_404(self, client, mock_session):
        """Not found errors should not be retried."""
        error_resp = _make_response(404, {}, reason="Not Found")
        mock_session.get.return_value = error_resp

        with pytest.raises(UWNotFoundError):
            client._get("stock/AAPL/info")
        assert mock_session.get.call_count == 1

    def test_rate_limit_uses_retry_after_header(self, client, mock_session):
        """Retry-After header value should be respected."""
        rate_resp = _make_response(429, {}, reason="Too Many Requests", headers={"Retry-After": "5"})
        success_resp = _make_response(200, {"data": "ok"})
        mock_session.get.side_effect = [rate_resp, success_resp]

        with patch("time.sleep") as mock_sleep:
            client._get("stock/AAPL/info")
        # Should sleep at least 5 seconds (from Retry-After)
        mock_sleep.assert_called()
        sleep_arg = mock_sleep.call_args[0][0]
        assert sleep_arg >= 5

    def test_429_exhausted_raises_rate_limit_error(self, client, mock_session):
        """After all retries on 429, should raise UWRateLimitError."""
        rate_resp = _make_response(429, {}, reason="Too Many Requests", headers={"Retry-After": "1"})
        mock_session.get.return_value = rate_resp

        with patch("time.sleep"):
            with pytest.raises(UWRateLimitError):
                client._get("stock/AAPL/info")

    def test_connection_error_retried(self, client, mock_session):
        """Connection errors should be retried."""
        from requests.exceptions import ConnectionError
        success_resp = _make_response(200, {"data": "ok"})
        mock_session.get.side_effect = [ConnectionError("Network unreachable"), success_resp]

        with patch("time.sleep"):
            result = client._get("stock/AAPL/info")
        assert result == {"data": "ok"}

    def test_timeout_error_retried(self, client, mock_session):
        """Timeout errors should be retried."""
        from requests.exceptions import Timeout
        success_resp = _make_response(200, {"data": "ok"})
        mock_session.get.side_effect = [Timeout("Request timed out"), success_resp]

        with patch("time.sleep"):
            result = client._get("stock/AAPL/info")
        assert result == {"data": "ok"}


# ══════════════════════════════════════════════════════════════════════
# EXCEPTION HIERARCHY
# ══════════════════════════════════════════════════════════════════════


class TestExceptionHierarchy:
    def test_all_errors_inherit_from_uw_api_error(self):
        assert issubclass(UWAuthError, UWAPIError)
        assert issubclass(UWRateLimitError, UWAPIError)
        assert issubclass(UWNotFoundError, UWAPIError)
        assert issubclass(UWValidationError, UWAPIError)
        assert issubclass(UWServerError, UWAPIError)

    def test_uw_api_error_has_status_code(self):
        err = UWAPIError("test error", status_code=500)
        assert err.status_code == 500
        assert "test error" in str(err)

    def test_uw_api_error_has_response_body(self):
        err = UWAPIError("test error", status_code=422, response_body={"message": "bad"})
        assert err.response_body == {"message": "bad"}


# ══════════════════════════════════════════════════════════════════════
# DARK POOL ENDPOINTS (Primary Edge)
# ══════════════════════════════════════════════════════════════════════


class TestDarkPoolEndpoints:
    def test_get_darkpool_flow(self, client, mock_session):
        mock_data = {"data": [{"ticker": "AAPL", "size": 1000, "price": "150.00"}]}
        mock_session.get.return_value = _make_response(200, mock_data)

        result = client.get_darkpool_flow("AAPL")
        assert result == mock_data
        call_url = mock_session.get.call_args[0][0]
        assert "darkpool/AAPL" in call_url

    def test_get_darkpool_flow_with_params(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_darkpool_flow("AAPL", date="2026-03-01", min_premium=100000, limit=100)
        call_kwargs = mock_session.get.call_args[1]
        assert call_kwargs["params"]["date"] == "2026-03-01"
        assert call_kwargs["params"]["min_premium"] == 100000
        assert call_kwargs["params"]["limit"] == 100

    def test_get_darkpool_flow_pagination_cursors(self, client, mock_session):
        """UW pages darkpool by time: older_than / newer_than + limit."""
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_darkpool_flow(
            "GLD",
            date="2026-08-06",
            limit=500,
            older_than="2026-08-06T15:30:00Z",
            order="desc",
        )
        params = mock_session.get.call_args[1]["params"]
        assert params["date"] == "2026-08-06"
        assert params["limit"] == 500
        assert params["older_than"] == "2026-08-06T15:30:00Z"
        assert params["order"] == "desc"

    def test_get_darkpool_flow_uppercases_ticker(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_darkpool_flow("aapl")
        call_url = mock_session.get.call_args[0][0]
        assert "darkpool/AAPL" in call_url

    def test_get_darkpool_recent(self, client, mock_session):
        mock_data = {"data": [{"ticker": "MSFT", "size": 5000}]}
        mock_session.get.return_value = _make_response(200, mock_data)

        result = client.get_darkpool_recent()
        assert result == mock_data
        call_url = mock_session.get.call_args[0][0]
        assert "darkpool/recent" in call_url

    def test_get_darkpool_recent_with_limit(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_darkpool_recent(limit=50)
        call_kwargs = mock_session.get.call_args[1]
        assert call_kwargs["params"]["limit"] == 50


# ══════════════════════════════════════════════════════════════════════
# OPTIONS FLOW ENDPOINTS
# ══════════════════════════════════════════════════════════════════════


class TestOptionsFlowEndpoints:
    def test_get_flow_alerts(self, client, mock_session):
        mock_data = {"data": [{"ticker": "MSFT", "alert_rule": "RepeatedHits"}]}
        mock_session.get.return_value = _make_response(200, mock_data)

        result = client.get_flow_alerts()
        assert result == mock_data
        call_url = mock_session.get.call_args[0][0]
        assert "option-trades/flow-alerts" in call_url

    def test_get_flow_alerts_with_ticker(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_flow_alerts(ticker="AAPL")
        params = mock_session.get.call_args[1]["params"]
        assert params["ticker_symbol"] == "AAPL"

    def test_get_flow_alerts_with_full_params(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_flow_alerts(
            ticker="AAPL",
            min_premium=50000,
            max_premium=1000000,
            is_sweep=True,
            is_call=True,
            is_otm=True,
            all_opening=True,
            min_dte=14,
            max_dte=60,
            limit=100,
        )
        params = mock_session.get.call_args[1]["params"]
        assert params["ticker_symbol"] == "AAPL"
        assert params["min_premium"] == 50000
        assert params["max_premium"] == 1000000
        assert params["is_sweep"] is True
        assert params["is_call"] is True
        assert params["is_otm"] is True
        assert params["all_opening"] is True
        assert params["min_dte"] == 14
        assert params["max_dte"] == 60
        assert params["limit"] == 100

    def test_get_flow_alerts_bid_ask_filters(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_flow_alerts(is_ask_side=True, is_bid_side=False)
        params = mock_session.get.call_args[1]["params"]
        assert params["is_ask_side"] is True
        assert params["is_bid_side"] is False

    def test_get_flow_alerts_by_ticker(self, client, mock_session):
        """Convenience method that filters by ticker."""
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_flow_alerts_by_ticker("NVDA", min_premium=100000)
        params = mock_session.get.call_args[1]["params"]
        assert params["ticker_symbol"] == "NVDA"
        assert params["min_premium"] == 100000

    def test_get_stock_flow_alerts(self, client, mock_session):
        """Per-stock flow alerts endpoint."""
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_stock_flow_alerts("AAPL")
        call_url = mock_session.get.call_args[0][0]
        assert "stock/AAPL/flow-alerts" in call_url

    def test_get_flow_per_strike(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_flow_per_strike("AAPL")
        call_url = mock_session.get.call_args[0][0]
        assert "stock/AAPL/flow-per-strike" in call_url

    def test_get_flow_per_expiry(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_flow_per_expiry("AAPL")
        call_url = mock_session.get.call_args[0][0]
        assert "stock/AAPL/flow-per-expiry" in call_url

    def test_get_net_prem_ticks(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_net_prem_ticks("AAPL")
        call_url = mock_session.get.call_args[0][0]
        assert "stock/AAPL/net-prem-ticks" in call_url


# ══════════════════════════════════════════════════════════════════════
# STOCK INFORMATION ENDPOINTS
# ══════════════════════════════════════════════════════════════════════


class TestStockInfoEndpoints:
    def test_get_stock_info(self, client, mock_session):
        mock_data = {"data": {"ticker": "AAPL", "full_name": "Apple Inc.", "sector": "Technology"}}
        mock_session.get.return_value = _make_response(200, mock_data)

        result = client.get_stock_info("AAPL")
        assert result == mock_data
        call_url = mock_session.get.call_args[0][0]
        assert "stock/AAPL/info" in call_url

    def test_get_stock_info_uppercases(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": {}})
        client.get_stock_info("msft")
        call_url = mock_session.get.call_args[0][0]
        assert "stock/MSFT/info" in call_url

    def test_get_options_volume(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": {}})
        client.get_options_volume("AAPL")
        call_url = mock_session.get.call_args[0][0]
        assert "stock/AAPL/options-volume" in call_url

    def test_get_stock_ohlc(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_stock_ohlc("AAPL", candle_size="1d")
        call_url = mock_session.get.call_args[0][0]
        assert "stock/AAPL/ohlc/1d" in call_url


# ══════════════════════════════════════════════════════════════════════
# OPTIONS CHAIN ENDPOINTS
# ══════════════════════════════════════════════════════════════════════


class TestOptionsChainEndpoints:
    def test_get_option_contracts(self, client, mock_session):
        mock_data = {"data": [{"option_symbol": "AAPL260320C00150000"}]}
        mock_session.get.return_value = _make_response(200, mock_data)

        result = client.get_option_contracts("AAPL")
        assert result == mock_data
        call_url = mock_session.get.call_args[0][0]
        assert "stock/AAPL/option-contracts" in call_url

    def test_get_option_contracts_with_filters(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_option_contracts(
            "AAPL",
            expiry="2026-03-20",
            option_type="call",
            vol_greater_oi=True,
            exclude_zero_vol_chains=True,
        )
        params = mock_session.get.call_args[1]["params"]
        assert params["expiry"] == "2026-03-20"
        assert params["option_type"] == "call"
        assert params["vol_greater_oi"] is True
        assert params["exclude_zero_vol_chains"] is True

    def test_get_option_chain(self, client, mock_session):
        """Options chain by expiry is via option-contracts with expiry param."""
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_option_chain("AAPL", expiry="2026-06-19")
        params = mock_session.get.call_args[1]["params"]
        assert params["expiry"] == "2026-06-19"

    def test_get_expiry_breakdown(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_expiry_breakdown("AAPL")
        call_url = mock_session.get.call_args[0][0]
        assert "stock/AAPL/expiry-breakdown" in call_url

    def test_get_option_contract_historic(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_option_contract_historic("AAPL260320C00150000")
        call_url = mock_session.get.call_args[0][0]
        assert "option-contract/AAPL260320C00150000/historic" in call_url

    def test_get_greeks(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_greeks("AAPL", expiry="2026-03-20")
        call_url = mock_session.get.call_args[0][0]
        assert "stock/AAPL/greeks" in call_url
        params = mock_session.get.call_args[1]["params"]
        assert params["expiry"] == "2026-03-20"


# ══════════════════════════════════════════════════════════════════════
# GREEK EXPOSURE (GEX) ENDPOINTS
# ══════════════════════════════════════════════════════════════════════


class TestGreekExposureEndpoints:
    def test_get_greek_exposure(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_greek_exposure("AAPL")
        call_url = mock_session.get.call_args[0][0]
        assert "stock/AAPL/greek-exposure" in call_url

    def test_get_greek_exposure_by_strike(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_greek_exposure_by_strike("AAPL")
        call_url = mock_session.get.call_args[0][0]
        assert "stock/AAPL/greek-exposure/strike" in call_url

    def test_get_greek_exposure_by_expiry(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_greek_exposure_by_expiry("AAPL")
        call_url = mock_session.get.call_args[0][0]
        assert "stock/AAPL/greek-exposure/expiry" in call_url

    def test_get_greek_flow(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_greek_flow("AAPL")
        call_url = mock_session.get.call_args[0][0]
        assert "stock/AAPL/greek-flow" in call_url


# ══════════════════════════════════════════════════════════════════════
# VOLATILITY ENDPOINTS
# ══════════════════════════════════════════════════════════════════════


class TestVolatilityEndpoints:
    def test_get_realized_volatility(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_realized_volatility("AAPL")
        call_url = mock_session.get.call_args[0][0]
        assert "stock/AAPL/volatility/realized" in call_url

    def test_get_volatility_term_structure(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_volatility_term_structure("AAPL")
        call_url = mock_session.get.call_args[0][0]
        assert "stock/AAPL/volatility/term-structure" in call_url

    def test_get_volatility_stats(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_volatility_stats("AAPL")
        call_url = mock_session.get.call_args[0][0]
        assert "stock/AAPL/volatility/stats" in call_url

    def test_get_iv_rank(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_iv_rank("AAPL")
        call_url = mock_session.get.call_args[0][0]
        assert "stock/AAPL/iv-rank" in call_url


# ══════════════════════════════════════════════════════════════════════
# ANALYST RATINGS ENDPOINTS
# ══════════════════════════════════════════════════════════════════════


class TestAnalystRatingsEndpoints:
    def test_get_analyst_ratings(self, client, mock_session):
        mock_data = {"data": [{"ticker": "AAPL", "action": "maintained"}]}
        mock_session.get.return_value = _make_response(200, mock_data)

        result = client.get_analyst_ratings()
        assert result == mock_data
        call_url = mock_session.get.call_args[0][0]
        assert "screener/analysts" in call_url

    def test_get_analyst_ratings_with_params(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_analyst_ratings(
            ticker="AAPL",
            action="upgraded",
            recommendation="buy",
            limit=50,
        )
        params = mock_session.get.call_args[1]["params"]
        assert params["ticker"] == "AAPL"
        assert params["action"] == "upgraded"
        assert params["recommendation"] == "buy"
        assert params["limit"] == 50

    def test_get_analyst_ratings_by_ticker(self, client, mock_session):
        """Convenience method for single-ticker filtering."""
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_analyst_ratings_by_ticker("MSFT")
        params = mock_session.get.call_args[1]["params"]
        assert params["ticker"] == "MSFT"


# ══════════════════════════════════════════════════════════════════════
# SEASONALITY ENDPOINTS
# ══════════════════════════════════════════════════════════════════════


class TestSeasonalityEndpoints:
    def test_get_monthly_seasonality(self, client, mock_session):
        mock_data = {"data": [{"month": "January", "avg_return": "2.5"}]}
        mock_session.get.return_value = _make_response(200, mock_data)

        result = client.get_monthly_seasonality("AAPL")
        assert result == mock_data
        call_url = mock_session.get.call_args[0][0]
        assert "seasonality/AAPL/monthly" in call_url

    def test_get_year_month_seasonality(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_year_month_seasonality("AAPL")
        call_url = mock_session.get.call_args[0][0]
        assert "seasonality/AAPL/year-month" in call_url

    def test_get_market_seasonality(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_market_seasonality()
        call_url = mock_session.get.call_args[0][0]
        assert "seasonality/market" in call_url

    def test_get_month_performers(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_month_performers("March")
        call_url = mock_session.get.call_args[0][0]
        assert "seasonality/March/performers" in call_url


# ══════════════════════════════════════════════════════════════════════
# SHORT INTEREST ENDPOINTS
# ══════════════════════════════════════════════════════════════════════


class TestShortInterestEndpoints:
    def test_get_short_interest(self, client, mock_session):
        mock_data = {"data": [{"ticker": "AAPL", "short_interest": "1.2"}]}
        mock_session.get.return_value = _make_response(200, mock_data)

        result = client.get_short_interest("AAPL")
        assert result == mock_data
        call_url = mock_session.get.call_args[0][0]
        assert "shorts/AAPL/interest-float/v2" in call_url

    def test_get_short_data(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_short_data("AAPL")
        call_url = mock_session.get.call_args[0][0]
        assert "shorts/AAPL/data" in call_url

    def test_get_short_volume_ratio(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_short_volume_ratio("AAPL")
        call_url = mock_session.get.call_args[0][0]
        assert "shorts/AAPL/volume-and-ratio" in call_url


# ══════════════════════════════════════════════════════════════════════
# INSTITUTIONAL DATA ENDPOINTS
# ══════════════════════════════════════════════════════════════════════


class TestInstitutionalEndpoints:
    def test_get_institutional_ownership(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_institutional_ownership("AAPL")
        call_url = mock_session.get.call_args[0][0]
        assert "institution/AAPL/ownership" in call_url

    def test_get_institution_holdings(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_institution_holdings("Vanguard")
        call_url = mock_session.get.call_args[0][0]
        assert "institution/Vanguard/holdings" in call_url

    def test_get_institutions(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_institutions()
        call_url = mock_session.get.call_args[0][0]
        assert "/institutions" in call_url


# ══════════════════════════════════════════════════════════════════════
# INSIDER TRADING ENDPOINTS
# ══════════════════════════════════════════════════════════════════════


class TestInsiderEndpoints:
    def test_get_insider_transactions(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_insider_transactions()
        call_url = mock_session.get.call_args[0][0]
        assert "insider/transactions" in call_url

    def test_get_insider_transactions_with_params(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_insider_transactions(ticker="AAPL", limit=50)
        params = mock_session.get.call_args[1]["params"]
        assert params["ticker"] == "AAPL"
        assert params["limit"] == 50

    def test_get_insider_by_ticker(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_insider_by_ticker("AAPL")
        call_url = mock_session.get.call_args[0][0]
        assert "insider/AAPL" in call_url

    def test_get_insider_ticker_flow(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_insider_ticker_flow("AAPL")
        call_url = mock_session.get.call_args[0][0]
        assert "insider/AAPL/ticker-flow" in call_url


# ══════════════════════════════════════════════════════════════════════
# CONGRESS TRADING ENDPOINTS
# ══════════════════════════════════════════════════════════════════════


class TestCongressEndpoints:
    def test_get_congress_recent_trades(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_congress_recent_trades()
        call_url = mock_session.get.call_args[0][0]
        assert "congress/recent-trades" in call_url

    def test_get_congress_recent_trades_with_params(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_congress_recent_trades(ticker="AAPL", limit=20)
        params = mock_session.get.call_args[1]["params"]
        assert params["ticker"] == "AAPL"
        assert params["limit"] == 20

    def test_get_congress_trader(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_congress_trader(name="Pelosi")
        params = mock_session.get.call_args[1]["params"]
        assert params["name"] == "Pelosi"


# ══════════════════════════════════════════════════════════════════════
# ETF ENDPOINTS
# ══════════════════════════════════════════════════════════════════════


class TestETFEndpoints:
    def test_get_etf_info(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": {}})
        client.get_etf_info("SPY")
        call_url = mock_session.get.call_args[0][0]
        assert "etfs/SPY/info" in call_url

    def test_get_etf_holdings(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_etf_holdings("SPY")
        call_url = mock_session.get.call_args[0][0]
        assert "etfs/SPY/holdings" in call_url

    def test_get_etf_exposure(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_etf_exposure("AAPL")
        call_url = mock_session.get.call_args[0][0]
        assert "etfs/AAPL/exposure" in call_url


# ══════════════════════════════════════════════════════════════════════
# MARKET OVERVIEW ENDPOINTS
# ══════════════════════════════════════════════════════════════════════


class TestMarketEndpoints:
    def test_get_market_tide(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_market_tide()
        call_url = mock_session.get.call_args[0][0]
        assert "market/market-tide" in call_url

    def test_get_sector_etfs(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_sector_etfs()
        call_url = mock_session.get.call_args[0][0]
        assert "market/sector-etfs" in call_url

    def test_get_total_options_volume(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_total_options_volume()
        call_url = mock_session.get.call_args[0][0]
        assert "market/total-options-volume" in call_url

    def test_get_oi_change(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_oi_change()
        call_url = mock_session.get.call_args[0][0]
        assert "market/oi-change" in call_url

    def test_get_economic_calendar(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_economic_calendar()
        call_url = mock_session.get.call_args[0][0]
        assert "market/economic-calendar" in call_url

    def test_get_fda_calendar(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_fda_calendar(
            target_date_min="2026-08-07",
            target_date_max="2027-08-07",
            limit=200,
        )
        call_url = mock_session.get.call_args[0][0]
        assert "market/fda-calendar" in call_url
        assert mock_session.get.call_args[1]["params"] == {
            "target_date_min": "2026-08-07",
            "target_date_max": "2027-08-07",
            "limit": 200,
        }


# ══════════════════════════════════════════════════════════════════════
# EARNINGS ENDPOINTS
# ══════════════════════════════════════════════════════════════════════


class TestEarningsEndpoints:
    def test_get_earnings_premarket(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_earnings_premarket(date="2026-08-10", limit=100, page=0)
        call_url = mock_session.get.call_args[0][0]
        assert "earnings/premarket" in call_url
        assert mock_session.get.call_args[1]["params"] == {
            "date": "2026-08-10", "limit": 100, "page": 0,
        }

    def test_get_earnings_afterhours(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_earnings_afterhours(date="2026-08-10", limit=100, page=1)
        call_url = mock_session.get.call_args[0][0]
        assert "earnings/afterhours" in call_url
        assert mock_session.get.call_args[1]["params"] == {
            "date": "2026-08-10", "limit": 100, "page": 1,
        }

    def test_get_earnings_by_ticker(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_earnings_by_ticker("AAPL")
        call_url = mock_session.get.call_args[0][0]
        assert "earnings/AAPL" in call_url


# ══════════════════════════════════════════════════════════════════════
# SCREENER ENDPOINTS
# ══════════════════════════════════════════════════════════════════════


class TestScreenerEndpoints:
    def test_get_stock_screener(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_stock_screener()
        call_url = mock_session.get.call_args[0][0]
        assert "screener/stocks" in call_url

    def test_get_stock_screener_with_params(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_stock_screener(sector="Technology", limit=50)
        params = mock_session.get.call_args[1]["params"]
        assert params["sector"] == "Technology"
        assert params["limit"] == 50

    def test_get_option_contracts_screener(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_option_contracts_screener()
        call_url = mock_session.get.call_args[0][0]
        assert "screener/option-contracts" in call_url

    def test_get_short_screener(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_short_screener()
        call_url = mock_session.get.call_args[0][0]
        assert "short_screener" in call_url


# ══════════════════════════════════════════════════════════════════════
# NEWS ENDPOINTS
# ══════════════════════════════════════════════════════════════════════


class TestNewsEndpoints:
    def test_get_news_headlines(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_news_headlines()
        call_url = mock_session.get.call_args[0][0]
        assert "news/headlines" in call_url

    def test_get_news_headlines_with_params(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_news_headlines(ticker="AAPL", limit=10)
        params = mock_session.get.call_args[1]["params"]
        assert params["ticker"] == "AAPL"
        assert params["limit"] == 10


# ══════════════════════════════════════════════════════════════════════
# MAX PAIN / OI CHANGE
# ══════════════════════════════════════════════════════════════════════


class TestMaxPainEndpoints:
    def test_get_max_pain(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_max_pain("AAPL")
        call_url = mock_session.get.call_args[0][0]
        assert "stock/AAPL/max-pain" in call_url

    def test_get_stock_oi_change(self, client, mock_session):
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_stock_oi_change("AAPL")
        call_url = mock_session.get.call_args[0][0]
        assert "stock/AAPL/oi-change" in call_url


# ══════════════════════════════════════════════════════════════════════
# CONTEXT MANAGER / CLOSE
# ══════════════════════════════════════════════════════════════════════


class TestLifecycle:
    def test_close_closes_session(self, client):
        mock_session = MagicMock()
        client._session = mock_session
        client.close()
        mock_session.close.assert_called_once()

    def test_context_manager(self, mock_env_token):
        with UWClient() as client:
            assert client._token == "test-token-abc123"
        # After exiting, session should be closed

    def test_context_manager_with_block(self, mock_env_token):
        """Verify context manager closes session on exit."""
        client = UWClient()
        mock_session = MagicMock()
        client._session = mock_session

        with client:
            pass  # use client
        mock_session.close.assert_called_once()


# ══════════════════════════════════════════════════════════════════════
# PARAMETER BUILDING HELPERS
# ══════════════════════════════════════════════════════════════════════


class TestParamBuilding:
    def test_none_params_excluded(self, client, mock_session):
        """Parameters set to None should not be sent."""
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_flow_alerts(ticker=None, min_premium=None, is_sweep=None)
        params = mock_session.get.call_args[1]["params"]
        assert "ticker_symbol" not in params
        assert "min_premium" not in params
        assert "is_sweep" not in params

    def test_false_boolean_included(self, client, mock_session):
        """False boolean params should still be sent (they are not None)."""
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_flow_alerts(is_sweep=False)
        params = mock_session.get.call_args[1]["params"]
        assert params["is_sweep"] is False

    def test_zero_value_included(self, client, mock_session):
        """Zero-value params should still be sent."""
        mock_session.get.return_value = _make_response(200, {"data": []})
        client.get_flow_alerts(min_premium=0)
        params = mock_session.get.call_args[1]["params"]
        assert params["min_premium"] == 0


# ══════════════════════════════════════════════════════════════════════
# INTEGRATION: BACKWARD COMPAT WITH uw_api_get PATTERN
# ══════════════════════════════════════════════════════════════════════


class TestBackwardCompatPattern:
    """
    Ensure the client can serve the same use patterns as the old uw_api_get function.
    The old function returned {"error": "..."} on failures instead of raising.
    The new client raises exceptions, but we verify the mapping is correct.
    """

    def test_darkpool_ticker_pattern(self, client, mock_session):
        """fetch_flow.py pattern: uw_api_get(f'darkpool/{ticker}', params={'date': date})"""
        mock_session.get.return_value = _make_response(200, {"data": [{"size": 1000}]})
        result = client.get_darkpool_flow("AAPL", date="2026-03-01")
        assert "data" in result

    def test_flow_alerts_ticker_pattern(self, client, mock_session):
        """fetch_flow.py pattern: uw_api_get('option-trades/flow-alerts', params={...})"""
        mock_session.get.return_value = _make_response(200, {"data": [{"alert_rule": "test"}]})
        result = client.get_flow_alerts(ticker="AAPL", min_premium=50000, limit=100)
        assert "data" in result

    def test_stock_info_pattern(self, client, mock_session):
        """fetch_ticker.py pattern: uw_api_get(f'/stock/{ticker}/info')"""
        mock_session.get.return_value = _make_response(200, {"data": {"full_name": "Apple Inc."}})
        result = client.get_stock_info("AAPL")
        assert "data" in result

    def test_option_contracts_pattern(self, client, mock_session):
        """leap_scanner_uw.py pattern: uw_api_get(f'/stock/{ticker}/option-contracts')"""
        mock_session.get.return_value = _make_response(200, {"data": []})
        result = client.get_option_contracts("AAPL")
        assert "data" in result

    def test_iv_rank_pattern(self, client, mock_session):
        """leap_scanner_uw.py pattern: uw_api_get(f'/stock/{ticker}/iv-rank')"""
        mock_session.get.return_value = _make_response(200, {"data": [{"iv_rank_1y": 50}]})
        result = client.get_iv_rank("AAPL")
        assert "data" in result

    def test_analyst_screener_pattern(self, client, mock_session):
        """fetch_analyst_ratings.py pattern: screener/analysts with params"""
        mock_session.get.return_value = _make_response(200, {"data": []})
        result = client.get_analyst_ratings(ticker="AAPL")
        assert "data" in result
