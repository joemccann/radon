"""marketdesk_articles ledger: claim, crash heal, failure cap, Dropbox upload outbox."""
from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research import marketdesk_store as store  # noqa: E402

ROOT = Path(__file__).resolve().parents[1] / "db/migrations"
NOW = datetime(2026, 10, 26, 15, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class Ref:
    article_id: str
    url: str
    published_at: datetime
    title: str


@dataclass(frozen=True)
class Upload:
    path_lower: str
    id: str
    rev: str
    content_hash: str
    already_present: bool = False


def ref(article_id="4AJs5Adl2Yp", hours_ago=1, title="Fed cuts"):
    return Ref(article_id, "https://marketdesk.ai/library/browse/" + article_id, NOW - timedelta(hours=hours_ago), title)


@pytest.fixture
def db(monkeypatch):
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.executescript((ROOT / "0001_init.sql").read_text())
    conn.executescript((ROOT / "0071_research_post_sources.sql").read_text())
    conn.executescript((ROOT / "0102_marketdesk_articles.sql").read_text())
    calls = []

    def execute(sql, args=(), timeout=4.0):
        assert 0 < timeout <= 10
        calls.append((sql, tuple(args)))
        return [tuple(r) for r in conn.execute(sql, tuple(args)).fetchall()]

    monkeypatch.setattr(store, "hrana_execute", execute)
    yield SimpleNamespace(execute=conn.execute, calls=calls)
    conn.close()


def row(db, article_id="4AJs5Adl2Yp"):
    cur = db.execute("SELECT * FROM marketdesk_articles WHERE article_id=?", (article_id,))
    values = cur.fetchone()
    return dict(zip([c[0] for c in cur.description], values)) if values else None


def test_claim_new_inserts_pending_row(db):
    assert store.claim(ref()) is True
    saved = row(db)
    assert saved["status"] == "pending" and saved["attempts"] == 0
    assert saved["title"] == "Fed cuts"
    assert saved["article_url"] == "https://marketdesk.ai/library/browse/4AJs5Adl2Yp"
    assert datetime.fromisoformat(saved["published_at"]) == NOW - timedelta(hours=1)
    assert saved["first_seen_at"] and saved["updated_at"]
    assert all("marketdesk_articles" in sql for sql, _ in db.calls)


def test_claim_pending_retry_true_published_and_failed_false(db):
    assert store.claim(ref()) and store.claim(ref())
    store.record_published("4AJs5Adl2Yp", "research-" + "a" * 32, "/api/newsfeed/research/files/" + "b" * 64 + ".pdf", "b" * 64)
    assert store.claim(ref()) is False
    assert store.claim(ref("Zz99Yy88Xx7")) is True
    for _ in range(5):
        store.record_failure("Zz99Yy88Xx7", "boom")
    assert store.claim(ref("Zz99Yy88Xx7")) is False


def test_claim_does_not_overwrite_existing_row(db):
    store.claim(ref(title="Original"))
    store.record_failure("4AJs5Adl2Yp", "x")
    store.claim(ref(title="Changed"))
    saved = row(db)
    assert saved["title"] == "Original" and saved["attempts"] == 1


def test_record_failure_counts_and_flips_at_five(db):
    store.claim(ref())
    attempts = [store.record_failure("4AJs5Adl2Yp", f"err {i}") for i in range(5)]
    assert attempts == [1, 2, 3, 4, 5]
    saved = row(db)
    assert saved["status"] == "failed" and saved["last_error"] == "err 4"


def test_record_failure_never_demotes_published(db):
    store.claim(ref())
    store.record_published("4AJs5Adl2Yp", "research-" + "a" * 32, "/x.pdf", "b" * 64)
    store.record_failure("4AJs5Adl2Yp", "late")
    assert row(db)["status"] == "published"


def test_record_failure_truncates_error(db):
    store.claim(ref())
    store.record_failure("4AJs5Adl2Yp", "x" * 1000)
    assert len(row(db)["last_error"]) == 300


def test_unseen_excludes_published_failed_old_and_duplicates(db):
    fresh, done, dead, old = ref("Aaaaaaaa1"), ref("Bbbbbbbb2"), ref("Cccccccc3"), ref("Dddddddd4", hours_ago=49)
    pending = ref("Eeeeeeee5")
    for r in (done, dead, pending):
        store.claim(r)
    store.record_published(done.article_id, "research-" + "a" * 32, "/x.pdf", "b" * 64)
    for _ in range(5):
        store.record_failure(dead.article_id, "e")
    result = store.unseen([fresh, done, dead, old, pending, fresh], lookback_hours=48, now=NOW)
    assert [r.article_id for r in result] == ["Aaaaaaaa1", "Eeeeeeee5"]


def test_unseen_empty_makes_no_query(db):
    assert store.unseen([], lookback_hours=48, now=NOW) == []
    assert db.calls == []


def test_post_exists_reads_research_post_sources(db):
    post_id = "research-" + "c" * 32
    assert store.post_exists(post_id) is False
    db.execute("INSERT INTO posts (id,title,content,timestamp,images,tags,created_at,updated_at) "
               "VALUES (?,?,?,?,?,?,?,?)", (post_id, "t", "c", "2026-10-26T00:00:00Z", "[]", "[]", "x", "x"))
    db.execute("INSERT INTO research_post_sources(post_id,provenance_json) VALUES (?,?)",
               (post_id, json.dumps({"kind": "marketdesk", "url": "/u.pdf", "contentHash": "d" * 64})))
    assert store.post_exists(post_id) is True
    assert store.post_source(post_id)["contentHash"] == "d" * 64
    assert store.post_source("research-" + "e" * 32) is None


def test_record_published_sets_fields_and_clears_error(db):
    store.claim(ref())
    store.record_failure("4AJs5Adl2Yp", "transient")
    store.record_published("4AJs5Adl2Yp", "research-" + "a" * 32, "/api/newsfeed/research/files/" + "b" * 64 + ".pdf", "b" * 64)
    saved = row(db)
    assert saved["status"] == "published" and saved["post_id"] == "research-" + "a" * 32
    assert saved["pdf_asset"].endswith(".pdf") and saved["pdf_sha256"] == "b" * 64
    assert saved["last_error"] is None


def _published(db, article_id, hours_ago):
    store.claim(ref(article_id, hours_ago=hours_ago, title="T " + article_id))
    store.record_published(article_id, "research-" + "a" * 32, "/api/newsfeed/research/files/" + "b" * 64 + ".pdf", "b" * 64)


def test_pending_uploads_filter_and_order(db):
    _published(db, "Aaaaaaaa1", 3)
    _published(db, "Bbbbbbbb2", 5)
    _published(db, "Cccccccc3", 4)
    _published(db, "Dddddddd4", 6)
    _published(db, "Eeeeeeee5", 7)
    store.claim(ref("Ffffffff6"))  # pending, never uploaded
    store.record_dropbox("Cccccccc3", "uploaded", Upload("/radon/x.pdf", "id:1", "r1", "h" * 64))
    store.record_dropbox("Dddddddd4", "skipped_no_creds", None)
    store.record_dropbox("Eeeeeeee5", "failed", None, error="conflict")
    rows = store.pending_uploads(limit=10)
    ids = [r["article_id"] for r in rows]
    assert set(ids) == {"Aaaaaaaa1", "Bbbbbbbb2", "Dddddddd4", "Eeeeeeee5"}
    # Never-attempted and skipped rows first (oldest first); failed retries last so a stuck row cannot starve.
    assert ids == ["Dddddddd4", "Bbbbbbbb2", "Aaaaaaaa1", "Eeeeeeee5"]
    first = rows[0]
    assert set(first) >= {"article_id", "post_id", "title", "published_at", "pdf_asset", "pdf_sha256", "dropbox_status"}
    assert first["title"] == "T Dddddddd4"
    assert len(store.pending_uploads(limit=2)) == 2


def test_record_dropbox_uploaded_stores_result(db):
    _published(db, "Aaaaaaaa1", 1)
    store.record_dropbox("Aaaaaaaa1", "failed", None, error="network " * 100)
    assert len(row(db, "Aaaaaaaa1")["last_error"]) == 300
    store.record_dropbox("Aaaaaaaa1", "uploaded", Upload("/radon/marketdesk/2026/x.pdf", "id:abc", "015f", "h" * 64))
    saved = row(db, "Aaaaaaaa1")
    assert saved["dropbox_status"] == "uploaded"
    assert (saved["dropbox_path"], saved["dropbox_id"], saved["dropbox_rev"], saved["dropbox_content_hash"]) == (
        "/radon/marketdesk/2026/x.pdf", "id:abc", "015f", "h" * 64)
    assert saved["status"] == "published"


def test_record_dropbox_rejects_unknown_status(db):
    with pytest.raises(ValueError):
        store.record_dropbox("Aaaaaaaa1", "uploading", None)
    with pytest.raises(ValueError):
        store.record_dropbox("Aaaaaaaa1", "uploaded", None)


def test_claim_counts_an_interrupted_attempt_and_retires_at_five(db):
    """A run killed mid-article (systemd timeout) leaves its lease; the next claim counts it."""
    assert store.claim(ref()) is True
    assert row(db)["attempts"] == 0 and row(db)["last_error"] == store.LEASE
    for expected in (1, 2, 3, 4):
        assert store.claim(ref()) is True
        assert row(db)["attempts"] == expected
    assert store.claim(ref()) is False
    saved = row(db)
    assert saved["status"] == "failed" and saved["attempts"] == 5 and "interrupted" in saved["last_error"]


def test_finished_attempts_do_not_leave_a_lease(db):
    store.claim(ref())
    store.record_failure("4AJs5Adl2Yp", "no_pdf")
    assert store.claim(ref()) is True
    assert row(db)["attempts"] == 1


def test_park_keeps_the_attempt_budget(db):
    store.claim(ref())
    store.park("4AJs5Adl2Yp", "http_503 " + "x" * 1000)
    saved = row(db)
    assert saved["attempts"] == 0 and saved["status"] == "pending" and saved["last_error"].startswith("http_503")
    assert len(saved["last_error"]) == 300
    assert store.claim(ref()) is True and row(db)["attempts"] == 0


def test_park_never_touches_published(db):
    store.claim(ref())
    store.record_published("4AJs5Adl2Yp", "research-" + "a" * 32, "/x.pdf", "b" * 64)
    store.park("4AJs5Adl2Yp", "late")
    assert row(db)["status"] == "published" and row(db)["last_error"] is None


def test_newest_known_published_at(db):
    assert store.newest_known_published_at() is None
    store.claim(ref("Aaaaaaaa1", hours_ago=5))
    store.claim(ref("Bbbbbbbb2", hours_ago=2))
    assert store.newest_known_published_at() == NOW - timedelta(hours=2)
