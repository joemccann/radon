"""Working-order snapshot filters.

orders-sync is RTH-gated. A DAY order that IB cancelled at the prior
regular-session close can linger in Turso overnight and still offer
Modify. Filter those ghosts on read.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def _et_calendar_date(value: datetime) -> str:
    return value.astimezone(ET).strftime("%Y-%m-%d")


def _parse_iso(value: str) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def is_prior_session_day_order(
    order: Mapping[str, Any],
    updated_at: Optional[str],
    *,
    now: Optional[datetime] = None,
) -> bool:
    """True when a DAY order's snapshot is from a previous ET calendar day."""
    if str(order.get("tif") or "").upper() != "DAY":
        return False
    stamped = _parse_iso(updated_at or "")
    if stamped is None:
        return False
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    return _et_calendar_date(stamped) < _et_calendar_date(clock)


def filter_working_open_orders(
    rows: Sequence[tuple[Mapping[str, Any], Optional[str]]],
    *,
    now: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """Keep open-order payloads that are still plausible working orders."""
    kept: list[dict[str, Any]] = []
    for order, updated_at in rows:
        if is_prior_session_day_order(order, updated_at, now=now):
            continue
        kept.append(dict(order))
    return kept


def working_order_missing_message(tif: Optional[str] = None) -> str:
    if str(tif or "").upper() == "DAY":
        return (
            "This DAY order is no longer working at IB. "
            "DAY orders end at the regular-session close."
        )
    return "Order is no longer working at IB. It may have filled or been cancelled."
