"""Service control for the operator admin panel.

Surfaces the same units the operator-radon.sh CLI manages (radon-* systemd
units plus the IB Gateway container's service unit) and exposes start/stop/
restart actions. Whitelisted at the unit-name boundary so the panel cannot
control arbitrary system units.

Host modes:
  - Hetzner / Linux with systemd  -> uses ``systemctl`` for unit control.
  - Anything else (laptop docker, launchd, dev)  -> returns ``supported=False``
    so the UI can render a "service control is host-only" notice without an
    error spike.

The endpoint surface is intentionally small (status + 3 verbs) so the front
end can render a generic table without per-service branching.
"""

from __future__ import annotations

import asyncio
import fcntl
import http.client
import ipaddress
import json
import logging
import os
import re
import signal
import shutil
import ssl
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger("radon.services")

# Sentinels emitted by systemctl when a timestamp slot has never been written
# (e.g. ExecMainExitTimestamp on a service that has never finished running).
_NEVER_TIMESTAMPS = frozenset({"", "n/a", "0"})

# Locale-stable timestamp format. systemctl emits timestamps in the host's
# active locale; we force LC_ALL=C in :func:`_systemctl` so they arrive in
# this format regardless of how the box is configured.
_SYSTEMCTL_TIMESTAMP_FORMATS = (
    "%a %Y-%m-%d %H:%M:%S %Z",
    "%a %Y-%m-%d %H:%M:%S",
)

# Whitelisted unit-name pattern. Any unit listed by /admin/services or passed
# to /admin/services/<unit>/<action> must match. This keeps the panel from
# being a generic systemctl proxy. Compared against the canonical form
# (missing type suffix → ``.service``), matching systemd.
_UNIT_PATTERN = re.compile(r"^radon-[a-z0-9-]+(?:\.service|\.timer)?$|^radon-ib-gateway\.service$")

# systemd unit types. A name with none of these suffixes is a .service.
# Tuple so ``str.endswith`` can take it in one call.
_UNIT_TYPE_SUFFIXES = (
    ".service",
    ".socket",
    ".target",
    ".device",
    ".mount",
    ".automount",
    ".swap",
    ".timer",
    ".path",
    ".slice",
    ".scope",
    ".snapshot",
)

# Internal systemd adapter used only by the IB watchdog after it has acquired
# its exact preheld lease. It must never be listed or controllable through the
# generic admin service endpoint.
GATEWAY_PREHELD_UNIT = "radon-ib-gateway-preheld-restart.service"
_INTERNAL_UNITS = frozenset({GATEWAY_PREHELD_UNIT})

# Static catalogue surfaced in /admin/services when systemd is unavailable.
# Lets the UI render the panel + the "not controllable from here" notice
# instead of an empty state.
_PLACEHOLDER_UNITS: List[str] = [
    "radon-ib-gateway.service",
    "radon-api.service",
    "radon-relay.service",
    "radon-monitor.service",
    "radon-newsfeed.service",
    "radon-nextjs.service",
]


@dataclass
class UnitStatus:
    """Snapshot of a single systemd unit, JSON-serializable."""

    unit: str
    load_state: str        # "loaded" | "not-found" | "masked" | ...
    active_state: str      # "active" | "inactive" | "failed" | "activating" | ...
    sub_state: str         # "running" | "dead" | "exited" | ...
    description: str
    can_control: bool
    # When the unit last became active OR last finished (oneshots). UTC ISO8601.
    # ``None`` means "never run" or "timestamp unreadable".
    last_active_at: Optional[str] = None
    # Most recent exit code, populated for ``Type=oneshot`` services. ``None``
    # for long-running daemons (where the value would always be 0 / unset).
    last_exit_code: Optional[int] = None
    # Seconds since the unit became active, populated only for currently-running
    # daemons (``ActiveState=active`` AND ``SubState=running``).
    uptime_secs: Optional[int] = None

    def to_dict(self) -> dict:
        return asdict(self)


def canonicalize_unit_name(unit: str) -> str:
    """Return systemd's implied unit name for ``unit``.

    ``systemctl`` treats a name without a type suffix as ``.service``, so
    ``radon-ib-gateway`` and ``radon-ib-gateway.service`` are the same unit.
    Denylist, broker-restart lease, and the app-role privileged-action gate
    must compare this form; matching the caller's spelling lets those
    interlocks be skipped.
    """
    if not isinstance(unit, str):
        return ""
    name = unit.strip()
    if not name:
        return name
    if name.endswith(_UNIT_TYPE_SUFFIXES):
        return name
    return f"{name}.service"


def is_valid_unit(unit: str) -> bool:
    """True when ``unit`` is in the allowlist for service control.

    Centralised so both the listing endpoint and the action endpoint use the
    same rule. Anything outside this pattern is rejected at the boundary.
    Canonicalizes first so an unsuffixed denylist name cannot slip through.
    """
    unit = canonicalize_unit_name(unit)
    return unit not in _INTERNAL_UNITS and bool(_UNIT_PATTERN.match(unit))


def host_role() -> str:
    """RADON_HOST_ROLE for this process.

    REL-207 (R-570): casefolded, and a NON-EMPTY unknown value maps to the
    least-privileged role ("app": never execs the control helper) — "garbage
    is combined" silently granted combined-role behaviour to a hand-edited
    `App` on the app VM. Unset/empty stays "combined" (the dev default).
    """
    raw = (os.environ.get("RADON_HOST_ROLE") or "").strip().strip("\"'")
    if not raw:
        return "combined"
    lowered = raw.casefold()
    if lowered in {"app", "broker", "combined"}:
        return lowered
    return "app"


_HETZNER_PRIVATE = ipaddress.ip_network("10.0.0.0/16")
REMOTE_VERBS = frozenset({"start", "stop", "restart", "reset-lease", "status"})



def is_systemd_available() -> bool:
    """True when this host can run ``systemctl`` against ``radon-*`` units.

    On the laptop (macOS / dev) the binary is absent and we degrade to a
    read-only catalogue. The boolean is intentionally narrow: presence of
    ``systemctl`` on PATH is enough; whether the caller has permission is
    surfaced later by the per-action result.
    """
    return shutil.which("systemctl") is not None


async def _systemctl(*args: str, timeout: float = 15.0) -> tuple[str, str, int]:
    """Run a systemctl invocation and return (stdout, stderr, returncode).

    Wraps subprocess so all callers share the same timeout and decode rules.
    Forces ``LC_ALL=C`` so timestamp strings come back in a single parseable
    locale regardless of host configuration.
    """
    env = {"LC_ALL": "C", "LANG": "C"}
    proc = await asyncio.create_subprocess_exec(
        "systemctl",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**_inherit_systemctl_env(), **env},
        start_new_session=True,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return (
            stdout.decode("utf-8", errors="replace").strip(),
            stderr.decode("utf-8", errors="replace").strip(),
            proc.returncode if proc.returncode is not None else -1,
        )
    except asyncio.CancelledError:
        await _terminate_process_group(proc)
        raise
    except asyncio.TimeoutError:
        await _terminate_process_group(proc)
        return ("", "systemctl timed out", -1)


def _inherit_systemctl_env() -> Dict[str, str]:
    """Return the minimum env systemctl needs (PATH + DBus session)."""
    keep = {"PATH", "HOME", "USER", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"}
    return {k: v for k, v in os.environ.items() if k in keep}


def _parse_show_output(raw: str) -> Dict[str, str]:
    """Parse ``systemctl show -p key1,key2 unit`` output into a dict."""
    fields: Dict[str, str] = {}
    for line in raw.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        fields[key.strip()] = value.strip()
    return fields


def parse_systemctl_timestamp(raw: str) -> Optional[str]:
    """Convert systemctl's human timestamp into a UTC ISO8601 string.

    systemctl with ``LC_ALL=C`` emits values like
    ``"Tue 2026-05-19 18:41:51 UTC"``. Returns ``None`` for the "never set"
    sentinel (empty / ``"0"`` / ``"n/a"``) so callers can render "never run".
    """
    if not raw or raw.strip().lower() in _NEVER_TIMESTAMPS:
        return None
    text = raw.strip()
    for fmt in _SYSTEMCTL_TIMESTAMP_FORMATS:
        try:
            dt = datetime.strptime(text, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    return None


def _parse_int(raw: str) -> Optional[int]:
    """Return int(raw) or ``None`` when raw is empty / non-numeric."""
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _derive_last_active(parsed: Dict[str, str]) -> Optional[str]:
    """Pick the most relevant "last activity" timestamp for the unit.

    Long-running daemons report their start time via ``ActiveEnterTimestamp``.
    Oneshot units transition back to ``inactive`` after each run; the most
    useful timestamp there is ``ExecMainExitTimestamp`` (last finish) which
    falls through to ``InactiveEnterTimestamp`` if the exec slot is empty.
    """
    candidates = (
        parsed.get("ExecMainExitTimestamp", ""),
        parsed.get("InactiveEnterTimestamp", ""),
        parsed.get("ActiveEnterTimestamp", ""),
    )
    iso_values = [parse_systemctl_timestamp(c) for c in candidates]
    iso_values = [v for v in iso_values if v]
    if not iso_values:
        return None
    # Lexicographic max works on ISO8601 strings ending in Z.
    return max(iso_values)


def _derive_uptime_secs(parsed: Dict[str, str]) -> Optional[int]:
    """Seconds since ``ActiveEnterTimestamp`` for a currently-running unit.

    Returns ``None`` unless the unit is ``active`` + ``running`` (anything
    else, like a oneshot that already exited, doesn't have a meaningful
    uptime to display).
    """
    if parsed.get("ActiveState") != "active":
        return None
    if parsed.get("SubState") != "running":
        return None
    iso = parse_systemctl_timestamp(parsed.get("ActiveEnterTimestamp", ""))
    if not iso:
        return None
    try:
        start = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    delta = (datetime.now(timezone.utc) - start).total_seconds()
    if delta < 0:
        return 0
    return int(delta)


def _derive_last_exit_code(parsed: Dict[str, str]) -> Optional[int]:
    """Most recent exec exit code, or ``None`` if the unit has never run.

    Only meaningful for ``Type=oneshot``; for long-running daemons systemd
    reports the running PID's last status which would be misleading.
    """
    service_type = parsed.get("Type", "")
    if service_type != "oneshot":
        return None
    return _parse_int(parsed.get("ExecMainStatus", ""))


async def list_units() -> List[str]:
    """Return the canonical list of radon-* units this host knows about.

    On a systemd host we enumerate loaded radon-* units via
    ``systemctl list-units 'radon-*' --all --no-legend``. On non-systemd
    hosts we fall back to ``_PLACEHOLDER_UNITS`` so the UI can still render.
    """
    if not is_systemd_available():
        return list(_PLACEHOLDER_UNITS)

    stdout, _stderr, rc = await _systemctl(
        "list-units", "radon-*", "--all", "--no-legend", "--plain",
    )
    if rc != 0 or not stdout:
        return list(_PLACEHOLDER_UNITS)

    units: List[str] = []
    for line in stdout.splitlines():
        first = line.strip().split()
        if not first:
            continue
        unit = first[0]
        if is_valid_unit(unit):
            units.append(unit)
    return units or list(_PLACEHOLDER_UNITS)


async def show_unit(unit: str) -> UnitStatus:
    """Return a :class:`UnitStatus` snapshot for a single unit.

    Always returns a value, never raises — a not-found / unreadable unit
    surfaces as ``load_state="not-found"`` so the UI can render the row.
    """
    unit = canonicalize_unit_name(unit)
    if not is_valid_unit(unit):
        return UnitStatus(unit, "rejected", "unknown", "unknown", "", can_control=False)

    # App FastAPI runs in a container with no systemctl. Gateway status still
    # comes from the broker daemon over mTLS.
    if unit == GATEWAY_UNIT and host_role() == "app":
        status = UnitStatus(
            unit,
            load_state="remote",
            active_state="unknown",
            sub_state="unknown",
            description="IB Gateway on broker",
            can_control=False,
        )
        if not is_remote_gateway_configured():
            return status
        remote = await remote_gateway_action("status")
        status.can_control = True
        if "transition-pending" in remote.detail:
            # REL-172 (R-475): mid-restart on the broker. `activating` is what
            # gatewayPowerState reads as transitional, so Start stays disarmed.
            status.active_state = "activating"
            status.sub_state = "transition-pending"
        elif remote.ok and "running" in remote.detail:
            status.active_state = "active"
            status.sub_state = "running"
        elif "stopped" in remote.detail:
            status.active_state = "inactive"
            status.sub_state = "dead"
        return status

    if not is_systemd_available():
        return UnitStatus(
            unit,
            load_state="unsupported",
            active_state="unknown",
            sub_state="unknown",
            description="systemctl unavailable on this host",
            can_control=False,
        )

    stdout, _stderr, rc = await _systemctl(
        "show", unit,
        "-p", "LoadState",
        "-p", "ActiveState",
        "-p", "SubState",
        "-p", "Description",
        "-p", "Type",
        "-p", "ActiveEnterTimestamp",
        "-p", "InactiveEnterTimestamp",
        "-p", "ExecMainStartTimestamp",
        "-p", "ExecMainExitTimestamp",
        "-p", "ExecMainStatus",
    )
    if rc != 0:
        return UnitStatus(unit, "unknown", "unknown", "unknown", "", can_control=False)

    parsed = _parse_show_output(stdout)
    load_state = parsed.get("LoadState", "unknown")
    status = UnitStatus(
        unit=unit,
        load_state=load_state,
        active_state=parsed.get("ActiveState", "unknown"),
        sub_state=parsed.get("SubState", "unknown"),
        description=parsed.get("Description", ""),
        can_control=load_state == "loaded",
        last_active_at=_derive_last_active(parsed),
        last_exit_code=_derive_last_exit_code(parsed),
        uptime_secs=_derive_uptime_secs(parsed),
    )
    if unit != GATEWAY_UNIT:
        return status

    # A Type=oneshot/RemainAfterExit wrapper can stay active (exited) after
    # the Docker container dies. Never surface that stale unit state as a
    # healthy Gateway; ask the authoritative helper for the real container.
    if not is_gateway_control_available():
        status.can_control = False
        status.active_state = "unknown"
        status.sub_state = "unknown"
        return status
    helper_stdout, _helper_stderr, helper_rc = await _run_gateway_helper(
        "status", timeout=15.0,
    )
    if helper_rc == 0 and helper_stdout == "running":
        status.active_state = "active"
        status.sub_state = "running"
    elif helper_stdout == "stopped":
        status.active_state = "inactive"
        status.sub_state = "dead"
    else:
        status.active_state = "unknown"
        status.sub_state = "unknown"
    status.can_control = True
    return status


async def list_units_with_status() -> List[UnitStatus]:
    """Snapshot every known radon-* unit. Used by ``GET /admin/services``."""
    units = await list_units()
    statuses = await asyncio.gather(*(show_unit(u) for u in units))
    return list(statuses)


ALLOWED_ACTIONS = frozenset({"start", "stop", "restart"})

# Cycling the IB Gateway fires an IBKR Mobile 2FA push. The installed cloud
# helper is the single lease owner and Docker lifecycle path; callers must not
# pre-acquire and then delegate because same-holder re-entry correctly fails.
GATEWAY_UNIT = "radon-ib-gateway.service"
GATEWAY_CONTROL_PATH = "/usr/local/bin/radon-ib-gateway-control"
GATEWAY_CONTROL_TIMEOUT_S = 120.0
GATEWAY_LEASE_HELD_RC = 75
GATEWAY_CONTROL_BUSY_RC = 74
PROCESS_TERM_GRACE_S = 1.0
# REL-171 (R-499): deadlines on the remote path are monotonic. The broker
# helper runs under HELPER_TIMEOUT_S = 120 (scripts/ib_gateway_remote/serve.py);
# this budget must outlive it plus the mTLS round trip, and the Next routes
# (web/app/api/admin/**) must outlive this. Literals on purpose: both tests
# (test_services.py, web/tests/admin-remote-timeouts.test.ts) parse them.
REMOTE_TIMEOUT_S = 135.0
# REL-171 (R-474): the status probe backs a 5s UI poll. It gets its own short
# socket timeout, one in-flight probe per process (later polls coalesce onto
# it instead of stacking executor threads) and a brief result cache.
REMOTE_STATUS_TIMEOUT_S = 5.0
REMOTE_STATUS_CACHE_S = 2.0
# ActionResult.returncode sentinels for the remote path (R-500): a dead mTLS
# link is a gateway timeout, a malformed broker reply is a bad gateway —
# never the -1 "caller error" bucket the route maps to 400.
REMOTE_UNREACHABLE_RC = 504
REMOTE_BAD_REPLY_RC = 502


def _remote_url_allowed(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.path not in {"", "/"}:
        return False
    host = parsed.hostname or ""
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return addr.is_loopback or addr in _HETZNER_PRIVATE


def is_remote_gateway_configured() -> bool:
    """True when the app host can call the broker daemon over mTLS."""
    if host_role() != "app":
        return False
    url = (os.environ.get("RADON_IB_REMOTE_URL") or "").strip()
    if not _remote_url_allowed(url):
        return False
    ca = Path(os.environ.get("RADON_IB_REMOTE_CA") or "")
    cert = Path(os.environ.get("RADON_IB_REMOTE_CLIENT_CERT") or "")
    key = Path(os.environ.get("RADON_IB_REMOTE_CLIENT_KEY") or "")
    return ca.is_file() and cert.is_file() and key.is_file()


def _remote_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context(
        cafile=os.environ["RADON_IB_REMOTE_CA"],
    )
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = False
    ctx.load_cert_chain(
        os.environ["RADON_IB_REMOTE_CLIENT_CERT"],
        os.environ["RADON_IB_REMOTE_CLIENT_KEY"],
    )
    return ctx


def _decode_remote_body(raw: bytes) -> dict:
    """Broker body -> dict, or a structured failure. Never raises (R-501)."""
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {"ok": False, "detail": "broker reply is not JSON", "malformed": True}
    if not isinstance(payload, dict):
        return {"ok": False, "detail": "broker reply is not an object", "malformed": True}
    return payload


def _remote_http(verb: str, timeout: float) -> tuple[int, dict]:
    if verb not in REMOTE_VERBS:
        return -1, {"ok": False, "detail": f"verb not allowed: {verb}"}
    base = os.environ["RADON_IB_REMOTE_URL"].rstrip("/")
    path = "/status" if verb == "status" else f"/{verb}"
    method = "GET" if verb == "status" else "POST"
    req = urllib.request.Request(base + path, method=method, data=b"" if method == "POST" else None)
    try:
        with urllib.request.urlopen(req, context=_remote_ssl_context(), timeout=timeout) as resp:
            return resp.status, _decode_remote_body(resp.read())
    except urllib.error.HTTPError as exc:
        payload = _decode_remote_body(exc.read())
        payload.setdefault("detail", str(exc))
        return exc.code, payload
    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
        ValueError,
        # REL-200 (R-566): a TLS-valid broker emitting non-HTTP garbage raises
        # BadStatusLine/HTTPException — structured 502, never a raw 500.
        http.client.HTTPException,
    ) as exc:
        return -1, {"ok": False, "detail": str(exc)}


# max_workers > 1 on purpose (REL-200): an abandoned wedged probe still
# occupies its worker until its socket dies; the replacement must not queue
# behind it. Coalescing keeps at most one LIVE probe; the extra workers only
# absorb abandoned corpses.
_remote_status_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ib-remote-status")
_remote_status_guard = threading.Lock()
_remote_status_inflight: Optional[Future] = None
_remote_status_started: float = 0.0
_remote_status_cache: Optional[tuple[float, tuple[int, dict]]] = None
# REL-200 (R-561): urlopen's timeout bounds per-socket ops, not the total —
# a one-byte-every-4s broker wedges the probe forever. Past this dwell the
# in-flight future is abandoned (thread left to die with its socket) and a
# fresh probe replaces it.
REMOTE_STATUS_ABANDON_AFTER_S = 3 * REMOTE_STATUS_TIMEOUT_S


def _reset_remote_status_cache() -> None:
    """Test hook: forget the cached probe and the in-flight handle."""
    global _remote_status_inflight, _remote_status_cache, _remote_status_started
    with _remote_status_guard:
        _remote_status_inflight = None
        _remote_status_cache = None
        _remote_status_started = 0.0


def _remote_status_future() -> Future:
    """The one in-flight status probe, started if none is running.

    REL-200 (R-561): an in-flight probe older than the abandonment dwell is
    dropped and replaced — its worker thread dies with its wedged socket.
    """
    global _remote_status_inflight, _remote_status_started
    with _remote_status_guard:
        stale = (
            _remote_status_inflight is not None
            and not _remote_status_inflight.done()
            and time.monotonic() - _remote_status_started > REMOTE_STATUS_ABANDON_AFTER_S
        )
        if stale:
            logger.warning(
                "broker status probe wedged for >%.0fs; abandoning it and "
                "starting a fresh probe",
                REMOTE_STATUS_ABANDON_AFTER_S,
            )
            _remote_status_inflight = None
        if _remote_status_inflight is None or _remote_status_inflight.done():
            _remote_status_inflight = _remote_status_executor.submit(
                _remote_http, "status", REMOTE_STATUS_TIMEOUT_S
            )
            _remote_status_started = time.monotonic()
        return _remote_status_inflight


async def _remote_status() -> tuple[int, dict]:
    """Bounded, coalesced, briefly cached broker status (R-474).

    Ten concurrent UI polls against a silently unreachable broker used to
    stack ten 120s executor threads and starve every other to_thread user.
    Now one probe is in flight at a time, every caller waits at most
    REMOTE_STATUS_TIMEOUT_S on it, and a fresh answer is reused for
    REMOTE_STATUS_CACHE_S.
    """
    global _remote_status_cache
    cached = _remote_status_cache
    if cached is not None and time.monotonic() - cached[0] < REMOTE_STATUS_CACHE_S:
        return cached[1]
    future = _remote_status_future()
    try:
        result = await asyncio.wait_for(
            asyncio.wrap_future(future), timeout=REMOTE_STATUS_TIMEOUT_S + 0.5
        )
    except asyncio.TimeoutError:
        return -1, {
            "ok": False,
            "detail": (
                f"broker status probe exceeded {REMOTE_STATUS_TIMEOUT_S:.0f}s "
                "(one probe stays in flight; later polls coalesce onto it)"
            ),
        }
    with _remote_status_guard:
        _remote_status_cache = (time.monotonic(), result)
    return result


def _remote_returncode(payload: dict, status: int) -> int:
    """``returncode`` from the broker body as an int, or the bad-reply sentinel."""
    raw = payload.get("returncode")
    if raw is None or raw == "":
        return 0 if payload.get("ok") else status
    try:
        return int(raw)
    except (TypeError, ValueError):
        return REMOTE_BAD_REPLY_RC


async def remote_gateway_action(action: str) -> ActionResult:
    """POST one allowlisted verb to the broker daemon. Never execs the helper."""
    if action not in REMOTE_VERBS:
        return ActionResult(GATEWAY_UNIT, action, False, f"action {action!r} is not allowed", -1)
    if action == "status":
        status, payload = await _remote_status()
    else:
        status, payload = await asyncio.to_thread(_remote_http, action, REMOTE_TIMEOUT_S)
    if not isinstance(payload, dict):
        payload = {"ok": False, "detail": "broker reply is not an object", "malformed": True}
    detail = str(payload.get("detail") or payload.get("state") or payload.get("error") or "")
    rc = _remote_returncode(payload, status)
    if status == 409 or rc in {GATEWAY_LEASE_HELD_RC, GATEWAY_CONTROL_BUSY_RC, PUSH_LOCK_HELD_RC}:
        return ActionResult(GATEWAY_UNIT, action, False, detail, PUSH_LOCK_HELD_RC)
    ok = bool(payload.get("ok")) or (action == "status" and payload.get("state") in {"running", "stopped"})
    if ok:
        return ActionResult(GATEWAY_UNIT, action, True, detail or f"remote {action} HTTP {status}", rc)
    if status == -1:
        failure_rc = REMOTE_UNREACHABLE_RC
    elif payload.get("malformed") or rc in {0, -1}:
        failure_rc = REMOTE_BAD_REPLY_RC
    else:
        failure_rc = rc
    return ActionResult(GATEWAY_UNIT, action, False, detail or f"remote {action} HTTP {status}", failure_rc)


OPERATOR_CLI_PATH = "/usr/local/bin/radon"
OPERATOR_UNIT_TIMEOUT_S = 75.0
# Shared with radon-cloud deploy/operator control so admin mutations never
# race a release mid-transition. Override only in tests.
DEPLOY_LOCK_FILE = Path(
    os.environ.get("RADON_DEPLOY_LOCK_FILE", "/home/radon/.radon-deploy.lock")
)
SYSTEMCTL_MUTATION_TIMEOUT_S = 60.0

# ActionResult.returncode sentinel for "refused: 2FA push lock held by another
# holder". The route maps it to HTTP 409 (conflict, retry later) instead of
# the generic 400/502 buckets.
PUSH_LOCK_HELD_RC = 409


@dataclass
class ActionResult:
    """Outcome of a start/stop/restart call against a single unit."""

    unit: str
    action: str
    ok: bool
    detail: str
    returncode: int

    def to_dict(self) -> dict:
        return asdict(self)


async def control_unit(unit: str, action: str) -> ActionResult:
    """Control an allowed unit through its authoritative lifecycle path.

    Returns an :class:`ActionResult` whether or not the call succeeded so
    the route handler can shape an HTTP response from a single object.
    Canonicalizes the unit name before every interlock so systemd aliases
    (``radon-ib-gateway`` ≡ ``radon-ib-gateway.service``) cannot skip the
    denylist, the broker restart lease, or the privileged-action gate.
    """
    unit = canonicalize_unit_name(unit)
    if action not in ALLOWED_ACTIONS:
        return ActionResult(unit, action, False, f"action {action!r} is not allowed", -1)

    if not is_valid_unit(unit):
        return ActionResult(unit, action, False, f"unit {unit!r} is not allowed", -1)

    if unit == GATEWAY_UNIT:
        return await _control_gateway(action)

    if not is_systemd_available():
        return ActionResult(
            unit, action, False,
            "systemctl is not available on this host. "
            "Service control is only available on the Hetzner deployment.",
            -1,
        )

    return await _control_unit_under_deploy_lock(unit, action)


async def _control_unit_under_deploy_lock(unit: str, action: str) -> ActionResult:
    """Run a non-gateway systemctl mutation while holding the deploy lock."""
    try:
        handle = open(DEPLOY_LOCK_FILE, "a+", encoding="utf-8")
    except OSError as exc:
        return ActionResult(
            unit,
            action,
            False,
            f"cannot open deploy/control lock at {DEPLOY_LOCK_FILE}: {exc}",
            -1,
        )

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return ActionResult(
            unit,
            action,
            False,
            "deploy/control lock held",
            PUSH_LOCK_HELD_RC,
        )

    try:
        stdout, stderr, rc = await _systemctl(
            action, unit, timeout=SYSTEMCTL_MUTATION_TIMEOUT_S,
        )
        detail = stderr or stdout or f"systemctl exited with rc={rc}"
        if rc == -1 and "timed out" in detail.lower():
            cancel_detail = await _cancel_pending_systemd_jobs(unit)
            if cancel_detail:
                detail = f"{detail}; {cancel_detail}"
        return ActionResult(unit, action, rc == 0, detail, rc)
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


async def _cancel_pending_systemd_jobs(unit: str) -> str:
    """Cancel still-running systemd jobs for ``unit`` after a timed-out action."""
    stdout, _stderr, rc = await _systemctl(
        "list-jobs", "--no-legend", "--plain", "--no-pager", timeout=15.0,
    )
    if rc != 0 or not stdout:
        return ""

    job_ids: list[str] = []
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == unit:
            job_ids.append(parts[0])
    if not job_ids:
        return ""

    for job_id in job_ids:
        await _systemctl("cancel", job_id, timeout=15.0)
    return f"cancelled pending systemd jobs {', '.join(job_ids)}"


def is_gateway_control_available() -> bool:
    """Return whether the installed authoritative Gateway helper is usable."""
    return os.access(GATEWAY_CONTROL_PATH, os.X_OK)


async def _control_gateway(action: str) -> ActionResult:
    """Delegate Gateway lifecycle to the helper that owns lease acquisition."""
    if host_role() == "app":
        if not is_remote_gateway_configured():
            return ActionResult(
                GATEWAY_UNIT,
                action,
                False,
                "RADON_HOST_ROLE=app. Gateway lifecycle is on the broker.",
                -1,
            )
        return await remote_gateway_action(action)
    if not is_gateway_control_available():
        return ActionResult(
            GATEWAY_UNIT,
            action,
            False,
            f"IB Gateway control helper unavailable at {GATEWAY_CONTROL_PATH}",
            -1,
        )

    stdout, stderr, rc = await _run_gateway_helper(
        action, timeout=GATEWAY_CONTROL_TIMEOUT_S,
    )
    detail = stderr or stdout or f"Gateway control exited with rc={rc}"
    if rc in {GATEWAY_LEASE_HELD_RC, GATEWAY_CONTROL_BUSY_RC}:
        logger.warning("admin Gateway %s refused by active 2FA lease: %s", action, detail)
        return ActionResult(GATEWAY_UNIT, action, False, detail, PUSH_LOCK_HELD_RC)
    return ActionResult(GATEWAY_UNIT, action, rc == 0, detail, rc)


async def _run_gateway_helper(
    action: str, *, timeout: float,
) -> tuple[str, str, int]:
    """Run one helper command with bounded, fully-reaped subprocess cleanup."""
    env = {**_inherit_systemctl_env(), "LC_ALL": "C", "LANG": "C"}
    proc = await asyncio.create_subprocess_exec(
        GATEWAY_CONTROL_PATH,
        action,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        start_new_session=True,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout,
        )
    except asyncio.CancelledError:
        await _terminate_process_group(proc)
        raise
    except asyncio.TimeoutError:
        await _terminate_process_group(proc)
        return "", f"IB Gateway control timed out after {timeout:.0f}s", -1

    rc = proc.returncode if proc.returncode is not None else -1
    out = stdout.decode("utf-8", errors="replace").strip()
    err = stderr.decode("utf-8", errors="replace").strip()
    return out, err, rc


async def _terminate_process_group(proc: asyncio.subprocess.Process) -> None:
    """TERM, then KILL and reap a subprocess plus every descendant it owns."""
    pid = getattr(proc, "pid", None)
    if pid is not None:
        try:
            os.killpg(pid, signal.SIGTERM)
        except (PermissionError, ProcessLookupError):
            pass
    elif proc.returncode is None:
        try:
            proc.kill()
        except (PermissionError, ProcessLookupError):
            pass

    try:
        await asyncio.wait_for(proc.wait(), timeout=PROCESS_TERM_GRACE_S)
    except (asyncio.TimeoutError, ProcessLookupError):
        pass

    if pid is not None:
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
    try:
        await proc.wait()
    except ProcessLookupError:
        pass


# Path to the operator CLI installed by radon-cloud/scripts/setup-vps.sh.
# Restart-all goes through this wrapper rather than enumerating units in
# Python because the wrapper knows the correct stop/start ordering (IB Gateway
# first) and reads the current list of radon-* units from systemctl directly.
# Walltime ceiling for a full stack restart. radon restart on the live VPS
# typically takes 60-90s; 180s gives headroom for IB Gateway boot + 2FA
# socket-listening probe without leaving the HTTP request hanging forever.
STACK_RESTART_TIMEOUT_S = 180.0


def is_operator_cli_available() -> bool:
    """True when the radon operator CLI is installed and executable.

    Mirrors :func:`is_systemd_available` for the higher-level wrapper.
    """
    return os.access(OPERATOR_CLI_PATH, os.X_OK)


async def _run_operator_command(
    *args: str, timeout: float,
) -> tuple[str, str, int]:
    """Run one fixed operator command with bounded process-group cleanup."""
    env = {**_inherit_systemctl_env(), "LC_ALL": "C", "LANG": "C"}
    proc = await asyncio.create_subprocess_exec(
        OPERATOR_CLI_PATH,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        start_new_session=True,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.CancelledError:
        await _terminate_process_group(proc)
        raise
    except asyncio.TimeoutError:
        await _terminate_process_group(proc)
        return "", f"operator timed out after {timeout:.0f}s", -1
    return (
        stdout.decode("utf-8", errors="replace").strip(),
        stderr.decode("utf-8", errors="replace").strip(),
        proc.returncode if proc.returncode is not None else -1,
    )


async def restart_full_stack() -> ActionResult:
    """Run ``radon restart`` to stop+start every ``radon-*`` systemd unit.

    Uses the operator CLI installed at :data:`OPERATOR_CLI_PATH` because the
    wrapper knows the correct stop/start ordering (IB Gateway first) and
    auto-discovers the unit set via ``systemctl list-units 'radon-*'``.
    Returns the same :class:`ActionResult` shape as :func:`control_unit` so
    the route handler treats it uniformly.

    Notes:
        - Bounded by :data:`STACK_RESTART_TIMEOUT_S`. On timeout the result
          reports ``ok=False`` with ``returncode=-1`` and the operator can
          recover via SSH.
        - This call kills the calling process indirectly via the systemd
          cascade (``radon-api.service`` is one of the units). The HTTP
          response may not make it back to the client. Callers must treat a
          dropped TCP connection AFTER a successful ``radon stop`` as a
          success indicator and verify by polling ``/health`` once the
          backend comes back.
    """
    if not is_operator_cli_available():
        return ActionResult(
            "radon-stack", "restart", False,
            "operator CLI not available at /usr/local/bin/radon — "
            "service control requires the Hetzner deployment.",
            -1,
        )

    stdout, stderr, rc = await _run_operator_command(
        "restart", timeout=STACK_RESTART_TIMEOUT_S,
    )
    detail = stderr or stdout or f"radon restart exited with rc={rc}"
    if rc == GATEWAY_CONTROL_BUSY_RC:
        return ActionResult(
            "radon-stack", "restart", False, detail, PUSH_LOCK_HELD_RC,
        )
    return ActionResult("radon-stack", "restart", rc == 0, detail, rc)
