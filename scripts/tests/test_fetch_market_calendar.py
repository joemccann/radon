"""Pure merge logic for the IBKR calendar accumulator.

The IB fetch itself needs a live gateway and is verified live, not in unit tests.
"""
from __future__ import annotations

from fetch_market_calendar import merge_days


def test_merge_overrides_existing_and_keeps_accumulated():
    existing = {
        "2026-06-15": {"status": "open", "open_et": "09:30", "close_et": "16:00"},
        "2026-06-19": {"status": "open", "open_et": "09:30", "close_et": "16:00"},
    }
    new = {
        "2026-06-19": {"status": "closed", "open_et": None, "close_et": None},
        "2026-06-22": {"status": "open", "open_et": "09:30", "close_et": "16:00"},
    }
    merged = merge_days(existing, new)
    # Sorted by date.
    assert list(merged.keys()) == ["2026-06-15", "2026-06-19", "2026-06-22"]
    # IBKR (new) wins for the overlapping date.
    assert merged["2026-06-19"]["status"] == "closed"
    # Older accumulated date preserved.
    assert merged["2026-06-15"]["status"] == "open"


def test_merge_into_empty():
    assert merge_days({}, {"2026-06-22": {"status": "open"}}) == {
        "2026-06-22": {"status": "open"}
    }
