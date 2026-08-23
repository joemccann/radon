"""Token-wide IBKR Flex lockout, shared by every Flex SendRequest caller.

IBKR's published Flex v3 table ends at 1021. Code 1025 ("Too many failed
attempts. Please review your configuration.") is undocumented and is a
lockout earned by retrying failed generation (1001) against the same token.
Every further SendRequest extends it. cash-flow-sync, radon-perf-twr, and
POST /performance/background share IB_FLEX_TOKEN, so a per-handler breaker
cannot clear it.

One sidecar, one token. The deadline belongs to the IBKR event, not to the
caller: `record_lockout` is EXTEND-ONLY, so an independent arming path cannot
slide a live 7-day outage into a 14-day one (R-100). The sidecar is written
atomically (R-129) and, when it is gone, rehydrated from the `service_health`
dual-write (R-130) — a deploy that wipes `data/` must not drop a live lockout.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from .atomic_io import atomic_save

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
    """R-129: atomic write so a concurrent reader never sees a truncated file."""
    try:
        SIDECAR.parent.mkdir(parents=True, exist_ok=True)
        atomic_save(SIDECAR, {"next_attempt_at": until, "code": str(code)})
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


def _rehydrate_from_health() -> Optional[datetime]:
    """Sidecar gone: the lockout deadline recorded in `service_health`, or None.

    R-130: the dual-write used to be write-only, so removing the gitignored
    sidecar silently dropped a live 7-day lockout. Fail closed on 1025
    evidence in either writer's row, else fail open.
    """
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


def _stored_deadline() -> Optional[datetime]:
    stored = _read_sidecar()
    if stored is not None:
        return stored
    return _rehydrate_from_health()


def active_until(*, now: Optional[datetime] = None) -> Optional[str]:
    """Live lockout deadline, or None. Consumes a lapsed sidecar."""
    moment = now or datetime.now(timezone.utc)
    stored = _read_sidecar()
    if stored is None:
        stored = _rehydrate_from_health()
        if stored is None:
            return None
        if moment >= stored:
            return None
        _arm_sidecar(_utc_iso(stored))
        return _utc_iso(stored)
    if moment >= stored:
        clear()
        _heartbeat_ok()
        return None
    return _utc_iso(stored)


def is_blocked(*, now: Optional[datetime] = None) -> bool:
    return active_until(now=now) is not None


def raise_if_blocked(*, now: Optional[datetime] = None) -> None:
    until = active_until(now=now)
    if until:
        raise FlexTokenLocked(f"Flex token locked until {until}")


def record_lockout(code: str = "1025", *, now: Optional[datetime] = None) -> str:
    """Arm the sidecar and heartbeat. Returns the ISO deadline in force.

    EXTEND-ONLY (R-100): a live deadline is never moved. It used to be
    rewritten to `now + 7d` on every call, and since a pre-flight
    `FlexTokenLocked` (no HTTP performed) exited with the same code as a
    fresh IBKR 1025, the daemon handler re-armed on it — every independent
    caller doubled a token-wide outage without making one Flex request.
    """
    moment = now or datetime.now(timezone.utc)
    existing = _stored_deadline()
    if existing is not None and existing > moment:
        return _utc_iso(existing)

    until = deadline_for(now=moment)
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


def _heartbeat_ok() -> None:
    """Clear the flex-web-service row once a lockout lapses (R-130).

    `clear()` only unlinked the sidecar, so the banner stayed red for ~24h
    inside the row's 8-day window, quoting a `next_attempt_at` in the past.
    """
    try:
        from db.writer import record_service_health
    except Exception:
        return
    try:
        record_service_health(SERVICE, "ok")
    except Exception:
        return


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
