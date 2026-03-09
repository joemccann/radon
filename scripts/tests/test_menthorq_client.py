"""Tests for MenthorQClient — headless browser client for MenthorQ data extraction.

RED/GREEN TDD: All tests written first (RED), then client implemented (GREEN).
Uses unittest.mock to avoid any real browser or API calls.
"""
import inspect
import json
import os

import pytest
from unittest.mock import MagicMock, patch, PropertyMock, call

from clients.menthorq_client import (
    MenthorQClient,
    MenthorQError,
    MenthorQAuthError,
    MenthorQNotFoundError,
    MenthorQExtractionError,
    BASE_URL,
    DASHBOARD_COMMANDS,
    DASHBOARD_TICKERS,
    TICKER_TAB_COMMANDS,
    SUMMARY_CATEGORIES,
    SCREENER_SLUGS,
    FOREX_CARD_SLUGS,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def mock_env_creds():
    """Provide mock MenthorQ credentials and Anthropic key in environment."""
    with patch.dict(os.environ, {
        "MENTHORQ_USER": "test@example.com",
        "MENTHORQ_PASS": "testpass123",
        "ANTHROPIC_API_KEY": "sk-ant-test-key",
    }):
        yield


@pytest.fixture
def mock_playwright():
    """Mock Playwright so no real browser is launched."""
    with patch("clients.menthorq_client.sync_playwright") as mock_sp, \
         patch("clients.menthorq_client.time.sleep"):
        # Build the mock browser chain
        mock_pw = MagicMock()
        mock_sp.return_value.__enter__ = MagicMock(return_value=mock_pw)
        mock_sp.return_value.__exit__ = MagicMock(return_value=False)

        mock_browser = MagicMock()
        mock_pw.chromium.launch.return_value = mock_browser

        mock_context = MagicMock()
        mock_browser.new_context.return_value = mock_context

        mock_page = MagicMock()
        mock_context.new_page.return_value = mock_page

        # Default: login succeeds (URL doesn't contain /login after submit)
        mock_page.url = "https://menthorq.com/account/"

        yield {
            "sync_playwright": mock_sp,
            "playwright": mock_pw,
            "browser": mock_browser,
            "context": mock_context,
            "page": mock_page,
        }


@pytest.fixture
def client(mock_env_creds, mock_playwright):
    """Create a MenthorQClient with mocked browser and credentials."""
    c = MenthorQClient(headless=True)
    yield c


# ══════════════════════════════════════════════════════════════════════
# 1. INITIALIZATION
# ══════════════════════════════════════════════════════════════════════


class TestMenthorQClientInit:
    def test_loads_creds_from_env(self, mock_env_creds, mock_playwright):
        """Client loads MENTHORQ_USER and MENTHORQ_PASS from environment."""
        c = MenthorQClient()
        assert c._username == "test@example.com"
        assert c._password == "testpass123"

    def test_missing_user_raises_auth_error(self, mock_playwright):
        """Missing MENTHORQ_USER raises MenthorQAuthError."""
        with patch.dict(os.environ, {"MENTHORQ_PASS": "pass"}, clear=True):
            os.environ.pop("MENTHORQ_USER", None)
            with pytest.raises(MenthorQAuthError, match="MENTHORQ_USER"):
                MenthorQClient()

    def test_missing_pass_raises_auth_error(self, mock_playwright):
        """Missing MENTHORQ_PASS raises MenthorQAuthError."""
        with patch.dict(os.environ, {"MENTHORQ_USER": "user"}, clear=True):
            os.environ.pop("MENTHORQ_PASS", None)
            with pytest.raises(MenthorQAuthError, match="MENTHORQ_PASS"):
                MenthorQClient()

    def test_no_hardcoded_credentials_in_source(self):
        """Verify no credentials are hardcoded in the source file."""
        import re
        source = inspect.getsource(MenthorQClient)
        assert "joseph" not in source.lower()
        assert "gmail" not in source.lower()
        assert "RX$" not in source
        # Check for email addresses (but allow @ in decorators/string formatting)
        email_pattern = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', source)
        assert len(email_pattern) == 0, f"Found email-like strings: {email_pattern}"

    def test_accepts_headless_param(self, mock_env_creds, mock_playwright):
        """Client passes headless parameter to browser launch."""
        c = MenthorQClient(headless=False)
        mock_playwright["playwright"].chromium.launch.assert_called_with(headless=False)


# ══════════════════════════════════════════════════════════════════════
# 2. LOGIN
# ══════════════════════════════════════════════════════════════════════


class TestMenthorQClientLogin:
    def test_login_navigates_to_login_url(self, client, mock_playwright):
        """Login navigates to the MenthorQ login page."""
        page = mock_playwright["page"]
        # The constructor calls _login(), which should have navigated to login URL
        calls = page.goto.call_args_list
        login_calls = [c for c in calls if "login" in str(c).lower()]
        assert len(login_calls) >= 1, "Should navigate to login URL"

    def test_login_fills_form_and_submits(self, client, mock_playwright):
        """Login fills username and password fields, then submits."""
        page = mock_playwright["page"]
        # Should have queried for form fields
        assert page.query_selector.call_count > 0, "Should query for form elements"

    def test_login_failure_raises_auth_error(self, mock_env_creds, mock_playwright):
        """Login failure (still on /login page after submit) raises MenthorQAuthError."""
        page = mock_playwright["page"]
        # Simulate login failure — URL still contains /login
        page.url = "https://menthorq.com/login/"
        with pytest.raises(MenthorQAuthError, match="[Ll]ogin"):
            MenthorQClient()


# ══════════════════════════════════════════════════════════════════════
# 3. EOD DATA
# ══════════════════════════════════════════════════════════════════════


SAMPLE_EOD_HTML = """
<div class="command-card" data-command-slug="eod">
  <div class="main-container">
    <table>
      <tr><th>Field</th><th>Value</th></tr>
      <tr><td>Last Price</td><td>5740.00</td></tr>
      <tr><td>Change %</td><td>-1.33</td></tr>
    </table>
    <div class="ticker-name">S&P 500 INDEX</div>
    <div class="score-block" data-score-type="qscore">
      <span class="score-value">1</span>
      <span class="score-label">Low</span>
    </div>
  </div>
</div>
"""


class TestMenthorQClientEOD:
    def test_get_eod_builds_correct_url(self, client, mock_playwright):
        """get_eod() navigates to the correct URL with ticker and date params."""
        page = mock_playwright["page"]
        page.content.return_value = SAMPLE_EOD_HTML

        # Mock _scrape_eod_fields to return valid data
        with patch.object(client, "_scrape_eod_fields", return_value={"last_price": 5740.0}):
            client.get_eod("SPX", "2026-03-06")

        # Check navigation URL contains expected params
        nav_calls = page.goto.call_args_list
        eod_calls = [c for c in nav_calls if "eod" in str(c)]
        assert len(eod_calls) >= 1, "Should navigate to EOD URL"
        url_str = str(eod_calls[-1])
        assert "commands=eod" in url_str
        assert "ticker=SPX" in url_str

    def test_get_eod_returns_structured_data(self, client, mock_playwright):
        """get_eod() returns a dict with expected keys."""
        expected = {
            "ticker": "SPX",
            "date": "2026-03-06",
            "last_price": 5740.0,
            "change_pct": -1.33,
        }
        with patch.object(client, "_scrape_eod_fields", return_value=expected):
            result = client.get_eod("SPX", "2026-03-06")
        assert isinstance(result, dict)
        assert result["ticker"] == "SPX"

    def test_get_eod_extracts_price_fields(self, client, mock_playwright):
        """get_eod() result contains numeric price fields."""
        expected = {
            "ticker": "SPX",
            "date": "2026-03-06",
            "last_price": 5740.0,
            "change_pct": -1.33,
            "iv_30d": 20.59,
            "hv_30": 12.19,
        }
        with patch.object(client, "_scrape_eod_fields", return_value=expected):
            result = client.get_eod("SPX", "2026-03-06")
        assert isinstance(result["last_price"], (int, float))
        assert isinstance(result["change_pct"], (int, float))

    def test_get_eod_extracts_qscore_fields(self, client, mock_playwright):
        """get_eod() result contains qscore sub-dict."""
        expected = {
            "ticker": "SPX",
            "date": "2026-03-06",
            "last_price": 5740.0,
            "qscore": {"score": 1, "label": "Low"},
        }
        with patch.object(client, "_scrape_eod_fields", return_value=expected):
            result = client.get_eod("SPX", "2026-03-06")
        assert "qscore" in result
        assert result["qscore"]["score"] == 1

    def test_get_eod_handles_missing_data(self, client, mock_playwright):
        """get_eod() raises MenthorQExtractionError when scrape returns empty."""
        with patch.object(client, "_scrape_eod_fields", return_value={}):
            with pytest.raises(MenthorQExtractionError):
                client.get_eod("INVALID", "2026-03-06")

    def test_get_eod_requires_ticker_and_date(self, client):
        """get_eod() requires both ticker and date parameters."""
        with pytest.raises(TypeError):
            client.get_eod()  # type: ignore
        with pytest.raises(TypeError):
            client.get_eod("SPX")  # type: ignore


# ══════════════════════════════════════════════════════════════════════
# 4. CTA DATA
# ══════════════════════════════════════════════════════════════════════


class TestMenthorQClientCTA:
    def test_get_cta_builds_correct_url(self, client, mock_playwright):
        """get_cta() navigates to CTA dashboard URL with date param."""
        page = mock_playwright["page"]
        page.evaluate.return_value = 4  # Card count polling
        with patch.object(client, "_download_card_images", return_value={}):
            with patch.object(client, "_screenshot_cards", return_value={}):
                with patch.object(client, "_extract_via_vision", return_value=[]):
                    try:
                        client.get_cta("2026-03-06")
                    except MenthorQExtractionError:
                        pass  # Expected when no images

        nav_calls = page.goto.call_args_list
        cta_calls = [c for c in nav_calls if "commands=cta" in str(c)]
        assert len(cta_calls) >= 1, "Should navigate to CTA URL"

    def test_get_cta_downloads_four_card_images(self, client, mock_playwright):
        """get_cta() attempts to download S3 images for all 4 CTA cards."""
        page = mock_playwright["page"]
        page.evaluate.return_value = 4
        with patch.object(client, "_download_card_images") as mock_dl:
            mock_dl.return_value = {
                "main": b"png1",
                "index": b"png2",
                "commodity": b"png3",
                "currency": b"png4",
            }
            with patch.object(client, "_extract_via_vision", return_value=[{"underlying": "test"}]):
                client.get_cta("2026-03-06")

            mock_dl.assert_called_once()
            args = mock_dl.call_args
            slugs = args[0][1] if len(args[0]) > 1 else args[1].get("slugs", {})
            assert len(slugs) == 4 or isinstance(slugs, dict) and len(slugs) == 4

    def test_get_cta_calls_vision_extraction(self, client, mock_playwright):
        """get_cta() sends each image to Vision for extraction."""
        page = mock_playwright["page"]
        page.evaluate.return_value = 4
        with patch.object(client, "_download_card_images", return_value={
            "main": b"png_bytes_main",
            "index": b"png_bytes_index",
        }):
            with patch.object(client, "_extract_via_vision", return_value=[{"underlying": "test"}]) as mock_vision:
                client.get_cta("2026-03-06")
                assert mock_vision.call_count == 2

    def test_get_cta_returns_tables_dict(self, client, mock_playwright):
        """get_cta() returns dict mapping table keys to lists of dicts."""
        page = mock_playwright["page"]
        page.evaluate.return_value = 4
        with patch.object(client, "_download_card_images", return_value={
            "main": b"png1",
            "index": b"png2",
        }):
            with patch.object(client, "_extract_via_vision", return_value=[
                {"underlying": "E-Mini S&P 500", "position_today": 0.45}
            ]):
                result = client.get_cta("2026-03-06")

        assert isinstance(result, dict)
        assert "main" in result
        assert isinstance(result["main"], list)
        assert result["main"][0]["underlying"] == "E-Mini S&P 500"

    def test_get_cta_falls_back_to_screenshots(self, client, mock_playwright):
        """get_cta() falls back to screenshots when S3 download fails."""
        page = mock_playwright["page"]
        page.evaluate.return_value = 4
        with patch.object(client, "_download_card_images", return_value={}):
            with patch.object(client, "_screenshot_cards", return_value={
                "main": b"png1",
            }) as mock_ss:
                with patch.object(client, "_extract_via_vision", return_value=[{"underlying": "test"}]):
                    client.get_cta("2026-03-06")
                mock_ss.assert_called_once()


# ══════════════════════════════════════════════════════════════════════
# 5. SCREENER
# ══════════════════════════════════════════════════════════════════════


class TestMenthorQClientScreener:
    def test_get_screener_builds_correct_url(self, client, mock_playwright):
        """get_screener() navigates to screener URL with commands param."""
        page = mock_playwright["page"]
        with patch.object(client, "_scrape_tables", return_value=[{"ticker": "AAPL"}]):
            client.get_screener("options")

        nav_calls = page.goto.call_args_list
        screener_calls = [c for c in nav_calls if "screener" in str(c) and "options" in str(c)]
        assert len(screener_calls) >= 1

    def test_get_screener_category_builds_correct_url(self, client, mock_playwright):
        """get_screener_category() navigates to category screener URL."""
        page = mock_playwright["page"]
        with patch.object(client, "_scrape_tables", return_value=[{"ticker": "AAPL"}]):
            client.get_screener_category("gamma", "highest_gex_change")

        nav_calls = page.goto.call_args_list
        cat_calls = [c for c in nav_calls if "category=gamma" in str(c)]
        assert len(cat_calls) >= 1

    def test_get_screener_returns_list_of_dicts(self, client, mock_playwright):
        """get_screener() returns a list of dicts."""
        with patch.object(client, "_scrape_tables", return_value=[
            {"ticker": "AAPL", "score": 5},
            {"ticker": "MSFT", "score": 4},
        ]):
            result = client.get_screener("options")

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["ticker"] == "AAPL"


# ══════════════════════════════════════════════════════════════════════
# 6. LIFECYCLE
# ══════════════════════════════════════════════════════════════════════


class TestMenthorQClientLifecycle:
    def test_context_manager_closes_browser(self, mock_env_creds, mock_playwright):
        """Context manager closes browser on exit."""
        with MenthorQClient() as c:
            browser = mock_playwright["browser"]
        browser.close.assert_called()

    def test_close_is_idempotent(self, client, mock_playwright):
        """Calling close() multiple times doesn't raise."""
        client.close()
        client.close()  # Should not raise

    def test_close_handles_already_closed(self, client, mock_playwright):
        """close() handles case where browser is already None."""
        client._browser = None
        client.close()  # Should not raise


# ══════════════════════════════════════════════════════════════════════
# 7. DASHBOARD IMAGE
# ══════════════════════════════════════════════════════════════════════


class TestMenthorQClientDashboardImage:
    def test_get_dashboard_image_builds_correct_url(self, client, mock_playwright):
        """get_dashboard_image() navigates to dashboard URL with command param."""
        page = mock_playwright["page"]
        page.evaluate.return_value = 1  # card count polling
        with patch.object(client, "_download_card_images", return_value={"vol": b"\x89PNG_s3"}):
            client.get_dashboard_image("vol")

        nav_calls = page.goto.call_args_list
        vol_calls = [c for c in nav_calls if "commands=vol" in str(c)]
        assert len(vol_calls) >= 1

    def test_get_dashboard_image_tries_s3_first(self, client, mock_playwright):
        """get_dashboard_image() tries S3 download before viewport screenshot."""
        page = mock_playwright["page"]
        page.evaluate.return_value = 1
        with patch.object(client, "_download_card_images", return_value={"vol": b"\x89PNG_s3"}) as mock_dl:
            result = client.get_dashboard_image("vol")
            mock_dl.assert_called_once()
            assert result == b"\x89PNG_s3"
            # Should NOT fall back to page.screenshot
            page.screenshot.assert_not_called()

    def test_get_dashboard_image_falls_back_to_screenshot(self, client, mock_playwright):
        """get_dashboard_image() falls back to viewport screenshot when S3 fails."""
        page = mock_playwright["page"]
        page.evaluate.return_value = 0  # no cards found
        page.screenshot.return_value = b"\x89PNG_viewport"
        with patch.object(client, "_download_card_images", return_value={}):
            result = client.get_dashboard_image("vol")
            assert result == b"\x89PNG_viewport"
            page.screenshot.assert_called_once()

    def test_get_dashboard_image_returns_png_bytes(self, client, mock_playwright):
        """get_dashboard_image() returns PNG bytes."""
        page = mock_playwright["page"]
        page.evaluate.return_value = 1
        with patch.object(client, "_download_card_images", return_value={"eod": b"\x89PNG_fake_data"}):
            result = client.get_dashboard_image("eod")
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_get_dashboard_image_with_tickers(self, client, mock_playwright):
        """get_dashboard_image() passes optional tickers param."""
        page = mock_playwright["page"]
        page.evaluate.return_value = 1
        with patch.object(client, "_download_card_images", return_value={"cryptos_options": b"\x89PNG"}):
            client.get_dashboard_image("cryptos_options", tickers="cryptos_options")

        nav_calls = page.goto.call_args_list
        crypto_calls = [c for c in nav_calls if "tickers=cryptos_options" in str(c)]
        assert len(crypto_calls) >= 1

    def test_get_dashboard_image_raises_on_empty(self, client, mock_playwright):
        """get_dashboard_image() raises MenthorQExtractionError when both S3 and screenshot fail."""
        page = mock_playwright["page"]
        page.evaluate.return_value = 0
        page.screenshot.return_value = b""
        with patch.object(client, "_download_card_images", return_value={}):
            with pytest.raises(MenthorQExtractionError):
                client.get_dashboard_image("forex")

    def test_get_dashboard_image_raises_on_exception(self, client, mock_playwright):
        """get_dashboard_image() raises MenthorQExtractionError on screenshot failure."""
        page = mock_playwright["page"]
        page.evaluate.return_value = 0
        page.screenshot.side_effect = Exception("screenshot failed")
        with patch.object(client, "_download_card_images", return_value={}):
            with pytest.raises(MenthorQExtractionError, match="screenshot"):
                client.get_dashboard_image("futures")

    def test_get_dashboard_image_uses_command_slug(self, client, mock_playwright):
        """get_dashboard_image() passes the command as the card slug to _download_card_images."""
        page = mock_playwright["page"]
        page.evaluate.return_value = 1
        with patch.object(client, "_download_card_images", return_value={"vol": b"\x89PNG"}) as mock_dl:
            client.get_dashboard_image("vol")
            # Verify the slugs dict uses command as both key and value
            args = mock_dl.call_args
            slugs = args[0][1]
            assert "vol" in slugs
            assert slugs["vol"] == "vol"

    def test_get_dashboard_image_rejects_invalid_command(self, client, mock_playwright):
        """get_dashboard_image() raises MenthorQExtractionError for unknown commands."""
        with pytest.raises(MenthorQExtractionError, match="Unknown dashboard command"):
            client.get_dashboard_image("gex")


# ══════════════════════════════════════════════════════════════════════
# 8. INTRADAY
# ══════════════════════════════════════════════════════════════════════


class TestMenthorQClientIntraday:
    def test_get_intraday_builds_correct_url(self, client, mock_playwright):
        """get_intraday() navigates to intraday dashboard URL."""
        page = mock_playwright["page"]
        with patch.object(client, "_scrape_tables", return_value=[]):
            client.get_intraday()

        nav_calls = page.goto.call_args_list
        intraday_calls = [c for c in nav_calls if "commands=intraday" in str(c)]
        assert len(intraday_calls) >= 1

    def test_get_intraday_returns_list(self, client, mock_playwright):
        """get_intraday() returns a list of dicts."""
        with patch.object(client, "_scrape_tables", return_value=[
            {"ticker": "SPX", "price": 5740.0}
        ]):
            result = client.get_intraday()
        assert isinstance(result, list)
        assert result[0]["ticker"] == "SPX"


# ══════════════════════════════════════════════════════════════════════
# 9. FUTURES
# ══════════════════════════════════════════════════════════════════════


class TestMenthorQClientFutures:
    def test_get_futures_list_builds_correct_url(self, client, mock_playwright):
        """get_futures_list() navigates to futures list URL."""
        page = mock_playwright["page"]
        with patch.object(client, "_scrape_tables", return_value=[]):
            client.get_futures_list()

        nav_calls = page.goto.call_args_list
        futures_calls = [c for c in nav_calls if "type=futures" in str(c) and "commands=list" in str(c)]
        assert len(futures_calls) >= 1

    def test_get_futures_detail_includes_ticker(self, client, mock_playwright):
        """get_futures_detail() includes ticker in URL params."""
        page = mock_playwright["page"]
        with patch.object(client, "_scrape_tables", return_value=[]):
            client.get_futures_detail("ES")

        nav_calls = page.goto.call_args_list
        detail_calls = [c for c in nav_calls if "ticker=ES" in str(c)]
        assert len(detail_calls) >= 1

    def test_get_futures_contracts_includes_date(self, client, mock_playwright):
        """get_futures_contracts() includes ticker and date in URL params."""
        page = mock_playwright["page"]
        with patch.object(client, "_scrape_tables", return_value=[]):
            client.get_futures_contracts("ES", "2026-03-06")

        nav_calls = page.goto.call_args_list
        contract_calls = [c for c in nav_calls if "ticker=ES" in str(c) and "date=2026-03-06" in str(c)]
        assert len(contract_calls) >= 1

    def test_get_futures_list_returns_list(self, client, mock_playwright):
        """get_futures_list() returns a list of dicts."""
        with patch.object(client, "_scrape_tables", return_value=[
            {"ticker": "ES", "price": 5740.0}
        ]):
            result = client.get_futures_list()
        assert isinstance(result, list)

    def test_get_futures_detail_requires_ticker(self, client):
        """get_futures_detail() requires ticker param."""
        with pytest.raises(TypeError):
            client.get_futures_detail()  # type: ignore

    def test_get_futures_contracts_requires_params(self, client):
        """get_futures_contracts() requires ticker and date params."""
        with pytest.raises(TypeError):
            client.get_futures_contracts()  # type: ignore


# ══════════════════════════════════════════════════════════════════════
# 10. FOREX
# ══════════════════════════════════════════════════════════════════════


class TestMenthorQClientForex:
    def test_get_forex_list_builds_correct_url(self, client, mock_playwright):
        """get_forex_list() navigates to forex list URL."""
        page = mock_playwright["page"]
        with patch.object(client, "_scrape_tables", return_value=[]):
            client.get_forex_list()

        nav_calls = page.goto.call_args_list
        forex_calls = [c for c in nav_calls if "type=forex" in str(c) and "commands=list" in str(c)]
        assert len(forex_calls) >= 1

    def test_get_forex_detail_includes_ticker(self, client, mock_playwright):
        """get_forex_detail() includes ticker in URL params."""
        page = mock_playwright["page"]
        with patch.object(client, "_scrape_tables", return_value=[]):
            client.get_forex_detail("EURUSD")

        nav_calls = page.goto.call_args_list
        detail_calls = [c for c in nav_calls if "ticker=EURUSD" in str(c)]
        assert len(detail_calls) >= 1

    def test_get_forex_list_returns_list(self, client, mock_playwright):
        """get_forex_list() returns a list of dicts."""
        with patch.object(client, "_scrape_tables", return_value=[{"pair": "EURUSD"}]):
            result = client.get_forex_list()
        assert isinstance(result, list)

    def test_get_forex_detail_requires_ticker(self, client):
        """get_forex_detail() requires ticker param."""
        with pytest.raises(TypeError):
            client.get_forex_detail()  # type: ignore


# ══════════════════════════════════════════════════════════════════════
# 11. CRYPTO
# ══════════════════════════════════════════════════════════════════════


class TestMenthorQClientCrypto:
    def test_get_crypto_list_builds_correct_url(self, client, mock_playwright):
        """get_crypto_list() navigates to crypto list URL."""
        page = mock_playwright["page"]
        with patch.object(client, "_scrape_tables", return_value=[]):
            client.get_crypto_list()

        nav_calls = page.goto.call_args_list
        crypto_calls = [c for c in nav_calls if "type=crypto" in str(c) and "commands=list" in str(c)]
        assert len(crypto_calls) >= 1

    def test_get_crypto_detail_includes_ticker(self, client, mock_playwright):
        """get_crypto_detail() includes ticker in URL params."""
        page = mock_playwright["page"]
        with patch.object(client, "_scrape_tables", return_value=[]):
            client.get_crypto_detail("BTC")

        nav_calls = page.goto.call_args_list
        detail_calls = [c for c in nav_calls if "ticker=BTC" in str(c)]
        assert len(detail_calls) >= 1

    def test_get_crypto_list_returns_list(self, client, mock_playwright):
        """get_crypto_list() returns a list of dicts."""
        with patch.object(client, "_scrape_tables", return_value=[{"ticker": "BTC"}]):
            result = client.get_crypto_list()
        assert isinstance(result, list)

    def test_get_crypto_detail_requires_ticker(self, client):
        """get_crypto_detail() requires ticker param."""
        with pytest.raises(TypeError):
            client.get_crypto_detail()  # type: ignore


# ══════════════════════════════════════════════════════════════════════
# 12. DASHBOARD IMAGE — TICKER TAB SELECTION
# ══════════════════════════════════════════════════════════════════════


class TestMenthorQClientDashboardTicker:
    def test_get_dashboard_image_with_ticker_clicks_tab(self, client, mock_playwright):
        """get_dashboard_image() with ticker param clicks the ticker tab element."""
        page = mock_playwright["page"]
        page.evaluate.return_value = 1  # card count polling

        mock_tab = MagicMock()
        page.query_selector.return_value = mock_tab

        with patch.object(client, "_download_card_images", return_value={"eod": b"\x89PNG_s3"}):
            client.get_dashboard_image("eod", ticker="nvda")

        # Verify the ticker tab selector was queried
        tab_calls = [
            c for c in page.query_selector.call_args_list
            if 'data-ticker="nvda"' in str(c)
        ]
        assert len(tab_calls) >= 1, "Should query for ticker tab element"
        # Verify click was called on the tab
        mock_tab.click.assert_called()

    def test_get_dashboard_image_ticker_rejects_non_tab_command(self, client, mock_playwright):
        """get_dashboard_image() with ticker raises error for commands without tabs."""
        with pytest.raises(MenthorQExtractionError, match="does not support ticker tabs"):
            client.get_dashboard_image("cta", ticker="nvda")

    def test_get_dashboard_image_ticker_rejects_vol(self, client, mock_playwright):
        """get_dashboard_image() with ticker raises error for vol command."""
        with pytest.raises(MenthorQExtractionError, match="does not support ticker tabs"):
            client.get_dashboard_image("vol", ticker="spy")

    def test_get_dashboard_image_ticker_rejects_forex(self, client, mock_playwright):
        """get_dashboard_image() with ticker raises error for forex command."""
        with pytest.raises(MenthorQExtractionError, match="does not support ticker tabs"):
            client.get_dashboard_image("forex", ticker="spy")

    def test_get_dashboard_image_ticker_missing_tab_continues(self, client, mock_playwright):
        """get_dashboard_image() continues with warning if ticker tab element not found."""
        page = mock_playwright["page"]
        page.evaluate.return_value = 1

        # query_selector returns None for the ticker tab
        def qs_side_effect(selector):
            if "data-ticker" in selector:
                return None
            return MagicMock()

        page.query_selector.side_effect = qs_side_effect

        with patch.object(client, "_download_card_images", return_value={"eod": b"\x89PNG_s3"}):
            result = client.get_dashboard_image("eod", ticker="unknown_ticker")
        assert result == b"\x89PNG_s3"


# ══════════════════════════════════════════════════════════════════════
# 13. SUMMARY PAGES
# ══════════════════════════════════════════════════════════════════════


class TestMenthorQClientSummary:
    def test_get_summary_futures_builds_correct_url(self, client, mock_playwright):
        """get_summary('futures') navigates to the correct summary URL."""
        page = mock_playwright["page"]
        with patch.object(client, "_scrape_tables", return_value=[{"ticker": "ES"}]):
            client.get_summary("futures")

        nav_calls = page.goto.call_args_list
        summary_calls = [c for c in nav_calls if "type=summary" in str(c) and "category=futures" in str(c)]
        assert len(summary_calls) >= 1

    def test_get_summary_cryptos_builds_correct_url(self, client, mock_playwright):
        """get_summary('cryptos') navigates to the correct summary URL."""
        page = mock_playwright["page"]
        with patch.object(client, "_scrape_tables", return_value=[{"ticker": "BTC"}]):
            client.get_summary("cryptos")

        nav_calls = page.goto.call_args_list
        summary_calls = [c for c in nav_calls if "type=summary" in str(c) and "category=cryptos" in str(c)]
        assert len(summary_calls) >= 1

    def test_get_summary_returns_list(self, client, mock_playwright):
        """get_summary() returns a list of dicts."""
        with patch.object(client, "_scrape_tables", return_value=[
            {"ticker": "ES", "oi%": 0.5, "volume%": 1.2},
        ]):
            result = client.get_summary("futures")
        assert isinstance(result, list)
        assert result[0]["ticker"] == "ES"

    def test_get_summary_rejects_invalid_category(self, client, mock_playwright):
        """get_summary() raises MenthorQExtractionError for unknown category."""
        with pytest.raises(MenthorQExtractionError, match="Unknown summary category"):
            client.get_summary("stocks")


# ══════════════════════════════════════════════════════════════════════
# 14. SCREENER SLUG VALIDATION
# ══════════════════════════════════════════════════════════════════════


class TestMenthorQClientScreenerValidation:
    def test_get_screener_category_rejects_invalid_category(self, client, mock_playwright):
        """get_screener_category() raises error for unknown category."""
        with pytest.raises(MenthorQExtractionError, match="Unknown screener category"):
            client.get_screener_category("invalid_category", "some_slug")

    def test_get_screener_category_rejects_invalid_slug(self, client, mock_playwright):
        """get_screener_category() raises error for unknown slug in valid category."""
        with pytest.raises(MenthorQExtractionError, match="Unknown slug"):
            client.get_screener_category("gamma", "nonexistent_slug")

    def test_get_screener_category_accepts_valid_slug(self, client, mock_playwright):
        """get_screener_category() accepts valid category+slug combinations."""
        with patch.object(client, "_scrape_tables", return_value=[{"ticker": "AAPL"}]):
            result = client.get_screener_category("gamma", "highest_gex_change")
        assert isinstance(result, list)


# ══════════════════════════════════════════════════════════════════════
# 15. CONSTANTS VALIDATION
# ══════════════════════════════════════════════════════════════════════


class TestMenthorQConstants:
    def test_dashboard_tickers_has_16_entries(self):
        """DASHBOARD_TICKERS contains exactly 16 tickers."""
        assert len(DASHBOARD_TICKERS) == 16

    def test_ticker_tab_commands_are_subset_of_dashboard(self):
        """TICKER_TAB_COMMANDS are all valid DASHBOARD_COMMANDS."""
        assert TICKER_TAB_COMMANDS.issubset(set(DASHBOARD_COMMANDS.keys()))

    def test_screener_slugs_has_6_categories(self):
        """SCREENER_SLUGS contains 6 categories."""
        assert len(SCREENER_SLUGS) == 6

    def test_screener_slugs_total_count(self):
        """SCREENER_SLUGS contains 45 total slugs."""
        total = sum(len(slugs) for slugs in SCREENER_SLUGS.values())
        assert total == 45


# ══════════════════════════════════════════════════════════════════════
# 16. DISCOVER SCREENER CARDS
# ══════════════════════════════════════════════════════════════════════


class TestMenthorQClientDiscoverScreenerCards:
    def test_discover_screener_cards_navigates_correctly(self, client, mock_playwright):
        """discover_screener_cards() navigates to type=screeners&category= without slug."""
        page = mock_playwright["page"]
        page.evaluate.return_value = [
            {"title": "Highest GEX Change", "slug": "highest_gex_change", "description": ""},
        ]
        client.discover_screener_cards("gamma")

        nav_calls = page.goto.call_args_list
        # Find the call that has type=screeners and category=gamma
        screener_calls = [
            c for c in nav_calls
            if "type=screeners" in str(c) and "category=gamma" in str(c)
        ]
        assert len(screener_calls) >= 1, "Should navigate to type=screeners&category=gamma"
        # Verify slug= is NOT in the URL
        url_str = str(screener_calls[-1])
        assert "slug=" not in url_str, "URL should NOT contain slug= param"

    def test_discover_screener_cards_returns_card_list(self, client, mock_playwright):
        """discover_screener_cards() returns list of dicts with title, slug, description."""
        page = mock_playwright["page"]
        mock_cards = [
            {"title": "Highest GEX Change", "slug": "highest_gex_change", "description": "Top GEX movers"},
            {"title": "Highest Negative DEX Change", "slug": "highest_negative_dex_change", "description": ""},
        ]
        page.evaluate.return_value = mock_cards

        result = client.discover_screener_cards("gamma")

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["title"] == "Highest GEX Change"
        assert result[0]["slug"] == "highest_gex_change"
        assert result[0]["description"] == "Top GEX movers"
        assert result[1]["title"] == "Highest Negative DEX Change"
        assert result[1]["slug"] == "highest_negative_dex_change"

    def test_discover_screener_cards_rejects_invalid_category(self, client, mock_playwright):
        """discover_screener_cards() raises MenthorQExtractionError for invalid category."""
        with pytest.raises(MenthorQExtractionError, match="Unknown screener category"):
            client.discover_screener_cards("nonexistent_category")

    def test_discover_screener_cards_returns_empty_for_no_cards(self, client, mock_playwright):
        """discover_screener_cards() returns empty list when page has no card elements."""
        page = mock_playwright["page"]
        page.evaluate.return_value = []

        result = client.discover_screener_cards("gamma")

        assert isinstance(result, list)
        assert len(result) == 0


# ══════════════════════════════════════════════════════════════════════
# 17. GET ALL SCREENER DATA
# ══════════════════════════════════════════════════════════════════════


class TestMenthorQClientGetAllScreenerData:
    def test_get_all_screener_data_fetches_all_slugs(self, client, mock_playwright):
        """get_all_screener_data() calls _scrape_tables once per slug in category."""
        with patch.object(client, "_scrape_tables", return_value=[{"ticker": "AAPL"}]) as mock_scrape:
            client.get_all_screener_data("gamma")

        # gamma has 5 slugs
        assert mock_scrape.call_count == len(SCREENER_SLUGS["gamma"])
        assert mock_scrape.call_count == 5

    def test_get_all_screener_data_returns_dict_of_slug_to_data(self, client, mock_playwright):
        """get_all_screener_data() returns dict mapping each slug to its row data."""
        page = mock_playwright["page"]

        def scrape_side_effect(p):
            return [{"ticker": "SPY", "gex": 1.5}]

        with patch.object(client, "_scrape_tables", side_effect=scrape_side_effect):
            result = client.get_all_screener_data("gamma")

        assert isinstance(result, dict)
        # All gamma slugs should be keys in the result
        for slug in SCREENER_SLUGS["gamma"]:
            assert slug in result, f"Missing slug key: {slug}"
            assert isinstance(result[slug], list)
            assert result[slug][0]["ticker"] == "SPY"

        # Verify navigation URLs contain correct slug params
        nav_calls = page.goto.call_args_list
        for slug in SCREENER_SLUGS["gamma"]:
            slug_calls = [c for c in nav_calls if f"slug={slug}" in str(c)]
            assert len(slug_calls) >= 1, f"Should navigate to slug={slug}"

    def test_get_all_screener_data_handles_failed_slug(self, client, mock_playwright):
        """get_all_screener_data() catches errors per slug; failed slug gets empty list."""
        call_count = [0]

        def scrape_side_effect(p):
            call_count[0] += 1
            # Fail on the 2nd slug
            if call_count[0] == 2:
                raise Exception("Simulated scrape failure")
            return [{"ticker": "AAPL"}]

        with patch.object(client, "_scrape_tables", side_effect=scrape_side_effect):
            result = client.get_all_screener_data("gamma")

        assert isinstance(result, dict)
        assert len(result) == 5  # All slugs present

        # The 2nd slug should have an empty list
        second_slug = SCREENER_SLUGS["gamma"][1]
        assert result[second_slug] == []

        # Others should have data
        first_slug = SCREENER_SLUGS["gamma"][0]
        assert len(result[first_slug]) == 1
        assert result[first_slug][0]["ticker"] == "AAPL"

    def test_get_all_screener_data_rejects_invalid_category(self, client, mock_playwright):
        """get_all_screener_data() raises MenthorQExtractionError for invalid category."""
        with pytest.raises(MenthorQExtractionError, match="Unknown screener category"):
            client.get_all_screener_data("invalid_category")


# ══════════════════════════════════════════════════════════════════════
# 18. FOREX LEVELS — TEXT CARD PARSING
# ══════════════════════════════════════════════════════════════════════


SAMPLE_FOREX_GAMMA_TEXT = (
    "$EURUSD: Call Resistance, 1.09602, Put Support, 1.06113, HVL, 1.07857, "
    "1D Max Move, 1.10200, 1D Min Move, 1.05800, "
    "GEX 1, 1.08000, GEX 2, 1.08500, GEX 3, 1.09000"
    "\n$GBPUSD: Call Resistance, 1.35000, Put Support, 1.30000, HVL, 1.32500"
)

SAMPLE_FOREX_BLINDSPOT_TEXT = (
    "$EURUSD: BL 1, 1.06113, BL 2, 1.06414, BL 3, 1.06700"
    "\n$GBPUSD: BL 1, 1.30000, BL 2, 1.30500"
)


class TestMenthorQClientForexLevels:
    def test_get_forex_levels_navigates_correctly(self, client, mock_playwright):
        """get_forex_levels() navigates to type=dashboard&commands=forex."""
        page = mock_playwright["page"]
        page.evaluate.return_value = 2  # card count polling

        with patch.object(client, "_scrape_forex_text_card", return_value=SAMPLE_FOREX_GAMMA_TEXT):
            try:
                client.get_forex_levels()
            except MenthorQExtractionError:
                pass

        nav_calls = page.goto.call_args_list
        forex_calls = [c for c in nav_calls if "commands=forex" in str(c)]
        assert len(forex_calls) >= 1

    def test_get_forex_levels_returns_gamma_and_blindspot(self, client, mock_playwright):
        """get_forex_levels() returns dict with gamma and blindspot keys."""
        page = mock_playwright["page"]
        page.evaluate.return_value = 2

        def scrape_side_effect(p, slug):
            if slug == "forex_gamma":
                return SAMPLE_FOREX_GAMMA_TEXT
            elif slug == "forex_blindspot":
                return SAMPLE_FOREX_BLINDSPOT_TEXT
            return None

        with patch.object(client, "_scrape_forex_text_card", side_effect=scrape_side_effect):
            result = client.get_forex_levels()

        assert isinstance(result, dict)
        assert "gamma" in result
        assert "blindspot" in result
        assert isinstance(result["gamma"], list)
        assert isinstance(result["blindspot"], list)

    def test_get_forex_levels_parses_gamma_pairs(self, client, mock_playwright):
        """get_forex_levels() parses gamma data into per-pair dicts."""
        page = mock_playwright["page"]
        page.evaluate.return_value = 2

        def scrape_side_effect(p, slug):
            if slug == "forex_gamma":
                return SAMPLE_FOREX_GAMMA_TEXT
            return SAMPLE_FOREX_BLINDSPOT_TEXT

        with patch.object(client, "_scrape_forex_text_card", side_effect=scrape_side_effect):
            result = client.get_forex_levels()

        gamma = result["gamma"]
        assert len(gamma) == 2
        assert gamma[0]["pair"] == "EURUSD"
        assert gamma[0]["call_resistance"] == 1.09602
        assert gamma[0]["put_support"] == 1.06113
        assert gamma[0]["hvl"] == 1.07857
        assert gamma[1]["pair"] == "GBPUSD"

    def test_get_forex_levels_parses_blindspot_pairs(self, client, mock_playwright):
        """get_forex_levels() parses blindspot data into per-pair dicts."""
        page = mock_playwright["page"]
        page.evaluate.return_value = 2

        def scrape_side_effect(p, slug):
            if slug == "forex_gamma":
                return SAMPLE_FOREX_GAMMA_TEXT
            return SAMPLE_FOREX_BLINDSPOT_TEXT

        with patch.object(client, "_scrape_forex_text_card", side_effect=scrape_side_effect):
            result = client.get_forex_levels()

        blindspot = result["blindspot"]
        assert len(blindspot) == 2
        assert blindspot[0]["pair"] == "EURUSD"
        assert blindspot[0]["bl_1"] == 1.06113
        assert blindspot[0]["bl_2"] == 1.06414

    def test_get_forex_levels_raises_on_empty(self, client, mock_playwright):
        """get_forex_levels() raises MenthorQExtractionError when no data found."""
        page = mock_playwright["page"]
        page.evaluate.return_value = 0

        with patch.object(client, "_scrape_forex_text_card", return_value=None):
            with pytest.raises(MenthorQExtractionError, match="no data"):
                client.get_forex_levels()


class TestMenthorQParseForexText:
    """Test the static _parse_forex_text helper directly."""

    def test_parses_single_pair(self):
        text = "$EURUSD: Call Resistance, 1.09602, Put Support, 1.06113"
        result = MenthorQClient._parse_forex_text(text)
        assert len(result) == 1
        assert result[0]["pair"] == "EURUSD"
        assert result[0]["call_resistance"] == 1.09602
        assert result[0]["put_support"] == 1.06113

    def test_parses_multiple_pairs(self):
        text = "$EURUSD: HVL, 1.078\n$GBPUSD: HVL, 1.325\n$USDJPY: HVL, 150.50"
        result = MenthorQClient._parse_forex_text(text)
        assert len(result) == 3
        assert result[0]["pair"] == "EURUSD"
        assert result[1]["pair"] == "GBPUSD"
        assert result[2]["pair"] == "USDJPY"
        assert result[2]["hvl"] == 150.50

    def test_handles_empty_text(self):
        assert MenthorQClient._parse_forex_text("") == []
        assert MenthorQClient._parse_forex_text(None) == []

    def test_handles_blindspot_format(self):
        text = "$EURUSD: BL 1, 1.06113, BL 2, 1.06414"
        result = MenthorQClient._parse_forex_text(text)
        assert len(result) == 1
        assert result[0]["bl_1"] == 1.06113
        assert result[0]["bl_2"] == 1.06414


class TestMenthorQForexConstants:
    def test_forex_card_slugs_has_two_entries(self):
        """FOREX_CARD_SLUGS contains exactly 2 slugs."""
        assert len(FOREX_CARD_SLUGS) == 2
        assert "forex_gamma" in FOREX_CARD_SLUGS
        assert "forex_blindspot" in FOREX_CARD_SLUGS
