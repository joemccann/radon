"""Unit tests for the shared card-screenshot helper.

2026-11-19: every share card shipped without PNGs. The helper shelled out to
`node scripts/screenshot_card.cjs`, but the radon-api image (which runs the
generators) has no `playwright` npm module. It does ship Python Playwright and
its Chromium, so the helper renders in-process with Python Playwright.

The fake-browser tests never launch Chromium. The real-render test runs only
where a Playwright Chromium is installed.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils import card_screenshot
from utils.card_screenshot import screenshot_card

PNG_BYTES = b"\x89PNG\r\n\x1a\n"


def _fake_playwright(*, card_found=True, png=PNG_BYTES, launch_error=None):
    """A sync_playwright() stand-in whose screenshots write `png` to `path`."""

    def write_png(path, **_kwargs):
        Path(path).write_bytes(png)

    page = MagicMock()
    page.screenshot.side_effect = write_png
    card = MagicMock()
    card.screenshot.side_effect = write_png
    page.query_selector.return_value = card if card_found else None

    browser = MagicMock()
    browser.new_page.return_value = page
    chromium = MagicMock()
    if launch_error:
        chromium.launch.side_effect = launch_error
    else:
        chromium.launch.return_value = browser

    playwright = MagicMock()
    playwright.chromium = chromium
    manager = MagicMock()
    manager.__enter__.return_value = playwright
    manager.__exit__.return_value = False
    return MagicMock(return_value=manager), page, card, browser


class TestScreenshotCard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.png = str(Path(self.tmp.name) / "card.png")

    def tearDown(self):
        self.tmp.cleanup()

    def test_screenshots_the_selected_card_from_a_file_url(self):
        factory, page, card, browser = _fake_playwright()
        with patch.object(card_screenshot, "sync_playwright", factory):
            ok = screenshot_card("/tmp/card.html", self.png, ".card")

        self.assertTrue(ok)
        self.assertEqual(page.goto.call_args[0][0], Path("/tmp/card.html").resolve().as_uri())
        page.query_selector.assert_called_with(".card")
        card.screenshot.assert_called_once_with(path=self.png)
        browser.close.assert_called_once()

    def test_default_selector_is_dot_card(self):
        factory, page, _card, _browser = _fake_playwright()
        with patch.object(card_screenshot, "sync_playwright", factory):
            screenshot_card("/tmp/card.html", self.png)
        page.query_selector.assert_called_with(".card")

    def test_falls_back_to_full_page_when_card_missing(self):
        factory, page, _card, _browser = _fake_playwright(card_found=False)
        with patch.object(card_screenshot, "sync_playwright", factory):
            ok = screenshot_card("/tmp/card.html", self.png)
        self.assertTrue(ok)
        page.screenshot.assert_called_once_with(path=self.png, full_page=True)

    def test_returns_false_when_png_empty(self):
        factory, _page, _card, _browser = _fake_playwright(png=b"")
        with patch.object(card_screenshot, "sync_playwright", factory):
            self.assertFalse(screenshot_card("/tmp/card.html", self.png))

    def test_returns_false_when_browser_cannot_launch(self):
        factory, _page, _card, _browser = _fake_playwright(
            launch_error=RuntimeError("Executable doesn't exist")
        )
        with patch.object(card_screenshot, "sync_playwright", factory):
            self.assertFalse(screenshot_card("/tmp/card.html", self.png))

    def test_never_shells_out_to_node(self):
        self.assertFalse(hasattr(card_screenshot, "NODE_HELPER"))
        self.assertFalse(
            (card_screenshot.PROJECT_ROOT / "scripts" / "screenshot_card.cjs").exists()
        )


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            p.chromium.launch(headless=True).close()
        return True
    except Exception:  # noqa: BLE001 — any launch failure means no browser here
        return False


@unittest.skipUnless(_chromium_available(), "Playwright Chromium not installed")
class TestScreenshotCardRealRender(unittest.TestCase):
    def test_renders_a_real_png(self):
        with tempfile.TemporaryDirectory() as tmp:
            html = Path(tmp) / "card.html"
            html.write_text('<div class="card" style="width:100px;height:50px;background:#000"></div>')
            png = Path(tmp) / "card.png"
            self.assertTrue(screenshot_card(str(html), str(png)))
            self.assertTrue(png.read_bytes().startswith(PNG_BYTES))


if __name__ == "__main__":
    unittest.main()
