"""Per-scan Turso mirror + service_health heartbeat (DUR-01).

The FastAPI-side mirror (``server._maybe_dual_write_to_db``) is gone: its
synchronous libsql writes starved the single uvicorn event loop even from a
worker thread, because libsql holds the GIL during a hung commit (see
``feedback_no_sync_libsql_on_fastapi_event_loop`` and commits c9e518a /
2647c93). Each scan subprocess mirrors its own snapshot instead — the
subprocess has its own GIL, so a hung Turso write can only stall that scan,
never the API. The JSON cache in ``data/`` stays authoritative and is written
by the caller before this mirror runs; a failed Turso write degrades only the
mirror.

Division of labor vs ``db.service_cycle`` (DUR-14)
==================================================

This module stays the single chokepoint for MIRROR-FED scans — every
service in :data:`SNAPSHOT_UPSERTS`. It owns the snapshot upsert AND the
service_health heartbeat together and NEVER raises, because the mirror is
best-effort by definition.

STANDALONE writers (cri_scan, gex_scan, ib_sync, ib_orders,
cta_sync_service, llm_token_index, gamma_rotation_gap,
fetch_analyst_ratings) own their snapshot writes inline and wrap them in
``db.service_cycle.service_cycle`` instead, which heartbeats ok on every
clean exit and writes error + a ~5-min retry embargo BEFORE re-raising.
Don't add a standalone writer here, and don't hand a mirror-fed scan a
service_cycle — one chokepoint per writer family.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Optional

# service name → db.writer upsert for the snapshot table. ``None`` means the
# scan has no snapshot table — the file cache is canonical and only the
# service_health row is written, so the staleness banner can still notice a
# scheduled scan going silent.
SNAPSHOT_UPSERTS: dict[str, Optional[str]] = {
    "vcg-scan": "upsert_vcg_snapshot",
    "scanner": "upsert_scanner_snapshot",
    "discover": "upsert_discover_snapshot",
    "flow-analysis": "upsert_flow_analysis_snapshot",
    "performance": "upsert_performance_snapshot",
    "oi-changes": "upsert_oi_changes",
    "leap-scan": None,
    "garch-scan": None,
    "theta-harvester": "upsert_theta_harvester_snapshot",
    "strength-confirmation": "upsert_strength_confirmation_snapshot",
    "breadth-scan": "upsert_breadth_snapshot",
}

# breadth_snapshots is (date, taken_at)-keyed like cri_snapshots; its upsert
# takes (date_str, taken_at, payload) instead of the (scan_time, payload)
# arity every other mirror-fed upsert uses.
DATE_KEYED_UPSERTS = {"upsert_breadth_snapshot"}


def mirror_scan_snapshot(service: str, payload: dict, taken_at: Optional[str] = None) -> None:
    """Best-effort: upsert the scan's Turso snapshot + heartbeat service_health.

    Never raises past the unknown-service guard — the row state reflects THIS
    writer's own outcome (``ok`` on a clean mirror, ``error`` with the detail
    when the snapshot write fails), and a failed Turso write must never crash
    the scan that produced the data.
    """
    if service not in SNAPSHOT_UPSERTS:
        raise ValueError(f"unknown scan service: {service}")
    scan_iso = taken_at or _payload_timestamp(service, payload)
    try:
        from db import writer
    except ImportError:  # pragma: no cover — DB layer optional in stripped envs
        return
    try:
        writer.ensure_no_replica_for_writers()
        upsert_name = SNAPSHOT_UPSERTS[service]
        if upsert_name in DATE_KEYED_UPSERTS:
            getattr(writer, upsert_name)(
                _payload_session_date(payload) or _today_et_str(),
                scan_iso or _today_et_str(),
                payload,
            )
        elif upsert_name:
            getattr(writer, upsert_name)(scan_iso or _today_et_str(), payload)
        writer.record_service_health(service, "ok", finished_at=scan_iso)
    except Exception as exc:  # noqa: BLE001 — best-effort mirror
        print(f"[{service}] db dual-write non-fatal: {exc}", file=sys.stderr)
        try:
            writer.record_service_health(
                service, "error", finished_at=scan_iso, error={"detail": str(exc)},
            )
        except Exception:
            pass


def _today_et_str() -> str:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


def _payload_session_date(payload: dict) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    latest = payload.get("latest")
    if isinstance(latest, dict):
        session_date = latest.get("session_date")
        if isinstance(session_date, str) and session_date:
            return session_date
    return None


def _payload_timestamp(service: str, payload: dict) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    if service == "discover":
        return payload.get("discovery_time") or payload.get("scan_time")
    return payload.get("scan_time")
