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

Callers pass the connection explicitly (bounded transactional Hrana HTTP in
the ingest CLI, a local libsql :memory: DB in tests). HTTP connections queue
SQL predicates and cleanup in a single atomic request. The HTTP path also
repairs a missing FTS mirror without changing a hash-identical canonical row.
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
    if hasattr(db, "execute_transaction"):
        return _upsert_documents_http(db, list(docs))
    counts = {"inserted": 0, "updated": 0, "skipped": 0}
    last_chunk_ix: dict[tuple[str, str], int] = {}
    try:
        # Reserve the writer before reading: a deferred transaction's read
        # snapshot cannot be upgraded after a concurrent writer commits.
        db.execute("BEGIN IMMEDIATE")
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
    except BaseException:
        # BEGIN can fail before a transaction exists, or a dead stream can
        # reject rollback too. Preserve the write failure for retry policy.
        try:
            db.rollback()
        except Exception:
            pass
        raise
    return counts


def delete_source_docs(db, source: str, missing_doc_keys: Iterable[str]) -> int:
    """Prune all chunks of the given source's documents whose doc_key is in
    missing_doc_keys (i.e. no longer emitted by the connector). Returns the
    number of chunk rows deleted."""
    doc_keys = list(missing_doc_keys)
    if not doc_keys:
        return 0
    if hasattr(db, "execute_transaction"):
        return _delete_source_docs_http(db, source, doc_keys)
    key_marks = ", ".join("?" for _ in doc_keys)
    try:
        # Reserve the writer before reading: a deferred transaction's read
        # snapshot cannot be upgraded after a concurrent writer commits.
        db.execute("BEGIN IMMEDIATE")
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
    except BaseException:
        # BEGIN can fail before a transaction exists, or a dead stream can
        # reject rollback too. Preserve the write failure for retry policy.
        try:
            db.rollback()
        except Exception:
            pass
        raise
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


# HTTP writes are an entire server-side transaction, never a rotating-baton
# conversation. Initial SELECT receipts drive accounting only; every mutation
# uses SQL predicates so all work and cleanup can be queued in one request.
def _upsert_documents_http(db, docs: list[KnowledgeDoc]) -> dict[str, int]:
    counts = {"inserted": 0, "updated": 0, "skipped": 0, "pruned": 0}
    groups: dict[tuple[str, str], list[KnowledgeDoc]] = {}
    for doc in docs:
        groups.setdefault((doc.source, doc.doc_key), []).append(doc)
    if not groups:
        return counts
    statements, snapshots = [], []
    for key, chunks in groups.items():
        snapshots.append((len(statements), chunks))
        statements.append((
            "SELECT id, chunk_ix, content_hash, embedding IS NOT NULL FROM knowledge "
            "WHERE source = ? AND doc_key = ?", key,
        ))
        for doc in chunks:
            identity = (doc.source, doc.doc_key, doc.chunk_ix)
            digest = doc.content_hash()
            statements.append((
                "DELETE FROM knowledge_fts WHERE rowid IN (SELECT id FROM knowledge "
                "WHERE source = ? AND doc_key = ? AND chunk_ix = ? AND content_hash != ?)",
                identity + (digest,),
            ))
            now = _now_iso()
            args = (doc.source, doc.scope, doc.doc_key, doc.chunk_ix, doc.title,
                    doc.summary, doc.content, _metadata_json(doc))
            tail = (digest, doc.created_at or now, doc.last_activity_at or now)
            if doc.embedding is None:
                insert, args = _INSERT_NO_EMBEDDING_SQL, args + tail
            else:
                insert = _INSERT_WITH_EMBEDDING_SQL
                args += (json.dumps(doc.embedding),) + tail
            # Preserve id and created_at; changed content clears a stale vector
            # when the incoming document has no embedding.
            statements.append((insert + " ON CONFLICT(source, doc_key, chunk_ix) DO UPDATE SET "
                "scope=excluded.scope, title=excluded.title, summary=excluded.summary, "
                "content=excluded.content, metadata=excluded.metadata, "
                "embedding=excluded.embedding, content_hash=excluded.content_hash, "
                "last_activity_at=excluded.last_activity_at "
                "WHERE knowledge.content_hash != excluded.content_hash", args))
            if doc.embedding is not None:
                statements.append((
                    "UPDATE knowledge SET embedding=vector32(?) WHERE source=? "
                    "AND doc_key=? AND chunk_ix=? AND content_hash=? AND embedding IS NULL",
                    (json.dumps(doc.embedding),) + identity + (digest,),
                ))
            # Changed mirrors were removed above; unchanged/backfill mirrors
            # remain untouched. A previously missing mirror is safely repaired.
            statements.append((
                "INSERT INTO knowledge_fts(rowid,title,summary,content) "
                "SELECT id,title,summary,content FROM knowledge WHERE source=? "
                "AND doc_key=? AND chunk_ix=? AND NOT EXISTS "
                "(SELECT 1 FROM knowledge_fts WHERE rowid=knowledge.id)", identity,
            ))
        max_ix = max(doc.chunk_ix for doc in chunks)
        predicate = "source=? AND doc_key=? AND chunk_ix>?"
        args = key + (max_ix,)
        statements.append((
            "DELETE FROM knowledge_fts WHERE rowid IN (SELECT id FROM knowledge WHERE "
            + predicate + ")", args,
        ))
        statements.append(("DELETE FROM knowledge WHERE " + predicate, args))
    receipts = db.execute_transaction(statements)
    for index, chunks in snapshots:
        before = {row[1]: (row[2], bool(row[3])) for row in receipts[index].fetchall()}
        max_ix = max(doc.chunk_ix for doc in chunks)
        counts["pruned"] += sum(ix > max_ix for ix in before)
        for doc in chunks:
            digest = doc.content_hash()
            old = before.get(doc.chunk_ix)
            if old is None:
                counts["inserted"] += 1
                before[doc.chunk_ix] = (digest, doc.embedding is not None)
            elif old[0] != digest:
                counts["updated"] += 1
                before[doc.chunk_ix] = (digest, doc.embedding is not None)
            elif not old[1] and doc.embedding is not None:
                counts["updated"] += 1
                before[doc.chunk_ix] = (digest, True)
            else:
                counts["skipped"] += 1
    return counts


def _delete_source_docs_http(db, source: str, doc_keys: list[str]) -> int:
    marks = ",".join("?" for _ in doc_keys)
    predicate = f"source=? AND doc_key IN ({marks})"
    args = (source, *doc_keys)
    receipts = db.execute_transaction([
        ("SELECT id FROM knowledge WHERE " + predicate, args),
        ("DELETE FROM knowledge_fts WHERE rowid IN (SELECT id FROM knowledge WHERE "
         + predicate + ")", args),
        ("DELETE FROM knowledge WHERE " + predicate, args),
    ])
    return len(receipts[0].fetchall())
