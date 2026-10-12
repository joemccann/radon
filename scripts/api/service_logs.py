"""Journal tail behind a failed or stale /admin row (``GET /admin/logs``).

Resolution: a ``service_health`` name maps to its owning systemd unit through
``service_log_units.json`` (only names whose unit is NOT ``radon-<name>.service``;
a JSON ``null`` means no unit owns the writer), else ``radon-<name>.service``.
A ``.timer`` reads the journal of the ``.service`` it triggers.

Fetching, first match wins:

1. the radon-control socket (production app container): ``{"op":"logs"}``,
   which runs ``sudo -n /usr/local/bin/radon logs <unit>`` on the host;
2. local systemd (bare-metal host): the same fixed journalctl argv, no sudo;
3. neither: ``available=False`` with an operator-readable reason.

An unreadable journal is never a 500: the caller gets 200 with
``available=False``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Optional

from api import host_control
from api import journal_entries
from api import services as admin_services

logger = logging.getLogger("radon.api")

DEFAULT_OVERRIDES_PATH = Path(__file__).resolve().parent / "service_log_units.json"
OVERRIDES_PATH = DEFAULT_OVERRIDES_PATH
LOG_UNIT_RE = re.compile(r"^radon-[a-z0-9-]+\.service$")
_QUERY_UNIT_RE = re.compile(r"^radon-[a-z0-9-]+\.(?:service|timer)$")
_SERVICE_NAME_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_DERIVABLE_NAME_RE = re.compile(r"^[a-z0-9-]+$")
# The operator bounds journalctl at 10s and the daemon waits 15s for it.
HOST_CONTROL_LOGS_TIMEOUT_S = 20.0
JOURNAL_TIMEOUT_S = 10.0


class LogQueryError(ValueError):
    """The query names no resolvable service or unit (HTTP 400)."""


def _is_log_unit(unit: object) -> bool:
    return (
        isinstance(unit, str)
        and bool(LOG_UNIT_RE.match(unit))
        and not unit.startswith("radon-beta-")
        and admin_services.is_valid_unit(unit)
    )


def _load_overrides() -> dict:
    try:
        raw = json.loads(Path(OVERRIDES_PATH).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("service log unit overrides unreadable (%s): %s", OVERRIDES_PATH, exc)
        return {}
    return raw if isinstance(raw, dict) else {}


def validate_service(service: object) -> str:
    if not isinstance(service, str) or not _SERVICE_NAME_RE.match(service):
        raise LogQueryError("service must be a service_health name")
    return service


def validate_unit(unit: object) -> str:
    if (
        not isinstance(unit, str)
        or not _QUERY_UNIT_RE.match(unit)
        or unit.startswith("radon-beta-")
        or not admin_services.is_valid_unit(unit)
    ):
        raise LogQueryError("unit must be radon-NAME.service or radon-NAME.timer")
    return unit


def resolve_unit(*, service: Optional[str] = None, unit: Optional[str] = None) -> Optional[str]:
    """The ``.service`` whose journal answers for ``service`` or ``unit``.

    ``None`` means no systemd unit owns that writer.
    """
    if unit is not None:
        unit = validate_unit(unit)
        if unit.endswith(".timer"):
            unit = unit[: -len(".timer")] + ".service"
        return unit if _is_log_unit(unit) else None
    service = validate_service(service)
    overrides = _load_overrides()
    if service in overrides:
        mapped = overrides[service]
        if mapped is None:
            return None
        if _is_log_unit(mapped):
            return mapped
        logger.warning("service log unit override for %s is not a radon service: %r", service, mapped)
        return None
    if not _DERIVABLE_NAME_RE.match(service):
        return None
    derived = f"radon-{service}.service"
    return derived if _is_log_unit(derived) else None


def _payload(service, unit, *, available, reason=None, entries=None, truncated=False) -> dict:
    return {
        "service": service,
        "unit": unit,
        "available": available,
        "reason": reason,
        "entries": entries or [],
        "truncated": bool(truncated),
    }


def _clean_entries(raw: object) -> list[dict]:
    """Trust nothing in the daemon reply beyond the documented entry shape."""
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        ts, message, priority = row.get("ts"), row.get("message"), row.get("priority")
        if not isinstance(ts, str) or not isinstance(message, str):
            continue
        if isinstance(priority, bool) or not isinstance(priority, int):
            priority = None
        out.append({
            "ts": ts,
            "priority": priority,
            "message": message[: journal_entries.MAX_LOG_MESSAGE_CHARS],
        })
    return out


async def _run_journalctl(argv: list[str], timeout: float) -> tuple[int, str, str]:
    """Exec the fixed journalctl argv. Returns (returncode, stdout, stderr)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"},
            start_new_session=True,
        )
    except OSError as exc:
        return 127, "", f"journalctl could not start: {exc}"
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "", f"journalctl timed out after {timeout:.0f}s"
    return (
        proc.returncode if proc.returncode is not None else 1,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace").strip(),
    )


async def read_logs(
    *, service: Optional[str] = None, unit: Optional[str] = None, actor: str = "unknown",
) -> dict:
    """The ``GET /admin/logs`` body. Raises ``LogQueryError`` on bad input."""
    if (service is None) == (unit is None):
        raise LogQueryError("pass exactly one of service or unit")
    resolved = resolve_unit(service=service, unit=unit)
    if resolved is None:
        label = service if service is not None else unit
        return _payload(
            service, None, available=False,
            reason=f"No systemd unit owns {label}, so there is no journal to show.",
        )

    if host_control.socket_present():
        try:
            reply = await host_control.acall(
                {"op": "logs", "unit": resolved, "actor": actor}, HOST_CONTROL_LOGS_TIMEOUT_S,
            )
        except host_control.HostControlError as exc:
            return _payload(service, resolved, available=False,
                            reason=f"Host control did not return logs: {exc}")
        if not reply.get("ok"):
            detail = str(reply.get("error") or reply.get("detail") or "refused")[:300]
            return _payload(service, resolved, available=False,
                            reason=f"Host control did not return logs: {detail}")
        return _payload(service, resolved, available=True,
                        entries=_clean_entries(reply.get("entries")),
                        truncated=reply.get("truncated") is True)

    if admin_services.is_systemd_available():
        rc, stdout, stderr = await _run_journalctl(
            journal_entries.journal_argv(resolved), JOURNAL_TIMEOUT_S,
        )
        if rc != 0:
            detail = (stderr or f"exited {rc}")[-300:]
            return _payload(service, resolved, available=False,
                            reason=f"journalctl failed: {detail}")
        entries, truncated = journal_entries.parse_journal_json(stdout)
        return _payload(service, resolved, available=True, entries=entries, truncated=truncated)

    return _payload(
        service, resolved, available=False,
        reason="The journal is not readable from this host: no systemd and no host control socket.",
    )
