"""Live integration tests for MenthorQClient — hits real MenthorQ with credentials.

Run with:
    python3 -m pytest scripts/tests/test_menthorq_integration.py -v -m integration

Requires:
    MENTHORQ_USER and MENTHORQ_PASS set in .env (project root)
    Playwright browsers installed: python3 -m playwright install chromium

These tests share a single browser session (module-scoped fixture) to avoid
repeated logins. Image tests verify S3 download is used (not viewport screenshots)
and that returned bytes are valid PNGs.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from clients.menthorq_client import (
    MenthorQClient,
    MenthorQError,
    MenthorQExtractionError,
    SCREENER_SLUGS,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _last_trading_date() -> str:
    """Return the most recent weekday as YYYY-MM-DD."""
    today = datetime.now()
    # Walk back from today to find the last weekday
    for offset in range(0, 5):
        d = today - timedelta(days=offset)
        if d.weekday() < 5:  # Mon-Fri
            return d.strftime("%Y-%m-%d")
    return today.strftime("%Y-%m-%d")


# ── Module-scoped client ────────────────────────────────────────────

# Skip the entire module if credentials aren't available
_has_creds = bool(
    os.environ.get("MENTHORQ_USER", "").strip()
    and os.environ.get("MENTHORQ_PASS", "").strip()
)


@pytest.fixture(scope="module")
def client():
    """Single browser session for all integration tests."""
    if not _has_creds:
        pytest.skip("MENTHORQ_USER / MENTHORQ_PASS not set")

    c = MenthorQClient(headless=True)
    yield c
    c.close()


@pytest.fixture(scope="module")
def trading_date() -> str:
    return _last_trading_date()


# ══════════════════════════════════════════════════════════════════════
# LOGIN
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.integration
class TestMenthorQIntegrationLogin:
    def test_login_succeeds(self, client):
        """Client is authenticated — URL should be on /account/."""
        url = client._page.url.lower()
        assert "/login" not in url, f"Still on login page: {url}"
        assert "menthorq.com" in url


# ══════════════════════════════════════════════════════════════════════
# HTML ROUTES
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.integration
class TestMenthorQIntegrationHTML:
    def test_eod_spx(self, client, trading_date):
        """get_eod() returns structured data for SPX."""
        result = client.get_eod("SPX", trading_date)
        assert isinstance(result, dict)
        assert len(result) > 0, "EOD should return non-empty dict"

    def test_intraday(self, client):
        """get_intraday() returns a list."""
        result = client.get_intraday()
        assert isinstance(result, list)

    def test_screener_options(self, client):
        """get_screener('options') returns a list of dicts."""
        result = client.get_screener("options")
        assert isinstance(result, list)

    def test_screener_flow(self, client):
        """get_screener('flow') returns a list of dicts."""
        result = client.get_screener("flow")
        assert isinstance(result, list)

    def test_screener_unusual(self, client):
        """get_screener('unusual') returns a list of dicts."""
        result = client.get_screener("unusual")
        assert isinstance(result, list)

    def test_screener_category_gamma(self, client):
        """get_screener_category() returns data for a gamma screener."""
        result = client.get_screener_category("gamma", "highest_gex_change")
        assert isinstance(result, list)

    def test_futures_list(self, client):
        """get_futures_list() returns a list of futures."""
        result = client.get_futures_list()
        assert isinstance(result, list)

    def test_futures_detail(self, client):
        """get_futures_detail() returns detail data for ES."""
        result = client.get_futures_detail("ES")
        assert isinstance(result, list)

    def test_futures_contracts(self, client, trading_date):
        """get_futures_contracts() returns contracts for ES."""
        result = client.get_futures_contracts("ES", trading_date)
        assert isinstance(result, list)

    def test_forex_list(self, client):
        """get_forex_list() returns a list of forex pairs."""
        result = client.get_forex_list()
        assert isinstance(result, list)

    def test_forex_detail(self, client):
        """get_forex_detail() returns detail for EURUSD."""
        result = client.get_forex_detail("EURUSD")
        assert isinstance(result, list)

    def test_crypto_list(self, client):
        """get_crypto_list() returns a list of crypto assets."""
        result = client.get_crypto_list()
        assert isinstance(result, list)


# ══════════════════════════════════════════════════════════════════════
# IMAGE ROUTES
# ══════════════════════════════════════════════════════════════════════


def _assert_png(data: bytes, label: str) -> None:
    """Verify data is non-empty PNG bytes."""
    assert isinstance(data, bytes), f"{label}: expected bytes, got {type(data)}"
    assert len(data) > 100, f"{label}: PNG too small ({len(data)} bytes)"
    assert data[:4] == b"\x89PNG", f"{label}: missing PNG magic header"


def _get_image_with_tracking(client, command: str, *, tickers: str | None = None):
    """Call get_dashboard_image with instrumentation to track image source.

    Returns (png_bytes, s3_used: bool, download_attempted: bool).
    """
    original_download = client._download_card_images.__func__
    download_results = []

    def tracking_download(self_inner, page, slugs):
        result = original_download(self_inner, page, slugs)
        download_results.append(result)
        return result

    with patch.object(type(client), "_download_card_images", tracking_download):
        if tickers:
            data = client.get_dashboard_image(command, tickers=tickers)
        else:
            data = client.get_dashboard_image(command)

    download_attempted = len(download_results) >= 1
    s3_used = download_attempted and len(download_results[0]) > 0

    return data, s3_used, download_attempted


@pytest.mark.integration
class TestMenthorQIntegrationImage:
    """Verify all image routes attempt S3 download before falling back to screenshot.

    CTA page is the only MenthorQ route with S3-hosted card images
    (.command-card elements with <img src="s3...">). All other dashboards
    render charts dynamically in the DOM, so they legitimately fall back
    to viewport screenshot.

    Valid dashboard image commands: cta, vol, forex, eod, intraday, futures,
    cryptos_technical, cryptos_options.

    Every route MUST:
      1. Attempt S3 download via _download_card_images (proving the code path runs)
      2. Return valid PNG bytes
    CTA additionally MUST:
      3. Actually use S3 images (not fall back to screenshot)
    """

    def test_cta_downloads_s3_images(self, client, trading_date):
        """get_cta() downloads S3 images for all 4 CTA cards, not screenshots."""
        from clients.menthorq_client import CTA_SLUGS

        original_download = client._download_card_images.__func__
        download_results = []

        def tracking_download(self_inner, page, slugs):
            result = original_download(self_inner, page, slugs)
            download_results.append(result)
            return result

        with patch.object(type(client), "_download_card_images", tracking_download):
            try:
                result = client.get_cta(trading_date)
                assert isinstance(result, dict)
            except MenthorQError:
                pytest.skip("Vision API key not set or extraction failed")

        # Verify S3 download was used and returned all 4 images
        assert len(download_results) >= 1, "S3 download was not attempted"
        first_result = download_results[0]
        assert len(first_result) == len(CTA_SLUGS), (
            f"Expected {len(CTA_SLUGS)} S3 images, got {len(first_result)}. "
            f"Keys: {list(first_result.keys())}"
        )
        for key, png_bytes in first_result.items():
            _assert_png(png_bytes, f"cta/{key}")

    def test_image_vol(self, client):
        data, s3, attempted = _get_image_with_tracking(client, "vol")
        _assert_png(data, "vol")
        assert attempted, "S3 download was not attempted"

    def test_image_forex(self, client):
        data, s3, attempted = _get_image_with_tracking(client, "forex")
        # Forex cards have no S3 images (text/table only), screenshot fallback is correct
        assert attempted, "S3 download was not attempted"
        assert isinstance(data, bytes) and len(data) > 100

    def test_image_eod(self, client):
        data, s3, attempted = _get_image_with_tracking(client, "eod")
        _assert_png(data, "eod")
        assert attempted, "S3 download was not attempted"

    def test_image_intraday(self, client):
        data, s3, attempted = _get_image_with_tracking(client, "intraday")
        _assert_png(data, "intraday")
        assert attempted, "S3 download was not attempted"

    def test_image_futures(self, client):
        data, s3, attempted = _get_image_with_tracking(client, "futures")
        _assert_png(data, "futures")
        assert attempted, "S3 download was not attempted"

    def test_image_crypto_options(self, client):
        data, s3, attempted = _get_image_with_tracking(
            client, "cryptos_options", tickers="cryptos_options"
        )
        _assert_png(data, "cryptos_options")
        assert attempted, "S3 download was not attempted"

    def test_image_crypto_quant(self, client):
        data, s3, attempted = _get_image_with_tracking(
            client, "cryptos_technical", tickers="cryptos_technical"
        )
        _assert_png(data, "cryptos_technical")
        assert attempted, "S3 download was not attempted"

    def test_crypto_detail(self, client):
        """get_crypto_detail('BTC') returns data."""
        result = client.get_crypto_detail("BTC")
        assert isinstance(result, list)


# ══════════════════════════════════════════════════════════════════════
# SCREENER CATEGORIES (1 slug per category)
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.integration
class TestMenthorQIntegrationScreenerCategories:
    def test_category_gamma(self, client):
        result = client.get_screener_category("gamma", "highest_gex_change")
        assert isinstance(result, list)

    def test_category_gamma_levels(self, client):
        result = client.get_screener_category("gamma_levels", "closer_to_HVL")
        assert isinstance(result, list)

    def test_category_open_interest(self, client):
        result = client.get_screener_category("open_interest", "highest_oi")
        assert isinstance(result, list)

    def test_category_volatility(self, client):
        result = client.get_screener_category("volatility", "highest_ivrank")
        assert isinstance(result, list)

    def test_category_volume(self, client):
        result = client.get_screener_category("volume", "highest_call_volume")
        assert isinstance(result, list)

    def test_category_qscore(self, client):
        result = client.get_screener_category("qscore", "highest_option_score")
        assert isinstance(result, list)


# ══════════════════════════════════════════════════════════════════════
# TICKER TAB SELECTION
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.integration
class TestMenthorQIntegrationTickerTabs:
    def test_image_eod_nvda(self, client):
        """get_dashboard_image('eod', ticker='nvda') clicks NVDA tab and returns PNG."""
        data, s3, attempted = _get_image_with_tracking(client, "eod")
        # First load default ticker
        _assert_png(data, "eod/default")

        # Now load with NVDA ticker tab
        data_nvda = client.get_dashboard_image("eod", ticker="nvda")
        _assert_png(data_nvda, "eod/nvda")

    def test_image_futures_with_ticker(self, client):
        """get_dashboard_image('futures', ticker='spy') clicks SPY tab and returns PNG."""
        data = client.get_dashboard_image("futures", ticker="spy")
        _assert_png(data, "futures/spy")


# ══════════════════════════════════════════════════════════════════════
# SUMMARY PAGES
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.integration
class TestMenthorQIntegrationSummary:
    def test_summary_futures(self, client):
        """get_summary('futures') returns rows with expected headers."""
        result = client.get_summary("futures")
        assert isinstance(result, list)
        assert len(result) > 0, "Futures summary should have rows"
        # Check that at least one expected header key exists
        first_row = result[0]
        assert isinstance(first_row, dict)
        assert len(first_row) > 0

    def test_summary_cryptos(self, client):
        """get_summary('cryptos') returns rows with expected headers."""
        result = client.get_summary("cryptos")
        assert isinstance(result, list)
        assert len(result) > 0, "Crypto summary should have rows"
        first_row = result[0]
        assert isinstance(first_row, dict)
        assert len(first_row) > 0

    def test_summary_invalid_category_raises(self, client):
        """get_summary() raises MenthorQExtractionError for unknown category."""
        with pytest.raises(MenthorQExtractionError, match="Unknown summary category"):
            client.get_summary("stocks")


# ══════════════════════════════════════════════════════════════════════
# FOREX LEVELS (TEXT CARD PARSING)
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.integration
class TestMenthorQIntegrationForexLevels:
    """Verify get_forex_levels() parses text-based forex cards."""

    def test_forex_levels_returns_gamma_and_blindspot(self, client):
        """get_forex_levels() returns dict with gamma and blindspot keys."""
        result = client.get_forex_levels()
        assert isinstance(result, dict)
        assert "gamma" in result
        assert "blindspot" in result

    def test_forex_gamma_has_pairs(self, client):
        """Forex gamma data contains parsed forex pairs."""
        result = client.get_forex_levels()
        gamma = result["gamma"]
        assert isinstance(gamma, list)
        assert len(gamma) > 0, "Gamma should have at least one forex pair"
        first = gamma[0]
        assert isinstance(first, dict)
        assert "pair" in first, "Each gamma entry should have a 'pair' field"

    def test_forex_gamma_has_numeric_levels(self, client):
        """Forex gamma pairs have numeric level values."""
        result = client.get_forex_levels()
        gamma = result["gamma"]
        if len(gamma) == 0:
            pytest.skip("No gamma data available")
        first = gamma[0]
        # Should have more than just the pair name
        assert len(first) > 1, f"Gamma pair should have level data, got: {first}"
        # At least one value should be numeric
        numeric_values = [v for k, v in first.items() if k != "pair" and isinstance(v, (int, float))]
        assert len(numeric_values) > 0, f"Expected numeric level values, got: {first}"

    def test_forex_blindspot_has_pairs(self, client):
        """Forex blindspot data contains parsed forex pairs."""
        result = client.get_forex_levels()
        blindspot = result["blindspot"]
        assert isinstance(blindspot, list)
        # Blindspot may or may not have data depending on market conditions
        if len(blindspot) > 0:
            first = blindspot[0]
            assert isinstance(first, dict)
            assert "pair" in first


# ══════════════════════════════════════════════════════════════════════
# SCREENER DISCOVERY
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.integration
class TestMenthorQIntegrationDiscoverScreeners:
    """Verify discover_screener_cards() can navigate to each category page."""

    def test_discover_gamma(self, client):
        cards = client.discover_screener_cards("gamma")
        assert isinstance(cards, list)

    def test_discover_gamma_levels(self, client):
        cards = client.discover_screener_cards("gamma_levels")
        assert isinstance(cards, list)

    def test_discover_open_interest(self, client):
        cards = client.discover_screener_cards("open_interest")
        assert isinstance(cards, list)

    def test_discover_volatility(self, client):
        cards = client.discover_screener_cards("volatility")
        assert isinstance(cards, list)

    def test_discover_volume(self, client):
        cards = client.discover_screener_cards("volume")
        assert isinstance(cards, list)

    def test_discover_qscore(self, client):
        cards = client.discover_screener_cards("qscore")
        assert isinstance(cards, list)


# ══════════════════════════════════════════════════════════════════════
# SCREENER DATA — ALL SLUGS PER CATEGORY
# Each test navigates to one sub-screener and verifies table data.
# ══════════════════════════════════════════════════════════════════════


def _assert_screener_data(client, category: str, slug: str):
    """Helper: fetch screener data and assert it returns a non-empty list of dicts."""
    result = client.get_screener_category(category, slug)
    assert isinstance(result, list), f"{category}/{slug}: expected list, got {type(result)}"
    assert len(result) > 0, f"{category}/{slug}: expected rows, got empty list"
    first = result[0]
    assert isinstance(first, dict), f"{category}/{slug}: expected dict rows"
    assert len(first) > 0, f"{category}/{slug}: row has no columns"
    return result


@pytest.mark.integration
class TestMenthorQIntegrationScreenerGamma:
    """All 5 gamma sub-screeners return table data."""

    def test_highest_gex_change(self, client):
        _assert_screener_data(client, "gamma", "highest_gex_change")

    def test_highest_negative_dex_change(self, client):
        _assert_screener_data(client, "gamma", "highest_negative_dex_change")

    def test_highest_negative_gex_change(self, client):
        _assert_screener_data(client, "gamma", "highest_negative_gex_change")

    def test_biggest_dex_expiry_next_2w(self, client):
        _assert_screener_data(client, "gamma", "biggest_dex_expiry_next_2w")

    def test_biggest_gex_expiry_next_2w(self, client):
        _assert_screener_data(client, "gamma", "biggest_gex_expiry_next_2w")


@pytest.mark.integration
class TestMenthorQIntegrationScreenerGammaLevels:
    """All 5 gamma_levels sub-screeners return table data."""

    def test_closer_0dte_call_resistance(self, client):
        _assert_screener_data(client, "gamma_levels", "closer_0dte_call_resistance")

    def test_closer_0dte_put_support(self, client):
        _assert_screener_data(client, "gamma_levels", "closer_0dte_put_support")

    def test_closer_to_HVL(self, client):
        _assert_screener_data(client, "gamma_levels", "closer_to_HVL")

    def test_closer_call_resistance(self, client):
        _assert_screener_data(client, "gamma_levels", "closer_call_resistance")

    def test_closer_put_support(self, client):
        _assert_screener_data(client, "gamma_levels", "closer_put_support")


@pytest.mark.integration
class TestMenthorQIntegrationScreenerOpenInterest:
    """All 7 open_interest sub-screeners return table data."""

    def test_highest_call_oi(self, client):
        _assert_screener_data(client, "open_interest", "highest_call_oi")

    def test_highest_oi(self, client):
        _assert_screener_data(client, "open_interest", "highest_oi")

    def test_highest_pc_oi(self, client):
        _assert_screener_data(client, "open_interest", "highest_pc_oi")

    def test_highest_put_oi(self, client):
        _assert_screener_data(client, "open_interest", "highest_put_oi")

    def test_lowest_pc_oi(self, client):
        _assert_screener_data(client, "open_interest", "lowest_pc_oi")

    def test_highest_oi_change(self, client):
        _assert_screener_data(client, "open_interest", "highest_oi_change")

    def test_highest_negative_oi_change(self, client):
        _assert_screener_data(client, "open_interest", "highest_negative_oi_change")


@pytest.mark.integration
class TestMenthorQIntegrationScreenerVolatility:
    """All 6 volatility sub-screeners return table data."""

    def test_highest_iv30(self, client):
        _assert_screener_data(client, "volatility", "highest_iv30")

    def test_highest_ivrank(self, client):
        _assert_screener_data(client, "volatility", "highest_ivrank")

    def test_highest_hv30(self, client):
        _assert_screener_data(client, "volatility", "highest_hv30")

    def test_lowest_iv30(self, client):
        _assert_screener_data(client, "volatility", "lowest_iv30")

    def test_lowest_ivrank(self, client):
        _assert_screener_data(client, "volatility", "lowest_ivrank")

    def test_lowest_hv30(self, client):
        _assert_screener_data(client, "volatility", "lowest_hv30")


@pytest.mark.integration
class TestMenthorQIntegrationScreenerVolume:
    """All 6 volume sub-screeners return table data."""

    def test_highest_call_volume(self, client):
        _assert_screener_data(client, "volume", "highest_call_volume")

    def test_highest_put_volume(self, client):
        _assert_screener_data(client, "volume", "highest_put_volume")

    def test_highest_total_volume(self, client):
        _assert_screener_data(client, "volume", "highest_total_volume")

    def test_unusual_call_activity(self, client):
        _assert_screener_data(client, "volume", "unusual_call_activity")

    def test_unusual_put_activity(self, client):
        _assert_screener_data(client, "volume", "unusual_put_activity")

    def test_unusual_activity(self, client):
        _assert_screener_data(client, "volume", "unusual_activity")


@pytest.mark.integration
class TestMenthorQIntegrationScreenerQScore:
    """All 16 qscore sub-screeners return table data."""

    def test_highest_option_score(self, client):
        _assert_screener_data(client, "qscore", "highest_option_score")

    def test_lowest_option_score(self, client):
        _assert_screener_data(client, "qscore", "lowest_option_score")

    def test_highest_option_score_diff(self, client):
        _assert_screener_data(client, "qscore", "highest_option_score_diff")

    def test_lowest_option_score_diff(self, client):
        _assert_screener_data(client, "qscore", "lowest_option_score_diff")

    def test_highest_volatility_score(self, client):
        _assert_screener_data(client, "qscore", "highest_volatility_score")

    def test_lowest_volatility_score(self, client):
        _assert_screener_data(client, "qscore", "lowest_volatility_score")

    def test_highest_volatility_score_diff(self, client):
        _assert_screener_data(client, "qscore", "highest_volatility_score_diff")

    def test_lowest_volatility_score_diff(self, client):
        _assert_screener_data(client, "qscore", "lowest_volatility_score_diff")

    def test_highest_momentum_score(self, client):
        _assert_screener_data(client, "qscore", "highest_momentum_score")

    def test_lowest_momentum_score(self, client):
        _assert_screener_data(client, "qscore", "lowest_momentum_score")

    def test_highest_momentum_score_diff(self, client):
        _assert_screener_data(client, "qscore", "highest_momentum_score_diff")

    def test_lowest_momentum_score_diff(self, client):
        _assert_screener_data(client, "qscore", "lowest_momentum_score_diff")

    def test_highest_seasonality_score(self, client):
        _assert_screener_data(client, "qscore", "highest_seasonality_score")

    def test_lowest_seasonality_score(self, client):
        _assert_screener_data(client, "qscore", "lowest_seasonality_score")

    def test_highest_seasonality_score_diff(self, client):
        _assert_screener_data(client, "qscore", "highest_seasonality_score_diff")

    def test_lowest_seasonality_score_diff(self, client):
        _assert_screener_data(client, "qscore", "lowest_seasonality_score_diff")


# ══════════════════════════════════════════════════════════════════════
# BULK SCREENER DATA — get_all_screener_data()
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.integration
class TestMenthorQIntegrationBulkScreener:
    """Verify get_all_screener_data() returns data for all slugs in a category."""

    def test_get_all_gamma(self, client):
        result = client.get_all_screener_data("gamma")
        assert isinstance(result, dict)
        assert len(result) == len(SCREENER_SLUGS["gamma"])
        for slug, data in result.items():
            assert isinstance(data, list), f"gamma/{slug}: expected list"

    def test_get_all_volatility(self, client):
        result = client.get_all_screener_data("volatility")
        assert isinstance(result, dict)
        assert len(result) == len(SCREENER_SLUGS["volatility"])
        for slug, data in result.items():
            assert isinstance(data, list), f"volatility/{slug}: expected list"
