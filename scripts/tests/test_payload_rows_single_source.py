"""UW row lists are read in one place.

A payload is either a list of row dicts or {"data": [rows]}.
Scanners and the catalyst jobs used to carry that rule separately.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OWNER = "scripts/utils/payload_rows.py"
DEFS = ("def _as_rows(", "def _rows_from_payload(")


def test_uw_row_list_has_one_definition():
    found = {name: [] for name in DEFS}
    for path in (REPO_ROOT / "scripts").rglob("*.py"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel.startswith("scripts/tests/") or "/tests/" in f"/{rel}":
            continue
        text = path.read_text(encoding="utf-8")
        for name in DEFS:
            if name in text:
                found[name].append(rel)
    assert found == {name: [] for name in DEFS}


def test_rows_from_payload_keeps_dict_rows_only():
    from utils.payload_rows import rows_from_payload

    row = {"ticker": "AAPL"}
    assert rows_from_payload({"data": [row, "nope", 1]}) == [row]
    assert rows_from_payload([row, None]) == [row]
    assert rows_from_payload({"data": "nope"}) == []
    assert rows_from_payload({"rows": [row]}) == []
    assert rows_from_payload("nope") == []
    assert rows_from_payload(None) == []
