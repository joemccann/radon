#!/usr/bin/env python3
"""Cash-flow sync — Monitor daemon handler.

Pulls IB CashTransaction rows (deposits, withdrawals, dividends, interest,
fees) from the NAV Flex Query and persists to the `cash_flows` Turso
table once per ET trading day at 17:00 ET (1h after the 4PM ET close).

Why daily, not 4-hourly: IBKR Flex Web Service uses a sliding-window
rate limit. Every request during throttle — including failures —
pushes the reset further out. The previous 4h cadence with internal
retries fired up to 12 attempts per 24h. While the token was throttled,
those attempts perpetuated the throttle indefinitely (May 9 2026 — Joe
burned ~24h of cash flow visibility this way). Cash flows publish once
per day; one well-timed daily call after market close is sufficient
and stays inside the rate budget.

Throttle handling: documented Flex codes 1001 / 1018 / 1019 trigger an
exponential circuit breaker (24h -> 48h -> 72h -> 168h capped) via
``_throttle_backoff``. The breaker composes with the daily 17:00 ET
window: when the embargo says "not before tomorrow", the handler still
waits until tomorrow at 17:00 ET — never a partial-day re-probe.

Wired into monitor_daemon via scripts/monitor_daemon/run.py:create_daemon().
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover — Py3.9 fallback (already shipped)
    from backports.zoneinfo import ZoneInfo  # type: ignore

from monitor_daemon.handlers import _throttle_backoff
from monitor_daemon.handlers._throttle_backoff import FlexThrottleError
from monitor_daemon.handlers.base import BaseHandler

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# CHECK_INTERVAL is retained for the daemon's bookkeeping (it falls back
# to BaseHandler.is_due in tests that don't override our window). The
# real cadence is enforced by the ``is_due`` override below.
CHECK_INTERVAL = 24 * 60 * 60  # 24h floor — exact firing handled by is_due

ET = ZoneInfo("America/New_York")

# Daily fire window: 17:00 ET (inclusive) through 17:59 ET (inclusive).
# A 1h window gives the daemon's 30s loop ~120 chances to pick it up
# under normal operation. Outside the window we still allow a "late
# fire" if last_run is on a strictly earlier ET trading day, so a
# daemon outage doesn't skip a day.
FIRE_HOUR_ET = 17
FIRE_WINDOW_HOURS = 1


def _now_utc() -> datetime:
    """Indirection to make `datetime.now(tz=UTC)` patchable in tests."""
    return datetime.now(timezone.utc)


def _et_date(now_utc: datetime) -> str:
    """ET calendar date as YYYY-MM-DD for the given UTC moment."""
    return now_utc.astimezone(ET).strftime("%Y-%m-%d")


def _is_trading_day_et(now_utc: datetime) -> bool:
    """True iff the ET calendar day is a US equity trading day."""
    et_dt = now_utc.astimezone(ET)
    if et_dt.weekday() >= 5:
        return False
    # Lazy import — keeps the handler importable in environments without
    # the holiday config in place.
    try:
        from utils.market_calendar import load_holidays
    except Exception:
        return True
    holidays = load_holidays(et_dt.year)
    return et_dt.strftime("%Y-%m-%d") not in holidays


class CashFlowSyncHandler(BaseHandler):
    """Run scripts/cash_flow_sync.py once per ET trading day at 17:00 ET."""

    name = "cash_flow_sync"
    interval_seconds = CHECK_INTERVAL
    requires_market_hours = False
    service_name = "cash-flow-sync"

    def __init__(self) -> None:
        super().__init__()
        self._backoff_state: Dict[str, Any] = _throttle_backoff.initial_state()

    # ------------------------------------------------------------------
    # State persistence — circuit breaker survives daemon restarts.
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
            }
        else:
            self._backoff_state = _throttle_backoff.initial_state()

    # ------------------------------------------------------------------
    # Cadence override — daily window + circuit breaker.
    # ------------------------------------------------------------------
    def is_due(self) -> bool:
        if not self._enabled:
            return False

        now_utc = _now_utc()

        if _throttle_backoff.is_blocked(self._backoff_state, now_utc=now_utc):
            return False

        if not _is_trading_day_et(now_utc):
            return False

        et_now = now_utc.astimezone(ET)
        if et_now.hour < FIRE_HOUR_ET:
            return False

        # Past the start of the window. If the daemon already fired today
        # in ET, we're done — never double-fire same ET trading day.
        if self.last_run is not None:
            last_run_et_date = self.last_run.astimezone(ET).strftime("%Y-%m-%d")
            today_et_date = et_now.strftime("%Y-%m-%d")
            if last_run_et_date == today_et_date:
                return False

        # We're past 17:00 ET on a trading day and haven't fired today.
        # Fire — even if past the 1h "preferred" window, since we'd
        # otherwise skip the day entirely.
        return True

    # ------------------------------------------------------------------
    # Execution — one shot, no internal retries on throttle codes.
    # ------------------------------------------------------------------
    def execute(self) -> Dict[str, Any]:
        """Wrap inner logic with service_health heartbeat (success+error).

        Inner ``_execute_inner`` now raises ``RuntimeError`` on every
        retryable failure path so the BaseHandler contract
        (``HandlerSoftFailure``) fires uniformly. The outer wrapper still
        records the failure heartbeat with throttle metadata before
        re-raising — that's what advances the circuit breaker without
        latching ``last_run`` and burning the daily slot.

        2026-05-14 incident: a single 60s Flex timeout cost ~7 days of
        cash flow data because the inner method RETURNED status=error
        and BaseHandler latched ``last_run``. With both ``_execute_inner``
        raising and ``BaseHandler.run()`` enforcing the contract, the bug
        is structurally impossible.

        Heartbeats route through ``self.record_cycle_health`` (DUR-14) so
        the structural heartbeat in ``BaseHandler.run()`` knows this cycle
        recorded its own richer row (throttle metadata) and never clobbers
        it with a plain ok/error.
        """
        started_at = self._utc_now_iso()
        try:
            result = self._execute_inner()
        except Exception as exc:
            self._record_failure(started_at, str(exc))
            raise

        # Defence in depth: if a future caller stubs ``_execute_inner``
        # to return a soft-failure dict (status=error), promote it to a
        # raised exception here so the failure heartbeat fires before
        # BaseHandler.run() applies the same contract enforcement.
        # Two layers — ``execute()`` and ``BaseHandler.run()`` — make the
        # 2026-05-14 latching bug impossible regardless of test stubs.
        inner_error = result.get("error") if result.get("status") == "error" else None
        if inner_error:
            self._record_failure(started_at, str(inner_error))
            raise RuntimeError(str(inner_error))

        self._mark_success()
        self.record_cycle_health("ok", started_at=started_at)

        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _record_failure(
        self,
        started_at: str,
        message: str,
    ) -> None:
        """Persist failure heartbeat with next_attempt_at metadata."""
        is_throttle = "FlexThrottleError" in message or any(
            f"code {code}" in message for code in ("1001", "1018", "1019")
        )

        now_utc = _now_utc()
        if is_throttle:
            self._backoff_state = _throttle_backoff.record_throttle(
                self._backoff_state, now_utc=now_utc
            )
        else:
            self._backoff_state = _throttle_backoff.record_soft_failure(
                self._backoff_state, now_utc=now_utc
            )

        next_attempt_at = self._next_attempt_iso(now_utc)
        self.record_cycle_health(
            "error",
            started_at=started_at,
            error={"message": message, "next_attempt_at": next_attempt_at},
        )

    def _mark_success(self) -> None:
        """Reset the circuit breaker after a successful sync."""
        self._backoff_state = _throttle_backoff.record_success(self._backoff_state)

    def _next_attempt_iso(self, now_utc: datetime) -> Optional[str]:
        """ISO timestamp of the next eligible fire window."""
        embargo_until = _throttle_backoff.blocked_until(self._backoff_state)
        next_window = self._next_daily_window_after(now_utc)
        candidates = [c for c in (embargo_until, next_window) if c is not None]
        if not candidates:
            return None
        return max(candidates).isoformat()

    @staticmethod
    def _next_daily_window_after(now_utc: datetime) -> datetime:
        """Next 17:00 ET on a trading day strictly later than today."""
        et_now = now_utc.astimezone(ET)
        from datetime import timedelta as _td
        candidate = et_now.replace(hour=FIRE_HOUR_ET, minute=0, second=0, microsecond=0)
        if candidate <= et_now:
            candidate = candidate + _td(days=1)
        # Walk forward to a trading day.
        for _ in range(8):
            probe_utc = candidate.astimezone(timezone.utc)
            if _is_trading_day_et(probe_utc):
                return probe_utc
            candidate = candidate + _td(days=1)
        return candidate.astimezone(timezone.utc)

    def _execute_inner(self) -> Dict[str, Any]:
        """Run the sync subprocess. Raise on every retryable failure path.

        Returning ``{"status": "error", ...}`` would let
        ``BaseHandler.run()`` latch ``last_run`` and starve the panel for
        24h. Raising routes failures through the contract's
        ``HandlerSoftFailure`` lane so the next 30s daemon cycle re-evaluates
        ``is_due`` (gated by the 5-min embargo from ``record_soft_failure``
        or the 24h+ embargo from ``record_throttle``).
        """
        if not os.environ.get("IB_FLEX_TOKEN") or not os.environ.get("IB_FLEX_NAV_QUERY_ID"):
            return {"status": "skip", "reason": "IB_FLEX_TOKEN / IB_FLEX_NAV_QUERY_ID not configured"}

        script = PROJECT_ROOT / "scripts" / "cash_flow_sync.py"
        if not script.exists():
            raise RuntimeError(f"script not found: {script}")

        try:
            result = subprocess.run(
                [sys.executable, "-m", "scripts.cash_flow_sync"],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=180,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("cash_flow_sync timed out after 180s") from None
        except Exception as exc:
            raise RuntimeError(str(exc)) from exc

        if result.returncode != 0:
            tail = (result.stderr or result.stdout or "").splitlines()[-3:]
            error_msg = " | ".join(tail)
            logger.warning("cash_flow_sync failed: %s", error_msg)
            raise RuntimeError(error_msg)

        last_line = (result.stdout or "").strip().splitlines()[-1] if result.stdout else ""
        return {"status": "ok", "summary": last_line}


# Re-export so callers can `from cash_flow_sync import FlexThrottleError`.
__all__ = ["CashFlowSyncHandler", "CHECK_INTERVAL", "FlexThrottleError"]
