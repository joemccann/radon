#!/usr/bin/env python3
"""Cash-flow sync — Monitor daemon handler.

NOT REGISTERED since 2026-09-02. Cash flows come from the sFTP-delivered
statement (radon-flex-pull.timer -> flex_delivery_ingest -> cash_flow_sync
--from-file), and that ingest writes the `cash-flow-sync` heartbeat. This
handler's daily no-source run exited EXIT_FLEX_SEND_DISABLED and painted
"Flex Web Service is file-ingest only" over rows the delivery had already
synced. The class stays for its embargo bookkeeping contracts
(R-104/R-108/R-109); registering it again means a weekday SendRequest.

Pulls IB CashTransaction rows (deposits, withdrawals, dividends, interest,
fees) from the NAV Flex Query and persists to the `cash_flows` Turso
table once per ET trading day at 08:00 ET (pre-open; see FIRE_HOUR_ET).

Why daily, not 4-hourly: IBKR Flex Web Service uses a sliding-window
rate limit. Every request during throttle — including failures —
pushes the reset further out. The previous 4h cadence with internal
retries fired up to 12 attempts per 24h. While the token was throttled,
those attempts perpetuated the throttle indefinitely (May 9 2026 — Joe
burned ~24h of cash flow visibility this way). Cash flows publish once
per day; one well-timed daily call is sufficient
and stays inside the rate budget.

Throttle handling: the one documented rate-limit code (1018) triggers an
exponential circuit breaker (90s -> 5m -> 15m -> 1h capped) via
``_throttle_backoff``. IBKR's published 1018 limit is one request per
second and ten per minute, per token, so the ladder is measured in
minutes; it used to run 24h -> 168h, three orders of magnitude harsher
than the constraint it modelled. The breaker composes with thedaily window: an embargo that outlives the window still defers to
tomorrow rather than re-probing later the same day.

1001 and 1019 are NOT rate limits and never reach the breaker. 1019 is
"statement generation in progress" and 1001 is a transient generation
failure; both take the soft lane, bounded by
``MAX_SOFT_ATTEMPTS_PER_ET_DAY``.

Wired into monitor_daemon via scripts/monitor_daemon/run.py:create_daemon().
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
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

# Daily fire window: 08:00 ET (inclusive) through 08:59 ET (inclusive).
# A 1h window gives the daemon's 30s loop ~120 chances to pick it up
# under normal operation. Outside the window we still allow a "late
# fire" if last_run is on a strictly earlier ET trading day, so a
# daemon outage doesn't skip a day.
#
# Why morning, not 17:00 ET (the original slot): every 17:00 ET
# SendRequest from 2026-08-10 through 2026-08-20 failed with Flex 1001
# ("statement could not be generated at this time") — IBKR's post-close
# statement-generation window reliably refuses the NAV query — while the
# SAME query id succeeds every morning at 07:34 ET via radon-perf-twr.
# Flex only serves its last FINALIZED statement (~T+1 for cash rows), so
# an evening pull never captured same-day data anyway; the morning pull
# loses nothing and lands in a window that demonstrably works.
FIRE_HOUR_ET = 8
FIRE_WINDOW_HOURS = 1

# Soft-failure retry budget per ET day. A Flex outage that keeps TIMING OUT
# (never returning a documented throttle code) routes every failure through
# record_soft_failure, which never escalates — unbounded, that retries every
# ~5 min until midnight ET, and each retry burns the sliding-window request
# budget exactly like a throttled call (module docstring; May 9 2026).
MAX_SOFT_ATTEMPTS_PER_ET_DAY = 3

# Margin between the script's own poll budget and the subprocess kill. It
# has to cover interpreter start, dotenv, the Turso write and a slow final
# poll — nothing more. The three deadlines must nest:
#
#   cash_flow_sync.FLEX_POLL_BUDGET_SECONDS
#     < subprocess_timeout_seconds()
#       < CashFlowSyncHandler.max_runtime_seconds
#
# They did not nest before 2026-08-16 (569s script budget, 180s subprocess
# kill, 120s daemon deadline), so the script never reached its own timeout
# and 14 of the 16 recorded failures were us SIGKILLing our own process
# mid-poll — each one spending a Flex SendRequest and writing zero rows.
SUBPROCESS_TIMEOUT_MARGIN_SECONDS = 60.0
HANDLER_DEADLINE_MARGIN_SECONDS = 60.0

# Exit codes emitted by scripts/cash_flow_sync.py, mapped to how this
# handler must react. Classifying by exit code (rather than by searching
# the last three stderr lines for "code 1001") is what stops a permanent
# auth failure from burning 3 SendRequests every trading day forever.
EXIT_CONFIG_ERROR = 1
EXIT_THROTTLE = 10
EXIT_FLEX_APP_ERROR = 11
EXIT_FLEX_LOCKOUT = 15
# See cash_flow_sync.EXIT_FLEX_PREFLIGHT_EMBARGO (R-100): the subprocess
# refused to SendRequest because the shared sidecar was already armed. That
# is the embargo WORKING; it must not extend the deadline.
EXIT_FLEX_PREFLIGHT_EMBARGO = 16
# R-134: single-sourced from utils.flex_embargo — the two copies had already
# started to disagree with each other across four call sites.
from utils.flex_embargo import LOCKOUT_DAYS as LOCKOUT_EMBARGO_DAYS  # noqa: E402

_SOFT_EXIT_CLASSES = {
    12: "not_ready",
    13: "parse_error",
    14: "write_error",
}


def _flex_poll_budget_seconds() -> float:
    """The script's own wall budget. Imported lazily so the daemon never
    pays for the script's dotenv load at import time."""
    from cash_flow_sync import FLEX_POLL_BUDGET_SECONDS  # noqa: PLC0415

    return float(FLEX_POLL_BUDGET_SECONDS)


def subprocess_timeout_seconds() -> float:
    """Kill the sync subprocess only AFTER it has spent its own budget."""
    return _flex_poll_budget_seconds() + SUBPROCESS_TIMEOUT_MARGIN_SECONDS


class SyncFailure(RuntimeError):
    """A classified failure of the sync subprocess.

    ``flex_class`` is the contract between the script's exit code and this
    handler's breaker bookkeeping. It crosses the subprocess boundary as a
    number, not as a string a log line could truncate.
    """

    def __init__(self, message: str, *, flex_class: str, code: Optional[str] = None):
        super().__init__(message)
        self.flex_class = flex_class
        self.code = code


def _read_service_health_row(service: str) -> Optional[tuple]:
    """Bounded, read-only single-row lookup. Lazy import keeps stripped
    environments importable and lets tests stub the transport."""
    from db import hrana_http  # noqa: PLC0415

    rows = hrana_http.hrana_execute(
        "SELECT state, last_attempt_finished_at, last_error "
        "FROM service_health WHERE service = ?",
        (service,),
    )
    return rows[0] if rows else None


def _persisted_next_attempt_at(service: str = "cash-flow-sync") -> Optional[str]:
    """The `next_attempt_at` this handler last persisted to service_health.

    Independent of `daemon_state.json`, which is why it can rebuild an
    embargo the state file lost.
    """
    row = _read_service_health_row(service)
    if not row:
        return None
    raw = row[2]
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    value = parsed.get("next_attempt_at")
    return str(value) if value else None


def _last_line(stdout: Optional[str]) -> str:
    lines = (stdout or "").strip().splitlines()
    return lines[-1] if lines else ""


def _parse_status_line(stdout: Optional[str]) -> Dict[str, Any]:
    """The script's machine-readable last stdout line, or ``{}``.

    Detail only — classification comes from the exit code. A malformed or
    absent line must never change how a failure is handled.
    """
    try:
        parsed = json.loads(_last_line(stdout))
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


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
    """Run scripts/cash_flow_sync.py once per ET trading day at 08:00 ET."""

    name = "cash_flow_sync"
    interval_seconds = CHECK_INTERVAL
    requires_market_hours = False
    service_name = "cash-flow-sync"

    # Must sit ABOVE subprocess_timeout_seconds() so the daemon's generic
    # REL-008 deadline never preempts this handler's own failure
    # bookkeeping. Pinned by test_cash_flow_sync_timeout_retry_budget.py.
    max_runtime_seconds = 540.0

    def __init__(self) -> None:
        super().__init__()
        self._backoff_state: Dict[str, Any] = _throttle_backoff.initial_state()
        # R-109: a run the daemon's deadline killed keeps executing in its
        # worker thread. Generations let its late bookkeeping be refused.
        self._run_generation = 0
        self._retired_generation = 0

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
                "soft_attempt_date": raw.get("soft_attempt_date"),
                "soft_attempts": int(raw.get("soft_attempts") or 0),
            }
        else:
            self._backoff_state = _throttle_backoff.initial_state()

    def seed_state_after_corrupt_load(self) -> None:
        """Rebuild the embargo when `daemon_state.json` could not be read.

        The state file is checksummed and shared by every handler, so one
        bad write starts this handler blank and forgets a live 24-168h
        Flex embargo. Re-probing a token IBKR still considers hot extends
        the sliding window instead of clearing it, which is how a one-day
        problem becomes a week-long one.

        `service_health.last_error.next_attempt_at` is written on every
        failed cycle and lives in Turso, so it survives whatever ate the
        file. Read-only, best-effort, and only ever ADDS an embargo.
        """
        # R-104/R-108: prefer the SHARED sidecar — any Flex caller may have
        # armed it, and a missing state file must not forget their lockout.
        shared = self._shared_embargo_until()
        next_attempt = shared or _persisted_next_attempt_at(
            self.service_name or "cash-flow-sync"
        )
        if not next_attempt:
            return
        try:
            parsed = datetime.fromisoformat(next_attempt)
        except (ValueError, TypeError):
            return
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if parsed <= _now_utc():
            return
        self._backoff_state = dict(self._backoff_state)
        self._backoff_state["blocked_until"] = next_attempt
        logger.warning(
            "cash_flow_sync state file was unreadable; embargo rebuilt from "
            "service_health (next attempt %s)", next_attempt
        )

    # ------------------------------------------------------------------
    # Cadence override — daily window + circuit breaker.
    # ------------------------------------------------------------------
    def _shared_embargo_until(self) -> Optional[str]:
        """The token-wide deadline any Flex caller may have armed.

        R-104: `is_due` used to gate only on this handler's PRIVATE backoff
        state, so a lockout armed by perf-twr or the blotter left the daily
        window open — the subprocess spawned, died on the embargo, and (per
        R-100) extended the lockout another week.
        """
        try:
            from utils.flex_embargo import active_until
            return active_until()
        except Exception:
            return None

    def _adopt_shared_embargo(self) -> None:
        until = self._shared_embargo_until()
        if not until:
            return
        self._backoff_state = dict(self._backoff_state)
        self._backoff_state["blocked_until"] = until

    def is_due(self) -> bool:
        if not self._enabled:
            return False

        now_utc = _now_utc()

        if self._shared_embargo_until():
            return False

        if _throttle_backoff.is_blocked(self._backoff_state, now_utc=now_utc):
            return False

        if self._soft_budget_exhausted(now_utc):
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

        # We are past the fire window on a trading day and have not fired today.
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
        # R-109: the daemon's hard deadline abandons this call but the thread
        # keeps running. Whichever finishes LAST used to win — a late
        # _mark_success cleared blocked_until and wrote an ok row over the
        # embargo the deadline path had just recorded. Stamp a generation at
        # entry; every write below refuses if the run has since been retired.
        self._run_generation += 1
        generation = self._run_generation

        try:
            result = self._execute_inner()
        except Exception as exc:
            if self._is_retired(generation):
                raise
            self._record_failure(started_at, str(exc), exc=exc)
            raise

        # Defence in depth: if a future caller stubs ``_execute_inner``
        # to return a soft-failure dict (status=error), promote it to a
        # raised exception here so the failure heartbeat fires before
        # BaseHandler.run() applies the same contract enforcement.
        # Two layers — ``execute()`` and ``BaseHandler.run()`` — make the
        # 2026-05-14 latching bug impossible regardless of test stubs.
        inner_error = result.get("error") if result.get("status") == "error" else None
        if inner_error:
            if not self._is_retired(generation):
                self._record_failure(started_at, str(inner_error))
            raise RuntimeError(str(inner_error))

        if self._is_retired(generation):
            logger.warning(
                "cash_flow_sync run %s finished after its deadline kill — "
                "success bookkeeping refused so the recorded embargo stands",
                generation,
            )
            return result

        self._mark_success()
        self.record_cycle_health("ok", started_at=started_at)

        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def retire_current_run(self) -> None:
        """Disown the in-flight run after the daemon's deadline killed it.

        R-109: without this the late worker thread's `_mark_success` cleared
        `blocked_until` and wrote an `ok` row over the failure the deadline
        path recorded, and the soft-attempt counter could be double-counted
        for one attempt.
        """
        self._retired_generation = self._run_generation

    def _is_retired(self, generation: int) -> bool:
        return generation <= self._retired_generation

    def record_deadline_failure(self, message: str) -> None:
        """Called by the daemon when its hard deadline kills this handler.

        Without this hook the daemon writes a generic error row with no
        ``next_attempt_at`` (invisible to the incident watchdog's embargo
        suppression — the 2026-08-04 page storm) and skips the soft-attempt
        counter entirely, so the next cycle re-probes a token that has
        already spent a request this evening.
        """
        self._record_failure(self._utc_now_iso(), message)

    def _classify_failure(self, message: str, exc: Optional[BaseException]) -> str:
        """throttle | permanent | config | soft.

        Prefers the typed exception carrying the subprocess's exit code.
        The stderr substring match is retained ONLY as a fallback for a
        subprocess from an older deploy, and says so in the log.
        """
        if isinstance(exc, FlexThrottleError):
            return "throttle"
        flex_class = getattr(exc, "flex_class", None)
        if flex_class in ("throttle", "permanent", "config", "lockout"):
            return flex_class
        if flex_class is not None:
            return "soft"

        # Legacy substring fallback, for a subprocess predating the typed
        # exit-code contract. 1018 ONLY: 1001 is a transient generation failure
        # and 1019 is "still generating", and routing either into the breaker
        # lane is what turned "try again shortly" into a multi-day embargo.
        if "FlexThrottleError" in message or "code 1018" in message:
            logger.warning(
                "cash_flow_sync throttle classified by stderr substring, not exit "
                "code — the subprocess predates the typed exit-code contract"
            )
            return "throttle"
        return "soft"

    def _record_failure(
        self,
        started_at: str,
        message: str,
        *,
        exc: Optional[BaseException] = None,
    ) -> None:
        """Persist failure heartbeat with next_attempt_at metadata."""
        failure_class = self._classify_failure(message, exc)
        now_utc = _now_utc()

        if failure_class == "throttle":
            self._backoff_state = _throttle_backoff.record_throttle(
                self._backoff_state, now_utc=now_utc
            )
        elif failure_class == "lockout":
            self._record_lockout(now_utc)
        elif failure_class == "preflight_embargo":
            # The shared sidecar already holds the deadline; mirror it into
            # the private backoff state so is_due agrees, but never re-arm.
            self._adopt_shared_embargo()
        elif failure_class == "config":
            self._record_config_error(now_utc)
        elif failure_class == "permanent":
            # Auth failure / unknown query id / undocumented error code.
            # Retrying cannot flip it, so spend no more of today's budget;
            # the next daily window re-probes once a human has looked.
            self._advance_soft_attempts(now_utc, to=MAX_SOFT_ATTEMPTS_PER_ET_DAY)
        else:
            self._advance_soft_attempts(now_utc)

        next_attempt_at = self._next_attempt_iso(now_utc)
        self.record_cycle_health(
            "error",
            started_at=started_at,
            error={
                "message": message,
                "class": failure_class,
                "next_attempt_at": next_attempt_at,
            },
        )

    def _advance_soft_attempts(self, now_utc: datetime, *, to: Optional[int] = None) -> None:
        """Spend one (or all) of today's soft-retry budget and set the
        short embargo. Never escalates the throttle ladder."""
        today = _et_date(now_utc)
        prior = self._backoff_state
        same_day = prior.get("soft_attempt_date") == today
        attempts = int(prior.get("soft_attempts") or 0) if same_day else 0
        self._backoff_state = _throttle_backoff.record_soft_failure(
            prior, now_utc=now_utc
        )
        self._backoff_state["soft_attempt_date"] = today
        self._backoff_state["soft_attempts"] = to if to is not None else attempts + 1

    def _record_lockout(self, now_utc: datetime) -> None:
        """1025 is a token lockout. The next daily 08:00 window is too soon.

        Production 2026-08-21 classified this as permanent, scheduled Monday
        08:00 ET, and that SendRequest is what kept the lozenge red. Hold
        the token for LOCKOUT_EMBARGO_DAYS and arm the shared sidecar so
        TWR and /performance/background do not poke it either.
        """
        today = _et_date(now_utc)
        state = dict(self._backoff_state)
        state["soft_attempt_date"] = today
        state["soft_attempts"] = MAX_SOFT_ATTEMPTS_PER_ET_DAY
        state["blocked_until"] = (
            now_utc + timedelta(days=LOCKOUT_EMBARGO_DAYS)
        ).isoformat()
        self._backoff_state = state
        try:
            from utils.flex_embargo import record_lockout
            record_lockout("1025", now=now_utc)
        except Exception:
            return

    def _record_config_error(self, now_utc: datetime) -> None:
        """A missing token is a CONFIG error, never a success.

        Before 2026-08-16 this path returned ``{"status": "skip"}``, which
        is not ``error``, so ``execute()`` fell through to ``_mark_success``
        and RESET the circuit breaker while syncing nothing — a dropped env
        var reported healthy forever and masked every real failure.

        The breaker is left exactly as it was (no Flex request was spent,
        so nothing about the token's rate budget changed) and the embargo
        is only ever EXTENDED, never shortened, so a config error cannot
        buy a shorter re-probe than the throttle ladder already imposed.
        """
        state = dict(self._backoff_state)
        cooldown_until = now_utc + timedelta(
            seconds=_throttle_backoff.SOFT_RETRY_COOLDOWN_SECS
        )
        current = _throttle_backoff.blocked_until(state)
        if current is None or current < cooldown_until:
            state["blocked_until"] = cooldown_until.isoformat()
        self._backoff_state = state

    def _soft_budget_exhausted(self, now_utc: datetime) -> bool:
        """True when today's ET day already spent its soft-retry budget —
        the next SendRequest waits for tomorrow's daily window."""
        state = self._backoff_state
        if state.get("soft_attempt_date") != _et_date(now_utc):
            return False
        return int(state.get("soft_attempts") or 0) >= MAX_SOFT_ATTEMPTS_PER_ET_DAY

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
        """Next fire-window open on a trading day strictly later than today."""
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
            raise SyncFailure(
                "IB_FLEX_TOKEN / IB_FLEX_NAV_QUERY_ID not configured",
                flex_class="config",
            )

        script = PROJECT_ROOT / "scripts" / "cash_flow_sync.py"
        if not script.exists():
            raise SyncFailure(f"script not found: {script}", flex_class="config")

        timeout_s = subprocess_timeout_seconds()
        try:
            result = subprocess.run(
                [sys.executable, "-m", "scripts.cash_flow_sync"],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"cash_flow_sync timed out after {timeout_s:.0f}s") from None
        except Exception as exc:
            raise RuntimeError(str(exc)) from exc

        status = _parse_status_line(result.stdout)

        if result.returncode != 0:
            raise self._failure_for(result, status)

        return {"status": "ok", "summary": _last_line(result.stdout), "detail": status}

    @staticmethod
    def _failure_for(result: Any, status: Dict[str, Any]) -> BaseException:
        """Map the subprocess exit code onto a typed, classified failure."""
        tail = (result.stderr or result.stdout or "").splitlines()[-3:]
        message = " | ".join(tail) or f"cash_flow_sync exited {result.returncode}"
        logger.warning("cash_flow_sync failed (exit %s): %s", result.returncode, message)

        code = result.returncode
        if code == EXIT_THROTTLE:
            return FlexThrottleError(str(status.get("code") or "unknown"), message)
        if code == EXIT_FLEX_PREFLIGHT_EMBARGO:
            return SyncFailure(message, flex_class="preflight_embargo",
                               code=str(status.get("code") or "1025"))
        if code == EXIT_FLEX_LOCKOUT:
            return SyncFailure(message, flex_class="lockout",
                               code=str(status.get("code") or "1025"))
        if code == EXIT_FLEX_APP_ERROR:
            return SyncFailure(message, flex_class="permanent",
                               code=str(status.get("code") or ""))
        if code == EXIT_CONFIG_ERROR:
            # Exit 1 is also the LEGACY catch-all: a subprocess from a
            # pre-2026-08-16 deploy exits 1 for a throttle too. Only the
            # status line proves it is really a config error; without one,
            # fall through to the stderr substring classifier.
            if status.get("class") == "config_error":
                return SyncFailure(message, flex_class="config")
            return RuntimeError(message)
        if code in _SOFT_EXIT_CLASSES:
            return SyncFailure(message, flex_class=_SOFT_EXIT_CLASSES[code])
        # Unknown exit code from an older deploy: fall back to the stderr
        # substring classifier in ``_record_failure``.
        return RuntimeError(message)


# Re-export so callers can `from cash_flow_sync import FlexThrottleError`.
__all__ = ["CashFlowSyncHandler", "CHECK_INTERVAL", "FlexThrottleError"]
