#!/usr/bin/env python3
"""IB Gateway API-aware watchdog.

Docker's TCP healthcheck only confirms the JVM has a socket bound on
port 4001. It can't tell whether the IB API thread is actually
responsive — a state we've hit three times in 24h on Hetzner where
the gateway accepts TCP connections but the API handshake hangs
forever, leaving fill-monitor / journal-sync / orders all timing out
while Docker reports "healthy."

This watchdog watches FastAPI's `/health` (which DOES probe the API
layer via the existing `ib_pool` machinery) and triggers a clean
`systemctl restart radon-ib-gateway.service` after sustained
degradation. Cadence: 60s, threshold: 3 consecutive degraded
readings (= ~3 min of real-time hang).

State persists at ``STATE_PATH`` so each oneshot invocation can pick
up the counter from the previous one. Reset on healthy, or on any
signal that isn't this specific degradation pattern (e.g. awaiting
2FA — which the 2FA backoff in scripts/api/ib_gateway.py already
handles and is NOT this bug).

See ``docs/ib-gateway-healthcheck-hardening.md`` for the design.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, TypeVar

# Cross-process advisory lock that prevents two restart paths (this
# watchdog + scripts/api/ib_gateway.restart_ib_gateway) from firing two
# IBKR Mobile 2FA pushes within minutes of each other. IBKR's backend
# rejects every push approval when multiple are stacked; the lock is
# the only structural defence.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
from utils import ib_2fa_lock  # noqa: E402

LOG = logging.getLogger("ib_watchdog")

# Identifier used when the watchdog holds the 2FA push lock. Distinct
# from the FastAPI restart_ib_gateway holder so an operator inspecting
# /var/lib/radon/ib-2fa-push-lock.json sees which component fired the
# active push.
WATCHDOG_LOCK_HOLDER = "scripts.ib_watchdog.trigger_restart"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


# --- Configuration -----------------------------------------------------------

DEFAULT_HEALTH_URL = "http://127.0.0.1:8321/health"
DEFAULT_HEALTH_TIMEOUT_SECS = 5.0
DEFAULT_STATE_PATH = Path("/var/lib/radon/ib-watchdog-state.json")
DEFAULT_RESTART_UNIT = "radon-ib-gateway.service"
# Three consecutive degraded readings (60s apart) = ~3 min of hang
# before we restart. Calibrated against today's incidents: the JVM
# hangs we've seen each lasted 10+ minutes, so 3 cycles trips well
# before user impact while skipping transient blips.
DEFAULT_THRESHOLD_CYCLES = 3

# Hard ceiling on a single cycle. Belt-and-suspenders against ANY sub-step
# blocking forever (the proven root cause of the gateway staying UNREACHABLE
# for 6+ hours when a libsql commit hung with no native timeout). main()
# arms a SIGALRM for this many seconds and exits cleanly if it fires, so the
# every-minute oneshot timer can always run the next tick.
CYCLE_HARD_TIMEOUT_SECS = 45

# Per-sub-step timeouts. Each must be comfortably under CYCLE_HARD_TIMEOUT_SECS
# so an individual stuck call is caught and abandoned BEFORE the whole-cycle
# alarm fires — keeping the normal failure mode "this sub-step failed,
# continue" rather than "kill the process."
SERVICE_HEALTH_WRITE_TIMEOUT_SECS = 8.0
LOCK_OP_TIMEOUT_SECS = 5.0


# --- Bounded sub-step execution ---------------------------------------------

_T = TypeVar("_T")


class _SubStepTimeout(Exception):
    """A bounded sub-step exceeded its timeout and was abandoned."""


def _run_bounded(label: str, timeout: float, fn: Callable[[], _T]) -> _T:
    """Run ``fn`` in a daemon thread and abandon it if it exceeds ``timeout``.

    libsql's commit() and (in pathological storage states) the filesystem
    lock ops have no native timeout, so a single hung call can stall the
    oneshot cycle indefinitely. We run such calls on a daemon thread and
    join with a deadline. On timeout we raise ``_SubStepTimeout``; the
    abandoned thread is a daemon and dies with the process — it can never
    keep the next cycle from running.
    """
    result: list[_T] = []
    error: list[BaseException] = []

    def _target() -> None:
        try:
            result.append(fn())
        except BaseException as exc:  # noqa: BLE001 — propagate to caller
            error.append(exc)

    worker = threading.Thread(target=_target, name=f"watchdog-{label}", daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise _SubStepTimeout(f"{label} exceeded {timeout:.0f}s")
    if error:
        raise error[0]
    return result[0]


# --- /health response handling ----------------------------------------------


@dataclass(frozen=True)
class GatewayState:
    """The subset of FastAPI /health we care about."""

    service_state: str  # "healthy" | "unhealthy" | "unknown"
    port_listening: bool
    upstream_dead: bool
    auth_state: str  # "authenticated" | "awaiting_2fa" | "unreachable" | …
    # The next three default to "no active recovery" — that's the
    # only safe assumption for tests built against the old dataclass
    # shape and matches the literal /health payload when FastAPI has
    # not yet attempted a restart this process lifetime.
    push_lock_active: bool = False
    backoff_attempt_count: int = 0
    next_attempt_in_secs: float = 0.0

    @classmethod
    def from_health_payload(cls, payload: dict) -> Optional["GatewayState"]:
        gw = payload.get("ib_gateway") or {}
        if not isinstance(gw, dict):
            return None
        backoff = gw.get("restart_backoff") or {}
        push_lock = backoff.get("push_lock") if isinstance(backoff, dict) else None
        return cls(
            service_state=str(gw.get("service_state", "unknown")),
            port_listening=bool(gw.get("port_listening", False)),
            upstream_dead=bool(gw.get("upstream_dead", False)),
            auth_state=str(gw.get("auth_state", "unknown")),
            push_lock_active=isinstance(push_lock, dict) and bool(push_lock.get("holder")),
            backoff_attempt_count=int(backoff.get("attempt_count", 0)) if isinstance(backoff, dict) else 0,
            next_attempt_in_secs=float(backoff.get("next_attempt_in_secs", 0.0)) if isinstance(backoff, dict) else 0.0,
        )


def fetch_health(url: str, timeout: float) -> Optional[GatewayState]:
    """Fetch /health and return the gateway state, or None on error.

    Network errors and bad payloads return None — the caller treats
    that as "watchdog can't tell, leave state alone" rather than
    falsely incrementing the degradation counter.
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        LOG.warning("health probe failed: %s", exc)
        return None
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        LOG.warning("health payload not JSON: %s", exc)
        return None
    state = GatewayState.from_health_payload(payload)
    if state is None:
        LOG.warning("health payload missing ib_gateway")
    return state


# --- Degradation classification ---------------------------------------------


def is_api_hang(state: GatewayState) -> bool:
    """The specific failure mode this watchdog exists to catch.

    TCP socket is bound (port_listening) but the upstream API isn't
    responding to handshakes (upstream_dead). This is distinct from
    awaiting_2fa (handled by `is_stuck_awaiting_2fa`) and from a
    fully-down gateway (port_listening would be False — Docker
    `restart: always` handles that).
    """
    if not state.port_listening:
        return False  # Port down → Docker restart policy handles it
    if state.auth_state == "awaiting_2fa":
        return False  # Handled by stuck-2FA path below
    return state.upstream_dead


def is_stuck_awaiting_2fa(state: GatewayState) -> bool:
    """The second failure mode: IB Gateway sits at the 2FA push prompt
    indefinitely because nothing is driving recovery.

    FastAPI's `restart_ib_gateway` has its own in-process backoff
    ladder, but that's only consulted when something else triggers a
    restart — there is no proactive heartbeat that ticks it forward.
    On a fresh FastAPI start (attempt_count=0, no push_lock), the
    system will sit awaiting_2fa forever waiting for an operator to
    notice and click "Force 2FA Push" in /admin.

    We fire when ALL of:
      - auth_state is awaiting_2fa (gateway is genuinely stuck)
      - no push lock holder (nothing else has a push in flight)
      - no scheduled retry pending (`next_attempt_in_secs <= 0`)
    The push lock is the cross-process safety against stacking — IBKR
    rejects every approval when multiple pushes are pending. See
    `feedback_2fa_push_stacking.md`.
    """
    if state.auth_state != "awaiting_2fa":
        return False
    if state.push_lock_active:
        return False  # A push is already pending; let it resolve
    if state.next_attempt_in_secs > 0:
        return False  # FastAPI has a scheduled retry queued
    return True


# --- State persistence ------------------------------------------------------


@dataclass
class WatchdogState:
    degraded_count: int = 0
    last_restart_at: float = 0.0
    last_outcome: str = "init"  # human-readable status for ops
    # Consecutive cycles where IB Gateway has been stuck at the 2FA push
    # prompt with no other recovery driver active. Separate counter from
    # `degraded_count` (the api-hang case) so the two failure modes don't
    # cross-pollute thresholds.
    stuck_2fa_count: int = 0

    def to_dict(self) -> dict:
        return {
            "degraded_count": self.degraded_count,
            "last_restart_at": self.last_restart_at,
            "last_outcome": self.last_outcome,
            "stuck_2fa_count": self.stuck_2fa_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WatchdogState":
        return cls(
            degraded_count=int(data.get("degraded_count", 0)),
            last_restart_at=float(data.get("last_restart_at", 0.0)),
            last_outcome=str(data.get("last_outcome", "init")),
            stuck_2fa_count=int(data.get("stuck_2fa_count", 0)),
        )


def load_state(path: Path) -> WatchdogState:
    if not path.exists():
        return WatchdogState()
    try:
        with path.open() as fh:
            return WatchdogState.from_dict(json.load(fh))
    except (json.JSONDecodeError, OSError) as exc:
        LOG.warning("state load failed (%s); resetting", exc)
        return WatchdogState()


def save_state(path: Path, state: WatchdogState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as fh:
        json.dump(state.to_dict(), fh)
    os.replace(tmp, path)


# --- Restart action ---------------------------------------------------------


def trigger_restart(unit: str, dry_run: bool = False) -> bool:
    """Restart the IB Gateway systemd unit. Returns True on success.

    Uses `systemctl restart` rather than calling FastAPI's
    `/ib/restart` directly because FastAPI's path is gated on the
    2FA backoff — we want this watchdog to be an escape hatch even
    when that backoff is active (it isn't fired for this specific
    failure mode, but defense in depth).
    """
    cmd = ["systemctl", "restart", unit]
    if dry_run:
        LOG.info("[dry-run] would run: %s", " ".join(cmd))
        return True
    try:
        subprocess.run(cmd, check=True, timeout=30)
        return True
    except subprocess.CalledProcessError as exc:
        LOG.error("systemctl restart failed: rc=%s stderr=%s", exc.returncode, exc.stderr)
        return False
    except subprocess.TimeoutExpired:
        LOG.error("systemctl restart timed out after 30s")
        return False


# --- service_health write ---------------------------------------------------


def _write_service_health(
    state_label: str,
    error_message: Optional[str],
) -> None:
    """Perform the actual Turso write. Module-level (not a closure) so tests
    can patch it directly and so the lazy import that keeps the script usable
    in dev environments without Radon's full dependency surface is isolated
    from the bounding logic in ``record_service_health``."""
    from scripts.db.writer import record_service_health as _write

    _write(
        "ib-watchdog",
        state_label,
        started_at=None,
        finished_at=None,  # writer fills timestamp
        error={"message": error_message} if error_message else None,
    )


def record_service_health(
    state_label: str,
    error_message: Optional[str] = None,
) -> None:
    """Best-effort heartbeat into Turso so the banner can show our state.

    The write goes through ``_run_bounded`` because libsql's commit() makes a
    synchronous network call to Turso with NO native timeout — a slow or
    unreachable backend was the proven cause of the watchdog hanging for 6+
    hours. On timeout we skip the heartbeat for this cycle; the row is
    best-effort by contract. A missing writer (dev environments) is also
    tolerated.
    """
    try:
        _run_bounded(
            "service-health-write",
            SERVICE_HEALTH_WRITE_TIMEOUT_SECS,
            lambda: _write_service_health(state_label, error_message),
        )
    except _SubStepTimeout as exc:
        LOG.warning("service_health write abandoned (%s); continuing", exc)
    except ImportError:
        LOG.debug("service_health writer not available in this environment")
    except Exception as exc:  # pragma: no cover — never crash the watchdog
        LOG.warning("service_health write failed: %s", exc)


# --- Bounded 2FA-lock access ------------------------------------------------

# Sentinel holder used when a bounded lock op times out. Surfaces in
# last_outcome so an operator can see the deferral was caused by a stuck
# filesystem op rather than a real competing push.
_LOCK_TIMEOUT_HOLDER = "lock-op-timeout"


def _check_2fa_push_lock_bounded(now: float):
    """Bounded ``check_2fa_push_lock``. On timeout, conservatively report a
    synthetic held lock so the caller DEFERS its restart (never fires blind)."""
    try:
        return _run_bounded(
            "2fa-lock-check",
            LOCK_OP_TIMEOUT_SECS,
            lambda: ib_2fa_lock.check_2fa_push_lock(now=now),
        )
    except _SubStepTimeout as exc:
        LOG.warning("2FA lock check abandoned (%s); deferring restart", exc)
        return ib_2fa_lock.PushLock(
            holder=_LOCK_TIMEOUT_HOLDER,
            acquired_at=now,
            expires_at=now,
            reason="lock check timed out",
        )


def _acquire_2fa_push_lock_bounded(now: float, *, reason: str):
    """Bounded ``acquire_2fa_push_lock``. On timeout, report (False, None) so
    the caller treats it as a lost race and skips the restart this cycle."""
    try:
        return _run_bounded(
            "2fa-lock-acquire",
            LOCK_OP_TIMEOUT_SECS,
            lambda: ib_2fa_lock.acquire_2fa_push_lock(
                WATCHDOG_LOCK_HOLDER,
                ttl_secs=ib_2fa_lock.DEFAULT_LOCK_TTL_SECS,
                reason=reason,
                now=now,
            ),
        )
    except _SubStepTimeout as exc:
        LOG.warning("2FA lock acquire abandoned (%s); skipping restart", exc)
        return (False, None)


# --- Stuck-awaiting-2FA handler ---------------------------------------------

# Three cycles (~3 min) of confirmed stuck-2FA before firing a fresh push.
# Lower than the api-hang threshold (also 3) but separate so a user who's
# slow to approve a push isn't punished by an immediate re-fire.
DEFAULT_STUCK_2FA_THRESHOLD = 3


def _handle_stuck_awaiting_2fa(
    *,
    state: "WatchdogState",
    state_path: Path,
    restart_unit: str,
    dry_run: bool,
    clock: callable,
    threshold: int = DEFAULT_STUCK_2FA_THRESHOLD,
) -> "WatchdogState":
    """Increment the stuck-2FA counter and, when threshold is hit, fire a
    fresh push by restarting the IB Gateway unit. Respects the cross-process
    2FA push lock so we never stack a second push on a pending one — that
    pattern is what motivated the lock in the first place (see
    `feedback_2fa_push_stacking.md`).
    """
    state.stuck_2fa_count += 1
    LOG.warning(
        "IB Gateway stuck awaiting 2FA (cycle %s/%s, no push in flight)",
        state.stuck_2fa_count,
        threshold,
    )

    if state.stuck_2fa_count < threshold:
        state.last_outcome = (
            f"stuck_2fa_{state.stuck_2fa_count}_of_{threshold}"
        )
        save_state(state_path, state)
        record_service_health("ok")
        return state

    # Threshold hit. Respect any in-flight push the FastAPI side may have
    # acquired between our /health probe and now.
    existing_lock = _check_2fa_push_lock_bounded(now=clock())
    if existing_lock is not None and existing_lock.holder != WATCHDOG_LOCK_HOLDER:
        LOG.warning(
            "stuck_2fa threshold hit but 2FA push lock raced — held by %r — "
            "deferring this cycle",
            existing_lock.holder,
        )
        state.last_outcome = f"stuck_2fa_lock_raced:{existing_lock.holder}"
        save_state(state_path, state)
        record_service_health("ok")
        return state

    acquired, lock_now = _acquire_2fa_push_lock_bounded(
        now=clock(),
        reason=f"stuck awaiting 2FA for {state.stuck_2fa_count} cycles",
    )
    if not acquired:
        LOG.warning(
            "stuck_2fa: failed to acquire push lock — held by %r",
            lock_now.holder if lock_now else "unknown",
        )
        state.last_outcome = "stuck_2fa_lock_race"
        save_state(state_path, state)
        record_service_health("ok")
        return state

    LOG.warning(
        "stuck_2fa sustained %s cycles — firing fresh push via %s restart",
        state.stuck_2fa_count,
        restart_unit,
    )
    ok = trigger_restart(restart_unit, dry_run=dry_run)
    state.last_restart_at = clock()
    state.stuck_2fa_count = 0  # Reset; next cycle observes recovery
    state.last_outcome = f"stuck_2fa_push_fired:{'ok' if ok else 'fail'}"
    save_state(state_path, state)
    record_service_health(
        "ok",
        error_message=(
            f"fired fresh 2FA push (restarted {restart_unit}); approve on IBKR Mobile"
            if ok
            else f"FAILED to restart {restart_unit} for stuck-2FA recovery"
        ),
    )
    return state


# --- Main cycle -------------------------------------------------------------


def run_cycle(
    *,
    health_url: str = DEFAULT_HEALTH_URL,
    health_timeout: float = DEFAULT_HEALTH_TIMEOUT_SECS,
    state_path: Path = DEFAULT_STATE_PATH,
    restart_unit: str = DEFAULT_RESTART_UNIT,
    threshold: int = DEFAULT_THRESHOLD_CYCLES,
    dry_run: bool = False,
    clock: callable = time.time,
) -> WatchdogState:
    """Execute one cycle. Returns the final state for inspection / testing.

    Kept as a pure-ish function (no top-level side effects beyond
    the dependencies it accepts) so tests can drive it deterministically.
    """
    state = load_state(state_path)

    health = fetch_health(health_url, health_timeout)
    if health is None:
        # Can't tell — record but don't act. Keeps the watchdog from
        # restarting the gateway when FastAPI itself is the broken thing.
        # Reset stuck_2fa_count too: we have no evidence the 2FA prompt
        # is still pending, so don't let the counter age across an outage.
        state.last_outcome = "probe_unreachable"
        state.stuck_2fa_count = 0
        save_state(state_path, state)
        record_service_health("ok", error_message=None)
        return state

    if not is_api_hang(health):
        # api-hang counter resets unconditionally — it only counts the
        # `upstream_dead with authenticated auth_state` pattern, and
        # we're not seeing that.
        if state.degraded_count > 0:
            LOG.info(
                "gateway no longer api-degraded "
                "(service_state=%s auth_state=%s) — resetting counter from %s",
                health.service_state,
                health.auth_state,
                state.degraded_count,
            )
        state.degraded_count = 0

        # Stuck-awaiting-2FA branch: separate failure mode from api-hang.
        if is_stuck_awaiting_2fa(health):
            return _handle_stuck_awaiting_2fa(
                state=state,
                state_path=state_path,
                restart_unit=restart_unit,
                dry_run=dry_run,
                clock=clock,
                threshold=threshold,
            )

        # If we're still in awaiting_2fa but a push lock holder OR a
        # scheduled retry is active, recovery is in flight — freeze the
        # stuck counter where it is. Don't reset (so the next cycle after
        # the lock clears acts promptly) and don't increment (so the user
        # has time to approve the in-flight push without us re-firing).
        if health.auth_state == "awaiting_2fa":
            state.last_outcome = (
                f"awaiting_2fa_recovery_in_flight:"
                f"push_lock={health.push_lock_active}"
                f":next_attempt={int(health.next_attempt_in_secs)}s"
            )
            save_state(state_path, state)
            record_service_health("ok")
            return state

        # Genuinely healthy — clear both counters.
        state.stuck_2fa_count = 0
        state.last_outcome = f"healthy:{health.service_state}/{health.auth_state}"
        save_state(state_path, state)
        record_service_health("ok")
        return state

    # API hang detected — increment.
    state.degraded_count += 1
    LOG.warning(
        "api hang detected (cycle %s/%s) — port=%s upstream_dead=%s auth=%s",
        state.degraded_count,
        threshold,
        health.port_listening,
        health.upstream_dead,
        health.auth_state,
    )

    if state.degraded_count < threshold:
        state.last_outcome = f"degraded_{state.degraded_count}_of_{threshold}"
        save_state(state_path, state)
        record_service_health(
            "ok",  # Watchdog itself is fine; just observing degradation
            error_message=None,
        )
        return state

    # Threshold hit — but before restarting, check the cross-process 2FA
    # push lock. If another holder (typically scripts.api.ib_gateway) is
    # mid-2FA-push, restarting here would stack a second push on top —
    # the failure pattern that motivated this lock. Skip this cycle and
    # let the FastAPI path's push resolve first.
    existing_lock = _check_2fa_push_lock_bounded(now=clock())
    if existing_lock is not None and existing_lock.holder != WATCHDOG_LOCK_HOLDER:
        LOG.warning(
            "api hang sustained %s cycles but 2FA push lock held by %r "
            "(expires in %ds) — refusing restart to avoid stacking IBKR pushes",
            state.degraded_count,
            existing_lock.holder,
            max(0, int(existing_lock.expires_at - clock())),
        )
        # IMPORTANT: keep the counter where it is. The hang is still
        # real, and once the lock clears we want the next cycle to
        # act immediately (no fresh 3-cycle warm-up).
        state.last_outcome = (
            f"2fa_push_in_flight:{existing_lock.holder}:"
            f"degraded_{state.degraded_count}_of_{threshold}"
        )
        save_state(state_path, state)
        record_service_health(
            "ok",
            error_message=(
                f"deferred restart — 2FA push from {existing_lock.holder} in flight"
            ),
        )
        return state

    # Take the lock BEFORE issuing the restart. If acquire fails (extremely
    # rare race), treat it the same as "another holder owns it" and skip.
    acquired, lock_now = _acquire_2fa_push_lock_bounded(
        now=clock(),
        reason=f"api hang sustained {state.degraded_count} cycles",
    )
    if not acquired:
        LOG.warning(
            "2FA push lock raced: now held by %r — refusing restart",
            lock_now.holder if lock_now else "unknown",
        )
        state.last_outcome = "2fa_push_lock_race"
        save_state(state_path, state)
        record_service_health("ok")
        return state

    LOG.warning(
        "api hang sustained %s cycles — triggering %s restart",
        state.degraded_count,
        restart_unit,
    )
    ok = trigger_restart(restart_unit, dry_run=dry_run)
    state.last_restart_at = clock()
    state.degraded_count = 0  # Reset; let the next cycle observe recovery
    state.last_outcome = f"restarted:{'ok' if ok else 'fail'}"
    save_state(state_path, state)
    record_service_health(
        "error" if ok else "error",
        error_message=(
            f"triggered {restart_unit} restart after {threshold} cycles of api hang"
            if ok
            else f"FAILED to restart {restart_unit} after {threshold} cycles of api hang"
        ),
    )
    return state


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--health-url", default=DEFAULT_HEALTH_URL)
    p.add_argument(
        "--health-timeout",
        type=float,
        default=DEFAULT_HEALTH_TIMEOUT_SECS,
    )
    p.add_argument("--state-path", type=Path, default=DEFAULT_STATE_PATH)
    p.add_argument("--restart-unit", default=DEFAULT_RESTART_UNIT)
    p.add_argument(
        "--threshold",
        type=int,
        default=DEFAULT_THRESHOLD_CYCLES,
        help="consecutive degraded cycles before restart",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="log the restart command instead of running it",
    )
    return p.parse_args(argv)


def _abort_on_cycle_timeout(signum, frame):  # noqa: ARG001 — signal handler ABI
    """SIGALRM handler: a sub-step blocked past the hard ceiling. Log and exit
    so the next every-minute oneshot tick can run. A hung cycle that never
    exits is the proven cause of the gateway staying unreachable for hours."""
    LOG.error(
        "cycle exceeded hard timeout of %ss: aborting and exiting so the next "
        "tick can run",
        CYCLE_HARD_TIMEOUT_SECS,
    )
    os._exit(2)


def _arm_cycle_alarm() -> bool:
    """Arm the whole-cycle SIGALRM watchdog. Returns True if armed.

    SIGALRM is only available on the main thread of a Unix process, which is
    exactly where the oneshot CLI runs. If unavailable (Windows / non-main
    thread under test), we skip arming — the per-sub-step bounds still apply.
    """
    if not hasattr(signal, "SIGALRM"):
        return False
    try:
        signal.signal(signal.SIGALRM, _abort_on_cycle_timeout)
        signal.alarm(CYCLE_HARD_TIMEOUT_SECS)
        return True
    except (ValueError, OSError) as exc:
        LOG.warning("could not arm cycle alarm (%s); relying on sub-step bounds", exc)
        return False


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    armed = _arm_cycle_alarm()
    try:
        state = run_cycle(
            health_url=args.health_url,
            health_timeout=args.health_timeout,
            state_path=args.state_path,
            restart_unit=args.restart_unit,
            threshold=args.threshold,
            dry_run=args.dry_run,
        )
    finally:
        if armed:
            signal.alarm(0)  # disarm — cycle finished within the ceiling
    LOG.info("cycle done: %s", state.last_outcome)
    return 0


if __name__ == "__main__":
    sys.exit(main())
