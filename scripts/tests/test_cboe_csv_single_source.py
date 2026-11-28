"""One Cboe daily-prices parser.

Straddle, VIX term structure, COR, and VIX correlation each walked the
same MM/DD/YYYY CSV. The VIX model spread file is a different schema.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OWNER = "scripts/utils/cboe_csv.py"
MARKER = '(raw.get("DATE") or "").strip(), "%m/%d/%Y"'


def test_cboe_daily_csv_loop_has_one_home():
    found = []
    for path in (REPO_ROOT / "scripts").rglob("*.py"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel.startswith("scripts/tests/") or "/tests/" in f"/{rel}":
            continue
        if MARKER in path.read_text(encoding="utf-8"):
            found.append(rel)
    assert found == [OWNER]


def test_parse_daily_prices_skips_a_bad_row_and_sorts():
    from utils.cboe_csv import parse_daily_prices

    text = "DATE,CLOSE\n08/14/2026,14.25\nbad,1\n08/13/2026,14.00\n"
    assert parse_daily_prices(text, "CLOSE") == [
        {"date": "2026-08-13", "value": 14.0},
        {"date": "2026-08-14", "value": 14.25},
    ]
    assert parse_daily_prices(text, "SPX") == []
