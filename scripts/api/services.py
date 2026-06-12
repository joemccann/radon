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
import logging
import os
import re
import shutil
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

# scripts/utils lives under <repo>/scripts/utils; this module lives at
# <repo>/scripts/api. Add the scripts dir to sys.path so the shared
# 2FA push-lock module imports cleanly under any pytest rootdir.
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from utils import ib_2fa_lock  # noqa: E402

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
# being a generic systemctl proxy.
_UNIT_PATTERN = re.compile(r"^radon-[a-z0-9-]+(?:\.service|\.timer)?$|^radon-ib-gateway\.service$")

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


def is_valid_unit(unit: str) -> bool:
    """True when ``unit`` is in the allowlist for service control.

    Centralised so both the listing endpoint and the action endpoint use the
    same rule. Anything outside this pattern is rejected at the boundary.
    """
    return bool(_UNIT_PATTERN.match(unit))


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
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return (
            stdout.decode("utf-8", errors="replace").strip(),
            stderr.decode("utf-8", errors="replace").strip(),
            proc.returncode if proc.returncode is not None else -1,
        )
    except asyncio.TimeoutError:
        proc.kill()
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
    if not is_valid_unit(unit):
        return UnitStatus(unit, "rejected", "unknown", "unknown", "", can_control=False)

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
    return UnitStatus(
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


async def list_units_with_status() -> List[UnitStatus]:
    """Snapshot every known radon-* unit. Used by ``GET /admin/services``."""
    units = await list_units()
    statuses = await asyncio.gather(*(show_unit(u) for u in units))
    return list(statuses)


ALLOWED_ACTIONS = frozenset({"start", "stop", "restart"})

# Cycling the IB Gateway fires an IBKR Mobile 2FA push — start/restart on this
# unit must hold the shared cross-process push lock so the panel never stacks
# a second push on top of the FastAPI restart path or the watchdog self-heal.
# Stop fires no push and is never gated.
GATEWAY_UNIT = "radon-ib-gateway.service"
_GATEWAY_PUSH_ACTIONS = frozenset({"start", "restart"})

# Identifier this module uses when taking the shared 2FA push lock — distinct
# from the FastAPI restart path and the watchdog so the lock file names the
# component mid-2FA-cycle.
ADMIN_PANEL_LOCK_HOLDER = "admin-panel"

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
    """Invoke ``systemctl <action> <unit>`` after allowlist + verb checks.

    Returns an :class:`ActionResult` whether or not the call succeeded so
    the route handler can shape an HTTP response from a single object.
    """
    if action not in ALLOWED_ACTIONS:
        return ActionResult(unit, action, False, f"action {action!r} is not allowed", -1)

    if not is_valid_unit(unit):
        return ActionResult(unit, action, False, f"unit {unit!r} is not allowed", -1)

    if not is_systemd_available():
        return ActionResult(
            unit, action, False,
            "systemctl is not available on this host. "
            "Service control is only available on the Hetzner deployment.",
            -1,
        )

    if unit == GATEWAY_UNIT and action in _GATEWAY_PUSH_ACTIONS:
        refusal = _take_gateway_push_lock(unit, action)
        if refusal is not None:
            return refusal
        # The lock is deliberately NOT released after systemctl returns —
        # the 2FA push is still pending at that point. The crash-safe TTL
        # (DEFAULT_LOCK_TTL_SECS) or the authenticated-probe release in
        # restart_ib_gateway clears it.

    stdout, stderr, rc = await _systemctl(action, unit, timeout=60.0)
    detail = stderr or stdout or f"systemctl exited with rc={rc}"
    return ActionResult(unit, action, rc == 0, detail, rc)


def _take_gateway_push_lock(unit: str, action: str) -> Optional[ActionResult]:
    """Acquire the shared 2FA push lock for a gateway cycle.

    Returns ``None`` when acquired (caller proceeds) or a refusal
    :class:`ActionResult` naming the current holder when another restart
    path already has a push in flight.
    """
    acquired, current = ib_2fa_lock.acquire_2fa_push_lock(
        ADMIN_PANEL_LOCK_HOLDER, reason=f"admin panel {action} {unit}",
    )
    if acquired:
        return None
    assert current is not None  # acquire returns (False, lock) on refusal
    remaining = ib_2fa_lock.remaining_lock_secs()
    logger.warning(
        "admin panel %s %s refused — 2FA push lock held by %r (%ds remaining)",
        action, unit, current.holder, remaining,
    )
    return ActionResult(
        unit, action, False,
        f"2FA push lock held by {current.holder!r} ({remaining}s remaining). "
        "Cycling the gateway now would stack a second IBKR push and every "
        "approval would be rejected. Approve the pending push (or wait for "
        "the lock to expire), then POST /ib/reset-backoff to retry.",
        PUSH_LOCK_HELD_RC,
    )


# Path to the operator CLI installed by radon-cloud/scripts/setup-vps.sh.
# Restart-all goes through this wrapper rather than enumerating units in
# Python because the wrapper knows the correct stop/start ordering (IB Gateway
# first) and reads the current list of radon-* units from systemctl directly.
OPERATOR_CLI_PATH = "/usr/local/bin/radon"

# Walltime ceiling for a full stack restart. radon restart on the live VPS
# typically takes 60-90s; 180s gives headroom for IB Gateway boot + 2FA
# socket-listening probe without leaving the HTTP request hanging forever.
STACK_RESTART_TIMEOUT_S = 180.0


def is_operator_cli_available() -> bool:
    """True when the radon operator CLI is installed and executable.

    Mirrors :func:`is_systemd_available` for the higher-level wrapper.
    """
    return os.access(OPERATOR_CLI_PATH, os.X_OK)


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

    env = {**_inherit_systemctl_env(), "LC_ALL": "C", "LANG": "C"}
    proc = await asyncio.create_subprocess_exec(
        OPERATOR_CLI_PATH, "restart",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=STACK_RESTART_TIMEOUT_S,
        )
        rc = proc.returncode if proc.returncode is not None else -1
        out = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()
        detail = err or out or f"radon restart exited with rc={rc}"
        return ActionResult("radon-stack", "restart", rc == 0, detail, rc)
    except asyncio.TimeoutError:
        proc.kill()
        return ActionResult(
            "radon-stack", "restart", False,
            f"radon restart timed out after {STACK_RESTART_TIMEOUT_S:.0f}s "
            "— check VPS state via SSH",
            -1,
        )
