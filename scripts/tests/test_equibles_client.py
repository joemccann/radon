"""Tests for EquiblesClient — shared Equibles API client.

RED/GREEN TDD: written before the client. No network is ever touched; the
requests.Session is replaced with a MagicMock and time.sleep is patched so
backoff is asserted, not slept.
"""
import json
import os
from datetime import date, timedelta
from pathlib import Path

import pytest
from unittest.mock import MagicMock, patch

from clients.equibles_client import (
    FRACTION_FIELDS,
    PERCENT_FIELDS,
    PERCENTILE_FIELDS,
    EquiblesAPIError,
    EquiblesAuthError,
    EquiblesClient,
    EquiblesNotFoundError,
    EquiblesRateLimitError,
    EquiblesServerError,
    EquiblesValidationError,
    PagedResult,
    normalize_fractions,
)

FIXTURES = Path(__file__).parent / "fixtures" / "equibles"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def recent_day(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


def make_response(status_code=200, json_data=None, headers=None, reason="OK"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.reason = reason
    resp.headers = headers or {}
    resp.json.return_value = json_data if json_data is not None else {}
    return resp


def error_body(code: str, status: int, message: str = "boom") -> dict:
    return {"error": {"code": code, "message": message, "status": status}}


@pytest.fixture
def mock_env_key():
    with patch.dict(os.environ, {"EQUIBLES_API_KEY": "not-a-real-key"}):
        yield


@pytest.fixture
def client(mock_env_key):
    return EquiblesClient()


@pytest.fixture
def session(client):
    mock = MagicMock()
    client._session = mock
    return mock


@pytest.fixture
def sleeps():
    """Capture backoff sleeps instead of waiting on them."""
    recorded = []
    with patch("clients.equibles_client.time.sleep", side_effect=recorded.append):
        yield recorded


def requested_url(session) -> str:
    return session.get.call_args[0][0]


def requested_params(session) -> dict:
    return session.get.call_args[1]["params"]


# ── init / lifecycle ──────────────────────────────────────────────────


class TestClientInit:
    def test_reads_key_from_env(self, mock_env_key):
        assert EquiblesClient()._api_key == "not-a-real-key"

    def test_explicit_key_used_without_env(self):
        with patch.dict(os.environ, {}, clear=True):
            assert EquiblesClient(api_key="explicit")._api_key == "explicit"

    def test_missing_key_raises_auth_error(self):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(EquiblesAuthError):
                EquiblesClient()

    def test_default_base_url(self, client):
        assert client._base_url == "https://api.equibles.com/v1"

    def test_bearer_auth_header(self, client):
        assert client._session.headers["Authorization"] == "Bearer not-a-real-key"

    def test_context_manager_closes_session(self, mock_env_key):
        with EquiblesClient() as ctx:
            ctx._session = MagicMock()
            inner = ctx._session
        inner.close.assert_called_once()


# ── request layer ─────────────────────────────────────────────────────


class TestRequestLayer:
    def test_get_builds_url_and_returns_json(self, client, session):
        session.get.return_value = make_response(json_data={"data": []})
        assert client._get("/stocks/AAPL/short-volume") == {"data": []}
        assert requested_url(session) == "https://api.equibles.com/v1/stocks/AAPL/short-volume"

    def test_get_passes_timeout(self, client, session):
        session.get.return_value = make_response(json_data={})
        client._get("cftc/positions/latest")
        assert session.get.call_args[1]["timeout"] == client._timeout

    def test_build_params_drops_none(self, client):
        assert client._build_params(limit=10, offset=None) == {"limit": 10}

    def test_safe_json_survives_unparseable_body(self, client):
        resp = make_response(status_code=500)
        resp.json.side_effect = ValueError("no json")
        assert client._safe_json(resp) == {}

    def test_request_count_increments(self, client, session):
        session.get.return_value = make_response(json_data={})
        client._get("cftc/contracts")
        client._get("cftc/contracts")
        assert client.request_count == 2


# ── error envelope → typed exceptions ─────────────────────────────────


class TestErrorMapping:
    @pytest.mark.parametrize(
        "code,status,expected",
        [
            ("invalid_parameter", 400, EquiblesValidationError),
            ("unauthorized", 401, EquiblesAuthError),
            ("not_found", 404, EquiblesNotFoundError),
        ],
    )
    def test_non_retryable_codes(self, client, session, code, status, expected):
        session.get.return_value = make_response(
            status_code=status, json_data=error_body(code, status)
        )
        with pytest.raises(expected) as exc:
            client._get("stocks/ZZZZ/short-interest")
        assert exc.value.status_code == status
        assert exc.value.code == code
        assert "boom" in str(exc.value)
        assert session.get.call_count == 1

    def test_rate_limited_raises_after_retries(self, client, session, sleeps):
        session.get.return_value = make_response(
            status_code=429,
            json_data=load_fixture("error_rate_limited"),
            headers={"Retry-After": "2"},
        )
        with pytest.raises(EquiblesRateLimitError) as exc:
            client._get("stocks/AAPL/short-volume")
        assert exc.value.code == "rate_limited"
        assert session.get.call_count == 1 + client._max_retries

    @pytest.mark.parametrize("status,code", [(500, "internal_error"), (503, "service_unavailable")])
    def test_server_errors_raise_server_error(self, client, session, sleeps, status, code):
        session.get.return_value = make_response(
            status_code=status, json_data=error_body(code, status)
        )
        with pytest.raises(EquiblesServerError):
            client._get("cftc/positions/latest")

    def test_unknown_status_raises_base_error(self, client, session):
        session.get.return_value = make_response(status_code=418, json_data={})
        with pytest.raises(EquiblesAPIError):
            client._get("cftc/contracts")

    def test_all_errors_share_base(self):
        for exc in (
            EquiblesAuthError,
            EquiblesRateLimitError,
            EquiblesNotFoundError,
            EquiblesValidationError,
            EquiblesServerError,
        ):
            assert issubclass(exc, EquiblesAPIError)


# ── retry / backoff ───────────────────────────────────────────────────


class TestRetryBackoff:
    def test_server_error_then_success(self, client, session, sleeps):
        session.get.side_effect = [
            make_response(status_code=500, json_data=error_body("internal_error", 500)),
            make_response(json_data={"data": [1]}),
        ]
        assert client._get("cftc/contracts") == {"data": [1]}
        assert sleeps == [1.0]

    def test_backoff_is_exponential_and_bounded(self, client, session, sleeps):
        session.get.return_value = make_response(
            status_code=500, json_data=error_body("internal_error", 500)
        )
        with pytest.raises(EquiblesServerError):
            client._get("cftc/contracts")
        assert sleeps == [1.0, 2.0, 4.0]
        assert session.get.call_count == 4

    def test_retry_after_header_honored_on_429(self, client, session, sleeps):
        session.get.side_effect = [
            make_response(
                status_code=429,
                json_data=error_body("rate_limited", 429),
                headers={"Retry-After": "7"},
            ),
            make_response(json_data={"ok": True}),
        ]
        assert client._get("stocks/AAPL/short-volume") == {"ok": True}
        assert sleeps == [7.0]

    def test_connection_error_retried_then_raises(self, client, session, sleeps):
        import requests

        session.get.side_effect = requests.ConnectionError("down")
        with pytest.raises(EquiblesAPIError):
            client._get("cftc/contracts")
        assert session.get.call_count == 1 + client._max_retries


# ── rate-limit budget headers ─────────────────────────────────────────


class TestRateLimitHeaders:
    def test_headers_exposed_as_attributes(self, client, session):
        session.get.return_value = make_response(
            json_data={},
            headers={
                "X-RateLimit-Limit": "100000",
                "X-RateLimit-Remaining": "99412",
                "X-RateLimit-Reset": "1786500000",
            },
        )
        client._get("cftc/contracts")
        assert client.rate_limit_limit == 100000
        assert client.rate_limit_remaining == 99412
        assert client.rate_limit_reset == 1786500000

    def test_headers_recorded_on_error_responses_too(self, client, session):
        session.get.return_value = make_response(
            status_code=404,
            json_data=error_body("not_found", 404),
            headers={"X-RateLimit-Remaining": "5"},
        )
        with pytest.raises(EquiblesNotFoundError):
            client._get("stocks/ZZZZ/short-volume")
        assert client.rate_limit_remaining == 5

    def test_missing_headers_leave_attributes_none(self, client, session):
        session.get.return_value = make_response(json_data={})
        client._get("cftc/contracts")
        assert client.rate_limit_remaining is None


# ── fraction normalization (locks the contract) ───────────────────────


class TestFractionNormalization:
    """The API MIXES fractions and percents. These lock which is which.

    Ground truth verified live 2026-08-11 against shortPosition/sharesOutstanding;
    the full field table lives in docs/equibles-api.md.
    """

    def test_short_volume_percent_is_already_a_percent_and_passes_through(self, client, session):
        """Regression: scaling this rendered 3937% for NVDA."""
        session.get.return_value = make_response(json_data=load_fixture("short_volume"))
        row = client.get_short_volume("AAPL")["data"][0]
        assert row["shortVolumePercent"] == pytest.approx(4.23)
        assert "shortVolumePct" not in row

    def test_short_volume_percent_agrees_with_its_own_raw_counts(self, client, session):
        """The percent field and the raw counts must describe the same quantity."""
        session.get.return_value = make_response(json_data=load_fixture("short_volume"))
        row = client.get_short_volume("AAPL")["data"][0]
        computed = row["shortVolume"] / row["totalVolume"] * 100
        assert row["shortVolumePercent"] == pytest.approx(computed, rel=1e-3)

    def test_percent_of_total_passes_through_untouched(self, client, session):
        """institutional-holders reports percentOfTotal as a percent, not a fraction."""
        session.get.return_value = make_response(json_data=load_fixture("institutional_holders"))
        row = client.get_institutional_holders("AAPL")["data"][0]
        assert row["percentOfTotal"] == pytest.approx(5.12)

    def test_short_interest_percent_of_shares_normalized(self, client, session):
        session.get.return_value = make_response(json_data=load_fixture("short_squeeze_scores"))
        row = client.get_short_squeeze_scores(ticker="GME")["data"][0]
        assert row["shortInterestPctOfShares"] == pytest.approx(22.11)
        assert "shortInterestPercentOfShares" not in row

    def test_remaining_squeeze_fractions_normalized(self, client, session):
        """These three arrive as fractions and previously leaked through raw."""
        session.get.return_value = make_response(json_data=load_fixture("short_squeeze_scores"))
        row = client.get_short_squeeze_scores(ticker="GME")["data"][0]
        assert row["shortVolumeShareTrendPct"] == pytest.approx(16.03)
        assert row["shortInterestChangePct"] == pytest.approx(-3.27)
        assert row["failsToDeliverPctOfShares"] == pytest.approx(0.28)
        assert row["priceAboveVwapPct"] == pytest.approx(6.42)

    def test_percentile_fields_are_ranks_and_never_scaled(self, client, session):
        session.get.return_value = make_response(json_data=load_fixture("short_squeeze_scores"))
        row = client.get_short_squeeze_scores(ticker="GME")["data"][0]
        assert row["shortInterestPercentile"] == pytest.approx(98.82)
        assert row["daysToCoverPercentile"] == pytest.approx(85.53)
        assert row["shortVolumeTrendPercentile"] == pytest.approx(87.47)

    def test_every_declared_percent_field_survives_normalization(self):
        """No field may appear in both registries - that would be a 100x bug."""
        assert PERCENT_FIELDS.isdisjoint(FRACTION_FIELDS.keys())
        assert PERCENTILE_FIELDS.isdisjoint(FRACTION_FIELDS.keys())
        payload = {"data": [{name: 42.0 for name in PERCENT_FIELDS | PERCENTILE_FIELDS}]}
        row = normalize_fractions(payload)["data"][0]
        assert all(row[name] == 42.0 for name in PERCENT_FIELDS | PERCENTILE_FIELDS)

    def test_non_ratio_fields_untouched(self, client, session):
        session.get.return_value = make_response(json_data=load_fixture("short_volume"))
        row = client.get_short_volume("AAPL")["data"][0]
        assert row["shortVolume"] == 4213551
        assert row["date"] == "2026-08-07"

    def test_null_ratio_stays_null(self, client, session):
        session.get.return_value = make_response(
            json_data={"data": [{"shortInterestPercentOfShares": None}]}
        )
        row = client.get_short_squeeze_scores(ticker="GME")["data"][0]
        assert row["shortInterestPctOfShares"] is None

    def test_deeply_nested_structures_preserved(self, client, session):
        session.get.return_value = make_response(json_data=load_fixture("atm_programs"))
        row = client.get_atm_programs("AAPL")["data"][0]
        assert row["capacity"]["amount"] == 500000000
        assert row["isFullyUtilized"] is False

    def test_normalization_can_be_disabled(self, mock_env_key):
        raw_client = EquiblesClient(normalize_ratios=False)
        raw_client._session = MagicMock()
        raw_client._session.get.return_value = make_response(
            json_data=load_fixture("short_squeeze_scores")
        )
        row = raw_client.get_short_squeeze_scores(ticker="GME")["data"][0]
        assert row["shortInterestPercentOfShares"] == pytest.approx(0.2211)
        assert "shortInterestPctOfShares" not in row


# ── pagination ────────────────────────────────────────────────────────


def paged_response(rows, has_more):
    return make_response(json_data={"data": rows, "meta": {"hasMore": has_more}})


class TestPagination:
    def test_walks_until_has_more_false(self, client, session):
        session.get.side_effect = [
            paged_response([{"n": 1}, {"n": 2}], True),
            paged_response([{"n": 3}], False),
        ]
        result = client.paginate(client.get_short_volume, "AAPL", limit=2)
        assert isinstance(result, PagedResult)
        assert [r["n"] for r in result.rows] == [1, 2, 3]
        assert result.pages == 2
        assert result.requests_used == 2
        assert result.has_more is False

    def test_advances_offset_by_limit(self, client, session):
        session.get.side_effect = [
            paged_response([{"n": 1}], True),
            paged_response([{"n": 2}], False),
        ]
        client.paginate(client.get_short_volume, "AAPL", limit=1)
        offsets = [call[1]["params"]["offset"] for call in session.get.call_args_list]
        assert offsets == [0, 1]

    def test_max_pages_bounds_the_walk(self, client, session):
        session.get.side_effect = [paged_response([{"n": i}], True) for i in range(10)]
        result = client.paginate(client.get_short_volume, "AAPL", limit=1, max_pages=3)
        assert result.pages == 3
        assert result.requests_used == 3
        assert result.has_more is True
        assert session.get.call_count == 3

    def test_default_max_pages_is_modest_and_bounded(self, client):
        assert 1 <= client._max_pages <= 20

    def test_requests_used_counts_retries(self, client, session, sleeps):
        session.get.side_effect = [
            make_response(status_code=500, json_data=error_body("internal_error", 500)),
            paged_response([{"n": 1}], False),
        ]
        result = client.paginate(client.get_short_volume, "AAPL", limit=1)
        assert result.requests_used == 2

    def test_passes_date_window_through(self, client, session):
        session.get.side_effect = [paged_response([], False)]
        start, end = recent_day(30), recent_day(0)
        client.paginate(client.get_short_volume, "AAPL", limit=5, start_date=start, end_date=end)
        params = requested_params(session)
        assert params["startDate"] == start
        assert params["endDate"] == end

    def test_normalizes_rows_while_walking(self, client, session):
        session.get.side_effect = [
            make_response(
                json_data={
                    "data": [{"shortInterestPercentOfShares": 0.05}],
                    "meta": {"hasMore": False},
                }
            )
        ]
        result = client.paginate(client.get_short_squeeze_scores, limit=1)
        assert result.rows[0]["shortInterestPctOfShares"] == pytest.approx(5.0)


# ── F1: ATS venue share ───────────────────────────────────────────────


class TestOffExchangeEndpoints:
    def test_off_exchange_volume(self, client, session):
        session.get.return_value = make_response(json_data=load_fixture("off_exchange_volume"))
        start, end = recent_day(60), recent_day(0)
        client.get_off_exchange_volume("aapl", start_date=start, end_date=end, limit=100, offset=0)
        assert requested_url(session).endswith("/stocks/AAPL/off-exchange-volume")
        assert requested_params(session) == {
            "startDate": start,
            "endDate": end,
            "limit": 100,
            "offset": 0,
        }

    def test_short_volume(self, client, session):
        session.get.return_value = make_response(json_data=load_fixture("short_volume"))
        client.get_short_volume("AAPL", limit=5)
        assert requested_url(session).endswith("/stocks/AAPL/short-volume")
        assert requested_params(session) == {"limit": 5}

    def test_largest_short_volume(self, client, session):
        session.get.return_value = make_response(json_data={"data": []})
        client.get_largest_short_volume(
            min_average_daily_volume=1000000, min_market_cap=2000000000,
            min_dollar_volume=50000000, sort_by="shortVolumePercent", limit=25,
        )
        assert requested_url(session).endswith("/short-volume/largest")
        assert requested_params(session) == {
            "minAverageDailyVolume": 1000000,
            "minMarketCap": 2000000000,
            "minDollarVolume": 50000000,
            "sortBy": "shortVolumePercent",
            "limit": 25,
        }


# ── F2: short crowding / squeeze ──────────────────────────────────────


class TestShortInterestEndpoints:
    def test_short_interest(self, client, session):
        session.get.return_value = make_response(json_data={"data": []})
        client.get_short_interest("gme", start_date=recent_day(90))
        assert requested_url(session).endswith("/stocks/GME/short-interest")
        assert requested_params(session)["startDate"] == recent_day(90)

    def test_short_interest_snapshot(self, client, session):
        session.get.return_value = make_response(json_data={"data": []})
        client.get_short_interest_snapshot(min_days_to_cover=5, sort_by="daysToCover", limit=50)
        assert requested_url(session).endswith("/short-interest/snapshot")
        assert requested_params(session) == {
            "minDaysToCover": 5,
            "sortBy": "daysToCover",
            "limit": 50,
        }

    def test_short_squeeze_scores(self, client, session):
        session.get.return_value = make_response(json_data=load_fixture("short_squeeze_scores"))
        client.get_short_squeeze_scores(ticker="gme", limit=10, offset=0)
        assert requested_url(session).endswith("/short-squeeze-scores")
        assert requested_params(session) == {"ticker": "GME", "limit": 10, "offset": 0}


# ── F3: 13F smart money ───────────────────────────────────────────────


class TestInstitutionalEndpoints:
    def test_institutional_holders(self, client, session):
        session.get.return_value = make_response(json_data=load_fixture("institutional_holders"))
        client.get_institutional_holders("aapl", report_date="2026-06-30", limit=500, offset=0)
        assert requested_url(session).endswith("/stocks/AAPL/institutional-holders")
        assert requested_params(session)["reportDate"] == "2026-06-30"

    def test_institutional_ownership(self, client, session):
        session.get.return_value = make_response(json_data={"data": []})
        client.get_institutional_ownership("AAPL", periods=8)
        assert requested_url(session).endswith("/stocks/AAPL/institutional-ownership")
        assert requested_params(session) == {"periods": 8}

    def test_institutional_activity(self, client, session):
        session.get.return_value = make_response(json_data={"data": []})
        client.get_institutional_activity("AAPL", report_date="2026-06-30", limit=20)
        assert requested_url(session).endswith("/stocks/AAPL/institutional-activity")

    def test_13f_market_activity(self, client, session):
        session.get.return_value = make_response(json_data={"data": []})
        client.get_13f_market_activity(bucket="top-buys", report_date="2026-06-30", limit=25)
        assert requested_url(session).endswith("/13f/market-activity")
        assert requested_params(session)["bucket"] == "top-buys"

    def test_13f_most_held(self, client, session):
        session.get.return_value = make_response(json_data={"data": []})
        client.get_13f_most_held(sort="filersDelta", limit=25)
        assert requested_url(session).endswith("/13f/most-held")
        assert requested_params(session)["sort"] == "filersDelta"

    def test_search_institutions(self, client, session):
        session.get.return_value = make_response(json_data={"data": []})
        client.search_institutions("berkshire", limit=5)
        assert requested_url(session).endswith("/institutions")
        assert requested_params(session) == {"query": "berkshire", "limit": 5}

    def test_institution_portfolio(self, client, session):
        session.get.return_value = make_response(json_data={"data": []})
        client.get_institution_portfolio("0001067983", limit=100)
        assert requested_url(session).endswith("/institutions/0001067983/portfolio")

    def test_institution_summary(self, client, session):
        session.get.return_value = make_response(json_data={})
        client.get_institution_summary("0001067983")
        assert requested_url(session).endswith("/institutions/0001067983/summary")

    def test_institution_sector_allocation(self, client, session):
        session.get.return_value = make_response(json_data={})
        client.get_institution_sector_allocation("0001067983")
        assert requested_url(session).endswith("/institutions/0001067983/sector-allocation")

    def test_institution_activity(self, client, session):
        session.get.return_value = make_response(json_data={})
        client.get_institution_activity("0001067983", report_date="2026-06-30")
        assert requested_url(session).endswith("/institutions/0001067983/activity")

    def test_super_investors(self, client, session):
        session.get.return_value = make_response(json_data={"data": []})
        client.get_super_investors()
        assert requested_url(session).endswith("/super-investors")

    def test_consensus_holdings_joins_institutions(self, client, session):
        session.get.return_value = make_response(json_data={"data": []})
        client.get_consensus_holdings(["0001067983", "0001350694"], min_funds=2, limit=50)
        assert requested_url(session).endswith("/consensus-holdings")
        assert requested_params(session)["institutions"] == "0001067983,0001350694"


# ── F4: CFTC COT positioning ──────────────────────────────────────────


class TestCftcEndpoints:
    def test_contracts_search(self, client, session):
        session.get.return_value = make_response(json_data={"data": []})
        client.get_cftc_contracts(query="gold", alias="GC")
        assert requested_url(session).endswith("/cftc/contracts")
        assert requested_params(session) == {"query": "gold", "alias": "GC"}

    def test_contract_positions(self, client, session):
        session.get.return_value = make_response(json_data={"data": []})
        client.get_cftc_positions("088691", start_date=recent_day(365), limit=100, offset=0)
        assert requested_url(session).endswith("/cftc/contracts/088691/positions")
        assert requested_params(session)["startDate"] == recent_day(365)

    def test_latest_positions_by_category(self, client, session):
        session.get.return_value = make_response(json_data=load_fixture("cftc_positions_latest"))
        payload = client.get_cftc_latest_positions(category="Metals")
        assert requested_url(session).endswith("/cftc/positions/latest")
        assert requested_params(session) == {"category": "Metals"}
        assert payload["data"][0]["netCommercial"] == -218411


# ── F5: filing forensics ──────────────────────────────────────────────


class TestFilingForensicsEndpoints:
    def test_proposed_sales(self, client, session):
        session.get.return_value = make_response(json_data={"data": []})
        client.get_proposed_sales("aapl", limit=50)
        assert requested_url(session).endswith("/stocks/AAPL/proposed-sales")

    def test_atm_programs(self, client, session):
        session.get.return_value = make_response(json_data=load_fixture("atm_programs"))
        client.get_atm_programs("AAPL")
        assert requested_url(session).endswith("/stocks/AAPL/atm-programs")

    def test_buyback_programs(self, client, session):
        session.get.return_value = make_response(json_data={"data": []})
        client.get_buyback_programs("AAPL")
        assert requested_url(session).endswith("/stocks/AAPL/buyback-programs")

    def test_executive_changes(self, client, session):
        session.get.return_value = make_response(json_data={"data": []})
        client.get_executive_changes(
            "AAPL", action="Resigned", start_date=recent_day(180), limit=100, offset=0
        )
        assert requested_url(session).endswith("/stocks/AAPL/executive-changes")
        assert requested_params(session)["action"] == "Resigned"

    def test_stock_screener_passes_camel_case_filters(self, client, session):
        session.get.return_value = make_response(json_data={"data": [], "totalCount": 0})
        client.get_stock_screener(
            hasGoingConcernDoubt=True, minShortInterestPercent=15, minSqueezeScore=70, limit=50
        )
        assert requested_url(session).endswith("/screener/stocks")
        assert requested_params(session) == {
            "hasGoingConcernDoubt": True,
            "minShortInterestPercent": 15,
            "minSqueezeScore": 70,
            "limit": 50,
        }


# ── no network under test ─────────────────────────────────────────────


class TestNoRealNetwork:
    def test_every_endpoint_goes_through_get(self, client, session):
        session.get.return_value = make_response(json_data={})
        client.get_super_investors()
        assert session.get.called
        assert not hasattr(session, "post") or not session.post.called
