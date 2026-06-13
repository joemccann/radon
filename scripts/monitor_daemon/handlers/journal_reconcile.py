#!/usr/bin/env python3
"""
Journal Reconcile Handler — Daily cross-check of executed_orders vs journal.

The JRN-01 root cause: journal_sync._dual_write caught Hrana/libsql
``stream-not-found`` exceptions, logged them, and returned — so the disk
(trade_log.json) write succeeded while the Turso journal row was silently
dropped. On every subsequent pass _fills_to_entries deduped against the
seen_ids from disk, so the gap became permanent.

This handler runs once daily, compares executed_orders with the journal
table over a bounded look-back window, and alerts when exec_ids exist in
executed_orders but have no matching journal row.

Key design decisions (from rca.reconcile_design):
  - Pure Turso read — does NOT require IB Gateway.
  - Heartbeats ok on EVERY cycle, even zero-gap runs.
  - Raises on retryable DB errors so BaseHandler does not latch last_run.
  - State reflects THIS writer's health; gaps are logged and alerted, not
    written as error state.
  - Never auto-backfills — ALERT-ONLY (feedback_ib_auto_recovery_conservative).
  - False-positive avoidance: BAG combo parents, fuzzy date window, exec_id
    part-set matching.

Registration: see web/lib/serviceHealthWindows.ts and
scripts/watchdog/services.py — both MUST carry "journal-reconcile" as a
daily scheduled entry with requires_ib=false.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from .base import BaseHandler

logger = logging.getLogger(__name__)

# Run once per day.  24h in seconds.
RECONCILE_INTERVAL = 86_400

# Look back this many calendar days when scanning executed_orders.
RECONCILE_WINDOW_DAYS = 7

# Maximum gap rows to include in the detail payload (keeps service_health
# rows compact; the full list is always logged).
_MAX_GAP_DETAIL = 5


def _iso_date(dt_str: str) -> str:
    """Normalise a datetime string to YYYY-MM-DD. Returns '' on failure."""
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except Exception:
        return ""


def _is_bag_combo_parent(payload: dict[str, Any]) -> bool:
    """True when the executed_orders payload represents a BAG (combo) parent
    execution rather than a real per-leg fill.

    BAG parents have ``secType = "BAG"`` or the symbol field contains
    commas (leg symbols).  We also accept execId dot-count as a
    supplementary signal but never as the sole classifier (the RCA doc
    noted single-leg exec_ids like "0002920b.6a26c483.01.01" also have
    three dots).
    """
    contract = payload.get("contract") or {}
    sec_type = contract.get("secType") or payload.get("secType") or ""
    if str(sec_type).upper() == "BAG":
        return True
    symbol = str(payload.get("symbol") or contract.get("symbol") or "")
    return "," in symbol


def _journal_exec_id_parts(exec_id_raw: Any) -> set[str]:
    """Expand a journal ``ib_exec_id`` value into its constituent part-set.

    journal_rehydrate.py can join multiple per-fill exec_ids with '+' into
    a single composite ``ib_exec_id`` value, e.g. "FILL-A+FILL-B".
    An executed_orders exec_id is covered if it appears in the part-set.
    """
    s = str(exec_id_raw or "").strip()
    if not s:
        return set()
    parts: set[str] = set()
    for part in s.split("+"):
        p = part.strip()
        if p:
            parts.add(p)
    return parts


def _build_journal_coverage(
    db: Any,
    since_date: str,
) -> dict[str, set[str]]:
    """Read journal rows since ``since_date`` and return two lookup structures.

    Returns a dict with two keys:
      "exec_ids": set[str] — every individual exec_id part that appears in
          any journal row's ib_exec_id field over the window.
      "contract_dates": set[tuple] — (ticker, strike_str, right, expiry_norm,
          date_str) tuples for the per-contract-date fallback.
    """
    cursor = db.execute(
        """
        SELECT payload, filled_at, written_at
        FROM journal
        WHERE filled_at >= ?
        ORDER BY filled_at ASC
        """,
        (since_date,),
    )
    rows = cursor.fetchall()

    exec_ids: set[str] = set()
    contract_dates: set[tuple] = set()

    for row in rows:
        payload_raw = row[0]
        filled_at = row[1] or ""

        try:
            payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw or {}
        except Exception:
            payload = {}

        ib_exec_id = payload.get("ib_exec_id") or ""
        for part in _journal_exec_id_parts(ib_exec_id):
            exec_ids.add(part)

        # Build the per-contract-date fallback key.  Normalise expiry to
        # YYYYMMDD (compact IB form stored in journal rows).
        ticker = str(payload.get("ticker") or payload.get("symbol") or "").upper()
        strike = str(payload.get("strike") or "")
        right = str(payload.get("right") or "")
        expiry = str(payload.get("expiry") or "")
        date_str = _iso_date(filled_at) if filled_at else str(payload.get("date") or "")

        if ticker and date_str:
            contract_dates.add((ticker, strike, right, expiry, date_str))

    return {"exec_ids": exec_ids, "contract_dates": contract_dates}


def _executed_orders_in_window(db: Any, since_ts: str) -> list[dict[str, Any]]:
    """Fetch executed_orders rows with fill_time >= since_ts."""
    cursor = db.execute(
        """
        SELECT exec_id, perm_id, payload, fill_time, recorded_at
        FROM executed_orders
        WHERE fill_time >= ?
        ORDER BY fill_time ASC
        """,
        (since_ts,),
    )
    rows = cursor.fetchall()

    result: list[dict[str, Any]] = []
    for row in rows:
        exec_id = row[0]
        payload_raw = row[2]
        fill_time = row[3] or ""

        try:
            payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw or {}
        except Exception:
            payload = {}

        result.append(
            {
                "exec_id": exec_id,
                "fill_time": fill_time,
                "payload": payload,
            }
        )
    return result


def _extract_contract_key(payload: dict[str, Any]) -> tuple[str, str, str, str]:
    """Return (ticker, strike_str, right, expiry) from an executed_orders payload."""
    contract = payload.get("contract") or {}
    ticker = str(payload.get("symbol") or contract.get("symbol") or "").upper()
    strike = str(contract.get("strike") or payload.get("strike") or "")
    right = str(contract.get("right") or payload.get("right") or "")
    # expiry may be ISO or compact — normalise to compact YYYYMMDD for comparison
    expiry_raw = str(contract.get("expiry") or contract.get("lastTradeDateOrContractMonth") or "")
    # compact form: strip dashes if ISO
    expiry = expiry_raw.replace("-", "") if len(expiry_raw) == 10 and "-" in expiry_raw else expiry_raw
    return ticker, strike, right, expiry


def _has_nearby_journal_row(
    exec_item: dict[str, Any],
    contract_dates: set[tuple],
) -> bool:
    """Fallback coverage check: any journal row for the same contract within
    ±1 calendar day of the fill date passes, because journal_rehydrate groups
    fills by first_time and can place the row on a neighboring date.
    """
    ticker, strike, right, expiry = _extract_contract_key(exec_item["payload"])
    if not ticker:
        return False

    fill_date_str = _iso_date(exec_item["fill_time"])
    if not fill_date_str:
        return False

    try:
        fill_date = datetime.strptime(fill_date_str, "%Y-%m-%d").date()
    except ValueError:
        return False

    for offset in (-1, 0, 1):
        candidate = (fill_date + timedelta(days=offset)).strftime("%Y-%m-%d")
        if (ticker, strike, right, expiry, candidate) in contract_dates:
            return True
    return False


def _find_gaps(
    exec_items: list[dict[str, Any]],
    coverage: dict[str, set[str]],
) -> list[dict[str, Any]]:
    """Return executed_orders items that have no journal coverage.

    A gap is an executed_orders row whose exec_id:
      1. Is NOT in the journal exec_id part-set (exact match fails), AND
      2. Does NOT appear in the ±1-day per-contract fallback set.

    BAG combo parents are always skipped (they have no journal row by design).
    """
    exec_ids = coverage["exec_ids"]
    contract_dates = coverage["contract_dates"]

    gaps: list[dict[str, Any]] = []
    for item in exec_items:
        if _is_bag_combo_parent(item["payload"]):
            continue
        if item["exec_id"] in exec_ids:
            continue
        # Fallback: any journal row for the same contract within ±1 day
        if _has_nearby_journal_row(item, contract_dates):
            continue
        gaps.append(item)

    return gaps


class JournalReconcileHandler(BaseHandler):
    """Daily cross-check of executed_orders versus the journal table.

    Detects fills recorded in executed_orders that never made it into the
    journal — the silent drop symptom from JRN-01 (Hrana stream-not-found
    exceptions in journal_sync._dual_write).

    Alert-only: never writes to the journal itself.
    """

    name = "journal_reconcile"
    interval_seconds = RECONCILE_INTERVAL
    requires_market_hours = False
    service_name = "journal-reconcile"

    def execute(self) -> dict[str, Any]:
        db = self._open_db()
        if db is None:
            raise RuntimeError("journal-reconcile: DB unavailable")

        window_start_ts, window_start_date = self._window_boundaries()
        exec_items = _executed_orders_in_window(db, window_start_ts)
        coverage = _build_journal_coverage(db, window_start_date)
        gaps = _find_gaps(exec_items, coverage)

        if gaps:
            self._alert_on_gaps(gaps)

        return self._build_result(exec_items, gaps)

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _open_db() -> Any:
        try:
            from db.client import get_db  # noqa: PLC0415 — lazy; libsql optional
            return get_db()
        except Exception as exc:  # noqa: BLE001
            logger.warning("journal-reconcile: DB unavailable: %s", exc)
            return None

    @staticmethod
    def _window_boundaries() -> tuple[str, str]:
        """Return (iso_timestamp, date_string) for the start of the look-back window."""
        now = datetime.now(timezone.utc)
        since = now - timedelta(days=RECONCILE_WINDOW_DAYS)
        # Turso fill_time is stored as an ISO string; use the same format.
        since_ts = since.isoformat().replace("+00:00", "Z")
        since_date = since.strftime("%Y-%m-%d")
        return since_ts, since_date

    @staticmethod
    def _alert_on_gaps(gaps: list[dict[str, Any]]) -> None:
        """Log discovered gaps prominently (journald + the nightly DUR-12 archive).

        The operator-facing alert is the service_health ``error`` state this
        handler returns via ``result['error']`` (BaseHandler's swallowed-failure
        convention), which the watchdog dispatches through the normal
        grouping/Pushover path and which /admin + DUR-11 history surface. There
        is NO direct-notify primitive for monitor-daemon handlers — the old
        ``from utils.notify import notify`` referenced a module that does not
        exist, so every gap alert was silently swallowed at the import.
        """
        count = len(gaps)
        sample = [g["exec_id"] for g in gaps[:_MAX_GAP_DETAIL]]
        logger.warning(
            "journal-reconcile: %d exec_id(s) in executed_orders have no journal row: %s%s "
            "(repair with scripts/backfill_journal_from_executed_orders.py)",
            count,
            sample,
            " (truncated)" if count > _MAX_GAP_DETAIL else "",
        )

    @staticmethod
    def _build_result(
        exec_items: list[dict[str, Any]],
        gaps: list[dict[str, Any]],
    ) -> dict[str, Any]:
        count = len(gaps)
        result: dict[str, Any] = {
            "executed_orders_scanned": len(exec_items),
            "gaps_found": count,
            "window_days": RECONCILE_WINDOW_DAYS,
        }
        if gaps:
            sample = [g["exec_id"] for g in gaps[:_MAX_GAP_DETAIL]]
            result["gap_exec_ids"] = sample
            if count > _MAX_GAP_DETAIL:
                result["gap_exec_ids_truncated"] = True
            # Surface as service_health state=error via BaseHandler's
            # swallowed-failure convention: the handler RAN fine (run status
            # stays ok, last_run latches so it re-checks tomorrow), but the
            # journal DATA is incomplete, so the row goes error with the gap
            # list. /admin, the banner, DUR-11 history, and the watchdog all
            # see it. State clears to ok automatically once the gaps are
            # backfilled.
            result["error"] = (
                f"{count} executed fill(s) missing from journal: {sample}"
                + (" (truncated)" if count > _MAX_GAP_DETAIL else "")
            )
        return result
