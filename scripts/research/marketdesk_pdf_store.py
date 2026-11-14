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
from research.marketdesk_pdf_rate import (
    FLOOR_SECONDS, jittered_delay, load_state, on_quota, on_soft, on_success,
)

ARTICLE_BASE = "https://marketdesk.ai/library/browse/"
ASSET_BASE = "/api/newsfeed/research/files/"
ID = re.compile(r"[A-Za-z0-9]{8,16}\Z")
HASH = re.compile(r"[a-f0-9]{64}\Z")
LEASE_SECONDS = 600
# Live admission interval is marketdesk_pdf_throttle.interval_seconds.
# FLOOR_SECONDS is the fastest persistent pace, not a fixed 1800s slot.
MIN_INTERVAL_SECONDS = FLOOR_SECONDS
MAX_ITEM_ATTEMPTS = 8
ITEM_BACKOFF_CAP = 86400
FRESH_WINDOW_DAYS = 1.5
OLDEST_SLA_SECONDS = 24 * 60 * 60
GROWTH_FLOOR = 48
QUOTA_ERRORS = frozenset({"preview_quota", "download_quota", "http_429"})
SOFT_ERRORS = frozenset({"http_transport"})
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
        f"WHERE disposition='active' AND article_id IN ({ELIGIBLE_IDS}) AND ((status='pending' AND julianday(next_attempt_at)<=julianday(?)) "
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
    # A killed claim increments attempts and leaves last_error NULL. That used
    # to keep the reservation forever. Attempts alone spend it; quota errors
    # still reserve because the file itself did not fail.
    return ("(priority=1 OR NOT EXISTS(SELECT 1 FROM marketdesk_pdf_queue pq WHERE pq.priority=1 "
            "AND pq.disposition='active' AND pq.status!='downloaded' AND pq.article_id IN (" + ELIGIBLE_IDS + ") "
            f"AND (pq.attempts<{PRIORITY_MAX_FAILURES} OR pq.last_error IN ({_QUOTA_SQL})) "
            "AND pq.article_id IN (" + _selected() + ")))")


def _rate_row():
    rows = hrana_execute(
        "SELECT interval_seconds,batch_size,soft_streak,success_credits,jitter_bps FROM marketdesk_pdf_throttle WHERE singleton=1")
    if not rows:
        return load_state(FLOOR_SECONDS, 1, 0, 0), 0
    interval, batch, soft, credits, jitter = rows[0]
    return load_state(interval, batch, soft, credits), int(jitter or 0)


def _sync_disposition(context_hash, now):
    """Policy rejects leave the active backlog. A later eligible decision comes back. Exhausted rows do not."""
    stamp = _stamp(now)
    hrana_execute(
        "UPDATE marketdesk_pdf_queue SET disposition='filtered',disposition_reason=("
        "SELECT reason_code FROM marketdesk_pdf_selection x WHERE x.article_id=marketdesk_pdf_queue.article_id),"
        "disposition_at=? WHERE disposition='active' AND status!='downloaded' AND article_id IN ("
        "SELECT x.article_id FROM marketdesk_pdf_selection x JOIN marketdesk_articles a ON a.article_id=x.article_id "
        "JOIN posts p ON p.id=a.post_id WHERE x.eligible=0 AND x.context_hash=? AND x.post_updated_at IS p.updated_at)",
        (stamp, context_hash))
    hrana_execute(
        "UPDATE marketdesk_pdf_queue SET disposition='active',disposition_reason=NULL,disposition_at=NULL "
        "WHERE disposition='filtered' AND article_id IN ("
        "SELECT x.article_id FROM marketdesk_pdf_selection x JOIN marketdesk_articles a ON a.article_id=x.article_id "
        "JOIN posts p ON p.id=a.post_id WHERE x.eligible=1 AND x.context_hash=? AND x.post_updated_at IS p.updated_at)",
        (context_hash,))
    hrana_execute(
        "UPDATE marketdesk_pdf_queue SET disposition='exhausted',disposition_reason='attempts_exhausted',disposition_at=? "
        "WHERE disposition='active' AND attempts>=? AND status!='downloaded' AND (status='pending' OR lease_until IS NULL "
        "OR julianday(lease_until)<=julianday(?))",
        (stamp, MAX_ITEM_ATTEMPTS, stamp))


def _due_sql():
    # Fresh lane (lane 0) takes reports from the last 36h, newest first, then
    # the oldest backlog. The next admission flips the lane, so a batch of one
    # still spends every other slot on the backlog. Priority still wins.
    return (
        "priority DESC,"
        "CASE WHEN (SELECT lane FROM marketdesk_pdf_throttle WHERE singleton=1)=0 "
        "THEN CASE WHEN julianday(published_at)>=julianday(?)-? THEN 0 ELSE 1 END "
        "ELSE CASE WHEN julianday(published_at)<julianday(?)-? THEN 0 ELSE 1 END END,"
        "CASE WHEN julianday(published_at)>=julianday(?)-? THEN -julianday(published_at) ELSE julianday(published_at) END,"
        "article_id")


def _lease(current, context_hash, *, limit, jitter_bps):
    """Lease up to `limit` rows. The admit trigger moves the account clock only when a row changes hands."""
    if jitter_bps is not None:
        hrana_execute("UPDATE marketdesk_pdf_throttle SET jitter_bps=? WHERE singleton=1",
                      (max(0, min(1000, int(jitter_bps))),))
    stamp = _stamp(current)
    token = uuid.uuid4().hex
    order = _due_sql()
    rows = hrana_execute(
        "UPDATE marketdesk_pdf_queue SET status='downloading',attempts=attempts+1,lease_token=?||article_id,"
        "lease_until=?,updated_at=? WHERE article_id IN (SELECT article_id FROM marketdesk_pdf_queue "
        f"WHERE article_id IN ({ELIGIBLE_IDS}) AND article_id IN ({_selected()}) AND {_priority_gate()} "
        "AND disposition='active' AND attempts<? "
        "AND (SELECT julianday(next_attempt_at) FROM marketdesk_pdf_throttle WHERE singleton=1)<=julianday(?) "
        "AND ((status='pending' AND julianday(next_attempt_at)<=julianday(?)) "
        "OR (status='downloading' AND julianday(lease_until)<=julianday(?))) "
        f"ORDER BY {order} LIMIT ?) "
        "RETURNING article_id,article_url,title,published_at,lease_token",
        (token, _stamp(current + timedelta(seconds=LEASE_SECONDS)), stamp,
         context_hash, context_hash, MAX_ITEM_ATTEMPTS, stamp, stamp, stamp,
         stamp, FRESH_WINDOW_DAYS, stamp, FRESH_WINDOW_DAYS, stamp, FRESH_WINDOW_DAYS,
         max(1, int(limit))))
    work = []
    for key, url, title, published, leased in rows:
        if not ID.fullmatch(key) or url != ARTICLE_BASE + key:
            raise ValueError("invalid persisted MarketDesk PDF reference")
        work.append(Work(key, url, title, datetime.fromisoformat(published.replace("Z", "+00:00")), leased))
    return work


def claim_batch(*, now=None, jitter_bps=0):
    """One shared window. A second caller at the same instant gets nothing."""
    current = _time(now)
    context_hash = prepare_selection(now=current)
    _sync_disposition(context_hash, current)
    _state, _jitter = _rate_row()
    return _lease(current, context_hash, limit=_state.batch_size, jitter_bps=jitter_bps)


def claim_next(*, now=None, interval_seconds=MIN_INTERVAL_SECONDS):
    """One row from the shared window. `interval_seconds` cannot outrun the persisted controller."""
    del interval_seconds  # persisted interval_seconds is the rate. Callers cannot lower it.
    current = _time(now)
    context_hash = prepare_selection(now=current)
    _sync_disposition(context_hash, current)
    rows = _lease(current, context_hash, limit=1, jitter_bps=0)
    return rows[0] if rows else None


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


def _soft(code):
    return code in SOFT_ERRORS or code.startswith("http_5")


def retry(work, error, *, now=None, backoff_seconds=900, retry_after=None):
    """Item clock and account clock move separately. A quota signal refunds the attempt."""
    code = error if isinstance(error, str) and re.fullmatch(r"[a-z0-9_]{1,64}", error) else "download_failed"
    current = _time(now)
    state, _jitter = _rate_row()
    quota = code in QUOTA_ERRORS
    statements = []
    if code == "unauthenticated":
        # A dead session is not a file failure and not a quota multiplier.
        # Hold the account for an hour. The attempt stays spent so a file that
        # 401s on its own can still reach the exhausted state.
        statements.append((
            "UPDATE marketdesk_pdf_throttle SET next_attempt_at=MAX(next_attempt_at,?),"
            "last_error=? WHERE singleton=1 "
            "AND EXISTS(SELECT 1 FROM marketdesk_pdf_queue WHERE article_id=? AND lease_token=?)",
            (_stamp(current + timedelta(seconds=3600)), code, work.article_id, work.token)))
    if quota:
        wait = retry_after
        if code == "unauthenticated":
            wait = max(int(wait or 0), 3600)
        new_state, delay = on_quota(state, retry_after=wait)
        delay = jittered_delay(delay, 0)
        statements.append((
            "UPDATE marketdesk_pdf_throttle SET interval_seconds=?,batch_size=?,soft_streak=0,success_credits=0,"
            "next_attempt_at=?,quota_failures=MIN(3,quota_failures+1),last_error=? WHERE singleton=1 "
            "AND EXISTS(SELECT 1 FROM marketdesk_pdf_queue WHERE article_id=? AND lease_token=?)",
            (new_state.interval_seconds, new_state.batch_size, _stamp(current + timedelta(seconds=delay)),
             code, work.article_id, work.token)))
        statements.append((
            "UPDATE marketdesk_pdf_queue SET status='pending',attempts=MAX(attempts-1,0),"
            "next_attempt_at=(SELECT next_attempt_at FROM marketdesk_pdf_throttle WHERE singleton=1),"
            "last_error=?,lease_token=NULL,lease_until=NULL,updated_at=? WHERE article_id=? AND lease_token=?",
            (code, _stamp(current), work.article_id, work.token)))
    else:
        if _soft(code):
            new_state, slowed = on_soft(state)
            if slowed is None:
                statements.append((
                    "UPDATE marketdesk_pdf_throttle SET soft_streak=?,last_error=? WHERE singleton=1 "
                    "AND EXISTS(SELECT 1 FROM marketdesk_pdf_queue WHERE article_id=? AND lease_token=?)",
                    (new_state.soft_streak, code, work.article_id, work.token)))
            else:
                # Lengthen the next gap. Do not pull next_attempt_at forward.
                statements.append((
                    "UPDATE marketdesk_pdf_throttle SET interval_seconds=?,soft_streak=0,success_credits=?,"
                    "last_error=?,next_attempt_at=MAX(next_attempt_at,?) WHERE singleton=1 "
                    "AND EXISTS(SELECT 1 FROM marketdesk_pdf_queue WHERE article_id=? AND lease_token=?)",
                    (new_state.interval_seconds, new_state.success_credits, code,
                     _stamp(current + timedelta(seconds=slowed)), work.article_id, work.token)))
        try:
            base = max(60, min(int(backoff_seconds), ITEM_BACKOFF_CAP))
        except (TypeError, ValueError):
            base = 900
        statements.append((
            "UPDATE marketdesk_pdf_queue SET status='pending',"
            "next_attempt_at=strftime('%Y-%m-%dT%H:%M:%f+00:00',?, '+' || MIN(?,? * (1 << MIN(MAX(attempts-1,0),7))) || ' seconds'),"
            "last_error=?,lease_token=NULL,lease_until=NULL,updated_at=?,"
            "disposition=CASE WHEN attempts>=? THEN 'exhausted' ELSE disposition END,"
            "disposition_reason=CASE WHEN attempts>=? THEN 'attempts_exhausted' ELSE disposition_reason END,"
            "disposition_at=CASE WHEN attempts>=? THEN ? ELSE disposition_at END "
            "WHERE article_id=? AND lease_token=?",
            (_stamp(current), ITEM_BACKOFF_CAP, base, code, _stamp(current),
             MAX_ITEM_ATTEMPTS, MAX_ITEM_ATTEMPTS, MAX_ITEM_ATTEMPTS, _stamp(current),
             work.article_id, work.token)))
    hrana_transaction(statements)


def release(work, *, now=None):
    """Return an unstarted lease to the queue without spending the attempt."""
    hrana_execute(
        "UPDATE marketdesk_pdf_queue SET status='pending',attempts=MAX(attempts-1,0),lease_token=NULL,lease_until=NULL,"
        "updated_at=? WHERE article_id=? AND lease_token=? AND status='downloading'",
        (_stamp(now), work.article_id, work.token))


def note_success(*, now=None):
    """One additive step per clean cycle. Does not move the next admission earlier."""
    state, _jitter = _rate_row()
    new_state = on_success(state)
    hrana_execute(
        "UPDATE marketdesk_pdf_throttle SET interval_seconds=?,batch_size=?,soft_streak=0,success_credits=? "
        "WHERE singleton=1",
        (new_state.interval_seconds, new_state.batch_size, new_state.success_credits))


def backlog_breach(*, now=None):
    """Eligible active rows only. Unclassified rows still count until a decision lands."""
    current = _time(now)
    try:
        context_hash = policy.load_context().key
    except policy.SelectionUnavailable:
        return None
    rows = hrana_execute(
        "SELECT COUNT(*),MIN(first_seen_at) FROM marketdesk_pdf_queue q WHERE q.disposition='active' "
        "AND q.status IN ('pending','downloading') AND q.article_id IN (" + ELIGIBLE_IDS + ") AND ("
        f"q.article_id IN ({_selected()}) OR NOT EXISTS(SELECT 1 FROM marketdesk_pdf_selection x "
        "JOIN marketdesk_articles a ON a.article_id=x.article_id JOIN posts p ON p.id=a.post_id "
        "WHERE x.article_id=q.article_id AND x.context_hash=? AND x.post_updated_at IS p.updated_at))",
        (context_hash, context_hash))
    depth = int(rows[0][0] or 0) if rows else 0
    oldest = rows[0][1] if rows else None
    previous = hrana_execute("SELECT observed_pending FROM marketdesk_pdf_throttle WHERE singleton=1")
    prior = previous[0][0] if previous else None
    growing = prior is not None and depth > int(prior) and depth >= GROWTH_FLOOR
    hrana_execute("UPDATE marketdesk_pdf_throttle SET observed_pending=? WHERE singleton=1", (depth,))
    age = 0
    if oldest:
        seen = datetime.fromisoformat(str(oldest).replace("Z", "+00:00"))
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
        age = int((current - seen).total_seconds())
    if (oldest and age > OLDEST_SLA_SECONDS) or growing:
        return f"PDF archive backlog: pending={depth} oldest_h={age // 3600} growing={int(bool(growing))}"
    return None


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
        f"AND article_id IN ({_selected()}) AND disposition='active' AND status!='downloaded')", (context_hash,))
    if not rows:
        return None
    code = str(rows[0][0])
    return code if re.fullmatch(r"[a-z0-9_]{1,64}", code) else "download_failed"
