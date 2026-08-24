"""Token-wide IBKR Flex lockout, shared by every Flex SendRequest caller.

IBKR's published Flex v3 table ends at 1021. Code 1025 ("Too many failed
attempts. Please review your configuration.") is undocumented and is a
lockout earned by retrying failed generation (1001) against the same token.
Every further SendRequest extends it. cash-flow-sync, radon-perf-twr, and
POST /performance/background share IB_FLEX_TOKEN, so a per-handler breaker
cannot clear it.

One token, one durable record. The `service_health` row
(`flex-web-service`) is the token-wide sink: the laptop and Hetzner share
IB_FLEX_TOKEN and one Turso DB but have separate `data/` trees, so the
sidecar alone is a per-host breaker. Dual-write both; read the sidecar
first and fall back to the row (rehydrating the sidecar) when the file is
missing. A DB outage on the read side fails open with a log line.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

LOCKOUT_CODES = frozenset({"1025"})
LOCKOUT_DAYS = 7
SERVICE = "flex-web-service"
SIDECAR = Path(__file__).resolve().parent.parent.parent / "data" / "flex_token_embargo.json"


class FlexTokenLocked(RuntimeError):
    """The Flex token is under a 1025 lockout. Do not SendRequest."""


class FlexLockoutNotRecorded(RuntimeError):
    """Neither the sidecar nor the service_health row accepted the lockout.
    The deadline is the message so callers can still surface it."""


def _utc_iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def deadline_for(*, now: Optional[datetime] = None) -> str:
    moment = now or datetime.now(timezone.utc)
    return _utc_iso(moment + timedelta(days=LOCKOUT_DAYS))


def _read_sidecar() -> Optional[datetime]:
    try:
        raw = json.loads(SIDECAR.read_text()).get("next_attempt_at")
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    return _parse_iso(raw)


def _write_sidecar(until: str, code: str) -> bool:
    try:
        SIDECAR.parent.mkdir(parents=True, exist_ok=True)
        SIDECAR.write_text(json.dumps({
            "next_attempt_at": until,
            "code": str(code),
        }))
    except OSError as exc:
        log.warning("flex_embargo: sidecar %s not written: %s", SIDECAR, exc)
        return False
    return True


def _read_service_health() -> Optional[dict[str, Any]]:
    """The durable lockout payload (`last_error` JSON), or None. Never
    raises: a Turso outage fails open here and is logged."""
    try:
        from db.hrana_http import read_service_health_http
        row = read_service_health_http(SERVICE)
    except Exception as exc:
        log.warning("flex_embargo: service_health read failed, fail-open: %s", exc)
        return None
    if not row or not row.get("last_error"):
        return None
    try:
        payload = json.loads(row["last_error"])
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _rehydrate_from_service_health(moment: datetime) -> Optional[datetime]:
    payload = _read_service_health()
    if not payload:
        return None
    stored = _parse_iso(payload.get("next_attempt_at"))
    if stored is None or moment >= stored:
        return None
    _write_sidecar(_utc_iso(stored), str(payload.get("code") or "1025"))
    return stored


def active_until(*, now: Optional[datetime] = None) -> Optional[str]:
    """Live lockout deadline, or None. Consumes a lapsed sidecar; with no
    sidecar, falls back to the token-wide service_health row."""
    moment = now or datetime.now(timezone.utc)
    stored = _read_sidecar()
    if stored is None:
        stored = _rehydrate_from_service_health(moment)
    if stored is None:
        return None
    if moment >= stored:
        clear()
        return None
    return _utc_iso(stored)


def is_blocked(*, now: Optional[datetime] = None) -> bool:
    return active_until(now=now) is not None


def raise_if_blocked(*, now: Optional[datetime] = None) -> None:
    until = active_until(now=now)
    if until:
        raise FlexTokenLocked(f"Flex token locked until {until}")


def record_lockout(code: str = "1025", *, now: Optional[datetime] = None) -> str:
    """Arm the sidecar and the service_health row. Returns the ISO deadline.

    Raises :class:`FlexLockoutNotRecorded` when neither sink landed, so no
    caller can report an embargo that exists nowhere.
    """
    until = deadline_for(now=now)
    sidecar_landed = _write_sidecar(until, code)
    durable_landed = bool(_heartbeat(until, code))
    if not (sidecar_landed or durable_landed):
        raise FlexLockoutNotRecorded(until)
    return until


def clear() -> None:
    try:
        SIDECAR.unlink(missing_ok=True)
    except OSError:
        pass


def is_lockout_code(code: Optional[str]) -> bool:
    return str(code or "").strip() in LOCKOUT_CODES


def _heartbeat(until: str, code: str) -> bool:
    """Dual-write the lockout to service_health. True iff the row landed."""
    try:
        from db.writer import record_service_health
    except Exception as exc:
        log.warning("flex_embargo: db.writer unavailable: %s", exc)
        return False
    try:
        record_service_health(
            SERVICE,
            "error",
            error={
                "message": f"Flex lockout (code {code}). Do not retry.",
                "class": "lockout",
                "code": str(code),
                "next_attempt_at": until,
            },
        )
    except Exception as exc:
        log.warning("flex_embargo: service_health write failed: %s", exc)
        return False
    return True
