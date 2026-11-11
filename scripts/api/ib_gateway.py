"""IB Gateway health check and lifecycle surface for FastAPI.

The Gateway always runs on a remote host (the broker, reached over
Tailscale). Its lifecycle is owned by ``radon-ib-gateway-control`` on that
host; this module only probes it. ``ensure_ib_gateway`` reports
reachability and ``restart_ib_gateway`` refuses, so nothing in this process
can cycle a Gateway or fire an IBKR Mobile 2FA push.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import struct
import sys
import time
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

# scripts/utils lives under <repo>/scripts/utils; this module lives at
# <repo>/scripts/api. Add the scripts dir to sys.path so the shared
# 2FA push-lock module imports cleanly under any pytest rootdir.
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from utils import ib_2fa_lock  # noqa: E402
from api import db_http  # noqa: E402
from db.service_health_sql import (  # noqa: E402
    SERVICE_HEALTH_UPSERT_SQL,
    service_health_upsert_args,
)

logger = logging.getLogger("radon.ib_gateway")

# A startup holder older processes took on the shared 2FA push lock. The
# authentication edge in ``handle_auth_state_transition`` still clears it so a
# lease left behind by one of them never outlives the login it was guarding.
IB_GATEWAY_ENSURE_LOCK_HOLDER = "scripts.api.ib_gateway.ensure_ib_gateway"


IB_HOST = os.environ.get("IB_GATEWAY_HOST", "127.0.0.1")
IB_PORT = int(os.environ.get("IB_GATEWAY_PORT", "4001"))

# ``/health/lite`` has a 0.5s total budget. Keep this raw protocol probe short
# enough to fail closed without consuming that endpoint's worker budget.
CLOUD_PROTOCOL_PROBE_TIMEOUT_SECS = 0.25

# The version exchange is side-effect-free: it proves the IB API dispatcher is
# alive without creating a client session, consuming a client ID, or prompting
# for 2FA. A TCP connection alone is insufficient because Docker/socat can
# accept the host port while the Gateway's inner Java listener is absent.
_IB_API_SIGNATURE = b"API\x00"
_IB_API_VERSION_RANGE = b"v100..187"

# The lease guard is held across _write_lock_file's two fsyncs, so an
# unbounded flock hangs the FastAPI handler that reads it — with no timeout and
# no log line. R-211 converted the two READ paths here; the WRITE paths (acquire
# and release), which are the ones that actually hold the exclusive guard across
# both fsyncs, were left unbounded until T-200. Every ib_2fa_lock call in this
# module now goes through a bounded wrapper below.
LOCK_OP_TIMEOUT_SECS = 5.0


def _check_2fa_push_lock_bounded(now: float):
    """Bounded ``check_2fa_push_lock``. On timeout report a held lease so the
    caller DEFERS rather than firing a second push blind."""
    try:
        return ib_2fa_lock.check_2fa_push_lock(
            now=now, guard_timeout_secs=LOCK_OP_TIMEOUT_SECS
        )
    except ib_2fa_lock.GuardLockTimeout as exc:
        logger.warning("2FA lock check abandoned (%s); treating as held", exc)
        return ib_2fa_lock.PushLock(
            holder="guard-timeout",
            acquired_at=now,
            expires_at=now + ib_2fa_lock.DEFAULT_LOCK_TTL_SECS,
            reason=f"lease guard busy: {exc}",
        )


def _release_2fa_push_lock_bounded(*, expected_holder: Optional[str] = None):
    """Bounded ``release_2fa_push_lock``. A release that cannot take the guard
    is logged and skipped — the lease still auto-expires at its TTL, which is
    strictly better than hanging the caller inside a FastAPI handler."""
    try:
        return ib_2fa_lock.release_2fa_push_lock(
            expected_holder=expected_holder,
            guard_timeout_secs=LOCK_OP_TIMEOUT_SECS,
        )
    except ib_2fa_lock.GuardLockTimeout as exc:
        logger.warning(
            "2FA lock release abandoned (%s); lease will expire at its TTL", exc
        )
        return None


class AuthState(str, Enum):
    """The only auth_state values /health and /health/lite produce.

    Payloads carry ``.value`` (plain strings). The health daemon maps the same
    literals (``health_service.probes.AUTH_STATE_COARSE``); a parity test pins
    the two.
    """

    UNREACHABLE = "unreachable"      # port closed or upstream API dead
    AUTHENTICATED = "authenticated"  # a pool role holds managed accounts
    AWAITING_2FA = "awaiting_2fa"    # port up, no accounts (login pending)
    UNKNOWN = "unknown"              # no pool status to judge from
    REMOTE = "remote"                # cloud TCP-only answer, no pool here


# Auth-state transition tracking — drives auto-reconnect when IBKR 2FA resolves
# (awaiting_2fa → authenticated) while the FastAPI pool clients are still stuck
# disconnected. Documented in feedback_ib_pool_stuck_after_2fa.md as the
# follow-up to feedback_ib_gateway_2fa_verification.md. Without this hook the
# manual recovery is `systemctl restart radon-api.service`.
_auth_transition_state: Dict = {
    "previous_auth_state": None,   # last observed auth_state ("authenticated", "awaiting_2fa", ...)
    "last_reconnect_at": 0.0,      # epoch seconds; last time auto-reconnect fired
}

# Default ceiling for the pool reconnect call from inside the auth-transition
# handler. A wedge in pool reconnect must never block the probe loop.
RECONNECT_TIMEOUT_SECS = 30.0

# Default ceiling for the service_health heal step. A wedge in libsql (e.g.
# WAL contention with another writer) must not block the auth-transition
# handler — surfacing a stale banner is preferable to stalling /health.
HEAL_TIMEOUT_SECS = 10.0

class IBFailure(str, Enum):
    """What a failure text says about IB Gateway reachability."""

    NONE = "none"
    # Raw socket / API-handshake failure text from a subprocess or writer.
    CONNECTION = "connection"
    # This process already decided the Gateway is down (recovery verdicts).
    GATEWAY_DOWN = "gateway_down"


# The one list of raw "IB unreachable" substrings, matched case-insensitively.
# Used by the runtime recovery wrapper (server.py), the orders-sync heartbeat
# and the service_health heal below.
IB_CONNECTION_ERROR_PATTERNS: tuple[str, ...] = (
    "connect call failed",
    "econnrefused",
    "connection refused",
    "timeouterror",
    "api connection failed",
    "failed to connect to ib",
    "ibconnectionerror",
    "make sure api port",
    "connectivity between ibkr and",
    "request timed out",
    "127.0.0.1:4001",
)

# Gateway-down verdicts _run_ib_script_with_recovery returns. Their text is
# owned here so the classifier and the producers cannot drift.
GATEWAY_NOT_ACCEPTING_ERROR = (
    "IB Gateway is not accepting connections. Check IBKR Mobile for 2FA approval."
)
GATEWAY_COOLDOWN_SKIP_ERROR = "IB Gateway connection recently failed. Retrying shortly."
GATEWAY_NOT_RESPONDING_CLOUD_ERROR = (
    "IB Gateway is not responding (cloud mode). Check remote host and Tailscale."
)
# "down and restart failed" is no longer produced; it stays so a service_health
# row latched by an older process still classifies as gateway-down.
_GATEWAY_DOWN_VERDICT_MARKERS: tuple[str, ...] = (
    "ib gateway is not responding",
    "ib gateway is not accepting connections",
    "ib gateway is down and restart failed",
    "ib gateway connection recently failed",
)


def classify_ib_failure(text: object) -> IBFailure:
    """Classify failure text. A verdict this process wrote wins over raw text."""
    haystack = str(text or "").lower()
    if not haystack:
        return IBFailure.NONE
    if any(marker in haystack for marker in _GATEWAY_DOWN_VERDICT_MARKERS):
        return IBFailure.GATEWAY_DOWN
    if any(pattern in haystack for pattern in IB_CONNECTION_ERROR_PATTERNS):
        return IBFailure.CONNECTION
    return IBFailure.NONE


def restart_backoff_state() -> Dict:
    """The shared 2FA push lock for /health and operator visibility.

    No per-process restart ladder exists: this process never cycles the
    Gateway. ``applicable`` stays False so readers do not mistake the absent
    ladder for "no retry pending".

    BLOCKING. The ``push_lock`` read reaches ``_is_orphaned``, which runs
    ORPHAN_CONFIRM_PROBES socket connects with sleeps between them whenever a
    lease is held past its grace and the port is down — the incident state, and
    ~1.85s per call. Async callers must reach it via ``asyncio.to_thread``.
    T-201.
    """
    now = time.time()
    lock = _check_2fa_push_lock_bounded(now)
    push_lock = None
    if lock is not None:
        push_lock = {
            "holder": lock.holder,
            "acquired_at": lock.acquired_at,
            "expires_at": lock.expires_at,
            "remaining_secs": max(0, int(lock.expires_at - now)),
            "reason": lock.reason,
        }
    return {
        "push_lock": push_lock,
        "applicable": False,
        "gateway_mode": "cloud",
    }


# Synthetic hold while the broker reports a pending container transition: a
# push may be in flight that no lease describes yet. Sized to the helper's
# settle budget so it clears on the next poll once the transition converges.
BROKER_TRANSITION_HOLD_SECS = 60

# Synthetic hold while the broker status probe fails: its lease is unknown, and
# treating unknown as free let the operator fire Force 2FA into a held lease
# (409). Short so the next successful poll clears it.
BROKER_UNREACHABLE_HOLD_SECS = 15


def _synthetic_push_lock(holder: str, hold_secs: int, reason: str) -> Dict:
    now = time.time()
    return {
        "holder": holder,
        "acquired_at": now,
        "expires_at": now + hold_secs,
        "remaining_secs": hold_secs,
        "reason": reason,
    }


async def _overlay_broker_push_lock(backoff: Dict) -> None:
    """Replace ``push_lock`` with the broker's lease on the app role."""
    from api import services  # lazy: services imports nothing from here

    if services.host_role() != "app" or not services.is_remote_gateway_configured():
        return
    status, payload = await services._remote_status()
    if status == -1 or not isinstance(payload, dict):
        backoff["source"] = "broker-unreachable"
        backoff["push_lock"] = _synthetic_push_lock(
            "broker:unreachable", BROKER_UNREACHABLE_HOLD_SECS, "broker lease state unknown"
        )
        return
    backoff["source"] = "broker"
    lease = payload.get("lease")
    if isinstance(lease, dict) and lease.get("holder"):
        try:
            remaining = max(0, int(lease.get("remaining_secs") or 0))
        except (TypeError, ValueError):
            remaining = 0
        backoff["push_lock"] = {
            "holder": str(lease.get("holder")),
            "acquired_at": lease.get("acquired_at"),
            "expires_at": lease.get("expires_at"),
            "remaining_secs": remaining,
            "reason": str(lease.get("reason") or ""),
        }
        return
    transition = payload.get("transition") or (
        "pending" if str(payload.get("detail") or payload.get("state") or "").strip() == "transition-pending" else None
    )
    if transition:
        backoff["push_lock"] = _synthetic_push_lock(
            "broker:transition-pending", BROKER_TRANSITION_HOLD_SECS, "broker Gateway transition pending"
        )
        return
    backoff["push_lock"] = None


def reset_restart_backoff() -> Dict:
    """Release the shared 2FA push lock. Operator path: 'I just approved 2FA'.

    The operator just told us the in-flight push is approved, so the lease
    should not delay the next legitimate restart from the Gateway's owner.
    """
    released = _release_2fa_push_lock_bounded()
    return {
        "reset": True,
        "lock_released": released.to_dict() if released else None,
    }


def _port_listening(host: str = IB_HOST, port: int = IB_PORT, timeout: float = 2.0) -> bool:
    """Check if IB Gateway port is accepting connections."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (ConnectionRefusedError, OSError, socket.timeout):
        return False


def _cloud_protocol_responding(
    host: str = IB_HOST,
    port: int = IB_PORT,
    timeout: float = CLOUD_PROTOCOL_PROBE_TIMEOUT_SECS,
) -> bool:
    """Return whether the cloud Gateway answers a bounded IB version exchange.

    This deliberately performs no authentication or API request. It is a
    protocol-level liveness check for the cloud-mode proxy topology, where a
    host TCP listener can outlive the inner Gateway listener.
    """
    sock = None
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.settimeout(timeout)
        sock.sendall(
            _IB_API_SIGNATURE
            + struct.pack(">I", len(_IB_API_VERSION_RANGE))
            + _IB_API_VERSION_RANGE
        )
        return bool(sock.recv(4096))
    except (ConnectionRefusedError, ConnectionResetError, OSError, socket.timeout):
        return False
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


# Cloud mode — remote Gateway, no local lifecycle management


async def _check_cloud() -> Dict:
    """Check remote Gateway health via TCP plus a bounded API handshake."""
    port_ok = await asyncio.to_thread(_port_listening)
    protocol_ok = (
        await asyncio.to_thread(_cloud_protocol_responding) if port_ok else False
    )
    return {
        "port_listening": port_ok,
        "upstream_dead": port_ok and not protocol_ok,
        "service_state": (
            "reachable" if protocol_ok else "unhealthy" if port_ok else "unreachable"
        ),
        "host": IB_HOST,
        "port": IB_PORT,
        "gateway_mode": "cloud",
    }


async def _ensure_cloud() -> Dict:
    """Verify remote Gateway is reachable. No restart capability."""
    port_ok = await asyncio.to_thread(_port_listening)
    if port_ok:
        return {"status": "already_running", "port_listening": True, "gateway_mode": "cloud"}
    logger.warning(
        "Cloud IB Gateway not reachable at %s:%d — check remote host",
        IB_HOST, IB_PORT,
    )
    return {
        "status": "unreachable",
        "port_listening": False,
        "gateway_mode": "cloud",
        "error": f"Cloud IB Gateway at {IB_HOST}:{IB_PORT} is not reachable. Check remote host and Tailscale.",
    }


async def _probe_authenticated(timeout: float = 8.0) -> tuple[bool, List[str]]:
    """Open a throwaway IB connection and probe `managedAccounts()`.

    Returns (authenticated, accounts). Authenticated = accounts list non-empty.
    Used to verify a restart actually completed login — not just that the API
    socket is listening. Empty accounts on a listening port means TWS is sitting
    at the IBKR Mobile 2FA prompt.
    """
    try:
        from ib_insync import IB
    except ImportError:
        return (False, [])

    def _do_probe() -> tuple[bool, List[str]]:
        # R-142: ib_insync's connect() reaches util.getLoop(), which raises
        # RuntimeError in a thread with no loop installed — and asyncio.to_thread
        # gives it exactly that. The bare except below would report the
        # plumbing error as "not authenticated", so a live gateway looks like a
        # 2FA wait to three consumers. ib_pool._connect_in_thread already does
        # this; the probe has to as well.
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())

        ib = IB()
        try:
            # CLI range (90-99) per CLAUDE.md client-id allocation — clientId 98
            # to avoid colliding with pool (3-5), relay (10-19), subprocesses,
            # scanners, daemons. Brief connect + immediate disconnect.
            ib.connect(IB_HOST, IB_PORT, clientId=98, timeout=timeout)
            accounts = list(ib.managedAccounts() or [])
            return (bool(accounts), accounts)
        except Exception as exc:
            logger.warning("IB auth probe failed: %s: %s", type(exc).__name__, exc)
            return (False, [])
        finally:
            try:
                ib.disconnect()
            except Exception:
                pass

    return await asyncio.to_thread(_do_probe)


def _pool_has_disconnected_slot(pool) -> bool:
    """Return True if any pool role is `connected=False` in its current status."""
    if pool is None:
        return False
    try:
        status = pool.status() or {}
    except Exception:
        return False
    return any(not role_info.get("connected", False) for role_info in status.values())


def _pool_disconnected_roles(pool) -> list:
    """Return the roles whose current status reports connected=False."""
    if pool is None:
        return []
    try:
        status = pool.status() or {}
    except Exception:
        return []
    return [
        role for role, role_info in status.items()
        if not role_info.get("connected", False)
    ]


def _format_pool_state(pool) -> str:
    """Compact one-line pool state for logs, e.g. "sync=False orders=True data=False"."""
    if pool is None:
        return "<no pool>"
    try:
        status = pool.status() or {}
    except Exception:
        return "<status unavailable>"
    return " ".join(
        f"{role}={role_info.get('connected', False)}" for role, role_info in status.items()
    )


def _error_message_looks_like_ib_outage(last_error_blob: Optional[str]) -> bool:
    """Return True iff ``last_error`` plausibly indicates an IB outage.

    ``last_error`` is the JSON-encoded blob written by ``record_service_health``
    — typically ``{"message": "..."}`` or ``{"detail": "..."}``. We pull the
    free-text message out (or fall back to the raw blob) and check for a
    substring match against the production patterns IB-dependent writers
    emit when the Gateway is unreachable.

    Matching is case-insensitive. A row whose ``last_error`` does NOT look
    like an IB outage (schema bug, payload validation failure, ...) is left
    untouched — auto-healing those would mask real problems.
    """
    if not last_error_blob:
        return False
    try:
        parsed = json.loads(last_error_blob)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    message: str
    if isinstance(parsed, dict):
        message = str(parsed.get("message") or parsed.get("detail") or last_error_blob)
    else:
        message = last_error_blob
    return classify_ib_failure(message) is not IBFailure.NONE


def _query_ib_dependent_error_services() -> List[str]:
    """Return names of IB-dependent services whose row in ``service_health``
    is currently ``state=error`` AND whose ``last_error`` looks like an IB
    outage. Anything else is left alone.

    Reads over the bounded hrana HTTP pipeline (``api.db_http``) — sync
    libsql is banned in this process because its GIL-holding native calls
    starve the event loop even from a worker thread. Any read failure
    degrades to an empty list — auto-heal is a nice-to-have, not a
    critical path.
    """
    try:
        # ``_SCRIPTS_DIR`` is already on sys.path (added at module import) so
        # the flat ``watchdog.services`` shape works under both
        # ``python -m scripts.api.server`` and pytest.
        from watchdog.services import requires_ib  # type: ignore[import-not-found]
    except ImportError as exc:
        logger.debug("auth heal: dependencies unavailable (%s); skipping query", exc)
        return []

    try:
        rows = db_http.hrana_execute(
            "SELECT service, last_error FROM service_health WHERE state = ?",
            ("error",),
        )
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("auth heal: failed to query service_health: %s", exc)
        return []

    healable: List[str] = []
    for row in rows:
        service = row[0]
        last_error = row[1]
        if not requires_ib(service):
            continue
        if not _error_message_looks_like_ib_outage(last_error):
            continue
        healable.append(service)
    return healable


async def _clear_service_health_error(service: str) -> None:
    """Write ``state=ok`` with ``last_error=NULL`` for one service.

    Executes the canonical upsert from ``db.service_health_sql`` — the same
    statement ``record_service_health`` uses — over the bounded hrana HTTP
    pipeline, so serialization and timestamping stay defined in one place
    while this process never touches sync libsql (whose GIL-holding native
    ``commit()`` made the old ``asyncio.to_thread`` wrapping a non-bound).
    The hrana socket timeout is the real bound; ``to_thread`` keeps the
    GIL-releasing network wait off the loop.
    """
    await asyncio.to_thread(
        db_http.hrana_execute,
        SERVICE_HEALTH_UPSERT_SQL,
        service_health_upsert_args(service, "ok", error=None),
    )


async def _heal_ib_dependent_service_health(timeout: float) -> List[str]:
    """Heal any stale IB-outage ``error`` rows. Returns the list of healed names.

    Bounded by ``timeout``. If the DB call hangs (replica wedge, contention),
    we log a warning and return an empty list — the auth-transition handler
    must not stall because of the banner-clear step.
    """
    async def _inner() -> List[str]:
        services = await asyncio.to_thread(_query_ib_dependent_error_services)
        if not services:
            return []
        for service in services:
            await _clear_service_health_error(service)
        return services

    try:
        healed = await asyncio.wait_for(_inner(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(
            "auth heal: service_health clear timed out after %.1fs; banner may stay stale",
            timeout,
        )
        return []
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("auth heal: clear step raised %s: %s", type(exc).__name__, exc)
        return []

    if healed:
        logger.info(
            "auth recovered: cleared %d stale error rows for IB-dependent services: %s",
            len(healed), healed,
        )
    return healed


# Leaving either of these for authenticated is a login that may have stranded
# the pool and the service_health rows. unreachable -> authenticated (a
# Gateway that came back already logged in) needs the same heal + release as
# a resolved 2FA wait.
_RECOVERY_EDGE_SOURCES = frozenset({AuthState.AWAITING_2FA.value, AuthState.UNREACHABLE.value})


def _is_recovery_edge(previous, new_auth_state) -> bool:
    return previous in _RECOVERY_EDGE_SOURCES and new_auth_state == AuthState.AUTHENTICATED.value


def last_observed_auth_state():
    """The most recent auth_state any health check derived, or None.

    REL-177 (R-488): on-demand routes (streaks) consult this before touching
    the pool so an awaiting_2fa gateway is skipped in microseconds instead of
    burning two bounded 15s calls per lookup.
    """
    return _auth_transition_state.get("previous_auth_state")


async def handle_auth_state_transition(
    new_auth_state: str,
    pool,
    reconnect_timeout: float = RECONNECT_TIMEOUT_SECS,
    heal_timeout: float = HEAL_TIMEOUT_SECS,
) -> bool:
    """Detect awaiting_2fa → authenticated edges and recover the data plane.

    The IB connection pool (sync/orders/data) can stay `connected=False` after
    IBKR 2FA approval even though the Gateway is fully authenticated to a fresh
    probe. The manual recovery is `systemctl restart radon-api.service`. This
    handler automates the same fix by calling `pool.reconnect_all()` whenever
    the auth_state edge fires AND at least one pool slot is disconnected.

    On the same edge we ALSO clear stale ``service_health.state=error`` rows
    for IB-dependent services whose ``last_error`` looks like an IB outage.
    Without this step the UI banner keeps showing IB-related errors long
    after IB is back — the writers don't naturally heartbeat until their
    next market-hours cycle. The heal pass is independent of the pool
    reconnect: rows can be stale even when the pool is fine (e.g. when a
    handler raised on read and never wrote ``state=ok`` afterwards).

    Behavior:
      • First observation (previous=None): record state, take no action — we
        only act on real transitions, not first-probe assumptions.
      • previous == new: no transition, do nothing. Mid-session disconnects
        are handled by the per-role auto-reconnect in `_PoolContext`.
      • previous in {"awaiting_2fa", "unreachable"} AND new == "authenticated":
          1. Heal stale IB-outage error rows in ``service_health`` (bounded
             by ``heal_timeout``).
          2. If any pool slot is disconnected, schedule
             ``pool.reconnect_all()`` bounded by ``reconnect_timeout``.
        Returns True iff the pool reconnect was attempted. (The heal step's
        outcome is logged but doesn't change the return value — it's
        orthogonal to "did we kick the pool".)
      • Any other transition: record state, take no action.

    Both follow-up steps are bounded by ``asyncio.wait_for`` so a wedge in
    the pool's connect path OR in libsql cannot block the calling probe loop.
    """
    previous = _auth_transition_state["previous_auth_state"]
    # Always advance the tracker — even on the no-action paths — so the NEXT
    # observation can correctly compute the transition.
    _auth_transition_state["previous_auth_state"] = new_auth_state

    if previous is None or previous == new_auth_state:
        return False

    if not _is_recovery_edge(previous, new_auth_state):
        return False

    # Only clear the startup holder we own, and only on a real authentication
    # edge. An unconditional release could erase a different actor's newly
    # acquired lease while that actor is already starting another cycle.
    _release_2fa_push_lock_bounded(
        expected_holder=IB_GATEWAY_ENSURE_LOCK_HOLDER
    )

    # Step 1: clear stale IB-outage error rows so the UI banner clears the
    # moment IB recovers. Idempotent — if there's nothing to heal it's a
    # silent no-op.
    await _heal_ib_dependent_service_health(timeout=heal_timeout)

    if not _pool_has_disconnected_slot(pool):
        logger.info(
            "auth transition: %s -> %s; pool fully connected (%s); no reconnect needed",
            previous, new_auth_state, _format_pool_state(pool),
        )
        return False

    logger.info(
        "auth transition: %s -> %s; pool state: %s; triggering reconnect",
        previous, new_auth_state, _format_pool_state(pool),
    )
    _auth_transition_state["last_reconnect_at"] = time.time()

    try:
        await asyncio.wait_for(pool.reconnect_all(), timeout=reconnect_timeout)
        logger.info(
            "auth transition: pool reconnect complete; new state: %s",
            _format_pool_state(pool),
        )
    except asyncio.TimeoutError:
        logger.warning(
            "auth transition: pool reconnect timed out after %.1fs; pool state: %s",
            reconnect_timeout, _format_pool_state(pool),
        )
    except Exception as e:
        logger.warning(
            "auth transition: pool reconnect raised %s: %s; pool state: %s",
            type(e).__name__, e, _format_pool_state(pool),
        )

    return True


def _pool_has_connected_accounted_slot(pool, roles=None) -> bool:
    """Return True iff a pool role reports connected==True AND non-empty
    managed_accounts — the only state that proves this process can talk to a
    logged-in Gateway. This is the success predicate for ``recover_stuck_pool``:
    a TCP-connected-but-account-less slot is NOT a recovered pool.

    With ``roles`` given, EVERY named role must be connected+accounted. The
    ANY-role form let healthy orders/sync slots mask a still-wedged data role,
    so recovery reported success and the escalation ladder never counted
    (R-060).
    """
    if pool is None:
        return False
    try:
        status = pool.status() or {}
    except Exception:
        return False
    if roles is None:
        return any(
            role_info.get("connected") and role_info.get("managed_accounts")
            for role_info in status.values()
        )
    scoped = [status.get(role) or {} for role in roles]
    return bool(scoped) and all(
        role_info.get("connected") and role_info.get("managed_accounts")
        for role_info in scoped
    )


async def recover_stuck_pool(
    pool,
    reconnect_timeout: float = RECONNECT_TIMEOUT_SECS,
) -> bool:
    """Level-triggered, pool-INDEPENDENT recovery for the stuck-pool-after-2FA case.

    The edge-triggered ``handle_auth_state_transition`` cannot escape the
    documented deadlock: ``auth_state`` is derived FROM the pool, so while the
    pool is stuck (all roles connected=False / account-less) every probe derives
    "awaiting_2fa", the transition edge never fires, and an external 2FA approval
    is invisible forever. This function breaks that loop by probing the Gateway
    directly (throwaway clientId 98), NOT by reading the wedged pool's own state.

    Decision ladder:
      1. No pool, or no disconnected slot → no-op, return False (healthy: nothing
         to do, and crucially we do NOT probe).
      2. Disconnected slot present → probe the Gateway with ``_probe_authenticated``.
         If NOT authenticated (no managed accounts) → genuine 2FA wait; return
         False and do NOTHING. We never touch the Gateway, never acquire the 2FA
         push lock, never restart — those paths live in ``restart_ib_gateway``.
      3. Probe authenticated → ``pool.reconnect_roles(stuck_roles)`` (bounded),
         ROLE-SCOPED so live orders/sync sessions are never torn down for a
         wedged data slot (R-060). RE-READ the pool and return True iff EVERY
         stuck role is now connected WITH managed_accounts. The verified
         re-read is what the transition handler lacks — it swallows the
         reconnect outcome and reports success on a still-dead pool.

    Bounded by ``asyncio.wait_for`` so a wedge in the connect path can never
    block the calling heartbeat loop. Errors propagate to the caller, which runs
    this inside the heartbeat try/except.
    """
    stuck_roles = _pool_disconnected_roles(pool)
    if not stuck_roles:
        return False

    authenticated, accounts = await _probe_authenticated()
    if not authenticated:
        logger.debug(
            "pool recovery: disconnected slot present but Gateway probe is not "
            "authenticated (genuine 2FA wait); pool state: %s",
            _format_pool_state(pool),
        )
        return False

    logger.info(
        "pool recovery: Gateway authenticated (accounts=%s) but pool roles %s "
        "stuck (%s); triggering role-scoped reconnect",
        accounts, stuck_roles, _format_pool_state(pool),
    )
    await asyncio.wait_for(pool.reconnect_roles(stuck_roles), timeout=reconnect_timeout)

    recovered = _pool_has_connected_accounted_slot(pool, roles=stuck_roles)
    logger.info(
        "pool recovery: reconnect complete; recovered=%s; pool state: %s",
        recovered, _format_pool_state(pool),
    )
    return recovered


def _remote_cert_summary() -> Optional[dict]:
    """Days left on the app-side mTLS client cert (REL-178 / R-496)."""
    cert_path = os.environ.get("RADON_IB_REMOTE_CLIENT_CERT", "")
    if not cert_path:
        return None
    try:
        from utils.cert_expiry import cert_days_left
    except ImportError:  # pragma: no cover
        from scripts.utils.cert_expiry import cert_days_left
    days = cert_days_left(cert_path)
    if days is None:
        return None
    return {"cert_days_left": round(days, 1)}


def _derive_auth_state(check_result: Dict, pool_status: Optional[dict]) -> str:
    """Derive auth_state from a check result + optional pool status.

    States:
      unreachable   — port not listening
      authenticated — at least one connected pool client returns managed_accounts
      awaiting_2fa  — port listening but no accounts visible (TWS at 2FA prompt
                      or pool fully disconnected from a Gateway that's pre-login)
      unknown       — pool status unavailable; cannot distinguish auth/no-auth
    """
    if not check_result.get("port_listening"):
        return AuthState.UNREACHABLE.value
    if not pool_status:
        return AuthState.UNKNOWN.value
    for role_info in pool_status.values():
        if role_info.get("connected") and role_info.get("managed_accounts"):
            return AuthState.AUTHENTICATED.value
    return AuthState.AWAITING_2FA.value


async def _operator_hold() -> Optional[Dict]:
    """The IBKR operator hold for /health, or None when unknown. Never raises:
    a status surface must not fail on its own decoration."""
    from api import services  # lazy: services imports nothing from here

    try:
        return await services.operator_hold_state()
    except Exception as exc:  # noqa: BLE001
        logger.warning("operator hold state unavailable: %s", exc)
        return None


async def check_ib_gateway(
    pool_status: Optional[dict] = None,
    pool=None,
) -> Dict:
    """Check IB Gateway health. Returns status dict for /health endpoint.

    Pass `pool_status` (typically `IBPool.status()`) so the response can
    distinguish "logged in" from "port listening but awaiting 2FA". Without
    it, auth_state is "remote".

    Pass `pool` (the IBPool instance itself) to enable autonomous recovery on
    the awaiting_2fa → authenticated transition. The /health endpoint already
    has both — passing them through keeps recovery on the same cadence as the
    auth-state probe and avoids a second scheduler.
    """
    result = await _check_cloud()
    # The admin panel uses this state to prevent duplicate IBKR pushes.
    # It is local control-plane state and must be present regardless of
    # where the Gateway itself runs.
    result["restart_backoff"] = await asyncio.to_thread(restart_backoff_state)
    # REL-172 (R-475): on the app host the lease lives on the BROKER; the
    # local lock file is never held by its helper. Proxy the broker's
    # lease / transition so Force 2FA and Start disarm during a push.
    await _overlay_broker_push_lock(result["restart_backoff"])
    result["operator_hold"] = await _operator_hold()
    if not result.get("port_listening") or result.get("upstream_dead"):
        result["auth_state"] = AuthState.UNREACHABLE.value
    elif pool_status:
        # Pool gives us visibility into managed_accounts on the remote
        # Gateway via this process's own client connections — that's the
        # authoritative auth signal.
        result["auth_state"] = _derive_auth_state(result, pool_status)
    else:
        # No pool to probe (cold start, or this process doesn't run a
        # pool). The TCP probe alone can't distinguish authenticated from
        # awaiting_2fa, so report "remote" and defer to the host that
        # actually owns the Gateway.
        result["auth_state"] = AuthState.REMOTE.value
        remote_summary = _remote_cert_summary()
        if remote_summary is not None:
            result["remote"] = remote_summary
    if pool is not None and result["auth_state"] != AuthState.REMOTE.value:
        await handle_auth_state_transition(result["auth_state"], pool)
    return result


async def ensure_ib_gateway() -> Dict:
    """Report whether the remote Gateway is reachable. Called at FastAPI startup."""
    return await _ensure_cloud()


async def restart_ib_gateway(pool=None) -> Dict:
    """Refuse: this process cannot cycle the remote Gateway.

    Returns before touching the 2FA push lease, so a killed request cannot
    block the broker's helper without ever sending a push. ``pool`` is
    accepted for the caller's signature and unused.
    """
    return {
        "restarted": False,
        "gateway_mode": "cloud",
        "error": f"Cannot restart remote Gateway at {IB_HOST}:{IB_PORT}. Manage it on the remote host.",
    }
