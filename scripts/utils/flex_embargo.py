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
The row is also the token-wide record across hosts: the laptop and Hetzner
share IB_FLEX_TOKEN and one Turso DB but have separate `data/` trees (T-100).
A DB outage on the read side fails open with a log line.

Row ownership (§1#22): this module writes only the `flex-web-service` row.
`cash-flow-sync` belongs to the Flex ingest and is read here as evidence,
never written. A LOCAL sidecar lapse is not evidence that the SHARED lockout
lapsed: the shared row is flipped to `ok` only when its own deadline has
passed, and only by compare-and-set on the `last_error` that was read.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from .atomic_io import atomic_save

log = logging.getLogger(__name__)

LOCKOUT_CODES = frozenset({"1025"})
# How long an UNKNOWN lockout state blocks. Long enough that a transient Turso
# blip cannot let a caller through, short enough that a genuinely clear token
# is not embargoed for the full 7 days on a store outage. R-212.
UNKNOWN_STATE_BLOCK_HOURS = 1.0
# How long a manual clear() suppresses health-row rehydration. R-213.
CLEAR_SUPPRESSION_HOURS = 24.0
LOCKOUT_DAYS = 7
SERVICE = "flex-web-service"
CASH_FLOW_SERVICE = "cash-flow-sync"
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


def _arm_sidecar(until: str, code: str = "1025") -> bool:
    """R-129: atomic write so a concurrent reader never sees a truncated file.
    True iff the sidecar landed; a read-only data/ is logged, not hidden (T-100)."""
    try:
        SIDECAR.parent.mkdir(parents=True, exist_ok=True)
        atomic_save(SIDECAR, {"next_attempt_at": until, "code": str(code)})
    except OSError as exc:
        log.warning("flex_embargo: sidecar %s not written: %s", SIDECAR, exc)
        return False
    return True


# IBKR's own 1025 wording. A bare "1025" substring is NOT evidence: the
# cash-flow handler writes any failure message into `last_error` with no
# `code` key and re-stamps `last_attempt_finished_at` every failed run, so a
# reference id or an HTTP body containing those four digits used to mint a
# brand-new 7-day deadline that `record_lockout`'s extend-only guard then read
# as the incumbent. R-213.
_LOCKOUT_PHRASES = ("too many failed attempts", "error 1025", "code 1025", "1025:")


def _is_1025_error(parsed: dict[str, Any]) -> bool:
    if is_lockout_code(parsed.get("code")):
        return True
    blob = f"{parsed.get('message') or ''}".lower()
    return any(phrase in blob for phrase in _LOCKOUT_PHRASES)


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


class FlexEmbargoStoreUnavailable(RuntimeError):
    """The durable lockout record could not be read. Not the same as 'no lockout'."""


def _query_health_rows(service: str) -> list[Any]:
    """Bounded SELECT only. hrana_execute is write-only (returns None).

    Raises rather than swallowing: the caller must be able to tell a read
    FAILURE from an empty result. R-212.
    """
    from db.hrana_http import hrana_query

    rows = hrana_query(
        "SELECT state, last_attempt_finished_at, last_error "
        "FROM service_health WHERE service = ?",
        (service,),
    )
    return list(rows) if rows else []


def _durable_store_available() -> bool:
    """True when a durable lockout record could exist to be lost.

    Fail-closed only makes sense against a store that is actually configured.
    When Turso credentials are absent — a laptop without `.env` — the sidecar
    is the ONLY record, so its absence is genuine information and blocking on
    it would embargo Flex for a misconfiguration. R-212.

    FAILING to read the credentials is not the same as their absence. On that
    path a `TURSO_DB_URL` in the environment is the evidence that a durable
    record could exist, and the guard stays CLOSED — reading a transient
    import or env-load failure as "unconfigured" waves every Flex caller into
    a live IBKR 1025 lockout, and each SendRequest extends it.
    """
    try:
        from db.hrana_http import http_url_from_libsql, read_env

        db_url, token = read_env()
    except Exception as exc:
        log.warning("flex_embargo: durable-store probe failed: %s", exc)
        return bool(os.environ.get("TURSO_DB_URL"))
    return bool(http_url_from_libsql(db_url) and token)


def _health_rows(service: str) -> list[Any]:
    try:
        return _query_health_rows(service)
    except Exception as exc:
        log.warning("flex_embargo: service_health read failed: %s", exc)
        if not _durable_store_available():
            # Nothing durable was ever there to lose; the sidecar's absence is
            # the whole answer.
            return []
        raise FlexEmbargoStoreUnavailable(str(exc)) from exc


def _rehydrate_from_health() -> Optional[datetime]:
    """Sidecar gone: the lockout deadline recorded in `service_health`, or None.

    R-130: the dual-write used to be write-only, so removing the gitignored
    sidecar silently dropped a live 7-day lockout. Fail closed on 1025
    evidence in either writer's row, else fail open.

    `clear()` resets only the row this module owns, so `cash-flow-sync`
    evidence stamped at or before the last manual clear is spent. §1#22.
    """
    cleared_at = _cleared_at()
    for service in (SERVICE, CASH_FLOW_SERVICE):
        rows = _health_rows(service)
        if not rows:
            continue
        if service == CASH_FLOW_SERVICE and cleared_at is not None:
            evidence_at = _parse_iso(rows[0][1] if len(rows[0]) > 1 else None)
            if evidence_at is not None and evidence_at <= cleared_at:
                continue
        deadline = _deadline_from_health_row(rows[0])
        if deadline is not None:
            return deadline
    return None


def _stored_deadline() -> Optional[datetime]:
    """Incumbent deadline for the extend-only guard, or None if unknown.

    An unreadable store means "no known incumbent" HERE, which makes
    `record_lockout` arm a fresh deadline — the safe direction for arming.
    `active_until` handles the unknown state separately, because there the
    safe direction is the opposite one. R-212.
    """
    stored = _read_sidecar()
    if stored is not None:
        return stored
    try:
        return _rehydrate_from_health()
    except FlexEmbargoStoreUnavailable:
        return None


def active_until(*, now: Optional[datetime] = None) -> Optional[str]:
    """Live lockout deadline, or None. Consumes a lapsed sidecar."""
    moment = now or datetime.now(timezone.utc)
    stored = _read_sidecar()
    if stored is None:
        if _cleared_marker_covers(moment):
            return None
        try:
            stored = _rehydrate_from_health()
        except FlexEmbargoStoreUnavailable:
            # No sidecar AND no readable durable record. Treating that as
            # "not blocked" sends every Flex caller into a live IBKR lockout,
            # and each such SendRequest extends the lockout at IBKR's end.
            # Fail closed for a bounded window instead. R-212.
            log.warning(
                "flex_embargo: lockout state unknown (no sidecar, store "
                "unreadable) — blocking for %.0fh",
                UNKNOWN_STATE_BLOCK_HOURS,
            )
            return _utc_iso(moment + timedelta(hours=UNKNOWN_STATE_BLOCK_HOURS))
        if stored is None:
            return None
        if moment >= stored:
            return None
        _arm_sidecar(_utc_iso(stored))
        return _utc_iso(stored)
    if moment >= stored:
        # Natural lapse, not a manual escape: unlink WITHOUT the suppression
        # marker, so a genuinely new lockout after this point still arms.
        # The lapse is LOCAL: the shared rows may carry another host's later
        # lockout, so re-read them before reporting the token clear. §1#22.
        _unlink_sidecar()
        try:
            shared = _rehydrate_from_health()
        except FlexEmbargoStoreUnavailable:
            log.warning(
                "flex_embargo: sidecar lapsed but the shared record is unreadable "
                "— blocking for %.0fh",
                UNKNOWN_STATE_BLOCK_HOURS,
            )
            return _utc_iso(moment + timedelta(hours=UNKNOWN_STATE_BLOCK_HOURS))
        if shared is not None and moment < shared:
            _arm_sidecar(_utc_iso(shared))
            return _utc_iso(shared)
        _heartbeat_ok(moment)
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

    Raises :class:`FlexLockoutNotRecorded` when neither sink landed, so no
    caller can report an embargo that exists nowhere (T-100).
    """
    moment = now or datetime.now(timezone.utc)
    existing = _stored_deadline()
    if existing is not None and existing > moment:
        return _utc_iso(existing)

    until = deadline_for(now=moment)
    sidecar_landed = _arm_sidecar(until, code)
    durable_landed = bool(_heartbeat(until, code))
    if not (sidecar_landed or durable_landed):
        raise FlexLockoutNotRecorded(until)
    return until


def _unlink_sidecar() -> None:
    try:
        SIDECAR.unlink(missing_ok=True)
    except OSError:
        pass


def _cleared_path() -> Path:
    return SIDECAR.with_name(SIDECAR.name + ".cleared")


def _cleared_at() -> Optional[datetime]:
    try:
        raw = _cleared_path().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return _parse_iso(raw)


def _cleared_marker_covers(moment: datetime) -> bool:
    """True while a manual clear() still suppresses health-row rehydration.

    Without this, deleting the sidecar was undone by the very next
    `active_until()`: it found no sidecar, rehydrated from the still-`error`
    row and RE-ARMED. The marker lapses on its own so a genuinely new lockout
    is not suppressed forever. R-213.
    """
    cleared_at = _cleared_at()
    if cleared_at is None:
        return False
    return moment < cleared_at + timedelta(hours=CLEAR_SUPPRESSION_HOURS)


def _write_text_atomic(path: Path, text: str) -> None:
    """temp + os.replace: a concurrent reader sees the old marker or the new one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def clear(*, now: Optional[datetime] = None) -> None:
    """Drop a live embargo and stop the health rows re-arming it.

    Resets the `flex-web-service` row (this module's own), so the banner does
    not stay red. `cash-flow-sync` is not ours to write: its pre-clear
    evidence is skipped by `_rehydrate_from_health` instead. §1#22.
    """
    moment = now or datetime.now(timezone.utc)
    _unlink_sidecar()
    try:
        _write_text_atomic(_cleared_path(), _utc_iso(moment))
    except OSError as exc:
        log.warning("flex_embargo: clear marker not written: %s", exc)
    try:
        from db.writer import record_service_health
    except Exception:
        return
    try:
        record_service_health(SERVICE, "ok")
    except Exception as exc:
        log.warning("flex_embargo: clear could not reset %s: %s", SERVICE, exc)


def is_lockout_code(code: Optional[str]) -> bool:
    return str(code or "").strip() in LOCKOUT_CODES


_CLEAR_LAPSED_ROW_SQL = (
    "UPDATE service_health SET state = 'ok', last_error = NULL, updated_at = ? "
    "WHERE service = ? AND state = 'error' AND last_error IS ?"
)


def _heartbeat_ok(moment: datetime) -> None:
    """Clear the flex-web-service row once ITS OWN lockout lapsed (R-130).

    `clear()` only unlinked the sidecar, so the banner stayed red for ~24h
    inside the row's 8-day window, quoting a `next_attempt_at` in the past.

    §1#22: the row is shared across hosts, so a local lapse proves nothing
    about it. Flip it only when the deadline it carries has passed, and only
    if `last_error` still holds what was read (CAS), so a lockout recorded in
    between survives. `cash-flow-sync` is never written here.
    """
    try:
        rows = _query_health_rows(SERVICE)
    except Exception as exc:
        log.warning("flex_embargo: lapse could not read %s: %s", SERVICE, exc)
        return
    if not rows or rows[0][0] != "error":
        return
    deadline = _deadline_from_health_row(rows[0])
    if deadline is not None and moment < deadline:
        return
    try:
        from db.hrana_http import hrana_execute

        hrana_execute(_CLEAR_LAPSED_ROW_SQL, (_utc_iso(datetime.now(timezone.utc)), SERVICE, rows[0][2]))
    except Exception as exc:
        log.warning("flex_embargo: lapsed %s row not cleared: %s", SERVICE, exc)


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


# --- CLI entry point ---------------------------------------------------------
#
# Without this CLI `clear()` had no caller,
# so the only manual escape from a bad embargo was deleting the sidecar — which
# the next `active_until()` silently undid by rehydrating from the still-error
# health row. R-213.


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Flex token 1025 embargo")
    parser.add_argument(
        "command", choices=("status", "clear"),
        help="status: print the live deadline. clear: drop it and reset the rows.",
    )
    args = parser.parse_args(argv)

    if args.command == "status":
        until = active_until()
        print(f"flex embargo active until {until}" if until else "flex embargo: none")
        return 0

    before = active_until()
    clear()
    after = active_until()
    print(f"cleared flex embargo (was {before or 'none'}, now {after or 'none'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
