"""Row contract for the `knowledge` table (migration 0028_knowledge.sql).

One KnowledgeDoc per document chunk, keyed by (source, doc_key, chunk_ix).
content_hash is the incremental-ingest idempotency key: connectors re-emit
their full corpus and the store skips anything whose hash is unchanged. It
covers every connector-supplied persisted field (scope, title, summary,
content, metadata) so any change re-syncs the row and the FTS mirror, and
each field is length-prefixed so values cannot collide across boundaries.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


def content_hash(
    source: str,
    scope: str,
    doc_key: str,
    chunk_ix: int,
    title: str | None,
    summary: str | None,
    content: str,
    metadata: dict | None,
) -> str:
    fields = (
        source, scope, doc_key, str(chunk_ix),
        title or "", summary or "", content,
        json.dumps(metadata, sort_keys=True) if metadata is not None else "",
    )
    digest = hashlib.sha256()
    for field in fields:
        encoded = field.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


@dataclass
class KnowledgeDoc:
    source: str  # connector: 'journal' | 'evals' | 'newsfeed' | 'docs' | 'incidents'
    scope: str  # 'trading' | 'research' | 'ops'
    doc_key: str  # stable per-document key within source
    content: str  # raw text; consumers cite/quote this
    chunk_ix: int = 0
    title: str | None = None
    summary: str | None = None  # distilled searchable form (retrieval key only)
    metadata: dict | None = None
    embedding: list[float] | None = None  # 384d; None until the embed step runs
    created_at: str | None = None  # UTC ISO; store fills with now() when None
    last_activity_at: str | None = None  # UTC ISO; store fills with now() when None

    def content_hash(self) -> str:
        return content_hash(
            self.source, self.scope, self.doc_key, self.chunk_ix,
            self.title, self.summary, self.content, self.metadata,
        )
