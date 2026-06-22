"""Persistence for simulated paper fills.

Writes to the `paper_fills` table (migration 0021), keeping the IB-rehydrated
`journal` table untouched. Mirrors the helper style of
``scripts/db/writer.py``: take a ready payload, write one row, commit.

This module performs DB I/O and is therefore NOT exercised by the unit tests
(which stay offline per the house rules — the DB client refuses real
connections under PYTEST_CURRENT_TEST by design). It is the integration glue
between the pure matcher/fills core and the paper book.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

DEFAULT_ACCOUNT = "PAPER"
SHADOW_SOURCE = "shadow"


@dataclass(frozen=True)
class PaperFill:
    """A simulated fill ready to persist."""

    fill_id: str
    ticker: str
    side: str
    order_type: str
    quantity: float
    fill_price: float
    payload: dict[str, Any]
    account: str = DEFAULT_ACCOUNT
    source: str = SHADOW_SOURCE
    filled_at: Optional[str] = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _get_db():
    try:
        from db.client import get_db  # type: ignore[no-redef]
    except ImportError:  # pragma: no cover - import path fallback
        from scripts.db.client import get_db  # type: ignore[no-redef]
    return get_db()


def record_paper_fill(fill: PaperFill) -> None:
    """Persist one simulated fill, idempotent on ``fill_id``."""
    db = _get_db()
    filled_at = fill.filled_at or _now_iso()
    db.execute(
        """
        INSERT OR REPLACE INTO paper_fills (
          fill_id, account, source, ticker, side, order_type,
          quantity, fill_price, filled_at, payload, written_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fill.fill_id,
            fill.account,
            fill.source,
            fill.ticker,
            fill.side,
            fill.order_type,
            fill.quantity,
            fill.fill_price,
            filled_at,
            json.dumps(fill.payload),
            _now_iso(),
        ),
    )
    db.commit()
