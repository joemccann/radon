"""Shared card-screenshot helper.

Renders a card element from a local HTML file to a PNG with Python Playwright.
The radon-api image that runs the share generators ships Python Playwright and
its Chromium (`PLAYWRIGHT_BROWSERS_PATH=/ms-playwright`) but no `playwright`
npm module, so this renders in-process instead of shelling out to Node.
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator, Optional

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - playwright is pinned in requirements.txt
    sync_playwright = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SELECTOR = ".card"
NAV_TIMEOUT_MS = 20_000
SETTLE_MS = 300
DEVICE_SCALE_FACTOR = 2

# Lazy session for `shared_browser`: launch happens on the first screenshot
# inside the block so a mocked `screenshot_card` in a generator test never
# starts Chromium.
_session: ContextVar[Optional[dict[str, Any]]] = ContextVar(
    "screenshot_browser_session", default=None
)


@contextmanager
def shared_browser() -> Iterator[None]:
    """Reuse one Chromium for every `screenshot_card` inside the block.

    A share report renders four cards. Without this, each PNG paid a fresh
    `chromium.launch` (PERF-063). Nested calls share the outer session.
    Launch is deferred until the first real screenshot so tests that stub
    `screenshot_card` do not start a browser.
    """
    if _session.get() is not None:
        yield
        return
    state: dict[str, Any] = {"browser": None, "playwright_cm": None}
    token = _session.set(state)
    try:
        yield
    finally:
        _session.reset(token)
        browser = state["browser"]
        if browser is not None:
            try:
                browser.close()
            except Exception:  # noqa: BLE001 — session teardown must not mask the render
                pass
        cm = state["playwright_cm"]
        if cm is not None:
            try:
                cm.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass


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


def _launch_browser() -> tuple[Any, Any]:
    cm = sync_playwright()
    playwright = cm.__enter__()
    try:
        browser = playwright.chromium.launch(headless=True)
    except Exception:
        cm.__exit__(None, None, None)
        raise
    return cm, browser


def _render(html_path: str, png_path: str, selector: str) -> None:
    session = _session.get()
    if session is not None:
        if session["browser"] is None:
            cm, browser = _launch_browser()
            session["playwright_cm"] = cm
            session["browser"] = browser
        _render_on(session["browser"], html_path, png_path, selector)
        return
    cm, browser = _launch_browser()
    try:
        _render_on(browser, html_path, png_path, selector)
    finally:
        try:
            browser.close()
        finally:
            cm.__exit__(None, None, None)


def _render_on(browser: Any, html_path: str, png_path: str, selector: str) -> None:
    page = browser.new_page(device_scale_factor=DEVICE_SCALE_FACTOR)
    try:
        page.set_default_timeout(NAV_TIMEOUT_MS)
        _open(page, Path(html_path).resolve().as_uri())
        card = _wait_for_card(page, selector)
        page.wait_for_timeout(SETTLE_MS)
        if card:
            card.screenshot(path=png_path)
        else:
            page.screenshot(path=png_path, full_page=True)
    finally:
        page.close()


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
