"""Turso ledger for the MarketDesk ingest (migration 0102): claim, failure cap, Dropbox upload outbox.

Attempt accounting: ``claim`` writes a lease (``last_error = LEASE``) before the slow work. A
finished attempt replaces it (``record_published`` / ``record_failure`` / ``park``); a run killed
mid-article (systemd TimeoutStartSec) leaves it, and the next ``claim`` counts that attempt.
``record_failure`` spends one attempt; ``park`` (provider outage, expired session, HTTP 5xx) does not.

Every statement goes through ``api.db_http.hrana_execute`` (bounded HTTP, direct to Turso).
Nothing durable is host-local. Refs are duck-typed ``ArticleRef`` (article_id, url,
published_at, title); upload results are duck-typed ``UploadResult``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from api.db_http import hrana_execute

MAX_ATTEMPTS = 5
LEASE = "attempt_in_progress"
INTERRUPTED = "interrupted: the previous run was killed mid-article"
ERROR_MAX_CHARS = 300
DROPBOX_STATUSES = frozenset({"uploaded", "skipped_no_creds", "failed"})
_IN_CHUNK = 200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _error(value) -> str:
    return str(value or "")[:ERROR_MAX_CHARS]


def unseen(refs, *, lookback_hours: int, now: datetime | None = None) -> list:
    """Refs inside the lookback window that are not published or failed out, deduped, order kept."""
    cutoff = _utc(now or datetime.now(timezone.utc)) - timedelta(hours=lookback_hours)
    candidates, seen = [], set()
    for ref in refs:
        if ref.article_id in seen or _utc(ref.published_at) < cutoff:
            continue
        seen.add(ref.article_id)
        candidates.append(ref)
    done: set[str] = set()
    for start in range(0, len(candidates), _IN_CHUNK):
        ids = [ref.article_id for ref in candidates[start:start + _IN_CHUNK]]
        rows = hrana_execute(
            "SELECT article_id FROM marketdesk_articles WHERE status IN ('published','failed') "
            f"AND article_id IN ({','.join('?' * len(ids))})", ids)
        done.update(row[0] for row in rows)
    return [ref for ref in candidates if ref.article_id not in done]


def claim(ref) -> bool:
    """Insert the article if new (never overwrites), count an interrupted previous attempt, then lease it.

    True when it should be processed now. A pending row whose attempts are spent becomes 'failed'.
    """
    now = _now()
    hrana_execute(
        "INSERT INTO marketdesk_articles (article_id,title,article_url,published_at,first_seen_at,updated_at) "
        "VALUES (?,?,?,?,?,?) ON CONFLICT(article_id) DO NOTHING",
        (ref.article_id, str(ref.title or "")[:500], ref.url, _utc(ref.published_at).isoformat(), now, now))
    hrana_execute(
        "UPDATE marketdesk_articles SET attempts=attempts+1, last_error=?, updated_at=? "
        "WHERE article_id=? AND status='pending' AND last_error=?",
        (INTERRUPTED, now, ref.article_id, LEASE))
    hrana_execute(
        "UPDATE marketdesk_articles SET status='failed', updated_at=? "
        "WHERE article_id=? AND status='pending' AND attempts>=?",
        (now, ref.article_id, MAX_ATTEMPTS))
    rows = hrana_execute("SELECT status, attempts FROM marketdesk_articles WHERE article_id=?", (ref.article_id,))
    if not rows or rows[0][0] != "pending" or int(rows[0][1]) >= MAX_ATTEMPTS:
        return False
    hrana_execute("UPDATE marketdesk_articles SET last_error=?, updated_at=? WHERE article_id=? AND status='pending'",
                  (LEASE, now, ref.article_id))
    return True


def newest_known_published_at() -> datetime | None:
    """Publication time of the newest article the ledger has seen (listing-gap detection)."""
    rows = hrana_execute("SELECT MAX(published_at) FROM marketdesk_articles")
    if not rows or not rows[0][0]:
        return None
    return _utc(datetime.fromisoformat(str(rows[0][0]).replace("Z", "+00:00")))


def post_source(post_id: str) -> dict | None:
    """Provenance already committed for a post (crash heal between publish and ledger write)."""
    rows = hrana_execute("SELECT provenance_json FROM research_post_sources WHERE post_id=? LIMIT 1", (post_id,))
    if not rows:
        return None
    try:
        value = json.loads(rows[0][0] or "{}")
    except (TypeError, ValueError):
        value = {}
    return value if isinstance(value, dict) else {}


def post_exists(post_id: str) -> bool:
    return post_source(post_id) is not None


def record_published(article_id: str, post_id: str, pdf_asset: str, pdf_sha256: str) -> None:
    hrana_execute(
        "UPDATE marketdesk_articles SET status='published', post_id=?, pdf_asset=?, pdf_sha256=?, "
        "last_error=NULL, updated_at=? WHERE article_id=?",
        (post_id, pdf_asset, pdf_sha256, _now(), article_id))


def record_failure(article_id: str, error: str) -> int:
    """Count one failed attempt on a pending row; it becomes 'failed' (never retried) at MAX_ATTEMPTS."""
    rows = hrana_execute(
        "UPDATE marketdesk_articles SET attempts=attempts+1, last_error=?, updated_at=?, "
        f"status=CASE WHEN attempts+1>={MAX_ATTEMPTS} THEN 'failed' ELSE status END "
        "WHERE article_id=? AND status='pending' RETURNING attempts",
        (_error(error), _now(), article_id))
    if rows:
        return int(rows[0][0])
    current = hrana_execute("SELECT attempts FROM marketdesk_articles WHERE article_id=?", (article_id,))
    return int(current[0][0]) if current else 0


def park(article_id: str, error: str) -> None:
    """Record a transient failure (outage, expired session, HTTP 5xx) without spending an attempt."""
    hrana_execute(
        "UPDATE marketdesk_articles SET last_error=?, updated_at=? WHERE article_id=? AND status='pending'",
        (_error(error) or "parked", _now(), article_id))


_UPLOAD_COLUMNS = ("article_id", "post_id", "title", "published_at", "pdf_asset", "pdf_sha256", "dropbox_status")


def pending_uploads(limit: int = 10) -> list[dict]:
    """Published rows not yet in Dropbox. Never-tried and skipped rows first, then failed retries, oldest first."""
    rows = hrana_execute(
        f"SELECT {','.join(_UPLOAD_COLUMNS)} FROM marketdesk_articles "
        "WHERE status='published' AND (dropbox_status IS NULL OR dropbox_status IN ('skipped_no_creds','failed')) "
        "ORDER BY CASE WHEN dropbox_status='failed' THEN 1 ELSE 0 END, published_at, article_id LIMIT ?",
        (int(limit),))
    return [dict(zip(_UPLOAD_COLUMNS, row)) for row in rows]


def record_dropbox(article_id: str, status: str, result=None, error: str | None = None) -> None:
    if status not in DROPBOX_STATUSES:
        raise ValueError("unknown Dropbox upload status")
    if status == "uploaded" and result is None:
        raise ValueError("uploaded status requires an upload result")
    now = _now()
    if result is not None:
        hrana_execute(
            "UPDATE marketdesk_articles SET dropbox_status=?, dropbox_path=?, dropbox_id=?, dropbox_rev=?, "
            "dropbox_content_hash=?, last_error=NULL, updated_at=? WHERE article_id=?",
            (status, result.path_lower, result.id, result.rev, result.content_hash, now, article_id))
    elif status == "failed":
        hrana_execute("UPDATE marketdesk_articles SET dropbox_status=?, last_error=?, updated_at=? WHERE article_id=?",
                      (status, _error(error) or "upload failed", now, article_id))
    else:
        hrana_execute("UPDATE marketdesk_articles SET dropbox_status=?, updated_at=? WHERE article_id=?",
                      (status, now, article_id))
