"""Writer-contract chokepoint for STANDALONE service_health writers (DUR-14).

The three writer contracts — heartbeat on EVERY cycle including no-change
short-circuits (feedback_service_health_heartbeat), row state from THIS
writer's own outcome only (feedback_service_health_writer_state_not_event_content),
and raise-with-embargo instead of latching on retryable failures
(feedback_dont_latch_last_run_on_soft_failure) — were conventions, and each
was violated by a sibling writer within days of being documented. This
context manager makes them structural::

    from db.service_cycle import service_cycle

    with service_cycle("orders-sync", market_hours_class="intraday") as cycle:
        cycle.finished_at = data["last_sync"]
        replace_open_orders_for_session(rows)   # raises → error row + re-raise

Any clean exit (including an early ``return`` no-change short-circuit)
heartbeats ``ok``. Any exception writes ``error`` with a ~5-minute retry
embargo (``next_attempt_at``) and RE-RAISES, so a daemon/timer caller never
latches its slot on a transient failure. There is deliberately no API to
set the row state by hand — state derives only from how the block exits.

Division of labor vs ``db.scan_mirror``
=======================================

``scan_mirror.mirror_scan_snapshot`` stays the single chokepoint for
MIRROR-FED scans (vcg-scan, scanner, discover, flow-analysis, performance,
oi-changes, leap-scan, garch-scan): it owns the snapshot upsert AND the
heartbeat together and NEVER raises, because a failed Turso mirror must not
crash the scan that produced the data (the JSON cache stays authoritative).

``service_cycle`` is for STANDALONE writers that own their snapshot writes
inline (cri_scan, gex_scan, ib_sync, ib_orders, cta_sync_service,
llm_token_index, gamma_rotation_gap, fetch_analyst_ratings). It re-raises
by design; callers that want best-effort dual-write semantics wrap the
``with`` block in their own try/except (the error row has already been
written by the time the exception escapes).

Monitor-daemon handlers use neither — ``BaseHandler.run()`` carries the
structural heartbeat for them (``service_name`` class attribute).
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator, Optional

#: Mirrors ``monitor_daemon.handlers._throttle_backoff.SOFT_RETRY_COOLDOWN_SECS``
#: — short enough that a transient blip recovers within the same window,
#: long enough that a timer-driven retry doesn't hammer the upstream.
SOFT_RETRY_EMBARGO_SECS = 5 * 60

#: Cadence classes a writer may declare. Must agree with the service's
#: category/bucket in web/lib/serviceHealthWindows.ts + scripts/watchdog/services.py
#: (the registration-completeness CI test pins the NAME; the class is
#: declared-intent documentation at the call site).
MARKET_HOURS_CLASSES = frozenset({"intraday", "continuous", "daily", "on-demand"})


class ServiceCycle:
    """Per-cycle handle. The only caller-writable knob is ``finished_at``
    (the data timestamp for COALESCE-preserving upserts) — state is not
    settable, by design."""

    __slots__ = ("service", "market_hours_class", "started_at", "finished_at")

    def __init__(self, service: str, market_hours_class: str, started_at: str) -> None:
        self.service = service
        self.market_hours_class = market_hours_class
        self.started_at = started_at
        self.finished_at: Optional[str] = None


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _resolve_writer():
    """Resolve ``db.writer`` at call time (sys.modules-aware so test stubs
    and the flat ``sys.path.insert(scripts/)`` import style both work).
    Returns None on hosts without the DB layer."""
    try:
        from db import writer as flat_writer  # noqa: PLC0415

        return sys.modules.get("db.writer", flat_writer)
    except ImportError:
        try:
            from . import writer as pkg_writer  # noqa: PLC0415

            return pkg_writer
        except ImportError:  # pragma: no cover — stripped envs
            return None


def _record(service: str, state: str, *, started_at: str,
            finished_at: Optional[str], error: Optional[dict] = None) -> None:
    """Best-effort heartbeat — never raises past this function."""
    try:
        writer = _resolve_writer()
        if writer is None:
            return
        ensure = getattr(writer, "ensure_no_replica_for_writers", None)
        if ensure is not None:
            ensure()
        writer.record_service_health(
            service, state,
            started_at=started_at, finished_at=finished_at, error=error,
        )
    except Exception as exc:  # noqa: BLE001 — telemetry must not mask the cycle outcome
        print(f"[{service}] service_health heartbeat failed: {exc}", file=sys.stderr)


def _error_payload(exc: BaseException) -> dict:
    next_attempt = _now_utc() + timedelta(seconds=SOFT_RETRY_EMBARGO_SECS)
    return {"message": str(exc), "next_attempt_at": _iso(next_attempt)}


@contextmanager
def service_cycle(service: str, *, market_hours_class: str) -> Iterator[ServiceCycle]:
    """Wrap one writer cycle. ok on any clean exit, error + ~5-min embargo
    + re-raise on any exception. See module docstring for the contract."""
    if market_hours_class not in MARKET_HOURS_CLASSES:
        raise ValueError(
            f"unknown market_hours_class {market_hours_class!r} "
            f"(expected one of {sorted(MARKET_HOURS_CLASSES)})"
        )
    cycle = ServiceCycle(service, market_hours_class, _iso(_now_utc()))
    try:
        yield cycle
    except Exception as exc:
        _record(
            service, "error",
            started_at=cycle.started_at, finished_at=cycle.finished_at,
            error=_error_payload(exc),
        )
        raise
    _record(service, "ok", started_at=cycle.started_at, finished_at=cycle.finished_at)


def record_failed_cycle(service: str, exc: BaseException,
                        finished_at: Optional[str] = None) -> None:
    """Error row + embargo for failures detected OUTSIDE a with-block
    (e.g. a main() that catches the whole scan). Best-effort, never raises."""
    now = _iso(_now_utc())
    _record(
        service, "error",
        started_at=now, finished_at=finished_at,
        error=_error_payload(exc),
    )
