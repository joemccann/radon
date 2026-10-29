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
    monkeypatch.setattr(store.policy, "load_context", lambda: store.policy.Context(frozenset(), {}))
    conn = sqlite3.connect(":memory:", isolation_level=None)
    for name in ("0001_init.sql", "0071_research_post_sources.sql", "0102_marketdesk_articles.sql", "0103_marketdesk_pdf_queue.sql", "0104_marketdesk_pdf_account_backoff.sql", "0105_marketdesk_pdf_priority.sql"):
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
    published(db, "XYZabc12345")
    published(db)
    store.enqueue(ref(), now=NOW)
    store.enqueue(ref(), now=NOW)
    store.enqueue(ref("XYZabc12345"), now=NOW)
    first = store.claim_next(now=NOW, interval_seconds=300)
    assert first.article_id == ref().article_id
    assert store.claim_next(now=NOW + timedelta(seconds=1799), interval_seconds=300) is None
    assert store.claim_next(now=NOW + timedelta(seconds=1800), interval_seconds=300) is not None
    assert db.execute("SELECT count(*) FROM marketdesk_pdf_queue").fetchone()[0] == 2


@pytest.mark.parametrize("requested_interval", [1, 60, 299])
def test_durable_admission_enforces_minimum_even_for_direct_callers(db, requested_interval):
    published(db, "XYZabc12345")
    published(db)
    store.enqueue(ref(), now=NOW)
    store.enqueue(ref("XYZabc12345"), now=NOW)
    assert store.claim_next(now=NOW, interval_seconds=requested_interval) is not None
    assert store.claim_next(now=NOW + timedelta(seconds=1799), interval_seconds=requested_interval) is None
    assert store.claim_next(now=NOW + timedelta(seconds=1800), interval_seconds=requested_interval) is not None


def test_abandoned_lease_replays_and_stale_owner_cannot_commit(db):
    published(db)
    store.enqueue(ref(), now=NOW)
    old = store.claim_next(now=NOW, interval_seconds=300)
    new = store.claim_next(now=NOW + timedelta(seconds=1801), interval_seconds=300)
    assert old.token != new.token
    url = "/api/newsfeed/research/files/" + "a" * 64 + ".pdf"
    assert not store.record_download(old, url, "a" * 64, now=NOW)
    assert store.record_download(new, url, "a" * 64, now=NOW)


def test_provider_backoff_is_global_and_does_not_retire_backlog(db):
    published(db, "XYZabc12345")
    published(db)
    store.enqueue(ref(), now=NOW)
    store.enqueue(ref("XYZabc12345"), now=NOW)
    work = store.claim_next(now=NOW, interval_seconds=300)
    store.retry(work, "download_quota", now=NOW, backoff_seconds=21600)
    assert store.claim_next(now=NOW + timedelta(seconds=21599), interval_seconds=300) is None
    assert store.claim_next(now=NOW + timedelta(seconds=21600), interval_seconds=300) is not None


@pytest.mark.parametrize("failed_table", ["marketdesk_pdf_throttle", "marketdesk_pdf_queue"])
def test_failed_backoff_transaction_preserves_both_clock_and_owned_lease(db, failed_table):
    published(db)
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


def test_seed_replays_old_published_rows_outside_latest_listing(db):
    for key in ("ABCdef12345", "XYZabc12345"):
        published(db, key)
    db.execute("UPDATE marketdesk_articles SET published_at='2026-10-20T00:00:00+00:00'")
    assert store.seed_missing(now=NOW) == 2
    assert store.seed_missing(now=NOW) == 0
    assert len(store.pending(limit=100, now=NOW)) == 2


def test_attachment_preserves_article_copy_identity_and_certificate(db):
    key, post_id = ref().article_id, "research-" + "a" * 32
    source = {"kind": "marketdesk", "articleId": key, "evidenceKind": "article", "contentHash": "b" * 64,
              "url": ref().url, "humanizer": {"textHash": "unchanged"}, "figures": []}
    db.execute("INSERT INTO posts(id,title,content,timestamp,created_at,updated_at) VALUES(?,?,?,?,?,?)",
               (post_id, "Copy", "Positioning rose 12%.", NOW.isoformat(), NOW.isoformat(), NOW.isoformat()))
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
    assert db.execute("SELECT title,content FROM posts").fetchone() == ("Copy", "Positioning rose 12%.")
    assert store.attach_downloads(now=NOW) == 0


@pytest.mark.parametrize("replacement", ["missing", "wrong_url", "wrong_hash"])
def test_same_post_source_refresh_restores_actual_pdf_pair(db, replacement):
    key, post_id = ref().article_id, "research-" + "a" * 32
    source = {"kind": "marketdesk", "articleId": key, "evidenceKind": "article", "contentHash": "b" * 64,
              "url": ref().url, "humanizer": {"textHash": "unchanged"}, "figures": []}
    db.execute("INSERT INTO posts(id,title,content,timestamp,created_at,updated_at) VALUES(?,?,?,?,?,?)",
               (post_id, "Copy", "Positioning rose 12%.", NOW.isoformat(), NOW.isoformat(), NOW.isoformat()))
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
    assert db.execute("SELECT title,content FROM posts").fetchone() == ("Copy", "Positioning rose 12%.")
    assert store.attach_downloads(now=NOW + timedelta(minutes=10)) == 0


def test_attachment_compare_and_swap_preserves_concurrent_source_edit(db, monkeypatch):
    key, post_id = ref().article_id, "research-" + "a" * 32
    source = {"kind": "marketdesk", "articleId": key, "contentHash": "b" * 64, "url": ref().url}
    db.execute("INSERT INTO posts(id,title,content,timestamp,created_at,updated_at) VALUES(?,?,?,?,?,?)",
               (post_id, "Copy", "Positioning rose 12%.", NOW.isoformat(), NOW.isoformat(), NOW.isoformat()))
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


def test_already_saved_pdf_waits_for_publication_and_never_touches_other_source(db):
    published(db)
    store.enqueue(ref(), now=NOW)
    work = store.claim_next(now=NOW, interval_seconds=300)
    store.record_download(work, "/api/newsfeed/research/files/" + "c" * 64 + ".pdf", "c" * 64, now=NOW)
    db.execute("UPDATE marketdesk_articles SET status='held',post_id=NULL")
    assert store.attach_downloads(now=NOW) == 0
    assert db.execute("SELECT status FROM marketdesk_pdf_queue").fetchone()[0] == "downloaded"


def test_stale_retry_does_not_extend_global_backoff(db):
    published(db)
    store.enqueue(ref(), now=NOW)
    old = store.claim_next(now=NOW)
    new = store.claim_next(now=NOW + timedelta(seconds=1801))
    store.retry(old, "download_quota", now=NOW + timedelta(seconds=1801), backoff_seconds=3600)
    assert store.blocked() is None
    assert db.execute("SELECT lease_token FROM marketdesk_pdf_queue").fetchone()[0] == new.token


def test_previously_downloaded_held_article_retains_existing_dropbox_outbox(db):
    published(db)
    store.enqueue(ref(), now=NOW)
    work = store.claim_next(now=NOW)
    url = "/api/newsfeed/research/files/" + "a" * 64 + ".pdf"
    store.record_download(work, url, "a" * 64, now=NOW)
    db.execute("UPDATE marketdesk_articles SET status='held'")
    store.attach_downloads(now=NOW)
    assert db.execute("SELECT status,pdf_asset,pdf_sha256,dropbox_status FROM marketdesk_articles").fetchone() == ("held", url, "a" * 64, None)


@pytest.mark.parametrize("asset,digest", [("https://evil.invalid/a.pdf", "a" * 64), ("/api/newsfeed/research/files/a.pdf", "bad")])
def test_invalid_download_result_never_persists(db, asset, digest):
    published(db)
    store.enqueue(ref(), now=NOW)
    work = store.claim_next(now=NOW)
    with pytest.raises(ValueError):
        store.record_download(work, asset, digest, now=NOW)


def test_retry_error_details_are_sanitized_and_visible(db):
    published(db)
    store.enqueue(ref(), now=NOW)
    work = store.claim_next(now=NOW)
    store.retry(work, "https://provider.invalid/?token=secret", now=NOW)
    assert store.blocked() == "download_failed"
    db.execute("UPDATE marketdesk_pdf_throttle SET last_error='raw provider secret'")
    assert store.blocked() == "download_failed"


@pytest.mark.parametrize("key", ["bad/id", "x", "a" * 30])
def test_invalid_ref_never_enters_queue(db, key):
    with pytest.raises(ValueError):
        store.enqueue(ref(key), now=NOW)


def published(db, key="ABCdef12345", *, status="published", source_kind="marketdesk", source_id=None):
    post_id = "research-" + key
    db.execute("INSERT INTO posts(id,title,content,timestamp,created_at,updated_at) VALUES(?,?,?,?,?,?)",
               (post_id, "Selected", "Positioning rose 12%.", NOW.isoformat(), NOW.isoformat(), NOW.isoformat()))
    db.execute("INSERT INTO research_post_sources VALUES(?,?)", (post_id, json.dumps({
        "kind": source_kind, "articleId": source_id or key, "url": ref(key).url})))
    db.execute("INSERT INTO marketdesk_articles(article_id,article_url,published_at,post_id,status,first_seen_at,updated_at) VALUES(?,?,?,?,?,?,?)",
               (key, ref(key).url, NOW.isoformat(), post_id, status, NOW.isoformat(), NOW.isoformat()))


@pytest.mark.parametrize("status", ["pending", "held", "dropped", "failed"])
def test_discovery_does_not_spend_pdf_budget_on_unpublished_reports(db, status):
    published(db, status=status)
    assert not store.enqueue(ref(), now=NOW)
    assert store.seed_missing(now=NOW) == 0
    assert store.claim_next(now=NOW) is None


@pytest.mark.parametrize("kind,source_id", [("dropbox", "ABCdef12345"), ("marketdesk", "XYZabc12345")])
def test_pdf_eligibility_requires_matching_published_source(db, kind, source_id):
    published(db, source_kind=kind, source_id=source_id)
    assert not store.enqueue(ref(), now=NOW)
    assert store.seed_missing(now=NOW) == 0


def test_legacy_pending_rows_are_parked_until_publication_without_deletion(db):
    published(db, status="held")
    db.execute("INSERT INTO marketdesk_pdf_queue(article_id,article_url,published_at,next_attempt_at,first_seen_at,updated_at,last_error) VALUES(?,?,?,?,?,?,?)",
               (ref().article_id, ref().url, NOW.isoformat(), NOW.isoformat(), NOW.isoformat(), NOW.isoformat(), "preview_quota"))
    assert store.pending(now=NOW) == []
    assert store.claim_next(now=NOW) is None
    assert store.blocked() is None
    assert db.execute("SELECT attempts FROM marketdesk_pdf_queue").fetchone() == (0,)
    db.execute("UPDATE marketdesk_articles SET status='published'")
    assert store.claim_next(now=NOW) is not None


def test_account_spacing_cannot_be_lowered_by_old_environment_or_direct_callers(db):
    published(db)
    published(db, "XYZabc12345")
    store.seed_missing(now=NOW)
    assert store.claim_next(now=NOW, interval_seconds=300) is not None
    assert store.claim_next(now=NOW + timedelta(seconds=1799), interval_seconds=300) is None
    assert store.claim_next(now=NOW + timedelta(seconds=1800), interval_seconds=300) is not None


@pytest.mark.parametrize("code", ["preview_quota", "download_quota", "http_429"])
def test_quota_escalation_is_account_wide_capped_and_atomic(db, code):
    published(db)
    published(db, "XYZabc12345")
    store.seed_missing(now=NOW)
    current = NOW
    for expected_hours in (6, 12, 24, 24):
        work = store.claim_next(now=current)
        assert work is not None
        store.retry(work, code, now=current, backoff_seconds=1)
        deadline = current + timedelta(hours=expected_hours)
        account = db.execute("SELECT next_attempt_at,quota_failures,last_error FROM marketdesk_pdf_throttle").fetchone()
        assert datetime.fromisoformat(account[0]) == deadline
        assert account[1] <= 3 and account[2] == code
        item = db.execute("SELECT next_attempt_at FROM marketdesk_pdf_queue WHERE article_id=?", (work.article_id,)).fetchone()
        assert item[0] == account[0]
        assert store.claim_next(now=deadline-timedelta(milliseconds=1)) is None
        current = deadline


def test_only_verified_provider_success_resets_account_quota_streak(db):
    published(db)
    store.seed_missing(now=NOW)
    first = store.claim_next(now=NOW)
    store.retry(first, "preview_quota", now=NOW)
    second = store.claim_next(now=NOW+timedelta(hours=6))
    asset = store.ASSET_BASE + "a"*64 + ".pdf"
    assert store.record_download(second, asset, "a"*64, now=NOW+timedelta(hours=6), provider_success=False)
    assert db.execute("SELECT quota_failures FROM marketdesk_pdf_throttle").fetchone() == (1,)
    published(db, "XYZabc12345")
    store.seed_missing(now=NOW+timedelta(hours=7))
    third = store.claim_next(now=NOW+timedelta(hours=7))
    assert store.record_download(third, asset, "a"*64, now=NOW+timedelta(hours=7), provider_success=True)
    assert db.execute("SELECT quota_failures,last_error FROM marketdesk_pdf_throttle").fetchone() == (0,None)


def test_selection_is_rechecked_if_publication_changes_after_due_scan(db, monkeypatch):
    published(db)
    store.seed_missing(now=NOW)
    pending = store.pending
    def change(*args, **kwargs):
        rows = pending(*args, **kwargs)
        db.execute("UPDATE marketdesk_articles SET status='held'")
        return rows
    monkeypatch.setattr(store, "pending", change)
    assert store.claim_next(now=NOW) is None
    assert db.execute("SELECT attempts FROM marketdesk_pdf_queue").fetchone() == (0,)


def test_strict_pdf_policy_excludes_off_book_without_changing_publication(db, monkeypatch):
    from research import marketdesk_pdf_policy as policy
    monkeypatch.setattr(policy, "load_context", lambda: policy.Context(frozenset({"AAPL"}), {}))
    published(db)
    db.execute("UPDATE posts SET content='$MSFT earnings rose 12%.'")
    store.seed_missing(now=NOW)
    assert store.claim_next(now=NOW) is None
    assert db.execute("SELECT status FROM marketdesk_articles").fetchone() == ("published",)
    assert db.execute("SELECT attempts FROM marketdesk_pdf_queue").fetchone() == (0,)


def test_pdf_selection_rechecks_changed_book_before_network_budget(db, monkeypatch):
    from research import marketdesk_pdf_policy as policy
    context = [policy.Context(frozenset({"MSFT"}), {})]
    monkeypatch.setattr(policy, "load_context", lambda: context[0])
    published(db)
    db.execute("UPDATE posts SET content='$AAPL earnings rose 12%.'")
    store.seed_missing(now=NOW)
    assert store.claim_next(now=NOW) is None
    context[0] = policy.Context(frozenset({"AAPL"}), {})
    assert store.claim_next(now=NOW) is not None


def test_selection_batches_cannot_starve_later_eligible_reports(db, monkeypatch):
    from research import marketdesk_pdf_policy as policy
    monkeypatch.setattr(policy, "load_context", lambda: policy.Context(frozenset({"AAPL"}), {}))
    for i in range(101):
        published(db, f"Report{i:06d}")
    db.execute("UPDATE posts SET content='$MSFT earnings rose 12%.'")
    db.execute("UPDATE posts SET content='$AAPL earnings rose 12%.' WHERE id='research-Report000100'")
    store.seed_missing(now=NOW)
    assert store.claim_next(now=NOW) is None
    assert db.execute("SELECT count(*) FROM marketdesk_pdf_selection").fetchone() == (100,)
    # Excluded selection does not consume the account clock or attempt count.
    work = store.claim_next(now=NOW)
    assert work.article_id == "Report000100"
    assert db.execute("SELECT count(*) FROM marketdesk_pdf_selection").fetchone() == (101,)


def test_post_edit_invalidates_an_old_eligible_pdf_decision(db, monkeypatch):
    published(db)
    store.seed_missing(now=NOW)
    store.prepare_selection(now=NOW)
    assert db.execute("SELECT eligible FROM marketdesk_pdf_selection").fetchone() == (1,)
    db.execute("UPDATE posts SET content='An election recap.', updated_at=?", ((NOW+timedelta(seconds=1)).isoformat(),))
    assert store.claim_next(now=NOW) is None
    assert db.execute("SELECT eligible FROM marketdesk_pdf_selection").fetchone() == (0,)


def test_account_success_acknowledgement_is_atomic(db):
    published(db)
    store.seed_missing(now=NOW)
    work = store.claim_next(now=NOW)
    db.execute("UPDATE marketdesk_pdf_throttle SET quota_failures=2,last_error='preview_quota'")
    db.execute("CREATE TRIGGER fail_ack BEFORE UPDATE ON marketdesk_pdf_queue BEGIN SELECT RAISE(ABORT,'simulated ack failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="simulated ack failure"):
        store.record_download(work, store.ASSET_BASE+'a'*64+'.pdf', 'a'*64, provider_success=True, now=NOW)
    assert db.execute("SELECT quota_failures,last_error FROM marketdesk_pdf_throttle").fetchone() == (2,'preview_quota')
    assert db.execute("SELECT lease_token FROM marketdesk_pdf_queue").fetchone() == (work.token,)


def test_corrupt_persisted_reference_never_reaches_provider(db):
    published(db)
    store.seed_missing(now=NOW)
    db.execute("UPDATE marketdesk_pdf_queue SET article_url='https://evil.invalid/a'")
    with pytest.raises(ValueError, match="persisted MarketDesk PDF reference"):
        store.claim_next(now=NOW)


def test_stale_owner_cannot_claim_another_workers_success_or_clear_quota(db):
    published(db)
    store.seed_missing(now=NOW)
    old = store.claim_next(now=NOW)
    new = store.claim_next(now=NOW+timedelta(minutes=31))
    asset = store.ASSET_BASE + 'a'*64 + '.pdf'
    assert store.record_download(new, asset, 'a'*64, now=NOW, provider_success=True)
    db.execute("UPDATE marketdesk_pdf_throttle SET quota_failures=2,last_error='preview_quota'")
    assert not store.record_download(old, asset, 'a'*64, now=NOW, provider_success=True)
    assert db.execute("SELECT quota_failures,last_error FROM marketdesk_pdf_throttle").fetchone() == (2,'preview_quota')


def priority_pair(db):
    for key in ('Older000001', 'Priority001'):
        published(db, key)
    store.enqueue(ref('Older000001'), now=NOW - timedelta(days=1))
    store.enqueue(ref('Priority001'), now=NOW)
    return 'Older000001', 'Priority001'


def test_priority_cohort_precedes_older_unrelated_queue_and_survives_restart(db, monkeypatch):
    ordinary, priority = priority_pair(db)
    clocks = db.execute('SELECT next_attempt_at FROM marketdesk_pdf_throttle').fetchall()
    dates = db.execute('SELECT article_id,next_attempt_at FROM marketdesk_pdf_queue ORDER BY article_id').fetchall()
    assert store.prioritize([priority]) == 1
    assert store.prioritize([priority, priority]) == 1
    assert db.execute('SELECT next_attempt_at FROM marketdesk_pdf_throttle').fetchall() == clocks
    assert db.execute('SELECT article_id,next_attempt_at FROM marketdesk_pdf_queue ORDER BY article_id').fetchall() == dates
    import importlib
    execute, transaction = store.hrana_execute, store.hrana_transaction
    importlib.reload(store)
    # Reloaded module uses the same durable database, not process-local priority.
    monkeypatch.setattr(store, "hrana_execute", execute)
    monkeypatch.setattr(store, "hrana_transaction", transaction)
    work = store.claim_next(now=NOW)
    assert work.article_id == priority
    assert store.claim_next(now=NOW + timedelta(seconds=1799)) is None
    assert store.record_download(work, store.ASSET_BASE + 'a'*64 + '.pdf', 'a'*64, now=NOW)
    assert store.claim_next(now=NOW + timedelta(seconds=1800)).article_id == ordinary


def test_priority_waits_for_its_retry_deadline_without_spending_other_pdf(db):
    _, priority = priority_pair(db)
    store.prioritize([priority])
    db.execute('UPDATE marketdesk_pdf_queue SET next_attempt_at=? WHERE article_id=?',
               ((NOW + timedelta(hours=1)).isoformat(), priority))
    clock = db.execute('SELECT next_attempt_at FROM marketdesk_pdf_throttle').fetchone()
    assert store.claim_next(now=NOW) is None
    assert db.execute('SELECT next_attempt_at FROM marketdesk_pdf_throttle').fetchone() == clock
    assert store.claim_next(now=NOW + timedelta(hours=1)).article_id == priority


@pytest.mark.parametrize('change', ['held', 'dropped', 'off_book', 'source_missing'])
def test_priority_cannot_override_publication_source_or_pdf_policy(db, change):
    ordinary, priority = priority_pair(db)
    store.prioritize([priority])
    if change in ('held', 'dropped'):
        db.execute('UPDATE marketdesk_articles SET status=? WHERE article_id=?', (change, priority))
    elif change == 'off_book':
        db.execute('UPDATE posts SET content=? WHERE id=?', ('$MSFT earnings rose 12%.', 'research-'+priority))
    else:
        db.execute('DELETE FROM research_post_sources WHERE post_id=?', ('research-'+priority,))
    assert store.claim_next(now=NOW).article_id == ordinary
    assert db.execute('SELECT attempts FROM marketdesk_pdf_queue WHERE article_id=?', (priority,)).fetchone() == (0,)


def test_priority_still_obeys_account_backoff_and_token_lease(db):
    _, priority = priority_pair(db)
    store.prioritize([priority])
    first = store.claim_next(now=NOW)
    store.retry(first, 'preview_quota', now=NOW)
    assert store.claim_next(now=NOW + timedelta(hours=5, minutes=59)) is None
    new = store.claim_next(now=NOW + timedelta(hours=6))
    assert new.article_id == priority and new.token != first.token
    assert not store.record_download(first, store.ASSET_BASE+'b'*64+'.pdf', 'b'*64)


@pytest.mark.parametrize('bad', [['../bad'], ['Unknown0001'], ['x'*17], 'Priority001', ['Priority001', 1], [f'Key{i:08}' for i in range(201)]])
def test_priority_configuration_validates_before_any_change(db, bad):
    _, priority = priority_pair(db)
    store.prioritize([priority])
    with pytest.raises(ValueError):
        store.prioritize(bad)
    assert db.execute('SELECT article_id FROM marketdesk_pdf_queue WHERE priority=1').fetchall() == [(priority,)]


def test_priority_configuration_is_atomic_and_can_be_cleared(db):
    ordinary, priority = priority_pair(db)
    store.prioritize([priority])
    db.execute("CREATE TRIGGER fail_priority BEFORE UPDATE ON marketdesk_pdf_queue WHEN NEW.article_id='Older000001' AND NEW.priority=1 BEGIN SELECT RAISE(ABORT,'failed priority'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.prioritize([ordinary])
    assert db.execute('SELECT article_id FROM marketdesk_pdf_queue WHERE priority=1').fetchall() == [(priority,)]
    assert store.prioritize([]) == 0
    assert store.claim_next(now=NOW).article_id == ordinary


def test_priority_selection_refresh_precedes_unrelated_stale_batches(db):
    for i in range(101):
        published(db, f'Batch{i:07d}')
    store.seed_missing(now=NOW)
    store.prioritize(['Batch0000100'])
    assert store.claim_next(now=NOW).article_id == 'Batch0000100'


def test_priority_update_between_scan_and_claim_is_rechecked(db, monkeypatch):
    _, priority = priority_pair(db)
    execute = store.hrana_execute
    def change(sql, args=(), **kwargs):
        result = execute(sql, args, **kwargs)
        if sql.startswith('UPDATE marketdesk_pdf_throttle SET next_attempt_at='):
            store.prioritize([priority])
        return result
    monkeypatch.setattr(store, 'hrana_execute', change)
    assert store.claim_next(now=NOW).article_id == priority


def test_priority_migration_preserves_existing_queue_and_clock():
    conn = sqlite3.connect(':memory:')
    for name in ('0001_init.sql', '0103_marketdesk_pdf_queue.sql', '0104_marketdesk_pdf_account_backoff.sql'):
        conn.executescript((ROOT/name).read_text())
    conn.execute("INSERT INTO marketdesk_pdf_queue(article_id,article_url,published_at,next_attempt_at,first_seen_at,updated_at) VALUES('Priority001','source','published','retry','seen','updated')")
    before = conn.execute('SELECT * FROM marketdesk_pdf_throttle').fetchall()
    conn.executescript((ROOT/'0105_marketdesk_pdf_priority.sql').read_text())
    assert conn.execute('SELECT next_attempt_at,priority FROM marketdesk_pdf_queue').fetchone() == ('retry', 0)
    assert conn.execute('SELECT * FROM marketdesk_pdf_throttle').fetchall() == before
    conn.close()
