"""Radon FastAPI server — replaces Python shell-outs from Next.js.

Persistent IB connections, shared UW client, uniform JSON responses.
Port 8321, no auth for local use.

Usage:
    python3 -m uvicorn scripts.api.server:app --host 127.0.0.1 --port 8321 --reload
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
import sys
import random
import time
import uuid
from collections import OrderedDict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, List, Optional, Tuple

from fastapi import FastAPI, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

# Ensure scripts/ is on sys.path for client imports
SCRIPTS_DIR = Path(__file__).parent.parent
PROJECT_ROOT = SCRIPTS_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
INTERNALS_SKEW_CACHE_DIR = DATA_DIR / "cache"
INTERNALS_SKEW_CACHE_TTL_SECONDS = 60 * 15

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from api.ib_pool import IBPool
from api import db_http
from db.service_health_sql import SERVICE_HEALTH_UPSERT_SQL, service_health_upsert_args
from api.demo_scan import demo_disabled_payload, demo_scan_response
from api.subprocess import run_script, run_module, run_script_raw, ScriptResult
from api.scan_gate import ScanGate
from api.ib_gateway import (
    check_ib_gateway,
    ensure_ib_gateway,
    restart_ib_gateway,
    recover_stuck_pool,
    is_docker_mode,
    is_cloud_mode,
    is_launchd_mode,
    reset_restart_backoff,
)
from api import ib_gateway
from api import services as admin_services
from clients.ib_client import DEFAULT_GATEWAY_PORT
from api.pool_order_manage import pool_cancel_order, pool_modify_order
from api.order_audit import record_order_event
from api.auth import verify_clerk_jwt, verify_api_key, is_trusted_local_request
from api.ws_ticket import create_ticket, validate_ticket
from api.routes.historical import router as historical_router
from api.routes.preferences import router as preferences_router
from api.routes.assistant_market import router as assistant_market_router

import app_preferences
from clients.menthorq_dashboard_client import (
    MenthorQDashboardAuthError,
    MenthorQDashboardClient,
    MenthorQDashboardPayloadError,
    MenthorQDashboardTimeoutError,
    MenthorQDashboardUpstreamError,
)
# Lightweight imports: get_embedder is a lazy singleton (fastembed/ONNX load
# only on first call, and only ever inside asyncio.to_thread — see /knowledge).
from knowledge.embed import get_embedder
from knowledge.retrieve import hybrid_search
from knowledge.sources.journal import TRADE_LOG_KEY_PREFIX

# Load .env from project root for Python scripts.
# .env.ib-mode (managed by scripts/ib mode) overlays after .env so its
# IB_GATEWAY_MODE/HOST values win — single switch, no .env rewriting.
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
    load_dotenv(PROJECT_ROOT / "web" / ".env")
    load_dotenv(PROJECT_ROOT / ".env.ib-mode", override=True)
except ImportError:
    pass

logger = logging.getLogger("radon.api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

# Suppress verbose ib_insync logging (positions, orders at INFO level)
logging.getLogger("ib_insync").setLevel(logging.WARNING)
logging.getLogger("ib_insync.wrapper").setLevel(logging.WARNING)
logging.getLogger("ib_insync.client").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
from clients.uw_client import UWClient
from clients.uw_client import UWAPIError, UWNotFoundError
from ib_insync import Index


# Shared state
# ---------------------------------------------------------------------------
ib_pool: Optional[IBPool] = None
uw_available: bool = False
test_mode: bool = os.environ.get("RADON_API_TEST_MODE", "").lower() in {"1", "true", "yes", "on"}
test_order_counter: int = 900000


def _next_test_order_ids() -> tuple[int, int]:
    global test_order_counter
    test_order_counter += 1
    order_id = test_order_counter
    perm_id = 8_000_000 + order_id
    return order_id, perm_id


IB_HEARTBEAT_INTERVAL_SECS = 15

# Worst-case wall-clock budget for the /health IB gateway probe. The probe can
# block for tens of seconds when the pool is mid-reconnect after a 2FA approval
# (handle_auth_state_transition -> pool.reconnect_all bounded at 30s + heal 10s).
# When it does, uvicorn workers pile up and every health-dependent UI surface
# shows a scary "operation aborted due to timeout" / RELAY OFFLINE state even
# though IB is healthy. /health must ALWAYS return fast with a structured
# payload; recovery itself stays on the unbounded _ib_recovery_heartbeat_loop.
HEALTH_GATEWAY_PROBE_TIMEOUT_SECS = 2.5

# /health/lite is polled by the health daemon and host-metrics sampler. It is
# read-only and has no recovery work to justify holding a worker behind a
# wedged gateway status probe; degrade its coarse fields after this short budget.
HEALTH_LITE_GATEWAY_PROBE_TIMEOUT_SECS = 0.5


def _bounded_env_int(name: str, default: int, *, minimum: int = 1, maximum: int = 64) -> int:
    try:
        parsed = int(os.environ.get(name, ""))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


@dataclass(frozen=True)
class _IBSyncOutcome:
    result: Any
    payload: Optional[dict] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return bool(getattr(self.result, "ok", False)) and self.error is None


class _IBSyncCoordinator:
    """Per-resource single-flight with a short successful-result minimum age."""

    def __init__(self, *, min_age_secs: float) -> None:
        self.min_age_secs = max(0.0, float(min_age_secs))
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock: Optional[asyncio.Lock] = None
        self._states: dict[str, dict[str, Any]] = {}

    def _bind_running_loop(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            active = [
                state["task"]
                for state in self._states.values()
                if state.get("task") is not None and not state["task"].done()
            ]
            if active:
                raise RuntimeError("IB sync coordinator cannot move loops with work in flight")
            self._loop = loop
            self._lock = asyncio.Lock()
            # A new event loop means a new application/test lifecycle. Avoid
            # carrying cached results across independent TestClient portals.
            self._states = {}
        assert self._lock is not None
        return self._lock

    async def run(
        self,
        key: str,
        operation: Callable[[], Awaitable[_IBSyncOutcome]],
    ) -> _IBSyncOutcome:
        lock = self._bind_running_loop()
        async with lock:
            state = self._states.setdefault(
                key,
                {"task": None, "last_success_at": 0.0, "last_outcome": None},
            )
            cached = state["last_outcome"]
            if (
                cached is not None
                and time.monotonic() - state["last_success_at"] < self.min_age_secs
            ):
                return cached
            task = state["task"]
            if task is None:
                task = asyncio.create_task(self._execute(key, operation))
                state["task"] = task
        # A disconnected browser must not cancel the shared subprocess for all
        # other callers. The coordinator task owns its own cleanup.
        return await asyncio.shield(task)

    async def _execute(
        self,
        key: str,
        operation: Callable[[], Awaitable[_IBSyncOutcome]],
    ) -> _IBSyncOutcome:
        outcome: Optional[_IBSyncOutcome] = None
        try:
            outcome = await operation()
            return outcome
        finally:
            lock = self._bind_running_loop()
            async with lock:
                state = self._states.setdefault(
                    key,
                    {"task": None, "last_success_at": 0.0, "last_outcome": None},
                )
                if outcome is not None and outcome.ok:
                    state["last_success_at"] = time.monotonic()
                    state["last_outcome"] = outcome
                state["task"] = None


IB_SYNC_MIN_AGE_SECS = _bounded_env_int(
    "RADON_IB_SYNC_MIN_AGE_SECS", 30, minimum=1, maximum=300
)
_ib_sync_coordinator = _IBSyncCoordinator(min_age_secs=IB_SYNC_MIN_AGE_SECS)


async def _portfolio_sync_operation() -> _IBSyncOutcome:
    result = await _run_ib_script_with_recovery(
        "ib_sync.py",
        ["--sync", "--json-output", "--db-optional", "--port", str(DEFAULT_GATEWAY_PORT)],
        timeout=30,
        raw=False,
    )
    if not result.ok:
        return _IBSyncOutcome(result=result, error=result.error or "Portfolio sync failed")
    if isinstance(result.data, dict) and result.data:
        return _IBSyncOutcome(result=result, payload=result.data)
    try:
        payload = await _read_latest_portfolio_snapshot_from_db()
    except Exception as exc:
        return _IBSyncOutcome(
            result=result,
            error=f"Failed to read synced portfolio from Turso: {exc}",
        )
    return _IBSyncOutcome(result=result, payload=payload)


async def _orders_sync_operation() -> _IBSyncOutcome:
    result = await _run_ib_script_with_recovery(
        "ib_orders.py",
        ["--sync", "--port", str(DEFAULT_GATEWAY_PORT)],
        timeout=30,
        raw=True,
    )
    if not result.ok:
        return _IBSyncOutcome(result=result, error=result.error or "Orders sync failed")
    try:
        payload = await _read_orders_snapshot_from_db()
    except Exception as exc:
        return _IBSyncOutcome(
            result=result,
            error=f"Failed to read synced orders from Turso: {exc}",
        )
    return _IBSyncOutcome(result=result, payload=payload)


async def _coordinated_portfolio_sync() -> _IBSyncOutcome:
    return await _ib_sync_coordinator.run("portfolio", _portfolio_sync_operation)


async def _coordinated_orders_sync() -> _IBSyncOutcome:
    return await _ib_sync_coordinator.run("orders", _orders_sync_operation)


# Pool-recovery escalation guard. Mirrors ib_gateway._auth_transition_state but
# governs the LEVEL-triggered recover_stuck_pool path: the auth_state edge in
# handle_auth_state_transition cannot fire while the pool is wedged because
# auth_state is derived FROM the pool (circular dependency / deadlock). This
# state machine drives the pool-independent probe-gated recovery on the same 15s
# heartbeat — single-flight + cooldown so a stuck episode triggers at most one
# reconnect per cooldown window, and only ever ONE self-restart per episode.
# See feedback_ib_pool_stuck_after_2fa.md.
POOL_RECOVERY_COOLDOWN_SECS = 60.0
POOL_RECOVERY_MAX_BEFORE_RESTART = 3

_pool_recovery_state: dict = {
    "in_progress": False,       # single-flight: skip overlapping ticks
    "last_attempt_at": 0.0,     # epoch seconds; cooldown gate
    "consecutive_failures": 0,  # verified-fail count → escalation ladder
}


def _restart_radon_api_self() -> None:
    """Exit the process so systemd (Restart=always) brings radon-api back fresh.

    This is the documented last-resort remedy when the pool stays wedged after
    a verified-authenticated Gateway: `systemctl restart radon-api.service`.
    Exiting under systemd restarts ONLY radon-api — never the Gateway (no 2FA
    push, no cascade to relay/monitor). os._exit avoids running atexit/teardown
    that could itself wedge on the very sockets we're trying to recycle.
    """
    logger.error(
        "pool recovery: escalation threshold reached — exiting radon-api for a "
        "systemd-managed restart (radon-api only; Gateway untouched)"
    )
    os._exit(1)


async def _recover_stuck_pool_guarded() -> None:
    """Single-flight + cooldown + escalation wrapper around recover_stuck_pool.

    Runs inside the heartbeat try/except so a probe/reconnect error can never
    kill the 15s loop. Escalates to a SINGLE radon-api self-restart only after
    POOL_RECOVERY_MAX_BEFORE_RESTART consecutive verified failures (Gateway
    probe authenticated yet pool still has no connected+accounted slot). The
    counter resets on success AND after an escalation so it can never loop.
    """
    if test_mode or ib_pool is None:
        return
    if _pool_recovery_state["in_progress"]:
        return
    now = time.time()
    if now - _pool_recovery_state["last_attempt_at"] < POOL_RECOVERY_COOLDOWN_SECS:
        return

    _pool_recovery_state["in_progress"] = True
    _pool_recovery_state["last_attempt_at"] = now
    try:
        recovered = await recover_stuck_pool(ib_pool)
    except Exception:
        logger.exception("pool recovery: recover_stuck_pool raised; will retry next cycle")
        return
    finally:
        _pool_recovery_state["in_progress"] = False

    if recovered:
        _pool_recovery_state["consecutive_failures"] = 0
        return

    # recover_stuck_pool returns False either for a healthy/no-op pool OR a
    # genuine 2FA wait — neither is a "verified failure" worth escalating. Only
    # escalate when the Gateway probe says authenticated yet the pool is STILL
    # stuck (reconnect ran but didn't take). Re-derive that exact signature.
    from api.ib_gateway import (
        _pool_disconnected_roles,
        _pool_has_connected_accounted_slot,
        _probe_authenticated,
    )

    stuck_roles = _pool_disconnected_roles(ib_pool)
    if not stuck_roles:
        _pool_recovery_state["consecutive_failures"] = 0
        return
    # Scope the accounted check to the STUCK roles: an ANY-role check let
    # healthy orders/sync slots reset the ladder while the data role stayed
    # wedged forever (R-060). Only a re-read showing the stuck roles recovered
    # (raced recovery between checks) clears the counter.
    if _pool_has_connected_accounted_slot(ib_pool, roles=stuck_roles):
        _pool_recovery_state["consecutive_failures"] = 0
        return
    try:
        authenticated, _accounts = await _probe_authenticated()
    except Exception:
        logger.exception("pool recovery: escalation probe raised; not escalating")
        return
    if not authenticated:
        # Genuine 2FA wait — do NOT count toward escalation.
        return

    _pool_recovery_state["consecutive_failures"] += 1
    failures = _pool_recovery_state["consecutive_failures"]
    logger.warning(
        "pool recovery: verified-fail %d/%d (Gateway authenticated, pool still stuck)",
        failures, POOL_RECOVERY_MAX_BEFORE_RESTART,
    )
    if failures >= POOL_RECOVERY_MAX_BEFORE_RESTART:
        _pool_recovery_state["consecutive_failures"] = 0
        _restart_radon_api_self()


async def _ib_recovery_heartbeat_tick() -> None:
    """Drive check_ib_gateway WITH the pool once, so the documented
    awaiting_2fa -> authenticated pool recovery (pool.reconnect_all) fires
    server-side, independent of any browser poll.

    The status consumers now read the read-only /edge-health surface (which
    probes /health/lite with pool=None and has NO side effects), so this loop is
    the sole FAST driver of recovery; the every-minute watchdog /health curl is
    the slower backstop. See feedback_ib_pool_stuck_after_2fa.md.

    After the edge-triggered check, we ALSO run the level-triggered
    recover_stuck_pool path (probe-gated, pool-independent) which breaks the
    auth_state-derived-from-pool deadlock the edge handler cannot escape. Both
    live under the same try/except so neither can kill the loop.
    """
    if ib_pool is None:
        return
    try:
        await check_ib_gateway(pool_status=ib_pool.status(), pool=ib_pool)
        await _recover_stuck_pool_guarded()
    except Exception:
        logger.exception("IB recovery heartbeat tick failed")


async def _ib_recovery_heartbeat_loop(interval: float = IB_HEARTBEAT_INTERVAL_SECS) -> None:
    while True:
        await asyncio.sleep(interval)
        await _ib_recovery_heartbeat_tick()


ORDERS_SYNC_INTERVAL_SECS = 5 * 60  # 5 min — comfortably under the 10-min watchdog window
# Same class as flow-refresh-capacity-502 / R-170: general-lane cap is 3,
# scan storms pin it, ib_orders.py is not on the reserved order lane.
ORDERS_SYNC_SHED_RETRIES = 2
ORDERS_SYNC_SHED_RETRY_DELAY_SECS = 8.0
# Operator POST /flow-analysis/{ticker} shares that general lane. Fail-fast
# 502 leaves the UI on ANALYZING with a raw capacity error. Retry the claim
# with the same budget as orders-sync; persistent shed still 502s.
# A shed is fail-fast — `_claim_subprocess_slot` returns False with no awaits —
# so 2 retries cost ~21s of a 120s budget and 502'd while the journal showed
# general-lane slots freeing every few seconds. /flow-analysis/AMZN served a
# Jun 16 report through 2026-08-28 on exactly that. The deadline below is the
# real bound; this is the ceiling that keeps the loop finite.
FLOW_REPORT_SHED_RETRIES = 12
# NOT 8.0 like orders-sync: a shared constant put both retry chains on the
# same 8s grid, contending in lockstep against a lane already saturated. R-355.
FLOW_REPORT_SHED_RETRY_DELAY_SECS = 5.0
# Uncapped, the exponential doubling spends the whole budget on two sleeps.
_SHED_BACKOFF_CAP_SECS = 20.0
# A 20-session AMZN pull measured 81s end to end. Claiming a slot with less
# budget than that left burns the lane and the UW spend on a run that must
# time out, so the chain stops probing below it.
FLOW_REPORT_MIN_RUN_SECS = 90.0
# Total wall clock one scan may occupy a general-lane slot for, probing
# included. Worst case was 3 x 300 + 16 = 916s of saturation for the next
# caller. R-354. Sized to seat one real scan plus a retry window. No longer
# tied to the HTTP round trip: the edge cuts the browser at 30s either way, so
# `_scan_once_per_ticker` detaches the scan and this budget bounds the CACHE
# WRITE, not a response anyone is still waiting on.
FLOW_REPORT_TOTAL_DEADLINE_SECS = 225.0
_CAPACITY_SHED_MARKER = "subprocess capacity exhausted"

# One in-flight scan per ticker. Nothing deduped concurrent requests, so N
# browser tabs on the same symbol each claimed a slot. R-354.
_FLOW_REPORT_INFLIGHT: dict[str, "asyncio.Task"] = {}
_FLOW_REPORT_INFLIGHT_LOCK: Optional["asyncio.Lock"] = None


def _is_capacity_shed(error: Optional[str]) -> bool:
    return bool(error) and _CAPACITY_SHED_MARKER in error.lower()


def _flow_report_inflight_lock() -> "asyncio.Lock":
    """Created on first use: the loop does not exist at import time."""
    global _FLOW_REPORT_INFLIGHT_LOCK
    if _FLOW_REPORT_INFLIGHT_LOCK is None:
        _FLOW_REPORT_INFLIGHT_LOCK = asyncio.Lock()
    return _FLOW_REPORT_INFLIGHT_LOCK


async def _scan_once_per_ticker(ticker: str, scan) -> Any:
    """Collapse concurrent scans of one ticker onto a single detached run.

    Two properties, both load-bearing:

    Dedupe — N tabs on one symbol each claimed a general-lane slot, so the
    operator's own duplicates were part of the saturation that then shed the
    scan they were all waiting for.

    Detachment — Caddy bounds the app upstream at a 30s
    `response_header_timeout` and a 20-session AMZN pull measures 81s, so the
    browser request is always cut first. Shielding the scan from its caller's
    cancellation lets the cache write land anyway, so the next page load is
    fresh instead of replaying the same doomed scan.
    """
    lock = _flow_report_inflight_lock()
    async with lock:
        task = _FLOW_REPORT_INFLIGHT.get(ticker)
        if task is None or task.done():
            task = asyncio.create_task(scan())
            # Nobody may be awaiting when this settles; consume the outcome so
            # a detached failure is not an "exception was never retrieved" log.
            task.add_done_callback(
                lambda t: None if t.cancelled() else t.exception()
            )
            _FLOW_REPORT_INFLIGHT[ticker] = task
    try:
        return await asyncio.shield(task)
    finally:
        if task.done() and _FLOW_REPORT_INFLIGHT.get(ticker) is task:
            _FLOW_REPORT_INFLIGHT.pop(ticker, None)


async def _run_script_retrying_capacity(
    script: str,
    args: list[str],
    *,
    timeout: float,
    retries: int,
    delay_s: float,
    label: str,
    deadline_s: Optional[float] = None,
    min_run_s: Optional[float] = None,
) -> ScriptResult:
    """Re-claim a general-lane slot after a capacity shed.

    The claim is fail-fast. Peer scans often free a slot in seconds, so a
    bounded sleep-and-retry is the operator-facing equivalent of the
    orders-sync / flow-refresh wrappers. Real script failures do not retry.

    `min_run_s` is the shortest window the script can finish in. Once less
    than that remains, probing stops: a slot claimed too late burns the lane
    and the upstream spend on a run that is guaranteed to time out.
    """
    started = time.monotonic()

    def _budget_left() -> Optional[float]:
        if deadline_s is None:
            return None
        return deadline_s - (time.monotonic() - started)

    def _attempt_timeout() -> float:
        left = _budget_left()
        return timeout if left is None else max(1.0, min(timeout, left))

    def _can_seat_a_run(after_backoff: float) -> bool:
        left = _budget_left()
        if left is None:
            return True
        remaining = left - after_backoff
        return remaining > 0 and (min_run_s is None or remaining >= min_run_s)

    result = await run_script(script, args, timeout=_attempt_timeout())
    attempts = 0
    while (
        not result.ok
        and _is_capacity_shed(result.error)
        and attempts < retries
    ):
        # Exponential with jitter. A fixed delay meant every client shed in the
        # same instant retried in the same instant — synchronised waves against
        # a lane that is by definition already saturated. R-355.
        backoff = min(
            _SHED_BACKOFF_CAP_SECS,
            delay_s * (2 ** attempts) * (0.5 + random.random()),
        )
        if not _can_seat_a_run(backoff):
            logger.info(
                "%s: capacity shed and the %.0fs deadline no longer seats a run "
                "— giving up after %d attempt(s) rather than holding a lane slot",
                label, deadline_s, attempts + 1,
            )
            return ScriptResult(
                ok=False,
                data=None,
                error=(
                    f"{_CAPACITY_SHED_MARKER}: still shed after {attempts + 1} "
                    f"attempts within the {deadline_s:.0f}s budget"
                ),
            )
        attempts += 1
        logger.info(
            "%s: capacity shed — retry %d/%d in %.1fs",
            label,
            attempts,
            retries,
            backoff,
        )
        await asyncio.sleep(backoff)
        result = await run_script(script, args, timeout=_attempt_timeout())
    if not result.ok and _is_capacity_shed(result.error) and attempts >= retries > 0:
        # R-356: the client cannot otherwise tell a first shed from one the
        # server already proved persistent across its whole budget.
        result = ScriptResult(
            ok=False,
            data=None,
            error=(
                f"{_CAPACITY_SHED_MARKER}: still shed after {attempts + 1} attempts"
            ),
        )
    return result


# A shed is not a healthy run: ib_orders.py never spawned and neither
# open_orders nor executed_orders was touched. The heartbeat exists so a
# transient capacity shed does not trip the 10-min stale window (R-170), but
# an UNBOUNDED run of them must still become visible — otherwise a saturated
# lane leaves orders-sync green with a fresh timestamp over an orders table
# that has not moved, and a fill or a cancel inside that window is invisible.
# R-216.
ORDERS_SYNC_MAX_CONSECUTIVE_SHEDS = 3
_orders_sync_consecutive_sheds = 0


def _reset_orders_sync_shed_state() -> None:
    """Called after a successful sync, and by tests."""
    global _orders_sync_consecutive_sheds
    _orders_sync_consecutive_sheds = 0


async def _heartbeat_orders_sync_skip(reason: str) -> None:
    """Record a tick that could not spawn ib_orders.py — as a shed, not an OK.

    Capacity shed is R-170: the general lane is full, not a writer fault.
    Without any row, two consecutive 5-min sheds trip the 10-min stale window
    (2026-08-24 19:30Z page 60096761, 19m silent while IB stayed up). With a
    fabricated healthy row, a permanent shed is silent forever. So: a distinct
    non-ok state, and an escalation to `error` once the streak passes the
    ceiling. R-216.
    """
    global _orders_sync_consecutive_sheds
    _orders_sync_consecutive_sheds += 1
    streak = _orders_sync_consecutive_sheds
    escalated = streak > ORDERS_SYNC_MAX_CONSECUTIVE_SHEDS
    # "warn" is the repo's existing vocabulary (web/lib/serviceHealth.ts:16);
    # it is not "ok" and it is not yet a page.
    state = "error" if escalated else "warn"
    try:
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        await asyncio.to_thread(
            db_http.hrana_execute,
            SERVICE_HEALTH_UPSERT_SQL,
            service_health_upsert_args(
                "orders-sync",
                state,
                started_at=now,
                finished_at=now,
                error={
                    "message": (
                        f"orders sync shed for subprocess capacity "
                        f"({streak} consecutive): {reason}"
                    ),
                    "class": "capacity-shed",
                    "consecutive_sheds": streak,
                },
            ),
        )
    except Exception:
        logger.exception("orders-sync loop: skip heartbeat failed (%s)", reason)


async def _orders_sync_tick() -> None:
    """Refresh open orders from IB during market hours.

    Keeps the orders-sync service_health row fresh so the watchdog's
    intraday bucket (10-min window) does not fire stale alerts during the
    trading day. The actual work mirrors what POST /orders/refresh does:
    run ib_orders.py --sync via the recovery-aware subprocess helper,
    which persists open_orders / executed_orders and heartbeats the
    orders-sync service_health row via service_cycle.

    Guards (all must pass):
    - not test_mode          — never run subprocess syncs in unit tests
    - market hours open      — the watchdog window is intraday-only; no
                               need to run outside 09:30–16:00 ET weekdays
    - pool has a connection  — proxy for "IB Gateway authenticated"; if
                               the pool is fully disconnected we would
                               just burn the IB cooldown and log an error
    """
    if test_mode:
        return
    if not _is_market_open_now_et():
        return
    if not _pool_has_any_connection():
        logger.debug("orders-sync loop: pool disconnected — skipping tick")
        return
    logger.info("orders-sync loop: running ib_orders.py --sync")
    outcome = await _coordinated_orders_sync()
    attempts = 0
    while (
        not outcome.ok
        and _is_capacity_shed(outcome.error)
        and attempts < ORDERS_SYNC_SHED_RETRIES
    ):
        attempts += 1
        logger.info(
            "orders-sync loop: capacity shed — retry %d/%d in %.0fs",
            attempts,
            ORDERS_SYNC_SHED_RETRIES,
            ORDERS_SYNC_SHED_RETRY_DELAY_SECS,
        )
        await asyncio.sleep(ORDERS_SYNC_SHED_RETRY_DELAY_SECS)
        outcome = await _coordinated_orders_sync()
    if outcome.ok:
        logger.info("orders-sync loop: sync complete")
        _reset_orders_sync_shed_state()
        return
    if _is_capacity_shed(outcome.error):
        logger.warning(
            "orders-sync loop: sync shed for subprocess capacity; next tick retries"
        )
        await _heartbeat_orders_sync_skip("subprocess capacity exhausted")
        return
    logger.warning("orders-sync loop: sync failed: %s", outcome.error)


async def _orders_sync_loop(interval: float = ORDERS_SYNC_INTERVAL_SECS) -> None:
    """Autonomous market-hours orders refresh loop.

    Sleeps first so the initial page-load /orders/refresh call (fired
    by the Next.js /orders route a few seconds after startup) has
    already run before we kick off the first autonomous sync.
    """
    while True:
        await asyncio.sleep(interval)
        try:
            await _orders_sync_tick()
        except Exception:
            logger.exception("orders-sync loop: unhandled exception — continuing")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start IB pool and UW client on startup, tear down on shutdown."""
    global ib_pool, uw_available

    # Operator preferences BEFORE anything can place an order. bootstrap()
    # publishes stored values into os.environ so every subprocess we spawn
    # (ib_place_order.py above all) inherits the caps the operator set; without
    # it a restart silently reverts the placement funnel to env/defaults.
    # Only this long-lived process opts into background refreshes — short-lived
    # subprocesses read the inherited overlay instead of opening their own
    # Turso socket per invocation.
    app_preferences.enable_background_refresh(True)
    if await asyncio.to_thread(app_preferences.bootstrap):
        logger.info("Operator preferences loaded from Turso")
    else:
        logger.warning("Operator preferences unavailable; env and code defaults in force")

    if test_mode:
        logger.info("Radon API starting in test mode; IB Gateway and pool startup are disabled")
        uw_available = bool(os.environ.get("UW_TOKEN"))
        yield
        logger.info("Radon API test mode shut down")
        return

    # Ensure IB Gateway is running before connecting pool
    gw_status = await ensure_ib_gateway()
    logger.info("IB Gateway: %s", gw_status)

    # IB pool — connect_all() blocks ~25-30s per client when IB Gateway is
    # awaiting_2fa. Three clients × that timeout = ~80s lifespan stall,
    # which prevents uvicorn from binding port 8321 inside the deploy
    # script's 45s health-check window and triggers a false-positive
    # rollback. Kick connect off as a background task instead — routes
    # already tolerate a not-yet-connected pool, and /health exposes
    # pool + auth_state so operators can see "connecting" without us
    # blocking the listener.
    ib_pool = IBPool()
    app.state.ib_pool = ib_pool

    async def _connect_ib_pool() -> None:
        try:
            pool_status = await ib_pool.connect_all()
            logger.info("IB pool status: %s", pool_status)
        except Exception:
            logger.exception("IB pool background connect failed")

    lifecycle_tasks = [asyncio.create_task(_connect_ib_pool())]

    # Server-side 2FA-recovery heartbeat. Consumers now poll the read-only
    # /edge-health surface, so the mutating recovery path can no longer ride a
    # browser /health poll — drive it here on a fixed cadence instead.
    lifecycle_tasks.append(asyncio.create_task(_ib_recovery_heartbeat_loop()))

    # Autonomous orders-sync loop — keeps the orders-sync service_health row
    # fresh during market hours so the watchdog's intraday bucket (10-min
    # window) does not fire stale alerts when no browser has visited /orders.
    lifecycle_tasks.append(asyncio.create_task(_orders_sync_loop()))

    # UW client — just verify token exists
    uw_available = bool(os.environ.get("UW_TOKEN"))
    if not uw_available:
        logger.warning("UW_TOKEN not set — UW-dependent endpoints will fail")

    # Phase 6: lifespan warming hooks for CRI / GEX have been removed.
    # The Turso embedded replica keeps both reads sub-millisecond, and the
    # systemd timers in radon-services (Hetzner) or laptop launchd plists
    # (local mode) refresh the underlying snapshots on cadence — so the
    # FastAPI server no longer needs to bootstrap those caches at boot.
    # Journal reconciliation still runs once at startup because trade-fill
    # rehydration is lifecycle-bound, not periodic.
    lifecycle_tasks.append(asyncio.create_task(_warm_journal_reconciliation_on_startup()))
    lifecycle_tasks.append(asyncio.create_task(_warm_knowledge_embedder_on_startup()))

    try:
        yield
    finally:
        for task in lifecycle_tasks:
            task.cancel()
        await asyncio.gather(*lifecycle_tasks, return_exceptions=True)
        if ib_pool:
            await ib_pool.disconnect_all()
        logger.info("Radon API shut down")


app = FastAPI(title="Radon API", version="1.0.0", lifespan=lifespan)
app.include_router(historical_router)
app.include_router(preferences_router)
app.include_router(assistant_market_router)

# Explicit origin allowlist (was a `https://.*\.radon\.run` wildcard regex). The
# wildcard matched ANY *.radon.run subdomain, so a subdomain takeover of a stale
# host would have passed CORS. We control every legitimate origin, so an explicit
# list is more auditable and removes that bypass surface. allow_credentials stays
# False (default) — API auth is Bearer-JWT / trusted-local, never cookie-based.
# Extend RADON_CORS_EXTRA_ORIGINS (comma-separated) for new first-party apps.
_CORS_ALLOWED_ORIGINS = [
    "https://app.radon.run",
    "https://demo.radon.run",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
] + [
    origin.strip()
    for origin in os.environ.get("RADON_CORS_EXTRA_ORIGINS", "").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth middleware — protect all routes except /health and internal ticket validation
AUTH_EXEMPT_PATHS = {
    "/health",
    "/ws-ticket/validate",
    # NOTE: /docs + /openapi.json are deliberately NOT exempt. They remain
    # reachable from loopback/tailnet via the trusted-local bypass below (open
    # Swagger over an SSH tunnel), but a public caller through the Caddy
    # `handle_path /api/ib/*` mount now gets 401 instead of a full map of the
    # admin/exec/order endpoint surface (CWE-200 recon hardening).
    # Pure market-calendar math for the demo signup webhook (demo.radon.run).
    # Carries no secrets and no account data; the Next.js webhook calls it
    # server-to-server with no user JWT, so it cannot depend on Clerk auth.
    "/demo/trial-expiry",
}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Require Clerk JWT for all endpoints except exempted paths and localhost."""
    if request.url.path in AUTH_EXEMPT_PATHS:
        return await call_next(request)

    # Skip auth for genuine server-to-server calls from localhost or tailnet
    # (Next.js → FastAPI; cloud-thin laptop dev → Hetzner FastAPI over Tailscale).
    # Requests forwarded through the public reverse proxy are NOT trusted.
    # Checked BEFORE the JWKS-configured gate so a server-to-server call never
    # depends on Clerk being configured.
    if is_trusted_local_request(request):
        return await call_next(request)

    # API key auth — scoped to historical/contract endpoints only
    service_identity = verify_api_key(request)
    if service_identity:
        request.state.user = service_identity
        return await call_next(request)

    # FAIL CLOSED: an untrusted/public request that reached here has no
    # server-to-server bypass and no API key, so it MUST present a valid Clerk
    # JWT. If CLERK_JWKS_URL is unset we cannot verify one — that is a deploy
    # misconfiguration, NOT an open door. Returning call_next() here would make
    # all 47 routes (orders/place, pi/exec, admin/*) world-callable through the
    # public Caddy proxy on a single missing env var (the "middleware is the
    # perimeter" / world-callable-/api/* incident class). Deny with 503. The
    # only way to disable auth is the explicit, loud, dev-only opt-in below —
    # never set RADON_AUTH_DISABLED on a public deployment.
    if not os.environ.get("CLERK_JWKS_URL"):
        if os.environ.get("RADON_AUTH_DISABLED") == "1":
            return await call_next(request)
        return JSONResponse(
            status_code=503,
            content={"detail": "Authentication unavailable: server auth is not configured."},
        )

    try:
        payload = await verify_clerk_jwt(request)
        request.state.user = payload
    except HTTPException as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )

    return await call_next(request)


# DNS-rebinding guard, added LAST so it runs OUTSIDE auth — an attacker page at
# http://rebind.attacker.example re-resolved to 127.0.0.1 becomes same-origin
# with this API in the operator's browser, which would let it READ every
# loopback-trusted response (/health account ids, /portfolio/sync, /cash-flows).
# Pinning the Host header to the names we actually serve removes that: a rebound
# request carries the attacker's hostname and is refused before it reaches a
# route. Every real caller is covered — Caddy preserves the public hostname
# (app/demo.radon.run), loopback callers (Next.js, WS relay, watchdog,
# health daemon, deploy health-gate) send localhost/127.0.0.1, and the
# cloud-thin laptop sends either the tailnet name from scripts/cloud.sh or the
# tailnet IP literal (see _CanonicalHostForPin below). Extend with
# RADON_ALLOWED_HOSTS (comma-separated) rather than widening this list.
def parse_allowed_hosts_env(raw: str) -> List[str]:
    """Split RADON_ALLOWED_HOSTS, dropping the bare wildcard.

    Starlette treats `"*"` anywhere in allowed_hosts as "serve every Host",
    which turns the pin off completely. An env var is the wrong place to be
    able to do that: it is set on a host we do not review in code, and the
    failure is silent. Extending the pin is allowed; deleting it is not.
    """
    return [host.strip() for host in (raw or "").split(",") if host.strip() and host.strip() != "*"]


_ALLOWED_HOSTS = [
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "app.radon.run",
    "demo.radon.run",
    "ib-gateway",
    "*.ts.net",
] + parse_allowed_hosts_env(os.environ.get("RADON_ALLOWED_HOSTS", ""))
if "pytest" in sys.modules:
    # In-process test clients address the app by a made-up single-label name:
    # starlette's TestClient uses "testserver", httpx.ASGITransport fixtures here
    # use "test". A single-label name has no public DNS, so it cannot carry a
    # rebinding attack in production — but keep it test-only anyway.
    _ALLOWED_HOSTS.extend(["testserver", "test"])

# Tailscale hands every node an address out of the CGNAT block. The cloud-thin
# laptop reaches this API at the IP literal (radon-api.service documents
# `--host 0.0.0.0 ... 100.112.32.16:8321`), which TrustedHostMiddleware cannot
# express: it matches whole names or one leading "*." only, never a CIDR.
_TAILNET_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def split_host_header(raw_host: str) -> Tuple[str, str]:
    """Return (hostname, port) from a Host header value, lowercased.

    Host names are case-insensitive and an IPv6 literal arrives bracketed
    (`[::1]:8321`); TrustedHostMiddleware does neither, so it has to be told
    the canonical form.
    """
    value = (raw_host or "").strip().lower().rstrip(".")
    if value.startswith("["):
        closing = value.find("]")
        if closing == -1:
            return "", ""
        return value[1:closing], value[closing + 1:].lstrip(":")
    head, _, tail = value.partition(":")
    if ":" in tail:  # unbracketed IPv6 literal, no port
        return value, ""
    return head, tail


def is_pinned_ip_literal(hostname: str) -> bool:
    """True for the IP literals this API legitimately answers on.

    Loopback (the Next.js / relay / watchdog hop) and the Tailscale CGNAT range
    (the cloud-thin laptop). Allowing IP literals does not reopen DNS rebinding:
    a rebind needs a NAME whose resolution the attacker controls, and a literal
    resolves only to itself. Any other literal — a public address, a routable
    IPv6 — is not a caller we serve and falls through to the name pin.
    """
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified or address in _TAILNET_CGNAT


# Carried on the per-request ASGI scope (never on scope["state"], which some
# servers share across requests from the lifespan state).
_ORIGINAL_HOST_KEY = "radon_original_host"


class _RestoreOriginalHost:
    """Put the caller's Host header back once the pin has matched.

    Runs directly beneath the pin, so routes, redirects and logs see exactly the
    header the client sent — canonicalization is scoped to the matching step and
    changes nothing downstream.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        original = scope.get(_ORIGINAL_HOST_KEY)
        if original is not None:
            MutableHeaders(scope=scope)["host"] = original
        await self.app(scope, receive, send)


class _CanonicalHostForPin:
    """Rewrite Host into the one form TrustedHostMiddleware can match.

    Starlette lowercases nothing and reads the hostname as `host.split(":")[0]`,
    so `LOCALHOST:8321` misses every entry and `[::1]:8321` reads as `[`. This
    normalizes case and unwraps the literal forms before the pin sees them; an
    IP literal we serve is presented as `localhost` because a CIDR has no name
    pattern. Everything else is passed through so the name pin still decides.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # TrustedHostMiddleware pins websocket handshakes too, so canonicalize
        # both scope types or the pin would disagree with itself.
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = MutableHeaders(scope=scope)
        raw_host = headers.get("host", "")
        hostname, port = split_host_header(raw_host)
        if hostname:
            matchable = "localhost" if is_pinned_ip_literal(hostname) else hostname
            canonical = f"{matchable}:{port}" if port else matchable
            if canonical != raw_host:
                scope[_ORIGINAL_HOST_KEY] = raw_host
                headers["host"] = canonical
        await self.app(scope, receive, send)


# add_middleware prepends, so the last one added runs first: canonicalize ->
# pin -> restore -> auth -> routes.
app.add_middleware(_RestoreOriginalHost)
app.add_middleware(
    TrustedHostMiddleware, allowed_hosts=_ALLOWED_HOSTS, www_redirect=False
)
app.add_middleware(_CanonicalHostForPin)


# ---------------------------------------------------------------------------
# Secret scrubbing for error responses
# ---------------------------------------------------------------------------
# Error paths interpolate raw upstream text (str(exc), subprocess stderr,
# result.error) into HTTPException detail at ~64 sites. A libsql/Turso failure,
# an IB error, or a subprocess crash can carry the Turso URL, an auth token, or
# an IB account id in that text — which would then ride out to the client (the
# same information-disclosure class as the /health account-id leak). Rather than
# scrub at 64 call sites, scrub once at the single chokepoint every raised
# HTTPException flows through: a custom handler.
_SECRET_SCRUB_PATTERNS = [
    (re.compile(r"libsql://[^\s'\"]+", re.IGNORECASE), "[redacted-db-url]"),
    (re.compile(r"https://[a-z0-9.-]+\.turso\.io[^\s'\"]*", re.IGNORECASE), "[redacted-db-url]"),
    (re.compile(r"(auth[_-]?token|authorization|bearer)(\s*[=:]\s*)\S+", re.IGNORECASE), r"\1\2[redacted]"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]*"), "[redacted-jwt]"),
    (re.compile(r"\bU\d{6,}\b"), "[redacted-account]"),
    # Named provider-key prefixes: Anthropic (sk-ant-), Clerk/Stripe (sk_live_/sk_test_).
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{6,}"), "[redacted-key]"),
    (re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{6,}\b"), "[redacted-key]"),
    # IB Flex Web Service token + generic api-key ride in URL query strings
    # (t=<token>), not headers — the header patterns above never catch them.
    (re.compile(r"([?&](?:t|token|api[_-]?key)=)[^\s&'\"]+", re.IGNORECASE), r"\1[redacted]"),
]


def _scrub_secrets(value):
    """Redact Turso URLs, auth tokens/JWTs, and IB account ids from any string
    (recursing into dict/list detail payloads) before it reaches the client."""
    if isinstance(value, str):
        for pattern, repl in _SECRET_SCRUB_PATTERNS:
            value = pattern.sub(repl, value)
        return value
    if isinstance(value, dict):
        return {k: _scrub_secrets(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub_secrets(v) for v in value]
    return value


@app.exception_handler(StarletteHTTPException)
async def scrubbed_http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Replaces the default HTTPException handler so the detail (which often
    carries raw upstream error text) is scrubbed of secrets before it is
    serialized to the client. Status + headers are preserved."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": _scrub_secrets(exc.detail)},
        headers=getattr(exc, "headers", None),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_cache(path: Path) -> Optional[dict]:
    """Read a JSON cache file, return None if missing/corrupt."""
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _write_cache(path: Path, data: dict) -> None:
    """Write JSON to cache file atomically via temp file + os.replace().

    The Turso snapshot + service_health mirror that used to piggyback here
    lives in the scan subprocesses now (db/scan_mirror.py) — synchronous
    libsql writes on this process starved the event loop even from a worker
    thread. See feedback_no_sync_libsql_on_fastapi_event_loop.
    """
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp", prefix=".cache_")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, str(path))
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


ORDERS_EXECUTED_LOOKBACK_HOURS = 36


def _safe_db_json_object(raw: Any) -> Optional[dict[str, Any]]:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


async def _read_latest_portfolio_snapshot_from_db() -> dict[str, Any]:
    rows = await asyncio.to_thread(
        db_http.hrana_execute,
        """
        SELECT payload
        FROM portfolio_snapshots
        ORDER BY taken_at DESC
        LIMIT 1
        """,
        (),
    )
    if not rows:
        raise RuntimeError("Portfolio snapshot unavailable")
    payload = _safe_db_json_object(rows[0][0] if rows[0] else None)
    if payload is None:
        raise RuntimeError("Portfolio snapshot payload unavailable")
    return payload


async def _read_orders_snapshot_from_db() -> dict[str, Any]:
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=ORDERS_EXECUTED_LOOKBACK_HOURS)
    ).isoformat().replace("+00:00", "Z")
    open_rows_task = asyncio.to_thread(
        db_http.hrana_execute,
        """
        SELECT payload, updated_at
        FROM open_orders
        ORDER BY updated_at DESC
        """,
        (),
    )
    executed_rows_task = asyncio.to_thread(
        db_http.hrana_execute,
        """
        SELECT payload, fill_time
        FROM executed_orders
        WHERE fill_time >= ?
        ORDER BY fill_time DESC
        """,
        (cutoff,),
    )
    open_rows, executed_rows = await asyncio.gather(open_rows_task, executed_rows_task)

    from utils.working_orders import is_prior_session_day_order

    open_orders: list[dict[str, Any]] = []
    latest_open_sync = ""
    for row in open_rows:
        payload = _safe_db_json_object(row[0] if row else None)
        if payload is None:
            continue
        updated_at = str(row[1] or "") if len(row) > 1 else ""
        if is_prior_session_day_order(payload, updated_at):
            continue
        open_orders.append(payload)
        if updated_at > latest_open_sync:
            latest_open_sync = updated_at

    executed_orders: list[dict[str, Any]] = []
    latest_exec_sync = ""
    for row in executed_rows:
        payload = _safe_db_json_object(row[0] if row else None)
        if payload is None:
            continue
        executed_orders.append(payload)
        fill_time = str(row[1] or "") if len(row) > 1 else ""
        if fill_time > latest_exec_sync:
            latest_exec_sync = fill_time

    return {
        "last_sync": latest_open_sync or latest_exec_sync,
        "open_orders": open_orders,
        "executed_orders": executed_orders,
        "open_count": len(open_orders),
        "executed_count": len(executed_orders),
    }


def _today_et_str() -> str:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


def _is_market_open_now_et() -> bool:
    # Holiday-aware (market_calendar.is_market_open_et consults
    # scripts/config/market_holidays.json), so the orders-sync / portfolio loop
    # no longer treats mid-week holidays like Juneteenth as open. Falls back to
    # weekday + 09:30-16:00 ET only if the calendar import/file is unavailable.
    try:
        from utils.market_calendar import is_market_open_et
        return is_market_open_et()
    except Exception:
        from zoneinfo import ZoneInfo
        try:
            et = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))
        except Exception:
            et = datetime.now()
        if et.weekday() >= 5:
            return False
        minutes = et.hour * 60 + et.minute
        return 9 * 60 + 30 <= minutes <= 16 * 60


def _scan_time_to_et_date(scan_time: str) -> Optional[str]:
    try:
        ts = datetime.fromisoformat(scan_time.replace("Z", "+00:00"))
        from zoneinfo import ZoneInfo
        return ts.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    except Exception:
        return None


def _is_gex_cache_stale(data: Optional[dict], *, now_ts: Optional[float] = None, current_market_open: Optional[bool] = None, today_et: Optional[str] = None) -> bool:
    """Market-aware GEX staleness check mirrored from the Next route logic."""
    if not data or not isinstance(data, dict):
        return True

    scan_time = data.get("scan_time")
    if not isinstance(scan_time, str) or not scan_time:
        return True

    today_et = today_et or _today_et_str()
    current_market_open = _is_market_open_now_et() if current_market_open is None else current_market_open

    session_date = _scan_time_to_et_date(scan_time)
    if not session_date or session_date != today_et:
        return True

    if not current_market_open:
        return False

    try:
        scan_dt = datetime.fromisoformat(scan_time.replace("Z", "+00:00"))
        current_ts = now_ts if now_ts is not None else time.time()
        return (current_ts - scan_dt.timestamp()) > 60
    except Exception:
        return True


def _is_cri_cache_stale(data: Optional[dict], *, mtime_ms: Optional[float] = None, now_ts: Optional[float] = None, current_market_open: Optional[bool] = None, today_et: Optional[str] = None) -> bool:
    """Market-aware CRI staleness check mirrored from the Next route logic."""
    if not data or not isinstance(data, dict):
        return True

    today_et = today_et or _today_et_str()
    current_market_open = _is_market_open_now_et() if current_market_open is None else current_market_open

    data_date = data.get("date")
    if not isinstance(data_date, str) or data_date != today_et:
        return True

    market_open_flag = data.get("market_open")
    if market_open_flag is False and not current_market_open:
        return False

    if mtime_ms is None:
        scan_time = data.get("scan_time")
        if not isinstance(scan_time, str) or not scan_time:
            return True
        try:
            mtime_ms = datetime.fromisoformat(scan_time.replace("Z", "+00:00")).timestamp() * 1000
        except Exception:
            return True

    current_ms = (now_ts if now_ts is not None else time.time()) * 1000
    return (current_ms - mtime_ms) > 60_000


# market_state() reasons that mean the whole day is a non-session (as opposed
# to a trading day merely outside 09:30-16:00 ET). Unknown/new reasons fall
# through as trading days so the gate can only ever fail open.
_NON_TRADING_SESSION_REASONS = {"weekend", "static:holiday", "ibkr:closed"}


def _is_trading_session_today() -> bool:
    try:
        from utils.market_calendar import market_state
        return market_state()["reason"] not in _NON_TRADING_SESSION_REASONS
    except Exception:
        # Fail open: a calendar bug must never silently disable startup
        # reconciliation forever.
        return True


async def _warm_journal_reconciliation_on_startup() -> None:
    if not _is_trading_session_today():
        # Weekend/holiday restarts see IB's Friday-frozen residuals (e.g.
        # expired options with 0-qty rows) and would false-flag mismatches.
        logger.info("Journal startup reconcile skipped: not a trading session")
        return
    logger.info("Journal startup reconcile triggered")
    # raw=True: ib_reconcile.py emits a status report on stdout, not
    # JSON. The default runner crashes on the first '{' in the report.
    result = await run_script_raw("ib_reconcile.py", [], timeout=120)
    if result.ok:
        logger.info("Journal startup reconcile complete")
    else:
        logger.warning("Journal startup reconcile failed: %s", result.error)


async def _warm_knowledge_embedder_on_startup() -> None:
    """Load the ~67 MB fastembed ONNX model off the event loop at boot so the
    first /knowledge request after a deploy does not pay the cold load plus
    fastembed's Hugging Face cache checks in-request (2026-08-30 03:04Z).
    get_embedder caches the result (or None) for every later call."""
    if test_mode:
        return  # demo VM never serves the corpus; don't hold the model in RAM
    embedder = await asyncio.to_thread(get_embedder)
    logger.info(
        "knowledge: embedder %s", "warm" if embedder else "unavailable, FTS-only"
    )


# Phase 6: _warm_cri_cache_on_startup and _warm_gex_cache_on_startup were
# deleted alongside their lifespan-task call sites. The Turso embedded
# replica keeps both caches current without any FastAPI-side bootstrap;
# scheduled refreshes are owned by the radon-services container (Hetzner
# mode) or the laptop launchd plists (local mode).


def _atomic_save(path: str, data: dict) -> str:
    """Use the project's atomic_save for portfolio/orders files."""
    from utils.atomic_io import atomic_save
    return atomic_save(path, data)


def _coerce_float(value: object) -> Optional[float]:
    """Parse an arbitrary value into a finite float."""
    if isinstance(value, (int, float)):
        return float(value) if value == value and value != float("inf") and value != float("-inf") else None
    if isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return None
        return parsed if parsed == parsed and parsed not in (float("inf"), float("-inf")) else None
    return None


def _coerce_date(value: object) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        try:
            return datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return None


def _normalize_risk_reversal_series(raw: object) -> List[dict]:
    """Normalize UW historical risk reversal payloads into a stable list."""
    rows: Iterable[object] = []
    if isinstance(raw, dict):
        raw_rows = raw.get("data")
        if isinstance(raw_rows, list):
            rows = raw_rows
    elif isinstance(raw, list):
        rows = raw

    normalized: List[dict] = []
    seen_dates: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        date = row.get("date")
        value = row.get("risk_reversal")
        if not isinstance(date, str):
            continue
        numeric = _coerce_float(value)
        if numeric is None:
            continue
        # Skip invalid or duplicate dates; keep the latest row for a date.
        if date in seen_dates:
            continue
        seen_dates.add(date)
        normalized.append({"date": date, "value": numeric})

    normalized.sort(key=lambda item: item["date"])
    return normalized


def _extract_expiry_candidates(raw: object) -> List[str]:
    rows: Iterable[object] = []
    if isinstance(raw, dict):
        raw_rows = raw.get("data")
        if isinstance(raw_rows, list):
            rows = raw_rows
    elif isinstance(raw, list):
        rows = raw

    candidates: List[str] = []
    for row in rows:
        if isinstance(row, dict):
            expiry = row.get("expiry")
            if not isinstance(expiry, str):
                expiry = row.get("expires")
            if not isinstance(expiry, str):
                expiry = row.get("expiration")
            if isinstance(expiry, str) and expiry not in candidates:
                candidates.append(expiry)
    return candidates


def _pick_preferred_expiry(raw: object, now: Optional[datetime] = None) -> Optional[str]:
    """Choose the nearest expiry that is today or newer, else the most recent expiry."""
    candidates = _extract_expiry_candidates(raw)
    if not candidates:
        return None

    parsed: List[Tuple[str, datetime]] = []
    for expiry in candidates:
        parsed_date = _coerce_date(expiry)
        if parsed_date is None:
            continue
        parsed.append((expiry, parsed_date))

    if not parsed:
        return candidates[0]

    current = now or datetime.now(timezone.utc)
    future_candidates = [(expiry, expiry_date) for expiry, expiry_date in parsed if expiry_date.date() >= current.date()]
    if future_candidates:
        return min(future_candidates, key=lambda item: item[1])[0]
    return max(parsed, key=lambda item: item[1])[0]


def _normalize_expiry_string(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None

    parsed = _coerce_date(value)
    if parsed is not None:
        return parsed.date().isoformat()

    compact = value.strip()
    if len(compact) == 8 and compact.isdigit():
        try:
            return datetime.strptime(compact, "%Y%m%d").date().isoformat()
        except ValueError:
            return None

    return None


def _sort_expiry_candidates(expiries: Iterable[str], now: Optional[datetime] = None) -> List[str]:
    parsed: List[Tuple[str, datetime]] = []
    seen: set[str] = set()
    for expiry in expiries:
        normalized = _normalize_expiry_string(expiry)
        if normalized is None or normalized in seen:
            continue
        parsed_date = _coerce_date(normalized)
        if parsed_date is None:
            continue
        seen.add(normalized)
        parsed.append((normalized, parsed_date))

    if not parsed:
        return []

    current = now or datetime.now(timezone.utc)
    future = sorted(
        (item for item in parsed if item[1].date() >= current.date()),
        key=lambda item: item[1],
    )
    past = sorted(
        (item for item in parsed if item[1].date() < current.date()),
        key=lambda item: item[1],
        reverse=True,
    )
    return [expiry for expiry, _ in [*future, *past]]


def _extract_ib_expiry_candidates(raw: object) -> List[str]:
    rows: Iterable[object] = raw if isinstance(raw, list) else []
    candidates: List[str] = []
    for row in rows:
        expirations = getattr(row, "expirations", None)
        if not expirations:
            continue
        for expiry in expirations:
            normalized = _normalize_expiry_string(expiry)
            if normalized and normalized not in candidates:
                candidates.append(normalized)
    return candidates


async def _fetch_ib_expiry_candidates(ticker: str) -> List[str]:
    normalized_ticker = ticker.upper()
    if ib_pool is None:
        return []

    attempts = [
        ("NASDAQ", "IND"),
        ("CBOE", "IND"),
        ("SMART", "IND"),
        ("", "IND"),
    ]
    for exchange, sec_type in attempts:
        try:
            async with ib_pool.acquire("data") as client:
                chains = await _bounded_chain_fetch(
                    client,
                    normalized_ticker,
                    exchange,
                    sec_type,
                )
            candidates = _sort_expiry_candidates(_extract_ib_expiry_candidates(chains))
            if candidates:
                logger.info(
                    "Internals skew: IB expiries for %s resolved via %s/%s (%d candidates)",
                    normalized_ticker,
                    exchange or "default",
                    sec_type,
                    len(candidates),
                )
                return candidates
        except Exception as exc:
            logger.warning(
                "Internals skew: IB expiry lookup failed for %s via %s/%s: %s",
                normalized_ticker,
                exchange or "default",
                sec_type,
                exc,
            )
    return []


def _preferred_index_exchange(ticker: str) -> str:
    return "NASDAQ" if ticker.upper() == "NDX" else "CBOE"


# REL-013 / R-014: qualifyContracts + reqSecDefOptParams have no native
# timeout — a wedged gateway would hold the pool's data-role lock forever
# and strand one executor thread per exchange attempt. wait_for abandons
# the thread but releases the lock and bounds the request.
_CHAIN_FETCH_TIMEOUT_SECS = 15.0


async def _bounded_chain_fetch(
    client: Any, ticker: str, exchange: str, sec_type: str
):
    return await asyncio.wait_for(
        asyncio.to_thread(
            _fetch_ib_index_option_chain, client, ticker, exchange, sec_type
        ),
        timeout=_CHAIN_FETCH_TIMEOUT_SECS,
    )


def _fetch_ib_index_option_chain(client: Any, ticker: str, exchange: str, sec_type: str) -> object:
    if sec_type != "IND":
        return client.get_option_chain(ticker, exchange, sec_type)

    contract = Index(symbol=ticker, exchange=exchange or _preferred_index_exchange(ticker))
    qualified = client.qualify_contract(contract)
    return client.ib.reqSecDefOptParams(ticker, exchange, sec_type, qualified.conId)


def _prepend_expiry(candidates: List[str], expiry: Optional[str]) -> List[str]:
    normalized = _normalize_expiry_string(expiry)
    if normalized is None:
        return candidates
    return [normalized, *[candidate for candidate in candidates if candidate != normalized]]


def _limit_expiry_candidates(candidates: List[str], max_expiries: int) -> List[str]:
    if max_expiries <= 0 or len(candidates) <= max_expiries:
        return candidates
    if max_expiries == 1:
        return candidates[:1]

    last_index = len(candidates) - 1
    selected_indices = {0, last_index}
    for slot in range(1, max_expiries - 1):
        index = round(slot * last_index / (max_expiries - 1))
        selected_indices.add(index)

    return [candidates[index] for index in sorted(selected_indices)[:max_expiries]]


def _build_internals_skew_cache_path(
    nq_ticker: str,
    spx_ticker: str,
    timeframe: str,
    nq_delta: int,
    spx_delta: int,
    nq_expiry: Optional[str],
    spx_expiry: Optional[str],
) -> Path:
    key = (
        f"v7-uw-skew-history|{nq_ticker}|{spx_ticker}|{timeframe}|"
        f"{nq_delta}|{spx_delta}|{nq_expiry or ''}|{spx_expiry or ''}"
    )
    key_hash = hashlib.md5(key.encode()).hexdigest()[:16]
    return INTERNALS_SKEW_CACHE_DIR / f"internals_skew_history_{key_hash}.json"


def _read_internals_skew_cache(path: Path) -> Optional[dict]:
    cached = _read_cache(path)
    if not isinstance(cached, dict):
        return None

    generated_at = cached.get("generated_at")
    if not isinstance(generated_at, str):
        return None

    parsed = _coerce_date(generated_at)
    if parsed is None:
        return None

    age_seconds = (datetime.now(timezone.utc) - parsed.replace(tzinfo=timezone.utc)).total_seconds()
    if age_seconds > INTERNALS_SKEW_CACHE_TTL_SECONDS:
        return None
    return cached


def _internals_skew_cache_payload(
    nq_ticker: str,
    spx_ticker: str,
    timeframe: str,
    nq_delta: int,
    spx_delta: int,
    nq_expiry: Optional[str],
    spx_expiry: Optional[str],
    nq_rows: List[dict],
    spx_rows: List[dict],
    used_nq_expiries: List[str],
    used_spx_expiries: List[str],
) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "expiry_discovery": "Unusual Whales",
            "skew_history": "Unusual Whales",
        },
        "nq": {
            "ticker": nq_ticker.upper(),
            "expiry": used_nq_expiries[0] if used_nq_expiries else None,
            "expiries": used_nq_expiries,
            "delta": nq_delta,
            "timeframe": timeframe,
            "data": nq_rows,
        },
        "spx": {
            "ticker": spx_ticker.upper(),
            "expiry": used_spx_expiries[0] if used_spx_expiries else None,
            "expiries": used_spx_expiries,
            "delta": spx_delta,
            "timeframe": timeframe,
            "data": spx_rows,
        },
    }


def _merge_risk_reversal_series(series_rows: Iterable[List[dict]]) -> List[dict]:
    merged: dict[str, float] = {}
    for rows in series_rows:
        for row in rows:
            date = row.get("date")
            value = row.get("value")
            if not isinstance(date, str) or not isinstance(value, (int, float)):
                continue
            if date not in merged:
                merged[date] = float(value)
    return [{"date": date, "value": merged[date]} for date in sorted(merged)]


def _series_span_days(rows: List[dict]) -> int:
    if len(rows) < 2:
        return 0
    start = _coerce_date(rows[0].get("date"))
    end = _coerce_date(rows[-1].get("date"))
    if start is None or end is None:
        return 0
    return (end.date() - start.date()).days


def _needs_deeper_backfill(rows: List[dict], timeframe: str) -> bool:
    if not rows:
        return True
    span_days = _series_span_days(rows)
    normalized = timeframe.upper().strip()
    if normalized in {"5Y", "ALL"}:
        return span_days < 700
    if normalized == "2Y":
        return span_days < 400
    return False


async def _resolve_expiry_candidates(
    ticker: str,
    expiry: Optional[str] = None,
) -> Tuple[List[str], List[str], str]:
    normalized_ticker = ticker.upper()
    uw_candidates: List[str] = []
    try:
        with UWClient() as client:
            expiry_breakdown = client.get_expiry_breakdown(normalized_ticker)
        uw_candidates = _sort_expiry_candidates(_extract_expiry_candidates(expiry_breakdown))
    except Exception:
        uw_candidates = []

    uw_candidates = _prepend_expiry(uw_candidates, expiry)
    if uw_candidates:
        return [], uw_candidates, "uw"

    raise HTTPException(status_code=422, detail=f"No expiry available for {normalized_ticker}")


def _compose_expiry_candidates(
    ib_candidates: List[str],
    uw_candidates: List[str],
    max_expiries: int,
) -> List[str]:
    if not ib_candidates:
        return _limit_expiry_candidates(uw_candidates, max_expiries)
    if not uw_candidates:
        return _limit_expiry_candidates(ib_candidates, max_expiries)

    ib_budget = min(4, max_expiries)
    selected = _limit_expiry_candidates(ib_candidates, ib_budget)
    remaining = max_expiries - len(selected)
    if remaining <= 0:
        return selected

    uw_only = [candidate for candidate in uw_candidates if candidate not in selected]
    return selected + _limit_expiry_candidates(uw_only, remaining)


async def _fetch_risk_reversal_history(
    ticker: str,
    timeframe: str,
    delta: int,
    expiry: Optional[str] = None,
    max_expiries: int = 8,
) -> Tuple[List[dict], List[str], str]:
    normalized_ticker = ticker.upper()
    ib_candidates, uw_candidates, expiry_source = await _resolve_expiry_candidates(normalized_ticker, expiry)
    selected_candidates = _compose_expiry_candidates(ib_candidates, uw_candidates, max_expiries)

    last_error: Optional[BaseException] = None
    merged_rows: List[List[dict]] = []
    used_expiries: List[str] = []
    requested_expiry = _normalize_expiry_string(expiry)

    for candidate_expiry in selected_candidates:
        try:
            with UWClient() as client:
                payload = client.get_historical_risk_reversal_skew(
                    normalized_ticker,
                    expiry=candidate_expiry,
                    timeframe=timeframe,
                    delta=delta,
                )
            rows = _normalize_risk_reversal_series(payload)
            if rows:
                merged_rows.append(rows)
                used_expiries.append(candidate_expiry)
        except UWNotFoundError as exc:
            last_error = exc
            if requested_expiry and candidate_expiry == requested_expiry:
                continue
        except UWAPIError as exc:
            last_error = exc
            continue

    merged = _merge_risk_reversal_series(merged_rows)
    if "uw" in expiry_source and _needs_deeper_backfill(merged, timeframe):
        extra_candidates = _limit_expiry_candidates(
            [candidate for candidate in uw_candidates if candidate not in selected_candidates],
            12,
        )
        for candidate_expiry in extra_candidates:
            try:
                with UWClient() as client:
                    payload = client.get_historical_risk_reversal_skew(
                        normalized_ticker,
                        expiry=candidate_expiry,
                        timeframe=timeframe,
                        delta=delta,
                    )
                rows = _normalize_risk_reversal_series(payload)
                if rows:
                    merged_rows.append(rows)
                    used_expiries.append(candidate_expiry)
            except UWAPIError as exc:
                last_error = exc
                continue
        merged = _merge_risk_reversal_series(merged_rows)

    if merged:
        return merged, used_expiries, expiry_source

    if last_error is None:
        raise HTTPException(status_code=502, detail=f"Failed to fetch skew history for {normalized_ticker}")
    raise HTTPException(
        status_code=502,
        detail=getattr(last_error, "args", (f"Failed to fetch skew history for {normalized_ticker}",))[0],
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health(request: Request):
    # /health is auth-exempt and reachable from the public internet via Caddy's
    # `handle_path /api/ib/*`. Untrusted (proxied/public) callers get liveness
    # only — never IB auth/connection state, account IDs, restart backoff, or
    # internal topology. Short-circuit BEFORE check_ib_gateway so an internet
    # GET can't drive its pool-reconnect / heal side effects.
    if not is_trusted_local_request(request):
        return {"status": "ok"}

    pool_status = ib_pool.status() if ib_pool else None
    # Bound the probe: check_ib_gateway can block for tens of seconds when the
    # pool is reconnecting (see HEALTH_GATEWAY_PROBE_TIMEOUT_SECS). On timeout or
    # any error we fall back to a fast "degraded" gateway dict (probe_timed_out)
    # rather than hanging the endpoint. The payload SHAPE stays identical so the
    # web IBStatusContext / admin panel keep parsing the same keys. This
    # request is strictly observational: the unbounded
    # _ib_recovery_heartbeat_loop exclusively owns recovery, preventing page
    # polling from starting a reconnect cycle or a second 2FA path.
    try:
        gw = await asyncio.wait_for(
            check_ib_gateway(pool_status=pool_status, pool=None),
            timeout=HEALTH_GATEWAY_PROBE_TIMEOUT_SECS,
        )
    except Exception as exc:  # defensive: never hang or 500 /health
        timed_out = isinstance(exc, asyncio.TimeoutError)
        logger.warning(
            "/health gateway probe %s after %.1fs; returning degraded status",
            "timed out" if timed_out else f"raised {type(exc).__name__}",
            HEALTH_GATEWAY_PROBE_TIMEOUT_SECS,
        )
        gw = {
            "port_listening": False,
            "auth_state": "unknown",
            "service_state": "unknown",
            "container_state": "unknown",
            "upstream_dead": False,
            "probe_timed_out": True,
        }
    return {
        "status": "ok",
        "test_mode": test_mode,
        "ib_gateway": gw,
        "ib_pool": pool_status or {},
        "uw": uw_available,
    }


async def _measure_event_loop_lag_ms() -> float:
    """One timed call_soon roundtrip on the running loop (DUR-12).

    Microseconds when the loop is healthy; a loop starved by blocking work
    (the libsql dual-write wedge class) shows milliseconds-to-seconds.
    Never blocks — the await IS the measurement.
    """
    loop = asyncio.get_running_loop()
    queued_at = loop.time()
    woke = loop.create_future()
    loop.call_soon(woke.set_result, None)
    await woke
    return (loop.time() - queued_at) * 1000.0


@app.get("/health/lite")
async def health_lite():
    """Side-effect-free, account-free coarse IB state for high-frequency pollers
    (the standalone health daemon + the host-metrics sampler).

    Unlike /health, this passes pool=None: it must NEVER trigger
    handle_auth_state_transition / pool.reconnect_all(). The 2FA-recovery
    heartbeat deliberately stays on /health (driven by the operator's 15s admin
    poll); a frequently-polling daemon hitting a mutating endpoint would perturb
    the very recovery it observes. The payload is coarse on purpose — never
    managed_accounts (IBKR account IDs), ports, restart backoff, or topology.

    ``loop_lag_ms`` is the event-loop health signal the host-metrics sampler
    persists every minute (scripts/host_metrics_sampler.py).

    NOT in AUTH_EXEMPT_PATHS: the in-box daemon reaches it from loopback (covered
    by the bypass); public callers via Caddy /api/ib/health/lite get 401.
    """
    loop_lag_ms = await _measure_event_loop_lag_ms()
    pool_status = ib_pool.status() if ib_pool else None
    try:
        gw = await asyncio.wait_for(
            check_ib_gateway(pool_status=pool_status, pool=None),
            timeout=HEALTH_LITE_GATEWAY_PROBE_TIMEOUT_SECS,
        )
    except Exception as exc:  # never let a passive health probe consume a worker
        logger.warning(
            "/health/lite gateway probe %s after %.1fs; returning degraded status",
            "timed out" if isinstance(exc, asyncio.TimeoutError) else f"raised {type(exc).__name__}",
            HEALTH_LITE_GATEWAY_PROBE_TIMEOUT_SECS,
        )
        gw = {}
    return {
        "status": "ok",
        "auth_state": gw.get("auth_state", "unknown"),
        "service_state": gw.get("service_state", "unknown"),
        "upstream_dead": gw.get("upstream_dead", False),
        "port_listening": gw.get("port_listening", False),
        "loop_lag_ms": round(loop_lag_ms, 3),
    }


@app.post("/ws-ticket")
async def get_ws_ticket(payload: dict = Depends(verify_clerk_jwt)):
    """Issue a short-lived ticket for WebSocket authentication."""
    ticket = create_ticket(payload["sub"])
    return {"ticket": ticket}


@app.post("/ws-ticket/validate")
async def validate_ws_ticket(request: Request):
    """Validate a WebSocket ticket (called by the Node.js relay). Internal only."""
    body = await request.json()
    ticket = body.get("ticket", "")
    user_id = validate_ticket(ticket)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or expired ticket")
    return {"user_id": user_id}


@app.post("/demo/trial-expiry")
async def demo_trial_expiry(request: Request):
    """Compute the 3-trading-day demo trial window (demo.radon.run).

    Auth-exempt + secret-free: the Next.js user.created webhook posts a signup
    timestamp and gets back the ISO-ET start + 16:00-ET expiry of the Nth
    trading day, computed via the shared market calendar (weekends + US
    holidays + IBKR closures excluded). The calendar logic is Python-only, so
    the webhook delegates here rather than re-implementing it in TypeScript.
    """
    from utils.demo_trial import DEFAULT_TRADING_DAYS, trial_expiry_handler

    body = await request.json()
    start_iso_et = body.get("start_iso_et")
    if not start_iso_et:
        raise HTTPException(status_code=400, detail="start_iso_et is required")
    trading_days = body.get("trading_days", DEFAULT_TRADING_DAYS)
    try:
        trading_days = int(trading_days)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="trading_days must be an integer")
    try:
        return trial_expiry_handler(start_iso_et, trading_days)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _gateway_unit_controllable() -> bool:
    """True when THIS host owns the gateway lifecycle: systemd is present and
    the installed control helper (single 2FA-lease owner) exists. True on the
    Hetzner deployment, False on the laptop pointing at the remote gateway."""
    return (
        admin_services.is_systemd_available()
        and Path(admin_services.GATEWAY_CONTROL_PATH).exists()
    )


@app.post("/ib/restart")
async def ib_restart():
    """Restart IB Gateway via IBC service, then reconnect pool.

    Honors the restart backoff (1m → 60m capped) when prior attempts haven't
    completed login. Use POST /ib/reset-backoff after approving 2FA to retry
    immediately.

    Cloud mode on the gateway HOST delegates to the sanctioned systemd
    lifecycle unit (2026-07-26 incident: the operator page's restart button
    landed here and was refused unconditionally, while the watchdog stood
    down at its awaiting-2fa cap — leaving no working restart path). The
    helper owns the 2FA push lease and the latched-transition state machine;
    pool reconnect after auth is the recovery heartbeat's job
    (feedback_ib_pool_stuck_after_2fa).
    """
    if ib_gateway.is_cloud_mode() and _gateway_unit_controllable():
        action = await admin_services.control_unit(admin_services.GATEWAY_UNIT, "restart")
        if action.ok:
            return {
                "restarted": True,
                "gateway_mode": "cloud",
                "via": admin_services.GATEWAY_UNIT,
                "detail": action.detail,
                "note": "Gateway cycling — approve the IBKR Mobile 2FA push to complete login.",
            }
        raise HTTPException(
            status_code=503,
            detail={
                "restarted": False,
                "gateway_mode": "cloud",
                "via": admin_services.GATEWAY_UNIT,
                "reason": "2fa_push_in_flight"
                if action.returncode == admin_services.PUSH_LOCK_HELD_RC
                else "unit_restart_failed",
                "error": action.detail,
            },
        )

    result = await restart_ib_gateway(pool=ib_pool)
    if not result.get("restarted"):
        # Surface deferred (backoff) and unauthenticated outcomes as 503 so the
        # caller treats them as failure, but include the structured payload for
        # operator follow-up.
        raise HTTPException(status_code=503, detail=result)

    # Reconnect pool after Gateway restart
    if ib_pool:
        await ib_pool.disconnect_all()
        pool_status = await ib_pool.connect_all()
        result["pool"] = pool_status

    return result


@app.post("/ib/reset-backoff")
async def ib_reset_backoff():
    """Clear restart backoff state. Operator path: 'I just approved 2FA, try now'."""
    return reset_restart_backoff()


# ---------------------------------------------------------------------------
# Operator admin — service control (systemd-backed)
# ---------------------------------------------------------------------------

@app.get("/admin/services")
async def admin_services_list():
    """List radon-* systemd units with current load/active/sub state.

    On non-systemd hosts (laptop dev), returns the placeholder catalogue with
    ``supported=False`` so the UI can render a graceful "not controllable
    from here" state. Status payload is identical to the systemd path.
    """
    supported = admin_services.is_systemd_available()
    units = await admin_services.list_units_with_status()
    return {
        "supported": supported,
        "units": [u.to_dict() for u in units],
    }


@app.post("/admin/services/{unit}/{action}")
async def admin_service_action(unit: str, action: str):
    """Run ``systemctl <action> <unit>``. Allowlist-gated to radon-* units."""
    result = await admin_services.control_unit(unit, action)
    if not result.ok:
        if result.returncode == admin_services.PUSH_LOCK_HELD_RC:
            raise HTTPException(status_code=409, detail=result.to_dict())
        raise HTTPException(status_code=400 if result.returncode == -1 else 502, detail=result.to_dict())
    return result.to_dict()


@app.post("/admin/stack/restart")
async def admin_stack_restart():
    """Run the operator CLI's ``radon restart`` to cycle every radon-* unit.

    The TCP response may not survive the restart (FastAPI itself is one of
    the units cycled). Callers must only treat an explicit successful response
    as acceptance; a dropped request is indeterminate and status polling is
    required before a safe retry.
    """
    result = await admin_services.restart_full_stack()
    if not result.ok:
        if result.returncode == admin_services.PUSH_LOCK_HELD_RC:
            raise HTTPException(status_code=409, detail=result.to_dict())
        raise HTTPException(status_code=400 if result.returncode == -1 else 502, detail=result.to_dict())
    return result.to_dict()


# ---------------------------------------------------------------------------
# Phase 1: Stateless UW-only endpoints (subprocess-based)
# ---------------------------------------------------------------------------

@app.get("/uw/usage")
async def uw_usage():
    """Operator UW daily request budget from data/uw_budget.json."""
    from utils.uw_budget import usage_snapshot

    return usage_snapshot()


@app.post("/uw/usage/record")
async def uw_usage_record(
    count: int = 1,
    caller: str = "web",
    endpoint: str = "",
):
    """Mirror UW hits made outside UWClient into the shared daily budget.

    The Next.js route handlers fetch UW directly and used to increment
    nothing, leaving /uw/usage and the universe-scan brake blind to
    browsing-driven traffic (REL-036 / R-062). The flock write runs off the
    event loop.
    """
    from utils.uw_budget import record_hits, usage_snapshot

    if not (1 <= count <= 500):
        raise HTTPException(status_code=400, detail="count must be between 1 and 500")
    await asyncio.to_thread(
        record_hits, count, caller=caller or "web", endpoint=endpoint
    )
    return usage_snapshot()


FLOW_TAB_COOLDOWN_S = 3600  # hourly VPS timer + SCAN cache
_flow_tab_last: dict[str, float] = {}
_flow_tab_locks: dict[str, asyncio.Lock] = {}


async def _run_flow_tab(
    name: str,
    cache_name: str,
    script: str,
    args: list[str],
    *,
    timeout: int,
    force: bool,
    demo_key: str,
    demo_payload: dict,
):
    """Single-flight + 3600s cooldown for scanner / discover / flow-analysis.

    ``force=True`` spends UW again. The hourly wrapper always passes force:
    the cooldown equals the timer period, so a jittered early fire would
    otherwise be cache-served.
    """
    import time as _time

    if test_mode:
        return await demo_scan_response(demo_key, demo_payload)
    cache_path = DATA_DIR / cache_name
    lock = _flow_tab_locks.setdefault(name, asyncio.Lock())
    now = time.monotonic()
    if not force and now - _flow_tab_last.get(name, 0.0) < FLOW_TAB_COOLDOWN_S:
        cached = _read_cache(cache_path)
        if cached:
            return cached
    async with lock:
        if not force and time.monotonic() - _flow_tab_last.get(name, 0.0) < FLOW_TAB_COOLDOWN_S:
            cached = _read_cache(cache_path)
            if cached:
                return cached
        result = await run_script(script, args, timeout=timeout)
        if not result.ok:
            raise HTTPException(status_code=502, detail=result.error)
        if result.data and result.data.get("error"):
            raise HTTPException(status_code=400, detail=result.data["error"])
        _write_cache(cache_path, result.data)
        _flow_tab_last[name] = time.monotonic()
        return result.data


@app.post("/scan")
async def scan(force: bool = False):
    """Run watchlist scanner (scanner.py --top 25)."""
    workers = _bounded_env_int("RADON_SCANNER_WORKERS", 24)
    return await _run_flow_tab(
        "scanner",
        "scanner.json",
        "scanner.py",
        ["--top", "25", "--workers", str(workers)],
        timeout=120,
        force=force,
        demo_key="scanner",
        demo_payload={"scan_time": "", "results": []},
    )


@app.post("/discover")
async def discover(force: bool = False):
    """Run market-wide discovery. Scoring walk is 2 darkpool pages, min 3 alerts."""
    return await _run_flow_tab(
        "discover",
        "discover.json",
        "discover.py",
        ["--min-alerts", "3", "--dp-pages", "2"],
        timeout=180,
        force=force,
        demo_key="discover",
        demo_payload={"scan_time": "", "results": []},
    )


@app.post("/forecast/chronos")
async def forecast_chronos(request: Request):
    """Chronos-2 quantile forecast for a ticker flow-history metric."""
    if test_mode:
        return demo_disabled_payload("Chronos forecasting")
    body = await request.json()
    ticker = str(body.get("ticker", "")).upper()
    metric = str(body.get("metric", "flow_strength"))
    horizon = int(body.get("horizon", 10))
    lookback = int(body.get("lookback", 120))
    result = await run_script(
        "chronos_forecast.py",
        ["--ticker", ticker, "--metric", metric, "--horizon", str(horizon),
         "--lookback", str(lookback), "--json"],
        timeout=180,
    )
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    return result.data


@app.get("/backtest")
async def backtest_registry():
    """F12 — list the backtester strategy registry (which are wired)."""
    from backtest.strategies import list_strategies

    return {"strategies": list_strategies()}


def _execute_workflow_graph(graph: dict, confirm_order: bool) -> dict:
    """Run a workflow graph off the event loop and serialize the report.

    The executor is pure for the tested node paths; any external effect lives
    behind the patchable seams in ``workflow.nodes``. Order-emitting nodes block
    unless ``confirm_order`` is set — the OrderRiskGate confirmation seam.
    """
    from workflow.executor import WorkflowError, execute_graph

    try:
        report = execute_graph(graph, confirm_order=confirm_order)
    except WorkflowError as exc:
        return {"ok": False, "error": str(exc), "invalid": True}
    return {
        "ok": report.ok,
        "blocked_by": report.blocked_by,
        "blocked_gate": report.blocked_gate,
        "requires_confirmation": report.requires_confirmation,
        "steps": [
            {
                "node_id": step.node_id,
                "node_type": step.node_type,
                "rows_in": step.rows_in,
                "rows_out": step.rows_out,
                "blocked": step.blocked,
                "info": step.info,
            }
            for step in report.steps
        ],
        "final_rows": report.final_rows,
    }


_MAX_CONCURRENT_WORKFLOWS = 2
_active_workflows = 0


def _release_workflow_job(task: asyncio.Task) -> None:
    global _active_workflows
    _active_workflows = max(0, _active_workflows - 1)
    # A timed-out request no longer awaits the worker. Consume any eventual
    # exception so asyncio does not emit an unhandled-task warning.
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        pass


@app.post("/workflow/run")
async def workflow_run(request: Request):
    """F14 — execute an operator-authored flow-pipeline graph server-side.

    Body: ``{"graph": {nodes, edges}, "confirm_order": bool}``. Returns the
    serialized execution report. Order-emitting nodes require ``confirm_order``;
    a failing gate names the blocking node + gate.
    """
    body = await request.json()
    graph = body.get("graph")
    if not isinstance(graph, dict) or "nodes" not in graph:
        raise HTTPException(status_code=400, detail="body.graph {nodes, edges} required")
    confirm_order = bool(body.get("confirm_order", False))
    global _active_workflows
    if _active_workflows >= _MAX_CONCURRENT_WORKFLOWS:
        raise HTTPException(status_code=429, detail="workflow capacity exhausted")
    _active_workflows += 1
    task = asyncio.create_task(
        asyncio.to_thread(_execute_workflow_graph, graph, confirm_order)
    )
    released = False
    try:
        report = await asyncio.wait_for(asyncio.shield(task), timeout=31.0)
    except asyncio.TimeoutError as exc:
        task.add_done_callback(_release_workflow_job)
        released = True
        raise HTTPException(status_code=504, detail="workflow execution timed out") from exc
    finally:
        if not released:
            if task.done():
                _release_workflow_job(task)
            else:
                # Client cancellation must not leak the admission slot while
                # the shielded worker completes in its thread.
                task.add_done_callback(_release_workflow_job)
    if report.get("invalid"):
        raise HTTPException(status_code=400, detail=report.get("error", "invalid graph"))
    return report


_PAPER_PLACE_REQUIRED = ("ticker", "side", "order_type", "quantity")


@app.post("/paper/place")
async def paper_place(request: Request):
    """FU6 (B) — shadow-placement against current quotes.

    Body mirrors a single-leg order plus a market observation:
    ``{ticker, side, order_type, quantity, limit_price?, stop_price?,
    bid?, ask?, last?, fill_id?, account?}``. Runs the paper matcher + fills
    engine in a subprocess (``paper_place.py``) so the synchronous libsql write
    in ``paper.store`` never touches the uvicorn event loop. Returns the
    simulated fill (``status: filled|working``)."""
    body = await request.json()
    missing = [field for field in _PAPER_PLACE_REQUIRED if body.get(field) in (None, "")]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"paper/place requires: {', '.join(missing)}",
        )

    result = await run_script("paper_place.py", ["--spec", json.dumps(body)], timeout=30)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error or "paper placement failed")
    return result.data


@app.get("/backtest/{strategy}")
async def backtest_strategy(request: Request, strategy: str, refresh: bool = False):
    """F12 — latest walk-forward backtest run for a strategy.

    Returns the most recent persisted run from ``backtest_runs`` (bounded hrana
    read, off-loop). When none exists or ``refresh=true``, runs the subprocess
    (which persists the fresh run) and returns its result.
    """
    if not refresh:
        cached = await asyncio.to_thread(_load_latest_backtest_run, strategy)
        if cached is not None:
            return cached

    task = asyncio.create_task(run_script(
        "backtest_run.py", ["--strategy", strategy, "--persist"], timeout=180
    ))
    while not task.done():
        await asyncio.wait({task}, timeout=0.25)
        if await request.is_disconnected():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            raise HTTPException(status_code=499, detail="client disconnected")
    result = await task
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    return result.data


def _load_latest_backtest_run(strategy: str):
    """Bounded hrana read of the freshest backtest_runs payload, or None."""
    try:
        rows = db_http.hrana_execute(
            """
            SELECT payload FROM backtest_runs
            WHERE strategy = ?
            ORDER BY run_at DESC
            LIMIT 1
            """,
            (strategy,),
        )
    except Exception:
        return None
    if not rows:
        return None
    try:
        return json.loads(rows[0][0])
    except (TypeError, ValueError, IndexError):
        return None


@app.post("/flow-surprise")
async def flow_surprise(request: Request):
    """Flow-surprise residual: ranked watchlist, or one ticker if provided."""
    if test_mode:
        return demo_disabled_payload("Flow surprise")
    body = await request.json()
    metric = str(body.get("metric", "flow_strength"))
    top = int(body.get("top", 20))
    lookback = int(body.get("lookback", 250))
    args = ["--metric", metric, "--top", str(top), "--lookback", str(lookback)]
    ticker = body.get("ticker")
    if ticker:
        args += ["--ticker", str(ticker).upper()]
    result = await run_script("flow_surprise.py", args, timeout=240)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    return result.data


@app.post("/flow-analysis")
async def flow_analysis(force: bool = False):
    """Run portfolio flow analysis (flow_analysis.py)."""
    return await _run_flow_tab(
        "flow-analysis",
        "flow_analysis.json",
        "flow_analysis.py",
        [],
        timeout=120,
        force=force,
        demo_key="flow-analysis",
        demo_payload={"scan_time": "", "results": []},
    )


_TICKER_RE = re.compile(r"^[A-Z]{1,5}$")
_FLOW_REPORTS_DIR = DATA_DIR / "flow_reports"


@app.get("/flow-analysis/{ticker}")
async def get_flow_report(ticker: str):
    """Return the most recent flow report for a single ticker.

    Reads the cached report on disk; never triggers a fresh scan. The Next.js
    layer compares the cache age against `flowReportStaleness` to decide
    whether to issue a POST.
    """
    upper = ticker.upper()
    if not _TICKER_RE.match(upper):
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

    cache_path = _FLOW_REPORTS_DIR / f"{upper}.json"
    if not cache_path.exists():
        raise HTTPException(status_code=404, detail=f"No flow report cached for {upper}")
    try:
        return json.loads(cache_path.read_text())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read cache: {exc}")


@app.post("/flow-analysis/{ticker}")
async def run_flow_report(ticker: str):
    """Run a fresh flow scan for a single ticker, persist, and return."""
    upper = ticker.upper()
    if not _TICKER_RE.match(upper):
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

    if test_mode:
        cache_path = _FLOW_REPORTS_DIR / f"{upper}.json"
        if cache_path.exists():
            try:
                return json.loads(cache_path.read_text())
            except Exception:
                pass
        return demo_disabled_payload(f"Live flow analysis for {upper}")

    return await _scan_once_per_ticker(upper, lambda: _scan_and_cache(upper))


async def _scan_and_cache(upper: str) -> dict:
    """One flow scan for one ticker: run it, gate it, persist it, return it."""
    # 20 trading-day dark-pool history (flow_report DEFAULT_LOOKBACK_DAYS).
    # Cold liquid names paginate UW heavily; allow longer than the old 120s.
    # Capacity shed is retryable: the general lane is often full for seconds
    # because GET /informed-flow/{ticker} and hourly scans share it.
    result = await _run_script_retrying_capacity(
        "flow_report.py",
        [upper, "--days", "20"],
        timeout=300,
        retries=FLOW_REPORT_SHED_RETRIES,
        delay_s=FLOW_REPORT_SHED_RETRY_DELAY_SECS,
        label=f"flow-report {upper}",
        deadline_s=FLOW_REPORT_TOTAL_DEADLINE_SECS,
        min_run_s=FLOW_REPORT_MIN_RUN_SECS,
    )
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    if not result.data:
        raise HTTPException(status_code=502, detail="Flow report returned no data")
    if isinstance(result.data, dict) and result.data.get("error"):
        raise HTTPException(status_code=502, detail=result.data["error"])

    # Don't persist a structurally degraded report. The aggregate-only
    # check (2026-05-14 fix) caught reports where the WHOLE 5-day window
    # was zero, but it let through partial degradations where 1-2 days
    # silently swallowed a UW rate-limit and the OTHER days carried the
    # cross-day total past `> 0`. EWY surfaced 2026-05-15 with two recent
    # days showing "NO DATA" while UW was healthy — three good days
    # (1,499 total prints) waved the bad report through this guard.
    #
    # Two checks now:
    #   (a) aggregate `analysis.num_prints > 0` — the original guard
    #   (b) every per-day darkpool row covering a real trading day has
    #       `num_prints > 0`. If any trading-day slot is zero, we
    #       assume a per-day call was swallowed and refuse the cache
    #       write. The previous valid cache stays served.
    if not _flow_report_is_cacheable(result.data, upper):
        return result.data
    _write_cache(_FLOW_REPORTS_DIR / f"{upper}.json", result.data)
    return result.data


def _flow_report_is_cacheable(report: dict, ticker: str) -> bool:
    """Gate the flow-report cache write on structural validity.

    Refuses to write when the aggregate is empty OR any per-day darkpool
    row covering a real trading day shows zero prints. The latter is the
    signal that a per-day call was swallowed by `fetch_flow.py`'s retry
    layer — even after the 2026-05-15 narrowing, a sustained rate-limit
    can still bubble up as an empty day; that report is unsafe to cache.
    """
    aggregate_prints = (report.get("analysis") or {}).get("num_prints") or 0
    if aggregate_prints <= 0:
        logger.warning(
            "Skipping flow_reports cache for %s: aggregate num_prints=0 (transient UW failure)",
            ticker,
        )
        return False

    daily_rows = ((report.get("dark_pool") or {}).get("daily") or [])
    blank_dates: list[str] = []
    for row in daily_rows:
        date_str = (row.get("date") or "").strip()
        if not date_str:
            continue
        if (row.get("num_prints") or 0) > 0:
            continue
        # Only flag dates that are real US trading days. UW will legitimately
        # return [] for weekends / holidays / pre-data-availability dates.
        try:
            year, month, day = (int(p) for p in date_str.split("-"))
            from utils.market_calendar import _is_trading_day  # local import — keeps top tidy
            if _is_trading_day(datetime(year, month, day)):
                blank_dates.append(date_str)
        except Exception:
            continue

    if blank_dates:
        logger.warning(
            "Skipping flow_reports cache for %s: %d trading day(s) returned zero prints (%s) — likely partial UW outage",
            ticker, len(blank_dates), ",".join(blank_dates),
        )
        return False
    return True


@app.get("/attribution")
async def attribution():
    """Run portfolio attribution (portfolio_attribution.py --json)."""
    result = await run_script("portfolio_attribution.py", ["--json"], timeout=15)
    if not result.ok:
        raise HTTPException(status_code=500, detail=result.error)
    return result.data


# ---------------------------------------------------------------------------
# Phase 2: IB file-writer endpoints
# ---------------------------------------------------------------------------

@app.post("/portfolio/sync")
async def portfolio_sync():
    """Sync portfolio from IB and return the live payload.

    Scripts auto-allocate client IDs from subprocess range (20-49).
    Auto-restarts IB Gateway on ECONNREFUSED and retries once.
    """
    outcome = await _coordinated_portfolio_sync()
    if not outcome.ok:
        raise HTTPException(status_code=502, detail=outcome.error)
    return outcome.payload or {}


@app.post("/portfolio/background-sync", status_code=202)
async def portfolio_background_sync(bg: BackgroundTasks):
    """Fire-and-forget portfolio sync."""
    bg.add_task(_bg_sync_via_subprocess)
    return {"status": "accepted"}


async def _bg_sync_via_subprocess():
    """Background task: run ib_sync.py as subprocess with auto-recovery."""
    outcome = await _coordinated_portfolio_sync()
    if outcome.ok:
        logger.info("Background portfolio sync complete")
    else:
        logger.error("Background portfolio sync failed: %s", outcome.error)


@app.post("/orders/refresh")
async def orders_refresh():
    """Sync orders from IB, then return the Turso orders snapshot.

    Scripts auto-allocate client IDs from subprocess range (20-49).
    Auto-restarts IB Gateway on ECONNREFUSED and retries once.
    """
    if test_mode:
        return {"status": "ok", "orders": []}

    outcome = await _coordinated_orders_sync()
    if not outcome.ok:
        raise HTTPException(status_code=502, detail=outcome.error)
    return outcome.payload or {}


# ---------------------------------------------------------------------------
# Phase 3: IB order operations
# ---------------------------------------------------------------------------

# REL-005: accepted-placement timestamps for the per-minute rate cap, each
# tagged with the orderRef it was reserved for. In-process deque is
# deliberate: one FastAPI process owns all placement routes on a host, and a
# restart clearing the window is fail-open for at most one minute.
_order_rate_timestamps: deque = deque()


def _refuse_if_order_rate_exceeded(order_ref: Optional[str] = None) -> None:
    """Claim one slot in the per-minute placement budget.

    REL-026: `/orders/replace` reserves its slot BEFORE the destructive cancel
    loop, so passing the same `order_ref` again from the inner `orders_place`
    must not consume a second one — the replace is one placement, not two.
    """
    from order_limits import max_orders_per_min

    now = time.monotonic()
    while _order_rate_timestamps and now - _order_rate_timestamps[0][0] > 60.0:
        _order_rate_timestamps.popleft()
    if order_ref and any(ref == order_ref for _stamp, ref in _order_rate_timestamps):
        return
    cap = max_orders_per_min()
    if len(_order_rate_timestamps) >= cap:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "ORDER_RATE_LIMIT",
                "message": f"more than {cap} placements accepted in the last "
                           f"minute (RADON_MAX_ORDERS_PER_MIN) — refused",
            },
        )
    _order_rate_timestamps.append((now, order_ref))


def _refuse_if_order_limits_violated(params: dict) -> None:
    from order_limits import check_order_limits

    violation = check_order_limits(params)
    if violation:
        raise HTTPException(status_code=422, detail=violation)


def _refuse_if_trading_halted() -> None:
    """Kill switch (REL-004): fast 409 before any subprocess spawn.

    ib_place_order.place_order re-checks the flag (covers the workflow
    bridge that bypasses these routes); this route-level check just fails
    faster and cheaper.
    """
    from trading_halt import get_halt_state, is_trading_halted

    if is_trading_halted():
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TRADING_HALTED",
                "reason": get_halt_state().get("reason", "manual halt"),
            },
        )


_INDETERMINATE_PLACE_MARKERS = ("timed out", "not automatically retried")


def _is_indeterminate_place_failure(error: Optional[str]) -> bool:
    """True when a failed /orders/place attempt may still have reached IB:
    subprocess timeout after transmit, or the recovery path restarted the
    gateway and deliberately did not retry (non-idempotent). Pre-transmit
    infra failures (script missing, gateway unreachable before connect)
    are NOT indeterminate — the order never left the host."""
    message = (error or "").lower()
    return any(marker in message for marker in _INDETERMINATE_PLACE_MARKERS)


@app.post("/orders/place")
async def orders_place(request: Request):
    """Place an order via IB (on-demand connection, client_id=26)."""
    _refuse_if_trading_halted()
    body = await request.json()
    _refuse_if_order_limits_violated(body)
    # REL-006: mint the orderRef BEFORE the rate check so it survives a
    # SIGKILLed subprocess, and so a slot /orders/replace already reserved
    # for this ref is recognised instead of double-counted (REL-026).
    if not body.get("orderRef"):
        body["orderRef"] = f"radon-{uuid.uuid4().hex[:20]}"
    _refuse_if_order_rate_exceeded(body["orderRef"])
    if test_mode:
        order_id, perm_id = _next_test_order_ids()
        return {
            "status": "ok",
            "orderId": order_id,
            "permId": perm_id,
            "initialStatus": "Submitted",
            "message": "Order accepted in test mode",
            "echo": body,
        }

    order_ref = body["orderRef"]

    order_json = json.dumps(body)
    # 25s timeout accommodates: connect (~3s) + qualify (~2s) + place + the
    # 12s combo confirm-poll inside ib_place_order.py + finally-disconnect.
    # 15s was tight for combos and timed out before the script could surface
    # PendingSubmit-stuck rejections — the script then never wrote a result
    # and the route reported an "Invalid JSON output" or timeout. Combo
    # orders DAY-TIF outside RTH naturally sit longer in PendingSubmit, so
    # the script must be able to detect the no-confirm case and return an
    # error inside the FastAPI timeout window.
    result = await _run_ib_script_with_recovery(
        "ib_place_order.py", ["--json", order_json], timeout=25
    )
    if not result.ok:
        # SPX-02: log infra failures before raising so the reason survives journald
        logger.warning(
            "orders/place infra error for %s %s %s: %s",
            body.get("action", "?"),
            body.get("quantity", "?"),
            body.get("symbol", "?"),
            result.error,
        )
        # REL-006 / R-009 + REL-019: a killed placement subprocess or a
        # no-retry gateway-restart may have transmitted before failing —
        # record the attempt AND answer 504 ORDER_INDETERMINATE, never a
        # plain failure the caller would retry.
        if _is_indeterminate_place_failure(result.error):
            await record_order_event(
                "indeterminate",
                order_ref=order_ref,
                symbol=body.get("symbol"),
                action=body.get("action"),
                quantity=body.get("quantity"),
                limit_price=body.get("limitPrice"),
                detail={"error": result.error},
            )
            raise HTTPException(
                status_code=504,
                detail={
                    "code": "ORDER_INDETERMINATE",
                    "orderRef": order_ref,
                    "message": (
                        f"Placement failed after transmit may have occurred — "
                        f"outcome indeterminate. CHECK OPEN ORDERS (orderRef "
                        f"{order_ref}) before re-placing. Upstream: {result.error}"
                    ),
                },
            )
        raise HTTPException(status_code=502, detail=result.error)
    if result.data and result.data.get("status") == "error":
        # SPX-02: log the full structured detail (including ib_error_code / ib_error_text
        # from the grace-wait) so the reason survives journald even when IB Gateway
        # logs are encrypted (.ibgzenc).  Preserve the structured dict in the
        # HTTPException detail so radonFetch's coerceRadonErrorDetail can unwrap it
        # rather than collapsing to "[object Object]".
        error_detail = result.data
        logger.warning(
            "orders/place rejected by IB for %s %s %s: %s (ib_error_code=%s)",
            body.get("action", "?"),
            body.get("quantity", "?"),
            body.get("symbol", "?"),
            error_detail.get("message", "Order failed"),
            error_detail.get("ib_error_code"),
        )
        # REL-019: audit-trail the rejection (best-effort, never fails the response).
        await record_order_event(
            "rejected",
            order_ref=error_detail.get("orderRef") or body.get("orderRef"),
            order_id=error_detail.get("orderId"),
            perm_id=error_detail.get("permId"),
            symbol=body.get("symbol"),
            action=body.get("action"),
            quantity=body.get("quantity"),
            limit_price=body.get("limitPrice"),
            status="error",
            detail=error_detail,
        )
        raise HTTPException(status_code=502, detail=error_detail)
    # REL-019: audit-trail the successful submission (best-effort).
    data = result.data or {}
    await record_order_event(
        "submitted",
        order_ref=data.get("orderRef"),
        order_id=data.get("orderId"),
        perm_id=data.get("permId"),
        symbol=body.get("symbol"),
        action=body.get("action"),
        quantity=body.get("quantity"),
        limit_price=body.get("limitPrice"),
        status=data.get("initialStatus"),
    )
    return result.data


@app.post("/orders/whatif")
async def orders_whatif(request: Request):
    """Read-only IB margin preview (whatIfOrder, no transmit).

    Hits IB's pre-trade risk engine for the real initMargin of an order —
    including undefined-risk multi-leg combos where the client-side Reg-T
    estimate is unavailable — WITHOUT routing it. Authenticated (NOT
    auth-exempt): it clears middleware via is_trusted_local_request on the
    server-to-server hop; a public caller through Caddy correctly 401s.
    """
    body = await request.json()
    if test_mode:
        return {
            "status": "ok",
            "whatIf": True,
            "initMargin": 1234.56,
            "maintMargin": 1234.56,
            "equityWithLoanChange": -1234.56,
            "commission": 1.50,
            "commissionCurrency": "USD",
            "warning": None,
            "source": "ib",
            "echo": body,
        }

    order_json = json.dumps(body)
    # No confirm-poll: connect (~3s) + qualify (~2s) + whatIf (~2-4s). 12s ample.
    result = await _run_ib_script_with_recovery(
        "ib_place_order.py", ["--json", order_json, "--whatif"], timeout=12
    )
    if not result.ok:
        logger.warning(
            "orders/whatif infra error for %s %s: %s",
            body.get("symbol", "?"),
            body.get("action", "?"),
            result.error,
        )
        raise HTTPException(status_code=502, detail=result.error)
    if result.data and result.data.get("status") == "error":
        raise HTTPException(status_code=502, detail=result.data)
    return result.data


def _internal_json_request(payload: dict) -> Request:
    """Build an in-process request so the replace state machine reuses chokepoints."""
    encoded = json.dumps(payload).encode("utf-8")

    async def receive():
        return {"type": "http.request", "body": encoded, "more_body": False}

    return Request({"type": "http", "method": "POST", "headers": []}, receive)


@app.post("/orders/replace")
async def orders_replace(request: Request):
    """Preflight, cancel, and place a replacement with explicit partial state."""
    _refuse_if_trading_halted()
    body = await request.json()
    cancel_orders = body.get("cancelOrders")
    replacement = body.get("replaceOrder")
    if (
        not isinstance(cancel_orders, list)
        or not 1 <= len(cancel_orders) <= 8
        or not isinstance(replacement, dict)
    ):
        raise HTTPException(status_code=422, detail="Invalid replacement state-machine payload")
    for target in cancel_orders:
        if not isinstance(target, dict) or not (
            isinstance(target.get("orderId"), int) and target.get("orderId", 0) > 0
            or isinstance(target.get("permId"), int) and target.get("permId", 0) > 0
        ):
            raise HTTPException(status_code=422, detail="Invalid replacement cancel target")

    _refuse_if_order_limits_violated(replacement)
    if not replacement.get("orderRef"):
        replacement["orderRef"] = f"radon-replace-{uuid.uuid4().hex[:16]}"

    # REL-026: reserve the per-minute placement budget BEFORE anything
    # destructive. It used to be claimed inside orders_place, which runs after
    # the cancel loop — an exhausted budget left the operator's working orders
    # cancelled and the replacement refused, i.e. the position unhedged.
    _refuse_if_order_rate_exceeded(replacement["orderRef"])

    # Complete every non-transmitting validation before the first cancellation.
    await orders_whatif(_internal_json_request(replacement))

    # The what-if is the slow step and the kill switch exists for exactly the
    # minutes it spans — re-read the halt at the last instant before the first
    # cancellation rather than trusting the check at entry.
    _refuse_if_trading_halted()

    cancelled = []
    try:
        for target in cancel_orders:
            result = await orders_cancel(_internal_json_request(target))
            cancelled.append({
                "orderId": target.get("orderId"),
                "permId": target.get("permId"),
                "status": (result or {}).get("finalStatus", "cancelled"),
            })
        placed = await orders_place(_internal_json_request(replacement))
    except HTTPException as exc:
        status = exc.status_code if exc.status_code == 504 else 502
        raise HTTPException(
            status_code=status,
            detail={
                "code": "REPLACE_INDETERMINATE" if status == 504 else "REPLACE_PARTIAL",
                "phase": "placement" if len(cancelled) == len(cancel_orders) else "cancellation",
                "cancelled": cancelled,
                "replacementOrderRef": replacement["orderRef"],
                "upstream": exc.detail,
            },
        ) from exc

    return {
        **(placed or {}),
        "status": "ok",
        "cancelled": cancelled,
        "replacementOrderRef": replacement["orderRef"],
    }


@app.post("/orders/cancel")
async def orders_cancel(request: Request):
    """Cancel an open order via subprocess.

    IB scopes cancelOrder by clientId — only the clientId that placed the
    order can cancel it. The subprocess detects the original clientId and
    reconnects as that client before cancelling.
    """
    body = await request.json()
    if test_mode:
        return {
            "status": "ok",
            "message": "Cancel accepted in test mode",
            "echo": body,
        }

    order_id = body.get("orderId", 0)
    perm_id = body.get("permId", 0)

    args = ["cancel"]
    if order_id:
        args.extend(["--order-id", str(order_id)])
    if perm_id:
        args.extend(["--perm-id", str(perm_id)])

    result = await _run_ib_script_with_recovery("ib_order_manage.py", args, timeout=15)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    if result.data and result.data.get("status") == "error":
        raise HTTPException(status_code=502, detail=result.data.get("message", "Cancel failed"))
    # REL-019: audit-trail the successful cancel (best-effort).
    data = result.data or {}
    await record_order_event(
        "cancelled",
        order_id=data.get("orderId") or order_id or None,
        perm_id=perm_id or None,
        status=data.get("finalStatus"),
    )
    return result.data


async def _find_working_order(order_id: Any, perm_id: Any) -> Optional[dict]:
    """The working order's payload from the Turso snapshot, or None.

    Best-effort by construction (R-145): a just-placed order or a Turso blip
    must degrade to the old contract-quantity cap, never block a modify.
    """
    try:
        snapshot = await _read_orders_snapshot_from_db()
    except Exception as exc:  # noqa: BLE001
        logger.warning("orders/modify: could not read the working order: %s", exc)
        return None
    for payload in snapshot.get("open_orders", []):
        if perm_id and payload.get("permId") == perm_id:
            return payload
        if order_id and payload.get("orderId") == order_id:
            return payload
    return None


@app.post("/orders/modify")
async def orders_modify(request: Request):
    """Modify an open order via subprocess.

    Modify requires the original clientId that placed the order (IB scopes
    placeOrder by clientId). The subprocess detects the original clientId
    and reconnects as that client before modifying. Cancel can use the pool
    (master clientId=0 can cancel anything), but modify cannot.
    """
    _refuse_if_trading_halted()
    body = await request.json()
    if test_mode:
        return {
            "status": "ok",
            "message": "Modify accepted in test mode",
            "echo": body,
        }

    order_id = body.get("orderId", 0)
    perm_id = body.get("permId", 0)
    new_price = body.get("newPrice")
    new_quantity = body.get("newQuantity")
    outside_rth = body.get("outsideRth")

    # REL-005 / R-145: a working 1-lot must not be modifiable into a
    # 10,000-lot, AND the resized order must be measured on the working
    # order's real shape. `check_quantity_limit` hardcoded
    # `{"type": "option", "limitPrice": 0}`, so the notional and combo
    # max-loss branches were both skipped and `newPrice` was bounded only by
    # `> 0`. The snapshot read is best-effort: an unreadable working order
    # falls back to the contract-quantity cap, never to no cap.
    if new_quantity is not None or new_price is not None:
        from order_limits import check_modify_limits

        working_order = await _find_working_order(order_id, perm_id)
        violation = check_modify_limits(
            working_order, new_quantity=new_quantity, new_price=new_price
        )
        if violation:
            raise HTTPException(status_code=422, detail=violation)

    args = ["modify"]
    if order_id:
        args.extend(["--order-id", str(order_id)])
    if perm_id:
        args.extend(["--perm-id", str(perm_id)])
    if new_price is not None:
        args.extend(["--new-price", str(new_price)])
    if new_quantity is not None:
        args.extend(["--new-quantity", str(new_quantity)])
    if outside_rth is True:
        args.append("--outside-rth")
    elif outside_rth is False:
        args.append("--no-outside-rth")

    result = await _run_ib_script_with_recovery("ib_order_manage.py", args, timeout=15)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    if result.data and result.data.get("status") == "error":
        raise HTTPException(status_code=502, detail=result.data.get("message", "Modify failed"))
    # REL-019: audit-trail the successful modify (best-effort).
    data = result.data or {}
    await record_order_event(
        "modified",
        order_id=data.get("orderId") or order_id or None,
        perm_id=perm_id or None,
        limit_price=new_price,
        status=data.get("finalStatus"),
        detail={
            "newPrice": new_price,
            "newQuantity": new_quantity,
            "outsideRth": outside_rth,
        },
    )
    return result.data


# ---------------------------------------------------------------------------
# Kill switch (REL-004): halt/resume/status, mass-cancel, one-shot kill.
# ---------------------------------------------------------------------------

@app.get("/trading/status")
async def trading_status():
    from trading_halt import get_halt_state

    return get_halt_state()


@app.post("/trading/halt")
async def trading_halt_route(request: Request):
    from trading_halt import set_halt

    body = await request.json() if await request.body() else {}
    return set_halt(reason=body.get("reason", "manual halt"), actor="api")


@app.post("/trading/resume")
async def trading_resume():
    from trading_halt import clear_halt

    return clear_halt(actor="api")


async def _cancel_all_working_orders():
    """Run the master-client global cancel and surface the drain result."""
    result = await run_script("ib_cancel_all.py", [], timeout=30)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    if result.data and result.data.get("status") == "error":
        raise HTTPException(
            status_code=502,
            detail=result.data.get("message", "Global cancel failed"),
        )
    return result.data


@app.post("/orders/cancel-all")
async def orders_cancel_all(request: Request):
    """Cancel EVERY working order (master reqGlobalCancel + drain verify)."""
    body = await request.json() if await request.body() else {}
    if body.get("confirm") is not True:
        raise HTTPException(
            status_code=400,
            detail={"code": "CONFIRM_REQUIRED",
                    "message": "POST {\"confirm\": true} to cancel ALL working orders"},
        )
    return await _cancel_all_working_orders()


@app.post("/trading/kill")
async def trading_kill(request: Request):
    """The kill switch: halt new placements FIRST, then mass-cancel.

    Halt-before-cancel ordering means no new order can race in between
    the cancel sweep and the flag taking effect.
    """
    from trading_halt import set_halt

    body = await request.json() if await request.body() else {}
    halt_state = set_halt(
        reason=body.get("reason", "kill switch"), actor="api-kill"
    )
    cancel = await _cancel_all_working_orders()
    return {"halted": halt_state["halted"], "halt": halt_state, "cancel": cancel}


# ---------------------------------------------------------------------------
# Phase 4: Market data & long-running endpoints (subprocess-based)
# ---------------------------------------------------------------------------

@app.post("/cta/share")
async def cta_share():
    """Generate CTA X share report (4 cards + preview HTML). Returns output path."""
    result = await run_script("generate_cta_share.py", ["--json", "--no-open"], timeout=120)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    return result.data


@app.post("/journal/reconcile")
async def journal_reconcile():
    """Run IB reconciliation and persist the latest reconciliation log."""
    # ib_reconcile.py emits human-readable status on
    # stdout — use raw runner to avoid the JSON-parse crash.
    result = await run_script_raw("ib_reconcile.py", [], timeout=120)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    marker = "RADON_RECONCILIATION_SNAPSHOT="
    snapshot_at = next(
        (
            line[len(marker) :].strip()
            for line in result.stdout.splitlines()
            if line.startswith(marker)
        ),
        "",
    )
    if not snapshot_at:
        raise HTTPException(status_code=502, detail="Reconciliation snapshot was not persisted")
    return {"ok": True, "snapshot_at": snapshot_at}


@app.post("/journal/rehydrate")
async def journal_rehydrate(days: int = 365):
    """Removed. Journal recon is file ingest (flex_delivery_ingest / --from-file).

    Live fills stay on journal_sync. A page-driven SendRequest is how this
    token earned Flex 1025.
    """
    raise HTTPException(
        status_code=404,
        detail="Journal rehydrate is file-ingest only. Use journal_rehydrate.py --from-file.",
    )


# ── Intraday scan admission (regime / breadth / vcg / gex) ──────────
# One policy for the four routes the GET pages poll: a completed scan is
# served from cache for the cooldown, a failed scan is refused with 429 for
# the backoff. Re-spawning on every 5 s client poll after a 502 is what
# saturated the 2-vCPU host and starved the IB gateway on 2026-08-24.

SCAN_GATES: dict[str, ScanGate] = {
    name: ScanGate(name) for name in ("regime", "breadth", "vcg", "gex")
}
# Gates keyed by (scan, subject). A single shared "gex" gate meant a
# caller-supplied ticker could arm a 60 s failure backoff for EVERY ticker —
# repeating one bogus request held the real SPX panel dead — and, because
# gex.json holds exactly one ticker's payload, a successful NDX scan armed a
# cooldown whose cache read returned None for an SPX poll, so two tickers
# polled alternately spawned back-to-back 120 s subprocesses forever. R-217.
#
# The key is caller-supplied (/gex/scan takes any <=10-char alnum ticker) and
# the process lives for days, so the map needs a ceiling. Eviction is
# fail-CLOSED: only an IDLE gate is evictable, because re-minting an armed gate
# hands the next poll a COLD gate that bypasses the very cooldown/backoff that
# was dropped — R-217 re-entering through the eviction path. When every gate is
# armed, a novel subject is refused on the shared overflow gate instead of
# growing the map or displacing a live backoff. T-230.
MAX_SUBJECT_SCAN_GATES = 256

# ONE definition of the cri_scan child budget, shared by the 15-minute timer
# driver and the browser's /regime/scan. R-423.
from data_refresh import CRI_SCAN_TIMEOUT_SECS  # noqa: E402,PLC0415

# Saturation of the subject-gate map is a HOST condition, not a per-request
# failure: `_evict_idle_scan_gate` cannot evict while every tracked subject is
# inside the 120s cooldown, so it is sticky, and the only externally visible
# artifact was a 429 whose detail read "backing off after a failure". One record
# per burst, not one per request. R-424.
SCAN_GATE_SATURATION_REPORT_INTERVAL_S = 300.0
# None, NOT 0.0: `time.monotonic()` counts host uptime, so `0.0` reads as
# "reported at boot" and swallowed the whole first burst on any host less than
# SCAN_GATE_SATURATION_REPORT_INTERVAL_S old. The sentinel has to mean never.
_SCAN_GATE_SATURATION_REPORTED_AT: Optional[float] = None


def _scan_gate_overflow_detail() -> str:
    return (
        f"scan-gate map saturated: all {MAX_SUBJECT_SCAN_GATES} tracked subjects "
        "are cooling down or backing off, so a novel subject is refused rather "
        "than spawning a subprocess storm"
    )


def _write_scan_gate_saturation_row(detail: str) -> None:
    """DELIBERATELY log-only. R-424 asked for a `service_health` row as well.

    There is no honest name to write it under: `radon-api` is a UNIT, not a
    health name, and `check.py` only ever reads names in `SCHEDULED_SERVICES` —
    while `test_python_does_not_track_non_scheduled_services` requires every
    entry there to be `category: "scheduled"` in the web catalog, i.e. to have a
    CADENCE. Saturation has none: an absent row is the normal state, so a
    scheduled key for it would age to stale and page forever. The durable trace
    is this WARNING plus the 429 detail the caller actually sees; closing the
    row half needs an error-only catalog category, which does not exist yet.
    """
    logger.warning("%s", detail)


def _record_scan_gate_saturation() -> None:
    global _SCAN_GATE_SATURATION_REPORTED_AT

    detail = _scan_gate_overflow_detail()
    now = time.monotonic()
    reported_at = _SCAN_GATE_SATURATION_REPORTED_AT
    if (
        reported_at is not None
        and now - reported_at < SCAN_GATE_SATURATION_REPORT_INTERVAL_S
    ):
        return
    _SCAN_GATE_SATURATION_REPORTED_AT = now
    _write_scan_gate_saturation_row(detail)

_SUBJECT_SCAN_GATES: "OrderedDict[tuple[str, str], ScanGate]" = OrderedDict()
_OVERFLOW_SCAN_GATE = ScanGate("scan-overflow")


def _evict_idle_scan_gate() -> bool:
    """Drop the least-recently-used gate that is neither cooling down nor backing off."""
    victim = next(
        (
            key
            for key, gate in _SUBJECT_SCAN_GATES.items()
            if not gate.in_cooldown() and not gate.in_backoff()
        ),
        None,
    )
    if victim is None:
        return False
    del _SUBJECT_SCAN_GATES[victim]
    return True


def _scan_gate_for(scan: str, subject: str) -> ScanGate:
    key = (scan, subject.strip().upper())
    gate = _SUBJECT_SCAN_GATES.get(key)
    if gate is not None:
        _SUBJECT_SCAN_GATES.move_to_end(key)
        return gate
    if len(_SUBJECT_SCAN_GATES) >= MAX_SUBJECT_SCAN_GATES and not _evict_idle_scan_gate():
        # Every tracked subject is already cooling down or backing off, i.e.
        # the host is saturated. Admitting a novel subject here is exactly the
        # subprocess storm the gates exist to stop, so refuse it.
        _record_scan_gate_saturation()
        _OVERFLOW_SCAN_GATE.mark_failure()
        return _OVERFLOW_SCAN_GATE
    gate = ScanGate(f"{scan}:{key[1]}")
    _SUBJECT_SCAN_GATES[key] = gate
    return gate


def _reset_scan_gates() -> None:
    """Drop every per-subject gate (tests)."""
    _SUBJECT_SCAN_GATES.clear()
    _OVERFLOW_SCAN_GATE.reset()


async def _gated_scan(
    gate: ScanGate,
    read_cached: Callable[[], Optional[dict]],
    run: Callable[[], Awaitable[ScriptResult]],
    on_fresh: Callable[[ScriptResult], dict] = lambda result: result.data,
) -> dict:
    def _admit() -> Optional[dict]:
        # Cache FIRST. Checking the backoff first turned a single transient
        # failure into a hard 429 for 60 s even with a good cache written
        # seconds earlier sitting on disk — a blank panel where stale-but-
        # correct data was available. R-259.
        if gate.in_backoff() or gate.in_cooldown():
            cached = read_cached()
            if cached is not None:
                return cached
        if gate.in_backoff():
            # The overflow gate is SHARED and is marked failed the instant the
            # map saturates, so "backing off after a failure" named the wrong
            # cause — the caller's own scan never ran and never failed. A
            # dashboard widening its ticker list past the cap silently 429'd
            # every novel ticker with a message that pointed at the scan. R-424.
            detail = (
                _scan_gate_overflow_detail()
                if gate is _OVERFLOW_SCAN_GATE
                else f"{gate.name} scan backing off after a failure"
            )
            raise HTTPException(
                status_code=429,
                detail=detail,
                headers=gate.retry_after_header(),
            )
        return None

    # `is not None`, not truthiness: `_read_cache` returns whatever json.loads
    # produced, and an empty-object cache ({} — a truncated write, or a scan
    # that legitimately found no rows) is falsy. That reported a miss while the
    # cooldown said otherwise, so every 5 s poll fell through and spawned
    # another 120 s run_script — a permanent subprocess treadmill, invisible in
    # the response. R-259.
    hit = _admit()
    if hit is not None:
        return hit
    async with gate.lock:
        hit = _admit()
        if hit is not None:
            return hit
        result = await run()
        if not result.ok:
            gate.mark_failure()
            raise HTTPException(status_code=502, detail=result.error)
        try:
            payload = on_fresh(result)
        except HTTPException:
            gate.mark_failure()
            raise
        gate.mark_success()
        return payload


@app.post("/regime/scan")
async def regime_scan():
    """Run CRI scan (cri_scan.py --json). Cooldown + failure backoff via SCAN_GATES."""
    if test_mode:
        return await demo_scan_response("regime", {"scan_time": ""})

    def _persist(result: ScriptResult) -> dict:
        _write_cache(DATA_DIR / "cri.json", result.data)
        return result.data

    return await _gated_scan(
        SCAN_GATES["regime"],
        lambda: _read_cache(DATA_DIR / "cri.json"),
        # The SAME budget the timer child gets. At 120 the browser path
        # SIGKILLed exactly the slow-IB runs (60-103s) the timer budget was
        # raised to 180 to accommodate, which armed the regime scan gate for
        # 60s and 502'd the panel. R-423.
        lambda: run_script("cri_scan.py", ["--json"], timeout=CRI_SCAN_TIMEOUT_SECS),
        _persist,
    )


@app.post("/breadth/scan")
async def breadth_scan():
    """Run NYSE breadth scan (breadth_scan.py --json). Cooldown + failure backoff via SCAN_GATES.

    No --force: the script's off-hours cache gate serves the fresh cache
    without touching IB, so mount-time auto-syncs outside market hours cost
    zero IB quota. The script writes data/breadth.json atomically and mirrors
    its own Turso snapshot + service_health row; empty payloads are never
    cached, so the route returns stdout JSON without re-writing the cache here.
    """
    if test_mode:
        return await demo_scan_response("breadth-scan", {"scan_time": ""})
    return await _gated_scan(
        SCAN_GATES["breadth"],
        lambda: _read_cache(DATA_DIR / "breadth.json"),
        lambda: run_script("breadth_scan.py", ["--json"], timeout=120),
    )


# ── VCG (Volatility-Credit Gap) ─────────────────────────────────────

@app.post("/vcg/scan")
async def vcg_scan():
    """Run VCG scan (vcg_scan.py --json). Cooldown + failure backoff via SCAN_GATES."""
    if test_mode:
        return await demo_scan_response("vcg-scan", {"scan_time": ""})

    def _persist(result: ScriptResult) -> dict:
        _write_cache(DATA_DIR / "vcg.json", result.data)
        return result.data

    return await _gated_scan(
        SCAN_GATES["vcg"],
        lambda: _read_cache(DATA_DIR / "vcg.json"),
        lambda: run_script("vcg_scan.py", ["--json"], timeout=120),
        _persist,
    )


@app.post("/vcg/share")
async def vcg_share():
    """Generate VCG X share report (4 cards + preview HTML). Returns output path."""
    result = await run_script("generate_vcg_share.py", ["--json", "--no-open"], timeout=120)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    return result.data


# ── LEAP (IV Mispricing Scanner) ────────────────────────────────────

_leap_last_scan: float = 0.0
_leap_scan_lock: Optional[asyncio.Lock] = None
LEAP_COOLDOWN_S = 600  # 10 min — LEAP scans are slow + low-cadence
LEAP_PRESET_TIMEOUT_S = 3600
LEAP_TICKER_TIMEOUT_S = 300
_SCAN_TICKER_LIST_MAX = 25


def _parse_scan_tickers(raw: str, require_pairs: bool = False, dedupe: bool = True) -> list:
    """Validate + normalise a comma-separated ticker-scan query param.

    Raises HTTPException(400) on malformed symbols, oversized lists, or
    (for pair scanners) an odd symbol count.
    """
    tokens = [token.strip().upper() for token in (raw or "").split(",") if token.strip()]
    if dedupe:
        seen = set()
        unique = []
        for token in tokens:
            if token not in seen:
                seen.add(token)
                unique.append(token)
        tokens = unique
    for token in tokens:
        if not re.fullmatch(r"[A-Z]{1,6}", token):
            raise HTTPException(
                status_code=400,
                detail="tickers must be comma-separated 1-6 letter symbols",
            )
    if len(tokens) > _SCAN_TICKER_LIST_MAX:
        raise HTTPException(
            status_code=400,
            detail=f"too many tickers (max {_SCAN_TICKER_LIST_MAX})",
        )
    if require_pairs and tokens and len(tokens) % 2 != 0:
        raise HTTPException(
            status_code=400,
            detail="tickers must be an even number of symbols (consecutive pairs)",
        )
    return tokens


def _scan_cache_matches_preset(cached: Any, preset: str) -> bool:
    """True when a cached scan payload was produced by the requested preset.

    An "explicit"-universe cache (custom ticker scan) never satisfies a
    preset request; mirrors _theta_cache_matches_preset.
    """
    if not isinstance(cached, dict):
        return False
    universe = str(cached.get("universe") or "")
    preset_key = preset.lower()
    return universe.lower() in {f"preset:{preset_key}", f"fallback:{preset_key}"}


@app.post("/leap/scan")
async def leap_scan(preset: str = "largecaps", min_gap: float = 10.0, tickers: str = ""):
    """Run LEAP scan (leap_scanner_uw.py --preset X --json, or --tickers A,B).

    Default preset is the virtual `largecaps` universe (NDX+SPX). Scanning
    the full `indexes` union adds the Russell 2000 for ~4x the Unusual
    Whales requests, and those names rarely price a defined-risk LEAP. The
    scanner writes data/leap.json directly; stdout is text + a summary
    rather than JSON, so we ignore run_script's parsed payload and re-read
    the cache file after the subprocess completes. 600s cooldown stops
    accidental thrash on the Unusual Whales API; explicit ticker scans
    bypass it (cheap operator probes) and never advance it.
    """
    requested = _parse_scan_tickers(tickers)
    if test_mode:
        return await demo_scan_response(
            "leap-scan", {"scan_time": "", "min_gap": min_gap, "results": []}
        )
    global _leap_last_scan, _leap_scan_lock
    import time as _time
    if _leap_scan_lock is None:
        _leap_scan_lock = asyncio.Lock()
    now = time.monotonic()
    is_ticker_scan = bool(requested)
    if not is_ticker_scan and now - _leap_last_scan < LEAP_COOLDOWN_S:
        cached = _read_cache(DATA_DIR / "leap.json")
        if _scan_cache_matches_preset(cached, preset):
            return cached
    async with _leap_scan_lock:
        if not is_ticker_scan and time.monotonic() - _leap_last_scan < LEAP_COOLDOWN_S:
            cached = _read_cache(DATA_DIR / "leap.json")
            if _scan_cache_matches_preset(cached, preset):
                return cached
        workers = _bounded_env_int("RADON_LEAP_SCANNER_WORKERS", 16)
        if is_ticker_scan:
            args = [
                "--tickers", ",".join(requested),
                "--min-gap", str(min_gap),
                "--json",
                "--workers", str(workers),
            ]
            timeout = LEAP_TICKER_TIMEOUT_S
        else:
            args = [
                "--preset", preset,
                "--min-gap", str(min_gap),
                "--json",
                "--workers", str(workers),
            ]
            timeout = LEAP_PRESET_TIMEOUT_S
        result = await run_script("leap_scanner_uw.py", args, timeout=timeout)
        if not result.ok:
            raise HTTPException(status_code=502, detail=result.error)
        if not is_ticker_scan:
            _leap_last_scan = time.monotonic()
        # The leap scanner subprocess wrote the JSON cache atomically AND
        # recorded its own service_health[leap-scan] row (db/scan_mirror.py).
        cached = _read_cache(DATA_DIR / "leap.json")
        return cached or {
            "scan_time": "",
            "min_gap": min_gap,
            "universe": "explicit" if is_ticker_scan else f"preset:{preset}",
            "requested_tickers": requested,
            "results": [],
        }


# ── Theta Harvester (short-strangle theta scanner) ──────────────────

_theta_last_scan: float = 0.0
_theta_scan_lock: Optional[asyncio.Lock] = None
THETA_COOLDOWN_S = 3600  # 1h — matches radon-signals-refresh hourly ET cadence


# Route defaults mirror theta_harvester_scanner.py's MIN_DTE / MAX_DTE / no-credit-floor.
THETA_DEFAULT_MIN_DTE = 7
THETA_DEFAULT_MAX_DTE = 45


def _theta_results_have_earnings_field(cached: Any) -> bool:
    """True when the snapshot was written after earnings annotation shipped.

    Pre-feature rows omit the ``earnings`` key entirely. Those must not satisfy
    the scan cooldown — otherwise SCAN NDX after deploy returns a blank column
    until the cooldown window expires.
    """
    if not isinstance(cached, dict):
        return False
    results = cached.get("results") or []
    if not results:
        return True
    for row in results:
        if isinstance(row, dict) and "earnings" in row:
            return True
    return False


def _theta_cache_matches(
    cached: Any, preset: str, min_dte: int, max_dte: int, min_credit: float
) -> bool:
    """A cached snapshot may only satisfy the cooldown when BOTH the preset AND
    the search parameters match — otherwise a fresh DTE/credit search within the
    cooldown window would silently return the previous parameters' results."""
    if not isinstance(cached, dict):
        return False
    if not _theta_results_have_earnings_field(cached):
        return False
    universe = str(cached.get("universe") or "")
    preset_key = preset.lower()
    if universe.lower() not in {f"preset:{preset_key}", f"fallback:{preset_key}"}:
        return False
    params = cached.get("params") or {}
    try:
        return (
            int(params.get("min_dte")) == min_dte
            and int(params.get("max_dte")) == max_dte
            and float(params.get("min_credit")) == float(min_credit)
        )
    except (TypeError, ValueError):
        return False


@app.post("/theta-harvester/scan")
async def theta_harvester_scan(
    preset: str = "ndx100",
    limit: int = 0,
    ticker: str = "",
    min_dte: int = THETA_DEFAULT_MIN_DTE,
    max_dte: int = THETA_DEFAULT_MAX_DTE,
    min_credit: float = 0.0,
):
    """Run theta_harvester_scanner.py against a preset or explicit ticker.

    ``min_dte`` / ``max_dte`` bound the expiration window; ``min_credit`` is the
    minimum per-share strangle premium (0 = no floor). The script writes
    data/theta_harvester.json and records its own service_health row. Cooldown
    mirrors LEAP/GARCH to protect UW quotas and is keyed on preset + params.
    """
    global _theta_last_scan, _theta_scan_lock
    import time as _time
    ticker = ticker.upper().strip()
    if ticker and not re.fullmatch(r"[A-Z]{1,6}", ticker):
        raise HTTPException(status_code=400, detail="ticker must be 1-6 letters")
    if not (0 <= min_dte <= max_dte <= 400):
        raise HTTPException(status_code=400, detail="require 0 <= min_dte <= max_dte <= 400")
    if not (0.0 <= min_credit <= 1000.0):
        raise HTTPException(status_code=400, detail="min_credit must be between 0 and 1000")
    params_block = {"min_dte": min_dte, "max_dte": max_dte, "min_credit": min_credit}
    if test_mode:
        return await demo_scan_response(
            "theta-harvester",
            {
                "scan_time": "",
                "source": "Unusual Whales",
                "universe": f"preset:{preset}",
                "requested_tickers": [],
                "tickers_scanned": 0,
                "params": params_block,
                "candidates_found": 0,
                "theta_harvest_count": 0,
                "results": [],
            },
        )
    if _theta_scan_lock is None:
        _theta_scan_lock = asyncio.Lock()
    now = time.monotonic()
    is_ticker_scan = bool(ticker)
    if not is_ticker_scan and now - _theta_last_scan < THETA_COOLDOWN_S:
        cached = _read_cache(DATA_DIR / "theta_harvester.json")
        if _theta_cache_matches(cached, preset, min_dte, max_dte, min_credit):
            return cached
    async with _theta_scan_lock:
        if not is_ticker_scan and time.monotonic() - _theta_last_scan < THETA_COOLDOWN_S:
            cached = _read_cache(DATA_DIR / "theta_harvester.json")
            if _theta_cache_matches(cached, preset, min_dte, max_dte, min_credit):
                return cached
        workers = _bounded_env_int("RADON_THETA_SCANNER_WORKERS", 24)
        args = ["--json", "--workers", str(workers)]
        if is_ticker_scan:
            args.append(ticker)
        else:
            args.extend(["--preset", preset])
        if not is_ticker_scan and limit and limit > 0:
            args.extend(["--limit", str(limit)])
        args.extend([
            "--min-dte", str(min_dte),
            "--max-dte", str(max_dte),
            "--min-credit", str(min_credit),
        ])
        result = await run_script("theta_harvester_scanner.py", args, timeout=420)
        if not result.ok:
            raise HTTPException(status_code=502, detail=result.error)
        payload = result.data if isinstance(result.data, dict) else None
        scan_status = (payload or {}).get("scan_status")
        # A budget-blocked / coverage-failed scan never ran — stamping the 1h
        # cooldown would pin the stale snapshot for another hour (R-070).
        if not is_ticker_scan and not scan_status:
            _theta_last_scan = time.monotonic()
        if scan_status and payload is not None:
            return payload
        cached = _read_cache(DATA_DIR / "theta_harvester.json")
        return cached or {
            "scan_time": "",
            "source": "Unusual Whales",
            "universe": "explicit" if is_ticker_scan else f"preset:{preset}",
            "requested_tickers": [ticker] if is_ticker_scan else [],
            "tickers_scanned": 0,
            "params": params_block,
            "candidates_found": 0,
            "theta_harvest_count": 0,
            "results": [],
        }


# ── 7-Step Strength Confirmation scanner ───────────────────────────

_strength_last_scan: float = 0.0
_strength_scan_lock: Optional[asyncio.Lock] = None
STRENGTH_COOLDOWN_S = 3600  # 1h — matches radon-signals-refresh hourly ET cadence


def _strength_cache_matches_preset(cached: Any, preset: str) -> bool:
    if not isinstance(cached, dict):
        return False
    universe = str(cached.get("universe") or "")
    preset_key = preset.lower()
    return universe.lower() in {f"preset:{preset_key}", f"fallback:{preset_key}"}


@app.post("/strength-confirmation/scan")
async def strength_confirmation_scan(preset: str = "ndx100", limit: int = 0, ticker: str = ""):
    """Run strength_confirmation_scanner.py against a preset or explicit ticker.

    The script writes data/strength_confirmation.json and records its own
    service_health row. Ticker scans bypass preset cooldown for operator probes.
    """
    global _strength_last_scan, _strength_scan_lock
    import time as _time
    ticker = ticker.upper().strip()
    if ticker and not re.fullmatch(r"[A-Z]{1,6}", ticker):
        raise HTTPException(status_code=400, detail="ticker must be 1-6 letters")
    if test_mode:
        return await demo_scan_response(
            "strength-confirmation",
            {
                "scan_time": "",
                "source": "Unusual Whales + Radon regime caches",
                "universe": f"preset:{preset}",
                "requested_tickers": [],
                "tickers_scanned": 0,
                "candidates_found": 0,
                "confirmed_strength_count": 0,
                "results": [],
            },
        )
    if _strength_scan_lock is None:
        _strength_scan_lock = asyncio.Lock()
    now = time.monotonic()
    is_ticker_scan = bool(ticker)
    if not is_ticker_scan and now - _strength_last_scan < STRENGTH_COOLDOWN_S:
        cached = _read_cache(DATA_DIR / "strength_confirmation.json")
        if _strength_cache_matches_preset(cached, preset):
            return cached
    async with _strength_scan_lock:
        if not is_ticker_scan and time.monotonic() - _strength_last_scan < STRENGTH_COOLDOWN_S:
            cached = _read_cache(DATA_DIR / "strength_confirmation.json")
            if _strength_cache_matches_preset(cached, preset):
                return cached
        workers = _bounded_env_int("RADON_STRENGTH_SCANNER_WORKERS", 24)
        args = ["--json", "--workers", str(workers)]
        if is_ticker_scan:
            args.append(ticker)
        else:
            args.extend(["--preset", preset])
        if not is_ticker_scan and limit and limit > 0:
            args.extend(["--limit", str(limit)])
        result = await run_script("strength_confirmation_scanner.py", args, timeout=480)
        if not result.ok:
            raise HTTPException(status_code=502, detail=result.error)
        payload = result.data if isinstance(result.data, dict) else None
        scan_status = (payload or {}).get("scan_status")
        # A budget-blocked / coverage-failed scan never ran — stamping the 1h
        # cooldown would pin the stale snapshot for another hour (R-070).
        if not is_ticker_scan and not scan_status:
            _strength_last_scan = time.monotonic()
        if scan_status and payload is not None:
            return payload
        cached = _read_cache(DATA_DIR / "strength_confirmation.json")
        return cached or {
            "scan_time": "",
            "source": "Unusual Whales + Radon regime caches",
            "universe": "explicit" if is_ticker_scan else f"preset:{preset}",
            "requested_tickers": [ticker] if is_ticker_scan else [],
            "tickers_scanned": 0,
            "candidates_found": 0,
            "confirmed_strength_count": 0,
            "results": [],
        }


# ── Market calendar (IBKR-sourced trading schedule) ─────────────────


@app.post("/market-calendar/refresh")
async def market_calendar_refresh():
    """Pull the IBKR trading calendar (SPY liquidHours) and refresh the cache.

    fetch_market_calendar.py merges the rolling window into
    data/market_calendar.json (the SoT the relay + market_state() resolver read)
    and emits the summary JSON we return. The daily radon-market-calendar timer
    targets this route.
    """
    result = await run_script("fetch_market_calendar.py", [], timeout=60)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    return result.data


@app.get("/market-calendar")
async def market_calendar_get():
    """Return the cached IBKR-sourced calendar. 200 + ``missing`` flag when the
    cache hasn't been written yet (never 4xx for a legitimate empty state)."""
    cached = _read_cache(DATA_DIR / "market_calendar.json")
    if not cached:
        return {"missing": True, "source": None, "days": {}}
    return cached


# ── Catalysts (F3 — earnings / FDA / economic) ──────────────────────


@app.get("/catalysts")
async def catalysts_get(limit: int = 50):
    """Return the cached catalyst feed (earnings / FDA / economic).

    Written by ``scripts/fetch_catalysts.py`` to ``data/catalysts.json``.
    200 + ``missing`` flag when the cache hasn't been written yet (never 4xx
    for a legitimate empty state). ``limit`` caps the returned row count;
    rows are already sorted nearest-first by the scan.
    """
    cached = _read_cache(DATA_DIR / "catalysts.json")
    if not cached:
        return {"missing": True, "scan_time": None, "count": 0, "catalysts": []}
    rows = cached.get("catalysts", [])
    if isinstance(limit, int) and limit > 0:
        rows = rows[:limit]
    return {
        "scan_time": cached.get("scan_time"),
        "count": len(rows),
        "catalysts": rows,
    }


# ── Earnings dates (next report / within-DTE for scanners) ───────────

_EARNINGS_DATES_DIR = DATA_DIR / "earnings_dates"
_EARNINGS_BATCH_MAX = 50
_EARNINGS_TIMEOUT_S = 60.0
_EARNINGS_BATCH_TIMEOUT_S = 180.0


def _earnings_missing_payload(ticker: str, dte: Optional[int]) -> dict:
    """200 body when UW has no upcoming earnings (never 4xx for missing)."""
    return {
        "ticker": ticker.upper(),
        "report_date": None,
        "report_time": None,
        "days_until": None,
        "source": None,
        "expected_move_pct": None,
        "within_dte": None,
        "dte": dte,
        "missing": True,
    }


def _parse_earnings_tickers(raw: str) -> list[str]:
    """Validate comma-separated tickers for GET /earnings batch (cap 50)."""
    tokens = [t.strip().upper() for t in (raw or "").split(",") if t.strip()]
    if not tokens:
        raise HTTPException(status_code=400, detail="tickers is required")
    seen: set[str] = set()
    unique: list[str] = []
    for token in tokens:
        if not _TICKER_RE.match(token):
            raise HTTPException(status_code=400, detail="Invalid ticker symbol")
        if token not in seen:
            seen.add(token)
            unique.append(token)
    if len(unique) > _EARNINGS_BATCH_MAX:
        raise HTTPException(
            status_code=400,
            detail=f"too many tickers (max {_EARNINGS_BATCH_MAX})",
        )
    return unique


def _normalize_earnings_batch(data: Any) -> list:
    """CLI returns a dict for one ticker and a list for many."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    return []


@app.get("/earnings")
async def earnings_batch_get(
    tickers: str = Query(..., description="Comma-separated tickers (max 50)"),
    dte: Optional[int] = Query(None, description="Structure DTE window"),
):
    """Batch next-earnings annotate via ``scripts/earnings_dates.py``.

    ``GET /earnings?tickers=AAPL,HONA&dte=16``. Cap 50 tickers. 200 with
    per-row ``missing`` when UW has no upcoming date; 400 only for invalid
    symbols / empty / oversized lists.
    """
    symbols = _parse_earnings_tickers(tickers)
    args: list[str] = [*symbols]
    if dte is not None:
        args.extend(["--dte", str(dte)])
    args.append("--json")

    result = await run_script(
        "earnings_dates.py", args, timeout=_EARNINGS_BATCH_TIMEOUT_S
    )
    if result.ok and result.data is not None and not (
        isinstance(result.data, dict) and result.data.get("error")
    ):
        rows = _normalize_earnings_batch(result.data)
        return {"results": rows, "count": len(rows), "dte": dte}

    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    return {
        "results": [_earnings_missing_payload(t, dte) for t in symbols],
        "count": len(symbols),
        "dte": dte,
    }


@app.get("/earnings/{ticker}")
async def earnings_get(
    ticker: str,
    dte: Optional[int] = Query(None, description="Structure DTE window"),
):
    """Next earnings date / session / within-DTE for one ticker.

    Runs ``scripts/earnings_dates.py TICKER [--dte N] --json`` via the
    subprocess bridge (same pattern as informed-flow). 200 + ``missing``
    when UW has no upcoming row; on-disk cache under
    ``data/earnings_dates/{TICKER}.json`` is the fallback on live failure.
    Never 4xx for legitimate missing earnings — only 400 for invalid ticker.
    """
    upper = ticker.upper().strip()
    if not _TICKER_RE.match(upper):
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

    args: list[str] = [upper]
    if dte is not None:
        args.extend(["--dte", str(dte)])
    args.append("--json")

    result = await run_script(
        "earnings_dates.py", args, timeout=_EARNINGS_TIMEOUT_S
    )
    if result.ok and isinstance(result.data, dict) and not result.data.get("error"):
        return result.data

    cached = _read_cache(_EARNINGS_DATES_DIR / f"{upper}.json")
    if cached and isinstance(cached, dict):
        return cached

    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    if isinstance(result.data, dict):
        return result.data
    return _earnings_missing_payload(upper, dte)


# ── Informed flow (F4 — congress / insider / institutional) ─────────

_INFORMED_FLOW_DIR = DATA_DIR / "informed_flow"


@app.get("/informed-flow/{ticker}")
async def informed_flow_get(ticker: str):
    """Return the congress + insider + institutional surface for a ticker.

    Runs ``scripts/fetch_informed_flow.py`` via the subprocess bridge (live UW
    fetch with per-source failure tolerance), persists the per-ticker cache,
    and returns the normalised payload. 200 + ``missing`` flag for a
    structurally empty surface (never 4xx for a legitimate empty state); the
    on-disk cache stays the fallback when the live fetch fails.
    """
    upper = ticker.upper()
    if not _TICKER_RE.match(upper):
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

    result = await run_script("fetch_informed_flow.py", [upper, "--json"], timeout=60)
    if result.ok and isinstance(result.data, dict) and not result.data.get("error"):
        return result.data

    cached = _read_cache(_INFORMED_FLOW_DIR / f"{upper}.json")
    if cached:
        return cached
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    return {
        "ticker": upper,
        "missing": True,
        "congress_trades": [],
        "insider_trades": [],
        "institutional_summary": None,
    }


# ── Event odds (F5 — Polymarket overlay) ────────────────────────────

_EVENT_ODDS_DIR = DATA_DIR / "event_odds"


@app.get("/event-odds/{ticker}")
async def event_odds_get(ticker: str):
    """Return the Polymarket event-odds overlay for a ticker.

    Runs ``scripts/fetch_event_odds.py`` via the subprocess bridge (live
    Polymarket fetch + options-skew compare with per-source failure tolerance),
    persists the per-ticker cache, and returns the overlay payload. 200 +
    ``missing`` flag for a ticker with no mapped markets (never 4xx for a
    legitimate empty state); the on-disk cache stays the fallback when the live
    fetch fails.
    """
    upper = ticker.upper()
    if not _TICKER_RE.match(upper):
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

    result = await run_script("fetch_event_odds.py", [upper, "--json"], timeout=60)
    if result.ok and isinstance(result.data, dict) and not result.data.get("error"):
        return result.data

    cached = _read_cache(_EVENT_ODDS_DIR / f"{upper}.json")
    if cached:
        return cached
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    return {"ticker": upper, "missing": True, "overlays": []}


# ── Index options chain (Phase 3 — VIX et al.) ──────────────────────

_INDEX_OPTIONS_CHAIN_TIMEOUT_S = 45.0  # patched in tests


@app.get("/index-options/chain")
async def index_options_chain(symbol: str, expiry: str = ""):
    """List CBOE-listed index option contracts for `symbol`.

    Subprocess-backed (ib_chain.py --kind option) for the same reason
    /futures/chain is — cross-thread event loop deadlock on the pool's
    data client when result sets exceed ~50 contracts. VIX/SPX/NDX
    chains routinely return 1000+ contracts when expiry is unscoped.
    """
    from clients.contract_resolver import supports_index_options

    symbol_upper = symbol.upper()
    if not supports_index_options(symbol_upper):
        raise HTTPException(
            status_code=400,
            detail=f"index options not supported for {symbol_upper}; supported: VIX, SPX, NDX, RUT, XSP",
        )

    args = ["--kind", "option", "--symbol", symbol_upper]
    if expiry:
        args.extend(["--expiry", expiry])

    result = await run_script("ib_chain.py", args, timeout=_INDEX_OPTIONS_CHAIN_TIMEOUT_S)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    if result.data and result.data.get("error"):
        raise HTTPException(status_code=502, detail=result.data["error"])
    return result.data or {
        "symbol": symbol_upper,
        "exchange": "CBOE",
        "tradingClass": symbol_upper,
        "expirations": [],
        "contracts": [],
        "count": 0,
    }


# ── GARCH Convergence (Cross-Asset Vol Repricing Lag) ───────────────

_garch_last_scan: float = 0.0
_garch_scan_lock: Optional[asyncio.Lock] = None
GARCH_COOLDOWN_S = 600  # 10 min — UW rate-limit + scan latency
GARCH_PRESET_TIMEOUT_S = 3600
GARCH_TICKER_TIMEOUT_S = 180


@app.post("/garch-convergence/scan")
async def garch_convergence_scan(preset: str = "largecaps", tickers: str = ""):
    """Run GARCH convergence scan (garch_convergence.py --preset X --json,
    or --tickers A,B,C,D paired consecutively — even symbol count required).

    Default preset is the virtual `largecaps` universe (NDX+SPX curated
    pairs) — `indexes` adds the Russell 2000 at ~4x the Unusual Whales
    request cost. Mirrors /leap/scan semantics: 600s cooldown + lock, subprocess
    writes data/garch_convergence.json directly (and records its own
    service_health[garch-scan] row), we re-read the cache file after the
    subprocess completes. Explicit pair scans bypass the cooldown and never
    advance it.

    Built-in presets: semis, mega-tech, energy, china-etf, all. Virtual
    `largecaps` / `indexes` plus file presets (data/presets/) also accepted.
    """
    requested = _parse_scan_tickers(tickers, require_pairs=True, dedupe=False)
    if test_mode:
        return await demo_scan_response(
            "garch-scan", {"scan_time": "", "tickers": {}, "pairs": []}
        )
    global _garch_last_scan, _garch_scan_lock
    import time as _time
    if _garch_scan_lock is None:
        _garch_scan_lock = asyncio.Lock()
    now = time.monotonic()
    is_ticker_scan = bool(requested)
    if not is_ticker_scan and now - _garch_last_scan < GARCH_COOLDOWN_S:
        cached = _read_cache(DATA_DIR / "garch_convergence.json")
        if _scan_cache_matches_preset(cached, preset):
            return cached
    async with _garch_scan_lock:
        if not is_ticker_scan and time.monotonic() - _garch_last_scan < GARCH_COOLDOWN_S:
            cached = _read_cache(DATA_DIR / "garch_convergence.json")
            if _scan_cache_matches_preset(cached, preset):
                return cached
        workers = _bounded_env_int("RADON_GARCH_SCANNER_WORKERS", 16)
        if is_ticker_scan:
            args = [
                "--tickers", ",".join(requested),
                "--json", "--no-open",
                "--workers", str(workers),
            ]
            timeout = GARCH_TICKER_TIMEOUT_S
        else:
            args = [
                "--preset", preset,
                "--json", "--no-open",
                "--workers", str(workers),
            ]
            timeout = GARCH_PRESET_TIMEOUT_S
        result = await run_script("garch_convergence.py", args, timeout=timeout)
        if not result.ok:
            raise HTTPException(status_code=502, detail=result.error)
        if not is_ticker_scan:
            _garch_last_scan = time.monotonic()
        cached = _read_cache(DATA_DIR / "garch_convergence.json")
        return cached or {
            "scan_time": "",
            "universe": "explicit" if is_ticker_scan else f"preset:{preset}",
            "requested_tickers": requested,
            "tickers": {},
            "pairs": [],
        }


# ── GEX (Gamma Exposure Levels) ─────────────────────────────────────

def _gex_cache_for_ticker(payload: Any, ticker: str) -> Optional[dict]:
    if not isinstance(payload, dict):
        return None
    return payload if str(payload.get("ticker") or "").upper() == ticker else None


@app.post("/gex/share")
async def gex_share():
    """Generate GEX X share report (4 cards + preview HTML). Returns output path."""
    result = await run_script("generate_gex_share.py", ["--json", "--no-open"], timeout=120)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    return result.data


_SHARE_CARD_TYPES = frozenset({"gex", "internals"})


@app.get("/share/content", response_class=HTMLResponse)
async def share_content(type: str, name: str):
    """Serve an allowlisted generated preview from the host that created it."""
    if type not in _SHARE_CARD_TYPES:
        raise HTTPException(status_code=400, detail="Invalid share-card type")
    pattern = re.compile(rf"^tweet-{re.escape(type)}-\d{{4}}-\d{{2}}-\d{{2}}\.html$")
    if not pattern.fullmatch(name) or Path(name).name != name:
        raise HTTPException(status_code=403, detail="Access denied")
    path = PROJECT_ROOT / "reports" / name
    try:
        return HTMLResponse(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="File not found") from exc


@app.post("/gex/scan")
async def gex_scan(ticker: str = "SPX"):
    """Run GEX scan (gex_scan.py --json --ticker X). Cooldown + failure backoff via SCAN_GATES."""
    if test_mode:
        return await demo_scan_response("gex-scan", {"scan_time": ""})
    ticker = ticker.strip().upper()
    if not ticker or len(ticker) > 10 or not ticker.replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid ticker")

    def _persist(result: ScriptResult) -> dict:
        if _gex_cache_for_ticker(result.data, ticker) is None:
            raise HTTPException(status_code=502, detail="GEX result ticker mismatch")
        _write_cache(DATA_DIR / "gex.json", result.data)
        return result.data

    return await _gated_scan(
        _scan_gate_for("gex", ticker),
        lambda: _gex_cache_for_ticker(_read_cache(DATA_DIR / "gex.json"), ticker),
        lambda: run_script("gex_scan.py", ["--json", "--ticker", ticker], timeout=120),
        _persist,
    )


# ── Gamma Rotation Gap (SPY/TLT cross-asset gamma) ───────────────────

_gamma_rotation_last_scan: float = 0.0
_gamma_rotation_scan_lock: Optional[asyncio.Lock] = None
GAMMA_ROTATION_COOLDOWN_S = 60


@app.post("/gamma-rotation/scan")
async def gamma_rotation_scan():
    """Run SPY/TLT Gamma Rotation Gap scan."""
    if test_mode:
        return await demo_scan_response("gamma-rotation", {"scan_time": ""})
    global _gamma_rotation_last_scan, _gamma_rotation_scan_lock
    import time as _time
    if _gamma_rotation_scan_lock is None:
        _gamma_rotation_scan_lock = asyncio.Lock()
    now = time.monotonic()
    if now - _gamma_rotation_last_scan < GAMMA_ROTATION_COOLDOWN_S:
        cached = _read_cache(DATA_DIR / "gamma_rotation_gap.json")
        if cached:
            return cached
    async with _gamma_rotation_scan_lock:
        if time.monotonic() - _gamma_rotation_last_scan < GAMMA_ROTATION_COOLDOWN_S:
            cached = _read_cache(DATA_DIR / "gamma_rotation_gap.json")
            if cached:
                return cached
        result = await run_script("gamma_rotation_gap.py", ["--json"], timeout=120)
        if not result.ok:
            raise HTTPException(status_code=502, detail=result.error)
        _write_cache(DATA_DIR / "gamma_rotation_gap.json", result.data)
        _gamma_rotation_last_scan = time.monotonic()
        return result.data


@app.post("/regime/share")
async def regime_share():
    """Generate Regime/CRI X share report (4 cards + preview HTML). Returns output path."""
    result = await run_script("generate_regime_share.py", ["--json", "--no-open"], timeout=120)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    return result.data


# ── LLM Token Expenditure Index ─────────────────────────────────────

_llm_token_index_cache: dict[str, Any] = {"data": None, "fetched_at": 0.0, "days": 0}
_LLM_TOKEN_INDEX_TTL_S = 300  # 5 min — the underlying data updates once/day


@app.get("/llm-token-index")
async def llm_token_index(days: int = Query(default=180, ge=1, le=3650)):
    """Last N days of the Radon LLM Token Expenditure Index, ASC by date.

    Cached 5 min — the daily timer writes once at 06:30 UTC so anything
    tighter is wasted DB hops. Empty table returns an empty list (NOT a
    404) so the UI can render "no data yet" gracefully until the first
    timer fires.
    """
    import time as _time
    now = time.monotonic()
    if (
        _llm_token_index_cache["data"] is not None
        and _llm_token_index_cache["days"] == days
        and now - _llm_token_index_cache["fetched_at"] < _LLM_TOKEN_INDEX_TTL_S
    ):
        return _llm_token_index_cache["data"]

    # Bounded hrana read (db_http) — sync libsql is banned in this process
    # (GIL-holding native calls starve the event loop even from a thread).
    # `components` is intentionally omitted from the row shape so the chart
    # payload stays light.
    try:
        raw_rows = await asyncio.to_thread(
            db_http.hrana_execute,
            """
            SELECT date, index_value, raw_avg_usd, methodology_version
            FROM llm_token_index
            ORDER BY date DESC
            LIMIT ?
            """,
            (int(days),),
        )
        rows = [
            {
                "date": row[0],
                "index_value": float(row[1]),
                "raw_avg_usd": float(row[2]),
                "methodology_version": int(row[3]),
            }
            for row in raw_rows
        ]
        rows.reverse()  # ASC for chart consumption
    except Exception as exc:
        logger.warning("[llm-token-index] DB read failed: %s", exc)
        rows = []

    payload = {
        "rows": rows,
        "count": len(rows),
        "days": days,
        "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    _llm_token_index_cache["data"] = payload
    _llm_token_index_cache["fetched_at"] = now
    _llm_token_index_cache["days"] = days
    return payload


@app.post("/internals/share")
async def internals_share():
    """Generate internals share report using the shared CRI report builder."""
    result = await run_script(
        "generate_regime_share.py",
        ["--json", "--no-open", "--card-type", "internals"],
        timeout=120,
    )
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    return result.data


@app.get("/internals/skew-history")
async def internals_skew_history(
    nq_ticker: str = Query(default="NDX"),
    spx_ticker: str = Query(default="SPX"),
    timeframe: str = Query(default="5Y"),
    nq_delta: int = Query(default=25),
    spx_delta: int = Query(default=25),
    nq_expiry: Optional[str] = None,
    spx_expiry: Optional[str] = None,
):
    if not uw_available:
        raise HTTPException(status_code=503, detail="UW token is required for internals skew history")

    normalized_timeframe = timeframe.upper().strip() or "5Y"
    cache_path = _build_internals_skew_cache_path(
        nq_ticker,
        spx_ticker,
        normalized_timeframe,
        nq_delta,
        spx_delta,
        nq_expiry,
        spx_expiry,
    )
    cached = _read_internals_skew_cache(cache_path)
    if cached:
        return cached

    try:
        nq_rows, used_nq_expiries, nq_expiry_source = await _fetch_risk_reversal_history(
            nq_ticker,
            normalized_timeframe,
            nq_delta,
            nq_expiry,
            max_expiries=12,
        )
        spx_rows, used_spx_expiries, spx_expiry_source = await _fetch_risk_reversal_history(
            spx_ticker,
            normalized_timeframe,
            spx_delta,
            spx_expiry,
            max_expiries=12,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    payload = _internals_skew_cache_payload(
        nq_ticker,
        spx_ticker,
        normalized_timeframe,
        nq_delta,
        spx_delta,
        nq_expiry,
        spx_expiry,
        nq_rows,
        spx_rows,
        used_nq_expiries,
        used_spx_expiries,
    )
    payload["nq"]["expiry_source"] = nq_expiry_source
    payload["spx"]["expiry_source"] = spx_expiry_source
    _write_cache(cache_path, payload)
    return payload


@app.post("/blotter")
async def blotter_sync():
    """Removed. Historical blotter is Turso journal. Flex recon is --from-file."""
    raise HTTPException(
        status_code=404,
        detail="POST /blotter is file-ingest only. GET /orders reads journal_sync.",
    )


# ---------------------------------------------------------------------------
# Performance — task registry for deduplication (single-worker assumed)
# ---------------------------------------------------------------------------
_running_build: Optional[asyncio.Task] = None


PERFORMANCE_SCHEMA_VERSION = 2


def _refuse_legacy_performance_payload(payload: dict) -> dict:
    """A payload that is not schema_version 2 never reaches the page as a number.

    The pre-refactor builder published an unversioned shape with no `status`,
    which the read path defaulted to healthy — that is how a contaminated
    +951.28% rendered with an empty warnings array. Anything that is not v2 is
    served with its headline stripped and a loud status instead of silently.
    """
    if payload.get("schema_version") == PERFORMANCE_SCHEMA_VERSION:
        return payload
    refused = {k: v for k, v in payload.items() if k not in ("summary", "twr", "benchmark")}
    refused.update(
        {
            "schema_version": payload.get("schema_version"),
            "status": "degraded",
            "twr": None,
            "warnings": [
                {
                    "code": "LEGACY_PAYLOAD_REFUSED",
                    "severity": "error",
                    "message": (
                        "The performance builder returned a pre-v2 payload; its "
                        "return figures are suppressed because they carry no "
                        "integrity status."
                    ),
                    "context": {"schema_version": payload.get("schema_version")},
                }
            ],
        }
    )
    return refused


async def _do_performance_rebuild() -> dict:
    """Run the TWR builder (scripts/perf_twr_builder.py) and cache the result.

    One writer owns performance_snapshots. The builder persists to Turso itself
    and prints the v2 payload on stdout.
    """
    result = await run_script("perf_twr_builder.py", ["--json"], timeout=180)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    if not isinstance(result.data, dict):
        raise HTTPException(status_code=502, detail="performance builder returned no payload")
    payload = _refuse_legacy_performance_payload(result.data)
    _write_cache(DATA_DIR / "performance.json", payload)
    return payload


@app.post("/performance")
async def performance_sync():
    """Run portfolio performance metrics. 180s timeout.

    If a build is already in-flight, piggybacks on it (returns same result).

    R-101: this had NEITHER the embargo guard nor the cooldown its sibling
    `/performance/background` got in 23b1b9c5, and its callers include the
    cold-start GET path. With `performance_snapshots` empty or Turso reads
    failing, EVERY GET /api/performance blocked on a full builder run — the
    request storm that earned the 1025 in the first place.
    """
    raise HTTPException(
        status_code=404,
        detail="Performance rebuild is file-ingest only. Use perf_twr_builder.py --from-file.",
    )


# Floor between on-demand rebuilds. Every rebuild attempts an IBKR Flex fetch,
# and the endpoint previously guarded only against a CONCURRENT build -- so each
# page load that found a stale snapshot fired another one, roughly every 15
# minutes all day. IBKR answered with code 1025 ("Too many failed attempts"), a
# lockout earned by repeated failures, which took out /performance AND
# cash-flow-sync since they share the one token. radon-perf-twr.timer owns the
# schedule now (Tue..Sat 07:30 ET); this path is a fallback and needs a floor.
PERFORMANCE_BACKGROUND_COOLDOWN_S = 20 * 60

# R-102: the cooldown used to be a process-local `time.monotonic()` float.
# Any radon-api restart (deploy, watchdog, OOM) reset it to None and re-opened
# an immediate Flex fetch; under a crash loop the 20-minute floor was zero.
# It lives on disk now, in UTC epoch seconds, so a restart cannot buy a fetch.
PERFORMANCE_REBUILD_SIDECAR = DATA_DIR / "performance_rebuild_cooldown.json"


def _last_rebuild_epoch() -> Optional[float]:
    try:
        raw = json.loads(PERFORMANCE_REBUILD_SIDECAR.read_text())
        value = float(raw.get("last_rebuild_at"))
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    return value


def _record_rebuild_attempt() -> None:
    try:
        PERFORMANCE_REBUILD_SIDECAR.parent.mkdir(parents=True, exist_ok=True)
        from utils.atomic_io import atomic_save

        atomic_save(
            str(PERFORMANCE_REBUILD_SIDECAR),
            {"last_rebuild_at": datetime.now(timezone.utc).timestamp()},
        )
    except Exception:  # noqa: BLE001 — a cooldown write must never fail a build
        logger.warning("could not persist the performance rebuild cooldown")


def _rebuild_refusal() -> Optional[dict]:
    """`None` when a rebuild may proceed, else a refusal payload."""
    try:
        from utils.flex_embargo import active_until

        until = active_until()
    except Exception:
        until = None
    if until:
        return {"status": "lockout", "next_attempt_at": until}

    last = _last_rebuild_epoch()
    if last is None:
        return None
    age = datetime.now(timezone.utc).timestamp() - last
    if 0 <= age < PERFORMANCE_BACKGROUND_COOLDOWN_S:
        return {
            "status": "cooldown",
            "retry_in_seconds": int(PERFORMANCE_BACKGROUND_COOLDOWN_S - age),
        }
    return None


def _refuse_rebuild_or_none() -> None:
    """R-102: a refusal used to be a 202 to a caller that swallows the body,
    so `/performance` served a stale snapshot with no explanation anywhere.
    The synchronous endpoint answers with a real status code and logs."""
    refusal = _rebuild_refusal()
    if refusal is None:
        return
    logger.warning("performance rebuild refused: %s", refusal)
    raise HTTPException(
        status_code=503 if refusal["status"] == "lockout" else 429,
        detail=refusal,
    )


@app.post("/performance/background", status_code=202)
async def performance_background():
    """Fire-and-forget performance rebuild. Returns 202 immediately.

    Refuses a duplicate while one is in flight, and refuses a fresh Flex fetch
    inside the cooldown window or during a token lockout.
    """
    return JSONResponse(
        status_code=404,
        content={"status": "file_ingest_only", "detail": "Use perf_twr_builder.py --from-file."},
    )


_EQUITY_OPTIONS_CHAIN_TIMEOUT_S = 45.0


def _options_chain_failure_status(error: Optional[str]) -> int:
    if error and "timeout" in error.lower():
        return 504
    if error and "timed out" in error.lower():
        return 504
    return 502


@app.get("/options/chain")
async def options_chain(symbol: str, expiry: Optional[str] = None):
    """Fetch options chain for a symbol."""
    args = ["--symbol", symbol.upper()]
    if expiry:
        args.extend(["--expiry", expiry])
    result = await _run_ib_script_with_recovery(
        "ib_option_chain.py", args, timeout=_EQUITY_OPTIONS_CHAIN_TIMEOUT_S
    )
    if not result.ok:
        raise HTTPException(
            status_code=_options_chain_failure_status(result.error),
            detail=result.error,
        )
    if result.data and result.data.get("error"):
        detail = str(result.data["error"])
        raise HTTPException(
            status_code=_options_chain_failure_status(detail),
            detail=detail,
        )
    return result.data


@app.get("/options/expirations")
async def options_expirations(symbol: str):
    """List option expirations for a symbol."""
    result = await _run_ib_script_with_recovery(
        "ib_option_chain.py",
        ["--symbol", symbol.upper()],
        timeout=_EQUITY_OPTIONS_CHAIN_TIMEOUT_S,
    )
    if not result.ok:
        raise HTTPException(
            status_code=_options_chain_failure_status(result.error),
            detail=result.error,
        )
    if result.data and result.data.get("error"):
        detail = str(result.data["error"])
        raise HTTPException(
            status_code=_options_chain_failure_status(detail),
            detail=detail,
        )
    return {"symbol": result.data.get("symbol"), "expirations": result.data.get("expirations")}


_OPTIONS_EXPOSURE_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")
_OPTIONS_EXPOSURE_FREQUENCIES = {"eod", "intraday"}


@app.get("/options/exposure/{symbol}")
async def options_exposure(symbol: str, frequency: str = "eod"):
    """Return one normalized, uncached options exposure cube.

    MenthorQ authentication is resolved only inside the Python provider client;
    no dashboard token, cookie, or storage state crosses this route boundary.
    """

    if not _OPTIONS_EXPOSURE_SYMBOL_RE.fullmatch(symbol):
        raise HTTPException(status_code=400, detail="Invalid symbol")
    if frequency not in _OPTIONS_EXPOSURE_FREQUENCIES:
        raise HTTPException(status_code=400, detail="Invalid frequency")

    try:
        provider = MenthorQDashboardClient()
        return await asyncio.to_thread(provider.fetch_exposure, symbol, frequency)
    except MenthorQDashboardAuthError as exc:
        raise HTTPException(
            status_code=503,
            detail="Options exposure authentication is unavailable",
        ) from exc
    except MenthorQDashboardTimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail="Options exposure provider timed out",
        ) from exc
    except (MenthorQDashboardPayloadError, MenthorQDashboardUpstreamError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Options exposure data is unavailable",
        ) from exc


# ── RV Ratio (realized-vol ratio vs SPY) ────────────────────────────

_RV_RATIO_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")
RV_RATIO_COOLDOWN_S = 600  # daily-close data cannot change intraday
# Covers a cold two-leg (asset + SPY) 12y backfill: fetches plus batched
# Turso writes. 240s was blown in production when per-row writes ate the
# budget (2026-07-21); writes are now chunked, 360s is headroom for slow
# Yahoo/Turso days.
RV_RATIO_SCAN_TIMEOUT_S = 360
_rv_ratio_last_scan: dict[str, float] = {}
_rv_ratio_scan_locks: dict[str, asyncio.Lock] = {}
# Reserved _rv_ratio_scan_locks key (symbols are validated uppercase, so no
# collision): every scan subprocess also refreshes the shared SPY benchmark
# rows, and a concurrent scan during a SPY lineage reset (DELETE + re-insert)
# would read an empty/partial benchmark. All scans serialize on this lock.
_RV_RATIO_BENCHMARK_LOCK_KEY = "__benchmark__"


def _validated_rv_ratio_symbol(symbol: str) -> str:
    if not _RV_RATIO_SYMBOL_RE.fullmatch(symbol):
        raise HTTPException(status_code=400, detail="Invalid symbol")
    return symbol


def _rv_ratio_snapshot_path(symbol: str) -> Path:
    return DATA_DIR / "rv_ratio" / f"{symbol}.json"


def _rv_ratio_cooldown_remaining(symbol: str) -> float:
    last = _rv_ratio_last_scan.get(symbol)
    if last is None:
        return 0.0
    return RV_RATIO_COOLDOWN_S - (time.monotonic() - last)


def _rv_ratio_cooldown_response(remaining: float) -> dict:
    return {"status": "cooldown", "retry_in": max(1, int(round(remaining)))}


@app.get("/options/rv-ratio/{symbol}")
async def rv_ratio_get(symbol: str):
    """Serve the last RV-ratio snapshot from disk (debug/parity surface).

    The Turso-first read lives in the Next.js route; this mirrors the
    subprocess's atomic disk write. An absent snapshot is a legitimate
    pending state: 200 + missing flag, never 4xx
    (feedback_http_status_for_real_errors).
    """
    symbol = _validated_rv_ratio_symbol(symbol)
    cached = _read_cache(_rv_ratio_snapshot_path(symbol))
    if cached is None:
        return {"symbol": symbol, "missing": True}
    return cached


@app.post("/options/rv-ratio/{symbol}/scan")
async def rv_ratio_scan(symbol: str):
    """Run rv_ratio_scan.py synchronously and return the fresh payload.

    Per-symbol 600s cooldown + single-flight lock (LEAP precedent). The
    subprocess owns all Turso/disk writes; a failed run never advances
    the cooldown, so the operator can retry immediately.
    """
    symbol = _validated_rv_ratio_symbol(symbol)
    if test_mode:
        return {
            "schema_version": 1,
            "symbol": symbol,
            "missing": True,
            "reason": "insufficient_history",
            "scan_time": "",
        }
    remaining = _rv_ratio_cooldown_remaining(symbol)
    if remaining > 0:
        return _rv_ratio_cooldown_response(remaining)
    lock = _rv_ratio_scan_locks.setdefault(symbol, asyncio.Lock())
    async with lock:
        remaining = _rv_ratio_cooldown_remaining(symbol)
        if remaining > 0:
            return _rv_ratio_cooldown_response(remaining)
        benchmark_lock = _rv_ratio_scan_locks.setdefault(
            _RV_RATIO_BENCHMARK_LOCK_KEY, asyncio.Lock()
        )
        async with benchmark_lock:
            result = await run_script(
                "rv_ratio_scan.py", [symbol], timeout=RV_RATIO_SCAN_TIMEOUT_S
            )
        if not result.ok:
            raise HTTPException(status_code=503, detail=result.error)
        _rv_ratio_last_scan[symbol] = time.monotonic()
        return result.data


# ── Bullish Percent Index (P&F buy-signal breadth) ──────────────────

_BPI_INDICES = {"NDX", "SPX", "RUT"}
BPI_SCAN_COOLDOWN_S = 600  # daily-close data cannot change intraday
# Covers an incremental all-index run (constituents + ~2,600 member
# staleness probes + 1mo Yahoo fetches + chunked Turso writes). Full 2y
# backfills are operator-run on the VPS CLI, not through this endpoint.
BPI_SCAN_TIMEOUT_S = 900
_bpi_last_scan: dict[str, float] = {}
# One lock for ALL BPI scans: every run writes the shared
# price_history_daily member rows (NDX members are a subset of SPX), so
# concurrent subprocesses would double-fetch and interleave writes.
# Created lazily inside the handler (rv-ratio precedent) — a module-level
# Lock binds to the import-time loop on py3.9.
_bpi_scan_locks: dict[str, asyncio.Lock] = {}
_BPI_GLOBAL_LOCK_KEY = "__all__"


def _validated_bpi_index(index: str) -> str:
    normalized = index.strip().upper()
    if normalized != "ALL" and normalized not in _BPI_INDICES:
        raise HTTPException(status_code=400, detail="Invalid index")
    return normalized


def _bpi_cooldown_remaining(index: str) -> float:
    last = _bpi_last_scan.get(index)
    if last is None:
        return 0.0
    return BPI_SCAN_COOLDOWN_S - (time.monotonic() - last)


@app.post("/bpi/scan")
async def bpi_scan(index: str = "all"):
    """Run bpi_scan.py synchronously and return the fresh envelope.

    Per-index 600s cooldown + single-flight lock (RV-ratio precedent).
    The subprocess owns all Turso/disk writes; a failed run never
    advances the cooldown, so the operator can retry immediately.
    """
    index = _validated_bpi_index(index)
    if test_mode:
        return {"generated_at": "", "indices": {}}
    remaining = _bpi_cooldown_remaining(index)
    if remaining > 0:
        return _rv_ratio_cooldown_response(remaining)
    lock = _bpi_scan_locks.setdefault(_BPI_GLOBAL_LOCK_KEY, asyncio.Lock())
    async with lock:
        remaining = _bpi_cooldown_remaining(index)
        if remaining > 0:
            return _rv_ratio_cooldown_response(remaining)
        result = await run_script(
            "bpi_scan.py", ["--index", index.lower() if index == "ALL" else index],
            timeout=BPI_SCAN_TIMEOUT_S,
        )
        if not result.ok:
            raise HTTPException(status_code=503, detail=result.error)
        _bpi_last_scan[index] = time.monotonic()
        return result.data


# ── Futures chain (Phase 2 — VIX et al.) ────────────────────────────

_FUTURES_CHAIN_TIMEOUT_S = 30.0  # patched in tests


def _futures_chain_cache_path(symbol_upper: str) -> Path:
    return DATA_DIR / f"futures_chain_{symbol_upper}.json"


def _is_fresh_futures_cache(cached: Optional[dict]) -> bool:
    """A cache is fresh when it was stamped on the current ET trading day
    and carries at least one contract. The listed-futures chain is static
    intraday (the front month only rolls at expiry), so a same-day cache is
    always valid to serve.
    """
    if not isinstance(cached, dict):
        return False
    if cached.get("as_of_date") != _today_et_str():
        return False
    contracts = cached.get("contracts")
    return isinstance(contracts, list) and len(contracts) > 0


def _stamp_futures_chain(data: dict, symbol_upper: str) -> dict:
    stamped = dict(data)
    stamped["symbol"] = stamped.get("symbol") or symbol_upper
    stamped["as_of"] = datetime.now(timezone.utc).isoformat()
    stamped["as_of_date"] = _today_et_str()
    stamped["stale"] = False
    return stamped


@app.get("/futures/chain")
async def futures_chain(symbol: str):
    """List listed futures contracts for a supported underlying.

    Routes through ib_chain.py (subprocess) so the request gets its
    own event loop. The pool's data client lives on a thread with its
    own loop — calling sync IB methods from asyncio.to_thread crashes
    intermittently with "There is no current event loop in thread"
    (large payloads consistently; small payloads luckily). Subprocess
    avoids the cross-thread loop deadlock entirely.

    Per-symbol disk cache + stale-on-failure: the listed-futures chain is
    static intraday, so a same-day cache is served immediately (no live
    call, no farm dependency). On a cold/cross-day miss we run the
    subprocess; if that fails we serve the last good cache (flagged stale)
    rather than surfacing a timeout to the order ticket. Only 502 when
    there is no cache at all.
    """
    from clients.contract_resolver import supports_futures

    symbol_upper = symbol.upper()
    if not supports_futures(symbol_upper):
        raise HTTPException(
            status_code=400,
            detail=f"futures not supported for {symbol_upper}; supported: VIX",
        )

    cache_path = _futures_chain_cache_path(symbol_upper)
    cached = _read_cache(cache_path)

    if _is_fresh_futures_cache(cached):
        return cached

    result = await run_script(
        "ib_chain.py",
        ["--kind", "future", "--symbol", symbol_upper],
        timeout=_FUTURES_CHAIN_TIMEOUT_S,
    )

    live_ok = (
        result.ok
        and result.data
        and not result.data.get("error")
        and isinstance(result.data.get("contracts"), list)
        and len(result.data["contracts"]) > 0
    )
    if live_ok:
        stamped = _stamp_futures_chain(result.data, symbol_upper)
        try:
            from utils.atomic_io import atomic_save
            atomic_save(str(cache_path), stamped)
        except Exception:
            pass
        return stamped

    if isinstance(cached, dict) and isinstance(cached.get("contracts"), list):
        stale = dict(cached)
        stale["stale"] = True
        return stale

    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    if result.data and result.data.get("error"):
        raise HTTPException(status_code=502, detail=result.data["error"])
    return {"symbol": symbol_upper, "exchange": "CFE", "contracts": [], "count": 0}


# ── PI command surface ──────────────────────────────────────────────
# Scripts the embedded /api/pi chat command surface is permitted to spawn,
# split into two privilege tiers. The Next.js layer does the argument
# parsing / normalisation (preserving the existing helpers + tests);
# FastAPI enforces the tier + executes.
#
# READ-tier: pure analysis / scans. They write scan *caches* under data/
# but never touch live-account or canonical portfolio/journal/order state.
_PI_READ_SCRIPTS = frozenset({
    "scanner.py",
    "discover.py",
    "evaluate.py",
    "leap_scanner_uw.py",
})

# MUTATE-tier: connects to the live IB account and/or rewrites canonical
# portfolio/journal/order state. `ib_sync.py` pulls positions from IB.
# Running one requires explicit
# privileged authorization the chat surface does NOT pass by default.
_PI_MUTATE_SCRIPTS = frozenset({
    "ib_sync.py",
})

# Outer defence-in-depth allowlist: anything outside both tiers → 400.
_PI_SCRIPT_ALLOWLIST = _PI_READ_SCRIPTS | _PI_MUTATE_SCRIPTS
_PI_TICKER = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")
_PI_PRESETS = frozenset({"sectors", "mag7", "semis", "emerging", "china"})


def _pi_bounded_int(value: str, name: str, minimum: int, maximum: int) -> str:
    if not value.isdigit():
        raise HTTPException(status_code=400, detail=f"{name} must be an integer")
    parsed = int(value)
    if parsed < minimum or parsed > maximum:
        raise HTTPException(
            status_code=400,
            detail=f"{name} must be between {minimum} and {maximum}",
        )
    return str(parsed)


def _pi_parse_flags(
    args: list[str],
    *,
    value_flags: dict[str, tuple[int, int]],
    boolean_flags: frozenset[str] = frozenset(),
) -> tuple[list[str], list[str]]:
    normalized: list[str] = []
    positional: list[str] = []
    seen: set[str] = set()
    index = 0
    while index < len(args):
        token = args[index]
        if not token.startswith("--"):
            positional.append(token)
            index += 1
            continue
        if token in seen:
            raise HTTPException(status_code=400, detail=f"duplicate PI flag: {token}")
        seen.add(token)
        if token in boolean_flags:
            normalized.append(token)
            index += 1
            continue
        bounds = value_flags.get(token)
        if bounds is None or index + 1 >= len(args):
            raise HTTPException(status_code=400, detail=f"PI flag not allowed: {token}")
        normalized.extend([
            token,
            _pi_bounded_int(args[index + 1], token, bounds[0], bounds[1]),
        ])
        index += 2
    return normalized, positional


def _validate_pi_args(script: str, args: list[str]) -> list[str]:
    if len(args) > 32 or any(len(arg) > 128 or "\x00" in arg for arg in args):
        raise HTTPException(status_code=400, detail="PI arguments exceed bounds")

    if script == "scanner.py":
        normalized, positional = _pi_parse_flags(
            args,
            value_flags={"--top": (1, 100), "--min-score": (1, 100)},
        )
        if positional:
            raise HTTPException(status_code=400, detail="scanner does not accept positional arguments")
        return normalized

    if script == "discover.py":
        normalized, positional = _pi_parse_flags(
            args,
            value_flags={
                "--min-premium": (1, 1_000_000_000),
                "--min-alerts": (1, 100),
                "--dp-pages": (1, 40),
                "--dp-days": (1, 30),
            },
            boolean_flags=frozenset({"--include-indices"}),
        )
        if positional:
            raise HTTPException(status_code=400, detail="discover does not accept positional arguments")
        return normalized

    if script == "evaluate.py":
        normalized, positional = _pi_parse_flags(
            args,
            value_flags={"--days": (1, 30)},
        )
        if len(positional) != 1 or not _PI_TICKER.fullmatch(positional[0].upper()):
            raise HTTPException(status_code=400, detail="evaluate requires one valid ticker")
        return [positional[0].upper(), *normalized]

    if script == "leap_scanner_uw.py":
        # Preset is a bounded enum, not numeric, so handle it separately while
        # retaining strict rejection of every filesystem/network override.
        stripped = args
        preset_args: list[str] = []
        if "--preset" in args:
            preset_index = args.index("--preset")
            if preset_index + 1 >= len(args):
                raise HTTPException(status_code=400, detail="missing --preset value")
            preset = args[preset_index + 1].lower()
            if preset not in _PI_PRESETS:
                raise HTTPException(status_code=400, detail="invalid LEAP preset")
            stripped = args[:preset_index] + args[preset_index + 2:]
            preset_args = ["--preset", preset]
        normalized, positional = _pi_parse_flags(
            stripped,
            value_flags={"--min-gap": (1, 100)},
            boolean_flags=frozenset({"--json"}),
        )
        normalized.extend(preset_args)
        if len(positional) > 25 or any(not _PI_TICKER.fullmatch(item.upper()) for item in positional):
            raise HTTPException(status_code=400, detail="LEAP tickers are invalid or exceed 25")
        return [item.upper() for item in positional] + normalized

    if script == "ib_sync.py":
        normalized, positional = _pi_parse_flags(
            args,
            value_flags={},
            boolean_flags=frozenset({"--sync", "--no-prices"}),
        )
        if positional:
            raise HTTPException(status_code=400, detail="ib_sync does not accept positional arguments")
        return normalized

    raise HTTPException(status_code=400, detail="PI script schema missing")


@app.post("/pi/exec")
async def pi_exec(payload: dict, request: Request):
    """Execute an allowlisted PI script and return raw stdout/stderr text.

    Body shape: {"script": "scanner.py", "args": ["--top", "20"], "timeout": 120}

    Returns: {"ok": bool, "stdout": str, "stderr": str, "exit_code": int|null,
              "timed_out": bool}

    The Next.js /api/pi route owns parsing + allowlisting upstream; this
    enforces the same allowlist as a defence-in-depth measure.

    Least-privilege: READ-tier scripts run on the default path. A MUTATE-tier
    script (live-account / portfolio-state mutator) requires an explicit
    `allow_mutating: true` in the body; without it the request is refused 400.
    """
    script = payload.get("script") if isinstance(payload, dict) else None
    if not isinstance(script, str) or not script:
        raise HTTPException(status_code=400, detail="script is required")
    if script not in _PI_SCRIPT_ALLOWLIST:
        raise HTTPException(status_code=400, detail=f"Script not allowed: {script}")
    if script in _PI_MUTATE_SCRIPTS and (
        payload.get("allow_mutating") is not True
        or not is_trusted_local_request(request)
    ):
        raise HTTPException(
            status_code=403 if payload.get("allow_mutating") is True else 400,
            detail=(
                f"Script is MUTATE-tier and requires explicit "
                f"allow_mutating: true — {script}"
            ),
        )

    args = payload.get("args") or []
    if not isinstance(args, list) or any(not isinstance(a, str) for a in args):
        raise HTTPException(status_code=400, detail="args must be a list of strings")
    args = _validate_pi_args(script, args)

    timeout = payload.get("timeout", 120)
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="timeout must be a number")
    timeout = max(1.0, min(timeout, 600.0))

    result = await run_script_raw(script, args, timeout=timeout)
    return {
        "ok": result.ok,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
    }


@app.get("/ticker/ratings")
async def ticker_ratings(ticker: str):
    """Analyst ratings + targets for a single ticker.

    Thin passthrough to scripts/fetch_analyst_ratings.py with --json. The
    script outputs a JSON array (one entry per ticker requested); for the
    single-ticker case we unwrap and return the first element so the Next.js
    route can render it directly.
    """
    upper = ticker.upper().strip()
    if not upper:
        raise HTTPException(status_code=400, detail="ticker is required")
    result = await run_script(
        "fetch_analyst_ratings.py", [upper, "--json"], timeout=60
    )
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    payload = result.data
    if isinstance(payload, list):
        return payload[0] if payload else {}
    return payload


# ---------------------------------------------------------------------------
# IB Gateway auto-recovery
# ---------------------------------------------------------------------------

_IB_CONN_REFUSED_PATTERNS = (
    "Connect call failed",
    "ECONNREFUSED",
    "Connection refused",
    "TimeoutError",
    "API connection failed",
    "Failed to connect to IB",
    "IBConnectionError",
    "Make sure API port",
    "Connectivity between IBKR and",
    "request timed out",
)

# Cooldown: after an IB subprocess fails with a connection error, skip
# subsequent attempts for this many seconds to avoid churn.
_IB_SCRIPT_COOLDOWN_SECS = 15.0
_ib_last_failure: float = 0.0  # monotonic timestamp of last IB connection failure

# Scripts that transmit orders are NOT idempotent: a connection-flavored
# failure can surface after placeOrder already reached IB, so the automatic
# post-restart re-run would place a second identical live order (T-011).
_NON_IDEMPOTENT_IB_SCRIPTS = frozenset({"ib_place_order.py"})


def _is_ib_connection_error(error_msg: str) -> bool:
    """Check if an error message indicates IB Gateway is unreachable."""
    return any(p in (error_msg or "") for p in _IB_CONN_REFUSED_PATTERNS)


def _pool_has_any_connection() -> bool:
    """Quick check: does the pool have at least one live IB connection?

    If yes, the Gateway is up and subprocesses should be able to connect.
    If no, the Gateway is likely down — subprocess will also fail.
    """
    if not ib_pool:
        return False
    for role in ("sync", "orders", "data"):
        if ib_pool.is_connected(role):
            return True
    return False


def _should_auto_restart_ib_gateway_after_runtime_failure() -> bool:
    """Runtime subprocess failures should not churn a local launchd/IBC session.

    Startup still uses ensure_ib_gateway(); this only governs mid-session recovery.
    """
    if is_cloud_mode() or is_docker_mode():
        return False
    if is_launchd_mode():
        return False
    return True


async def _working_open_order_perm_ids() -> list:
    """permIds of working orders from Turso ``open_orders`` — no IB call.

    REL-018 (R-007): the auto-restart path must know what it is about to
    orphan. A snapshot failure returns [] — inventory is advisory, never
    restart-blocking.
    """
    try:
        rows = await asyncio.to_thread(
            db_http.hrana_execute,
            "SELECT perm_id FROM open_orders ORDER BY updated_at DESC",
            (),
        )
    except Exception as exc:
        logger.warning("Working-order snapshot failed before gateway restart: %s", exc)
        return []
    return [row[0] for row in rows if row and row[0] is not None]


async def _run_ib_script_with_recovery(
    script: str, args: list, timeout: float = 30, raw: bool = False
) -> ScriptResult:
    """Run an IB-dependent script with pre-flight health check and cooldown.

    Three layers of fast-fail:
    1. Cooldown: if a recent IB script failed, skip for _IB_SCRIPT_COOLDOWN_SECS
    2. Pool check: if pool is disconnected, verify Gateway before spawning
    3. Post-failure: verify Gateway health before restarting

    Pass `raw=True` for scripts that write to a file and emit
    human-readable text on stdout (ib_sync.py --sync, ib_orders.py
    --sync, ib_reconcile.py). The default JSON-parsing path crashes on
    those with "Invalid JSON output" because run_script greedily parses
    the first '{' it finds in the status text.
    """
    global _ib_last_failure
    _runner = run_script_raw if raw else run_script

    # Layer 1: Cooldown — skip if a recent failure occurred
    now = time.monotonic()
    if _ib_last_failure > 0 and (now - _ib_last_failure) < _IB_SCRIPT_COOLDOWN_SECS:
        elapsed = now - _ib_last_failure
        logger.debug(
            "Skipping %s — IB cooldown active (%.1fs since last failure, %ds cooldown)",
            script, elapsed, _IB_SCRIPT_COOLDOWN_SECS,
        )
        return ScriptResult(
            ok=False,
            error="IB Gateway connection recently failed. Retrying shortly.",
        )

    # Layer 2: Pre-flight pool check
    if not _pool_has_any_connection():
        gw_status = await check_ib_gateway(
            pool_status=ib_pool.status() if ib_pool else None
        )
        port_ok = gw_status.get("port_listening", False)
        upstream_dead = gw_status.get("upstream_dead", False)

        if not port_ok or upstream_dead:
            _ib_last_failure = now
            logger.warning(
                "Skipping %s — Gateway down (port=%s, upstream_dead=%s), pool disconnected",
                script, port_ok, upstream_dead,
            )
            return ScriptResult(
                ok=False,
                error="IB Gateway is not accepting connections. Check IBKR Mobile for 2FA approval.",
            )

        # REL-017 (R-008): an awaiting-2FA Gateway keeps the API socket open,
        # so the port check above passes and a placement subprocess would burn
        # its 25s timeout returning an indeterminate result. Order-transmitting
        # scripts require a fully authenticated session — refuse fast instead.
        auth_state = gw_status.get("auth_state", "unknown")
        if script in _NON_IDEMPOTENT_IB_SCRIPTS and auth_state != "authenticated":
            logger.warning(
                "Refusing %s — Gateway auth_state=%s (order placement requires 'authenticated')",
                script, auth_state,
            )
            return ScriptResult(
                ok=False,
                error=(
                    f"Order placement refused: IB Gateway auth state is "
                    f"'{auth_state}', not 'authenticated'. Approve any pending "
                    "IBKR Mobile 2FA prompt, then retry."
                ),
            )

    result = await _runner(script, args, timeout=timeout)

    # Clear cooldown on success
    if result.ok:
        _ib_last_failure = 0.0

    if not result.ok and _is_ib_connection_error(result.error):
        # Set cooldown to prevent churn from repeated failures
        _ib_last_failure = time.monotonic()

        # Verify Gateway is actually down before restarting
        gw_status = await check_ib_gateway()
        port_ok = gw_status.get("port_listening", False)
        upstream_dead = gw_status.get("upstream_dead", False)

        if port_ok and not upstream_dead:
            # Gateway is healthy — subprocess failed for other reasons
            logger.warning(
                "Script %s failed but Gateway is healthy — not restarting (cooldown %ds)",
                script, _IB_SCRIPT_COOLDOWN_SECS,
            )
            return result

        if not _should_auto_restart_ib_gateway_after_runtime_failure():
            if is_cloud_mode():
                logger.warning(
                    "IB Gateway unreachable in cloud mode (port=%s, upstream_dead=%s) — remote host owns recovery",
                    port_ok, upstream_dead,
                )
                result = ScriptResult(
                    ok=False,
                    error="IB Gateway is not responding (cloud mode). Check remote host and Tailscale.",
                )
            else:
                logger.warning(
                    "IB Gateway unreachable in managed mode (port=%s, upstream_dead=%s) — lock-aware watchdog owns recovery",
                    port_ok,
                    upstream_dead,
                )
                result = ScriptResult(
                    ok=False,
                    error=(
                        "IB Gateway is not responding. Lock-aware watchdog recovery is pending; "
                        "approve any existing IBKR Mobile 2FA prompt."
                    ),
                )
        else:
            working_perm_ids = await _working_open_order_perm_ids()
            if working_perm_ids:
                logger.warning(
                    "restarting gateway with %d working orders: %s",
                    len(working_perm_ids), working_perm_ids,
                )
            logger.warning(
                "IB Gateway unreachable (port=%s, upstream_dead=%s), attempting auto-restart...",
                port_ok, upstream_dead,
            )
            gw_result = await restart_ib_gateway()

            if gw_result.get("restarted") and gw_result.get("port_listening"):
                _ib_last_failure = 0.0  # Clear cooldown after successful restart
                if ib_pool:
                    await ib_pool.disconnect_all()
                    await ib_pool.connect_all()
                if script in _NON_IDEMPOTENT_IB_SCRIPTS:
                    logger.warning(
                        "IB Gateway restarted after %s failed — NOT re-running a "
                        "non-idempotent order script; outcome is indeterminate",
                        script,
                    )
                    error_msg = (
                        "IB Gateway was restarted after the placement attempt "
                        "failed. The order was NOT automatically retried — the "
                        "first attempt may have reached IB before the failure. "
                        "Check open orders before re-placing."
                    )
                    if working_perm_ids:
                        error_msg += (
                            f" {len(working_perm_ids)} working orders were live "
                            f"at restart (permIds: {working_perm_ids})."
                        )
                    result = ScriptResult(ok=False, error=error_msg)
                else:
                    logger.info("IB Gateway restarted, retrying %s", script)
                    result = await _runner(script, args, timeout=timeout)
            else:
                logger.error("IB Gateway restart failed: %s", gw_result)
                result = ScriptResult(
                    ok=False,
                    error=f"IB Gateway is down and restart failed. {gw_result.get('error', '')}".strip()
                        + " Check IBKR Mobile for 2FA approval.",
                )

    return result


# ---------------------------------------------------------------------------
# Cash flows (deposits, withdrawals, dividends, interest, fees)
# ---------------------------------------------------------------------------

@app.get("/cash-flows")
async def cash_flows(
    days: int = 90,
    types: str = "",
):
    """Return cash transactions from the `cash_flows` Turso table.

    Reads-only — populated by `scripts/cash_flow_sync.py` which runs daily
    via the monitor_daemon `cash_flow_sync` handler. Falls back to
    `data/cash_flows.json` if the DB read fails.

    Query params:
      days  - lookback window in days, default 90
      types - comma-separated filter (e.g. "Deposit,Withdrawal"); empty = all
    """
    type_filter = {t.strip() for t in types.split(",") if t.strip()} or None
    cutoff_iso = (datetime.now(timezone.utc).date() - timedelta(days=max(1, days))).isoformat()

    rows: list[dict[str, Any]] = []
    db_error: Optional[str] = None

    # Bounded hrana read (db_http) — sync libsql is banned in this process;
    # this route runs on every /orders view, the exact hot path the 06-11
    # wedge took down.
    try:
        raw_rows = await asyncio.to_thread(
            db_http.hrana_execute,
            """
            SELECT id, date, type, amount, currency, description, raw_type, synced_at
            FROM cash_flows
            WHERE date >= ?
            ORDER BY date DESC, id DESC
            """,
            (cutoff_iso,),
        )
        for row in raw_rows:
            rows.append({
                "id": row[0],
                "date": row[1],
                "type": row[2],
                "amount": row[3],
                "currency": row[4],
                "description": row[5],
                "raw_type": row[6],
                "synced_at": row[7],
            })
    except Exception as exc:
        db_error = str(exc)
        # Fall back to JSON file
        try:
            from utils.atomic_io import verified_load
            snapshot = verified_load(str(DATA_DIR / "cash_flows.json"))
            rows = [r for r in snapshot.get("rows", []) if r.get("date", "") >= cutoff_iso]
        except Exception:
            pass

    if type_filter:
        rows = [r for r in rows if r.get("type") in type_filter]

    summary = {
        "deposits": sum(r["amount"] for r in rows if r["type"] == "Deposit"),
        "withdrawals": sum(r["amount"] for r in rows if r["type"] == "Withdrawal"),
        "dividends": sum(r["amount"] for r in rows if r["type"] == "Dividend"),
        "net": sum(r["amount"] for r in rows),
    }

    # Most-recent successful sync touch among the rows that survived the
    # date cutoff + type filter. The UI uses this to render a small
    # "synced Xh ago — Flex publishes daily (T+1)" lozenge so operators
    # who just initiated a withdrawal understand WHY the panel hasn't
    # picked it up yet. See feedback_flex_cash_transaction_lag.md —
    # CashTransaction publishes once per day with a ~1-day settlement
    # lag, so a withdrawal initiated after the 17:00 ET daemon fire
    # won't appear until the next morning's pull.
    synced_values = [r["synced_at"] for r in rows if r.get("synced_at")]
    last_synced_at = max(synced_values) if synced_values else None

    # service_health row for cash-flow-sync — surfaces the daemon's most
    # recent attempt state so the lozenge can explain WHY a synced-Xh-ago
    # reading is stale. Most common cause: IBKR Flex throttle code 1001
    # ("Statement could not be generated"). When throttle is active we
    # surface the next_attempt_at so the operator knows when fresh data
    # will land instead of guessing.
    sync_status = await asyncio.to_thread(_load_cash_flow_sync_status)

    return {
        "rows": rows,
        "count": len(rows),
        "from_date": cutoff_iso,
        "summary": summary,
        "last_synced_at": last_synced_at,
        "sync_status": sync_status,
        "db_error": db_error,  # null on success; non-null when DB read failed and we fell back
    }


def _load_cash_flow_sync_status() -> dict[str, Any]:
    """Read service_health[cash-flow-sync] and surface throttle/error context.

    Returns a payload safe for the public route to expose:

        {
          "state": "ok" | "error" | "stale" | "unknown",
          "last_attempt_at": ISO str or None,
          "next_attempt_at": ISO str or None,
          "error_summary": short human-readable message or None,
          "is_throttled": bool,
        }

    `last_attempt_at` is intentionally distinct from `last_synced_at` —
    `last_synced_at` is the freshest row's sync timestamp (the last
    SUCCESS), `last_attempt_at` is the daemon's last try (success or
    failure). When `last_attempt_at > last_synced_at`, the daemon
    attempted but didn't write new rows — usually a throttle.

    All exceptions are swallowed; the route shouldn't 500 because we
    couldn't read service_health.
    """
    payload: dict[str, Any] = {
        "state": "unknown",
        "last_attempt_at": None,
        "next_attempt_at": None,
        "error_summary": None,
        "is_throttled": False,
        "is_lockout": False,
    }
    try:
        # Bounded hrana read; runs off-loop via asyncio.to_thread at the
        # call site. Sync libsql is banned in this process.
        rows = db_http.hrana_execute(
            """
            SELECT state, last_attempt_finished_at, last_error
            FROM service_health
            WHERE service = ?
            """,
            ("cash-flow-sync",),
        )
        row = rows[0] if rows else None
        if row is None:
            return payload
        state = row[0] or "unknown"
        last_attempt_at = row[1]
        last_error_raw = row[2]

        payload["state"] = state
        payload["last_attempt_at"] = last_attempt_at

        if last_error_raw:
            try:
                parsed = json.loads(last_error_raw)
                message = parsed.get("message") if isinstance(parsed, dict) else None
                next_attempt = parsed.get("next_attempt_at") if isinstance(parsed, dict) else None
            except Exception:
                message = str(last_error_raw)
                next_attempt = None

            if message:
                verdict = _classify_cash_flow_error(message)
                payload.update(verdict)
                if verdict.get("is_lockout"):
                    # Sidecar-or-Turso reconstruction of the live deadline
                    # beats whatever next_attempt_at the row happened to carry.
                    try:
                        from utils.flex_embargo import active_until
                        reconstructed = active_until()
                    except Exception:
                        reconstructed = None
                    if reconstructed:
                        next_attempt = reconstructed

            payload["next_attempt_at"] = next_attempt
    except Exception:
        # Never let a service_health read fail the cash-flows response.
        pass
    return payload


def _classify_cash_flow_error(message: str) -> dict[str, Any]:
    """Tag a cash-flow-sync failure so the UI need not substring-match IBKR.

    R-134: `is_throttled` used to fire on 1001|1018|1019. Per 436dcdc1 only
    1018 is a rate limit — 1001 is "could not be generated" and 1019 is
    "generation in progress", both of which take the bounded soft lane — so
    the amber "Flex throttled, don't manually retry" lozenge was rendered for
    a 5-minute soft wait. And 1025, the 7-DAY token lockout, was omitted
    entirely and fell through to generic red. `is_lockout` is its own flag.
    """
    lower = message.lower()
    is_lockout = "code 1025" in lower or "too many failed attempts" in lower
    verdict: dict[str, Any] = {
        "is_lockout": is_lockout,
        # Only 1018 is a rate limit. "throttle" stays as a fallback for the
        # producer's own wording, but a lockout is never a throttle.
        "is_throttled": (not is_lockout)
        and ("code 1018" in lower or "throttle" in lower),
    }
    if is_lockout:
        verdict["error_summary"] = "Flex lockout. Do not retry. Ingest with --from-file"
    elif "Flex throttle" in message:
        verdict["error_summary"] = "Flex throttled by IBKR"
    elif ":" in message:
        verdict["error_summary"] = message.split(":")[-1].strip()
    else:
        verdict["error_summary"] = message
    return verdict


# ---------------------------------------------------------------------------
# Short availability probe
# ---------------------------------------------------------------------------

# IB generic tick IDs for short availability:
#   46 = shortable (difficulty score: 3.0 easy / 1.5-2.5 locate / <1.5 no)
#   89 = short shares available
# Must be STREAMING (not snapshot) — per feedback_ib_snapshot_no_generic_ticks.md.
_SHORT_AVAIL_GENERIC_TICKS = "236"
_SHORT_TICK_DIFFICULTY = 46   # float field tickerId
_SHORT_TICK_SHARES = 89       # float field tickerId
_SHORT_PROBE_TIMEOUT_SECS = 6.0
_SHORTABLE_EASY_THRESHOLD = 2.5   # >= easy to borrow
_SHORTABLE_NO_THRESHOLD = 1.5     # <  no shares available

# UW short data freshness: refuse rows older than 3 trading days
_UW_SHORT_DATA_MAX_AGE_DAYS = 3


def _derive_shortability(
    difficulty: Optional[float],
    shortable_shares: Optional[float] = None,
) -> Optional[bool]:
    """Map IB tick 46 difficulty score (and/or tick 89 shares) to shortable boolean.

    When difficulty IS present:
      >= 2.5 → True (easy), < 1.5 → False (not shortable), 1.5-2.5 → None (locate-only).

    When difficulty is absent but shortable_shares is a positive number, shortable
    MUST be True — you cannot have 190M borrowable shares and "unknown" shortability
    (SPX-03 AAPL live repro: tick 89 arrived in the ~6s window, tick 46 did not).

    shortable stays None ONLY when BOTH difficulty AND shortable_shares are absent
    or shortable_shares is zero/None with no difficulty signal.
    """
    if difficulty is not None:
        if difficulty >= _SHORTABLE_EASY_THRESHOLD:
            return True
        if difficulty < _SHORTABLE_NO_THRESHOLD:
            return False
        return None  # locate-only range — neither clearly shortable nor blocked
    if shortable_shares is not None and shortable_shares > 0:
        return True
    return None


def _probe_short_ticks_in_thread(client: Any, ticker: str) -> dict:
    """Run a bounded streaming market data probe for short availability ticks.

    Uses STREAMING (not snapshot) to receive generic ticks 46 + 89.
    Polls up to _SHORT_PROBE_TIMEOUT_SECS for both fields to arrive,
    then cancels the subscription.

    Returns dict with 'difficulty' and 'shortable_shares' (both may be None).
    """
    from ib_insync import Stock

    contract = Stock(ticker, "SMART", "USD")
    try:
        qualified = client.ib.qualifyContracts(contract)
    except Exception:
        qualified = []
    if not qualified:
        return {"difficulty": None, "shortable_shares": None}

    ticker_obj = client.ib.reqMktData(qualified[0], _SHORT_AVAIL_GENERIC_TICKS, False, False)

    # Poll for tick data arrival; sleep in small increments
    poll_interval = 0.2
    elapsed = 0.0
    while elapsed < _SHORT_PROBE_TIMEOUT_SECS:
        difficulty = getattr(ticker_obj, f"tick{_SHORT_TICK_DIFFICULTY}", None)
        shares = getattr(ticker_obj, f"tick{_SHORT_TICK_SHARES}", None)
        # ib_insync stores generic ticks in the tickerId-indexed attributes;
        # fall back to direct attribute name search on the Ticker object
        if difficulty is None:
            difficulty = _read_generic_tick(ticker_obj, _SHORT_TICK_DIFFICULTY)
        if shares is None:
            shares = _read_generic_tick(ticker_obj, _SHORT_TICK_SHARES)
        if difficulty is not None:
            break
        client.ib.sleep(poll_interval)
        elapsed += poll_interval

    # Re-read after final sleep
    difficulty = _read_generic_tick(ticker_obj, _SHORT_TICK_DIFFICULTY)
    shares = _read_generic_tick(ticker_obj, _SHORT_TICK_SHARES)

    try:
        client.ib.cancelMktData(qualified[0])
    except Exception:
        pass

    return {
        "difficulty": difficulty,
        "shortable_shares": shares,
    }


def _read_generic_tick(ticker_obj: Any, tick_id: int) -> Optional[float]:
    """Read a generic tick value from an ib_insync Ticker object.

    ib_insync stores generic tick data in `ticks` list as GenericTick objects,
    and also populates named attributes like `shortableShares` (tick 89) and
    `shortable` (tick 46).
    """
    # Named shortcut attributes on Ticker
    _NAMED = {
        46: ("shortable",),
        89: ("shortableShares",),
    }
    for attr in _NAMED.get(tick_id, ()):
        val = getattr(ticker_obj, attr, None)
        if val is not None and val == val:  # exclude NaN
            try:
                return float(val)
            except (TypeError, ValueError):
                pass

    # Walk the raw ticks list
    for tick in getattr(ticker_obj, "ticks", []):
        if getattr(tick, "tickType", None) == tick_id:
            val = getattr(tick, "value", None)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass
    return None


def _uw_short_data_is_fresh(raw: dict, ticker: str) -> bool:
    """Check UW short data row for staleness and instrument identity.

    UW can serve stale rows for recycled tickers (e.g. SPCX was a SPAC ETF
    before a new company reused the symbol). Reject rows older than
    _UW_SHORT_DATA_MAX_AGE_DAYS trading days.
    """
    data_rows = raw.get("data") or []
    if not data_rows:
        return False
    latest = data_rows[0] if isinstance(data_rows, list) else None
    if not isinstance(latest, dict):
        return False
    as_of_str = latest.get("date") or latest.get("as_of") or ""
    if not as_of_str:
        return False
    try:
        from datetime import date
        as_of = date.fromisoformat(str(as_of_str)[:10])
        age_days = (datetime.now(timezone.utc).date() - as_of).days
        return age_days <= _UW_SHORT_DATA_MAX_AGE_DAYS
    except Exception:
        return False


def _extract_uw_fee_rebate(raw: dict) -> tuple[Optional[float], Optional[float], Optional[str]]:
    """Extract fee_rate, rebate_rate, and as_of from a UW short data response."""
    data_rows = raw.get("data") or []
    if not data_rows or not isinstance(data_rows, list):
        return None, None, None
    latest = data_rows[0] if data_rows else None
    if not isinstance(latest, dict):
        return None, None, None
    fee_rate = _safe_float(latest.get("fee_rate") or latest.get("borrowRate"))
    rebate_rate = _safe_float(latest.get("rebate_rate") or latest.get("rebateRate"))
    as_of = latest.get("date") or latest.get("as_of")
    return fee_rate, rebate_rate, str(as_of) if as_of else None


def _safe_float(val: Any) -> Optional[float]:
    """Parse a value to float, returning None on failure or NaN."""
    if val is None:
        return None
    try:
        f = float(val)
        return None if f != f else f  # exclude NaN
    except (TypeError, ValueError):
        return None


@app.get("/short-availability/{ticker}")
async def short_availability(ticker: str, request: Request):
    """Short availability data for a ticker.

    Primary: IB streaming probe for tick 46 (difficulty) + tick 89 (shortable shares).
    Fallback: UW get_short_data() for fee_rate / rebate_rate when IB has no data.

    ALWAYS returns 200 with missing:true when no data is available.
    Never raises 4xx (per feedback_http_status_for_real_errors.md).
    """
    upper = ticker.upper().strip()
    if not _TICKER_RE.match(upper):
        return JSONResponse({"ticker": upper, "shortable": None, "difficulty": None,
                             "shortable_shares": None, "fee_rate": None, "rebate_rate": None,
                             "source": "none", "as_of": datetime.now(timezone.utc).isoformat(),
                             "missing": True})

    difficulty: Optional[float] = None
    shortable_shares: Optional[float] = None
    fee_rate: Optional[float] = None
    rebate_rate: Optional[float] = None
    source = "none"
    as_of = datetime.now(timezone.utc).isoformat()

    # --- IB probe (primary) ---
    if ib_pool is not None and ib_pool.is_connected("data"):
        try:
            async with ib_pool.acquire("data") as client:
                result = await asyncio.wait_for(
                    asyncio.to_thread(_probe_short_ticks_in_thread, client, upper),
                    timeout=_SHORT_PROBE_TIMEOUT_SECS + 2.0,
                )
            difficulty = result.get("difficulty")
            shortable_shares = result.get("shortable_shares")
            if difficulty is not None or shortable_shares is not None:
                source = "ib"
                as_of = datetime.now(timezone.utc).isoformat()
        except asyncio.TimeoutError:
            logger.warning("short-availability/%s: IB probe timed out", upper)
        except Exception as exc:
            logger.warning("short-availability/%s: IB probe error: %s", upper, exc)

    # --- UW fallback (for fee/rebate or when IB returned nothing) ---
    if uw_available:
        try:
            raw = await asyncio.to_thread(_fetch_uw_short_data, upper)
            if raw is not None:
                if _uw_short_data_is_fresh(raw, upper):
                    uw_fee, uw_rebate, uw_as_of = _extract_uw_fee_rebate(raw)
                    fee_rate = uw_fee
                    rebate_rate = uw_rebate
                    if source == "none":
                        source = "uw"
                        as_of = uw_as_of or as_of
                else:
                    logger.info(
                        "short-availability/%s: UW data too old, ignoring (SPCX-style stale row)",
                        upper,
                    )
        except Exception as exc:
            logger.warning("short-availability/%s: UW fallback error: %s", upper, exc)

    shortable = _derive_shortability(difficulty, shortable_shares)
    missing = source == "none"

    return JSONResponse({
        "ticker": upper,
        "shortable": shortable,
        "difficulty": difficulty,
        "shortable_shares": shortable_shares,
        "fee_rate": fee_rate,
        "rebate_rate": rebate_rate,
        "source": source,
        "as_of": as_of,
        "missing": missing,
    })


def _fetch_uw_short_data(ticker: str) -> Optional[dict]:
    """Fetch UW short data synchronously (intended for asyncio.to_thread)."""
    try:
        with UWClient() as client:
            return client.get_short_data(ticker)
    except UWNotFoundError:
        return None
    except UWAPIError as exc:
        logger.info("short-availability/%s: UW get_short_data error: %s", ticker, exc)
        return None


# ---------------------------------------------------------------------------
# Knowledge retrieval (Phase 2 — hybrid search over the `knowledge` table)
# ---------------------------------------------------------------------------

_KNOWLEDGE_QUERY_MAX_CHARS = 500
_KNOWLEDGE_LIMIT_DEFAULT = 8
_KNOWLEDGE_LIMIT_MAX = 20
_KNOWLEDGE_TICKER_RE = re.compile(r"^[A-Z0-9]{1,6}$")
_KNOWLEDGE_FILTER_MAX_ITEMS = 10
_KNOWLEDGE_FILTER_MAX_ITEM_CHARS = 64
_KNOWLEDGE_COMPACT_CONTENT_CHARS = 1200
_KNOWLEDGE_COMPACT_NEIGHBOR_CHARS = 400
_KNOWLEDGE_COMPACT_MAX_NEIGHBORS = 2
_PRIOR_EVAL_SOURCES = ["journal", "evals"]
# One bounded retry absorbs a transient hrana flake (2026-07-20: a single
# platform blip 503'd the assistant's only thesis lookup); a second failure
# still surfaces the sanitized 503 within ~1 extra round-trip + backoff.
_KNOWLEDGE_RETRIEVAL_ATTEMPTS = 2
_KNOWLEDGE_RETRY_BACKOFF_SECS = 0.25
_THESIS_DOC_KEY_PREFIX = TRADE_LOG_KEY_PREFIX
_KNOWLEDGE_RESULT_FIELDS = (
    "source", "scope", "doc_key", "chunk_ix", "title", "summary",
    "content", "metadata", "score", "last_activity_at", "neighbors",
)


class _KnowledgeRows:
    def __init__(self, rows: list[tuple]):
        self._rows = rows

    def fetchall(self) -> list[tuple]:
        return self._rows


class _KnowledgeHranaConnection:
    """Duck-typed connection for knowledge.retrieve.hybrid_search backed by
    the bounded hrana pipeline — sync libsql is banned in this process
    (Event-Loop Discipline, scripts/api/CLAUDE.md)."""

    def execute(self, sql: str, args=()) -> _KnowledgeRows:
        return _KnowledgeRows(db_http.hrana_execute(sql, tuple(args)))


def _is_thesis_doc(row: dict) -> bool:
    """Thesis-bearing knowledge rows: evals reports and journal trade-log
    entries (they carry Thesis:/Notes: lines) — as opposed to raw IB fill
    rows, whose doc_keys are order/exec ids."""
    if row.get("source") == "evals":
        return True
    return str(row.get("doc_key") or "").startswith(_THESIS_DOC_KEY_PREFIX)


def _thesis_first(scored_rows: list[tuple[float, dict]]) -> list[tuple[float, dict]]:
    """Deterministic prior-evals rerank: thesis-bearing docs before raw fill
    rows, fused-score order preserved within each band. A bare-ticker query
    lets BM25 flood the top ranks with near-duplicate fill rows (2026-07-20:
    the EWY thesis never surfaced), so it runs over the full deduped pool."""
    theses: list[tuple[float, dict]] = []
    fills: list[tuple[float, dict]] = []
    for pair in scored_rows:
        (theses if _is_thesis_doc(pair[1]) else fills).append(pair)
    return theses + fills


def _knowledge_search_in_thread(
    query: str,
    scopes: Optional[List[str]],
    sources: Optional[List[str]],
    limit: int,
    rerank: Optional[Callable] = None,
) -> tuple[list[dict], str]:
    """Embed the query + run hybrid search, entirely off the event loop:
    the first get_embedder() call may load the ~67 MB ONNX model, embedding
    is sync CPU work, and each hrana statement blocks up to its timeout.
    A transient DbHttpError gets one in-thread retry (the query is embedded
    once); the last failure propagates to the caller's 503 mapping."""
    query_embedding = None
    embedder = get_embedder()
    if embedder is not None:
        try:
            query_embedding = embedder([query])[0]
        except Exception as exc:
            logger.warning("knowledge: query embedding failed (%s) — FTS-only", exc)
            query_embedding = None
    for attempt in range(1, _KNOWLEDGE_RETRIEVAL_ATTEMPTS + 1):
        try:
            results = hybrid_search(
                _KnowledgeHranaConnection(),
                query,
                query_embedding=query_embedding,
                scopes=scopes,
                sources=sources,
                limit=limit,
                rerank=rerank,
            )
            break
        except db_http.DbHttpError:
            if attempt >= _KNOWLEDGE_RETRIEVAL_ATTEMPTS:
                raise
            if query_embedding is not None:
                # vector_top_k over the ANN index is the statement that blows
                # the Hrana bound under load (0.3-1.6s normally, >4s on a
                # cold or busy host; 2026-08-30 03:05Z post-deploy 503s).
                # Retry without the leg that just timed out rather than
                # re-running it.
                logger.warning("knowledge: hybrid retrieval timed out; retrying FTS-only")
                query_embedding = None
            time.sleep(_KNOWLEDGE_RETRY_BACKOFF_SECS)
    retrieval = "hybrid" if query_embedding is not None else "fts-only"
    return results, retrieval


async def _run_knowledge_retrieval(
    query: str,
    scopes: Optional[List[str]],
    sources: Optional[List[str]],
    limit: int,
    compact: bool,
    *,
    route: str,
    rerank: Optional[Callable] = None,
) -> tuple[list[dict], str]:
    try:
        results, retrieval = await asyncio.to_thread(
            _knowledge_search_in_thread, query, scopes, sources, limit, rerank
        )
    except db_http.DbHttpError as exc:
        # DbHttpError messages are already scrubbed by db_http (exception
        # type + message, never SQL text or tokens) — safe to journal, and
        # without this line the 503 is invisible server-side (2026-07-20).
        logger.warning(
            "knowledge: %s retrieval failed after %d attempts (%s)",
            route, _KNOWLEDGE_RETRIEVAL_ATTEMPTS, exc,
        )
        raise HTTPException(
            status_code=503, detail="Knowledge retrieval is unavailable"
        ) from exc
    return [_knowledge_result_row(row, compact) for row in results], retrieval


def _knowledge_result_row(row: dict, compact: bool) -> dict:
    shaped = {field: row.get(field) for field in _KNOWLEDGE_RESULT_FIELDS}
    neighbors = [
        {"chunk_ix": neighbor.get("chunk_ix"), "content": neighbor.get("content") or ""}
        for neighbor in (shaped["neighbors"] or [])
    ]
    if compact:
        shaped["content"] = (shaped["content"] or "")[:_KNOWLEDGE_COMPACT_CONTENT_CHARS]
        neighbors = [
            {
                "chunk_ix": neighbor["chunk_ix"],
                "content": neighbor["content"][:_KNOWLEDGE_COMPACT_NEIGHBOR_CHARS],
            }
            for neighbor in neighbors[:_KNOWLEDGE_COMPACT_MAX_NEIGHBORS]
        ]
    shaped["neighbors"] = neighbors
    return shaped


def _validated_knowledge_query(body: dict) -> str:
    query = body.get("query")
    if isinstance(query, str):
        query = query.strip()
        if 1 <= len(query) <= _KNOWLEDGE_QUERY_MAX_CHARS:
            return query
    raise HTTPException(
        status_code=422, detail="query must be a string of 1-500 characters"
    )


def _validated_knowledge_filter(value, field: str) -> Optional[List[str]]:
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise HTTPException(status_code=422, detail=f"{field} must be a list of strings")
    # Each value becomes a SQL placeholder in three statements — cap the list
    # to the known scope/source vocabulary size so an authenticated caller
    # can't ship megabyte-scale SQL to Turso.
    if len(value) > _KNOWLEDGE_FILTER_MAX_ITEMS or any(
        len(item) > _KNOWLEDGE_FILTER_MAX_ITEM_CHARS for item in value
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                f"{field} accepts at most {_KNOWLEDGE_FILTER_MAX_ITEMS} values of "
                f"{_KNOWLEDGE_FILTER_MAX_ITEM_CHARS} characters each"
            ),
        )
    return value or None


def _clamped_knowledge_limit(value) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="limit must be an integer") from exc
    return max(1, min(limit, _KNOWLEDGE_LIMIT_MAX))


@app.post("/knowledge/search")
async def knowledge_search(request: Request):
    """Hybrid retrieval (FTS5 BM25 + vector + recency) over the knowledge
    base. Degrades to FTS-only when the local embedder is unavailable."""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="body must be a JSON object")
    query = _validated_knowledge_query(body)
    scopes = _validated_knowledge_filter(body.get("scopes"), "scopes")
    sources = _validated_knowledge_filter(body.get("sources"), "sources")
    limit = _clamped_knowledge_limit(body.get("limit", _KNOWLEDGE_LIMIT_DEFAULT))
    compact = bool(body.get("compact", False))

    # Demo isolation: the TEST_MODE instance gets no knowledge base — the
    # corpus carries real journal/eval figures (plan §Key decisions).
    if test_mode:
        return {"results": [], "retrieval": "fts-only"}

    results, retrieval = await _run_knowledge_retrieval(
        query, scopes, sources, limit, compact, route="/knowledge/search"
    )
    return {"results": results, "retrieval": retrieval}


@app.get("/knowledge/prior-evals")
async def knowledge_prior_evals(ticker: str, limit: int = _KNOWLEDGE_LIMIT_DEFAULT,
                                compact: bool = False):
    """Prior journal entries and evals for one ticker."""
    upper = ticker.strip().upper()
    if not _KNOWLEDGE_TICKER_RE.fullmatch(upper):
        raise HTTPException(status_code=400, detail="Invalid ticker")

    if test_mode:
        return {"ticker": upper, "results": [], "retrieval": "fts-only"}

    results, retrieval = await _run_knowledge_retrieval(
        upper, None, _PRIOR_EVAL_SOURCES, _clamped_knowledge_limit(limit), compact,
        route="/knowledge/prior-evals", rerank=_thesis_first,
    )
    return {"ticker": upper, "results": results, "retrieval": retrieval}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "scripts.api.server:app",
        host="127.0.0.1",
        port=8321,
        reload=True,
        reload_dirs=[str(SCRIPTS_DIR)],
        # Trust X-Forwarded-* only from the local Caddy hop so request.client.host
        # reflects the real remote IP for proxied traffic. The auth chokepoint
        # (is_trusted_local_request) also denies the bypass on any forwarded
        # request, so this is defense-in-depth — but it keeps logs/identity correct.
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
    )
