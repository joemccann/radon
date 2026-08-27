#!/usr/bin/env python3
"""Evening execution sweep — Monitor daemon handler (REL-012 / R-011).

Fills outside RTH + journal_sync's 15-min post-close grace (outsideRth
GTC fills, manual TWS trades, late busts/corrections) were invisible:
journal_sync's per-cycle IB session only covers the current session (a
fresh session next morning no longer returns yesterday's executions)
and the orders-sync loop that feeds ``executed_orders`` is market-hours
gated. journal_reconcile therefore had nothing to diff and the fill
stayed missing until a manual Flex rehydrate.

This handler runs once per ET trading day at 20:30 ET (after IB's
post-close processing) while the day's executions — including
after-hours ones — are still visible on the evening session:

  1. connects to IB (``client_id="auto"``) and pulls ``get_fills()``;
  2. imports any exec_id not yet in the journal via
     ``JournalSyncHandler.import_fills`` (the same dedupe/labelling/
     write machinery — never a copy);
  3. mirrors the executions into ``executed_orders`` via
     ``db.writer.upsert_executed_order`` (idempotent on execId) so
     journal_reconcile / journal-gap-sli can diff them;
  4. recovers CARRIED-OVER gaps from ``executed_orders`` (Turso), not
     from IB.

Step 4 exists because steps 1-3 only ever see the CURRENT day: a day
whose sweep never succeeded (dead gateway, daemon outage) leaves its
after-hours fills orphaned forever, since the next evening's session no
longer returns them. executed_orders still holds the durable payload, so
the gap is repairable without IB — otherwise journal-gap-sli pages every
cycle until a human runs backfill_journal_from_executed_orders.py by
hand (incident 20260813T140500Z: IWM perm 1805918121 filled 16:24:50 ET
on 2026-08-12, past journal_sync's 15-min post-close grace, while that
evening's sweep burned its 3-attempt budget on an unreachable gateway).

Failure handling follows the cash_flow_sync daily conventions: IB/DB
unavailability RAISES so ``BaseHandler.run()`` never latches
``last_run`` (feedback_dont_latch_last_run_on_soft_failure); each
failure sets a 5-min ``record_soft_failure`` embargo, capped at
MAX_SOFT_ATTEMPTS_PER_ET_DAY per ET day so a dead gateway can't be
hammered all evening.

Wired into monitor_daemon via scripts/monitor_daemon/run.py:create_daemon().
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from zoneinfo import ZoneInfo

from monitor_daemon.handlers import _throttle_backoff
from monitor_daemon.handlers.base import BaseHandler
from monitor_daemon.handlers.journal_sync import JournalSyncHandler
from clients.ib_client import IBClient, DEFAULT_HOST

try:
    from db.writer import upsert_executed_order  # type: ignore
except ImportError:  # pragma: no cover — DB layer optional in unit tests
    upsert_executed_order = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

DEFAULT_IB_PORT = 4001

# 24h floor for the daemon's bookkeeping; exact firing handled by is_due.
CHECK_INTERVAL = 24 * 60 * 60

ET = ZoneInfo("America/New_York")

# Daily fire time: 20:30 ET, after IB's post-close processing, while the
# evening session still exposes the day's executions. Late fire allowed
# any time after 20:30 ET if the handler hasn't run this ET day.
FIRE_HOUR_ET = 20
FIRE_MINUTE_ET = 30

# Soft-failure retry budget per ET day (mirrors cash_flow_sync): each
# failed attempt embargoes 5 min via record_soft_failure; after this
# many the next attempt waits for tomorrow's window.
MAX_SOFT_ATTEMPTS_PER_ET_DAY = 3

# Rolling window for carried-over gap recovery. Matches journal-gap-sli /
# journal-reconcile so the sweep repairs exactly what those sensors alert on.
GAP_RECOVERY_WINDOW_DAYS = 7


def _now_utc() -> datetime:
    """Indirection to make `datetime.now(tz=UTC)` patchable in tests."""
    return datetime.now(timezone.utc)


def _et_date(now_utc: datetime) -> str:
    return now_utc.astimezone(ET).strftime("%Y-%m-%d")


def _is_trading_day_et(now_utc: datetime) -> bool:
    """True iff the ET calendar day is a US equity trading day."""
    et_dt = now_utc.astimezone(ET)
    if et_dt.weekday() >= 5:
        return False
    try:
        from utils.market_calendar import load_holidays
    except Exception:
        return True
    holidays = load_holidays(et_dt.year)
    return et_dt.strftime("%Y-%m-%d") not in holidays


def _overlay_journal_realized_pnl(rows: List[Dict[str, Any]]) -> None:
    """Journal average-cost realized P&L over IB's drifted figure (advisory)."""
    try:
        from clients.journal_realized import overlay_journal_realized_pnl  # noqa: PLC0415

        # Bounded transport, paged read — see R-203.
        overlay_journal_realized_pnl(rows)
    except Exception as exc:  # noqa: BLE001
        logger.warning("evening_execution_sweep: realized P&L overlay skipped: %s", exc)


class EveningExecutionSweepHandler(BaseHandler):
    """Import the day's after-hours executions once per ET trading day."""

    name = "evening_execution_sweep"
    interval_seconds = CHECK_INTERVAL
    requires_market_hours = False
    service_name = "execution-sweep"

    def __init__(
        self,
        ib_port: int = DEFAULT_IB_PORT,
        client_id: "int | str" = "auto",
    ) -> None:
        super().__init__()
        self.ib_port = ib_port
        self.client_id = client_id
        self._backoff_state: Dict[str, Any] = _throttle_backoff.initial_state()

    # ------------------------------------------------------------------
    # State persistence — embargo state survives daemon restarts.
    # ------------------------------------------------------------------
    def get_state(self) -> Dict[str, Any]:
        state = super().get_state()
        state["backoff_state"] = dict(self._backoff_state)
        return state

    def set_state(self, state: Dict[str, Any]) -> None:
        super().set_state(state)
        raw = state.get("backoff_state")
        if isinstance(raw, dict):
            self._backoff_state = {
                "throttle_count": int(raw.get("throttle_count") or 0),
                "blocked_until": raw.get("blocked_until"),
                "soft_attempt_date": raw.get("soft_attempt_date"),
                "soft_attempts": int(raw.get("soft_attempts") or 0),
            }
        else:
            self._backoff_state = _throttle_backoff.initial_state()

    # ------------------------------------------------------------------
    # Cadence — once per ET trading day at/after 20:30 ET.
    # ------------------------------------------------------------------
    def is_due(self) -> bool:
        if not self._enabled:
            return False

        now_utc = _now_utc()

        if _throttle_backoff.is_blocked(self._backoff_state, now_utc=now_utc):
            return False

        if self._soft_budget_exhausted(now_utc):
            return False

        if not _is_trading_day_et(now_utc):
            return False

        et_now = now_utc.astimezone(ET)
        if (et_now.hour, et_now.minute) < (FIRE_HOUR_ET, FIRE_MINUTE_ET):
            return False

        # Never double-fire the same ET trading day; a late fire after a
        # daemon outage is fine — skipping the day defeats the sweep.
        if self.last_run is not None:
            if self.last_run.astimezone(ET).strftime("%Y-%m-%d") == et_now.strftime("%Y-%m-%d"):
                return False

        return True

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    def execute(self) -> Dict[str, Any]:
        """Sweep the evening session's executions into journal + executed_orders.

        Every retryable failure RAISES (soft failure — ``last_run`` not
        latched) after advancing the 5-min embargo, so the next daemon
        cycle retries on a measured cadence instead of burning the daily
        slot on a transient IB/DB error.
        """
        started_at = self._utc_now_iso()
        try:
            result = self._execute_inner()
        except Exception as exc:
            self._record_failure(started_at, str(exc))
            raise

        self._backoff_state = _throttle_backoff.record_success(self._backoff_state)
        self.record_cycle_health("ok", started_at=started_at)
        return result

    def _execute_inner(self) -> Dict[str, Any]:
        client = IBClient()
        try:
            client.connect(host=DEFAULT_HOST, port=self.ib_port, client_id=self.client_id)
            fills = client.get_fills()
            executed_rows = self._fetch_executed_rows(client)
        finally:
            try:
                client.disconnect()
            except Exception:  # noqa: BLE001 — disconnect must not mask the real error
                pass

        importer = JournalSyncHandler(ib_port=self.ib_port, client_id=self.client_id)
        journal_db = self._open_journal_db(importer)
        summary = importer.import_fills(fills, db=journal_db)
        mirrored = self._mirror_executed_orders(executed_rows)
        recovered = self._recover_carried_over_gaps(journal_db)

        result: Dict[str, Any] = {
            "status": "ok",
            "fills_seen": len(fills),
            "executed_mirrored": mirrored,
            "gaps_recovered": recovered,
            "timestamp": datetime.now().isoformat(),
        }
        result.update(summary)
        return result

    @staticmethod
    def _recover_carried_over_gaps(db: Any) -> int:
        """Close executed_orders-vs-journal gaps the IB session can't return.

        The evening session only exposes the CURRENT day's executions, so a
        day whose sweep never succeeded (dead gateway, daemon outage) leaves
        its after-hours fills permanently orphaned: the next sweep pulls a
        session that no longer contains them. ``executed_orders`` still holds
        the durable payload, so residual gaps are repaired from Turso using
        the same reconstruction the manual repair script uses — no second
        copy of the labelling logic, idempotent on exec_id.

        DB errors propagate: a failed recovery is a soft failure and the
        journal import that already happened is idempotent on retry.
        """
        if db is None:
            return 0
        from backfill_journal_from_executed_orders import (  # noqa: PLC0415 — lazy
            backfill,
        )

        actions = backfill(db, window_days=GAP_RECOVERY_WINDOW_DAYS, dry_run=False)
        return sum(1 for a in actions if a.get("status") == "inserted_from_eo")

    @staticmethod
    def _open_journal_db(importer: Optional[JournalSyncHandler] = None) -> Any:
        """Seam for tests: the journal DB handle import_fills dedupes against."""
        return (importer or JournalSyncHandler())._open_db()

    @staticmethod
    def _fetch_executed_rows(client: Any) -> List[Dict[str, Any]]:
        """Serialize the session's fills with the same shape orders-sync
        writes (reuses ib_orders.fetch_executed_orders — no copy)."""
        from ib_orders import fetch_executed_orders  # noqa: PLC0415 — lazy; heavy module

        return fetch_executed_orders(client)

    @staticmethod
    def _mirror_executed_orders(rows: List[Dict[str, Any]]) -> int:
        """Upsert executions into ``executed_orders`` (idempotent on execId).

        DB errors propagate — a failed mirror is a soft failure and the
        journal import that already happened is idempotent on retry.
        """
        if not rows or upsert_executed_order is None:
            return 0
        _overlay_journal_realized_pnl(rows)
        mirrored = 0
        for e in rows:
            exec_id = e.get("execId")
            if not isinstance(exec_id, str) or not exec_id:
                continue
            perm_id = e.get("permId")
            if not isinstance(perm_id, int):
                perm_id = None
            upsert_executed_order(exec_id, e, e.get("time") or "", perm_id=perm_id)
            mirrored += 1
        return mirrored

    # ------------------------------------------------------------------
    # Failure bookkeeping — 5-min embargo, capped per ET day.
    # ------------------------------------------------------------------
    def _record_failure(self, started_at: str, message: str) -> None:
        now_utc = _now_utc()
        today = _et_date(now_utc)
        prior = self._backoff_state
        same_day = prior.get("soft_attempt_date") == today
        attempts = int(prior.get("soft_attempts") or 0) if same_day else 0
        self._backoff_state = _throttle_backoff.record_soft_failure(prior, now_utc=now_utc)
        self._backoff_state["soft_attempt_date"] = today
        self._backoff_state["soft_attempts"] = attempts + 1

        self.record_cycle_health(
            "error",
            started_at=started_at,
            error={
                "message": message,
                "next_attempt_at": (
                    _throttle_backoff.blocked_until(self._backoff_state) or now_utc
                ).isoformat(),
            },
        )

    def _soft_budget_exhausted(self, now_utc: datetime) -> bool:
        state = self._backoff_state
        if state.get("soft_attempt_date") != _et_date(now_utc):
            return False
        return int(state.get("soft_attempts") or 0) >= MAX_SOFT_ATTEMPTS_PER_ET_DAY


__all__ = ["EveningExecutionSweepHandler", "CHECK_INTERVAL"]
