"""A missing or corrupt JSON object is read in one place.

Five scanners and the off-hours cache gate used to repeat the same
miss / parse-failure / non-dict rule.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OWNER = "scripts/utils/json_dict.py"
BODY = "def _read_cache_file("


def test_scanner_cache_reader_is_not_redefined():
    found = []
    for path in (REPO_ROOT / "scripts").rglob("*.py"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel.startswith("scripts/tests/") or "/tests/" in f"/{rel}":
            continue
        if BODY in path.read_text(encoding="utf-8"):
            found.append(rel)
    assert found == []


def test_read_json_dict_contract(tmp_path):
    from utils.json_dict import read_json_dict

    missing = tmp_path / "missing.json"
    assert read_json_dict(missing) is None

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{", encoding="utf-8")
    assert read_json_dict(corrupt) is None

    array = tmp_path / "array.json"
    array.write_text("[]", encoding="utf-8")
    assert read_json_dict(array) is None

    payload = {"scan_time": "2026-11-28T00:00:00Z"}
    good = tmp_path / "good.json"
    good.write_text(json.dumps(payload), encoding="utf-8")
    assert read_json_dict(good) == payload
