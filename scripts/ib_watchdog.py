#!/usr/bin/env python3
"""IB Gateway API-aware watchdog.

Docker's TCP healthcheck only confirms the JVM has a socket bound on
port 4001. It can't tell whether the IB API thread is actually
responsive — a state we've hit three times in 24h on Hetzner where
the gateway accepts TCP connections but the API handshake hangs
forever, leaving fill-monitor / journal-sync / orders all timing out
while Docker reports "healthy."

This watchdog watches FastAPI's `/health` (which DOES probe the API
layer via the existing `ib_pool` machinery) and triggers the fixed
preheld-lease restart adapter after sustained degradation. Cadence:
60s, threshold: 3 consecutive degraded
readings (= ~3 min of real-time hang).

State persists at ``STATE_PATH`` so each oneshot invocation can pick
up the counter from the previous one. Reset on healthy, or on any
signal that isn't this specific degradation pattern (e.g. awaiting
2FA — which the 2FA backoff in scripts/api/ib_gateway.py already
handles and is NOT this bug).

DUR-10 hardening:
  - SECOND SENSOR: when /health is unreachable (radon-api down, deploy
    window) the watchdog probes the gateway DIRECTLY — bounded TCP
    connect + IB API handshake — instead of going blind. The fallback
    may only CONTINUE an existing api-hang episode (the handshake
    itself must show upstream-dead); "api unreachable" alone can never
    mint a new restart trigger, because every restart costs a 2FA push.
  - QUIET WINDOWS around the gateway's own scheduled nightly restart,
    when the relogin transiently looks exactly like an api-hang (see
    ``quiet_window_active``).
  - service_health heartbeats go over the bounded stdlib hrana HTTP
    transport (``scripts/db/hrana_http``), never sync libsql.
  - one per-step duration summary line per cycle (``CycleTimings``).

See ``docs/ib-gateway-healthcheck-hardening.md`` for the design.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
DEFAULT_RESTART_UNIT = "radon-ib-gateway-preheld-restart.service"
# After a watchdog-initiated gateway restart resolves auth back to
# `authenticated`, the FastAPI ib_pool can stay stuck disconnected — every
# IB route then 502s "unresponsive" until radon-api is bounced once. This is
# the documented pool-stuck-after-2FA remedy (feedback_ib_pool_stuck_after_2fa).
DEFAULT_API_UNIT = "radon-api.service"
# A gateway restart briefly exposed a stale authenticated sample on 2026-07-11
# before settling at the 2FA prompt. Require consecutive samples before the
# one-shot API pool reconnect so a transient cannot be mistaken for recovery.
RECOVERY_CONFIRMATION_CYCLES = 2
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

# --- DUR-10: direct gateway probe (second sensor) ----------------------------

# Host/port resolution matches the relay (ib_realtime_server.js) and FastAPI
# (scripts/api/ib_gateway.py): IB_GATEWAY_HOST / IB_GATEWAY_PORT, defaulting
# to localhost:4001 (on the VPS the container binds 4001 to loopback).
GATEWAY_PROBE_CONNECT_TIMEOUT_SECS = 3.0
GATEWAY_PROBE_HANDSHAKE_TIMEOUT_SECS = 5.0

# Verdicts from probe_gateway_direct.
GATEWAY_ALIVE = "alive"      # TCP + IB API handshake answered
GATEWAY_WEDGED = "wedged"    # TCP accepted but the API never answered — upstream-dead
GATEWAY_DEAD = "dead"        # TCP refused — watchdog recovery ladder owns it
GATEWAY_UNKNOWN = "unknown"  # connect timeout / network ambiguity — can't tell

# The isolated health daemon (scripts/health_service, :8330). Used ONLY to
# attribute api-down vs box-down for the service_health message — NEVER for
# gateway state: its ib-gateway probe is TCP-only, which is exactly the
# port-listening-but-upstream-dead false-healthy this watchdog exists to catch.
HEALTH_DAEMON_STATUS_URL = os.environ.get(
    "RADON_HEALTH_STATUS_URL", "http://127.0.0.1:8330/status"
)
HEALTH_DAEMON_TIMEOUT_SECS = 3.0

# --- DUR-10: scheduled-restart quiet windows ----------------------------------

# IBC AutoRestartTime and ColdRestartTime are disabled because neither can
# acquire the 2FA lease. Keep only the observed 00:00 UTC server-session
# rollover window; a functional failure is suppressed for at most the bounded
# grace below, then enters the normal recovery ladder.
QUIET_WINDOWS_ENV = "RADON_GW_RESTART_QUIET_WINDOWS_UTC"
DEFAULT_QUIET_WINDOWS_UTC = "23:40-00:15"
# A quiet window identifies when a scheduled restart is plausible; it is not
# permission to ignore a real outage for the entire window. Persist the first
# functional failure and suppress for at most five minutes.
try:
    _scheduled_grace = int(os.environ.get("RADON_GW_SCHEDULED_RESTART_GRACE_SECS", "300"))
except (TypeError, ValueError):
    _scheduled_grace = 300
SCHEDULED_RESTART_GRACE_SECS = max(0, min(900, _scheduled_grace))


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


# --- Per-step cycle timing (DUR-10) -------------------------------------------


class CycleTimings:
    """Collects per-step wall-clock durations for the single summary line
    each cycle logs — the evidence trail for hunting residual hangs."""

    def __init__(self) -> None:
        self._steps: dict[str, float] = {}
        self._started = time.monotonic()

    @contextmanager
    def step(self, label: str):
        t0 = time.monotonic()
        try:
            yield
        finally:
            self._steps[label] = self._steps.get(label, 0.0) + (time.monotonic() - t0)

    def summary(self) -> str:
        parts = " ".join(f"{k}={v:.2f}s" for k, v in self._steps.items())
        total = time.monotonic() - self._started
        return f"{parts} total={total:.2f}s".strip()


# The cycle is a single-threaded oneshot; a module-level current-timings slot
# lets deep call sites (record_service_health, forensics) attribute their
# durations without threading a parameter through every signature.
_ACTIVE_TIMINGS: Optional[CycleTimings] = None


@contextmanager
def _timed(label: str):
    timings = _ACTIVE_TIMINGS
    if timings is None:
        yield
    else:
        with timings.step(label):
            yield


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
    probe_timed_out: bool = False

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
            probe_timed_out=bool(gw.get("probe_timed_out", False)),
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


# --- Second sensor: direct gateway probe (DUR-10) -----------------------------

# The IB API handshake prefix + supported-version range, exactly what ibapi
# clients send first. Any reply at all proves the API thread is dispatching.
_IB_API_SIGNATURE = b"API\x00"
_IB_API_VERSION_RANGE = b"v100..187"


def probe_gateway_direct(
    host: Optional[str] = None,
    port: Optional[int] = None,
    *,
    connect_timeout: float = GATEWAY_PROBE_CONNECT_TIMEOUT_SECS,
    handshake_timeout: float = GATEWAY_PROBE_HANDSHAKE_TIMEOUT_SECS,
) -> str:
    """Bounded direct gateway probe: TCP connect plus an IB API handshake
    (server-version exchange). All stdlib; every socket op carries a timeout.

    A bare TCP connect is NOT enough — socat keeps 4001 listening while the
    JVM's API thread is wedged, which is the precise false-healthy this
    watchdog exists to catch. The handshake separates the cases:

      GATEWAY_ALIVE    server answered the version exchange
      GATEWAY_WEDGED   TCP accepted but the API never answered (recv timeout)
                       or the upstream closed without a handshake (socat
                       accepting while the JVM socket is gone)
      GATEWAY_DEAD     TCP refused — port down; the watchdog recovery ladder
                       owns restart decisions
      GATEWAY_UNKNOWN  connect timeout / other network ambiguity
    """
    host = host or os.environ.get("IB_GATEWAY_HOST", "127.0.0.1")
    port = port or int(os.environ.get("IB_GATEWAY_PORT", "4001"))
    try:
        sock = socket.create_connection((host, port), timeout=connect_timeout)
    except ConnectionRefusedError:
        return GATEWAY_DEAD
    except OSError as exc:
        LOG.warning("direct gateway probe: connect to %s:%s failed: %s", host, port, exc)
        return GATEWAY_UNKNOWN
    try:
        sock.settimeout(handshake_timeout)
        sock.sendall(
            _IB_API_SIGNATURE
            + struct.pack(">I", len(_IB_API_VERSION_RANGE))
            + _IB_API_VERSION_RANGE
        )
        data = sock.recv(4096)
        return GATEWAY_ALIVE if data else GATEWAY_WEDGED
    except (socket.timeout, TimeoutError):
        return GATEWAY_WEDGED
    except ConnectionResetError:
        # socat resets the client when its upstream connect to the JVM fails.
        return GATEWAY_WEDGED
    except OSError as exc:
        LOG.warning("direct gateway probe: handshake error: %s", exc)
        return GATEWAY_UNKNOWN
    finally:
        sock.close()


def attribute_api_down(
    status_url: str = HEALTH_DAEMON_STATUS_URL,
    timeout: float = HEALTH_DAEMON_TIMEOUT_SECS,
) -> str:
    """Ask the isolated health daemon (:8330) WHY /health is unreachable —
    attribution for the operator only. Deliberately NEVER consulted for
    gateway state (its ib-gateway probe is TCP-only)."""
    try:
        with urllib.request.urlopen(status_url, timeout=timeout) as resp:
            payload = json.loads(resp.read(1_048_576))
    except Exception:  # noqa: BLE001 — attribution is best-effort
        return "attribution_unavailable"
    probe = (payload.get("probes") or {}).get("radon-api") or {}
    state = probe.get("state")
    if state == "down":
        return "radon_api_down"
    if state == "up":
        return "radon_api_probe_up"  # race: api came back between our probes
    return "attribution_unavailable"


# --- Scheduled-restart quiet windows (DUR-10) ----------------------------------


def _parse_quiet_windows(spec: str) -> list[tuple[int, int]]:
    """Parse "HH:MM-HH:MM[,HH:MM-HH:MM...]" (UTC) into minute-of-day pairs.
    Bad entries are skipped with a warning — a typo must never disable the
    watchdog, only the window."""
    windows: list[tuple[int, int]] = []
    for entry in spec.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            start_text, end_text = entry.split("-")
            sh, sm = (int(p) for p in start_text.strip().split(":"))
            eh, em = (int(p) for p in end_text.strip().split(":"))
            if not (0 <= sh < 24 and 0 <= eh < 24 and 0 <= sm < 60 and 0 <= em < 60):
                raise ValueError(entry)
            windows.append((sh * 60 + sm, eh * 60 + em))
        except ValueError:
            LOG.warning("ignoring malformed quiet-window entry %r", entry)
    return windows


def quiet_window_active(now: Optional[datetime] = None) -> bool:
    """True while inside a scheduled-gateway-restart quiet window (UTC).

    During a window, api-hang/stuck-2FA detections are logged (and forensics
    may fire) but no counter advances toward restart and the watchdog can
    initiate no 2FA push — the gateway's own relogin is in progress and a
    forced restart would stack pushes. Start inclusive, end exclusive;
    windows may wrap midnight (23:40-00:15)."""
    spec = os.environ.get(QUIET_WINDOWS_ENV, DEFAULT_QUIET_WINDOWS_UTC)
    now = now if now is not None else datetime.now(timezone.utc)
    minute = now.hour * 60 + now.minute
    for start, end in _parse_quiet_windows(spec):
        if start <= end:
            if start <= minute < end:
                return True
        elif minute >= start or minute < end:  # wraps midnight
            return True
    return False


# Watchdog only fires 2FA-chasing gateway restarts when the data plane is
# actually needed: a trading day (holiday/weekend aware via market_calendar),
# within this ET window (pre-open warmup → close). Off-hours / weekends /
# holidays → restarts freeze (alert-only). Without this, IBKR's weekend session
# expiry triggers a 2FA-push storm the operator can't use until Monday — the
# 2026-06-21 Sunday incident fired ~51 restarts, each a fresh push.
TWOFA_CHASE_WINDOW_ENV = "RADON_GW_2FA_CHASE_WINDOW_ET"
DEFAULT_2FA_CHASE_WINDOW_ET = "07:30-16:00"


def data_plane_window_active(now: Optional[datetime] = None) -> bool:
    """True when the IB data plane is needed → the watchdog may restart the
    gateway to chase 2FA.

    Fail-OPEN (returns True) on any error so a calendar/parse hiccup preserves
    2FA recovery rather than silently leaving the gateway un-recovered at the
    open. Restarts are only SUPPRESSED when we positively determine the market
    is closed (weekend / holiday / outside the pre-open→close window).
    """
    try:
        import zoneinfo

        from utils import market_calendar

        now = now if now is not None else datetime.now(timezone.utc)
        et = zoneinfo.ZoneInfo("America/New_York")
        et_now = now.astimezone(et) if now.tzinfo else now
        if not market_calendar._is_trading_day(et_now):
            return False  # weekend or US market holiday
        spec = os.environ.get(TWOFA_CHASE_WINDOW_ENV, DEFAULT_2FA_CHASE_WINDOW_ET)
        start_text, end_text = spec.split("-")
        sh, sm = (int(p) for p in start_text.strip().split(":"))
        eh, em = (int(p) for p in end_text.strip().split(":"))
        minute = et_now.hour * 60 + et_now.minute
        return (sh * 60 + sm) <= minute <= (eh * 60 + em)
    except Exception:
        LOG.warning("data_plane_window_active: failing open (could not resolve market state)")
        return True


# --- Degradation classification ---------------------------------------------


def is_api_hang(state: GatewayState) -> bool:
    """The specific failure mode this watchdog exists to catch.

    TCP socket is bound (port_listening) but the upstream API isn't
    responding to handshakes (upstream_dead). This is the JVM
    API-acceptor hang, distinct from a fully-down gateway. The direct
    functional probe routes both modes into the same watchdog ladder; Docker
    automatic restart is disabled because it cannot acquire the 2FA lease.

    ``upstream_dead`` is the decisive signal and overrides every
    auth_state EXCEPT ``awaiting_2fa``. While the gateway is parked at
    the 2FA push prompt the upstream API is legitimately not answering
    handshakes, so ``upstream_dead`` reads True — but restarting there
    cold-starts the gateway into a FULL login that mints a NEW push and
    invalidates the pending one. On 2026-07-05 that loop fired 48
    restarts / 48 pushes overnight; ``awaiting_2fa`` is therefore NEVER
    the api-hang — the dedicated stand-down (no restart, one alert per
    episode) owns it. This deliberately reverses the 2026-06-15 override
    (dead JVM acceptor with cached ``awaiting_2fa`` misrouted to the
    stuck-2FA push path, 15 restarts) WITHOUT reopening it: the genuine
    stuck-2FA case (``is_stuck_awaiting_2fa``) still requires
    ``upstream_dead`` False, so awaiting_2fa + upstream_dead now
    restarts NOTHING instead of restarting the wrong thing.
    """
    if not state.port_listening:
        return False  # Direct-probe DEAD classification owns the port-down path.
    if state.auth_state == "awaiting_2fa":
        return False  # Stand-down owns awaiting_2fa (2026-07-05 storm)
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
      - upstream is NOT dead (a wedged JVM acceptor is the api-hang,
        NOT a 2FA problem — firing a fresh push does nothing for a dead
        upstream and only spams IBKR; see `is_api_hang` + the 2026-06-15
        15× restart loop)
      - no push lock holder (nothing else has a push in flight)
      - no scheduled retry pending (`next_attempt_in_secs <= 0`)
    The push lock is the cross-process safety against stacking — IBKR
    rejects every approval when multiple pushes are pending. See
    `feedback_2fa_push_stacking.md`.
    """
    if state.auth_state != "awaiting_2fa":
        return False
    if state.upstream_dead:
        return False  # JVM acceptor hang → is_api_hang owns this
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
    # Epoch timestamps of watchdog-initiated stuck-2FA gateway restarts.
    # Bounds the stuck-2FA branch to STUCK_2FA_MAX_RESTARTS_PER_HOUR within
    # a rolling hour with exponential backoff between them, so this branch
    # can NEVER loop 15× again (2026-06-15 incident).
    stuck_2fa_restart_history: list = field(default_factory=list)
    # Consecutive watchdog-initiated api-hang gateway restarts with no
    # intervening recovery to `authenticated`. Bounds the api-hang ladder
    # (exponential backoff + hard cap): without it, degraded_count resets to
    # 0 on every restart and 3 fresh 60s cycles re-arm the ladder — the
    # ~6-min restart cadence of the 2026-07-05 storm (48 restarts/pushes).
    api_hang_restart_count: int = 0
    # True once the one-per-episode awaiting-2FA stand-down alert has been
    # emitted; cleared when auth leaves `awaiting_2fa` (episode over).
    awaiting_2fa_alerted: bool = False
    # Set True when THIS watchdog restarts the gateway (api-hang or stuck-2FA
    # path). When a later cycle observes auth back at `authenticated`, the
    # watchdog bounces radon-api ONCE to un-stick the ib_pool, then clears the
    # flag. Idempotent: the api restart fires on the auth→authenticated edge,
    # never in a loop (feedback_ib_pool_stuck_after_2fa).
    pending_pool_reconnect: bool = False
    # Consecutive authenticated samples observed while a watchdog-initiated
    # gateway recovery is pending. Any non-authenticated sample resets it.
    authenticated_recovery_count: int = 0
    # First epoch at which a functional failure was observed inside a scheduled
    # restart window. Suppression expires after SCHEDULED_RESTART_GRACE_SECS.
    quiet_degraded_since: float = 0.0

    def to_dict(self) -> dict:
        return {
            "degraded_count": self.degraded_count,
            "last_restart_at": self.last_restart_at,
            "last_outcome": self.last_outcome,
            "stuck_2fa_count": self.stuck_2fa_count,
            "stuck_2fa_restart_history": list(self.stuck_2fa_restart_history),
            "api_hang_restart_count": self.api_hang_restart_count,
            "awaiting_2fa_alerted": self.awaiting_2fa_alerted,
            "pending_pool_reconnect": self.pending_pool_reconnect,
            "authenticated_recovery_count": self.authenticated_recovery_count,
            "quiet_degraded_since": self.quiet_degraded_since,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WatchdogState":
        history = data.get("stuck_2fa_restart_history", [])
        return cls(
            degraded_count=int(data.get("degraded_count", 0)),
            last_restart_at=float(data.get("last_restart_at", 0.0)),
            last_outcome=str(data.get("last_outcome", "init")),
            stuck_2fa_count=int(data.get("stuck_2fa_count", 0)),
            stuck_2fa_restart_history=[float(t) for t in history]
            if isinstance(history, list)
            else [],
            api_hang_restart_count=int(data.get("api_hang_restart_count", 0)),
            awaiting_2fa_alerted=bool(data.get("awaiting_2fa_alerted", False)),
            pending_pool_reconnect=bool(data.get("pending_pool_reconnect", False)),
            authenticated_recovery_count=int(
                data.get("authenticated_recovery_count", 0)
            ),
            quiet_degraded_since=float(data.get("quiet_degraded_since", 0.0)),
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
    """Run Gateway recovery or restart one non-Gateway unit.

    The fixed preheld adapter owns the Gateway cycle. Wait for the oneshot so
    a successful systemd handoff is not mistaken for a successful recovery;
    otherwise failed helpers consume the bounded restart budget while the
    broker remains down. Other unit restarts route through the locked,
    root-owned operator instead of broad systemctl privilege.
    """
    is_preheld_gateway = unit == DEFAULT_RESTART_UNIT
    if is_preheld_gateway:
        cmd = ["systemctl", "start", unit]
        timeout = 40
    else:
        cmd = ["/usr/local/bin/radon", "unit", "restart", unit]
        timeout = 40
    if dry_run:
        LOG.info("[dry-run] would run: %s", " ".join(cmd))
        return True
    try:
        subprocess.run(cmd, check=True, timeout=timeout)
        return True
    except subprocess.CalledProcessError as exc:
        LOG.error("restart handoff failed: rc=%s stderr=%s", exc.returncode, exc.stderr)
        if is_preheld_gateway:
            ib_2fa_lock.release_2fa_push_lock(expected_holder=WATCHDOG_LOCK_HOLDER)
        return False
    except subprocess.TimeoutExpired:
        LOG.error("restart handoff timed out after %ss", timeout)
        if is_preheld_gateway:
            ib_2fa_lock.release_2fa_push_lock(expected_holder=WATCHDOG_LOCK_HOLDER)
        return False


# --- service_health write ---------------------------------------------------


def _write_service_health(
    state_label: str,
    error_message: Optional[str],
) -> None:
    """Perform the actual Turso write over the bounded stdlib hrana HTTP
    transport (``scripts/db/hrana_http``) — NEVER sync libsql: its native
    calls have no timeout and were the suspected source of this watchdog's
    post-Connection-refused 60s SIGTERM kills (148 timeout results in 7
    days). Module-level so tests can patch it directly; the import is lazy
    so dev environments without creds stay importable."""
    try:
        from db.hrana_http import write_service_health_http
    except ImportError:
        from scripts.db.hrana_http import write_service_health_http

    write_service_health_http(
        "ib-watchdog",
        state_label,
        error={"message": error_message} if error_message else None,
    )


def record_service_health(
    state_label: str,
    error_message: Optional[str] = None,
) -> None:
    """Best-effort heartbeat into Turso so the banner can show our state.

    The hrana transport carries a real 4s socket timeout; ``_run_bounded``
    stays as belt-and-suspenders (urllib's timeout is per-socket-op, not
    total-duration). On timeout we skip the heartbeat for this cycle; the
    row is best-effort by contract. Missing creds (dev environments) are
    tolerated quietly.
    """
    try:
        with _timed("health_write"):
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
        if "not configured" in str(exc):
            LOG.debug("service_health write skipped: %s", exc)
        else:
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
        return ib_2fa_lock.check_2fa_push_lock(
            now=now,
            guard_timeout_secs=LOCK_OP_TIMEOUT_SECS,
        )
    except ib_2fa_lock.GuardLockTimeout as exc:
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
        return ib_2fa_lock.acquire_2fa_push_lock(
            WATCHDOG_LOCK_HOLDER,
            ttl_secs=ib_2fa_lock.DEFAULT_LOCK_TTL_SECS,
            reason=reason,
            now=now,
            guard_timeout_secs=LOCK_OP_TIMEOUT_SECS,
        )
    except ib_2fa_lock.GuardLockTimeout as exc:
        LOG.warning("2FA lock acquire timed out (%s); skipping restart", exc)
        return (False, None)


# --- Stuck-awaiting-2FA handler ---------------------------------------------

# Three cycles (~3 min) of confirmed stuck-2FA before firing a fresh push.
# Lower than the api-hang threshold (also 3) but separate so a user who's
# slow to approve a push isn't punished by an immediate re-fire.
DEFAULT_STUCK_2FA_THRESHOLD = 3

# Rolling-hour cap on watchdog-initiated stuck-2FA gateway restarts. Each
# restart is a real IBKR 2FA push; on 2026-06-15 a misclassified dead-upstream
# looped 15 restarts/pushes in ~3h. Even genuine awaiting_2fa must be bounded:
# if two fresh pushes inside an hour don't resolve auth, a third won't either —
# something needs an operator, not more pushes.
STUCK_2FA_RESTART_WINDOW_SECS = 3600
STUCK_2FA_MAX_RESTARTS_PER_HOUR = 2
# Exponential backoff between consecutive stuck-2FA restarts within the window:
# base * 2**(restarts_already_in_window). Spaces a legitimate second push out
# from the first so the user has time to approve before we re-fire.
STUCK_2FA_BACKOFF_BASE_SECS = 300


def _prune_restart_history(history: list, now: float) -> list:
    """Drop restart timestamps older than the rolling window."""
    cutoff = now - STUCK_2FA_RESTART_WINDOW_SECS
    return [t for t in history if t >= cutoff]


def _stuck_2fa_restart_blocked(history: list, now: float) -> Optional[str]:
    """Return a human-readable reason the stuck-2FA restart is blocked this
    cycle (rolling-hour cap or backoff not yet elapsed), or None if it may
    proceed. ``history`` must already be pruned to the window."""
    if len(history) >= STUCK_2FA_MAX_RESTARTS_PER_HOUR:
        return (
            f"stuck_2fa_cap:{len(history)}_of_"
            f"{STUCK_2FA_MAX_RESTARTS_PER_HOUR}_in_hour"
        )
    if history:
        backoff = STUCK_2FA_BACKOFF_BASE_SECS * (2 ** (len(history) - 1))
        elapsed = now - max(history)
        if elapsed < backoff:
            return f"stuck_2fa_backoff:{int(backoff - elapsed)}s_remaining"
    return None


def _handle_stuck_awaiting_2fa(
    *,
    state: "WatchdogState",
    state_path: Path,
    restart_unit: str,
    dry_run: bool,
    clock: callable,
    threshold: int = DEFAULT_STUCK_2FA_THRESHOLD,
    quiet: bool = False,
) -> "WatchdogState":
    """Increment the stuck-2FA counter and, when threshold is hit, fire a
    fresh push by restarting the IB Gateway unit. Respects the cross-process
    2FA push lock so we never stack a second push on a pending one — that
    pattern is what motivated the lock in the first place (see
    `feedback_2fa_push_stacking.md`).
    """
    if quiet:
        # Scheduled-restart quiet window: the gateway's own relogin may be
        # parked at a transient 2FA-ish state. The watchdog must initiate
        # NO push here — freeze the counter (don't reset: post-window the
        # next cycle resumes from where we were).
        LOG.warning(
            "stuck-2FA signature while gateway restarts are frozen (scheduled "
            "quiet window or market closed) — freezing counter at %s, no push",
            state.stuck_2fa_count,
        )
        state.last_outcome = "quiet_window:stuck_2fa_frozen"
        save_state(state_path, state)
        record_service_health("ok")
        return state

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

    # Threshold hit. Rolling-hour cap + exponential backoff BEFORE any lock
    # work or restart — even a legitimately-stuck 2FA can NEVER loop 15× like
    # 2026-06-15. If two pushes inside an hour haven't resolved auth, a third
    # won't; the situation needs an operator, not more pushes.
    state.stuck_2fa_restart_history = _prune_restart_history(
        state.stuck_2fa_restart_history, clock()
    )
    blocked = _stuck_2fa_restart_blocked(state.stuck_2fa_restart_history, clock())
    if blocked is not None:
        LOG.warning(
            "stuck_2fa threshold hit but restart capped/backed-off (%s) — "
            "no push this cycle",
            blocked,
        )
        # Keep the counter at threshold (don't reset) so the moment the cap /
        # backoff clears the next cycle can act immediately.
        state.stuck_2fa_count = threshold
        state.last_outcome = blocked
        save_state(state_path, state)
        record_service_health(
            "ok",
            error_message=(
                f"stuck awaiting 2FA but watchdog restart {blocked} — "
                "operator may need to intervene"
            ),
        )
        return state

    # Respect any in-flight push acquired between our /health probe and now.
    # Holder names identify components, not logical operations, so a prior
    # watchdog lease blocks a second watchdog cycle too.
    existing_lock = _check_2fa_push_lock_bounded(now=clock())
    if existing_lock is not None:
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
    restart_at = clock()
    state.last_restart_at = restart_at
    # Record this restart in the rolling-hour history (only on a real attempt)
    # so the cap + backoff see it next cycle.
    state.stuck_2fa_restart_history = _prune_restart_history(
        state.stuck_2fa_restart_history, restart_at
    )
    state.stuck_2fa_restart_history.append(restart_at)
    state.stuck_2fa_count = 0  # Reset; next cycle observes recovery
    # Arm the one-shot radon-api bounce once auth resolves (see above).
    state.pending_pool_reconnect = True
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


# --- Awaiting-2FA stand-down (2026-07-05 storm) --------------------------------


def _stand_down_awaiting_2fa(
    *,
    state: "WatchdogState",
    state_path: Path,
    health: GatewayState,
) -> "WatchdogState":
    """The gateway is parked at the 2FA push prompt with a dead upstream
    (e.g. the weekly session-token expiry turned the scheduled AutoRestartTime
    into a FULL login). Any restart here cold-starts the gateway into another
    full login — a NEW push that invalidates the pending one, the exact loop
    of the 2026-07-05 storm. The watchdog stands down: no restart regardless
    of push-lock state, hang counter already reset by the caller, ONE
    service_health error heartbeat every cycle. Repeated writes keep writer
    liveness current; downstream state-transition/cooldown logic owns alert
    deduplication. Normal monitoring resumes when auth leaves
    ``awaiting_2fa``."""
    LOG.warning(
        "gateway awaiting 2FA (upstream_dead=%s) — watchdog standing down: "
        "no restart, approve the pending push on IBKR Mobile",
        health.upstream_dead,
    )
    state.awaiting_2fa_alerted = True
    state.last_outcome = "standing_down_awaiting_2fa"
    save_state(state_path, state)
    record_service_health(
        "error",
        error_message=(
            "IB Gateway parked awaiting 2FA (upstream dead) — watchdog "
            "standing down (restarting would mint a new push); approve "
            "the push on IBKR Mobile or force one from /admin"
        ),
    )
    return state


# --- DUR-08: JVM forensic capture on first hang detection --------------------

# How many seconds of the cycle the forensic capture may consume. Must stay
# well inside CYCLE_HARD_TIMEOUT_SECS minus the health probe + service_health
# write bounds, or the SIGALRM ceiling could kill the cycle mid-capture.
FORENSICS_BUDGET_SECS = 25.0


def _capture_hang_forensics() -> None:
    """Snapshot JVM evidence (thread dump via kill -3, docker logs/stats/ps)
    the FIRST time is_api_hang trips in an episode — before the restart
    ladder recycles the JVM and destroys the evidence. Bounded and
    best-effort by contract: any failure logs and returns; the restart
    ladder must never be blocked or delayed past its schedule."""
    try:
        import jvm_forensics

        with _timed("forensics"):
            jvm_forensics.capture_jvm_forensics(budget_secs=FORENSICS_BUDGET_SECS)
    except Exception as exc:  # noqa: BLE001 — forensics never block the ladder
        LOG.warning("jvm forensic capture failed (non-fatal): %s", exc)


# --- Fallback path: /health unreachable (DUR-10 second sensor) ----------------


def _handle_primary_sensor_down(
    *,
    state: "WatchdogState",
    state_path: Path,
    restart_unit: str,
    threshold: int,
    dry_run: bool,
    clock: callable,
    quiet: bool,
) -> "WatchdogState":
    """/health is unreachable — probe the gateway DIRECTLY rather than going
    blind. The direct protocol probe is the independent source of truth and
    owns both port-down and wedged recovery now that Docker restart is disabled.
    """
    with _timed("direct_probe"):
        verdict = probe_gateway_direct()
        attribution = attribute_api_down()
    LOG.warning(
        "primary sensor (/health) unreachable — direct gateway probe=%s "
        "attribution=%s degraded_count=%s",
        verdict,
        attribution,
        state.degraded_count,
    )
    # Without /health we can't see auth_state; never age the stuck-2FA
    # counter across an api outage (pre-DUR-10 behavior preserved).
    state.stuck_2fa_count = 0

    if verdict == GATEWAY_ALIVE:
        if state.degraded_count:
            LOG.info(
                "direct handshake answered — clearing api-hang episode (was %s)",
                state.degraded_count,
            )
        state.degraded_count = 0
        state.last_outcome = f"probe_unreachable:gateway_alive:{attribution}"
        save_state(state_path, state)
        record_service_health(
            "ok",
            error_message=(
                "primary sensor broken: radon-api /health unreachable "
                f"({attribution}); direct gateway handshake OK"
            ),
        )
        return state

    if verdict in (GATEWAY_DEAD, GATEWAY_WEDGED):
        # The independent functional sensor owns both a refused port and a
        # protocol-level wedge. Docker automatic restart is intentionally
        # disabled because it cannot participate in the 2FA push lease.
        return _advance_api_hang(
            state=state,
            state_path=state_path,
            restart_unit=restart_unit,
            threshold=threshold,
            dry_run=dry_run,
            clock=clock,
            quiet=quiet,
            evidence=f"direct gateway {verdict} while primary sensor down ({attribution})",
        )

    # GATEWAY_UNKNOWN — can't tell; freeze everything.
    state.last_outcome = f"probe_unreachable:gateway_unknown:{attribution}"

    save_state(state_path, state)
    record_service_health(
        "ok",
        error_message=(
            "primary sensor broken: radon-api /health unreachable "
            f"({attribution}); direct gateway probe={verdict}"
        ),
    )
    return state


# --- Api-hang ladder ----------------------------------------------------------

# Bounded remediation for the api-hang restart (2026-07-05 storm): the ladder
# resets degraded_count to 0 on every restart, so 3 fresh 60s cycles re-armed
# it and it fired at a fixed ~6-min cadence forever (48 restarts / 48 pushes
# in one night). Consecutive watchdog restarts without an intervening recovery
# to `authenticated` now back off exponentially (base * 2**(n-1), capped) and
# hard-stop at the cap — past it the watchdog alerts and stands down for the
# operator. `api_hang_restart_count` persists in the state file so the bound
# survives the oneshot process model, and resets on authenticated recovery.
API_HANG_MAX_CONSECUTIVE_RESTARTS = 3
API_HANG_BACKOFF_BASE_SECS = 360
API_HANG_BACKOFF_CAP_SECS = 3600


def _api_hang_restart_blocked(state: "WatchdogState", now: float) -> Optional[str]:
    """Return a human-readable reason the api-hang restart is blocked this
    cycle (consecutive cap or exponential backoff not yet elapsed), or None
    if it may proceed."""
    if state.api_hang_restart_count >= API_HANG_MAX_CONSECUTIVE_RESTARTS:
        return (
            f"api_hang_restart_cap:{state.api_hang_restart_count}_of_"
            f"{API_HANG_MAX_CONSECUTIVE_RESTARTS}"
        )
    if state.api_hang_restart_count > 0:
        backoff = min(
            API_HANG_BACKOFF_BASE_SECS * (2 ** (state.api_hang_restart_count - 1)),
            API_HANG_BACKOFF_CAP_SECS,
        )
        elapsed = now - state.last_restart_at
        if elapsed < backoff:
            return f"api_hang_backoff:{int(backoff - elapsed)}s_remaining"
    return None


def _advance_api_hang(
    *,
    state: "WatchdogState",
    state_path: Path,
    restart_unit: str,
    threshold: int,
    dry_run: bool,
    clock: callable,
    quiet: bool,
    evidence: str,
) -> "WatchdogState":
    """One confirmed api-hang observation: advance the counter and, at
    threshold, restart the gateway under the cross-process 2FA push lock.
    During a scheduled-restart quiet window the observation is logged (and
    forensics may fire) but the counter freezes and no push can start."""
    if quiet:
        now = clock()
        if state.quiet_degraded_since <= 0:
            state.quiet_degraded_since = now
        quiet_elapsed = max(0.0, now - state.quiet_degraded_since)
        if quiet_elapsed >= SCHEDULED_RESTART_GRACE_SECS:
            LOG.error(
                "scheduled-restart grace expired after %.0fs; counting functional "
                "failure despite quiet window (%s)",
                quiet_elapsed,
                evidence,
            )
            quiet = False

    if quiet:
        LOG.warning(
            "api hang observed (%s) during scheduled-restart quiet window — "
            "counter frozen at %s, no restart",
            evidence,
            state.degraded_count,
        )
        # Capture evidence once per quiet episode: a hang seen here has no
        # 0 -> 1 transition to hook, so key off the previous outcome.
        if state.degraded_count == 0 and not state.last_outcome.startswith(
            "quiet_window"
        ):
            _capture_hang_forensics()
        state.last_outcome = "quiet_window:api_hang_observed"
        save_state(state_path, state)
        record_service_health(
            "ok",
            error_message=(
                "api hang observed during scheduled-restart quiet window; "
                "suppressed (gateway's own restart in progress)"
            ),
        )
        return state

    state.quiet_degraded_since = 0.0

    state.degraded_count += 1
    LOG.warning(
        "api hang detected (cycle %s/%s) — %s",
        state.degraded_count,
        threshold,
        evidence,
    )

    # DUR-08: on the 0 -> 1 transition (new hang episode), capture JVM
    # forensics once before any restart destroys the evidence.
    if state.degraded_count == 1:
        _capture_hang_forensics()

    if state.degraded_count < threshold:
        state.last_outcome = f"degraded_{state.degraded_count}_of_{threshold}"
        save_state(state_path, state)
        record_service_health(
            "ok",  # Watchdog itself is fine; just observing degradation
            error_message=None,
        )
        return state

    # Threshold hit. Bounded remediation BEFORE any lock work or restart:
    # consecutive restarts back off exponentially and hard-stop at the cap,
    # so this ladder can never fire at a fixed cadence forever again
    # (2026-07-05). Counter is held at threshold so the first cycle after
    # the backoff clears acts immediately (no fresh warm-up).
    blocked = _api_hang_restart_blocked(state, clock())
    if blocked is not None:
        capped = blocked.startswith("api_hang_restart_cap")
        LOG.warning(
            "api hang sustained %s cycles but watchdog restart %s — "
            "standing down this cycle%s",
            state.degraded_count,
            blocked,
            " (restarts exhausted; operator intervention required)"
            if capped
            else "",
        )
        state.degraded_count = threshold
        state.last_outcome = blocked
        save_state(state_path, state)
        record_service_health(
            "error" if capped else "ok",
            error_message=(
                f"api hang persists but watchdog restart {blocked}"
                + (" — operator intervention required" if capped else "")
            ),
        )
        return state

    # Before restarting, check the cross-process 2FA push lock. Any active
    # lease, including one from a prior watchdog invocation, represents a
    # separate Gateway cycle whose push must resolve first.
    with _timed("lock"):
        existing_lock = _check_2fa_push_lock_bounded(now=clock())
    if existing_lock is not None:
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
    with _timed("lock"):
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
    with _timed("restart"):
        ok = trigger_restart(restart_unit, dry_run=dry_run)
    state.last_restart_at = clock()
    # Count the attempt toward the consecutive cap/backoff regardless of
    # outcome; only recovery to `authenticated` re-arms the ladder.
    state.api_hang_restart_count += 1
    state.degraded_count = 0  # Reset; let the next cycle observe recovery
    # Arm the one-shot radon-api bounce: once auth resolves back to
    # authenticated, the ib_pool may stay stuck disconnected until api is
    # restarted (feedback_ib_pool_stuck_after_2fa).
    state.pending_pool_reconnect = True
    state.last_outcome = f"restarted:{'ok' if ok else 'fail'}"
    save_state(state_path, state)
    record_service_health(
        "error",
        error_message=(
            f"triggered {restart_unit} restart after {threshold} cycles of api hang"
            if ok
            else f"FAILED to restart {restart_unit} after {threshold} cycles of api hang"
        ),
    )
    return state


# --- Pool reconnect after watchdog-initiated recovery ------------------------


def _reconnect_pool_after_recovery(
    *,
    state: "WatchdogState",
    state_path: Path,
    api_unit: str,
    dry_run: bool,
    health: GatewayState,
) -> "WatchdogState":
    """A prior watchdog-initiated gateway restart has resolved (auth back at
    authenticated). Bounce radon-api ONCE so the ib_pool — which can stay
    stuck disconnected after the gateway recovers — reconnects. The flag is
    cleared regardless of restart outcome so this fires exactly once per
    recovery and can never loop (feedback_ib_pool_stuck_after_2fa)."""
    LOG.warning(
        "gateway recovered to authenticated after watchdog restart — "
        "bouncing %s once to un-stick the ib_pool",
        api_unit,
    )
    with _timed("api_reconnect"):
        ok = trigger_restart(api_unit, dry_run=dry_run)
    state.pending_pool_reconnect = False  # one-shot — never loop
    state.authenticated_recovery_count = 0
    state.last_outcome = f"pool_reconnect:{'ok' if ok else 'fail'}:{health.auth_state}"
    save_state(state_path, state)
    record_service_health(
        "ok",
        error_message=(
            f"restarted {api_unit} once to reconnect ib_pool after gateway recovery"
            if ok
            else f"FAILED to restart {api_unit} for ib_pool reconnect"
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
    api_unit: str = DEFAULT_API_UNIT,
    threshold: int = DEFAULT_THRESHOLD_CYCLES,
    dry_run: bool = False,
    clock: callable = time.time,
    utcnow: Optional[Callable[[], datetime]] = None,
) -> WatchdogState:
    """Execute one cycle. Returns the final state for inspection / testing.

    Wraps the real work in :class:`CycleTimings` so every cycle — success or
    failure — ends with exactly one per-step duration summary line.
    """
    global _ACTIVE_TIMINGS
    timings = CycleTimings()
    _ACTIVE_TIMINGS = timings
    outcome = "crashed"
    try:
        state = _run_cycle_steps(
            health_url=health_url,
            health_timeout=health_timeout,
            state_path=state_path,
            restart_unit=restart_unit,
            api_unit=api_unit,
            threshold=threshold,
            dry_run=dry_run,
            clock=clock,
            utcnow=utcnow,
        )
        outcome = state.last_outcome
        return state
    finally:
        _ACTIVE_TIMINGS = None
        LOG.info("cycle steps: %s outcome=%s", timings.summary(), outcome)


def _run_cycle_steps(
    *,
    health_url: str,
    health_timeout: float,
    state_path: Path,
    restart_unit: str,
    api_unit: str,
    threshold: int,
    dry_run: bool,
    clock: callable,
    utcnow: Optional[Callable[[], datetime]],
) -> WatchdogState:
    state = load_state(state_path)
    _now_dt = (utcnow or (lambda: datetime.now(timezone.utc)))()
    quiet = quiet_window_active(_now_dt)
    # The stuck-2FA self-heal (NOT api-hang) additionally freezes when the data
    # plane isn't needed (weekend / holiday / off-hours): IBKR's weekend session
    # expiry parks the gateway at awaiting_2fa, and chasing it with restarts just
    # storms 2FA pushes nobody can use until the open (the 2026-06-21 incident).
    # api-hang stays 24/7 — a wedged JVM acceptor needs a restart whenever it
    # happens, and that's a different failure than a session that simply expired.
    stuck_2fa_quiet = quiet or not data_plane_window_active(_now_dt)

    with _timed("probe"):
        health = fetch_health(health_url, health_timeout)
    if health is None:
        state.authenticated_recovery_count = 0
        # Primary sensor down — fall back to the direct gateway probe
        # instead of going blind (DUR-10).
        return _handle_primary_sensor_down(
            state=state,
            state_path=state_path,
            restart_unit=restart_unit,
            threshold=threshold,
            dry_run=dry_run,
            clock=clock,
            quiet=quiet,
        )

    # `/health` and the persistent pool share failure modes. Probe the IB
    # protocol independently on every cycle so a green cached pool or a
    # structured `probe_timed_out` response cannot mask failure for new clients.
    with _timed("direct_probe"):
        functional_verdict = probe_gateway_direct()

    if health.auth_state == "awaiting_2fa" and functional_verdict in (
        GATEWAY_DEAD,
        GATEWAY_WEDGED,
    ):
        # Preserve the hard 2FA stand-down. Restarting here would invalidate the
        # pending push, regardless of what the independent transport sees.
        state.degraded_count = 0
        state.stuck_2fa_count = 0
        state.quiet_degraded_since = 0.0
        state.authenticated_recovery_count = 0
        return _stand_down_awaiting_2fa(
            state=state,
            state_path=state_path,
            health=GatewayState(
                service_state=health.service_state,
                port_listening=health.port_listening,
                upstream_dead=True,
                auth_state=health.auth_state,
                push_lock_active=health.push_lock_active,
                backoff_attempt_count=health.backoff_attempt_count,
                next_attempt_in_secs=health.next_attempt_in_secs,
                probe_timed_out=health.probe_timed_out,
            ),
        )

    if functional_verdict in (GATEWAY_DEAD, GATEWAY_WEDGED):
        state.authenticated_recovery_count = 0
        return _advance_api_hang(
            state=state,
            state_path=state_path,
            restart_unit=restart_unit,
            threshold=threshold,
            dry_run=dry_run,
            clock=clock,
            quiet=quiet,
            evidence=(
                f"independent protocol probe={functional_verdict} "
                f"http_timeout={health.probe_timed_out} "
                f"http_port={health.port_listening} auth={health.auth_state}"
            ),
        )

    if functional_verdict == GATEWAY_ALIVE:
        # Functional evidence wins over Docker's TCP-only health flag.
        health = GatewayState(
            service_state=health.service_state,
            port_listening=True,
            upstream_dead=False,
            auth_state=health.auth_state,
            push_lock_active=health.push_lock_active,
            backoff_attempt_count=health.backoff_attempt_count,
            next_attempt_in_secs=health.next_attempt_in_secs,
            probe_timed_out=health.probe_timed_out,
        )

    # An awaiting-2FA episode ends the moment auth reads anything else;
    # re-arm the one-per-episode stand-down alert.
    if health.auth_state != "awaiting_2fa":
        state.awaiting_2fa_alerted = False
    if health.auth_state != "authenticated":
        state.authenticated_recovery_count = 0

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
        state.quiet_degraded_since = 0.0

        # awaiting_2fa + dead upstream: the gateway is parked at the push
        # prompt (2026-07-05 storm). NO restart ladder owns this — stand
        # down, alert once per episode, resume when auth resolves.
        if health.auth_state == "awaiting_2fa" and health.upstream_dead:
            return _stand_down_awaiting_2fa(
                state=state, state_path=state_path, health=health
            )

        # Stuck-awaiting-2FA branch: separate failure mode from api-hang.
        if is_stuck_awaiting_2fa(health):
            return _handle_stuck_awaiting_2fa(
                state=state,
                state_path=state_path,
                restart_unit=restart_unit,
                dry_run=dry_run,
                clock=clock,
                threshold=threshold,
                quiet=stuck_2fa_quiet,
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

        # Genuinely healthy — clear both counters. Recovery to authenticated
        # also re-arms the bounded api-hang remediation (consecutive-restart
        # cap + backoff reset only here, never mid-episode).
        state.stuck_2fa_count = 0
        # One-shot pool reconnect: if a prior watchdog-initiated gateway
        # restart is now resolved (auth back to authenticated), the FastAPI
        # ib_pool may still be stuck disconnected. Bounce radon-api ONCE to
        # un-stick it, then clear the flag so this never loops.
        if state.pending_pool_reconnect and health.auth_state == "authenticated":
            state.authenticated_recovery_count += 1
            if state.authenticated_recovery_count < RECOVERY_CONFIRMATION_CYCLES:
                state.last_outcome = (
                    "pool_reconnect_confirmation:"
                    f"{state.authenticated_recovery_count}/"
                    f"{RECOVERY_CONFIRMATION_CYCLES}"
                )
                save_state(state_path, state)
                record_service_health(
                    "error",
                    error_message=(
                        "gateway recovery has one authenticated sample; "
                        "waiting for consecutive confirmation before API reconnect"
                    ),
                )
                return state
            state.api_hang_restart_count = 0
            try:
                ib_2fa_lock.release_2fa_push_lock(
                    expected_holder=WATCHDOG_LOCK_HOLDER,
                    guard_timeout_secs=LOCK_OP_TIMEOUT_SECS,
                )
            except ib_2fa_lock.GuardLockTimeout as exc:
                LOG.warning("watchdog lease release deferred: %s", exc)
            return _reconnect_pool_after_recovery(
                state=state,
                state_path=state_path,
                api_unit=api_unit,
                dry_run=dry_run,
                health=health,
            )

        if health.auth_state == "authenticated":
            state.api_hang_restart_count = 0
            state.authenticated_recovery_count = 0
            try:
                ib_2fa_lock.release_2fa_push_lock(
                    expected_holder=WATCHDOG_LOCK_HOLDER,
                    guard_timeout_secs=LOCK_OP_TIMEOUT_SECS,
                )
            except ib_2fa_lock.GuardLockTimeout as exc:
                LOG.warning("watchdog lease release deferred: %s", exc)

        state.last_outcome = f"healthy:{health.service_state}/{health.auth_state}"
        save_state(state_path, state)
        record_service_health("ok")
        return state

    # API hang detected — advance the ladder (quiet-window aware).
    return _advance_api_hang(
        state=state,
        state_path=state_path,
        restart_unit=restart_unit,
        threshold=threshold,
        dry_run=dry_run,
        clock=clock,
        quiet=quiet,
        evidence=(
            f"port={health.port_listening} upstream_dead={health.upstream_dead} "
            f"auth={health.auth_state}"
        ),
    )


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
        "--api-unit",
        default=DEFAULT_API_UNIT,
        help="radon-api unit to bounce once after gateway recovery (ib_pool reconnect)",
    )
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
            api_unit=args.api_unit,
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
