"""The TS order-status vocabulary must not drift from the Python one."""
from __future__ import annotations

import re
from pathlib import Path

from utils import ib_order_status as s

REPO = Path(__file__).resolve().parents[2]
TS = (REPO / "web" / "lib" / "orders" / "ibOrderStatus.ts").read_text()


def _strings(body: str) -> list[str]:
    return re.findall(r'"([^"]+)"', body)


def _ts_set(name: str) -> set[str]:
    hit = re.search(rf"export const {name}\b[^=]*=\s*new Set<\w+>\(\[([^\]]*)\]\)", TS)
    assert hit, f"{name} not found in ibOrderStatus.ts"
    return set(_strings(hit.group(1)))


def _ts_union(name: str) -> set[str]:
    hit = re.search(rf"export type {name}\s*=([^;]*);", TS)
    assert hit, f"type {name} not found in ibOrderStatus.ts"
    return set(_strings(hit.group(1)))


def test_status_union_matches_python():
    assert _ts_union("IbOrderStatus") == s.ALL_STATUSES


def test_sets_match_python():
    for name in ("WORKING", "TERMINAL_FAILED", "FILLED", "TERMINAL", "UNACKNOWLEDGED"):
        assert _ts_set(name) == getattr(s, name), name


def test_cancel_outcome_matches_python():
    assert _ts_union("CancelOutcome") == {m.value for m in s.CancelOutcome}
