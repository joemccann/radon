"""IB Gateway health check and lifecycle management.

Supports three modes controlled by IB_GATEWAY_MODE env var:
  - "cloud"   — remote Gateway (e.g. Hetzner via Tailscale). No local restart.
  - "docker"  — manages Gateway via Docker Compose
  - "launchd" — manages Gateway via IBC launchd service (legacy)

Default: "docker".
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
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

# Identifier used when this module takes the shared 2FA push lock. Other
# restart paths (ib_watchdog.py, ib_orderly_restart.py, …) use distinct
# holder strings so a glance at /var/lib/radon/ib-2fa-push-lock.json tells
# the operator which component is mid-2FA-cycle.
IB_GATEWAY_LOCK_HOLDER = "scripts.api.ib_gateway.restart_ib_gateway"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

IB_HOST = os.environ.get("IB_GATEWAY_HOST", "127.0.0.1")
IB_PORT = int(os.environ.get("IB_GATEWAY_PORT", "4001"))
GATEWAY_MODE = os.environ.get("IB_GATEWAY_MODE", "docker")  # "docker", "cloud", or "launchd"

# LaunchD paths
IBC_HOME = Path.home() / "ibc" / "bin"
STATUS_SCRIPT = IBC_HOME / "status-secure-ibc-service.sh"
START_SCRIPT = IBC_HOME / "start-secure-ibc-service.sh"
RESTART_SCRIPT = IBC_HOME / "restart-secure-ibc-service.sh"

# Docker paths. Defaults to the in-tree compose at <repo>/docker/ib-gateway/
# (matches the laptop dev layout). Override with IB_GATEWAY_COMPOSE_DIR when
# the compose project lives elsewhere — e.g. Hetzner runs the container from
# the radon-cloud repo via /home/radon/radon-cloud/docker-compose.yml, so a
# bare default would point FastAPI at the wrong project (and `docker compose
# ps` would silently report not_found while the container is actually up).
COMPOSE_DIR = Path(
    os.environ.get(
        "IB_GATEWAY_COMPOSE_DIR",
        str(Path(__file__).parent.parent.parent / "docker" / "ib-gateway"),
    )
)

# Timing
RESTART_WAIT_SECS = 45
PORT_POLL_INTERVAL = 3

# Prevent concurrent restart races
_restart_lock = asyncio.Lock()

# Restart backoff state — only reset when a probe confirms login (managedAccounts non-empty).
# Each restart sends a fresh IBKR Mobile 2FA push; tight retry loops spam the user
# AND can flag the account as suspicious, so we widen the gap between attempts
# until login completes.
BACKOFF_LADDER_SECS: List[int] = [60, 120, 300, 900, 1800, 3600]  # 1m,2m,5m,15m,30m,60m

_restart_state: Dict = {
    "attempt_count": 0,            # consecutive unconfirmed attempts
    "next_attempt_after": 0.0,     # epoch seconds; restart() refuses before this
    "last_attempt_at": 0.0,        # epoch seconds; last actual restart attempt
    "last_outcome": None,          # "authenticated" | "awaiting_2fa" | "unreachable" | None
    "last_accounts": [],           # most recent managedAccounts() probe result
}

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

# Substring patterns that classify a ``service_health.last_error`` as
# "caused by IB Gateway being unreachable". Matched case-insensitively
# against the JSON blob's ``message`` (or ``detail``) field.
#
# Sourced from the production patterns IB-dependent writers emit when
# the Gateway is unreachable — kept symmetric with the runtime classifier
# in ``scripts/api/server.py:_IB_CONN_REFUSED_PATTERNS`` so a future
# additional pattern only needs to be added in one place. We deliberately
# do NOT collapse the two lists into a shared constant: this module
# imports cleanly without the server, and a stray import from server.py
# would pull FastAPI into the IB Gateway helper's dependency surface.
_IB_OUTAGE_ERROR_PATTERNS: tuple[str, ...] = (
    "failed to connect to ib",
    "127.0.0.1:4001",
    "timeouterror",
    "connection refused",
    "econnrefused",
    "connect call failed",
    "api connection failed",
    "ibconnectionerror",
    "make sure api port",
    "request timed out",
)


def _next_backoff_delay(attempt_count: int) -> int:
    """Return delay in seconds for the Nth consecutive failed attempt (1-indexed)."""
    if attempt_count <= 0:
        return BACKOFF_LADDER_SECS[0]
    idx = min(attempt_count - 1, len(BACKOFF_LADDER_SECS) - 1)
    return BACKOFF_LADDER_SECS[idx]


def restart_backoff_state() -> Dict:
    """Snapshot of restart backoff state for /health and operator visibility.

    Includes ``push_lock`` so operators inspecting /health can tell at a
    glance whether a fresh restart would be blocked by an in-flight 2FA
    push from another holder.
    """
    now = time.time()
    lock = ib_2fa_lock.check_2fa_push_lock(now=now)
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
        "attempt_count": _restart_state["attempt_count"],
        "last_attempt_at": _restart_state["last_attempt_at"],
        "next_attempt_after": _restart_state["next_attempt_after"],
        "next_attempt_in_secs": max(0, int(_restart_state["next_attempt_after"] - now)),
        "last_outcome": _restart_state["last_outcome"],
        "push_lock": push_lock,
    }


def reset_restart_backoff() -> Dict:
    """Manually clear backoff. Operator path: 'I just approved 2FA, try again now'.

    Also releases the shared 2FA push lock so any other restart path
    (ib_watchdog, operator CLI) can proceed immediately. The two pieces
    of state move in lockstep: the operator just told us the in-flight
    push is approved, so neither the backoff window NOR the lock should
    delay the next legitimate restart attempt.
    """
    previous = {k: _restart_state[k] for k in ("attempt_count", "next_attempt_after", "last_outcome")}
    _restart_state["attempt_count"] = 0
    _restart_state["next_attempt_after"] = 0.0
    released = ib_2fa_lock.release_2fa_push_lock()
    return {
        "reset": True,
        "previous": previous,
        "lock_released": released.to_dict() if released else None,
    }

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _port_listening(host: str = IB_HOST, port: int = IB_PORT, timeout: float = 2.0) -> bool:
    """Check if IB Gateway port is accepting connections."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (ConnectionRefusedError, OSError, socket.timeout):
        return False


async def _poll_port(wait_secs: int = RESTART_WAIT_SECS) -> tuple:
    """Poll port until listening or timeout. Returns (port_ok, elapsed)."""
    elapsed = 0
    while elapsed < wait_secs:
        await asyncio.sleep(PORT_POLL_INTERVAL)
        elapsed += PORT_POLL_INTERVAL
        if await asyncio.to_thread(_port_listening):
            logger.info("IB Gateway accepting connections after %ds", elapsed)
            return True, elapsed
        logger.info("Waiting for IB Gateway... (%d/%ds)", elapsed, wait_secs)
    return False, elapsed


# ---------------------------------------------------------------------------
# LaunchD mode
# ---------------------------------------------------------------------------


def _has_close_wait(port: int = IB_PORT) -> bool:
    """Detect CLOSE_WAIT sockets on IB Gateway port.

    CLOSE_WAIT means the Gateway process is alive but the upstream IB
    session has dropped. Only relevant in launchd mode where we see
    the host-level TCP state directly.
    """
    try:
        out = subprocess.check_output(
            ["lsof", "-i", f":{port}", "-n", "-P"],
            timeout=5,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return "CLOSE_WAIT" in out
    except (subprocess.SubprocessError, OSError):
        return False


async def _run_shell(script: Path, timeout: float = 10.0) -> tuple:
    """Run a shell script, return (stdout, stderr, returncode)."""
    if not script.exists():
        return ("", f"Script not found: {script}", 1)

    proc = await asyncio.create_subprocess_exec(
        "bash", str(script),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return (
            stdout.decode("utf-8", errors="replace").strip(),
            stderr.decode("utf-8", errors="replace").strip(),
            proc.returncode,
        )
    except asyncio.TimeoutError:
        proc.kill()
        return ("", "Script timed out", -1)


async def _check_launchd() -> Dict:
    """Check Gateway health via launchd service state."""
    port_ok = await asyncio.to_thread(_port_listening)
    close_wait = await asyncio.to_thread(_has_close_wait) if port_ok else False

    service_state = "unknown"
    if STATUS_SCRIPT.exists():
        stdout, _, rc = await _run_shell(STATUS_SCRIPT)
        if rc == 0:
            for line in stdout.split("\n"):
                line = line.strip()
                if line.startswith("state ="):
                    service_state = line.split("=", 1)[1].strip()
                    break

    return {
        "port_listening": port_ok,
        "upstream_dead": close_wait,
        "service_state": service_state,
        "host": IB_HOST,
        "port": IB_PORT,
        "gateway_mode": "launchd",
    }


async def _ensure_launchd() -> Dict:
    """Ensure Gateway is running via launchd. Handles CLOSE_WAIT detection."""
    port_ok = await asyncio.to_thread(_port_listening)

    if port_ok:
        close_wait = await asyncio.to_thread(_has_close_wait)
        if close_wait:
            logger.warning(
                "IB Gateway on %s:%d has CLOSE_WAIT (upstream dead) — restarting",
                IB_HOST, IB_PORT,
            )
            return await _restart_launchd()
        return {"status": "already_running", "port_listening": True, "gateway_mode": "launchd"}

    logger.warning("IB Gateway not listening on %s:%d — attempting start", IB_HOST, IB_PORT)
    return await _restart_launchd()


async def _restart_launchd() -> Dict:
    """Restart Gateway via IBC launchd service scripts."""
    if not RESTART_SCRIPT.exists():
        return {
            "restarted": False,
            "error": f"IBC restart script not found at {RESTART_SCRIPT}",
            "port_listening": False,
            "gateway_mode": "launchd",
        }

    logger.info("Running IBC restart script...")
    stdout, stderr, rc = await _run_shell(RESTART_SCRIPT, timeout=60.0)

    if rc != 0:
        logger.warning("Restart script failed (rc=%d), trying start script...", rc)
        if START_SCRIPT.exists():
            stdout, stderr, rc = await _run_shell(START_SCRIPT, timeout=60.0)
        if rc != 0:
            return {
                "restarted": False,
                "error": f"Both restart and start scripts failed. stderr: {stderr[:200]}",
                "port_listening": False,
                "gateway_mode": "launchd",
            }

    logger.info("IBC script finished, waiting for Gateway (up to %ds)...", RESTART_WAIT_SECS)
    port_ok, elapsed = await _poll_port()

    if not port_ok:
        return {
            "restarted": True,
            "port_listening": False,
            "gateway_mode": "launchd",
            "error": (
                f"IBC service started but Gateway not accepting connections after {RESTART_WAIT_SECS}s. "
                "Check IBKR Mobile for 2FA approval."
            ),
        }

    return {
        "restarted": True,
        "port_listening": True,
        "wait_seconds": elapsed,
        "gateway_mode": "launchd",
    }


# ---------------------------------------------------------------------------
# Docker mode
# ---------------------------------------------------------------------------


async def _docker_compose(*args: str, timeout: float = 30.0) -> tuple:
    """Run docker compose command in the ib-gateway directory."""
    compose_file = COMPOSE_DIR / "docker-compose.yml"
    env_file = COMPOSE_DIR / ".env"

    if not compose_file.exists():
        return ("", f"Docker compose file not found at {compose_file}", 1)

    cmd = ["docker", "compose", "-f", str(compose_file)]
    if env_file.exists():
        cmd.extend(["--env-file", str(env_file)])
    cmd.extend(args)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return (
            stdout.decode("utf-8", errors="replace").strip(),
            stderr.decode("utf-8", errors="replace").strip(),
            proc.returncode,
        )
    except asyncio.TimeoutError:
        proc.kill()
        return ("", "Docker compose command timed out", -1)


async def _docker_container_state() -> tuple:
    """Get Docker container state and health.

    Returns (state, health) tuple, e.g. ("running", "healthy"), ("exited", "").
    """
    stdout, _, rc = await _docker_compose("ps", "--format", "json", timeout=10.0)
    if rc != 0 or not stdout:
        return "not_found", ""

    try:
        for line in stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if entry.get("Service") == "ib-gateway" or "ib-gateway" in entry.get("Name", ""):
                state = entry.get("State", "unknown").lower()
                health = entry.get("Health", "").lower()
                return state, health
    except (json.JSONDecodeError, KeyError):
        pass

    return "not_found", ""


async def _check_docker() -> Dict:
    """Check Gateway health via Docker container status."""
    port_ok = await asyncio.to_thread(_port_listening)
    container_state, container_health = await _docker_container_state()

    # Map container health status
    service_state = "unknown"
    if container_state == "running":
        if container_health == "healthy":
            service_state = "healthy"
        elif container_health == "unhealthy":
            service_state = "unhealthy"
        else:
            service_state = "starting" if not port_ok else "healthy"
    elif container_state == "restarting":
        service_state = "restarting"
    elif container_state in ("exited", "not_found"):
        service_state = "stopped"

    # In Docker mode, upstream_dead means the container is running but IB API
    # inside is not responsive (unhealthy healthcheck). Docker's port proxy
    # may still accept TCP connections even when the IB API is down.
    upstream_dead = container_state == "running" and container_health == "unhealthy"

    return {
        "port_listening": port_ok,
        "upstream_dead": upstream_dead,
        "service_state": service_state,
        "container_state": container_state,
        "container_health": container_health,
        "host": IB_HOST,
        "port": IB_PORT,
        "gateway_mode": "docker",
    }


async def _ensure_docker_container() -> Dict:
    """Ensure Docker container is running. Start if stopped, wait if restarting.

    In Docker mode, we do NOT attempt to restart the container — Docker's
    restart: unless-stopped policy handles that. We only start a stopped/missing
    container, or wait for a restarting one.
    """
    port_ok = await asyncio.to_thread(_port_listening)

    if port_ok:
        return {"status": "already_running", "port_listening": True, "gateway_mode": "docker"}

    container_state, container_health = await _docker_container_state()

    if container_state == "restarting":
        logger.info("Docker container is restarting, waiting for port...")
        port_ok, elapsed = await _poll_port()
        return {
            "status": "waited_for_restart",
            "port_listening": port_ok,
            "wait_seconds": elapsed,
            "gateway_mode": "docker",
        }

    if container_state in ("exited", "not_found"):
        logger.warning("Docker container %s — starting with docker compose up -d", container_state)
        _, stderr, rc = await _docker_compose("up", "-d", timeout=60.0)
        if rc != 0:
            return {
                "restarted": False,
                "error": f"docker compose up failed: {stderr[:200]}",
                "port_listening": False,
                "gateway_mode": "docker",
            }
    elif container_state == "running" and container_health == "unhealthy":
        # Container is running but IB API is unresponsive (e.g. 2FA expired).
        # Docker's restart: unless-stopped will cycle it. Don't restart from here.
        logger.warning("Docker container running but unhealthy — waiting for Docker auto-restart")
        return {
            "restarted": False,
            "port_listening": False,
            "gateway_mode": "docker",
            "error": "IB Gateway container is unhealthy (IB API not responding). Docker will auto-restart. Check IBKR Mobile for 2FA.",
        }

    # Container running but port not yet ready — wait
    logger.info("Waiting for Gateway port (up to %ds)...", RESTART_WAIT_SECS)
    port_ok, elapsed = await _poll_port()

    if not port_ok:
        return {
            "restarted": True,
            "port_listening": False,
            "gateway_mode": "docker",
            "error": (
                f"Container started but Gateway not accepting connections after {RESTART_WAIT_SECS}s. "
                "Check 2FA approval or container logs: scripts/docker_ib_gateway.sh logs"
            ),
        }

    return {
        "restarted": True,
        "port_listening": True,
        "wait_seconds": elapsed,
        "gateway_mode": "docker",
    }


async def _restart_docker() -> Dict:
    """Restart Gateway via Docker Compose.

    For running containers, issue docker compose restart. Docker's own
    restart: unless-stopped policy handles crash recovery — this is only
    for explicit user-initiated restarts via POST /ib/restart.
    """
    container_state, _ = await _docker_container_state()

    if container_state in ("exited", "not_found"):
        return await _ensure_docker_container()

    logger.info("Restarting Docker ib-gateway container...")
    _, stderr, rc = await _docker_compose("restart", "ib-gateway", timeout=60.0)

    if rc != 0:
        return {
            "restarted": False,
            "error": f"docker compose restart failed: {stderr[:200]}",
            "port_listening": False,
            "gateway_mode": "docker",
        }

    logger.info("Docker restart issued, waiting for Gateway (up to %ds)...", RESTART_WAIT_SECS)
    port_ok, elapsed = await _poll_port()

    if not port_ok:
        return {
            "restarted": True,
            "port_listening": False,
            "gateway_mode": "docker",
            "error": (
                f"Container restarted but Gateway not accepting connections after {RESTART_WAIT_SECS}s. "
                "Check 2FA or container logs."
            ),
        }

    return {
        "restarted": True,
        "port_listening": True,
        "wait_seconds": elapsed,
        "gateway_mode": "docker",
    }


# ---------------------------------------------------------------------------
# Cloud mode — remote Gateway, no local lifecycle management
# ---------------------------------------------------------------------------


async def _check_cloud() -> Dict:
    """Check remote Gateway health via TCP port probe only."""
    port_ok = await asyncio.to_thread(_port_listening)
    return {
        "port_listening": port_ok,
        "upstream_dead": False,
        "service_state": "reachable" if port_ok else "unreachable",
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


# ---------------------------------------------------------------------------
# Public API — dispatches by GATEWAY_MODE
# ---------------------------------------------------------------------------


def is_cloud_mode() -> bool:
    """Return True if Gateway runs on a remote host (no local lifecycle)."""
    return GATEWAY_MODE == "cloud"


def is_docker_mode() -> bool:
    """Return True if Gateway is managed by Docker."""
    return GATEWAY_MODE == "docker"


def is_launchd_mode() -> bool:
    """Return True if Gateway is managed by the local launchd/IBC service."""
    return GATEWAY_MODE == "launchd"


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
        ib = IB()
        try:
            # CLI range (90-99) per CLAUDE.md client-id allocation — clientId 98
            # to avoid colliding with pool (3-5), relay (10-19), subprocesses,
            # scanners, daemons. Brief connect + immediate disconnect.
            ib.connect(IB_HOST, IB_PORT, clientId=98, timeout=timeout)
            accounts = list(ib.managedAccounts() or [])
            return (bool(accounts), accounts)
        except Exception:
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
    haystack = message.lower()
    return any(pattern in haystack for pattern in _IB_OUTAGE_ERROR_PATTERNS)


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
      • previous == "awaiting_2fa" AND new == "authenticated":
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

    is_recovery_edge = previous == "awaiting_2fa" and new_auth_state == "authenticated"
    if not is_recovery_edge:
        return False

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
        return "unreachable"
    if not pool_status:
        return "unknown"
    for role_info in pool_status.values():
        if role_info.get("connected") and role_info.get("managed_accounts"):
            return "authenticated"
    return "awaiting_2fa"


async def check_ib_gateway(
    pool_status: Optional[dict] = None,
    pool=None,
) -> Dict:
    """Check IB Gateway health. Returns status dict for /health endpoint.

    Pass `pool_status` (typically `IBPool.status()`) so the response can
    distinguish "logged in" from "port listening but awaiting 2FA". Without
    it, auth_state is "remote" (cloud mode) or "unknown" (no pool to probe).

    Pass `pool` (the IBPool instance itself) to enable autonomous recovery on
    the awaiting_2fa → authenticated transition. The /health endpoint already
    has both — passing them through keeps recovery on the same cadence as the
    auth-state probe and avoids a second scheduler.
    """
    if is_cloud_mode():
        result = await _check_cloud()
        if not result.get("port_listening"):
            result["auth_state"] = "unreachable"
        elif pool_status:
            # Pool gives us visibility into managed_accounts on the remote
            # Gateway via this process's own client connections — that's the
            # authoritative auth signal even in cloud mode.
            result["auth_state"] = _derive_auth_state(result, pool_status)
        else:
            # No pool to probe (cold start, or this process doesn't run a
            # pool). The TCP probe alone can't distinguish authenticated from
            # awaiting_2fa, so report "remote" and defer to the host that
            # actually owns the Gateway.
            result["auth_state"] = "remote"
        if pool is not None and result["auth_state"] != "remote":
            await handle_auth_state_transition(result["auth_state"], pool)
        return result

    if is_docker_mode():
        result = await _check_docker()
    else:
        result = await _check_launchd()

    result["auth_state"] = _derive_auth_state(result, pool_status)
    result["restart_backoff"] = restart_backoff_state()

    if pool is not None:
        await handle_auth_state_transition(result["auth_state"], pool)

    return result


async def ensure_ib_gateway() -> Dict:
    """Ensure IB Gateway is running. Called at FastAPI startup."""
    async with _restart_lock:
        if is_cloud_mode():
            return await _ensure_cloud()
        if is_docker_mode():
            return await _ensure_docker_container()
        return await _ensure_launchd()


async def restart_ib_gateway(pool=None) -> Dict:
    """Restart IB Gateway. Honors the cross-process 2FA push lock AND the
    in-memory exponential backoff if 2FA stays unapproved.

    Gating order on entry (both gates must pass before issuing a restart):

      1. **2FA push lock** (cross-process, disk-backed). Any restart path
         that fires a fresh IBKR Mobile push — this function, the
         ``ib_watchdog`` oneshot, the operator CLI — first checks the
         lock. While the lock is held by ANOTHER holder, we refuse with
         ``reason="2fa_push_in_flight"``. Same-holder re-entry refreshes
         the lock and proceeds (idempotency for retries from inside the
         same process). The lock guards against the stacked-push failure
         documented in feedback_2fa_push_stacking.md: IBKR's backend
         cannot reconcile multiple pending push tokens for the same
         session — the user gets "unsuccessful" on every approval when
         pushes pile up.
      2. **In-memory backoff window** (per-process). Refuses fresh
         restart attempts inside an exponentially growing window
         (1m, 2m, 5m, 15m, 30m, 60m capped at 60m). Reset only when an
         authenticated probe confirms login.

    After an attempt we probe ``managedAccounts()`` and only treat the
    restart as a success when accounts are returned — a "port listening"
    success is NOT enough; Gateway sits with the API socket open while
    waiting for the IBKR Mobile 2FA push.

    On authenticated success: lock is RELEASED + backoff RESET so the
    next legitimate restart (potentially hours later) is not blocked.
    On awaiting_2fa or unreachable failure: lock REMAINS HELD until its
    TTL expires (or the operator hits ``POST /ib/reset-backoff``).

    Optional ``pool``: when provided, drives the auth-state transition
    handler on a successful authenticated probe. This autonomously fixes
    the documented "pool stuck after 2FA" failure mode
    (feedback_ib_pool_stuck_after_2fa.md).
    """
    async with _restart_lock:
        now = time.time()

        # --- Gate 1: cross-process 2FA push lock ---------------------------
        # Refuse if a different restart path already fired a push.
        # Same-holder acquire refreshes the lease and proceeds — that's
        # the path for a manual retry from inside the FastAPI process
        # after the same caller's previous attempt died mid-cycle.
        acquired, current_lock = ib_2fa_lock.acquire_2fa_push_lock(
            IB_GATEWAY_LOCK_HOLDER,
            reason="restart_ib_gateway",
            now=now,
        )
        if not acquired:
            assert current_lock is not None  # acquire returns (False, lock)
            wait_secs = max(0, int(current_lock.expires_at - now))
            logger.warning(
                "IB Gateway restart refused — 2FA push lock held by %r "
                "(expires in %ds)",
                current_lock.holder, wait_secs,
            )
            return {
                "restarted": False,
                "deferred": True,
                "reason": "2fa_push_in_flight",
                "lock_holder": current_lock.holder,
                "lock_expires_in_secs": wait_secs,
                "lock_acquired_at": current_lock.acquired_at,
                "error": (
                    f"Skipping restart — a 2FA push from {current_lock.holder!r} is "
                    f"already in flight ({wait_secs}s remaining). Stacking another "
                    "push causes IBKR to reject every approval. Approve the existing "
                    "push on your phone (or wait for the lock to expire), then call "
                    "POST /ib/reset-backoff to retry immediately."
                ),
            }

        # --- Gate 2: per-process exponential backoff -----------------------
        if _restart_state["next_attempt_after"] > now:
            wait_secs = int(_restart_state["next_attempt_after"] - now)
            last_iso = (
                datetime.fromtimestamp(_restart_state["last_attempt_at"]).isoformat()
                if _restart_state["last_attempt_at"]
                else "never"
            )
            return {
                "restarted": False,
                "deferred": True,
                "reason": "awaiting_backoff",
                "attempt_count": _restart_state["attempt_count"],
                "next_attempt_in_secs": wait_secs,
                "next_attempt_after": _restart_state["next_attempt_after"],
                "last_attempt_at": last_iso,
                "last_outcome": _restart_state["last_outcome"],
                "error": (
                    f"Skipping restart — last attempt at {last_iso} did not complete login "
                    f"({_restart_state['attempt_count']} consecutive). Backoff window "
                    f"of {wait_secs}s remaining. Approve IBKR Mobile 2FA, then call "
                    f"POST /ib/reset-backoff to retry immediately."
                ),
            }

        if is_cloud_mode():
            # Cloud mode never fires a local push — release the lock we
            # just took so it doesn't artificially block other holders.
            ib_2fa_lock.release_2fa_push_lock()
            return {
                "restarted": False,
                "gateway_mode": "cloud",
                "error": f"Cannot restart remote Gateway at {IB_HOST}:{IB_PORT}. Manage it on the remote host.",
            }

        result = await (_restart_docker() if is_docker_mode() else _restart_launchd())
        _restart_state["last_attempt_at"] = now

        if result.get("port_listening"):
            authenticated, accounts = await _probe_authenticated()
            result["managed_accounts"] = accounts
            if authenticated:
                _restart_state["attempt_count"] = 0
                _restart_state["next_attempt_after"] = 0.0
                _restart_state["last_outcome"] = "authenticated"
                _restart_state["last_accounts"] = accounts
                result["authenticated"] = True
                result["auth_state"] = "authenticated"
                # Release the lock — login completed, other restart paths
                # can proceed when they need to (e.g. a future bounce).
                ib_2fa_lock.release_2fa_push_lock()
                logger.info("IB Gateway restart verified — accounts: %s", accounts)
                # Drive the same auth-transition handler the periodic probe
                # uses, so a successful restart that lands the system back at
                # `authenticated` while the pool is still stale auto-recovers
                # the pool without a separate operator step.
                if pool is not None:
                    await handle_auth_state_transition("authenticated", pool)
            else:
                _restart_state["attempt_count"] += 1
                delay = _next_backoff_delay(_restart_state["attempt_count"])
                _restart_state["next_attempt_after"] = now + delay
                _restart_state["last_outcome"] = "awaiting_2fa"
                result["authenticated"] = False
                result["auth_state"] = "awaiting_2fa"
                result["next_attempt_in_secs"] = delay
                result["attempt_count"] = _restart_state["attempt_count"]
                logger.warning(
                    "IB Gateway restart: port up but managedAccounts empty — "
                    "treating as awaiting_2fa, next attempt allowed in %ds (attempt #%d). "
                    "2FA push lock held for %ds.",
                    delay, _restart_state["attempt_count"],
                    ib_2fa_lock.remaining_lock_secs(now=now),
                )
                # Record the awaiting_2fa observation in the transition tracker
                # so the next "authenticated" sighting fires the recovery edge.
                _auth_transition_state["previous_auth_state"] = "awaiting_2fa"
                # Lock stays HELD — the user is approving the push we
                # just fired, no other path should fire another.
        else:
            _restart_state["attempt_count"] += 1
            delay = _next_backoff_delay(_restart_state["attempt_count"])
            _restart_state["next_attempt_after"] = now + delay
            _restart_state["last_outcome"] = "unreachable"
            result["auth_state"] = "unreachable"
            result["next_attempt_in_secs"] = delay
            result["attempt_count"] = _restart_state["attempt_count"]
            logger.warning(
                "IB Gateway restart: port did not come up — next attempt allowed in %ds (attempt #%d). "
                "2FA push lock held for %ds.",
                delay, _restart_state["attempt_count"],
                ib_2fa_lock.remaining_lock_secs(now=now),
            )
            _auth_transition_state["previous_auth_state"] = "unreachable"
            # Lock stays HELD — even on a fully-down restart, IBC may
            # have started firing pushes before crashing; better to
            # block the next attempt than to stack on a phantom push.

        return result
