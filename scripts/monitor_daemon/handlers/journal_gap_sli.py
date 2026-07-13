#!/usr/bin/env python3
"""
Journal Gap SLI — Continuous executed_orders vs journal coverage check.

Daily ``JournalReconcileHandler`` (``journal-reconcile``) latches a snapshot
once per day. That is correct for the long-horizon audit but leaves a
multi-hour blind spot for silent ingest drops (JRN-01 class) and can leave
a latched ``error`` row stale after out-of-band repair until the next daily
pass (JRN-03 heal covers write paths only).

This handler re-runs the same pure-Turso gap detection on a continuous
cadence and writes a dedicated ``journal-gap-sli`` service_health row with
structured detail:

  - missing_exec_id_count
  - gap_exec_ids (sample)
  - window_days / min_age_minutes / executed_orders_scanned

Alert-only: never writes journal rows. Fresh fills younger than
``GAP_SLI_MIN_AGE_MINUTES`` are ignored so a live fill still racing
``journal_sync`` does not inflate the SLI.

Registration: web/lib/serviceHealthWindows.ts + scripts/watchdog/services.py
(continuous bucket, requires_ib=false).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .base import BaseHandler
from .journal_reconcile import (
    RECONCILE_WINDOW_DAYS,
    _MAX_GAP_DETAIL,
    _build_journal_coverage,
    _executed_orders_in_window,
    _find_gaps,
)

logger = logging.getLogger(__name__)

SERVICE_NAME = "journal-gap-sli"

# Continuous cadence — same ballpark as journal_sync / exit-orders.
GAP_SLI_INTERVAL_SECONDS = 300

# Rolling window matches daily reconcile so both sensors see the same set.
GAP_SLI_WINDOW_DAYS = RECONCILE_WINDOW_DAYS

# Ignore fills younger than this so journal_sync (5m) has time to land.
GAP_SLI_MIN_AGE_MINUTES = 10


def _fill_is_old_enough(
    item: dict[str, Any],
    *,
    cutoff: datetime,
) -> bool:
    """True when fill_time is parseable and at or before ``cutoff``."""
    fill_raw = item.get("fill_time") or ""
    if not fill_raw:
        return True
    try:
        fill_dt = datetime.fromisoformat(str(fill_raw).replace("Z", "+00:00"))
    except Exception:
        return True
    if fill_dt.tzinfo is None:
        fill_dt = fill_dt.replace(tzinfo=timezone.utc)
    return fill_dt <= cutoff


def scan_journal_gaps(
    db: Any,
    *,
    window_days: int = GAP_SLI_WINDOW_DAYS,
    min_age_minutes: int = 0,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Snapshot of executed_orders rows missing journal coverage.

    Pure Turso reads. Returns a dict with:
      missing_exec_id_count, gaps, executed_orders_scanned, window_days,
      min_age_minutes, window_start.
    """
    now_utc = now or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    since = now_utc - timedelta(days=window_days)
    since_ts = since.isoformat().replace("+00:00", "Z")
    since_date = since.strftime("%Y-%m-%d")

    exec_items = _executed_orders_in_window(db, since_ts)
    coverage = _build_journal_coverage(db, since_date)
    gaps = _find_gaps(exec_items, coverage)

    if min_age_minutes > 0:
        cutoff = now_utc - timedelta(minutes=min_age_minutes)
        gaps = [g for g in gaps if _fill_is_old_enough(g, cutoff=cutoff)]

    return {
        "missing_exec_id_count": len(gaps),
        "gaps": gaps,
        "executed_orders_scanned": len(exec_items),
        "window_days": window_days,
        "min_age_minutes": min_age_minutes,
        "window_start": since_ts,
    }


def _sli_detail(snap: dict[str, Any]) -> dict[str, Any]:
    """Compact structured payload for service_health.last_error."""
    count = int(snap["missing_exec_id_count"])
    gaps = snap.get("gaps") or []
    sample = [g["exec_id"] for g in gaps[:_MAX_GAP_DETAIL]]
    detail: dict[str, Any] = {
        "missing_exec_id_count": count,
        "window_days": snap["window_days"],
        "min_age_minutes": snap["min_age_minutes"],
        "executed_orders_scanned": snap["executed_orders_scanned"],
        "window_start": snap["window_start"],
    }
    if sample:
        detail["gap_exec_ids"] = sample
        if count > _MAX_GAP_DETAIL:
            detail["gap_exec_ids_truncated"] = True
    return detail


class JournalGapSliHandler(BaseHandler):
    """Continuous executed_orders vs journal gap SLI.

    Alert-only. Writes ``journal-gap-sli`` service_health every cycle with
    structured missing_exec_id_count detail so the watchdog /admin surface
    sees gaps within minutes rather than waiting for the daily reconcile.
    """

    name = "journal_gap_sli"
    interval_seconds = GAP_SLI_INTERVAL_SECONDS
    requires_market_hours = False
    service_name = SERVICE_NAME

    def execute(self) -> dict[str, Any]:
        db = self._open_db()
        if db is None:
            raise RuntimeError("journal-gap-sli: DB unavailable")

        snap = scan_journal_gaps(
            db,
            window_days=GAP_SLI_WINDOW_DAYS,
            min_age_minutes=GAP_SLI_MIN_AGE_MINUTES,
            now=self._now_utc(),
        )
        detail = _sli_detail(snap)
        count = detail["missing_exec_id_count"]

        state = "error" if count else "ok"
        self.record_cycle_health(state, error=detail)

        result: dict[str, Any] = {
            "missing_exec_id_count": count,
            "executed_orders_scanned": detail["executed_orders_scanned"],
            "window_days": detail["window_days"],
            "min_age_minutes": detail["min_age_minutes"],
        }
        if count:
            sample = detail.get("gap_exec_ids") or []
            result["gap_exec_ids"] = sample
            if detail.get("gap_exec_ids_truncated"):
                result["gap_exec_ids_truncated"] = True
            # Swallowed-failure convention for BaseHandler if structural
            # heartbeat ever runs; we already recorded structured detail.
            result["error"] = (
                f"{count} executed fill(s) missing from journal: {sample}"
                + (" (truncated)" if detail.get("gap_exec_ids_truncated") else "")
            )
            logger.warning(
                "journal-gap-sli: missing_exec_id_count=%d sample=%s "
                "(repair with scripts/backfill_journal_from_executed_orders.py)",
                count,
                sample,
            )
        else:
            logger.debug(
                "journal-gap-sli: missing_exec_id_count=0 scanned=%d",
                detail["executed_orders_scanned"],
            )
        return result

    @staticmethod
    def _open_db() -> Any:
        try:
            from db.client import get_db  # noqa: PLC0415 — lazy; libsql optional

            return get_db()
        except Exception as exc:  # noqa: BLE001
            logger.warning("journal-gap-sli: DB unavailable: %s", exc)
            return None

    @staticmethod
    def _now_utc() -> datetime:
        return datetime.now(timezone.utc)
