"""PDF backlog is independent of article publication and restart-safe."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from research import marketdesk_pdf_store as store

NOW = datetime(2026, 10, 27, 17, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1] / "db/migrations"


@pytest.fixture
def db(monkeypatch):
    conn = sqlite3.connect(":memory:", isolation_level=None)
    for name in ("0001_init.sql", "0071_research_post_sources.sql", "0102_marketdesk_articles.sql", "0103_marketdesk_pdf_queue.sql"):
        conn.executescript((ROOT / name).read_text())
    def execute(sql, args=(), **kwargs):
        return [tuple(row) for row in conn.execute(sql, args).fetchall()]
    def transaction(statements):
        conn.execute("BEGIN IMMEDIATE")
        try:
            for sql, args in statements:
                conn.execute(sql, args)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    monkeypatch.setattr(store, "hrana_execute", execute)
    monkeypatch.setattr(store, "hrana_transaction", transaction)
    yield conn
    conn.close()


def ref(article_id="ABCdef12345"):
    return SimpleNamespace(article_id=article_id, url="https://marketdesk.ai/library/browse/" + article_id,
                           title="A note", published_at=NOW)


def test_enqueue_and_global_budget_survive_new_worker(db):
    store.enqueue(ref(), now=NOW)
    store.enqueue(ref(), now=NOW)
    store.enqueue(ref("XYZabc12345"), now=NOW)
    first = store.claim_next(now=NOW, interval_seconds=300)
    assert first.article_id == ref().article_id
    assert store.claim_next(now=NOW + timedelta(seconds=299), interval_seconds=300) is None
    assert store.claim_next(now=NOW + timedelta(seconds=300), interval_seconds=300) is not None
    assert db.execute("SELECT count(*) FROM marketdesk_pdf_queue").fetchone()[0] == 2


@pytest.mark.parametrize("requested_interval", [1, 60, 299])
def test_durable_admission_enforces_minimum_even_for_direct_callers(db, requested_interval):
    store.enqueue(ref(), now=NOW)
    store.enqueue(ref("XYZabc12345"), now=NOW)
    assert store.claim_next(now=NOW, interval_seconds=requested_interval) is not None
    assert store.claim_next(now=NOW + timedelta(seconds=299), interval_seconds=requested_interval) is None
    assert store.claim_next(now=NOW + timedelta(seconds=300), interval_seconds=requested_interval) is not None


def test_abandoned_lease_replays_and_stale_owner_cannot_commit(db):
    store.enqueue(ref(), now=NOW)
    old = store.claim_next(now=NOW, interval_seconds=300)
    new = store.claim_next(now=NOW + timedelta(seconds=601), interval_seconds=300)
    assert old.token != new.token
    url = "/api/newsfeed/research/files/" + "a" * 64 + ".pdf"
    assert not store.record_download(old, url, "a" * 64, now=NOW)
    assert store.record_download(new, url, "a" * 64, now=NOW)


def test_provider_backoff_is_global_and_does_not_retire_backlog(db):
    store.enqueue(ref(), now=NOW)
    store.enqueue(ref("XYZabc12345"), now=NOW)
    work = store.claim_next(now=NOW, interval_seconds=300)
    store.retry(work, "download_quota", now=NOW, backoff_seconds=3600)
    assert store.claim_next(now=NOW + timedelta(seconds=3599), interval_seconds=300) is None
    assert store.claim_next(now=NOW + timedelta(seconds=3600), interval_seconds=300) is not None


@pytest.mark.parametrize("failed_table", ["marketdesk_pdf_throttle", "marketdesk_pdf_queue"])
def test_failed_backoff_transaction_preserves_both_clock_and_owned_lease(db, failed_table):
    store.enqueue(ref(), now=NOW)
    work = store.claim_next(now=NOW)
    clock = db.execute("SELECT next_attempt_at FROM marketdesk_pdf_throttle").fetchone()
    owned = db.execute("SELECT status,lease_token,next_attempt_at,last_error FROM marketdesk_pdf_queue").fetchone()
    db.execute(f"CREATE TRIGGER fail_backoff BEFORE UPDATE ON {failed_table} "
               "BEGIN SELECT RAISE(ABORT,'simulated database failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="simulated database failure"):
        store.retry(work, "preview_quota", now=NOW, backoff_seconds=3600)
    assert db.execute("SELECT next_attempt_at FROM marketdesk_pdf_throttle").fetchone() == clock
    assert db.execute("SELECT status,lease_token,next_attempt_at,last_error FROM marketdesk_pdf_queue").fetchone() == owned


def test_seed_replays_old_ledger_rows_outside_latest_listing(db):
    for key in ("ABCdef12345", "XYZabc12345"):
        db.execute("INSERT INTO marketdesk_articles(article_id,article_url,title,published_at,first_seen_at,updated_at) VALUES(?,?,?,?,?,?)",
                   (key, ref(key).url, "Old", "2026-10-20T00:00:00+00:00", NOW.isoformat(), NOW.isoformat()))
    assert store.seed_missing(now=NOW) == 2
    assert store.seed_missing(now=NOW) == 0
    assert len(store.pending(limit=100, now=NOW)) == 2


def test_attachment_preserves_article_copy_identity_and_certificate(db):
    key, post_id = ref().article_id, "research-" + "a" * 32
    source = {"kind": "marketdesk", "articleId": key, "evidenceKind": "article", "contentHash": "b" * 64,
              "url": ref().url, "humanizer": {"textHash": "unchanged"}, "figures": []}
    db.execute("INSERT INTO posts(id,title,content,timestamp,created_at,updated_at) VALUES(?,?,?,?,?,?)",
               (post_id, "Copy", "Unchanged body", NOW.isoformat(), NOW.isoformat(), NOW.isoformat()))
    db.execute("INSERT INTO research_post_sources VALUES(?,?)", (post_id, json.dumps(source)))
    db.execute("INSERT INTO marketdesk_articles(article_id,article_url,published_at,post_id,status,first_seen_at,updated_at) VALUES(?,?,?,?,?,?,?)",
               (key, ref().url, NOW.isoformat(), post_id, "published", NOW.isoformat(), NOW.isoformat()))
    store.enqueue(ref(), now=NOW)
    work = store.claim_next(now=NOW, interval_seconds=300)
    url = "/api/newsfeed/research/files/" + "c" * 64 + ".pdf"
    store.record_download(work, url, "c" * 64, now=NOW)
    assert store.attach_downloads(now=NOW) == 1
    attached = json.loads(db.execute("SELECT provenance_json FROM research_post_sources").fetchone()[0])
    assert attached == {**source, "pdfUrl": url, "pdfHash": "c" * 64}
    assert db.execute("SELECT pdf_asset,pdf_sha256 FROM marketdesk_articles").fetchone() == (url, "c" * 64)
    assert db.execute("SELECT title,content FROM posts").fetchone() == ("Copy", "Unchanged body")
    assert store.attach_downloads(now=NOW) == 0


@pytest.mark.parametrize("replacement", ["missing", "wrong_url", "wrong_hash"])
def test_same_post_source_refresh_restores_actual_pdf_pair(db, replacement):
    key, post_id = ref().article_id, "research-" + "a" * 32
    source = {"kind": "marketdesk", "articleId": key, "evidenceKind": "article", "contentHash": "b" * 64,
              "url": ref().url, "humanizer": {"textHash": "unchanged"}, "figures": []}
    db.execute("INSERT INTO posts(id,title,content,timestamp,created_at,updated_at) VALUES(?,?,?,?,?,?)",
               (post_id, "Copy", "Unchanged body", NOW.isoformat(), NOW.isoformat(), NOW.isoformat()))
    db.execute("INSERT INTO research_post_sources VALUES(?,?)", (post_id, json.dumps(source)))
    db.execute("INSERT INTO marketdesk_articles(article_id,article_url,published_at,post_id,status,first_seen_at,updated_at) VALUES(?,?,?,?,?,?,?)",
               (key, ref().url, NOW.isoformat(), post_id, "published", NOW.isoformat(), NOW.isoformat()))
    store.enqueue(ref(), now=NOW)
    work = store.claim_next(now=NOW)
    url = "/api/newsfeed/research/files/" + "c" * 64 + ".pdf"
    store.record_download(work, url, "c" * 64, now=NOW)
    assert store.attach_downloads(now=NOW) == 1
    refreshed = {**source, "publisher": "Corrected bank"}
    if replacement != "missing":
        refreshed.update(pdfUrl=url, pdfHash="c" * 64)
        refreshed["pdfUrl" if replacement == "wrong_url" else "pdfHash"] = "changed"
    # publish.publish's idempotent upsert replaces provenance for a stable post.
    db.execute("UPDATE research_post_sources SET provenance_json=? WHERE post_id=?", (json.dumps(refreshed), post_id))
    assert store.attach_downloads(now=NOW + timedelta(minutes=5)) == 1
    assert json.loads(db.execute("SELECT provenance_json FROM research_post_sources").fetchone()[0]) == {
        **refreshed, "pdfUrl": url, "pdfHash": "c" * 64}
    assert db.execute("SELECT title,content FROM posts").fetchone() == ("Copy", "Unchanged body")
    assert store.attach_downloads(now=NOW + timedelta(minutes=10)) == 0


def test_attachment_compare_and_swap_preserves_concurrent_source_edit(db, monkeypatch):
    key, post_id = ref().article_id, "research-" + "a" * 32
    source = {"kind": "marketdesk", "articleId": key, "contentHash": "b" * 64, "url": ref().url}
    db.execute("INSERT INTO posts(id,title,timestamp,created_at,updated_at) VALUES(?,?,?,?,?)",
               (post_id, "Copy", NOW.isoformat(), NOW.isoformat(), NOW.isoformat()))
    db.execute("INSERT INTO research_post_sources VALUES(?,?)", (post_id, json.dumps(source)))
    db.execute("INSERT INTO marketdesk_articles(article_id,article_url,published_at,post_id,status,first_seen_at,updated_at) VALUES(?,?,?,?,?,?,?)",
               (key, ref().url, NOW.isoformat(), post_id, "published", NOW.isoformat(), NOW.isoformat()))
    store.enqueue(ref(), now=NOW)
    work = store.claim_next(now=NOW)
    url = "/api/newsfeed/research/files/" + "c" * 64 + ".pdf"
    store.record_download(work, url, "c" * 64, now=NOW)
    transaction = store.hrana_transaction
    edited = {**source, "publisher": "Corrected bank"}
    def race(statements):
        db.execute("UPDATE research_post_sources SET provenance_json=?", (json.dumps(edited),))
        transaction(statements)
    monkeypatch.setattr(store, "hrana_transaction", race)
    assert store.attach_downloads(now=NOW) == 0
    assert json.loads(db.execute("SELECT provenance_json FROM research_post_sources").fetchone()[0]) == edited
    monkeypatch.setattr(store, "hrana_transaction", transaction)
    assert store.attach_downloads(now=NOW) == 1
    assert json.loads(db.execute("SELECT provenance_json FROM research_post_sources").fetchone()[0]) == {**edited, "pdfUrl": url, "pdfHash": "c" * 64}


def test_attachment_waits_for_publication_and_never_touches_other_source(db):
    store.enqueue(ref(), now=NOW)
    work = store.claim_next(now=NOW, interval_seconds=300)
    store.record_download(work, "/api/newsfeed/research/files/" + "c" * 64 + ".pdf", "c" * 64, now=NOW)
    assert store.attach_downloads(now=NOW) == 0
    assert db.execute("SELECT status FROM marketdesk_pdf_queue").fetchone()[0] == "downloaded"


def test_stale_retry_does_not_extend_global_backoff(db):
    store.enqueue(ref(), now=NOW)
    old = store.claim_next(now=NOW)
    new = store.claim_next(now=NOW + timedelta(seconds=601))
    store.retry(old, "download_quota", now=NOW + timedelta(seconds=601), backoff_seconds=3600)
    assert store.blocked() is None
    assert db.execute("SELECT lease_token FROM marketdesk_pdf_queue").fetchone()[0] == new.token


def test_downloaded_held_article_enters_existing_dropbox_outbox(db):
    db.execute("INSERT INTO marketdesk_articles(article_id,article_url,published_at,status,first_seen_at,updated_at) VALUES(?,?,?,?,?,?)",
               (ref().article_id, ref().url, NOW.isoformat(), "held", NOW.isoformat(), NOW.isoformat()))
    store.enqueue(ref(), now=NOW)
    work = store.claim_next(now=NOW)
    url = "/api/newsfeed/research/files/" + "a" * 64 + ".pdf"
    store.record_download(work, url, "a" * 64, now=NOW)
    store.attach_downloads(now=NOW)
    assert db.execute("SELECT status,pdf_asset,pdf_sha256,dropbox_status FROM marketdesk_articles").fetchone() == ("held", url, "a" * 64, None)


@pytest.mark.parametrize("asset,digest", [("https://evil.invalid/a.pdf", "a" * 64), ("/api/newsfeed/research/files/a.pdf", "bad")])
def test_invalid_download_result_never_persists(db, asset, digest):
    store.enqueue(ref(), now=NOW)
    work = store.claim_next(now=NOW)
    with pytest.raises(ValueError):
        store.record_download(work, asset, digest, now=NOW)


def test_retry_error_details_are_sanitized_and_visible(db):
    store.enqueue(ref(), now=NOW)
    work = store.claim_next(now=NOW)
    store.retry(work, "https://provider.invalid/?token=secret", now=NOW)
    assert store.blocked() == "download_failed"
    db.execute("UPDATE marketdesk_pdf_queue SET last_error='raw provider secret'")
    assert store.blocked() == "download_failed"


@pytest.mark.parametrize("key", ["bad/id", "x", "a" * 30])
def test_invalid_ref_never_enters_queue(db, key):
    with pytest.raises(ValueError):
        store.enqueue(ref(key), now=NOW)
