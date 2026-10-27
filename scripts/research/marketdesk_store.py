"""Turso ledger for the MarketDesk ingest (migration 0102): claim, failure cap, relevance decisions,
operator re-queue, Dropbox upload outbox.

Attempt accounting: ``claim`` writes a lease (``last_error = LEASE``) before the slow work. A
finished attempt replaces it (``record_published`` / ``record_failure`` / ``park``); a run killed
mid-article (systemd TimeoutStartSec) leaves it, and the next ``claim`` counts that attempt.
``record_failure`` spends one attempt; ``park`` (provider outage, expired session, HTTP 5xx) does not.

Every statement goes through ``api.db_http.hrana_execute`` (bounded HTTP, direct to Turso).
Nothing durable is host-local. Refs are duck-typed ``ArticleRef`` (article_id, url,
published_at, title); upload results are duck-typed ``UploadResult``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json

from api.db_http import hrana_execute

MAX_ATTEMPTS = 5
LEASE = "attempt_in_progress"
INTERRUPTED = "interrupted: the previous run was killed mid-article"
ERROR_MAX_CHARS = 300
DROPBOX_STATUSES = frozenset({"uploaded", "skipped_no_creds", "failed"})
DECISION_STATUSES = frozenset({"held", "dropped"})
WORK_PREFIX = "marketdesk:"
# Terminal for the listing: a held or dropped article comes back only through an operator re-queue.
_DONE = "('published','failed','held','dropped')"
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
            f"SELECT article_id FROM marketdesk_articles WHERE status IN {_DONE} "
            f"AND article_id IN ({','.join('?' * len(ids))})", ids)
        done.update(row[0] for row in rows)
    return [ref for ref in candidates if ref.article_id not in done]


def discover(refs) -> None:
    """Persist every discovered article before slow work; never acquire a processing lease here."""
    now = _now()
    for ref in refs:
        hrana_execute(
            "INSERT INTO marketdesk_articles (article_id,title,article_url,published_at,first_seen_at,updated_at) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(article_id) DO NOTHING",
            (ref.article_id, str(ref.title or "")[:500], ref.url, _utc(ref.published_at).isoformat(), now, now))


def pending(limit: int = 100) -> list:
    """Replay durable pending work outside the site's latest-25 window, with fair transient retries."""
    rows = hrana_execute(
        "SELECT article_id,article_url,published_at,title,note FROM marketdesk_articles "
        "WHERE status='pending' AND attempts<? "
        "ORDER BY CASE WHEN note IS NOT NULL THEN 0 ELSE 1 END,updated_at,published_at,article_id LIMIT ?",
        (MAX_ATTEMPTS, max(1, min(int(limit), 500))))
    return [RequeuedRef(r[0], r[1], _utc(datetime.fromisoformat(str(r[2]).replace("Z", "+00:00"))), r[3] or "", r[4] or "")
            for r in rows]


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


def _hex(fingerprint) -> str | None:
    return f"{int(fingerprint):016x}" if isinstance(fingerprint, int) and fingerprint else None


def _folders_json(folders) -> str | None:
    if not isinstance(folders, (list, tuple)):
        return None
    return json.dumps([str(f) for f in folders if isinstance(f, str) and f.strip()][:12])


def record_published(article_id: str, post_id: str, pdf_asset: str | None, pdf_sha256: str | None, *, fingerprint=None,
                     publisher: str | None = None, folders=None, file_name: str | None = None) -> None:
    hrana_execute(
        "UPDATE marketdesk_articles SET status='published', post_id=?, pdf_asset=COALESCE(?,pdf_asset), pdf_sha256=COALESCE(?,pdf_sha256), "
        "fingerprint=COALESCE(?,fingerprint), publisher=COALESCE(?,publisher), folders_json=COALESCE(?,folders_json), "
        "file_name=COALESCE(?,file_name), reason_code=NULL, note=NULL, last_error=NULL, updated_at=? WHERE article_id=?",
        (post_id, pdf_asset, pdf_sha256, _hex(fingerprint), publisher, _folders_json(folders),
         (str(file_name)[:300] if file_name else None), _now(), article_id))


def record_decision(article_id: str, status: str, reason_code: str | None, *, pdf_asset: str | None, pdf_sha256: str | None,
                    fingerprint, publisher: str | None, folders, file_name: str | None) -> None:
    """Held or dropped by the relevance filter. The PDF stays stored so the Dropbox outbox still saves it."""
    if status not in DECISION_STATUSES:
        raise ValueError("unknown relevance decision")
    hrana_execute(
        "UPDATE marketdesk_articles SET status=?, reason_code=?, pdf_asset=COALESCE(?,pdf_asset), pdf_sha256=COALESCE(?,pdf_sha256), fingerprint=?, publisher=?, "
        "folders_json=?, file_name=?, note=NULL, last_error=NULL, updated_at=? WHERE article_id=?",
        (status, str(reason_code or "")[:64] or None, pdf_asset, pdf_sha256, _hex(fingerprint), publisher,
         _folders_json(folders), (str(file_name)[:300] if file_name else None), _now(), article_id))


def published_fingerprints() -> list[tuple]:
    """(article_id, fingerprint hex, publisher) of published articles: the MarketDesk novelty index."""
    rows = hrana_execute(
        "SELECT article_id, fingerprint, publisher FROM marketdesk_articles "
        "WHERE status='published' AND fingerprint IS NOT NULL ORDER BY published_at DESC LIMIT 2000")
    return [tuple(r) for r in rows]


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


_UPLOAD_COLUMNS = ("article_id", "post_id", "title", "published_at", "pdf_asset", "pdf_sha256", "dropbox_status",
                   "folders_json", "file_name")


def pending_uploads(limit: int = 10) -> list[dict]:
    """Saved PDFs (published, held or dropped) not yet in Dropbox. Never-tried and skipped rows first, then failed
    retries, oldest first."""
    rows = hrana_execute(
        f"SELECT {','.join(_UPLOAD_COLUMNS)} FROM marketdesk_articles "
        "WHERE status IN ('published','held','dropped') AND pdf_asset IS NOT NULL AND (dropbox_status IS NULL OR dropbox_status IN ('skipped_no_creds','failed')) "
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


# -- operator feedback (research.feedback.apply state protocol) ---------------------------------------------


@dataclass(frozen=True)
class RequeuedRef:
    article_id: str
    url: str
    published_at: datetime
    title: str
    note: str
    institution: str | None = None


def _article_id(work_key) -> str | None:
    if not isinstance(work_key, str) or not work_key.startswith(WORK_PREFIX):
        return None
    return work_key[len(WORK_PREFIX):] or None


def feedback_handled() -> set[str]:
    return {row[0] for row in hrana_execute("SELECT feedback_id FROM marketdesk_feedback_handled LIMIT 5000")}


def mark_feedback_handled(feedback_id: str) -> None:
    hrana_execute("INSERT INTO marketdesk_feedback_handled (feedback_id, handled_at) VALUES (?,?) "
                  "ON CONFLICT(feedback_id) DO NOTHING", (str(feedback_id), _now()))


def work_key_for_file(file_id) -> str | None:
    if not isinstance(file_id, str) or not file_id:
        return None
    rows = hrana_execute("SELECT article_id FROM marketdesk_articles WHERE article_id=?", (file_id,))
    return WORK_PREFIX + file_id if rows else None


def has_live_work(work_key) -> bool:
    article_id = _article_id(work_key)
    if article_id is None:
        return False
    return bool(hrana_execute("SELECT 1 FROM marketdesk_articles WHERE article_id=? AND status='pending'", (article_id,)))


def requeue_with_note(work_key, note: str, *, feedback_id: str) -> bool:
    """A held or dropped article goes back to pending with the operator's note (fresh attempt budget)."""
    article_id = _article_id(work_key)
    if article_id is None:
        return False
    rows = hrana_execute(
        "UPDATE marketdesk_articles SET status='pending', note=?, attempts=0, last_error=NULL, reason_code=NULL, "
        "updated_at=? WHERE article_id=? AND status IN ('held','dropped') RETURNING article_id",
        (note, _now(), article_id))
    if not rows:
        return False
    mark_feedback_handled(feedback_id)
    return True


def requeued(limit: int = 10) -> list[RequeuedRef]:
    """Pending articles carrying an operator note, oldest re-queue first (they may be off the latest listing)."""
    rows = hrana_execute(
        "SELECT article_id, article_url, published_at, title, note FROM marketdesk_articles "
        "WHERE status='pending' AND note IS NOT NULL ORDER BY updated_at, article_id LIMIT ?", (int(limit),))
    return [RequeuedRef(r[0], r[1], _utc(datetime.fromisoformat(str(r[2]).replace("Z", "+00:00"))), r[3] or "", r[4])
            for r in rows]


def note_for(article_id: str) -> dict:
    rows = hrana_execute("SELECT note FROM marketdesk_articles WHERE article_id=? AND status='pending'", (article_id,))
    try:
        value = json.loads(rows[0][0]) if rows and rows[0][0] else {}
    except (TypeError, ValueError):
        value = {}
    return value if isinstance(value, dict) else {}
