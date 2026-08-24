"""Tickets handed from a delivered P1 Pushover to the laptop Grok runner.

The watchdog on Hetzner cannot run Grok. It persists a sanitized page
the moment Pushover accepts the emergency push; the laptop poller
claims that row and launches headless Grok. Best-effort: a Turso miss
must never block or unmute the page itself.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

log = logging.getLogger("watchdog.pages")

SERVICE_RE = re.compile(r"^[A-Za-z0-9_.:/-]{1,64}$")
KIND_RE = re.compile(r"^[A-Za-z0-9_.:/-]{1,32}$")
SEVERITY_RE = re.compile(r"^P[0-4]$")
_NON_PRINTABLE_RE = re.compile(r"[^\x20-\x7e]+")

EXCERPT_OPEN = "<untrusted-excerpt>"
EXCERPT_CLOSE = "</untrusted-excerpt>"
MAX_EXCERPT_CHARS = 240
STALE_CLAIM_SECS = 2 * 3600
MAX_ATTEMPTS = 3
# Returned when the claim count cannot be read: larger than any sane daily
# cap, so an unreadable ledger stands the responder down instead of freeing it.
_UNKNOWN_ACTION_COUNT = 10_000

INSERT_SQL = """
INSERT OR IGNORE INTO watchdog_pages (
  page_id, service, severity, kind, message_excerpt, paged_at, status, attempts
) VALUES (?, ?, ?, ?, ?, ?, 'pending', 0)
"""
CLAIM_SQL = """
UPDATE watchdog_pages
SET status = 'claimed', claimed_at = ?, claim_token = ?
WHERE page_id = ?
  AND (
    status = 'pending'
    OR (status = 'claimed' AND (claimed_at IS NULL OR claimed_at < ?))
  )
"""
LIST_SQL = """
SELECT page_id, service, severity, kind, message_excerpt, paged_at,
       status, claimed_at, attempts
FROM watchdog_pages
WHERE status = 'pending'
   OR (status = 'claimed' AND (claimed_at IS NULL OR claimed_at < ?))
ORDER BY paged_at ASC
LIMIT 3
"""


def _iso(now: datetime) -> str:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_service(service: str) -> str:
    return service if isinstance(service, str) and SERVICE_RE.match(service) else "unknown-service"


def _safe_kind(kind: str) -> str:
    return kind if isinstance(kind, str) and KIND_RE.match(kind) else "unknown"


def sanitize_excerpt(message: str) -> str:
    cleaned = _NON_PRINTABLE_RE.sub(" ", message or "")
    cleaned = cleaned.replace(EXCERPT_OPEN, " ").replace(EXCERPT_CLOSE, " ")
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > MAX_EXCERPT_CHARS:
        cleaned = cleaned[:MAX_EXCERPT_CHARS] + " ...[truncated]"
    return f"{EXCERPT_OPEN}{cleaned}{EXCERPT_CLOSE}"


def page_id(*, service: str, severity: str, kind: str, now: datetime) -> str:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    hour = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H")
    raw = f"{_safe_service(service)}|{severity}|{_safe_kind(kind)}|{hour}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def enqueue_delivered_page(
    *,
    service: str,
    severity: str,
    kind: str,
    message: str,
    now: datetime,
) -> Optional[str]:
    """Persist one ticket after Pushover accepted a P1. Never raises."""
    if severity != "P1":
        return None
    if not isinstance(severity, str) or not SEVERITY_RE.match(severity):
        return None
    try:
        from db.hrana_http import hrana_execute
    except ImportError:
        from scripts.db.hrana_http import hrana_execute

    sid = _safe_service(service)
    kid = _safe_kind(kind)
    pid = page_id(service=sid, severity=severity, kind=kid, now=now)
    try:
        hrana_execute(
            INSERT_SQL,
            (pid, sid, severity, kid, sanitize_excerpt(message), _iso(now)),
        )
    except Exception as exc:  # noqa: BLE001 — paging path stays up
        log.warning("watchdog_pages enqueue failed for %s: %s", sid, exc)
        return None
    return pid


def list_actionable_pages(*, now: datetime) -> list[dict]:
    try:
        from db.hrana_http import hrana_query
    except ImportError:
        from scripts.db.hrana_http import hrana_query

    stale_before = _iso(now - timedelta(seconds=STALE_CLAIM_SECS))
    rows = hrana_query(LIST_SQL, (stale_before,))
    pages = []
    for row in rows:
        pages.append({
            "page_id": row[0],
            "service": row[1],
            "severity": row[2],
            "kind": row[3],
            "message_excerpt": row[4],
            "paged_at": row[5],
            "status": row[6],
            "claimed_at": row[7],
            "attempts": int(row[8] or 0),
        })
    return pages


def actions_since(*, since: datetime) -> int:
    """Tickets claimed since `since` — i.e. how many times Grok was launched.

    Backs the responder's global daily cap. Best-effort like the rest of this
    module, but it fails CLOSED: a Turso miss returns a count that stands the
    responder down rather than one that lets it keep acting unbounded.
    """
    try:
        from db.hrana_http import hrana_query
    except ImportError:
        from scripts.db.hrana_http import hrana_query

    try:
        rows = hrana_query(
            "SELECT COUNT(*) FROM watchdog_pages WHERE claimed_at >= ?",
            (_iso(since),),
        )
    except Exception:  # noqa: BLE001
        log.warning("actions_since unavailable — standing the responder down")
        return _UNKNOWN_ACTION_COUNT
    return int(rows[0][0] or 0) if rows else 0


RERUN_RESULT_PREFIX = "restarted_unit:"


def reruns_since(service: str, *, since: datetime) -> int:
    """Oneshot re-runs already spent on THIS unit since `since`.

    The global daily cap bounds how often the responder acts at all; it does
    not stop one unit failing for an environmental reason (UW quota gone,
    provider 5xx) from consuming the whole day's budget, one re-run per
    unrelated merge to main. Fails CLOSED like `actions_since`.
    """
    try:
        from db.hrana_http import hrana_query
    except ImportError:
        from scripts.db.hrana_http import hrana_query

    try:
        rows = hrana_query(
            "SELECT COUNT(*) FROM watchdog_pages "
            "WHERE service = ? AND finished_at >= ? AND result LIKE ?",
            (service, _iso(since), RERUN_RESULT_PREFIX + "%"),
        )
    except Exception:  # noqa: BLE001
        log.warning("reruns_since unavailable — standing the rerun down")
        return _UNKNOWN_ACTION_COUNT
    return int(rows[0][0] or 0) if rows else 0


def claim_page(page_id: str, *, now: datetime, claim_token: str) -> bool:
    try:
        from db.hrana_http import hrana_execute, hrana_query
    except ImportError:
        from scripts.db.hrana_http import hrana_execute, hrana_query

    stale_before = _iso(now - timedelta(seconds=STALE_CLAIM_SECS))
    hrana_execute(CLAIM_SQL, (_iso(now), claim_token, page_id, stale_before))
    rows = hrana_query(
        "SELECT claim_token FROM watchdog_pages WHERE page_id = ?",
        (page_id,),
    )
    return bool(rows) and rows[0][0] == claim_token


def complete_page(
    page_id: str, *, status: str, result: str, now: datetime
) -> None:
    try:
        from db.hrana_http import hrana_execute
    except ImportError:
        from scripts.db.hrana_http import hrana_execute

    hrana_execute(
        """
        UPDATE watchdog_pages
        SET status = ?, finished_at = ?, result = ?, claim_token = NULL
        WHERE page_id = ?
        """,
        (status, _iso(now), (result or "")[:2000], page_id),
    )


def record_attempt_failure(page_id: str, *, now: datetime, error: str) -> str:
    try:
        from db.hrana_http import hrana_execute, hrana_query
    except ImportError:
        from scripts.db.hrana_http import hrana_execute, hrana_query

    rows = hrana_query(
        "SELECT attempts FROM watchdog_pages WHERE page_id = ?",
        (page_id,),
    )
    attempts = int(rows[0][0] or 0) + 1 if rows else 1
    status = "skipped" if attempts >= MAX_ATTEMPTS else "pending"
    hrana_execute(
        """
        UPDATE watchdog_pages
        SET attempts = ?, status = ?, claim_token = NULL, claimed_at = NULL,
            finished_at = ?, result = ?
        WHERE page_id = ?
        """,
        (attempts, status, _iso(now), (error or "")[:1000], page_id),
    )
    return status
