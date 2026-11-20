"""Shared card-screenshot helper.

Renders a card element from a local HTML file to a PNG with Python Playwright.
The radon-api image that runs the share generators ships Python Playwright and
its Chromium (`PLAYWRIGHT_BROWSERS_PATH=/ms-playwright`) but no `playwright`
npm module, so this renders in-process instead of shelling out to Node.
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - playwright is pinned in requirements.txt
    sync_playwright = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SELECTOR = ".card"
NAV_TIMEOUT_MS = 20_000
SETTLE_MS = 300
DEVICE_SCALE_FACTOR = 2


def screenshot_card(html_path: str, png_path: str, selector: str = DEFAULT_SELECTOR) -> bool:
    """Render the card at `selector` in `html_path` to `png_path`.

    Returns True only when the PNG exists and is non-empty. Any render failure
    is logged to stderr and returns False.
    """
    if sync_playwright is None:
        print("screenshot_card: playwright is not installed", file=sys.stderr)
        return False
    try:
        _render(html_path, png_path, selector)
    except Exception as exc:  # noqa: BLE001 — a failed card must not fail the report
        print(f"screenshot_card: render failed for {html_path}: {exc}", file=sys.stderr)
        return False
    return _is_nonempty_file(png_path)


def _render(html_path: str, png_path: str, selector: str) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(device_scale_factor=DEVICE_SCALE_FACTOR)
            page.set_default_timeout(NAV_TIMEOUT_MS)
            _open(page, Path(html_path).resolve().as_uri())
            card = _wait_for_card(page, selector)
            page.wait_for_timeout(SETTLE_MS)
            if card:
                card.screenshot(path=png_path)
            else:
                page.screenshot(path=png_path, full_page=True)
        finally:
            browser.close()


def _open(page, url: str) -> None:
    """Wait for web fonts when the network allows; a blocked font CDN still renders."""
    try:
        page.goto(url, wait_until="networkidle", timeout=NAV_TIMEOUT_MS)
    except Exception:  # noqa: BLE001
        page.goto(url, wait_until="load", timeout=NAV_TIMEOUT_MS)


def _wait_for_card(page, selector: str):
    try:
        page.wait_for_selector(selector, timeout=NAV_TIMEOUT_MS)
    except Exception:  # noqa: BLE001 — fall back to a full-page screenshot
        pass
    return page.query_selector(selector)


def _is_nonempty_file(path: str) -> bool:
    out = Path(path)
    return out.exists() and out.stat().st_size > 0
