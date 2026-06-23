#!/usr/bin/env python3
"""Retired journal bootstrap entry point.

Journal rows are now reconstructed from canonical Turso `executed_orders`
via `scripts/backfill_journal_from_executed_orders.py`, or imported directly
from IB Flex via `scripts/journal_rehydrate.py`.
"""

from __future__ import annotations


def main() -> int:
    print(
        "[bootstrap_journal] retired; use backfill_journal_from_executed_orders.py "
        "or journal_rehydrate.py"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
