"""Core check logic — parametric over service list.

`check_service(service, kind, now, market_state)` returns a
`CheckOutcome` capturing whether the service is healthy / stale /
errored / acked, whether hysteresis tripped this cycle, and the
severity used for cooldown bookkeeping.

`check_bucket(bucket, now)` is the per-timer entry point — looks up
the bucket's service list and runs the right kind of check on each,
gating the intraday bucket to market hours.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from . import ack as ack_mod
from . import cooldown as cooldown_mod
from . import services as services_mod


# ── outcomes ────────────────────────────────────────────────────────

@dataclass
class CheckOutcome:
    service: str
    kind: str                     # 'stale' | 'error'
    status: str                   # 'healthy' | 'stale' | 'error' | 'acked'
    severity: Optional[str]       # 'P1' | 'P2' | 'P3' | None when healthy
    fired: bool
    message: str
    consecutive_failures: int
    now: datetime


@dataclass
class BucketReport:
    bucket: str
    ran: bool
    outcomes: list[CheckOutcome] = field(default_factory=list)


# ── helpers ─────────────────────────────────────────────────────────

def _get_db():
    from db.client import get_db
    return get_db()


def _parse_iso(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def _read_service_health(service: str) -> Optional[dict]:
    db = _get_db()
    row = db.execute(
        """
        SELECT service, state, last_attempt_started_at, last_attempt_finished_at,
               last_error, updated_at
        FROM service_health WHERE service=?
        """,
        (service,),
    ).fetchone()
    if not row:
        return None
    return {
        "service": row[0],
        "state": row[1],
        "last_attempt_started_at": row[2],
        "last_attempt_finished_at": row[3],
        "last_error": row[4],
        "updated_at": row[5],
    }


def _format_age(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


# ── severity policy ─────────────────────────────────────────────────

def _resolve_severity(*, service: str, kind: str, market_state: str) -> str:
    """Severity rules from the spec:

      P1 — market-hours intraday silent     → vcg, cri, orders, portfolio while open
      P2 — any scheduled service in error
      P3 — continuous service stale, or any intraday stale off-hours
    """
    if kind == "error":
        return "P2"
    if service in services_mod.BUCKETS["intraday"] and market_state == "open":
        return "P1"
    return "P3"


# ── market gate ─────────────────────────────────────────────────────

def _market_state_for(now: datetime) -> str:
    """Mirror of getMarketStateFromDate() in web/lib/serviceHealthWindows.ts.
    UTC datetime in, ET-derived 'open' | 'extended' | 'closed' out.
    """
    # Convert to ET. UTC offset for America/New_York is -5 (EST) / -4 (EDT).
    # zoneinfo handles DST.
    try:
        from zoneinfo import ZoneInfo
        et = now.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        et = now  # fallback — should never happen on a tzdata-equipped host
    day = et.weekday()  # 0=Mon, 6=Sun
    if day >= 5:
        return "closed"
    minutes = et.hour * 60 + et.minute
    if 9 * 60 + 30 <= minutes <= 16 * 60:
        return "open"
    if (4 * 60 <= minutes < 9 * 60 + 30) or (16 * 60 < minutes <= 20 * 60):
        return "extended"
    return "closed"


def _intraday_timer_window_active(now: datetime) -> bool:
    """The intraday bucket only fires alerts during ET trading hours.
    Allow ``open`` and ``extended`` so a stale-during-the-9:30-bell
    snapshot still gets flagged at 9:35.
    """
    return _market_state_for(now) in {"open", "extended"}


def _seconds_since_open(now: datetime) -> int:
    """Seconds since today's 9:30 ET opening bell, clamped to >= 0.

    Used to grace a RTH-only scanner at the open: it cannot be 'stale during
    the open' before the market has been open long enough for it to run its
    first session cycle. Returns a huge value if tz data is unavailable so the
    caller falls back to the raw wall-clock age (no grace) rather than masking.
    """
    try:
        from zoneinfo import ZoneInfo
        et = now.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        return 1 << 30
    open_et = et.replace(hour=9, minute=30, second=0, microsecond=0)
    return max(0, int((et - open_et).total_seconds()))


# ── core check ──────────────────────────────────────────────────────

def check_service(*, service: str, kind: str, now: datetime, market_state: str) -> CheckOutcome:
    """Run a single check.

    ``kind`` selects what we're watching for:
      - ``stale`` → ok-but-past-window
      - ``error`` → state == 'error'

    Hysteresis (2 consecutive failures) gates ``fired``.
    """
    if ack_mod.is_acked(service=service, now=now):
        return CheckOutcome(
            service=service,
            kind=kind,
            status="acked",
            severity=None,
            fired=False,
            message=f"ack active",
            consecutive_failures=0,
            now=now,
        )

    health = _read_service_health(service)

    if kind == "error":
        return _check_error(service=service, health=health, now=now, market_state=market_state)
    return _check_stale(service=service, health=health, now=now, market_state=market_state)


def _check_error(*, service: str, health: Optional[dict], now: datetime, market_state: str) -> CheckOutcome:
    """`error` bucket — fire iff the row's state is 'error'."""
    if not health or health.get("state") != "error":
        cooldown_mod.record_success(service=service, kind="error")
        return CheckOutcome(
            service=service,
            kind="error",
            status="healthy",
            severity=None,
            fired=False,
            message="state ok",
            consecutive_failures=0,
            now=now,
        )

    err_blob = health.get("last_error") or "{}"
    try:
        err = json.loads(err_blob) if isinstance(err_blob, str) else err_blob
    except json.JSONDecodeError:
        err = {}
    err_msg = err.get("message") if isinstance(err, dict) else None
    decision = cooldown_mod.record_failure_and_decide(service=service, kind="error", now=now)
    severity = _resolve_severity(service=service, kind="error", market_state=market_state)
    msg = f"in error state: {err_msg or 'unknown'}"
    return CheckOutcome(
        service=service,
        kind="error",
        status="error",
        severity=severity,
        fired=decision.should_fire,
        message=msg,
        consecutive_failures=decision.consecutive_failures,
        now=now,
    )


def _check_stale(*, service: str, health: Optional[dict], now: datetime, market_state: str) -> CheckOutcome:
    """`stale` bucket — fire iff the row exists and is past its window.

    A service with NO row has never been activated (not deployed or deliberately
    dormant). That is an operator-known state, NOT an incident. Suppress it
    rather than paging — this stops llm-token-index and preset-rebalance (and
    any future never-activated services) from flooding on-call.

    Edge case: a newly-deployed writer that is broken and never writes its first
    row is also suppressed until it writes once. That window is acceptable — the
    deployment itself is verifiable, and the error bucket (which already returns
    healthy for no-row services) catches state=error rows once the writer starts.
    The alternative — paging on every never-seen service — was causing the live
    flood this guard fixes.
    """
    if not health:
        # No row ever written → dormant / not yet activated. Not an incident.
        cooldown_mod.record_success(service=service, kind="stale")
        return CheckOutcome(
            service=service,
            kind="stale",
            status="dormant",
            severity=None,
            fired=False,
            message="no service_health row — not yet activated",
            consecutive_failures=0,
            now=now,
        )

    window_s = services_mod.freshness_window_for(service, market_state)

    is_stale = False
    age_s = None
    if not health.get("updated_at"):
        is_stale = True
    else:
        try:
            updated = _parse_iso(health["updated_at"])
            age_s = int((now - updated).total_seconds())
            # Open-bell grace for RTH-only intraday scanners (cri-scan, vcg-scan,
            # …): at 9:30 their wall-clock age spans the overnight/weekend gap
            # when they legitimately don't run, so the tight open window would
            # fire a false "silent for 2d — market open". Cap the effective age
            # at how long the market has been open today — one window of grace to
            # produce the first session write. A genuinely silent scanner still
            # trips once the session has been open longer than its window.
            effective_age = age_s
            if market_state == "open" and service in services_mod.BUCKETS["intraday"]:
                effective_age = min(age_s, _seconds_since_open(now))
            is_stale = effective_age > window_s
        except Exception:
            is_stale = True

    if not is_stale:
        cooldown_mod.record_success(service=service, kind="stale")
        return CheckOutcome(
            service=service,
            kind="stale",
            status="healthy",
            severity=None,
            fired=False,
            message="fresh",
            consecutive_failures=0,
            now=now,
        )

    decision = cooldown_mod.record_failure_and_decide(service=service, kind="stale", now=now)
    severity = _resolve_severity(service=service, kind="stale", market_state=market_state)
    age_fmt = _format_age(age_s) if age_s is not None else "no data"
    window_fmt = _format_age(window_s)
    market_label = market_state
    msg = f"silent for {age_fmt} (window {window_fmt}) — market {market_label}"
    return CheckOutcome(
        service=service,
        kind="stale",
        status="stale",
        severity=severity,
        fired=decision.should_fire,
        message=msg,
        consecutive_failures=decision.consecutive_failures,
        now=now,
    )


# ── bucket runner ───────────────────────────────────────────────────

def check_bucket(*, bucket: str, now: Optional[datetime] = None) -> BucketReport:
    """Run every check for a bucket. Intraday is no-op outside ET
    trading hours so a stray manual run doesn't fire P1 alerts off-hours.
    """
    now = now or datetime.now(timezone.utc)

    if bucket == "intraday" and not _intraday_timer_window_active(now):
        return BucketReport(bucket=bucket, ran=False)

    kind = "error" if bucket == "error" else "stale"
    market_state = _market_state_for(now)

    if bucket not in services_mod.BUCKETS:
        raise ValueError(f"unknown bucket: {bucket}")

    outcomes = []
    for service in services_mod.BUCKETS[bucket]:
        outcomes.append(
            check_service(service=service, kind=kind, now=now, market_state=market_state)
        )
    return BucketReport(bucket=bucket, ran=True, outcomes=outcomes)
