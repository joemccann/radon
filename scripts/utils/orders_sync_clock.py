"""Orders snapshot clock.

``last_sync`` is when the book was last confirmed, not the newest fill.
An empty working book has no ``open_orders.updated_at``. The fill time
then ages forever and /orders paints STALE. The orders-sync ok heartbeat
is that confirmation. A warn or error row stamps ``finished_at`` without
rewriting the book, so only state ``ok`` counts. A timestamp with no
zone is not an instant.
"""

from __future__ import annotations

import re
from datetime import datetime

_ABSOLUTE = re.compile(r"(?:Z|[+-]\d{2}:\d{2})$")


def absolute_timestamp(value: object) -> str | None:
    if not isinstance(value, str) or not value or not _ABSOLUTE.search(value):
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value


def resolve_orders_last_sync(
    *,
    health_state: str | None,
    health_finished_at: str | None,
    latest_open_updated_at: str,
    latest_fill_time: str,
) -> str:
    open_at = absolute_timestamp(latest_open_updated_at)
    synced_at = (
        absolute_timestamp(health_finished_at) if health_state == "ok" else None
    )
    candidates = [item for item in (synced_at, open_at) if item]
    if not candidates:
        return latest_fill_time
    return max(
        candidates,
        key=lambda item: datetime.fromisoformat(item.replace("Z", "+00:00")),
    )
