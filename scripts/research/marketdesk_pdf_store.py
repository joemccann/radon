"""Durable PDF queue, global admission clock and optional source attachment.

This archive has no authority over article copy, publication or humanizer
certificates. All SQL uses bounded direct Turso HTTP. A leased attempt is
recoverable after a killed worker; throttle and retry clocks survive restarts.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import re
import uuid

from api.db_http import hrana_execute, hrana_transaction

ARTICLE_BASE = "https://marketdesk.ai/library/browse/"
ASSET_BASE = "/api/newsfeed/research/files/"
ID = re.compile(r"[A-Za-z0-9]{8,16}\Z")
HASH = re.compile(r"[a-f0-9]{64}\Z")
LEASE_SECONDS = 600


def _time(now=None):
    value = now or datetime.now(timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _stamp(now=None):
    return _time(now).isoformat()


def enqueue(ref, *, now=None):
    """Idempotent enqueue on discovery, including documents not selected for publication."""
    if not ID.fullmatch(ref.article_id) or ref.url != ARTICLE_BASE + ref.article_id:
        raise ValueError("invalid MarketDesk PDF reference")
    stamp = _stamp(now)
    rows = hrana_execute(
        "INSERT INTO marketdesk_pdf_queue(article_id,article_url,title,published_at,next_attempt_at,first_seen_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?) ON CONFLICT(article_id) DO NOTHING RETURNING article_id",
        (ref.article_id, ref.url, str(ref.title or "")[:500], _stamp(ref.published_at), stamp, stamp, stamp))
    return bool(rows)


def seed_missing(*, now=None):
    """Replay the entire known ledger, not only the provider's latest-25 listing."""
    stamp = _stamp(now)
    rows = hrana_execute(
        "INSERT INTO marketdesk_pdf_queue(article_id,article_url,title,published_at,next_attempt_at,first_seen_at,updated_at) "
        "SELECT article_id,article_url,title,published_at,?,?,? FROM marketdesk_articles "
        "WHERE (pdf_asset IS NULL OR pdf_asset='') AND article_id NOT IN (SELECT article_id FROM marketdesk_pdf_queue) "
        "RETURNING article_id", (stamp, stamp, stamp))
    return len(rows)


@dataclass(frozen=True)
class Work:
    article_id: str
    url: str
    title: str
    published_at: datetime
    token: str


def pending(limit=10, *, now=None):
    """Due backlog, including expired attempts. Read-only diagnostics; claim_next owns admission."""
    return hrana_execute(
        "SELECT article_id,article_url,title,published_at FROM marketdesk_pdf_queue "
        "WHERE (status='pending' AND next_attempt_at<=?) OR (status='downloading' AND lease_until<=?) "
        "ORDER BY next_attempt_at,published_at,article_id LIMIT ?", (_stamp(now), _stamp(now), max(1, min(int(limit), 100))))


def claim_next(*, now=None, interval_seconds=300):
    """Atomic fleet-wide throttle before a bounded single-row lease. A crash can only defer work."""
    current = _time(now)
    if not pending(limit=1, now=current):
        return None
    interval = max(300, min(int(interval_seconds), 86400))
    allowed = hrana_execute(
        "UPDATE marketdesk_pdf_throttle SET next_attempt_at=? WHERE singleton=1 AND next_attempt_at<=? RETURNING singleton",
        (_stamp(current + timedelta(seconds=interval)), _stamp(current)))
    if not allowed:
        return None
    token = uuid.uuid4().hex
    rows = hrana_execute(
        "UPDATE marketdesk_pdf_queue SET status='downloading',attempts=attempts+1,lease_token=?,lease_until=?,updated_at=? "
        "WHERE article_id=(SELECT article_id FROM marketdesk_pdf_queue "
        "WHERE (status='pending' AND next_attempt_at<=?) OR (status='downloading' AND lease_until<=?) "
        "ORDER BY next_attempt_at,published_at,article_id LIMIT 1) RETURNING article_id,article_url,title,published_at",
        (token, _stamp(current + timedelta(seconds=LEASE_SECONDS)), _stamp(current), _stamp(current), _stamp(current)))
    if not rows:
        return None
    key, url, title, published = rows[0]
    if not ID.fullmatch(key) or url != ARTICLE_BASE + key:
        raise ValueError("invalid persisted MarketDesk PDF reference")
    return Work(key, url, title, datetime.fromisoformat(published.replace("Z", "+00:00")), token)


def record_download(work, asset, digest, *, now=None):
    if not HASH.fullmatch(digest) or asset != ASSET_BASE + digest + ".pdf":
        raise ValueError("invalid PDF attachment")
    return bool(hrana_execute(
        "UPDATE marketdesk_pdf_queue SET status='downloaded',pdf_asset=?,pdf_sha256=?,last_error=NULL,"
        "lease_token=NULL,lease_until=NULL,updated_at=? WHERE article_id=? AND lease_token=? RETURNING article_id",
        (asset, digest, _stamp(now), work.article_id, work.token)))


def retry(work, error, *, now=None, backoff_seconds=900):
    current = _time(now)
    until = _stamp(current + timedelta(seconds=max(60, min(int(backoff_seconds), 86400))))
    # Error must be a short code, never an exception string containing signed URLs/cookies.
    code = error if isinstance(error, str) and re.fullmatch(r"[a-z0-9_]{1,64}", error) else "download_failed"
    # Keep provider backoff indivisible across a killed worker or failed SQL
    # step. Check ownership before the item update clears the lease token.
    hrana_transaction([
        ("UPDATE marketdesk_pdf_throttle SET next_attempt_at=MAX(next_attempt_at,?) WHERE singleton=1 "
         "AND EXISTS(SELECT 1 FROM marketdesk_pdf_queue WHERE article_id=? AND lease_token=?)",
         (until, work.article_id, work.token)),
        ("UPDATE marketdesk_pdf_queue SET status='pending',next_attempt_at=?,last_error=?,lease_token=NULL,"
         "lease_until=NULL,updated_at=? WHERE article_id=? AND lease_token=?",
         (until, code, _stamp(current), work.article_id, work.token)),
    ])


def attach_downloads(*, now=None, limit=25):
    """CAS provenance and ledger in one transaction. Never rewrite source identity or published copy."""
    # Saving a PDF does not require a published article. Held/dropped notes
    # remain eligible for the established Dropbox outbox, too.
    hrana_execute(
        "UPDATE marketdesk_articles SET pdf_asset=(SELECT pdf_asset FROM marketdesk_pdf_queue q WHERE q.article_id=marketdesk_articles.article_id),"
        "pdf_sha256=(SELECT pdf_sha256 FROM marketdesk_pdf_queue q WHERE q.article_id=marketdesk_articles.article_id),updated_at=? "
        "WHERE article_id IN (SELECT article_id FROM marketdesk_pdf_queue WHERE status='downloaded') "
        "AND (pdf_asset IS NULL OR pdf_asset='')", (_stamp(now),))
    rows = hrana_execute(
        "SELECT q.article_id,q.pdf_asset,q.pdf_sha256,a.post_id,s.provenance_json FROM marketdesk_pdf_queue q "
        "JOIN marketdesk_articles a ON a.article_id=q.article_id "
        "JOIN research_post_sources s ON s.post_id=a.post_id "
        "WHERE q.status='downloaded' AND CASE WHEN json_valid(s.provenance_json) THEN "
        "json_extract(s.provenance_json,'$.kind')='marketdesk' "
        "AND json_extract(s.provenance_json,'$.articleId')=q.article_id "
        "AND (json_extract(s.provenance_json,'$.pdfUrl') IS NOT q.pdf_asset "
        "OR json_extract(s.provenance_json,'$.pdfHash') IS NOT q.pdf_sha256) ELSE 0 END "
        "ORDER BY q.updated_at,q.article_id LIMIT ?", (max(1, min(int(limit), 100)),))
    attached = 0
    for key, asset, digest, post_id, raw in rows:
        try:
            source = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(source, dict) or source.get("kind") != "marketdesk" or source.get("articleId") != key:
            continue
        if not isinstance(digest, str) or not HASH.fullmatch(digest) or asset != ASSET_BASE + digest + ".pdf":
            continue
        updated = json.dumps({**source, "pdfUrl": asset, "pdfHash": digest}, separators=(",", ":"))
        hrana_transaction([
            ("UPDATE research_post_sources SET provenance_json=? WHERE post_id=? AND provenance_json=?", (updated, post_id, raw)),
            ("UPDATE marketdesk_articles SET pdf_asset=?,pdf_sha256=?,updated_at=? WHERE article_id=? AND post_id=? "
             "AND EXISTS(SELECT 1 FROM research_post_sources WHERE post_id=? AND provenance_json=?)",
             (asset, digest, _stamp(now), key, post_id, post_id, updated)),
            ("UPDATE marketdesk_pdf_queue SET attached_post_id=? WHERE article_id=? AND status='downloaded' "
             "AND EXISTS(SELECT 1 FROM research_post_sources WHERE post_id=? AND provenance_json=?)",
             (post_id, key, post_id, updated)),
        ])
        confirmed = hrana_execute("SELECT 1 FROM marketdesk_pdf_queue WHERE article_id=? AND attached_post_id=? "
                                  "AND EXISTS(SELECT 1 FROM research_post_sources WHERE post_id=? AND provenance_json=?)",
                                  (key, post_id, post_id, updated))
        attached += bool(confirmed)
    return attached


def blocked():
    """Provider failures remain visible during persisted backoff instead of a false green no-op."""
    rows = hrana_execute("SELECT last_error FROM marketdesk_pdf_queue WHERE status='pending' AND last_error IS NOT NULL "
                         "ORDER BY updated_at DESC LIMIT 1")
    if not rows:
        return None
    code = str(rows[0][0])
    return code if re.fullmatch(r"[a-z0-9_]{1,64}", code) else "download_failed"
