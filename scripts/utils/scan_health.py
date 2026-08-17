"""Explicit service_health telemetry for degraded UW universe scans.

A budget-blocked or coverage-failed scan used to exit 0 with nothing —
no cache write, no mirror, no service_health row at all (REL-036 / R-070).
Nothing distinguished "quota-blocked, self-clears at 20:00 ET" from
"scanner broken". The scanners now write one error row whose ``reason``
carries that distinction and, for budget blocks, a ``next_attempt_at``
deadline the watchdog's error bucket embargoes on (same convention as
``utils.uw_embargo``).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

from utils.uw_budget import ET, RESET_HOUR_ET

SCAN_STATUS_BUDGET_BLOCKED = "uw-budget-blocked"
SCAN_STATUS_COVERAGE_FAILED = "uw-coverage-failed"


def _utc_iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def next_quota_reset_iso(now: Optional[datetime] = None) -> str:
    """Next 20:00 ET UW quota reset as a UTC ISO timestamp."""
    if now is None:
        now = datetime.now(timezone.utc)
    local = now.astimezone(ET)
    reset = local.replace(hour=RESET_HOUR_ET, minute=0, second=0, microsecond=0)
    if local >= reset:
        reset += timedelta(days=1)
    return _utc_iso(reset)


def record_scan_degraded(
    service: str,
    reason: str,
    message: str,
    *,
    next_attempt_at: Optional[str] = None,
) -> None:
    """Write one distinguishable service_health error row. Never raises."""
    error: dict[str, str] = {"message": message, "reason": reason}
    if next_attempt_at:
        error["next_attempt_at"] = next_attempt_at
    try:
        from db import writer

        writer.ensure_no_replica_for_writers()
        writer.record_service_health(
            service,
            "error",
            finished_at=_utc_iso(datetime.now(timezone.utc)),
            error=error,
        )
    except Exception as exc:  # noqa: BLE001 — telemetry never masks the scan
        print(f"[{service}] degraded-scan heartbeat non-fatal: {exc}", file=sys.stderr)
