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
from research import marketdesk_pdf_policy as policy

ARTICLE_BASE = "https://marketdesk.ai/library/browse/"
ASSET_BASE = "/api/newsfeed/research/files/"
ID = re.compile(r"[A-Za-z0-9]{8,16}\Z")
HASH = re.compile(r"[a-f0-9]{64}\Z")
LEASE_SECONDS = 600
MIN_INTERVAL_SECONDS = 1800
QUOTA_ERRORS = frozenset({"preview_quota", "download_quota", "http_429"})
# A report that keeps failing on its own (e.g. provider HTTP 500 for one file)
# backs off exponentially and, after this many attempts, stops reserving
# priority precedence, so one bad file cannot starve the rest of the backlog.
PRIORITY_MAX_FAILURES = 3
_QUOTA_SQL = ",".join("'" + code + "'" for code in sorted(QUOTA_ERRORS))
# Trust the article pipeline's source, duplicate, accuracy and writing gates.
# Recheck at claim time so legacy discovery rows cannot bypass publication.
ELIGIBLE_IDS = """SELECT a.article_id FROM marketdesk_articles a
JOIN posts p ON p.id=a.post_id JOIN research_post_sources s ON s.post_id=a.post_id
WHERE a.status='published' AND CASE WHEN json_valid(s.provenance_json) THEN
json_extract(s.provenance_json,'$.kind')='marketdesk'
AND json_extract(s.provenance_json,'$.articleId')=a.article_id
AND json_extract(s.provenance_json,'$.url')=a.article_url ELSE 0 END"""


def _time(now=None):
    value = now or datetime.now(timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _stamp(now=None):
    return _time(now).isoformat()


def enqueue(ref, *, now=None):
    """Idempotent enqueue only after the report passes publication gates."""
    if not ID.fullmatch(ref.article_id) or ref.url != ARTICLE_BASE + ref.article_id:
        raise ValueError("invalid MarketDesk PDF reference")
    stamp = _stamp(now)
    rows = hrana_execute(
        "INSERT INTO marketdesk_pdf_queue(article_id,article_url,title,published_at,next_attempt_at,first_seen_at,updated_at) "
        f"SELECT ?,?,?,?,?,?,? WHERE ? IN ({ELIGIBLE_IDS}) ON CONFLICT(article_id) DO NOTHING RETURNING article_id",
        (ref.article_id, ref.url, str(ref.title or "")[:500], _stamp(ref.published_at), stamp, stamp, stamp, ref.article_id))
    return bool(rows)


def prioritize(article_ids):
    """Replace the bounded requested cohort atomically; leave every retry/lease clock intact.

    This is scheduling preference only. Publication, current PDF policy and the
    shared account admission clock still authorize every claimed download.
    """
    if not isinstance(article_ids, (list, tuple)) or len(article_ids) > 200:
        raise ValueError("expected at most 200 article IDs")
    if any(not isinstance(key, str) or not ID.fullmatch(key) for key in article_ids):
        raise ValueError("invalid priority article ID")
    keys = sorted(set(article_ids))
    statements = [("UPDATE marketdesk_pdf_queue SET priority=0 WHERE priority=1", ())]
    if keys:
        placeholders = ",".join("?" for _ in keys)
        known = hrana_execute(f"SELECT article_id FROM marketdesk_pdf_queue WHERE article_id IN ({placeholders})", tuple(keys))
        if {row[0] for row in known} != set(keys):
            raise ValueError("priority article is not in the PDF queue")
        statements.append((f"UPDATE marketdesk_pdf_queue SET priority=1 WHERE article_id IN ({placeholders})", tuple(keys)))
    hrana_transaction(statements)
    return len(keys)


def seed_missing(*, now=None):
    """Replay the entire known ledger, not only the provider's latest-25 listing."""
    stamp = _stamp(now)
    rows = hrana_execute(
        "INSERT INTO marketdesk_pdf_queue(article_id,article_url,title,published_at,next_attempt_at,first_seen_at,updated_at) "
        "SELECT article_id,article_url,title,published_at,?,?,? FROM marketdesk_articles "
        f"WHERE article_id IN ({ELIGIBLE_IDS}) AND (pdf_asset IS NULL OR pdf_asset='') "
        "AND article_id NOT IN (SELECT article_id FROM marketdesk_pdf_queue) ORDER BY article_id LIMIT 200 "
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
        f"WHERE article_id IN ({ELIGIBLE_IDS}) AND ((status='pending' AND julianday(next_attempt_at)<=julianday(?)) "
        "OR (status='downloading' AND julianday(lease_until)<=julianday(?))) "
        "ORDER BY next_attempt_at,published_at,article_id LIMIT ?", (_stamp(now), _stamp(now), max(1, min(int(limit), 100))))


def _selected():
    return ("SELECT x.article_id FROM marketdesk_pdf_selection x JOIN marketdesk_articles a ON a.article_id=x.article_id "
            "JOIN posts p ON p.id=a.post_id WHERE x.eligible=1 AND x.context_hash="
            "? AND x.post_updated_at IS p.updated_at")


def prepare_selection(*, now=None):
    """Bounded cached classification; rejected PDFs never spend provider allowance.

    A changed book/rule set invalidates old decisions immediately. At most 100
    stale/new rows are refreshed per timer cycle, so rejected early rows cannot
    starve later reports. Published text and post identity are never written.
    """
    context = policy.load_context()
    key = context.key
    rows = hrana_execute(
        "SELECT a.article_id,a.title,p.content,a.publisher,p.updated_at FROM marketdesk_articles a "
        "JOIN posts p ON p.id=a.post_id WHERE a.article_id IN ("
        "SELECT q.article_id FROM marketdesk_pdf_queue q WHERE q.status!='downloaded' "
        f"AND q.article_id IN ({ELIGIBLE_IDS}) "
        "AND NOT EXISTS(SELECT 1 FROM marketdesk_pdf_selection x JOIN marketdesk_articles a2 ON a2.article_id=x.article_id "
        "JOIN posts p2 ON p2.id=a2.post_id WHERE x.article_id=q.article_id AND x.context_hash=? "
        "AND x.post_updated_at IS p2.updated_at) ORDER BY q.priority DESC,q.article_id LIMIT 100) ORDER BY a.article_id", (key,))
    statements = []
    for article_id, title, content, publisher, updated in rows:
        eligible, reason = policy.decide(title=title, content=content, publisher=publisher, context=context)
        statements.append((
            "INSERT INTO marketdesk_pdf_selection(article_id,context_hash,post_updated_at,eligible,reason_code,updated_at) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(article_id) DO UPDATE SET context_hash=excluded.context_hash,"
            "post_updated_at=excluded.post_updated_at,eligible=excluded.eligible,reason_code=excluded.reason_code,updated_at=excluded.updated_at",
            (article_id, key, updated, int(eligible), reason, _stamp(now))))
    if statements:
        hrana_transaction(statements)
    return key


def _priority_gate():
    # Evaluate in the claim SQL too, so a concurrent operator update cannot let
    # ordinary work overtake a newly configured, eligible priority cohort.
    # Future retry/active lease rows still reserve precedence, never a download.
    # A priority row whose own downloads keep failing loses that reservation.
    return ("(priority=1 OR NOT EXISTS(SELECT 1 FROM marketdesk_pdf_queue pq WHERE pq.priority=1 "
            "AND pq.status!='downloaded' AND pq.article_id IN (" + ELIGIBLE_IDS + ") "
            f"AND (pq.attempts<{PRIORITY_MAX_FAILURES} OR pq.last_error IS NULL OR pq.last_error IN ({_QUOTA_SQL})) "
            "AND pq.article_id IN (" + _selected() + ")))")


def claim_next(*, now=None, interval_seconds=MIN_INTERVAL_SECONDS):
    """Atomic fleet-wide throttle before a bounded single-row lease. A crash can only defer work."""
    current = _time(now)
    context_hash = prepare_selection(now=current)
    if not pending(limit=1, now=current):
        return None
    if not hrana_execute(
        f"SELECT 1 FROM marketdesk_pdf_queue WHERE article_id IN ({ELIGIBLE_IDS}) AND article_id IN ({_selected()}) "
        f"AND {_priority_gate()} "
        "AND ((status='pending' AND julianday(next_attempt_at)<=julianday(?)) OR "
        "(status='downloading' AND julianday(lease_until)<=julianday(?))) LIMIT 1",
        (context_hash, context_hash, _stamp(current), _stamp(current))):
        return None
    interval = max(MIN_INTERVAL_SECONDS, min(int(interval_seconds), 86400))
    allowed = hrana_execute(
        "UPDATE marketdesk_pdf_throttle SET next_attempt_at=? WHERE singleton=1 AND julianday(next_attempt_at)<=julianday(?) RETURNING singleton",
        (_stamp(current + timedelta(seconds=interval)), _stamp(current)))
    if not allowed:
        return None
    token = uuid.uuid4().hex
    rows = hrana_execute(
        "UPDATE marketdesk_pdf_queue SET status='downloading',attempts=attempts+1,lease_token=?,lease_until=?,updated_at=? "
        "WHERE article_id=(SELECT article_id FROM marketdesk_pdf_queue "
        f"WHERE article_id IN ({ELIGIBLE_IDS}) AND article_id IN ({_selected()}) "
        f"AND {_priority_gate()} "
        "AND ((status='pending' AND julianday(next_attempt_at)<=julianday(?)) "
        "OR (status='downloading' AND julianday(lease_until)<=julianday(?))) "
        "ORDER BY priority DESC,next_attempt_at,published_at,article_id LIMIT 1) RETURNING article_id,article_url,title,published_at",
        (token, _stamp(current + timedelta(seconds=LEASE_SECONDS)), _stamp(current), context_hash, context_hash, _stamp(current), _stamp(current)))
    if not rows:
        return None
    key, url, title, published = rows[0]
    if not ID.fullmatch(key) or url != ARTICLE_BASE + key:
        raise ValueError("invalid persisted MarketDesk PDF reference")
    return Work(key, url, title, datetime.fromisoformat(published.replace("Z", "+00:00")), token)


def record_download(work, asset, digest, *, now=None, provider_success=False):
    if not HASH.fullmatch(digest) or asset != ASSET_BASE + digest + ".pdf":
        raise ValueError("invalid PDF attachment")
    # A recovered local receipt must not erase a later account-level refusal.
    statements = []
    if provider_success:
        statements.append((
            "UPDATE marketdesk_pdf_throttle SET quota_failures=0,last_error=NULL WHERE singleton=1 "
            "AND EXISTS(SELECT 1 FROM marketdesk_pdf_queue WHERE article_id=? AND lease_token=?)",
            (work.article_id, work.token)))
    statements.append((
        "UPDATE marketdesk_pdf_queue SET status='downloaded',pdf_asset=?,pdf_sha256=?,last_error=NULL,"
        "download_token=?,lease_token=NULL,lease_until=NULL,updated_at=? WHERE article_id=? AND lease_token=?",
        (asset, digest, work.token, _stamp(now), work.article_id, work.token)))
    hrana_transaction(statements)
    return bool(hrana_execute("SELECT 1 FROM marketdesk_pdf_queue WHERE article_id=? AND status='downloaded' "
                              "AND pdf_asset=? AND pdf_sha256=? AND download_token=?", (work.article_id, asset, digest, work.token)))


def retry(work, error, *, now=None, backoff_seconds=900):
    # Error must be a short code, never an exception string containing signed URLs/cookies.
    code = error if isinstance(error, str) and re.fullmatch(r"[a-z0-9_]{1,64}", error) else "download_failed"
    quota = code in QUOTA_ERRORS
    delay = max(21600 if quota else 60, min(int(backoff_seconds), 86400))
    # Compute escalation inside the transaction, shared across all reports and
    # processes. Keep the item and account clocks indivisible on failed writes.
    hrana_transaction([
        ("UPDATE marketdesk_pdf_throttle SET next_attempt_at=MAX(next_attempt_at,"
         "strftime('%Y-%m-%dT%H:%M:%f+00:00',?, '+' || MIN(86400,? * (1 << MIN(quota_failures,2))) || ' seconds')),"
         "quota_failures=MIN(3,quota_failures+?),last_error=? WHERE singleton=1 "
         "AND EXISTS(SELECT 1 FROM marketdesk_pdf_queue WHERE article_id=? AND lease_token=?)",
         (_stamp(now), delay, int(quota), code, work.article_id, work.token)) if quota else
        ("UPDATE marketdesk_pdf_throttle SET next_attempt_at=MAX(next_attempt_at,?),last_error=? WHERE singleton=1 "
         "AND EXISTS(SELECT 1 FROM marketdesk_pdf_queue WHERE article_id=? AND lease_token=?)",
         (_stamp(_time(now) + timedelta(seconds=delay)), code, work.article_id, work.token)),
        ("UPDATE marketdesk_pdf_queue SET status='pending',next_attempt_at=(SELECT next_attempt_at FROM marketdesk_pdf_throttle WHERE singleton=1),"
         "last_error=?,lease_token=NULL,lease_until=NULL,updated_at=? WHERE article_id=? AND lease_token=?",
         (code, _stamp(now), work.article_id, work.token)) if quota else
        # Item failure: this report's own clock doubles per attempt (capped at a
        # day) and never runs ahead of the shared account clock.
        ("UPDATE marketdesk_pdf_queue SET status='pending',next_attempt_at=MAX((SELECT next_attempt_at FROM marketdesk_pdf_throttle WHERE singleton=1),"
         "strftime('%Y-%m-%dT%H:%M:%f+00:00',?, '+' || MIN(86400,? * (1 << MIN(MAX(attempts-1,0),7))) || ' seconds')),"
         "last_error=?,lease_token=NULL,lease_until=NULL,updated_at=? WHERE article_id=? AND lease_token=?",
         (_stamp(now), delay, code, _stamp(now), work.article_id, work.token)),
    ])


def attach_downloads(*, now=None, limit=25):
    """CAS provenance and ledger in one transaction. Never rewrite source identity or published copy."""
    # Preserve previously saved assets even if their article was later held.
    # This local reconciliation does not authorize a new provider download.
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
    context_hash = policy.load_context().key
    rows = hrana_execute(
        "SELECT last_error FROM marketdesk_pdf_throttle WHERE singleton=1 AND last_error IS NOT NULL "
        f"AND EXISTS(SELECT 1 FROM marketdesk_pdf_queue WHERE article_id IN ({ELIGIBLE_IDS}) "
        f"AND article_id IN ({_selected()}) AND status!='downloaded')", (context_hash,))
    if not rows:
        return None
    code = str(rows[0][0])
    return code if re.fullmatch(r"[a-z0-9_]{1,64}", code) else "download_failed"
