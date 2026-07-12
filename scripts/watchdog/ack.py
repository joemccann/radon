"""Operator silences for the watchdog.

`radon-watchdog ack <service> [--hours N]` inserts a row with an
absolute expiry timestamp. The check loop reads `is_acked()` and
skips the service silently. `clear` removes the row; `list_active_acks`
powers the CLI `status` command.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from typing import Optional


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _parse_iso(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def _get_db():
    if os.environ.get("PYTEST_CURRENT_TEST"):
        from db.client import get_db
        return get_db()
    from .state_db import get_state_db
    return get_state_db()


def add_ack(*, service: str, hours: int = 4, reason: Optional[str] = None,
            now: Optional[datetime] = None) -> None:
    """Silence ``service`` for ``hours`` from ``now``.

    Idempotent — re-acking replaces the existing window so an operator
    can extend a previous silence without first calling `clear`.
    """
    now = now or datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=hours)
    db = _get_db()
    db.execute(
        """
        INSERT INTO watchdog_acks (service, acked_at, expires_at, reason)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(service) DO UPDATE SET
          acked_at   = excluded.acked_at,
          expires_at = excluded.expires_at,
          reason     = excluded.reason
        """,
        (service, _iso(now), _iso(expires_at), reason),
    )
    db.commit()


def is_acked(*, service: str, now: Optional[datetime] = None) -> bool:
    """True if an unexpired ack row exists for ``service``."""
    now = now or datetime.now(timezone.utc)
    db = _get_db()
    row = db.execute(
        "SELECT expires_at FROM watchdog_acks WHERE service=?",
        (service,),
    ).fetchone()
    if not row:
        return False
    expires = _parse_iso(row[0])
    return now < expires


def clear_ack(*, service: str) -> None:
    """Remove any ack row for ``service``. No-op if none present."""
    db = _get_db()
    db.execute("DELETE FROM watchdog_acks WHERE service=?", (service,))
    db.commit()


def list_active_acks(*, now: Optional[datetime] = None) -> list[dict]:
    """Return every ack whose expiry is still in the future."""
    now = now or datetime.now(timezone.utc)
    db = _get_db()
    rows = db.execute(
        "SELECT service, acked_at, expires_at, reason FROM watchdog_acks"
    ).fetchall()
    out = []
    for row in rows:
        expires = _parse_iso(row[2])
        if now >= expires:
            continue
        out.append({
            "service": row[0],
            "acked_at": row[1],
            "expires_at": row[2],
            "reason": row[3],
        })
    return out
