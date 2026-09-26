"""Knowledge ingest pipeline: connector fetch → pre-filter → distill → embed
→ upsert → prune (tasks/knowledge-base-plan.md Phase 1).

Determinism/cost guard: before distilling anything, the stored state for the
source is loaded in ONE query and any document whose connector-supplied
fields (content, title, metadata) are unchanged AND already has a summary —
and an embedding, whenever an embedder is actually available this run — is
skipped entirely. LLM output must never cause re-distillation of unchanged
docs: the skip is keyed on connector output, not on content_hash (which
covers the summary too), and the tickers key distill merges into metadata is
excluded from the comparison. Rows left FTS-only by a degraded run are
reprocessed once an embedder returns (store backfills the vector even when
the re-distilled summary hashes identical). The skip is per DOCUMENT, not per
chunk: passing only a doc's changed chunks to upsert_documents would falsely
prune its unchanged trailing chunks.

Vanished-doc pruning is cross-host safe: the knowledge table is SHARED
(production Turso) while some connector inputs are host-local gitignored
files, so an empty fetch never prunes, and a connector may scope pruning to
the keys it is authoritative for on this host via prunable_doc_keys().

CLI (systemd oneshot via radon-knowledge.timer):
    .venv/bin/python scripts/knowledge/ingest.py --source all
Progress goes to stderr; stdout carries a single JSON result line (house
subprocess discipline). The whole run heartbeats once through
service_cycle("knowledge-ingest") — clean exit → ok, any source failure →
error row + embargo + non-zero exit.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
import time
from pathlib import Path
from typing import Iterable, NamedTuple

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from credential_redaction import scrub_credential_text  # noqa: E402
from knowledge.distill import EnrichmentBudget, distill  # noqa: E402
from knowledge.embed import (  # noqa: E402
    EMBEDDING_DIM, EMBEDDING_DIM_V2, dual_write_enabled, embed_passages,
    embedding_text, get_embedder,
)
from knowledge.schema import KnowledgeDoc  # noqa: E402
from knowledge.store import delete_source_docs, upsert_documents  # noqa: E402

SERVICE_NAME = "knowledge-ingest"

# Bounded retries absorb a transient Turso lock/stream blip so the
# hourly oneshot does not page P1 on SQLITE_BUSY (2026-08-15: newsfeed
# failed once with no retry, NRestarts=0; 2026-08-21 17:22Z: newsfeed
# busy on both attempts of the then-budget of 2, incidents succeeded
# 6s later).
_SOURCE_ATTEMPTS = 4
_SOURCE_RETRY_BACKOFF_SECS = 1.0
_WRITE_ATTEMPTS = 4
_TRANSIENT_DB_MARKERS = (
    "sqlite_busy",
    "database is locked",
    "stream not found",
    "upstream forward failed",
    "timed out",
    "timeout",
    "connection reset",
    "server closed the connection",
)

# Bounded id-cursor pagination, same Turso HTTP-pipeline limit as the
# newsfeed posts read: a source's full stored content in one SELECT
# 502s once the corpus is large (2026-07-19). 200 rows that also carry an
# 8 KB embedding_v2 blob can approach the 8 MiB response cap, so the page
# shrinks to a byte budget before the first fetch.
_EXISTING_BATCH_ROWS = 200
_EXISTING_ASSUMED_ROW_BYTES = 16 * 1024
_EXISTING_ASSUMED_V2_ROW_BYTES = 48 * 1024

_EXISTING_SQL = (
    "SELECT id, doc_key, chunk_ix, content, title, metadata, "
    "summary, embedding, content_hash "
    "FROM knowledge WHERE source = ? AND id > ? ORDER BY id LIMIT ?"
)
_EXISTING_SQL_V2 = (
    "SELECT id, doc_key, chunk_ix, content, title, metadata, "
    "summary, embedding, content_hash, embedding_v2 "
    "FROM knowledge WHERE source = ? AND id > ? ORDER BY id LIMIT ?"
)


class _StoredChunk(NamedTuple):
    content: str
    title: str | None
    metadata_json: str | None
    summary: str | None
    embedding: bytes | None
    content_hash: str
    embedding_v2: bytes | None = None

    @property
    def has_summary(self):
        return self.summary is not None

    @property
    def has_embedding(self):
        return self.embedding is not None

    @property
    def has_embedding_v2(self):
        return self.embedding_v2 is not None

_EMPTY_UPSERT_COUNTS = {"inserted": 0, "updated": 0, "skipped": 0, "pruned": 0}

# Changed docs are distilled/embedded in bounded preparation batches. Each
# complete authoritative document is then upserted on a FRESH connection.
# One long-lived Hrana stream carrying minutes
# of distillation idle time plus thousands of write statements 502s on Turso
# (2026-07-19, newsfeed backfill). Document-level commits make progress
# durable: a mid-run failure keeps every completed document.
_INGEST_BATCH_DOCS = 200


def _document_batches(
    docs: list[KnowledgeDoc], max_chunks: int
) -> list[list[KnowledgeDoc]]:
    """Bound writes without ever splitting one authoritative document."""
    groups: dict[tuple[str, str], list[KnowledgeDoc]] = {}
    for doc in docs:
        groups.setdefault((doc.source, doc.doc_key), []).append(doc)
    batches: list[list[KnowledgeDoc]] = []
    current: list[KnowledgeDoc] = []
    for group in groups.values():
        if current and len(current) + len(group) > max_chunks:
            batches.append(current)
            current = []
        current.extend(group)
    if current:
        batches.append(current)
    return batches


def ingest_source(
    db,
    module,
    *,
    distill_enabled: bool = True,
    embed_enabled: bool = True,
    limit: int | None = None,
    db_factory=None,
    enrichment: EnrichmentBudget | None = None,
) -> dict:
    """Run one connector module through the full pipeline. Returns counts.

    With ``limit`` set the connector fetch is truncated to the first N
    documents, and vanished-doc pruning is skipped — a partial fetch must
    never delete documents it simply didn't reach. ``db_factory`` supplies a
    fresh connection per authoritative document; without it the shared
    ``db`` is used."""
    enrichment = enrichment if enrichment is not None else EnrichmentBudget()
    source = module.SOURCE
    fresh_db = db_factory if db_factory is not None else (lambda: db)
    docs = _fetch_docs(module, db, limit)
    existing = _load_existing(db, source)
    embedder = get_embedder() if embed_enabled else None
    to_process, skipped_docs = _pre_filter(
        docs, existing, require_embedding=embedder is not None,
        require_summary=distill_enabled
    )

    distilled = distill_failed = distill_deferred = embedded = 0
    counts = dict(_EMPTY_UPSERT_COUNTS)
    for batch in _document_batches(to_process, _INGEST_BATCH_DOCS):
        if distill_enabled:
            batch_distilled, batch_failed, batch_deferred = _distill_docs(batch, enrichment)
            distilled += batch_distilled
            distill_failed += batch_failed
            distill_deferred += batch_deferred
        _restore_unchanged_enrichment(batch, existing)
        if embed_enabled:
            want_v2 = dual_write_enabled() and _knowledge_has_embedding_v2(db)
            pending = [
                doc for doc in batch
                if doc.embedding is None or (want_v2 and doc.embedding_v2 is None)
            ]
            embedded += _embed_docs(pending, embedder, write_v2=want_v2)
        # One authoritative document is the smallest safe write transaction:
        # every chunk and its trailing-chunk prune must commit together. The
        # preparation batch must not reserve the shared writer for hundreds
        # of unrelated documents and serial HTTP round trips.
        for document in _document_batches(batch, max_chunks=1):
            if _prepared_document_is_current(document, existing):
                counts["skipped"] += len(document)
                continue
            persisted = _persist_prepared(
                fresh_db, lambda connection: upsert_documents(connection, document),
                source=source, doc_key=document[0].doc_key,
                chunk_count=len(document),
            )
            for key, value in persisted.items():
                if key == "row_errors":
                    counts.setdefault(key, []).extend(value)
                else:
                    counts[key] = counts.get(key, 0) + value
        print(
            f"[{SERVICE_NAME}] {source}: committed {len(batch)} prepared chunks",
            file=sys.stderr, flush=True,
        )

    deleted = 0
    if limit is None:
        vanished = _vanished_keys(module, docs, existing)
        deleted = _persist_prepared(
            fresh_db, lambda connection: delete_source_docs(connection, source, vanished),
            source=source,
        )

    result = {
        "source": source,
        "fetched": len({doc.doc_key for doc in docs}),
        "fetched_chunks": len(docs),
        "skipped_docs": skipped_docs,
        "distilled": distilled,
        "distill_failed": distill_failed,
        "distill_deferred": distill_deferred,
        "embedded": embedded,
        "deleted": deleted,
        **counts,
    }
    print(
        f"[{SERVICE_NAME}] {source}: "
        + " ".join(f"{key}={value}" for key, value in result.items() if key != "source"),
        file=sys.stderr,
    )
    return result


def _fetch_docs(module, db, limit: int | None) -> list[KnowledgeDoc]:
    if limit is None:
        return list(module.fetch(db))
    docs: list[KnowledgeDoc] = []
    seen_keys: set[str] = set()
    for doc in module.fetch(db):
        if doc.doc_key not in seen_keys:
            if len(seen_keys) == limit:
                break
            seen_keys.add(doc.doc_key)
        docs.append(doc)
    return docs


def _knowledge_has_embedding_v2(db) -> bool:
    try:
        rows = db.execute("PRAGMA table_info(knowledge)").fetchall()
    except Exception:
        return False
    return any(row[1] == "embedding_v2" for row in rows)


def _existing_page_limit(has_v2: bool, observed_bytes: int = 0, observed_rows: int = 0) -> int:
    from knowledge.http_db import MAX_RESPONSE_BYTES

    budget = max(1, MAX_RESPONSE_BYTES - MAX_RESPONSE_BYTES // 8)
    if observed_rows > 0:
        per_row = max(1, (observed_bytes + observed_rows - 1) // observed_rows)
    else:
        per_row = _EXISTING_ASSUMED_V2_ROW_BYTES if has_v2 else _EXISTING_ASSUMED_ROW_BYTES
    return max(1, min(_EXISTING_BATCH_ROWS, budget // per_row))


def _row_wire_bytes(row) -> int:
    total = 64
    for cell in row:
        if isinstance(cell, (bytes, bytearray, memoryview)):
            total += ((len(cell) + 2) // 3) * 4 + 48
        elif cell is not None:
            total += len(str(cell)) + 8
    return total


def _load_existing(db, source: str) -> dict[str, dict[int, _StoredChunk]]:
    """{doc_key: {chunk_ix: _StoredChunk}} for the source."""
    existing: dict[str, dict[int, _StoredChunk]] = {}
    has_v2 = _knowledge_has_embedding_v2(db)
    sql = _EXISTING_SQL_V2 if has_v2 else _EXISTING_SQL
    cursor = 0
    seen_rows = 0
    seen_bytes = 0
    while True:
        limit = _existing_page_limit(has_v2, seen_bytes, seen_rows)
        rows = db.execute(sql, (source, cursor, limit)).fetchall()
        if not rows:
            return existing
        for row in rows:
            embedding_v2 = None
            if has_v2:
                (row_id, doc_key, chunk_ix, content, title, metadata_json,
                 summary, embedding, digest, embedding_v2) = row
            else:
                (row_id, doc_key, chunk_ix, content, title, metadata_json,
                 summary, embedding, digest) = row
            existing.setdefault(doc_key, {})[chunk_ix] = _StoredChunk(
                content, title, metadata_json, summary, embedding, digest, embedding_v2
            )
            cursor = row_id
            seen_rows += 1
            seen_bytes += _row_wire_bytes(row)
        if len(rows) < limit:
            return existing


def _pre_filter(
    docs: Iterable[KnowledgeDoc],
    existing: dict[str, dict[int, _StoredChunk]],
    *,
    require_embedding: bool,
    require_summary: bool = True,
) -> tuple[list[KnowledgeDoc], int]:
    by_doc: dict[str, list[KnowledgeDoc]] = {}
    for doc in docs:
        by_doc.setdefault(doc.doc_key, []).append(doc)
    to_process: list[KnowledgeDoc] = []
    skipped = 0
    for doc_key, chunks in by_doc.items():
        if _is_unchanged_and_summarized(
            chunks, existing.get(doc_key), require_embedding, require_summary
        ):
            skipped += 1
        else:
            to_process.extend(chunks)
    return to_process, skipped


def _is_unchanged_and_summarized(
    chunks: list[KnowledgeDoc],
    stored: dict[int, _StoredChunk] | None,
    require_embedding: bool,
    require_summary: bool = True,
) -> bool:
    if stored is None or set(stored) != {chunk.chunk_ix for chunk in chunks}:
        return False
    return all(
        _chunk_is_current(chunk, stored[chunk.chunk_ix], require_embedding, require_summary)
        for chunk in chunks
    )


def _chunk_is_current(
    chunk: KnowledgeDoc, stored: _StoredChunk, require_embedding: bool,
    require_summary: bool = True,
) -> bool:
    if ((require_summary and not stored.has_summary)
            or (require_embedding and not stored.has_embedding)):
        return False
    return (
        stored.content == chunk.content
        and stored.title == chunk.title
        and _comparable_metadata(stored.metadata_json) == _comparable_metadata(chunk.metadata)
    )


def _comparable_metadata(value: str | dict | None) -> dict:
    """Metadata minus the tickers key distill merges in: connector-supplied
    metadata changes must re-sync the row, but tickers written by a previous
    distill pass must not read as connector drift."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    metadata = dict(value) if isinstance(value, dict) else {}
    metadata.pop("tickers", None)
    return metadata


def _vanished_keys(
    module, docs: list[KnowledgeDoc], existing: dict[str, dict[int, _StoredChunk]]
) -> list[str]:
    """Stored doc_keys safe to prune: never on an empty fetch (a host missing
    the source's local inputs must not wipe another host's corpus), and only
    keys the connector is authoritative for on this host."""
    if not docs:
        if existing:
            print(
                f"[{SERVICE_NAME}] {module.SOURCE}: fetch returned no documents; "
                "skipping vanished-doc pruning",
                file=sys.stderr,
            )
        return []
    emitted_keys = {doc.doc_key for doc in docs}
    vanished = [key for key in existing if key not in emitted_keys]
    limit_to_authoritative = getattr(module, "prunable_doc_keys", None)
    if limit_to_authoritative is not None:
        vanished = list(limit_to_authoritative(vanished))
    return vanished


def _distill_docs(docs: list[KnowledgeDoc], enrichment: EnrichmentBudget) -> tuple[int, int, int]:
    results, deferred = enrichment.run([(doc.title, doc.content) for doc in docs])
    distilled = failed = 0
    for doc, result in zip(docs, results):
        if result is None:
            failed += 1
            continue
        doc.summary = result["summary"]
        if result.get("tickers"):
            doc.metadata = {**(doc.metadata or {}), "tickers": result["tickers"]}
        distilled += 1
    return distilled, failed, deferred


def _prepared_document_is_current(docs, existing):
    """Compare final hashes, not raw-only prefilter keys: retain new summaries.

    Compare the complete chunk set so skipping never suppresses a trailing-chunk
    prune. An embedding-only backfill still requires a write.
    """
    stored = existing.get(docs[0].doc_key, {})
    return set(stored) == {doc.chunk_ix for doc in docs} and all(
        doc.content_hash() == stored[doc.chunk_ix].content_hash
        and (stored[doc.chunk_ix].has_embedding or doc.embedding is None)
        and (doc.embedding_v2 is None or stored[doc.chunk_ix].has_embedding_v2)
        for doc in docs
    )


def _restore_unchanged_enrichment(docs, existing):
    """A deferred optional retry must not erase a current sibling's enrichment."""
    for doc in docs:
        stored = existing.get(doc.doc_key, {}).get(doc.chunk_ix)
        if stored is None:
            continue
        if _chunk_is_current(doc, stored, require_embedding=False, require_summary=False):
            if doc.summary is None:
                doc.summary = stored.summary
                # Preserve model-added tickers when no replacement was produced.
                doc.metadata = json.loads(stored.metadata_json) if stored.metadata_json else None
        same_text = (
            embedding_text(doc.title, doc.summary, doc.content)
            == embedding_text(stored.title, stored.summary, stored.content)
        )
        if stored.embedding is not None and len(stored.embedding) == EMBEDDING_DIM * 4 and same_text:
            doc.embedding = list(struct.unpack(f"<{EMBEDDING_DIM}f", stored.embedding))
        if (stored.embedding_v2 is not None and len(stored.embedding_v2) == EMBEDDING_DIM_V2 * 4
                and same_text):
            doc.embedding_v2 = list(struct.unpack(f"<{EMBEDDING_DIM_V2}f", stored.embedding_v2))


def _embed_docs(docs: list[KnowledgeDoc], embedder, *, write_v2: bool | None = None) -> int:
    if write_v2 is None:
        write_v2 = dual_write_enabled()
    if not docs:
        return 0
    missing_local = [doc for doc in docs if doc.embedding is None]
    wrote = 0
    if embedder is not None and missing_local:
        texts = [embedding_text(doc.title, doc.summary, doc.content) for doc in missing_local]
        for doc, vector in zip(missing_local, embedder(texts)):
            doc.embedding = vector
        wrote = len(missing_local)
    elif embedder is None and missing_local and not write_v2:
        print(
            f"[{SERVICE_NAME}] embeddings unavailable — ingesting without vectors",
            file=sys.stderr,
        )
        return 0
    if write_v2:
        missing_v2 = [doc for doc in docs if doc.embedding_v2 is None]
        if missing_v2:
            texts = [embedding_text(doc.title, doc.summary, doc.content) for doc in missing_v2]
            try:
                vectors = embed_passages(texts)
            except Exception as exc:
                print(f"[{SERVICE_NAME}] embedding_v2 unavailable ({exc})", file=sys.stderr)
                vectors = []
            for doc, vector in zip(missing_v2, vectors):
                if len(vector) == EMBEDDING_DIM_V2:
                    doc.embedding_v2 = vector
                    wrote += 1
    return wrote


# ── CLI ──────────────────────────────────────────────────────────────


class _PersistenceExhausted(RuntimeError):
    """The prepared write used its retry budget; never replay enrichment."""


def _persist_prepared(db_factory, operation, *, source, doc_key=None, chunk_count=None):
    # Distillation and embeddings stay outside this loop. A fresh connection
    # retries the idempotent atomic write (COMMIT may be ambiguous), not optional LLM
    # work. Exhaustion is terminal for this source even when its cause is busy.
    for attempt in range(1, _WRITE_ATTEMPTS + 1):
        connection = None
        try:
            connection = db_factory()
            return operation(connection)
        except Exception as exc:
            if not _is_transient_db_error(exc):
                raise
            context = ""
            if doc_key is not None:
                safe_key = json.dumps(scrub_credential_text(str(doc_key))[:256])
                steps = getattr(connection, "last_transaction_step_count", "unknown")
                context = (f" doc_key={safe_key} chunk_count={chunk_count}"
                           f" statement_count={steps}")
            if attempt == _WRITE_ATTEMPTS:
                raise _PersistenceExhausted(
                    f"{source}:{context} prepared write failed after {attempt} attempts: {exc}"
                ) from exc
            print(
                f"[{SERVICE_NAME}] {source}:{context} transient prepared-write error "
                f"(attempt {attempt}/{_WRITE_ATTEMPTS}): {exc}; retrying persistence",
                file=sys.stderr, flush=True,
            )
            time.sleep(_SOURCE_RETRY_BACKOFF_SECS)


def _is_transient_db_error(exc: BaseException) -> bool:
    """True for retryable reads/writes, excluding an exhausted write budget."""
    if isinstance(exc, _PersistenceExhausted):
        return False
    from knowledge.http_db import TransportError
    if isinstance(exc, TransportError):
        return True
    message = str(exc).lower()
    return any(marker in message for marker in _TRANSIENT_DB_MARKERS)


def _fresh_db():
    """New bounded HTTP handle; transactions never share a native singleton."""
    from knowledge.http_db import Connection

    return Connection()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _load_dotenv_if_present()
    from db.service_cycle import service_cycle  # noqa: PLC0415
    from knowledge.sources import ALL_SOURCES  # noqa: PLC0415

    modules = _select_sources(ALL_SOURCES, args.source)
    results: dict[str, dict] = {}
    errors: dict[str, str] = {}
    enrichment = EnrichmentBudget()
    with service_cycle(SERVICE_NAME, market_hours_class="daily"):
        for name, module in modules.items():
            last_exc: BaseException | None = None
            for attempt in range(1, _SOURCE_ATTEMPTS + 1):
                try:
                    # Fresh connection per source (and per retry): a long or
                    # failing source can kill the shared Hrana stream and
                    # poison every source after it (incidents "stream not
                    # found" after newsfeed's 502 run, 2026-07-19).
                    results[name] = ingest_source(
                        _fresh_db(),
                        module,
                        distill_enabled=not args.no_distill,
                        embed_enabled=not args.no_embed,
                        limit=args.limit,
                        db_factory=_fresh_db,
                        enrichment=enrichment,
                    )
                    last_exc = None
                    break
                except Exception as exc:  # noqa: BLE001 — one bad source must not stop the rest
                    last_exc = exc
                    if _is_transient_db_error(exc) and attempt < _SOURCE_ATTEMPTS:
                        print(
                            f"[{SERVICE_NAME}] {name}: transient DB error "
                            f"(attempt {attempt}/{_SOURCE_ATTEMPTS}): {exc}; "
                            f"retrying in {_SOURCE_RETRY_BACKOFF_SECS}s",
                            file=sys.stderr,
                        )
                        time.sleep(_SOURCE_RETRY_BACKOFF_SECS)
                        continue
                    break
            if last_exc is not None:
                errors[name] = str(last_exc)
                print(f"[{SERVICE_NAME}] {name} failed: {last_exc}", file=sys.stderr)
        print(json.dumps({"ok": not errors, "sources": results, "errors": errors}),
              flush=True)
        if errors:
            raise RuntimeError(
                "knowledge ingest failed for: " + ", ".join(sorted(errors))
            )
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest connector sources into the Turso knowledge table."
    )
    parser.add_argument("--source", default="all",
                        help="connector name, or 'all' (default)")
    parser.add_argument("--no-distill", action="store_true",
                        help="skip LLM summarization (rows stay FTS-searchable)")
    parser.add_argument("--no-embed", action="store_true",
                        help="skip local embeddings")
    parser.add_argument("--limit", type=int, default=None,
                        help="cap documents per source (disables vanished-doc pruning)")
    return parser.parse_args(argv)


def _select_sources(all_sources: dict, name: str) -> dict:
    if name == "all":
        return dict(all_sources)
    if name not in all_sources:
        raise SystemExit(
            f"unknown source {name!r} (available: {', '.join(sorted(all_sources))})"
        )
    return {name: all_sources[name]}


def _load_dotenv_if_present() -> None:
    """Best-effort: load .env + web/.env (CEREBRAS_API_KEY, Turso creds) so
    the CLI Just Works, matching scripts/llm_token_index.py."""
    project_root = _SCRIPTS_DIR.parent
    try:
        from dotenv import load_dotenv  # noqa: PLC0415
    except ImportError:
        return
    load_dotenv(project_root / ".env")
    load_dotenv(project_root / "web" / ".env")


if __name__ == "__main__":
    sys.exit(main())
