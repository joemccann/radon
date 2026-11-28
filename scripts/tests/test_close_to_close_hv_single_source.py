"""The scanner historical-vol formula has one definition.

CRI uses utils.realized_vol. The leap scanner still has a separate copy
that does not require the later close to be positive.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OWNER = "scripts/utils/close_to_close_hv.py"
MARKER = "if recent[i - 1] > 0 and recent[i] > 0"


def test_scanner_hv_formula_has_one_home():
    found = []
    for path in (REPO_ROOT / "scripts").rglob("*.py"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel.startswith("scripts/tests/") or "/tests/" in f"/{rel}":
            continue
        if MARKER in path.read_text(encoding="utf-8"):
            found.append(rel)
    assert found == [OWNER]


def test_close_to_close_hv_needs_two_returns():
    from utils.close_to_close_hv import close_to_close_hv

    assert close_to_close_hv([100.0, 101.0], 2) is None
    value = close_to_close_hv([100.0, 101.0, 99.0, 102.0], 2)
    assert value is not None and value > 0
    assert close_to_close_hv([100.0, 0.0, -1.0, 1.0], 2) is None
