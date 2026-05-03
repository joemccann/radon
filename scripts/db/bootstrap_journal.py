#!/usr/bin/env python3
"""Phase-3 one-shot: copy data/trade_log.json into the Turso `journal` table.

Mirrors scripts/db/bootstrap_menthorq.py. Idempotent (ON CONFLICT DO UPDATE).
Safe to re-run any number of times.

Why this exists:
    journal_sync (live IB fills) and journal_rehydrate (Flex Query, ≤365d)
    only write rows for trades they discover after deploy. Pre-existing rows
    in trade_log.json never make it to the DB unless we explicitly seed them.
    Without this, the /api/journal route falls through to disk forever and
    the DB is permanently empty.

Stable trade_id strategy (must match runtime writers):
    1. ib_exec_id when present (the field journal_sync / rehydrate emit).
    2. Else legacy fingerprint  ``ticker|date|structure|id`` so older rows
       imported before ib_exec_id existed still get a deterministic key.

filled_at:
    Pulled from ``filled_at`` if present, else ``date``, so the route's
    ORDER BY filled_at DESC sorts correctly.

Usage:
    python3 -m scripts.db.bootstrap_journal
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(PROJECT_ROOT / "web" / ".env")

from db.writer import upsert_journal_entry  # noqa: E402

TRADE_LOG_PATH = PROJECT_ROOT / "data" / "trade_log.json"


def _stable_trade_id(trade: dict) -> str:
    exec_id = trade.get("ib_exec_id")
    if exec_id:
        return str(exec_id)
    ticker = trade.get("ticker", "")
    date = trade.get("date") or trade.get("close_date") or ""
    structure = trade.get("structure", "")
    legacy_id = trade.get("id", "")
    return f"{ticker}|{date}|{structure}|{legacy_id}"


def _filled_at(trade: dict) -> str | None:
    return trade.get("filled_at") or trade.get("date") or trade.get("close_date")


def main() -> int:
    if not TRADE_LOG_PATH.exists():
        print(f"[bootstrap_journal] no trade log at {TRADE_LOG_PATH}", file=sys.stderr)
        return 0

    payload = json.loads(TRADE_LOG_PATH.read_text(encoding="utf-8"))
    trades = payload.get("trades") if isinstance(payload, dict) else payload
    if not isinstance(trades, list):
        print(f"[bootstrap_journal] unexpected shape: {type(trades).__name__}", file=sys.stderr)
        return 1

    print(f"[bootstrap_journal] found {len(trades)} trades")

    written = 0
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        trade_id = _stable_trade_id(trade)
        if not trade_id:
            continue
        upsert_journal_entry(trade_id, trade, filled_at=_filled_at(trade))
        written += 1

    print(f"[bootstrap_journal] upserted {written} journal rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
