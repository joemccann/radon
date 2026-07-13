"""Single source of truth for the ``service_health`` upsert statement.

Shared by:
  - ``db.writer.record_service_health`` → ``db.hrana_http.write_service_health_http``
    (monitor daemon / service_cycle / scan_mirror — bounded socket timeout)
  - FastAPI heal path (``scripts/api/ib_gateway.py``) over ``api.db_http``
  - Direct callers of ``write_service_health_http`` (ib_watchdog, host_metrics,
    watchdog.notify)

All production paths execute this SAME statement over bounded HTTP — never
sync libsql on the critical path. The API process must never import
``db.client`` / ``db.writer`` (see ``test_no_sync_libsql_in_api.py``).

This module must stay free of libsql / db.client imports so scripts/api can
import it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

SERVICE_HEALTH_UPSERT_SQL = """
INSERT INTO service_health (service, state, last_attempt_started_at, last_attempt_finished_at, last_error, updated_at)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(service) DO UPDATE SET
  state                    = excluded.state,
  last_attempt_started_at  = COALESCE(excluded.last_attempt_started_at, service_health.last_attempt_started_at),
  last_attempt_finished_at = COALESCE(excluded.last_attempt_finished_at, service_health.last_attempt_finished_at),
  last_error               = excluded.last_error,
  updated_at               = excluded.updated_at
"""


def service_health_upsert_args(
    service: str,
    state: str,
    *,
    started_at: Optional[str] = None,
    finished_at: Optional[str] = None,
    error: Optional[dict[str, Any]] = None,
) -> tuple:
    """Positional args for :data:`SERVICE_HEALTH_UPSERT_SQL`.

    ``state`` ∈ {'ok', 'syncing', 'error', 'paused'}. ``error`` is JSON-
    serialized exactly as ``record_service_health`` always has.
    """
    return (
        service,
        state,
        started_at,
        finished_at,
        json.dumps(error) if error else None,
        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )
