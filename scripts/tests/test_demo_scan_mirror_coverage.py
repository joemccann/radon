"""Every snapshot the demo scan guard serves must be mirrored into demo Turso.

scripts/api/demo_scan.py answers TEST_MODE scan triggers from the newest row
in the demo Turso; scripts/db/mirror_market_snapshots_to_demo.js is the only
producer of those rows. A table named in the guard but absent from the mirror
lists silently serves the empty fallback envelope forever (vol_skew_mr_snapshots
shipped that way). This pins the two lists together.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

demo_scan = importlib.import_module("api.demo_scan")

MIRROR = Path(__file__).resolve().parents[1] / "db" / "mirror_market_snapshots_to_demo.js"
MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations"

# Guard tables that are deliberately NOT mirrored, with the reason.
NOT_MIRRORED = {
    # Portfolio-derived rows; the mirror purges this table on every run
    # (PURGED_ACCOUNT_TABLES) and the guard serves the empty envelope.
    "flow_analysis_snapshots",
}


def _mirror_block(source: str, name: str) -> str:
    return source.split(f"export const {name} = [", 1)[1].split("];", 1)[0]


def _mirror_entries(name: str) -> list[dict[str, str]]:
    block = _mirror_block(MIRROR.read_text(), name)
    entries = []
    for body in re.findall(r"\{([^{}]*)\}", block):
        entries.append(dict(re.findall(r'(\w+): "(\w+)"', body)))
    return entries


def _mirrored_tables() -> set[str]:
    return {
        entry["table"]
        for name in ("LATEST_ONE", "PER_KEY", "HISTORY")
        for entry in _mirror_entries(name)
    }


def _guard_tables() -> set[str]:
    tables = set()
    for sql in demo_scan._SNAPSHOT_SQL.values():
        tables.update(re.findall(r"\bFROM\s+(\w+)", sql, flags=re.IGNORECASE))
    return tables


def _table_columns(table: str) -> set[str]:
    for path in sorted(MIGRATIONS.glob("*.sql")):
        match = re.search(
            rf"CREATE TABLE IF NOT EXISTS {table}\s*\((.*?)\n\);",
            path.read_text(),
            flags=re.DOTALL,
        )
        if match:
            return set(re.findall(r"^\s*(\w+)\s+[A-Z]", match.group(1), flags=re.MULTILINE))
    raise AssertionError(f"{table} has no CREATE TABLE in scripts/db/migrations")


def test_every_demo_scan_snapshot_table_is_mirrored():
    missing = sorted(_guard_tables() - _mirrored_tables() - NOT_MIRRORED)
    assert missing == [], f"demo_scan serves tables the demo mirror never fills: {missing}"


def test_not_mirrored_allowlist_is_not_stale():
    assert NOT_MIRRORED <= _guard_tables()
    assert not (NOT_MIRRORED & _mirrored_tables())


def test_vol_skew_mr_mirrors_latest_scan():
    entry = next(e for e in _mirror_entries("LATEST_ONE") if e["table"] == "vol_skew_mr_snapshots")
    assert entry == {"table": "vol_skew_mr_snapshots", "orderCol": "scan_time"}
    assert "scan_time" in _table_columns("vol_skew_mr_snapshots")


def test_rv_ratio_mirrors_latest_row_per_symbol():
    entry = next(e for e in _mirror_entries("PER_KEY") if e["table"] == "rv_ratio_snapshots")
    assert entry == {"table": "rv_ratio_snapshots", "key": "symbol", "orderCol": "taken_at"}
    assert {"symbol", "taken_at"} <= _table_columns("rv_ratio_snapshots")


def test_every_mirror_order_and_key_column_exists_in_migrations():
    for name in ("LATEST_ONE", "PER_KEY", "HISTORY"):
        for entry in _mirror_entries(name):
            columns = _table_columns(entry["table"])
            for field in ("orderCol", "key"):
                if field in entry:
                    assert entry[field] in columns, (name, entry)
