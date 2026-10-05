"""Best-effort order audit trail for the FastAPI process (REL-019 / R-022).

Records one ``order_events`` row + one INFO log line for every order
outcome: successful submit, IB rejection, cancel, modify, and the
indeterminate gateway-restart case. Keyed by orderRef/permId so an
incident can be reconstructed without the order having filled.

Transport is the bounded hrana HTTP pipeline (``api.db_http``) — this
process never touches sync libsql (``test_no_sync_libsql_in_api``). The
sync-writer twin is ``db.writer.append_order_event``; the SQL and row
shape are shared via ``db.order_events_sql``.

Best-effort contract: an audit failure must NEVER change the HTTP
response of a live-money order — every failure is swallowed into a
WARNING log. Rejected outcomes also enqueue the existing P2 daily digest
(R-025 / REL-310), independently of the audit DB. Indeterminate attempts
never enqueue a rejection. This path does not send notifications.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from api.db_http import hrana_execute
from db.order_events_sql import ORDER_EVENT_INSERT_SQL, order_event_args
from credential_redaction import scrub_credential_text
from watchdog.check import CheckOutcome
from watchdog.notify import _enqueue_digest

logger = logging.getLogger("radon.order_audit")


async def record_order_event(
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
) -> bool:
    """Log + append one audit row. Never raises."""
    logger.info(
        "order_event %s orderRef=%s orderId=%s permId=%s symbol=%s action=%s qty=%s status=%s",
        event_type, order_ref, order_id, perm_id, symbol, action, quantity, status,
    )
    if event_type == "rejected" and not (detail or {}).get("indeterminate"):
        # R-025 / REL-310: persist independently of the initiating browser and
        # the audit DB. Enqueue only; the existing daily watchdog owns delivery.
        code = (detail or {}).get("ib_error_code")
        code = code if type(code) is int else "unknown"
        message = scrub_credential_text(
            f"{str(symbol or '?')[:32]} {str(action or '?')[:8]} {str(quantity)[:24]}: "
            f"order rejected (IB {code}; orderRef={str(order_ref or '?')[:128]}; "
            f"orderId={str(order_id if order_id is not None else '?')[:24]})"
        )
        await asyncio.to_thread(_enqueue_digest, CheckOutcome(
            service="order-rejections", kind="order-rejected", status="error", severity="P2",
            fired=True, message=message, consecutive_failures=1,
            now=datetime.now(timezone.utc),
        ))
    try:
        args = order_event_args(
            event_type,
            order_ref=order_ref,
            order_id=order_id,
            perm_id=perm_id,
            symbol=symbol,
            action=action,
            quantity=quantity,
            limit_price=limit_price,
            status=status,
            detail=detail,
        )
        await asyncio.to_thread(hrana_execute, ORDER_EVENT_INSERT_SQL, args)
        return True
    except Exception as exc:
        logger.warning(
            "order_events append failed (%s orderRef=%s orderId=%s): %s",
            event_type, order_ref, order_id, exc,
        )
        return False
