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
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterable, NamedTuple

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from knowledge.distill import distill  # noqa: E402
from knowledge.embed import embedding_text, get_embedder  # noqa: E402
from knowledge.schema import KnowledgeDoc  # noqa: E402
from knowledge.store import delete_source_docs, upsert_documents  # noqa: E402

SERVICE_NAME = "knowledge-ingest"
DISTILL_WORKERS = 8

# One bounded retry absorbs a transient Turso lock/stream blip so the
# hourly oneshot does not page P1 on SQLITE_BUSY (2026-08-15: newsfeed
# failed once under concurrent posts/knowledge writers, NRestarts=0).
_SOURCE_ATTEMPTS = 2
_SOURCE_RETRY_BACKOFF_SECS = 1.0
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
# 502s once the corpus is large (2026-07-19).
_EXISTING_BATCH_ROWS = 200

_EXISTING_SQL = (
    "SELECT id, doc_key, chunk_ix, content, title, metadata, "
    "summary IS NOT NULL, embedding IS NOT NULL "
    "FROM knowledge WHERE source = ? AND id > ? ORDER BY id LIMIT ?"
)


class _StoredChunk(NamedTuple):
    content: str
    title: str | None
    metadata_json: str | None
    has_summary: bool
    has_embedding: bool

_EMPTY_UPSERT_COUNTS = {"inserted": 0, "updated": 0, "skipped": 0, "pruned": 0}

# Changed docs are distilled/embedded/written in bounded batches, each batch
# upserted on a FRESH connection. One long-lived Hrana stream carrying minutes
# of distillation idle time plus thousands of write statements 502s on Turso
# (2026-07-19, newsfeed backfill), and batch-level writes make progress
# durable: a mid-run failure keeps every completed batch.
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
) -> dict:
    """Run one connector module through the full pipeline. Returns counts.

    With ``limit`` set the connector fetch is truncated to the first N
    documents, and vanished-doc pruning is skipped — a partial fetch must
    never delete documents it simply didn't reach. ``db_factory`` supplies a
    fresh connection per write batch; without it the shared ``db`` is used."""
    source = module.SOURCE
    fresh_db = db_factory if db_factory is not None else (lambda: db)
    docs = _fetch_docs(module, db, limit)
    existing = _load_existing(db, source)
    embedder = get_embedder() if embed_enabled else None
    to_process, skipped_docs = _pre_filter(
        docs, existing, require_embedding=embedder is not None
    )

    distilled = distill_failed = embedded = 0
    counts = dict(_EMPTY_UPSERT_COUNTS)
    for batch in _document_batches(to_process, _INGEST_BATCH_DOCS):
        if distill_enabled:
            batch_distilled, batch_failed = _distill_docs(batch)
            distilled += batch_distilled
            distill_failed += batch_failed
        if embed_enabled:
            embedded += _embed_docs(batch, embedder)
        for key, value in upsert_documents(fresh_db(), batch).items():
            counts[key] = counts.get(key, 0) + value

    deleted = 0
    if limit is None:
        deleted = delete_source_docs(fresh_db(), source, _vanished_keys(module, docs, existing))

    result = {
        "source": source,
        "fetched": len({doc.doc_key for doc in docs}),
        "fetched_chunks": len(docs),
        "skipped_docs": skipped_docs,
        "distilled": distilled,
        "distill_failed": distill_failed,
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


def _load_existing(db, source: str) -> dict[str, dict[int, _StoredChunk]]:
    """{doc_key: {chunk_ix: _StoredChunk}} for the source."""
    existing: dict[str, dict[int, _StoredChunk]] = {}
    cursor = 0
    while True:
        rows = db.execute(_EXISTING_SQL, (source, cursor, _EXISTING_BATCH_ROWS)).fetchall()
        if not rows:
            return existing
        for row_id, doc_key, chunk_ix, content, title, metadata_json, has_summary, has_embedding in rows:
            existing.setdefault(doc_key, {})[chunk_ix] = _StoredChunk(
                content, title, metadata_json, bool(has_summary), bool(has_embedding)
            )
            cursor = row_id
        if len(rows) < _EXISTING_BATCH_ROWS:
            return existing


def _pre_filter(
    docs: Iterable[KnowledgeDoc],
    existing: dict[str, dict[int, _StoredChunk]],
    *,
    require_embedding: bool,
) -> tuple[list[KnowledgeDoc], int]:
    by_doc: dict[str, list[KnowledgeDoc]] = {}
    for doc in docs:
        by_doc.setdefault(doc.doc_key, []).append(doc)
    to_process: list[KnowledgeDoc] = []
    skipped = 0
    for doc_key, chunks in by_doc.items():
        if _is_unchanged_and_summarized(chunks, existing.get(doc_key), require_embedding):
            skipped += 1
        else:
            to_process.extend(chunks)
    return to_process, skipped


def _is_unchanged_and_summarized(
    chunks: list[KnowledgeDoc],
    stored: dict[int, _StoredChunk] | None,
    require_embedding: bool,
) -> bool:
    if stored is None or set(stored) != {chunk.chunk_ix for chunk in chunks}:
        return False
    return all(
        _chunk_is_current(chunk, stored[chunk.chunk_ix], require_embedding)
        for chunk in chunks
    )


def _chunk_is_current(
    chunk: KnowledgeDoc, stored: _StoredChunk, require_embedding: bool
) -> bool:
    if not stored.has_summary or (require_embedding and not stored.has_embedding):
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


def _distill_docs(docs: list[KnowledgeDoc]) -> tuple[int, int]:
    if not docs:
        return 0, 0
    with ThreadPoolExecutor(max_workers=DISTILL_WORKERS) as pool:
        results = list(pool.map(lambda doc: distill(doc.title, doc.content), docs))
    distilled = failed = 0
    for doc, result in zip(docs, results):
        if result is None:
            failed += 1
            continue
        doc.summary = result["summary"]
        if result.get("tickers"):
            doc.metadata = {**(doc.metadata or {}), "tickers": result["tickers"]}
        distilled += 1
    return distilled, failed


def _embed_docs(docs: list[KnowledgeDoc], embedder) -> int:
    if not docs:
        return 0
    if embedder is None:
        print(
            f"[{SERVICE_NAME}] embeddings unavailable — ingesting without vectors",
            file=sys.stderr,
        )
        return 0
    texts = [embedding_text(doc.title, doc.summary, doc.content) for doc in docs]
    for doc, vector in zip(docs, embedder(texts)):
        doc.embedding = vector
    return len(docs)


# ── CLI ──────────────────────────────────────────────────────────────


def _is_transient_db_error(exc: BaseException) -> bool:
    """True for Turso lock/stream blips worth one retry; SQL/schema stay fatal."""
    message = str(exc).lower()
    return any(marker in message for marker in _TRANSIENT_DB_MARKERS)


def _fresh_db():
    """Drop the process singleton and open a new direct-to-cloud connection.

    get_db() caches one connection; without reset_connection the per-source
    and per-batch "fresh" factory is a no-op and a dead/busy Hrana stream
    poisons every later source (2026-07-19 stream-not-found, 2026-08-15
    SQLITE_BUSY on newsfeed)."""
    from db.client import get_db, reset_connection  # noqa: PLC0415

    reset_connection()
    return get_db()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _load_dotenv_if_present()
    from db.service_cycle import service_cycle  # noqa: PLC0415
    from knowledge.sources import ALL_SOURCES  # noqa: PLC0415

    modules = _select_sources(ALL_SOURCES, args.source)
    results: dict[str, dict] = {}
    errors: dict[str, str] = {}
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
