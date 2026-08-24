"""Token-wide IBKR Flex lockout, shared by every Flex SendRequest caller.

IBKR's published Flex v3 table ends at 1021. Code 1025 ("Too many failed
attempts. Please review your configuration.") is undocumented and is a
lockout earned by retrying failed generation (1001) against the same token.
Every further SendRequest extends it. cash-flow-sync, radon-perf-twr, and
POST /performance/background share IB_FLEX_TOKEN, so a per-handler breaker
cannot clear it.

One sidecar, one token. Dual-write service_health so the watchdog can see
the deadline after a deploy wipes `data/`.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

LOCKOUT_CODES = frozenset({"1025"})
LOCKOUT_DAYS = 7
SERVICE = "flex-web-service"
CASH_FLOW_SERVICE = "cash-flow-sync"
SIDECAR = Path(__file__).resolve().parent.parent.parent / "data" / "flex_token_embargo.json"


class FlexTokenLocked(RuntimeError):
    """The Flex token is under a 1025 lockout. Do not SendRequest."""


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


def _arm_sidecar(until: str, code: str = "1025") -> None:
    try:
        SIDECAR.parent.mkdir(parents=True, exist_ok=True)
        SIDECAR.write_text(json.dumps({
            "next_attempt_at": until,
            "code": str(code),
        }))
    except OSError:
        pass


def _is_1025_error(parsed: dict[str, Any]) -> bool:
    if is_lockout_code(parsed.get("code")):
        return True
    blob = f"{parsed.get('message') or ''} {parsed.get('code') or ''}".lower()
    return "1025" in blob or "too many failed attempts" in blob


def _deadline_from_health_row(row: Any) -> Optional[datetime]:
    if not row or len(row) < 3:
        return None
    last_attempt = _parse_iso(row[1])
    raw = row[2]
    parsed: Optional[dict[str, Any]]
    if isinstance(raw, dict):
        parsed = raw
    else:
        try:
            loaded = json.loads(raw) if raw else None
        except (ValueError, TypeError):
            text = str(raw or "").lower()
            if "1025" not in text and "too many failed attempts" not in text:
                return None
            loaded = {"message": str(raw)}
        parsed = loaded if isinstance(loaded, dict) else None
    if not parsed or not _is_1025_error(parsed):
        return None
    stored_next = _parse_iso(parsed.get("next_attempt_at"))
    candidates = []
    if last_attempt is not None:
        candidates.append(last_attempt + timedelta(days=LOCKOUT_DAYS))
    if stored_next is not None:
        candidates.append(stored_next)
    return max(candidates) if candidates else None


def _health_rows(service: str) -> list[Any]:
    """Bounded SELECT only. hrana_execute is write-only (returns None)."""
    try:
        from db.hrana_http import hrana_query
    except Exception:
        return []
    try:
        rows = hrana_query(
            "SELECT state, last_attempt_finished_at, last_error "
            "FROM service_health WHERE service = ?",
            (service,),
        )
    except Exception:
        return []
    return list(rows) if rows else []


def _reconstruct_from_turso() -> Optional[datetime]:
    """Sidecar gone. Fail closed on 1025 evidence, else fail open."""
    try:
        for service in (SERVICE, CASH_FLOW_SERVICE):
            rows = _health_rows(service)
            if not rows:
                continue
            deadline = _deadline_from_health_row(rows[0])
            if deadline is not None:
                return deadline
    except Exception:
        return None
    return None


def active_until(*, now: Optional[datetime] = None) -> Optional[str]:
    """Live lockout deadline, or None. Consumes a lapsed sidecar."""
    moment = now or datetime.now(timezone.utc)
    stored = _read_sidecar()
    if stored is None:
        stored = _reconstruct_from_turso()
        if stored is None:
            return None
        if moment >= stored:
            return None
        _arm_sidecar(_utc_iso(stored))
        return _utc_iso(stored)
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
    """Arm the sidecar and heartbeat. Returns the ISO deadline."""
    until = deadline_for(now=now)
    _arm_sidecar(until, code)
    _heartbeat(until, code)
    return until


def clear() -> None:
    try:
        SIDECAR.unlink(missing_ok=True)
    except OSError:
        pass


def is_lockout_code(code: Optional[str]) -> bool:
    return str(code or "").strip() in LOCKOUT_CODES


def _heartbeat(until: str, code: str) -> None:
    try:
        from db.writer import record_service_health
    except Exception:
        return
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
    except Exception:
        return
