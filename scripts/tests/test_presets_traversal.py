"""Security regression: scripts/utils/presets.py must reject path traversal.

The client-supplied `preset` string is forwarded unsanitized from four
authenticated scan endpoints (leap/garch/theta/strength) into load_preset().
Before the fix its only sanitization was name.replace(".json", "") — a substring
strip, not a containment check — so a name like "../../../etc/passwd" resolved
outside PRESETS_DIR. These tests pin the guard closed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from utils.presets import load_preset, _resolve_preset_path, PRESETS_DIR  # noqa: E402


TRAVERSAL_NAMES = [
    "../../../etc/passwd",
    "..%2f..%2fetc%2fpasswd",  # literal — not URL-decoded, but still not a slug
    "../secrets",
    "/etc/passwd",
    "foo/bar",
    "foo\\bar",
    "..",
    ".",
    "",
    "a" * 100,  # over the 64-char slug ceiling
]


@pytest.mark.parametrize("name", TRAVERSAL_NAMES)
def test_load_preset_rejects_traversal(name):
    with pytest.raises(FileNotFoundError):
        load_preset(name)


@pytest.mark.parametrize("name", TRAVERSAL_NAMES)
def test_resolve_preset_path_rejects_traversal(name):
    with pytest.raises(FileNotFoundError):
        _resolve_preset_path(name.replace(".json", ""))


def test_resolve_preset_path_stays_inside_presets_dir_for_valid_slug():
    resolved = _resolve_preset_path("sp500-semis")
    assert resolved.parent == PRESETS_DIR.resolve()
    assert resolved.name == "sp500-semis.json"


def test_traversal_never_escapes_even_if_target_exists(tmp_path, monkeypatch):
    # Plant a JSON file OUTSIDE the presets dir and confirm a traversal name
    # engineered to reach it is still refused (containment, not existence).
    outside = tmp_path / "evil.json"
    outside.write_text('{"tickers": ["PWNED"]}')
    rel = Path(outside).resolve()
    # Build a name that would join to the outside file if traversal worked.
    depth_up = "../" * (len(PRESETS_DIR.resolve().parts))
    hostile = f"{depth_up}{rel.as_posix().lstrip('/')[:-5]}"
    with pytest.raises(FileNotFoundError):
        load_preset(hostile)
