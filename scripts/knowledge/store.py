"""Writers for the `knowledge` table and its FTS5 mirror.

The mirror (knowledge_fts, rowid = knowledge.id) stores its own copy of
title/summary/content and is kept in sync HERE, not by triggers: the Phase 0
spike only proved plain AFTER INSERT triggers against remote Turso, and the
external-content update/delete trigger bodies would need multi-statement
BEGIN blocks that migrate.py's end-of-line `;` splitter cannot carry.

Idempotency key is content_hash — unchanged docs are skipped entirely (no
last_activity_at bump, no FTS churn); changed docs are updated in place. A
changed doc arriving without an embedding clears the stored one, since an
embedding of superseded content is worse than none. The one exception to the
hash-equal skip: an unchanged doc arriving WITH a vector where the stored row
has none gets an embedding-only backfill (recovery from FTS-only degraded
ingests), still without an activity bump or FTS churn.

Callers pass the connection explicitly (sync libsql via get_db() in oneshot
ingest scripts, a local libsql :memory: DB in tests).
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Iterable

from .schema import KnowledgeDoc

_SELECT_EXISTING_SQL = (
    "SELECT id, content_hash, embedding IS NOT NULL FROM knowledge "
    "WHERE source = ? AND doc_key = ? AND chunk_ix = ?"
)

_INSERT_COLUMNS = (
    "source, scope, doc_key, chunk_ix, title, summary, content, metadata, "
    "embedding, content_hash, created_at, last_activity_at"
)
_INSERT_WITH_EMBEDDING_SQL = (
    f"INSERT INTO knowledge ({_INSERT_COLUMNS}) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, vector32(?), ?, ?, ?)"
)
_INSERT_NO_EMBEDDING_SQL = (
    f"INSERT INTO knowledge ({_INSERT_COLUMNS}) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)"
)

_UPDATE_SET_COLUMNS = (
    "scope = ?, title = ?, summary = ?, content = ?, metadata = ?, "
    "content_hash = ?, last_activity_at = ?"
)
_UPDATE_WITH_EMBEDDING_SQL = (
    f"UPDATE knowledge SET {_UPDATE_SET_COLUMNS}, embedding = vector32(?) WHERE id = ?"
)
_UPDATE_NO_EMBEDDING_SQL = (
    f"UPDATE knowledge SET {_UPDATE_SET_COLUMNS}, embedding = NULL WHERE id = ?"
)

_BACKFILL_EMBEDDING_SQL = "UPDATE knowledge SET embedding = vector32(?) WHERE id = ?"

_FTS_DELETE_SQL = "DELETE FROM knowledge_fts WHERE rowid = ?"
_FTS_INSERT_SQL = "INSERT INTO knowledge_fts (rowid, title, summary, content) VALUES (?, ?, ?, ?)"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def upsert_documents(db, docs: Iterable[KnowledgeDoc]) -> dict[str, int]:
    """Idempotent upsert keyed on content_hash. Connectors emit every chunk of
    a document together, so chunks stored beyond the highest emitted chunk_ix
    are superseded and pruned. Returns
    {"inserted": n, "updated": n, "skipped": n, "pruned": n}."""
    counts = {"inserted": 0, "updated": 0, "skipped": 0}
    last_chunk_ix: dict[tuple[str, str], int] = {}
    for doc in docs:
        doc_id = (doc.source, doc.doc_key)
        last_chunk_ix[doc_id] = max(last_chunk_ix.get(doc_id, -1), doc.chunk_ix)
        digest = doc.content_hash()
        existing = db.execute(
            _SELECT_EXISTING_SQL, (doc.source, doc.doc_key, doc.chunk_ix)
        ).fetchone()
        if existing is not None and existing[1] == digest:
            if doc.embedding is not None and not existing[2]:
                _backfill_embedding(db, existing[0], doc)
                counts["updated"] += 1
            else:
                counts["skipped"] += 1
        elif existing is not None:
            _update_document(db, existing[0], doc, digest)
            counts["updated"] += 1
        else:
            _insert_document(db, doc, digest)
            counts["inserted"] += 1
    counts["pruned"] = _prune_trailing_chunks(db, last_chunk_ix)
    db.commit()
    return counts


def delete_source_docs(db, source: str, missing_doc_keys: Iterable[str]) -> int:
    """Prune all chunks of the given source's documents whose doc_key is in
    missing_doc_keys (i.e. no longer emitted by the connector). Returns the
    number of chunk rows deleted."""
    doc_keys = list(missing_doc_keys)
    if not doc_keys:
        return 0
    key_marks = ", ".join("?" for _ in doc_keys)
    id_rows = db.execute(
        f"SELECT id FROM knowledge WHERE source = ? AND doc_key IN ({key_marks})",
        (source, *doc_keys),
    ).fetchall()
    ids = [row[0] for row in id_rows]
    if ids:
        id_marks = ", ".join("?" for _ in ids)
        db.execute(f"DELETE FROM knowledge_fts WHERE rowid IN ({id_marks})", tuple(ids))
        db.execute(f"DELETE FROM knowledge WHERE id IN ({id_marks})", tuple(ids))
    db.commit()
    return len(ids)


def _prune_trailing_chunks(db, last_chunk_ix: dict[tuple[str, str], int]) -> int:
    pruned = 0
    for (source, doc_key), last_ix in last_chunk_ix.items():
        id_rows = db.execute(
            "SELECT id FROM knowledge WHERE source = ? AND doc_key = ? AND chunk_ix > ?",
            (source, doc_key, last_ix),
        ).fetchall()
        ids = [row[0] for row in id_rows]
        if ids:
            id_marks = ", ".join("?" for _ in ids)
            db.execute(f"DELETE FROM knowledge_fts WHERE rowid IN ({id_marks})", tuple(ids))
            db.execute(f"DELETE FROM knowledge WHERE id IN ({id_marks})", tuple(ids))
        pruned += len(ids)
    return pruned


def _insert_document(db, doc: KnowledgeDoc, digest: str) -> None:
    now = _now_iso()
    shared_args = (
        doc.source, doc.scope, doc.doc_key, doc.chunk_ix,
        doc.title, doc.summary, doc.content, _metadata_json(doc),
    )
    tail_args = (digest, doc.created_at or now, doc.last_activity_at or now)
    if doc.embedding is not None:
        cursor = db.execute(
            _INSERT_WITH_EMBEDDING_SQL,
            shared_args + (json.dumps(doc.embedding),) + tail_args,
        )
    else:
        cursor = db.execute(_INSERT_NO_EMBEDDING_SQL, shared_args + tail_args)
    _refresh_fts_row(db, cursor.lastrowid, doc)


def _update_document(db, row_id: int, doc: KnowledgeDoc, digest: str) -> None:
    shared_args = (
        doc.scope, doc.title, doc.summary, doc.content, _metadata_json(doc),
        digest, doc.last_activity_at or _now_iso(),
    )
    if doc.embedding is not None:
        db.execute(_UPDATE_WITH_EMBEDDING_SQL, shared_args + (json.dumps(doc.embedding), row_id))
    else:
        db.execute(_UPDATE_NO_EMBEDDING_SQL, shared_args + (row_id,))
    _refresh_fts_row(db, row_id, doc)


def _backfill_embedding(db, row_id: int, doc: KnowledgeDoc) -> None:
    db.execute(_BACKFILL_EMBEDDING_SQL, (json.dumps(doc.embedding), row_id))


def _refresh_fts_row(db, row_id: int, doc: KnowledgeDoc) -> None:
    db.execute(_FTS_DELETE_SQL, (row_id,))
    db.execute(_FTS_INSERT_SQL, (row_id, doc.title, doc.summary, doc.content))


def _metadata_json(doc: KnowledgeDoc) -> str | None:
    return json.dumps(doc.metadata) if doc.metadata is not None else None
