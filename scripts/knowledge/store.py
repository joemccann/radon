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

from .bounded_write import is_size_error, run_size_bounded, vector_payload
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
            args.append(vector_payload(doc.embedding))
        args.append(vector_payload(doc.embedding_v2))
        cursor = db.execute(sql, tuple(args) + tail_args)
    elif doc.embedding is not None:
        cursor = db.execute(
            _INSERT_WITH_EMBEDDING_SQL,
            shared_args + (vector_payload(doc.embedding),) + tail_args,
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
            db.execute(_UPDATE_WITH_EMBEDDING_SQL, shared_args + (vector_payload(doc.embedding), row_id))
        else:
            db.execute(_UPDATE_NO_EMBEDDING_SQL, shared_args + (row_id,))
    else:
        sets = _UPDATE_SET_COLUMNS
        args = list(shared_args)
        if doc.embedding is not None:
            sets += ", embedding = vector32(?)"
            args.append(vector_payload(doc.embedding))
        else:
            sets += ", embedding = NULL"
        if doc.embedding_v2 is not None:
            sets += ", embedding_v2 = vector32(?)"
            args.append(vector_payload(doc.embedding_v2))
        else:
            sets += ", embedding_v2 = NULL"
        args.append(row_id)
        db.execute(f"UPDATE knowledge SET {sets} WHERE id = ?", tuple(args))
    _refresh_fts_row(db, row_id, doc)


def _backfill_embedding(db, row_id: int, doc: KnowledgeDoc) -> None:
    db.execute(_BACKFILL_EMBEDDING_SQL, (vector_payload(doc.embedding), row_id))


def backfill_embedding_v2(db, row_id: int, vector) -> None:
    db.execute(_BACKFILL_EMBEDDING_V2_SQL, (vector_payload(vector), row_id))


def backfill_embedding_v2_batch(db, pairs) -> list[dict]:
    """Write ``(row_id, vector)`` pairs in byte-bounded transactions.

    A single row that cannot fit is returned in the error list. The other
    rows still commit.
    """
    errors = []

    def statements(batch):
        return [
            (_BACKFILL_EMBEDDING_V2_SQL, (vector_payload(vector), row_id))
            for row_id, vector in batch
        ]

    def write(batch):
        if hasattr(db, "execute_transaction"):
            db.execute_transaction(statements(batch))
            return
        try:
            db.execute("BEGIN IMMEDIATE")
            for row_id, vector in batch:
                backfill_embedding_v2(db, row_id, vector)
            db.commit()
        except BaseException:
            try:
                db.rollback()
            except Exception:
                pass
            raise

    def on_row(pair):
        row_id = pair[0]
        errors.append({
            "id": row_id,
            "error": f"knowledge row id {row_id} exceeds bounded request size",
        })

    run_size_bounded(list(pairs), write, lambda batch: _request_fits(statements(batch)), on_row_too_large=on_row)
    return errors


def _refresh_fts_row(db, row_id: int, doc: KnowledgeDoc) -> None:
    db.execute(_FTS_DELETE_SQL, (row_id,))
    db.execute(_FTS_INSERT_SQL, (row_id, doc.title, doc.summary, doc.content))


def _metadata_json(doc: KnowledgeDoc) -> str | None:
    return json.dumps(doc.metadata) if doc.metadata is not None else None


# HTTP writes are one server-side transaction when the payload fits. The
# vector is bound once, on the INSERT, and ON CONFLICT copies excluded.* so a
# hash-equal null embedding is backfilled without an activity bump. A document
# that cannot fit commits its rows (text, FTS, prune) with embeddings cleared,
# then fills those NULL vectors in byte-bounded follow-ups. Readers see the
# whole document's new text and never a stale vector beside it.
def _upsert_documents_http(db, docs: list[KnowledgeDoc]) -> dict:
    counts = {"inserted": 0, "updated": 0, "skipped": 0, "pruned": 0}
    groups: dict[tuple[str, str], list[KnowledgeDoc]] = {}
    for doc in docs:
        groups.setdefault((doc.source, doc.doc_key), []).append(doc)
    if not groups:
        return counts
    need_v2 = _need_embedding_v2(docs, db)
    row_errors: list[dict] = []
    statements, snapshots = _build_http_statements(list(groups.values()), need_v2, include_vectors=True)
    if _request_fits(statements):
        try:
            receipts = db.execute_transaction(statements)
        except Exception as exc:
            if not is_size_error(exc):
                raise
        else:
            _account_http(counts, receipts, snapshots, need_v2)
            return counts
    elif _step_overflow(statements) and not _over_byte_budget(statements):
        receipts = db.execute_transaction(statements)
        _account_http(counts, receipts, snapshots, need_v2)
        return counts
    for chunks in groups.values():
        _upsert_one_document_http(db, chunks, need_v2, counts, row_errors)
    if row_errors:
        counts["row_errors"] = row_errors
    return counts


def _upsert_one_document_http(db, chunks, need_v2, counts, row_errors) -> None:
    statements, snapshots = _build_http_statements([chunks], need_v2, include_vectors=True)
    if _step_overflow(statements) and not _over_byte_budget(statements):
        receipts = db.execute_transaction(statements)
        _account_http(counts, receipts, snapshots, need_v2)
        return
    if _request_fits(statements):
        try:
            receipts = db.execute_transaction(statements)
        except Exception as exc:
            if not is_size_error(exc):
                raise
        else:
            _account_http(counts, receipts, snapshots, need_v2)
            return
    text, text_snaps = _build_http_statements([chunks], need_v2, include_vectors=False)
    if _request_fits(text):
        try:
            receipts = db.execute_transaction(text)
        except Exception as exc:
            if not is_size_error(exc):
                raise
            _write_text_halved(db, chunks, need_v2, counts, row_errors)
        else:
            _account_http(counts, receipts, text_snaps, need_v2)
    elif _step_overflow(text) and not _over_byte_budget(text):
        db.execute_transaction(text)
    else:
        _write_text_halved(db, chunks, need_v2, counts, row_errors)
    failed = {(item["source"], item["doc_key"], item["chunk_ix"]) for item in row_errors}
    _write_vectors_bounded(
        db,
        [doc for doc in chunks if (doc.source, doc.doc_key, doc.chunk_ix) not in failed],
        row_errors,
    )


def _write_text_halved(db, chunks, need_v2, counts, row_errors) -> None:
    wrote = {"ok": False}

    def fits(batch):
        statements, _snapshots = _build_http_statements([batch], need_v2, include_vectors=False, prune=False)
        return _request_fits(statements)

    def write(batch):
        statements, snapshots = _build_http_statements([batch], need_v2, include_vectors=False, prune=False)
        receipts = db.execute_transaction(statements)
        _account_http(counts, receipts, snapshots, need_v2, count_prune=False)
        wrote["ok"] = True

    def on_row(doc):
        row_errors.append(_row_error(doc))

    run_size_bounded(chunks, write, fits, on_row_too_large=on_row)
    if wrote["ok"]:
        counts["pruned"] += _prune_document_http(
            db, chunks[0].source, chunks[0].doc_key, max(doc.chunk_ix for doc in chunks),
        )


def _write_vectors_bounded(db, chunks, row_errors) -> None:
    pending = [doc for doc in chunks if doc.embedding is not None or doc.embedding_v2 is not None]

    def fits(batch):
        return _request_fits(_vector_fill_statements(batch))

    def write(batch):
        db.execute_transaction(_vector_fill_statements(batch))

    run_size_bounded(pending, write, fits, on_row_too_large=lambda doc: row_errors.append(_row_error(doc)))


def _row_error(doc: KnowledgeDoc) -> dict:
    return {
        "source": doc.source,
        "doc_key": doc.doc_key,
        "chunk_ix": doc.chunk_ix,
        "error": (
            f"knowledge row {doc.source}/{doc.doc_key}#{doc.chunk_ix} "
            "exceeds bounded request size"
        ),
    }


def _request_fits(statements) -> bool:
    return not _step_overflow(statements) and not _over_byte_budget(statements)


def _step_overflow(statements) -> bool:
    from knowledge.http_db import MAX_TRANSACTION_STEPS
    return len(statements) + 3 > MAX_TRANSACTION_STEPS


def _over_byte_budget(statements) -> bool:
    from knowledge.http_db import transaction_request_bytes, write_budget_bytes
    return transaction_request_bytes(statements) > write_budget_bytes()


def _build_http_statements(groups, need_v2, *, include_vectors: bool, prune: bool = True):
    statements, snapshots = [], []
    for chunks in groups:
        key = (chunks[0].source, chunks[0].doc_key)
        snapshots.append((len(statements), chunks))
        statements.append((_HTTP_SELECT_V2 if need_v2 else _HTTP_SELECT, key))
        for doc in chunks:
            _append_chunk_statements(statements, doc, need_v2=need_v2, include_vectors=include_vectors)
        if prune:
            _append_prune_statements(statements, key, max(doc.chunk_ix for doc in chunks))
    return statements, snapshots


def _append_chunk_statements(statements, doc, *, need_v2, include_vectors) -> None:
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
    embedding = doc.embedding if include_vectors else None
    embedding_v2 = doc.embedding_v2 if include_vectors else None
    insert, args = _http_insert(args, tail, embedding, embedding_v2 if include_v2 else None, include_v2)
    statements.append((insert + _conflict_sql(include_v2), args))
    # Changed mirrors were removed above; unchanged mirrors stay. A missing
    # mirror is repaired without rewriting a hash-identical canonical row.
    statements.append((
        "INSERT INTO knowledge_fts(rowid,title,summary,content) "
        "SELECT id,title,summary,content FROM knowledge WHERE source=? "
        "AND doc_key=? AND chunk_ix=? AND NOT EXISTS "
        "(SELECT 1 FROM knowledge_fts WHERE rowid=knowledge.id)", identity,
    ))


def _conflict_sql(include_v2: bool) -> str:
    unchanged = "knowledge.content_hash != excluded.content_hash"
    v2_assign = ""
    where = f"{unchanged} OR (knowledge.embedding IS NULL AND excluded.embedding IS NOT NULL)"
    if include_v2:
        v2_assign = (
            "embedding_v2=CASE WHEN knowledge.content_hash != excluded.content_hash "
            "THEN excluded.embedding_v2 WHEN knowledge.embedding_v2 IS NULL "
            "THEN excluded.embedding_v2 ELSE knowledge.embedding_v2 END, "
        )
        where += " OR (knowledge.embedding_v2 IS NULL AND excluded.embedding_v2 IS NOT NULL)"
    return (
        " ON CONFLICT(source, doc_key, chunk_ix) DO UPDATE SET "
        f"scope=CASE WHEN {unchanged} THEN excluded.scope ELSE knowledge.scope END, "
        f"title=CASE WHEN {unchanged} THEN excluded.title ELSE knowledge.title END, "
        f"summary=CASE WHEN {unchanged} THEN excluded.summary ELSE knowledge.summary END, "
        f"content=CASE WHEN {unchanged} THEN excluded.content ELSE knowledge.content END, "
        f"metadata=CASE WHEN {unchanged} THEN excluded.metadata ELSE knowledge.metadata END, "
        + v2_assign +
        "embedding=CASE WHEN knowledge.content_hash != excluded.content_hash THEN excluded.embedding "
        "WHEN knowledge.embedding IS NULL THEN excluded.embedding ELSE knowledge.embedding END, "
        "content_hash=excluded.content_hash, "
        f"last_activity_at=CASE WHEN {unchanged} THEN excluded.last_activity_at "
        "ELSE knowledge.last_activity_at END "
        f"WHERE {where}"
    )


def _http_insert(args, tail, embedding, embedding_v2, include_v2: bool):
    extra = []
    emb_sql, emb_extra = _vector_slot(embedding)
    extra.extend(emb_extra)
    if include_v2:
        v2_sql, v2_extra = _vector_slot(embedding_v2)
        extra.extend(v2_extra)
        sql = (
            "INSERT INTO knowledge (source, scope, doc_key, chunk_ix, title, summary, content, "
            "metadata, embedding, embedding_v2, content_hash, created_at, last_activity_at) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, {emb_sql}, {v2_sql}, ?, ?, ?)"
        )
    elif emb_sql == "NULL":
        sql = _INSERT_NO_EMBEDDING_SQL
        extra = []
    else:
        sql = _INSERT_WITH_EMBEDDING_SQL
    return sql, args + tuple(extra) + tail


def _vector_slot(vector):
    if vector is None:
        return "NULL", []
    return "vector32(?)", [vector_payload(vector)]


def _vector_fill_statements(chunks):
    statements = []
    for doc in chunks:
        identity = (doc.source, doc.doc_key, doc.chunk_ix, doc.content_hash())
        if doc.embedding is not None:
            statements.append((
                "UPDATE knowledge SET embedding=vector32(?) WHERE source=? "
                "AND doc_key=? AND chunk_ix=? AND content_hash=? AND embedding IS NULL",
                (vector_payload(doc.embedding),) + identity,
            ))
        if doc.embedding_v2 is not None:
            statements.append((
                "UPDATE knowledge SET embedding_v2=vector32(?) WHERE source=? "
                "AND doc_key=? AND chunk_ix=? AND content_hash=? AND embedding_v2 IS NULL",
                (vector_payload(doc.embedding_v2),) + identity,
            ))
    return statements


def _append_prune_statements(statements, key, max_ix) -> None:
    predicate = "source=? AND doc_key=? AND chunk_ix>?"
    args = key + (max_ix,)
    statements.append((
        "DELETE FROM knowledge_fts WHERE rowid IN (SELECT id FROM knowledge WHERE "
        + predicate + ")", args,
    ))
    statements.append(("DELETE FROM knowledge WHERE " + predicate, args))


def _prune_document_http(db, source: str, doc_key: str, max_ix: int) -> int:
    predicate = "source=? AND doc_key=? AND chunk_ix>?"
    args = (source, doc_key, max_ix)
    receipts = db.execute_transaction([
        ("SELECT id FROM knowledge WHERE " + predicate, args),
        (
            "DELETE FROM knowledge_fts WHERE rowid IN (SELECT id FROM knowledge WHERE "
            + predicate + ")",
            args,
        ),
        ("DELETE FROM knowledge WHERE " + predicate, args),
    ])
    return len(receipts[0].fetchall())


def _account_http(counts, receipts, snapshots, need_v2, *, count_prune: bool = True) -> None:
    for index, chunks in snapshots:
        rows = receipts[index].fetchall()
        if need_v2:
            before = {row[1]: (row[2], bool(row[3]), bool(row[4])) for row in rows}
        else:
            before = {row[1]: (row[2], bool(row[3]), True) for row in rows}
        max_ix = max(doc.chunk_ix for doc in chunks)
        if count_prune:
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
