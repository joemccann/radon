"""Headless browser client for MenthorQ data extraction.

MenthorQ has no public API. All data sits behind WordPress auth, rendered as
HTML tables (scrapeable) or chart images (requires Claude Vision). This client
handles authentication, navigation, HTML scraping, and image-based extraction.

Usage::

    from clients.menthorq_client import MenthorQClient

    with MenthorQClient() as client:
        eod = client.get_eod("SPX", "2026-03-06")
        cta = client.get_cta("2026-03-06")

    # Or without context manager:
    client = MenthorQClient(headless=False)
    try:
        screener = client.get_screener("options")
    finally:
        client.close()

Credentials (project root .env, loaded via a local fallback loader):
    MENTHORQ_USER  -- MenthorQ email/username
    MENTHORQ_PASS  -- MenthorQ password

Vision API key (from web/.env or shell):
    ANTHROPIC_API_KEY / CLAUDE_CODE_API_KEY / CLAUDE_API_KEY
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.sync_api import sync_playwright, Page

from utils.env_loader import load_env_file

# Load .env from project root
load_env_file(Path(__file__).resolve().parent.parent.parent / ".env")

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════
# Constants
# ══════════════════════════════════════════════════════════════════════

BASE_URL = "https://menthorq.com/account/"
LOGIN_URL = "https://menthorq.com/login/"
DEFAULT_ARTIFACT_ROOT = Path(__file__).resolve().parent.parent.parent / "logs" / "menthorq_artifacts"
DEFAULT_STORAGE_STATE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "menthorq_cache" / "menthorq_storage_state.json"

# CTA card slugs (data-command-slug attributes)
CTA_SLUGS = {
    "main": "cta_table",
    "index": "cta_index",
    "commodity": "cta_commodity",
    "currency": "cta_currency",
}

# Valid dashboard commands (from MenthorQ sidebar navigation)
# Maps command slug → required tickers param (None if not needed)
DASHBOARD_COMMANDS = {
    "cta": None,
    "vol": None,
    "forex": None,
    "eod": "commons",
    "intraday": "commons",
    "futures": "futures",
    "cryptos_technical": "cryptos_technical",
    "cryptos_options": "cryptos_options",
}

# Dashboard commands that support ticker tab selection
TICKER_TAB_COMMANDS = {"eod", "intraday", "futures", "cryptos_technical", "cryptos_options"}

# Valid tickers for dashboard ticker tabs (16 tabs shown in sidebar)
DASHBOARD_TICKERS = [
    "spx", "vix", "ndx", "rut", "spy", "qqq", "iwm", "smh",
    "ibit", "nvda", "googl", "meta", "tsla", "amzn", "msft", "nflx",
]

# Valid summary categories
SUMMARY_CATEGORIES = {"futures", "cryptos"}

# Forex command card slugs (data-command-slug attributes on the forex dashboard)
FOREX_CARD_SLUGS = {"forex_gamma", "forex_blindspot"}

# Validated screener slugs by category (from live DOM discovery 2026-03-08)
SCREENER_SLUGS = {
    "gamma": [
        "highest_gex_change",
        "highest_negative_dex_change",
        "highest_negative_gex_change",
        "biggest_dex_expiry_next_2w",
        "biggest_gex_expiry_next_2w",
    ],
    "gamma_levels": [
        "closer_0dte_call_resistance",
        "closer_0dte_put_support",
        "closer_to_HVL",
        "closer_call_resistance",
        "closer_put_support",
    ],
    "open_interest": [
        "highest_call_oi",
        "highest_oi",
        "highest_pc_oi",
        "highest_put_oi",
        "lowest_pc_oi",
        "highest_oi_change",
        "highest_negative_oi_change",
    ],
    "volatility": [
        "highest_iv30",
        "highest_ivrank",
        "highest_hv30",
        "lowest_iv30",
        "lowest_ivrank",
        "lowest_hv30",
    ],
    "volume": [
        "highest_call_volume",
        "highest_put_volume",
        "highest_total_volume",
        "unusual_call_activity",
        "unusual_put_activity",
        "unusual_activity",
    ],
    "qscore": [
        "highest_option_score",
        "lowest_option_score",
        "highest_option_score_diff",
        "lowest_option_score_diff",
        "highest_volatility_score",
        "lowest_volatility_score",
        "highest_volatility_score_diff",
        "lowest_volatility_score_diff",
        "highest_momentum_score",
        "lowest_momentum_score",
        "highest_momentum_score_diff",
        "lowest_momentum_score_diff",
        "highest_seasonality_score",
        "lowest_seasonality_score",
        "highest_seasonality_score_diff",
        "lowest_seasonality_score_diff",
    ],
}

# Anthropic API key env var names (tried in order)
_ANTHROPIC_ENV_KEYS = ["ANTHROPIC_API_KEY", "CLAUDE_CODE_API_KEY", "CLAUDE_API_KEY"]

# Vision extraction prompt for CTA tables
_CTA_EXTRACTION_PROMPT = """Extract CTA positioning data from this table image.
Return ONLY a JSON array of objects with these exact fields:
[{"underlying":"E-Mini S&P 500 Index","position_today":0.45,"position_yesterday":0.21,"position_1m_ago":1.06,"percentile_1m":38,"percentile_3m":13,"percentile_1y":38,"z_score_3m":-1.56},...]

Rules:
- "underlying" is the asset name exactly as shown in the table
- Position values are decimal numbers as shown (can be negative)
- Percentiles are integers (e.g. 38 means 38th percentile)
- Z-scores are decimal numbers as shown (e.g. -1.56)
- Include ALL rows from the table
- Return ONLY the JSON array, no markdown, no explanation"""


# ══════════════════════════════════════════════════════════════════════
# Exception Hierarchy
# ══════════════════════════════════════════════════════════════════════


class MenthorQError(Exception):
    """Base exception for all MenthorQ client errors."""


class MenthorQAuthError(MenthorQError):
    """Login or credential failure."""


class MenthorQNotFoundError(MenthorQError):
    """Page or ticker not found."""


class MenthorQExtractionError(MenthorQError):
    """Vision or HTML parse failure."""


# ══════════════════════════════════════════════════════════════════════
# Client
# ══════════════════════════════════════════════════════════════════════


class MenthorQClient:
    """Headless browser client for MenthorQ data extraction.

    Features:
      - Playwright-managed Chromium with WordPress auth
      - HTML table scraping for structured data (EOD, screeners)
      - Screenshot + Claude Vision for image-rendered data (CTA)
      - Context manager support for clean browser lifecycle
    """

    # ── init / lifecycle ───────────────────────────────────────────

    def __init__(
        self,
        headless: bool = True,
        artifact_dir: str | Path | None = None,
        storage_state_path: str | Path | None = None,
    ):
        self._username = os.environ.get("MENTHORQ_USER", "").strip() or None
        self._password = os.environ.get("MENTHORQ_PASS", "").strip() or None

        if not self._username:
            raise MenthorQAuthError(
                "MENTHORQ_USER environment variable is not set. "
                "Add it to the project root .env file."
            )
        if not self._password:
            raise MenthorQAuthError(
                "MENTHORQ_PASS environment variable is not set. "
                "Add it to the project root .env file."
            )

        self._api_key = self._resolve_api_key()
        self._headless = headless
        self._artifact_dir = Path(artifact_dir) if artifact_dir else None
        self._storage_state_path = Path(storage_state_path) if storage_state_path else None

        # Launch browser and login
        self._pw_context = sync_playwright()
        self._pw = self._pw_context.__enter__()
        self._browser = self._pw.chromium.launch(headless=headless)
        browser_context_kwargs = {
            "viewport": {"width": 1920, "height": 1080},
            "user_agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }
        if self._storage_state_path and self._storage_state_path.exists():
            browser_context_kwargs["storage_state"] = str(self._storage_state_path)
        self._browser_context = self._browser.new_context(**browser_context_kwargs)
        self._page = self._browser_context.new_page()
        if not self._restore_session():
            self._login()

    def close(self) -> None:
        """Close browser and Playwright context."""
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if hasattr(self, "_pw_context") and self._pw_context is not None:
            try:
                self._pw_context.__exit__(None, None, None)
            except Exception:
                pass
            self._pw_context = None

    def __enter__(self) -> "MenthorQClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ── credentials ────────────────────────────────────────────────

    @staticmethod
    def _resolve_api_key() -> Optional[str]:
        """Resolve Anthropic API key from environment."""
        for key in _ANTHROPIC_ENV_KEYS:
            value = os.environ.get(key, "").strip()
            if value:
                return value
        return None

    def _restore_session(self) -> bool:
        """Reuse a stored authenticated browser state when still valid."""
        if not self._storage_state_path or not self._storage_state_path.exists():
            return False

        try:
            self._page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
            time.sleep(2)
        except Exception as exc:
            logger.warning(f"MenthorQ storage-state restore failed during navigation: {exc}")
            return False

        current_url = (self._page.url or "").lower()
        if "/login" in current_url or "/wp-login" in current_url:
            logger.info("MenthorQ storage state present but no longer authenticated.")
            return False

        # Also check page content — MenthorQ may redirect to account URL but show
        # "You are unauthorized to view this page" when the session has expired.
        try:
            page_text = (self._page.evaluate("() => document.body.innerText") or "").lower()
            if "unauthorized to view this page" in page_text or ("email" in page_text and "password" in page_text and "log in" in page_text):
                logger.info("MenthorQ storage state expired (unauthorized content detected).")
                return False
        except Exception:
            pass

        logger.info("MenthorQ session restored from saved storage state.")
        return True

    def _persist_storage_state(self) -> None:
        if not self._storage_state_path:
            return
        try:
            self._storage_state_path.parent.mkdir(parents=True, exist_ok=True)
            self._browser_context.storage_state(path=str(self._storage_state_path))
        except Exception as exc:
            logger.warning(f"Failed to persist MenthorQ storage state: {exc}")

    # ── login ──────────────────────────────────────────────────────

    def _login(self) -> None:
        """Authenticate to MenthorQ via WordPress login form."""
        logger.info("Navigating to MenthorQ login...")
        self._page.goto(LOGIN_URL, wait_until="networkidle", timeout=30000)
        time.sleep(2)

        # WordPress login form — try multiple selector patterns
        username_selectors = [
            'input[name="log"]',
            'input#user_login',
            'input[name="username"]',
            'input[type="text"]',
            'input[type="email"]',
        ]
        password_selectors = [
            'input[name="pwd"]',
            'input#user_pass',
            'input[name="password"]',
            'input[type="password"]',
        ]

        username_filled = False
        for sel in username_selectors:
            el = self._page.query_selector(sel)
            if el:
                el.fill(self._username)
                username_filled = True
                break

        password_filled = False
        for sel in password_selectors:
            el = self._page.query_selector(sel)
            if el:
                el.fill(self._password)
                password_filled = True
                break

        if not username_filled or not password_filled:
            self._capture_debug_artifacts("login", "Login form fields not found on MenthorQ login page.")
            raise MenthorQAuthError(
                "Login form fields not found on MenthorQ login page. "
                f"context={self._login_failure_context()}"
            )

        # Submit
        submit_selectors = [
            'input[name="wp-submit"]',
            'input[type="submit"]',
            'button[type="submit"]',
            '#wp-submit',
        ]
        submit_clicked = False
        for sel in submit_selectors:
            el = self._page.query_selector(sel)
            if el:
                el.click()
                submit_clicked = True
                break

        if not submit_clicked:
            self._capture_debug_artifacts("login", "Login submit control not found on MenthorQ login page.")
            raise MenthorQAuthError(
                "Login submit control not found on MenthorQ login page. "
                f"context={self._login_failure_context()}"
            )

        self._page.wait_for_load_state("networkidle", timeout=30000)
        time.sleep(3)

        # Verify login succeeded
        current_url = self._page.url.lower()
        if "/login" in current_url or "/wp-login" in current_url:
            self._capture_debug_artifacts("login", "Login failed — still on login page after submit.")
            raise MenthorQAuthError(
                "Login failed — still on login page after submit. "
                f"context={self._login_failure_context()}"
            )

        logger.info("MenthorQ login successful.")
        self._persist_storage_state()

    def _login_failure_context(self) -> str:
        """Capture a sanitized summary of the login page for debugging."""
        parts: list[str] = []

        try:
            title = self._page.title() or ""
        except Exception:
            title = ""
        title = self._sanitize_login_text(title, max_len=120)
        if title:
            parts.append(f"page_title={title}")

        body_excerpt = self._page_excerpt()
        if body_excerpt:
            parts.append(f"page_excerpt={body_excerpt}")

        current_url = (self._page.url or "").strip()
        if current_url:
            parts.append(f"url={current_url}")

        return "; ".join(parts) if parts else "no page context captured"

    @staticmethod
    def _sanitize_login_text(text: str, max_len: int = 220) -> str:
        if not isinstance(text, str):
            return ""
        collapsed = re.sub(r"\s+", " ", text).strip()
        if not collapsed:
            return ""
        collapsed = re.sub(
            r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
            "[redacted-email]",
            collapsed,
            flags=re.IGNORECASE,
        )
        return collapsed[:max_len]

    def _sanitize_sensitive_text(self, text: str) -> str:
        if not isinstance(text, str):
            return ""

        sanitized = text
        for secret in (self._username, self._password):
            if secret:
                sanitized = sanitized.replace(secret, "[redacted]")
        sanitized = re.sub(
            r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
            "[redacted-email]",
            sanitized,
            flags=re.IGNORECASE,
        )
        return sanitized

    def _page_excerpt(self) -> str:
        for selector in ("#mepr_error_msg", ".mepr_error", ".message", ".notice", "body"):
            text = ""
            try:
                text = self._page.locator(selector).inner_text(timeout=1000) or ""
            except Exception:
                pass
            text = self._sanitize_login_text(text)
            if not text:
                try:
                    text = self._sanitize_login_text(self._page.text_content(selector) or "")
                except Exception:
                    text = ""
            if text:
                return text
        return ""

    def _capture_debug_artifacts(self, stage: str, error_message: str | None = None) -> None:
        if self._artifact_dir is None:
            return

        self._artifact_dir.mkdir(parents=True, exist_ok=True)
        context = {
            "stage": stage,
            "final_url": (self._page.url or "").strip(),
            "page_title": self._sanitize_login_text(self._page.title() or "", max_len=160),
            "page_excerpt": self._page_excerpt(),
            "error_message": self._sanitize_login_text(error_message or "", max_len=220),
        }

        try:
            html = self._sanitize_sensitive_text(self._page.content() or "")
            (self._artifact_dir / "page.html").write_text(html, encoding="utf-8")
        except Exception:
            pass

        try:
            png = self._page.screenshot(type="png", full_page=True)
        except Exception:
            png = None
        if isinstance(png, (bytes, bytearray)):
            (self._artifact_dir / "page.png").write_bytes(bytes(png))

        (self._artifact_dir / "context.json").write_text(
            json.dumps(context, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    # ── navigation ─────────────────────────────────────────────────

    def _navigate(self, params: Dict[str, str]) -> Page:
        """Build MenthorQ URL from params, navigate, wait for load.

        Uses ``domcontentloaded`` instead of ``networkidle`` because
        chart-heavy pages (EOD, dashboards) have persistent network
        activity that prevents networkidle from resolving.

        Detects mid-session auth expiry: if MenthorQ redirects to the
        login page, re-authenticates and retries the navigation once.
        """
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{BASE_URL}?{query}"
        logger.info(f"Navigating to: {url}")
        self._page.goto(url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)

        current_url = (self._page.url or "").lower()
        if "/login" in current_url or "/wp-login" in current_url:
            logger.warning("Session expired mid-flow — re-authenticating")
            self._login()
            self._page.goto(url, wait_until="domcontentloaded", timeout=60000)
            time.sleep(5)

        return self._page

    # ══════════════════════════════════════════════════════════════
    # Phase 1: Core Methods
    # ══════════════════════════════════════════════════════════════

    # ── EOD ─────────────────────────────────────────────────────

    def get_eod(self, ticker: str, date: str) -> dict:
        """Fetch end-of-day data for a ticker via HTML scraping.

        Args:
            ticker: Stock/index ticker (e.g. "SPX", "AAPL")
            date: Date string YYYY-MM-DD

        Returns:
            Dict with fields like last_price, change_pct, iv_30d, qscore, etc.

        Raises:
            MenthorQExtractionError: If scraping returns empty data.
        """
        self._navigate({
            "action": "data",
            "type": "dashboard",
            "commands": "eod",
            "tickers": "commons",
            "date": date,
            "ticker": ticker,
        })

        result = self._scrape_eod_fields(self._page)
        if not result:
            raise MenthorQExtractionError(
                f"EOD scrape returned empty data for {ticker} on {date}. "
                "Page may not have loaded or ticker may be invalid."
            )
        return result

    # ── CTA ─────────────────────────────────────────────────────

    def get_cta(self, date: str) -> Dict[str, List[Dict[str, Any]]]:
        """Fetch CTA positioning data via S3 image download + Vision extraction.

        Downloads full-resolution PNGs from S3 (linked inside each card's
        ``<a class="lightbox"><img src="...">``), then sends those to Vision.
        Falls back to card screenshots if S3 download fails.

        Args:
            date: Date string YYYY-MM-DD

        Returns:
            Dict mapping table keys ("main", "index", "commodity", "currency")
            to lists of asset positioning dicts.

        Raises:
            MenthorQExtractionError: If no data could be extracted.
        """
        if not self._api_key:
            raise MenthorQExtractionError(
                "No Anthropic API key found. Set ANTHROPIC_API_KEY in environment."
            )

        self._navigate({
            "action": "data",
            "type": "dashboard",
            "commands": "cta",
            "date": date,
        })

        # Wait for card images to render (dynamic content, up to 30s)
        for _ in range(10):
            count = self._page.evaluate(
                "() => document.querySelectorAll('.command-card img').length"
            )
            if count >= len(CTA_SLUGS):
                break
            time.sleep(3)

        # Try S3 download first, fall back to screenshots
        images = self._download_card_images(self._page, CTA_SLUGS)
        if not images:
            logger.warning("S3 download failed, falling back to card screenshots")
            images = self._screenshot_cards(self._page, CTA_SLUGS)

        if not images:
            self._capture_debug_artifacts("cta-extraction", f"No CTA card images captured for {date}.")
            raise MenthorQExtractionError(
                f"No CTA card images captured for {date}."
            )

        tables: Dict[str, List[Dict]] = {}
        for table_key, png_bytes in images.items():
            extracted = self._extract_via_vision(png_bytes, _CTA_EXTRACTION_PROMPT)
            if extracted:
                tables[table_key] = extracted

        if not tables:
            self._capture_debug_artifacts("cta-extraction", f"Vision extraction returned no data for CTA tables on {date}.")
            raise MenthorQExtractionError(
                f"Vision extraction returned no data for CTA tables on {date}."
            )

        return tables

    # ── Screeners ──────────────────────────────────────────────

    def get_screener(self, commands: str) -> List[Dict[str, Any]]:
        """Fetch screener results via HTML table scraping.

        Args:
            commands: Screener type — "options", "flow", "unusual"

        Returns:
            List of dicts, one per screener row.
        """
        self._navigate({
            "action": "data",
            "type": "screener",
            "commands": commands,
        })
        return self._scrape_tables(self._page)

    def get_screener_category(
        self, category: str, slug: str
    ) -> List[Dict[str, Any]]:
        """Fetch category screener results via HTML table scraping.

        Args:
            category: Screener category (e.g. "gamma", "volatility")
            slug: Specific screener slug (e.g. "highest_gex_change")

        Returns:
            List of dicts, one per screener row.

        Raises:
            MenthorQExtractionError: If category or slug is not in SCREENER_SLUGS.
        """
        if category not in SCREENER_SLUGS:
            raise MenthorQExtractionError(
                f"Unknown screener category: {category}. "
                f"Valid categories: {', '.join(sorted(SCREENER_SLUGS))}"
            )
        if slug not in SCREENER_SLUGS[category]:
            raise MenthorQExtractionError(
                f"Unknown slug '{slug}' for category '{category}'. "
                f"Valid slugs: {', '.join(SCREENER_SLUGS[category])}"
            )
        self._navigate({
            "action": "data",
            "type": "screeners",
            "category": category,
            "slug": slug,
        })
        return self._scrape_tables(self._page)

    def discover_screener_cards(
        self, category: str
    ) -> List[Dict[str, str]]:
        """Navigate to a screener category page and discover all sub-screener cards.

        This navigates to the category overview (without a slug) and scrapes
        the DOM for card elements that represent each sub-screener. Useful for
        verifying SCREENER_SLUGS against the live DOM.

        Args:
            category: Screener category (e.g. "gamma", "volatility").

        Returns:
            List of dicts with keys: title, slug, description.

        Raises:
            MenthorQExtractionError: If category is not valid.
        """
        valid_categories = set(SCREENER_SLUGS.keys())
        if category not in valid_categories:
            raise MenthorQExtractionError(
                f"Unknown screener category: {category}. "
                f"Valid categories: {', '.join(sorted(valid_categories))}"
            )
        self._navigate({
            "action": "data",
            "type": "screeners",
            "category": category,
        })
        cards = self._page.evaluate("""() => {
            const cards = [];
            // Look for clickable screener cards in the DOM
            const cardElements = document.querySelectorAll(
                '.screener-card, .command-card, [data-slug], a[href*="slug="]'
            );
            for (const el of cardElements) {
                const title = (
                    el.querySelector('h3, h4, .card-title, .screener-title')
                    || el.querySelector(':first-child')
                );
                const desc = el.querySelector('p, .card-description, .screener-description');
                const slug = (
                    el.getAttribute('data-slug')
                    || el.getAttribute('data-command-slug')
                    || (el.href && new URL(el.href).searchParams.get('slug'))
                    || ''
                );
                if (title) {
                    cards.push({
                        title: title.textContent.trim(),
                        slug: slug,
                        description: desc ? desc.textContent.trim() : '',
                    });
                }
            }
            // Fallback: look for any links with slug params on the page
            if (cards.length === 0) {
                const links = document.querySelectorAll('a[href*="slug="]');
                for (const link of links) {
                    const url = new URL(link.href);
                    const slug = url.searchParams.get('slug') || '';
                    cards.push({
                        title: link.textContent.trim(),
                        slug: slug,
                        description: '',
                    });
                }
            }
            return cards;
        }""")
        return cards if isinstance(cards, list) else []

    def get_all_screener_data(
        self, category: str
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Fetch data for ALL sub-screeners in a category.

        Iterates every slug in SCREENER_SLUGS[category], navigates to each
        sub-screener page, scrapes the table, and returns all results.

        Args:
            category: Screener category (e.g. "gamma", "volatility").

        Returns:
            Dict mapping slug → list of row dicts.
            Example: {"highest_gex_change": [{"ticker": "HYG", ...}, ...], ...}

        Raises:
            MenthorQExtractionError: If category is not valid.
        """
        if category not in SCREENER_SLUGS:
            raise MenthorQExtractionError(
                f"Unknown screener category: {category}. "
                f"Valid categories: {', '.join(sorted(SCREENER_SLUGS))}"
            )
        results: Dict[str, List[Dict[str, Any]]] = {}
        for slug in SCREENER_SLUGS[category]:
            try:
                self._navigate({
                    "action": "data",
                    "type": "screeners",
                    "category": category,
                    "slug": slug,
                })
                data = self._scrape_tables(self._page)
                results[slug] = data
                logger.info(
                    f"Screener {category}/{slug}: {len(data)} rows"
                )
            except Exception as exc:
                logger.warning(f"Screener {category}/{slug} failed: {exc}")
                results[slug] = []
        return results

    # ── Summary ──────────────────────────────────────────────────

    def get_summary(self, category: str) -> List[Dict[str, Any]]:
        """Fetch summary page data via HTML table scraping.

        Summary pages contain overview tables for asset classes (e.g.
        Active Futures with 93 rows, Crypto Market Summary with 16 rows).

        Args:
            category: Summary category — "futures" or "cryptos".

        Returns:
            List of dicts, one per table row.

        Raises:
            MenthorQExtractionError: If category is not valid.
        """
        if category not in SUMMARY_CATEGORIES:
            raise MenthorQExtractionError(
                f"Unknown summary category: {category}. "
                f"Valid categories: {', '.join(sorted(SUMMARY_CATEGORIES))}"
            )
        self._navigate({
            "action": "data",
            "type": "summary",
            "category": category,
        })
        return self._scrape_tables(self._page)

    # ── Forex Levels ─────────────────────────────────────────────

    def get_forex_levels(self) -> Dict[str, List[Dict[str, Any]]]:
        """Fetch forex gamma levels and blindspot data via text card scraping.

        The forex dashboard renders two command cards (``forex_gamma`` and
        ``forex_blindspot``) with plain text (not HTML tables or images).
        Each card contains comma-separated key-value pairs per forex pair.

        Returns:
            Dict with keys ``"gamma"`` and ``"blindspot"``, each mapping to
            a list of dicts (one per forex pair) with parsed fields.

            Example::

                {
                    "gamma": [
                        {"pair": "EURUSD", "call_resistance": 1.196, "put_support": 1.161, ...},
                        ...
                    ],
                    "blindspot": [
                        {"pair": "EURUSD", "bl_1": 1.161, "bl_2": 1.164, ...},
                        ...
                    ]
                }

        Raises:
            MenthorQExtractionError: If no data can be extracted.
        """
        self._navigate({
            "action": "data",
            "type": "dashboard",
            "commands": "forex",
        })

        # Wait for command cards to render
        for _ in range(5):
            count = self._page.evaluate(
                "() => document.querySelectorAll('.command-card').length"
            )
            if count >= 2:
                break
            time.sleep(3)

        result: Dict[str, List[Dict[str, Any]]] = {}

        for card_slug, key in [("forex_gamma", "gamma"), ("forex_blindspot", "blindspot")]:
            text = self._scrape_forex_text_card(self._page, card_slug)
            if text:
                parsed = self._parse_forex_text(text)
                result[key] = parsed
                logger.info(f"Forex {key}: {len(parsed)} pairs parsed")
            else:
                logger.warning(f"No text found for forex card: {card_slug}")
                result[key] = []

        if not result.get("gamma") and not result.get("blindspot"):
            raise MenthorQExtractionError(
                "Forex levels extraction returned no data for either card."
            )

        return result

    # ══════════════════════════════════════════════════════════════
    # Phase 2: Dashboard Images + Asset Lists
    # ══════════════════════════════════════════════════════════════

    # ── Dashboard Images ─────────────────────────────────────────

    def get_dashboard_image(
        self,
        command: str,
        *,
        ticker: str | None = None,
        tickers: str | None = None,
    ) -> bytes:
        """Fetch a dashboard image, preferring S3 download over screenshot.

        Navigates to the dashboard page, optionally clicks a ticker tab to
        load that ticker's cards, then downloads the full-resolution S3 image.
        Falls back to a viewport screenshot if no S3 image is available.

        Args:
            command: Dashboard command slug (e.g. "eod", "vol", "cta").
            ticker: Optional ticker tab to click (e.g. "nvda", "spy").
                Only valid for commands in TICKER_TAB_COMMANDS.
            tickers: Optional tickers URL param (auto-populated from
                DASHBOARD_COMMANDS if not provided).

        Returns:
            PNG image bytes (S3 original or viewport screenshot).

        Raises:
            MenthorQExtractionError: If neither S3 download nor screenshot succeeds,
                if the command is invalid, or if ticker is passed for a
                command that doesn't support ticker tabs.
        """
        # Validate command
        if command not in DASHBOARD_COMMANDS:
            raise MenthorQExtractionError(
                f"Unknown dashboard command: {command}. "
                f"Valid commands: {', '.join(sorted(DASHBOARD_COMMANDS))}"
            )

        # Validate ticker tab usage
        if ticker and command not in TICKER_TAB_COMMANDS:
            raise MenthorQExtractionError(
                f"Command '{command}' does not support ticker tabs. "
                f"Ticker tabs are only available for: {', '.join(sorted(TICKER_TAB_COMMANDS))}"
            )

        params: Dict[str, str] = {
            "action": "data",
            "type": "dashboard",
            "commands": command,
        }
        # Auto-populate tickers from DASHBOARD_COMMANDS if not provided
        required_tickers = DASHBOARD_COMMANDS[command]
        if tickers:
            params["tickers"] = tickers
        elif required_tickers:
            params["tickers"] = required_tickers

        self._navigate(params)

        # Wait for card images to render (up to 15s)
        for _ in range(5):
            count = self._page.evaluate(
                "() => document.querySelectorAll('.command-card img').length"
            )
            if count >= 1:
                break
            time.sleep(3)

        # Click ticker tab if specified
        if ticker:
            tab = self._page.query_selector(f'[data-ticker="{ticker}"]')
            if tab:
                tab.click()
                time.sleep(3)
                # Wait for new cards to load after tab click
                for _ in range(5):
                    count = self._page.evaluate(
                        "() => document.querySelectorAll('.command-card img').length"
                    )
                    if count >= 1:
                        break
                    time.sleep(3)
            else:
                logger.warning(f"Ticker tab not found: {ticker}")

        # Try S3 download first
        slugs = {command: command}
        images = self._download_card_images(self._page, slugs)
        if images:
            png = next(iter(images.values()))
            logger.info(f"Dashboard S3 image: {command} ({len(png):,} bytes)")
            return png

        # Fall back to viewport screenshot
        logger.warning(
            f"No S3 image for {command}, falling back to viewport screenshot"
        )
        try:
            png = self._page.screenshot(type="png", full_page=False)
        except Exception as exc:
            raise MenthorQExtractionError(
                f"Dashboard screenshot failed for command={command}: {exc}"
            ) from exc

        if not png:
            raise MenthorQExtractionError(
                f"Dashboard screenshot returned empty bytes for command={command}."
            )

        logger.info(f"Dashboard image: {command} ({len(png):,} bytes)")
        return png

    # ── Intraday ─────────────────────────────────────────────────

    def get_intraday(self) -> List[Dict[str, Any]]:
        """Fetch intraday data via HTML table scraping.

        Returns:
            List of dicts with intraday data rows.
        """
        self._navigate({
            "action": "data",
            "type": "dashboard",
            "commands": "intraday",
        })
        return self._scrape_tables(self._page)

    # ── Futures ──────────────────────────────────────────────────

    def get_futures_list(self) -> List[Dict[str, Any]]:
        """Fetch list of futures instruments via HTML table scraping.

        Returns:
            List of dicts with futures instrument data.
        """
        self._navigate({
            "action": "data",
            "type": "futures",
            "commands": "list",
        })
        return self._scrape_tables(self._page)

    def get_futures_detail(self, ticker: str) -> List[Dict[str, Any]]:
        """Fetch detail data for a specific futures instrument.

        Args:
            ticker: Futures ticker (e.g. "ES", "NQ", "CL").

        Returns:
            List of dicts with futures detail data.
        """
        self._navigate({
            "action": "data",
            "type": "futures",
            "commands": "detail",
            "ticker": ticker,
        })
        return self._scrape_tables(self._page)

    def get_futures_contracts(
        self, ticker: str, date: str
    ) -> List[Dict[str, Any]]:
        """Fetch contracts for a futures instrument on a given date.

        Args:
            ticker: Futures ticker (e.g. "ES").
            date: Date string YYYY-MM-DD.

        Returns:
            List of dicts with contract data.
        """
        self._navigate({
            "action": "data",
            "type": "futures",
            "commands": "contracts",
            "ticker": ticker,
            "date": date,
        })
        return self._scrape_tables(self._page)

    # ── Forex ────────────────────────────────────────────────────

    def get_forex_list(self) -> List[Dict[str, Any]]:
        """Fetch list of forex instruments via HTML table scraping.

        Returns:
            List of dicts with forex instrument data.
        """
        self._navigate({
            "action": "data",
            "type": "forex",
            "commands": "list",
        })
        return self._scrape_tables(self._page)

    def get_forex_detail(self, ticker: str) -> List[Dict[str, Any]]:
        """Fetch detail data for a specific forex pair.

        Args:
            ticker: Forex pair (e.g. "EURUSD", "GBPUSD").

        Returns:
            List of dicts with forex detail data.
        """
        self._navigate({
            "action": "data",
            "type": "forex",
            "commands": "detail",
            "ticker": ticker,
        })
        return self._scrape_tables(self._page)

    # ── Crypto ───────────────────────────────────────────────────

    def get_crypto_list(self) -> List[Dict[str, Any]]:
        """Fetch list of crypto instruments via HTML table scraping.

        Returns:
            List of dicts with crypto instrument data.
        """
        self._navigate({
            "action": "data",
            "type": "crypto",
            "commands": "list",
        })
        return self._scrape_tables(self._page)

    def get_crypto_detail(self, ticker: str) -> List[Dict[str, Any]]:
        """Fetch detail data for a specific crypto asset.

        Args:
            ticker: Crypto ticker (e.g. "BTC", "ETH").

        Returns:
            List of dicts with crypto detail data.
        """
        self._navigate({
            "action": "data",
            "type": "crypto",
            "commands": "detail",
            "ticker": ticker,
        })
        return self._scrape_tables(self._page)

    # ══════════════════════════════════════════════════════════════
    # Low-Level Extraction Methods
    # ══════════════════════════════════════════════════════════════

    def _scrape_tables(self, page: Page) -> List[Dict[str, Any]]:
        """Extract all HTML tables from page into list of dicts.

        Each table row becomes a dict with column headers as keys.
        Returns combined rows from all tables found on the page.
        """
        result = page.evaluate("""() => {
            const tables = document.querySelectorAll('table');
            const allRows = [];
            for (const table of tables) {
                const headers = [];
                const headerRow = table.querySelector('thead tr, tr:first-child');
                if (!headerRow) continue;
                for (const th of headerRow.querySelectorAll('th, td')) {
                    headers.push(th.textContent.trim().toLowerCase().replace(/[\\s\\/]+/g, '_'));
                }
                if (headers.length === 0) continue;
                const bodyRows = table.querySelectorAll('tbody tr, tr:not(:first-child)');
                for (const row of bodyRows) {
                    const cells = row.querySelectorAll('td');
                    if (cells.length === 0) continue;
                    const obj = {};
                    for (let i = 0; i < Math.min(headers.length, cells.length); i++) {
                        let val = cells[i].textContent.trim();
                        // Try to parse numbers
                        const num = parseFloat(val.replace(/[,%$]/g, ''));
                        obj[headers[i]] = isNaN(num) ? val : num;
                    }
                    allRows.push(obj);
                }
            }
            return allRows;
        }""")
        return result if isinstance(result, list) else []

    def _scrape_eod_fields(self, page: Page) -> Dict[str, Any]:
        """Extract EOD-specific fields from the dashboard page.

        The EOD page renders data in two DOM sections:
          1. ``.ticker-container`` → ``.ticker-info`` divs (price, change, IV, etc.)
          2. ``.ticker-qscore-wrapper`` → ``.ticker-qscore-item`` divs (scores)
        """
        result = page.evaluate("""() => {
            const data = {};

            const num = (s) => {
                if (!s) return null;
                const n = parseFloat(s.replace(/[,%$±]/g, ''));
                return isNaN(n) ? null : n;
            };

            // 1. Ticker name from .ticker-container
            const container = document.querySelector('.ticker-container');
            if (!container) return data;

            const nameEl = container.querySelector('.ticker-name');
            if (nameEl) data.name = nameEl.textContent.trim();

            // 2. Info fields from .ticker-info divs
            const infos = container.querySelectorAll('.ticker-info');
            for (const info of infos) {
                const titleEl = info.querySelector('.ticker-info-title');
                const contentEl = info.querySelector('.ticker-info-content');
                if (!titleEl || !contentEl) continue;

                const key = titleEl.textContent.trim().toLowerCase()
                    .replace(/[\\s\\/]+/g, '_').replace(/[^a-z0-9_]/g, '');
                const val = contentEl.textContent.trim();
                const n = num(val);
                data[key] = n !== null ? n : val;
            }

            // 3. QScore items from .ticker-qscore-item divs
            const qscoreItems = container.querySelectorAll('.ticker-qscore-item');
            for (const item of qscoreItems) {
                const valueEl = item.querySelector('.item-value');
                const labelEl = item.querySelector('.item-label');
                const titleEl = item.querySelector('.item-title');
                const descEl = item.querySelector('.item-description');
                if (!titleEl || !valueEl) continue;

                const key = 'qscore_' + titleEl.textContent.trim().toLowerCase()
                    .replace(/[\\s]+/g, '_');
                data[key] = {
                    score: parseInt(valueEl.textContent.trim()) || 0,
                    label: labelEl ? labelEl.textContent.trim() : '',
                    description: descEl ? descEl.textContent.trim() : '',
                };
            }

            return data;
        }""")
        return result if isinstance(result, dict) else {}

    def _scrape_forex_text_card(self, page: Page, card_slug: str) -> Optional[str]:
        """Extract raw text from a forex command card's .data-text div.

        Args:
            page: Current Playwright page.
            card_slug: The data-command-slug value (e.g. "forex_gamma").

        Returns:
            Raw text content or None if not found.
        """
        text = page.evaluate(
            """(slug) => {
                const card = document.querySelector(
                    `.command-card[data-command-slug="${slug}"]`
                );
                if (!card) return null;
                const textEl = card.querySelector('.data-text');
                if (!textEl) {
                    // Fallback: try broader selectors
                    const content = card.querySelector('.data-content, .main-container');
                    return content ? content.textContent.trim() : null;
                }
                return textEl.textContent.trim();
            }""",
            card_slug,
        )
        return text if text else None

    @staticmethod
    def _parse_forex_text(text: str) -> List[Dict[str, Any]]:
        """Parse forex card text into structured list of pair dicts.

        Input format (one or more pairs, separated by $)::

            $EURUSD: Call Resistance, 1.19602, Put Support, 1.16113, HVL, 1.17857, ...
            $GBPUSD: Call Resistance, 1.35, ...

        Returns:
            List of dicts, one per forex pair, with "pair" key and
            numeric/string fields parsed from key-value pairs.
        """
        if not text or not text.strip():
            return []

        pairs: List[Dict[str, Any]] = []

        # Split by $ to isolate each pair's data
        segments = text.split("$")

        for segment in segments:
            segment = segment.strip()
            if not segment:
                continue

            # Extract pair name (before the colon)
            if ":" not in segment:
                continue

            pair_name, rest = segment.split(":", 1)
            pair_name = pair_name.strip()

            if not pair_name:
                continue

            row: Dict[str, Any] = {"pair": pair_name}

            # Parse comma-separated key-value pairs
            parts = [p.strip() for p in rest.split(",")]

            i = 0
            while i < len(parts):
                part = parts[i].strip()
                if not part:
                    i += 1
                    continue

                # Try to parse as: key, value (next part is the value)
                if i + 1 < len(parts):
                    value_str = parts[i + 1].strip()
                    try:
                        value = float(value_str)
                        # Normalize key: lowercase, spaces to underscores
                        key = part.lower().replace(" ", "_").replace("-", "_")
                        row[key] = value
                        i += 2
                        continue
                    except (ValueError, TypeError):
                        pass

                # If we can't parse as key-value, try the part itself as a value
                try:
                    float(part)
                    # It's a standalone number — skip
                    i += 1
                except (ValueError, TypeError):
                    # Non-numeric standalone text — add as string value
                    key = part.lower().replace(" ", "_").replace("-", "_")
                    row[key] = part
                    i += 1

            if len(row) > 1:  # More than just "pair"
                pairs.append(row)

        return pairs

    def _download_card_images(
        self, page: Page, slugs: Dict[str, str]
    ) -> Dict[str, bytes]:
        """Download full-resolution S3 images from CTA card elements.

        Each card contains ``<a class="lightbox"><img src="https://...s3...">``
        with the original high-res PNG. Downloads those directly via httpx
        instead of screenshotting the tiny card thumbnail.

        Args:
            page: Current Playwright page.
            slugs: Mapping of key names to data-command-slug values.

        Returns:
            Dict mapping key names to PNG bytes for each downloaded image.
            Returns empty dict if any image fails (caller should fall back).
        """
        import httpx

        images: Dict[str, bytes] = {}
        for key, slug in slugs.items():
            try:
                img_src = page.evaluate(
                    """(slug) => {
                        const card = document.querySelector(
                            `.command-card[data-command-slug="${slug}"]`
                        ) || document.querySelector(
                            `[data-command-slug="${slug}"]`
                        );
                        if (!card) return null;
                        const img = card.querySelector('img');
                        return img ? img.src : null;
                    }""",
                    slug,
                )
                if not img_src:
                    logger.warning(f"No img src found for card slug: {slug}")
                    return {}

                resp = httpx.get(img_src, timeout=30.0)
                if resp.status_code != 200:
                    logger.warning(
                        f"S3 download failed for {slug}: HTTP {resp.status_code}"
                    )
                    return {}

                images[key] = resp.content
                logger.info(
                    f"Downloaded S3 image: {key} ({len(resp.content):,} bytes)"
                )
            except Exception as exc:
                logger.warning(f"S3 download error for {slug}: {exc}")
                return {}

        return images

    def _screenshot_cards(
        self, page: Page, slugs: Dict[str, str]
    ) -> Dict[str, bytes]:
        """Screenshot card elements identified by data-command-slug.

        Args:
            page: Current Playwright page.
            slugs: Mapping of key names to data-command-slug values.

        Returns:
            Dict mapping key names to PNG bytes for each captured card.
        """
        screenshots: Dict[str, bytes] = {}
        for key, slug in slugs.items():
            try:
                card = page.query_selector(f'[data-command-slug="{slug}"]')
                if not card:
                    card = page.query_selector(
                        f'.command-card:has([data-command-slug="{slug}"])'
                    )
                if not card:
                    logger.warning(f"Card not found for slug: {slug}")
                    continue

                container = card.query_selector(".main-container") or card
                png = container.screenshot(type="png")
                screenshots[key] = png
                logger.info(f"Screenshot: {key} ({len(png):,} bytes)")
            except Exception as exc:
                logger.warning(f"Screenshot failed for {slug}: {exc}")
        return screenshots

    def _extract_via_vision(
        self, png_bytes: bytes, prompt: str
    ) -> Optional[List[Dict[str, Any]]]:
        """Send a screenshot to Claude Haiku Vision for structured extraction.

        Args:
            png_bytes: PNG image bytes.
            prompt: Extraction prompt describing the desired output format.

        Returns:
            List of dicts parsed from Vision response, or None on failure.
        """
        if not self._api_key:
            logger.warning("No Anthropic API key — skipping Vision extraction.")
            return None

        import httpx

        b64 = base64.b64encode(png_bytes).decode("utf-8")

        try:
            resp = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 4096,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/png",
                                        "data": b64,
                                    },
                                },
                                {"type": "text", "text": prompt},
                            ],
                        }
                    ],
                },
                timeout=60.0,
            )

            if resp.status_code != 200:
                logger.warning(
                    f"Vision API error: {resp.status_code} {resp.text[:200]}"
                )
                return None

            data = resp.json()
            text = None
            for block in data.get("content", []):
                if block.get("type") == "text":
                    text = block.get("text", "")
                    break

            if not text:
                return None

            # Strip markdown fences if present
            cleaned = text.strip()
            if cleaned.startswith("```"):
                cleaned = (
                    cleaned.split("\n", 1)[-1] if "\n" in cleaned else cleaned[3:]
                )
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

            parsed = json.loads(cleaned)
            if not isinstance(parsed, list):
                return None

            logger.info(f"Vision extracted {len(parsed)} rows")
            return parsed

        except Exception as exc:
            logger.warning(f"Vision extraction failed: {exc}")
            return None
