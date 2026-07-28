"""Hysteresis + cooldown state for the watchdog.

Two distinct concerns share one table:

 * **Hysteresis** — require 2 consecutive failures before firing.
   ``record_failure_and_decide()`` increments the counter; recovery
   resets it via ``record_success()``.
 * **Cooldown** — once an alert fires for (service, severity), suppress
   repeats for 1 hour. ``mark_notified()`` stamps the row;
   ``cooldown_allows_fire()`` checks against the window.

Severity and kind are related but distinct:

 * ``kind`` (``stale`` or ``error``) selects the counter column.
 * ``severity`` (``P1`` / ``P2`` / ``P3``) selects the cooldown
   namespace so a P1 and a P3 against the same service don't share a
   suppression window.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from typing import Optional


COOLDOWN_DURATION = timedelta(hours=1)
HYSTERESIS_THRESHOLD = 2
# A continuously-latched condition re-pages at most once per this ceiling.
# 2026-07-28 storm: with only the 1h window, a 6h latched outage re-armed a
# fresh unacknowledged P1 emergency every ~65 min. One page per
# condition-TRANSITION is the contract; recovery (record_success /
# mark_emergency_resolved) re-arms the next transition immediately past the
# 1h flap floor, and the ceiling keeps a multi-day outage paging once daily.
REARM_CEILING = timedelta(hours=24)


@dataclass
class FailureDecision:
    consecutive_failures: int
    should_fire: bool


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _parse_iso(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def _get_db():
    """Lazy import keeps ``import watchdog`` cheap and lets the test
    fixture monkeypatch ``db.client.get_db`` before this is called.
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        from db.client import get_db
        return get_db()
    from .state_db import get_state_db
    return get_state_db()


def _cooldown_key(severity: str) -> str:
    """Cooldown rows are namespaced by severity rather than kind so a
    P1 stale-alert and a P3 stale-alert don't share a suppression
    window. We reuse the ``kind`` column for this since the table
    already keys on (service, kind).
    """
    return f"severity:{severity}"


def record_failure_and_decide(*, service: str, kind: str, now: Optional[datetime] = None) -> FailureDecision:
    """Increment the consecutive-failures counter and decide whether
    to fire based purely on hysteresis (cooldown check is separate).
    """
    now = now or datetime.now(timezone.utc)
    db = _get_db()
    row = db.execute(
        "SELECT consecutive_failures FROM watchdog_cooldowns WHERE service=? AND kind=?",
        (service, kind),
    ).fetchone()
    current = int(row[0]) if row else 0
    next_count = current + 1
    db.execute(
        """
        INSERT INTO watchdog_cooldowns
          (service, kind, consecutive_failures, last_notified_at, last_outcome)
        VALUES (?, ?, ?, NULL, ?)
        ON CONFLICT(service, kind) DO UPDATE SET
          consecutive_failures = excluded.consecutive_failures,
          last_outcome         = excluded.last_outcome
        """,
        (service, kind, next_count, "failure"),
    )
    db.commit()
    return FailureDecision(
        consecutive_failures=next_count,
        should_fire=next_count >= HYSTERESIS_THRESHOLD,
    )


def record_success(*, service: str, kind: str) -> None:
    """Reset the consecutive-failures counter — single healthy check
    is enough; we don't carry recovery hysteresis on the other side.
    """
    db = _get_db()
    db.execute(
        """
        INSERT INTO watchdog_cooldowns
          (service, kind, consecutive_failures, last_outcome)
        VALUES (?, ?, 0, 'success')
        ON CONFLICT(service, kind) DO UPDATE SET
          consecutive_failures = 0,
          last_outcome         = 'success'
        """,
        (service, kind),
    )
    # Recovery also re-arms this service's notification gates: flipping the
    # severity rows off 'notified' marks the condition-transition boundary
    # that cooldown_allows_fire keys on (one page per transition).
    db.execute(
        "UPDATE watchdog_cooldowns SET last_outcome='resolved' "
        "WHERE service=? AND kind LIKE 'severity:%' AND last_outcome='notified'",
        (service,),
    )
    db.commit()


# A P1 emergency push keeps re-alerting for this long (notify.PUSHOVER_EMERGENCY
# _EXPIRE_SECS). An emergency is only worth cancelling while still inside it.
_EMERGENCY_EXPIRE_S = 3600


def active_emergency_services(*, now: Optional[datetime] = None) -> list[str]:
    """Services with a P1 emergency push that is still in its retry window
    (last_outcome='notified', notified < expire ago). These can be cancelled on
    recovery so they stop re-alerting after the condition clears."""
    now = now or datetime.now(timezone.utc)
    db = _get_db()
    rows = db.execute(
        "SELECT service, last_notified_at FROM watchdog_cooldowns "
        "WHERE kind='severity:P1' AND last_outcome='notified' AND last_notified_at IS NOT NULL"
    ).fetchall()
    active = []
    for service, last_notified in rows:
        try:
            if (now - _parse_iso(last_notified)).total_seconds() < _EMERGENCY_EXPIRE_S:
                active.append(service)
        except Exception:  # noqa: BLE001
            continue
    return active


def mark_emergency_resolved(*, service: str) -> None:
    """Flip a P1 row's outcome so we don't repeatedly cancel an already-cancelled
    emergency on subsequent recovered cycles."""
    db = _get_db()
    db.execute(
        "UPDATE watchdog_cooldowns SET last_outcome='resolved' "
        "WHERE service=? AND kind='severity:P1' AND last_outcome='notified'",
        (service,),
    )
    db.commit()


def cooldown_allows_fire(*, service: str, severity: str, now: Optional[datetime] = None) -> bool:
    """One page per condition-transition. True on first-ever check.

    - Inside the 1h flap floor: always suppressed.
    - Past the floor with a recovery since the last notification
      (last_outcome != 'notified'): allowed — a new transition.
    - Still latched (last_outcome == 'notified', no recovery observed):
      suppressed until the 24h re-arm ceiling.
    """
    now = now or datetime.now(timezone.utc)
    db = _get_db()
    row = db.execute(
        "SELECT last_notified_at, last_outcome FROM watchdog_cooldowns "
        "WHERE service=? AND kind=?",
        (service, _cooldown_key(severity)),
    ).fetchone()
    if not row or not row[0]:
        return True
    last = _parse_iso(row[0])
    elapsed = now - last
    if elapsed < COOLDOWN_DURATION:
        return False
    if row[1] == "notified":
        return elapsed >= REARM_CEILING
    return True


def mark_notified(*, service: str, severity: str, now: Optional[datetime] = None) -> None:
    """Stamp the cooldown row with ``now`` so subsequent checks inside
    the 1h window suppress repeats.
    """
    now = now or datetime.now(timezone.utc)
    db = _get_db()
    db.execute(
        """
        INSERT INTO watchdog_cooldowns
          (service, kind, consecutive_failures, last_notified_at, last_outcome)
        VALUES (?, ?, 0, ?, 'notified')
        ON CONFLICT(service, kind) DO UPDATE SET
          last_notified_at = excluded.last_notified_at,
          last_outcome     = 'notified'
        """,
        (service, _cooldown_key(severity), _iso(now)),
    )
    db.commit()
