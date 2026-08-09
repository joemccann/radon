"""Order-event audit-trail SQL shared by the sync writer and the FastAPI process.

No libsql import — safe to import under ``scripts/api`` (the AST lint
``test_no_sync_libsql_in_api`` bans ``db.client`` / ``db.writer`` there).
Mirrors the ``db.service_health_sql`` precedent so the row shape stays
single-source between ``db.writer.append_order_event`` (hrana via
``db.hrana_http``) and ``api.order_audit.record_order_event`` (hrana via
``api.db_http``).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

ORDER_EVENT_TYPES = ("submitted", "rejected", "cancelled", "modified", "indeterminate")

ORDER_EVENT_INSERT_SQL = """
INSERT INTO order_events
  (event_type, order_ref, order_id, perm_id, symbol, action,
   quantity, limit_price, status, detail, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def scrub_order_event_detail(detail: Any) -> Any:
    """Drop any key that names an account — audit rows must never carry
    account numbers (R-022 scrub requirement)."""
    if isinstance(detail, dict):
        return {
            key: scrub_order_event_detail(value)
            for key, value in detail.items()
            if "account" not in key.lower()
        }
    if isinstance(detail, list):
        return [scrub_order_event_detail(item) for item in detail]
    return detail


def order_event_args(
    event_type: str,
    *,
    order_ref: Optional[str] = None,
    order_id: Optional[int] = None,
    perm_id: Optional[int] = None,
    symbol: Optional[str] = None,
    action: Optional[str] = None,
    quantity: Optional[float] = None,
    limit_price: Optional[float] = None,
    status: Optional[str] = None,
    detail: Optional[dict[str, Any]] = None,
    created_at: Optional[str] = None,
) -> tuple:
    """Positional args for ``ORDER_EVENT_INSERT_SQL``, scrubbed and stamped."""
    if event_type not in ORDER_EVENT_TYPES:
        raise ValueError(f"unknown order event type: {event_type!r}")
    return (
        event_type,
        order_ref,
        order_id,
        perm_id,
        symbol,
        action,
        float(quantity) if quantity is not None else None,
        float(limit_price) if limit_price is not None else None,
        status,
        json.dumps(scrub_order_event_detail(detail)) if detail is not None else None,
        created_at
        or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )
