"""PERF-063: four share cards must share one Chromium launch.

`screenshot_card` started a fresh Playwright Chromium for every PNG.
A regime/GEX/VCG/CTA share renders four cards per click, so each Share
to X paid four browser-process launches. Sequential cards in one
generator process must reuse a single `chromium.launch`.
"""
from __future__ import annotations

import ast
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from utils import card_screenshot
from utils.card_screenshot import screenshot_card, shared_browser

PNG_BYTES = b"\x89PNG\r\n\x1a\n"
GENERATORS = (
    "generate_regime_share.py",
    "generate_gex_share.py",
    "generate_vcg_share.py",
    "generate_cta_share.py",
)


def _fake_playwright():
    def write_png(path, **_kwargs):
        Path(path).write_bytes(PNG_BYTES)

    page = MagicMock()
    page.screenshot.side_effect = write_png
    card = MagicMock()
    card.screenshot.side_effect = write_png
    page.query_selector.return_value = card

    browser = MagicMock()
    browser.new_page.return_value = page
    chromium = MagicMock()
    chromium.launch.return_value = browser

    playwright = MagicMock()
    playwright.chromium = chromium
    manager = MagicMock()
    manager.__enter__.return_value = playwright
    manager.__exit__.return_value = False
    return MagicMock(return_value=manager), browser, chromium


def test_without_shared_browser_each_card_launches():
    """The one-shot path still launches per call; reuse is opt-in."""
    factory, browser, chromium = _fake_playwright()
    with tempfile.TemporaryDirectory() as tmp:
        pngs = [str(Path(tmp) / f"card-{i}.png") for i in range(4)]
        with patch.object(card_screenshot, "sync_playwright", factory):
            oks = [
                screenshot_card(f"/tmp/card-{i}.html", png)
                for i, png in enumerate(pngs)
            ]
    assert chromium.launch.call_count == 4
    assert oks == [True, True, True, True]
    assert browser.close.call_count == 4


def test_four_cards_share_one_chromium_launch():
    factory, browser, chromium = _fake_playwright()
    with tempfile.TemporaryDirectory() as tmp:
        pngs = [str(Path(tmp) / f"card-{i}.png") for i in range(4)]
        with patch.object(card_screenshot, "sync_playwright", factory):
            with shared_browser():
                oks = [
                    screenshot_card(f"/tmp/card-{i}.html", png)
                    for i, png in enumerate(pngs)
                ]
        written = [Path(png).read_bytes() for png in pngs]

    assert chromium.launch.call_count == 1, (
        f"chromium.launch count {chromium.launch.call_count} wanted 1"
    )
    assert oks == [True, True, True, True]
    assert browser.close.call_count == 1
    assert written == [PNG_BYTES] * 4


def _shared_browser_with(node: ast.AST) -> bool:
    if not isinstance(node, ast.With):
        return False
    for item in node.items:
        ctx = item.context_expr
        func = ctx.func if isinstance(ctx, ast.Call) else ctx
        if isinstance(func, ast.Name) and func.id == "shared_browser":
            return True
    return False


def screenshot_calls_outside_shared_browser(source: str) -> list[int]:
    """Line numbers of screenshot_card() calls not inside `with shared_browser()`."""
    tree = ast.parse(source)
    outside: list[int] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.depth = 0

        def visit_With(self, node: ast.With) -> None:
            shared = _shared_browser_with(node)
            if shared:
                self.depth += 1
            self.generic_visit(node)
            if shared:
                self.depth -= 1

        def visit_Call(self, node: ast.Call) -> None:
            func = node.func
            if isinstance(func, ast.Name) and func.id == "screenshot_card" and self.depth == 0:
                outside.append(node.lineno)
            self.generic_visit(node)

    Visitor().visit(tree)
    return outside


def test_import_line_does_not_count_as_the_wrapper():
    """The old substring check stayed green when the with-block was dropped."""
    source = (
        "from utils.card_screenshot import screenshot_card, shared_browser\n"
        "def main():\n"
        "    screenshot_card('a.html', 'a.png')\n"
    )
    assert "shared_browser" in source
    assert screenshot_calls_outside_shared_browser(source) == [3]


def test_share_generators_open_one_browser_for_the_four_cards():
    scripts = Path(__file__).resolve().parents[1]
    for name in GENERATORS:
        source = (scripts / name).read_text(encoding="utf-8")
        outside = screenshot_calls_outside_shared_browser(source)
        assert outside == [], (
            f"{name} calls screenshot_card outside shared_browser at lines {outside}"
        )
