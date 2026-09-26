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
_SELECT_EXISTING_V2_SQL = (
    "SELECT id, content_hash, embedding IS NOT NULL, embedding_v2 IS NOT NULL FROM knowledge "
    "WHERE source = ? AND doc_key = ? AND chunk_ix = ?"
)
_HTTP_SELECT = (
    "SELECT id, chunk_ix, content_hash, embedding IS NOT NULL FROM knowledge "
    "WHERE source = ? AND doc_key = ?"
)
_HTTP_SELECT_V2 = (
    "SELECT id, chunk_ix, content_hash, embedding IS NOT NULL, embedding_v2 IS NOT NULL "
    "FROM knowledge WHERE source = ? AND doc_key = ?"
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
_BACKFILL_EMBEDDING_V2_SQL = (
    "UPDATE knowledge SET embedding_v2 = vector32(?) WHERE id = ? AND embedding_v2 IS NULL"
)

_FTS_DELETE_SQL = "DELETE FROM knowledge_fts WHERE rowid = ?"
_FTS_INSERT_SQL = "INSERT INTO knowledge_fts (rowid, title, summary, content) VALUES (?, ?, ?, ?)"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _has_embedding_v2_column(db) -> bool:
    try:
        rows = db.execute("PRAGMA table_info(knowledge)").fetchall()
    except Exception:
        return False
    return any(row[1] == "embedding_v2" for row in rows)


def _need_embedding_v2(docs, db=None) -> bool:
    if any(getattr(doc, "embedding_v2", None) is not None for doc in docs):
        return True
    from knowledge.embed import dual_write_enabled
    if not dual_write_enabled():
        return False
    # The HTTP writer must not open a second stream (PRAGMA) before the
    # atomic batch. v2 SQL is used once a doc actually carries the vector.
    if db is None or hasattr(db, "execute_transaction"):
        return False
    return _has_embedding_v2_column(db)


def upsert_documents(db, docs: Iterable[KnowledgeDoc]) -> dict[str, int]:
    """Idempotent upsert keyed on content_hash. Connectors emit every chunk of
    a document together, so chunks stored beyond the highest emitted chunk_ix
    are superseded and pruned. Returns
    {"inserted": n, "updated": n, "skipped": n, "pruned": n}."""
    docs = list(docs)
    if hasattr(db, "execute_transaction"):
        return _upsert_documents_http(db, docs)
    counts = {"inserted": 0, "updated": 0, "skipped": 0}
    last_chunk_ix: dict[tuple[str, str], int] = {}
    need_v2 = _need_embedding_v2(docs, db)
    select_sql = _SELECT_EXISTING_V2_SQL if need_v2 else _SELECT_EXISTING_SQL
    try:
        # Reserve the writer before reading: a deferred transaction's read
        # snapshot cannot be upgraded after a concurrent writer commits.
        db.execute("BEGIN IMMEDIATE")
        for doc in docs:
            doc_id = (doc.source, doc.doc_key)
            last_chunk_ix[doc_id] = max(last_chunk_ix.get(doc_id, -1), doc.chunk_ix)
            digest = doc.content_hash()
            existing = db.execute(
                select_sql, (doc.source, doc.doc_key, doc.chunk_ix)
            ).fetchone()
            if existing is not None and existing[1] == digest:
                changed = False
                if doc.embedding is not None and not existing[2]:
                    _backfill_embedding(db, existing[0], doc)
                    changed = True
                if need_v2 and doc.embedding_v2 is not None and not existing[3]:
                    backfill_embedding_v2(db, existing[0], doc.embedding_v2)
                    changed = True
                counts["updated" if changed else "skipped"] += 1
            elif existing is not None:
                _update_document(
                    db, existing[0], doc, digest,
                    clear_v2=need_v2 and doc.embedding_v2 is None,
                )
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
    if doc.embedding_v2 is not None:
        emb_sql = "NULL" if doc.embedding is None else "vector32(?)"
        sql = (
            "INSERT INTO knowledge (source, scope, doc_key, chunk_ix, title, summary, "
            "content, metadata, embedding, embedding_v2, content_hash, created_at, last_activity_at) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, {emb_sql}, vector32(?), ?, ?, ?)"
        )
        args = list(shared_args)
        if doc.embedding is not None:
            args.append(json.dumps(doc.embedding))
        args.append(json.dumps(doc.embedding_v2))
        cursor = db.execute(sql, tuple(args) + tail_args)
    elif doc.embedding is not None:
        cursor = db.execute(
            _INSERT_WITH_EMBEDDING_SQL,
            shared_args + (json.dumps(doc.embedding),) + tail_args,
        )
    else:
        cursor = db.execute(_INSERT_NO_EMBEDDING_SQL, shared_args + tail_args)
    _refresh_fts_row(db, cursor.lastrowid, doc)


def _update_document(db, row_id: int, doc: KnowledgeDoc, digest: str, *, clear_v2: bool = False) -> None:
    shared_args = (
        doc.scope, doc.title, doc.summary, doc.content, _metadata_json(doc),
        digest, doc.last_activity_at or _now_iso(),
    )
    if doc.embedding_v2 is None and not clear_v2:
        if doc.embedding is not None:
            db.execute(_UPDATE_WITH_EMBEDDING_SQL, shared_args + (json.dumps(doc.embedding), row_id))
        else:
            db.execute(_UPDATE_NO_EMBEDDING_SQL, shared_args + (row_id,))
    else:
        sets = _UPDATE_SET_COLUMNS
        args = list(shared_args)
        if doc.embedding is not None:
            sets += ", embedding = vector32(?)"
            args.append(json.dumps(doc.embedding))
        else:
            sets += ", embedding = NULL"
        if doc.embedding_v2 is not None:
            sets += ", embedding_v2 = vector32(?)"
            args.append(json.dumps(doc.embedding_v2))
        else:
            sets += ", embedding_v2 = NULL"
        args.append(row_id)
        db.execute(f"UPDATE knowledge SET {sets} WHERE id = ?", tuple(args))
    _refresh_fts_row(db, row_id, doc)


def _backfill_embedding(db, row_id: int, doc: KnowledgeDoc) -> None:
    db.execute(_BACKFILL_EMBEDDING_SQL, (json.dumps(doc.embedding), row_id))


def backfill_embedding_v2(db, row_id: int, vector) -> None:
    db.execute(_BACKFILL_EMBEDDING_V2_SQL, (json.dumps(list(vector)), row_id))


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
    need_v2 = _need_embedding_v2(docs, db)
    statements, snapshots = [], []
    for key, chunks in groups.items():
        snapshots.append((len(statements), chunks))
        statements.append((_HTTP_SELECT_V2 if need_v2 else _HTTP_SELECT, key))
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
            include_v2 = doc.embedding_v2 is not None or (need_v2 and doc.embedding_v2 is None)
            if include_v2:
                insert, args = _http_insert_v2(doc, args, tail)
                v2_assign = "embedding_v2=excluded.embedding_v2, "
            elif doc.embedding is None:
                insert, args = _INSERT_NO_EMBEDDING_SQL, args + tail
                v2_assign = ""
            else:
                insert = _INSERT_WITH_EMBEDDING_SQL
                args += (json.dumps(doc.embedding),) + tail
                v2_assign = ""
            # Preserve id and created_at; changed content clears a stale vector
            # when the incoming document has no embedding.
            statements.append((insert + " ON CONFLICT(source, doc_key, chunk_ix) DO UPDATE SET "
                "scope=excluded.scope, title=excluded.title, summary=excluded.summary, "
                "content=excluded.content, metadata=excluded.metadata, "
                + v2_assign +
                "embedding=excluded.embedding, content_hash=excluded.content_hash, "
                "last_activity_at=excluded.last_activity_at "
                "WHERE knowledge.content_hash != excluded.content_hash", args))
            if doc.embedding is not None:
                statements.append((
                    "UPDATE knowledge SET embedding=vector32(?) WHERE source=? "
                    "AND doc_key=? AND chunk_ix=? AND content_hash=? AND embedding IS NULL",
                    (json.dumps(doc.embedding),) + identity + (digest,),
                ))
            if doc.embedding_v2 is not None:
                statements.append((
                    "UPDATE knowledge SET embedding_v2=vector32(?) WHERE source=? "
                    "AND doc_key=? AND chunk_ix=? AND content_hash=? AND embedding_v2 IS NULL",
                    (json.dumps(doc.embedding_v2),) + identity + (digest,),
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
        rows = receipts[index].fetchall()
        if need_v2:
            before = {row[1]: (row[2], bool(row[3]), bool(row[4])) for row in rows}
        else:
            before = {row[1]: (row[2], bool(row[3]), True) for row in rows}
        max_ix = max(doc.chunk_ix for doc in chunks)
        counts["pruned"] += sum(ix > max_ix for ix in before)
        for doc in chunks:
            digest = doc.content_hash()
            old = before.get(doc.chunk_ix)
            has_v2 = doc.embedding_v2 is not None or not need_v2
            if old is None:
                counts["inserted"] += 1
                before[doc.chunk_ix] = (digest, doc.embedding is not None, has_v2)
            elif old[0] != digest:
                counts["updated"] += 1
                before[doc.chunk_ix] = (digest, doc.embedding is not None, has_v2)
            elif (not old[1] and doc.embedding is not None) or (
                need_v2 and not old[2] and doc.embedding_v2 is not None
            ):
                counts["updated"] += 1
                before[doc.chunk_ix] = (digest, True, True)
            else:
                counts["skipped"] += 1
    return counts


def _http_insert_v2(doc: KnowledgeDoc, args: tuple, tail: tuple) -> tuple[str, tuple]:
    emb_sql = "NULL" if doc.embedding is None else "vector32(?)"
    v2_sql = "NULL" if doc.embedding_v2 is None else "vector32(?)"
    extra: list = []
    if doc.embedding is not None:
        extra.append(json.dumps(doc.embedding))
    if doc.embedding_v2 is not None:
        extra.append(json.dumps(doc.embedding_v2))
    sql = (
        "INSERT INTO knowledge (source, scope, doc_key, chunk_ix, title, summary, content, "
        "metadata, embedding, embedding_v2, content_hash, created_at, last_activity_at) "
        f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, {emb_sql}, {v2_sql}, ?, ?, ?)"
    )
    return sql, args + tuple(extra) + tail


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
