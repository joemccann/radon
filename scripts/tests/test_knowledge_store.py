"""Knowledge store (Phase 0) — upsert idempotency, FTS mirror sync, pruning.

Runs against a LOCAL libsql :memory: database: libsql_experimental 0.0.55
supports FTS5 + vector32 locally (verified in the Phase 0 spike), so the
real 0028 migration is applied verbatim. No network, no get_db() — the
PYTEST_CURRENT_TEST guard never comes into play.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import libsql_experimental as libsql
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from knowledge.schema import KnowledgeDoc, content_hash  # noqa: E402
from knowledge.store import delete_source_docs, upsert_documents  # noqa: E402

_MIGRATION = _SCRIPTS_DIR / "db" / "migrations" / "0028_knowledge.sql"
_BOOTSTRAP_SQL = (
    "CREATE TABLE IF NOT EXISTS schema_migrations "
    "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
)

EMBEDDING_DIM = 384


def _split_statements(sql: str) -> list[str]:
    stripped = "\n".join(re.sub(r"^\s*--.*$", "", line) for line in sql.splitlines())
    return [s.strip() for s in re.split(r";\s*$", stripped, flags=re.MULTILINE) if s.strip()]


@pytest.fixture
def db():
    conn = libsql.connect(":memory:")
    conn.execute(_BOOTSTRAP_SQL)
    for stmt in _split_statements(_MIGRATION.read_text(encoding="utf-8")):
        conn.execute(stmt)
    conn.commit()
    return conn


def _days_ago(days: int) -> str:
    stamp = datetime.now(timezone.utc) - timedelta(days=days)
    return stamp.isoformat().replace("+00:00", "Z")


def _unit_vector(hot_ix: int) -> list[float]:
    values = [0.0] * EMBEDDING_DIM
    values[hot_ix] = 1.0
    return values


def _doc(**overrides) -> KnowledgeDoc:
    base = dict(
        source="docs",
        scope="ops",
        doc_key="tasks/lessons.md",
        chunk_ix=0,
        title="Lessons",
        summary="relay farm-down fix",
        content="the relay farm-down was fixed by a bounded reconnect ladder",
        metadata={"path": "tasks/lessons.md"},
        last_activity_at=_days_ago(1),
    )
    base.update(overrides)
    return KnowledgeDoc(**base)


def _knowledge_rows(db):
    return db.execute(
        "SELECT id, source, scope, doc_key, chunk_ix, title, summary, content, "
        "metadata, content_hash, created_at, last_activity_at FROM knowledge ORDER BY id"
    ).fetchall()


def _fts_ids(db, token: str) -> list[int]:
    rows = db.execute(
        "SELECT rowid FROM knowledge_fts WHERE knowledge_fts MATCH ?", (f'"{token}"',)
    ).fetchall()
    return [row[0] for row in rows]


class TestContentHash:
    def test_field_boundaries_cannot_collide(self):
        shifted_boundary = _doc(content="x|", summary="y")
        other_boundary = _doc(content="x", summary="|y")

        assert shifted_boundary.content_hash() != other_boundary.content_hash()

    def test_title_scope_and_metadata_changes_change_the_hash(self):
        base = _doc()

        assert _doc(title="Renamed").content_hash() != base.content_hash()
        assert _doc(scope="trading").content_hash() != base.content_hash()
        assert _doc(metadata={"path": "other.md"}).content_hash() != base.content_hash()


class TestUpsertDocuments:
    def test_insert_writes_row_and_fts_mirror(self, db):
        counts = upsert_documents(db, [_doc()])

        assert counts == {"inserted": 1, "updated": 0, "skipped": 0, "pruned": 0}
        rows = _knowledge_rows(db)
        assert len(rows) == 1
        row_id = rows[0][0]
        assert rows[0][3] == "tasks/lessons.md"
        assert rows[0][9] == content_hash(
            "docs", "ops", "tasks/lessons.md", 0, "Lessons",
            "relay farm-down fix",
            "the relay farm-down was fixed by a bounded reconnect ladder",
            {"path": "tasks/lessons.md"},
        )
        assert rows[0][10]  # created_at set
        assert _fts_ids(db, "reconnect") == [row_id]

    def test_reupsert_unchanged_is_skipped_with_no_write(self, db):
        upsert_documents(db, [_doc()])
        before = _knowledge_rows(db)

        counts = upsert_documents(db, [_doc(last_activity_at=_days_ago(0))])

        assert counts == {"inserted": 0, "updated": 0, "skipped": 1, "pruned": 0}
        assert _knowledge_rows(db) == before  # last_activity_at NOT bumped

    def test_changed_title_updates_row_and_fts(self, db):
        upsert_documents(db, [_doc()])

        counts = upsert_documents(db, [_doc(title="Postmortems")])

        assert counts == {"inserted": 0, "updated": 1, "skipped": 0, "pruned": 0}
        rows = _knowledge_rows(db)
        assert rows[0][5] == "Postmortems"
        assert _fts_ids(db, "Postmortems") == [rows[0][0]]

    def test_changed_content_updates_row_and_fts(self, db):
        upsert_documents(db, [_doc()])
        created_at_before = _knowledge_rows(db)[0][10]

        changed = _doc(
            content="the relay now escalates to a lock-held gateway restart",
            last_activity_at=_days_ago(0),
        )
        counts = upsert_documents(db, [changed])

        assert counts == {"inserted": 0, "updated": 1, "skipped": 0, "pruned": 0}
        rows = _knowledge_rows(db)
        assert len(rows) == 1
        assert rows[0][7] == changed.content
        assert rows[0][9] == changed.content_hash()
        assert rows[0][10] == created_at_before  # created_at preserved
        assert rows[0][11] == changed.last_activity_at
        assert _fts_ids(db, "escalates") == [rows[0][0]]
        assert _fts_ids(db, "reconnect") == []  # stale mirror row replaced

    def test_multiple_chunks_of_same_doc_coexist(self, db):
        counts = upsert_documents(
            db,
            [
                _doc(chunk_ix=0, content="chunk zero body"),
                _doc(chunk_ix=1, content="chunk one body"),
            ],
        )

        assert counts["inserted"] == 2
        chunk_ixs = [row[4] for row in _knowledge_rows(db)]
        assert chunk_ixs == [0, 1]

    def test_embedding_round_trips_as_f32_blob(self, db):
        upsert_documents(db, [_doc(embedding=_unit_vector(3))])

        blob = db.execute("SELECT embedding FROM knowledge").fetchone()[0]
        assert isinstance(blob, bytes)
        assert len(blob) == EMBEDDING_DIM * 4

    def test_unchanged_doc_with_new_embedding_backfills_over_null(self, db):
        """Recovery path for FTS-only rows written while the embedder was
        unavailable: an identical doc arriving WITH a vector must store it."""
        upsert_documents(db, [_doc()])
        before = _knowledge_rows(db)

        counts = upsert_documents(
            db, [_doc(embedding=_unit_vector(3), last_activity_at=_days_ago(0))]
        )

        assert counts == {"inserted": 0, "updated": 1, "skipped": 0, "pruned": 0}
        blob = db.execute("SELECT embedding FROM knowledge").fetchone()[0]
        assert isinstance(blob, bytes)
        assert len(blob) == EMBEDDING_DIM * 4
        assert _knowledge_rows(db) == before  # only the vector changed; no activity bump

    def test_unchanged_doc_with_stored_embedding_stays_skipped(self, db):
        upsert_documents(db, [_doc(embedding=_unit_vector(3))])

        counts = upsert_documents(db, [_doc(embedding=_unit_vector(5))])

        assert counts == {"inserted": 0, "updated": 0, "skipped": 1, "pruned": 0}

    def test_changed_doc_without_embedding_clears_stale_embedding(self, db):
        upsert_documents(db, [_doc(embedding=_unit_vector(3))])

        upsert_documents(db, [_doc(content="new body, not yet re-embedded")])

        blob = db.execute("SELECT embedding FROM knowledge").fetchone()[0]
        assert blob is None

    def test_shrinking_doc_prunes_trailing_chunks_and_fts(self, db):
        upsert_documents(
            db,
            [
                _doc(chunk_ix=0, content="chunk zero body"),
                _doc(chunk_ix=1, content="chunk one body"),
                _doc(chunk_ix=2, content="obsolete dampened oscillator"),
            ],
        )

        counts = upsert_documents(
            db,
            [
                _doc(chunk_ix=0, content="chunk zero body"),
                _doc(chunk_ix=1, content="chunk one body"),
            ],
        )

        assert counts == {"inserted": 0, "updated": 0, "skipped": 2, "pruned": 1}
        chunk_ixs = [row[4] for row in _knowledge_rows(db)]
        assert chunk_ixs == [0, 1]
        assert _fts_ids(db, "oscillator") == []

    def test_pruning_scoped_to_emitted_doc(self, db):
        upsert_documents(
            db,
            [
                _doc(doc_key="other.md", chunk_ix=0, content="other doc chunk zero"),
                _doc(doc_key="other.md", chunk_ix=1, content="other doc chunk one"),
            ],
        )

        counts = upsert_documents(db, [_doc(chunk_ix=0)])

        assert counts["pruned"] == 0
        assert len(_knowledge_rows(db)) == 3


class TestDeleteSourceDocs:
    def test_prunes_rows_and_fts_for_missing_keys(self, db):
        upsert_documents(
            db,
            [
                _doc(doc_key="gone.md", content="obsolete dampened oscillator"),
                _doc(doc_key="kept.md", content="still current reconnect notes"),
                _doc(source="newsfeed", doc_key="gone.md", content="same key other source"),
            ],
        )

        deleted = delete_source_docs(db, "docs", ["gone.md"])

        assert deleted == 1
        remaining = [(row[1], row[3]) for row in _knowledge_rows(db)]
        assert ("docs", "gone.md") not in remaining
        assert ("docs", "kept.md") in remaining
        assert ("newsfeed", "gone.md") in remaining  # other source untouched
        assert _fts_ids(db, "oscillator") == []

    def test_empty_key_list_is_a_noop(self, db):
        upsert_documents(db, [_doc()])

        assert delete_source_docs(db, "docs", []) == 0
        assert len(_knowledge_rows(db)) == 1
