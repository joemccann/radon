"""PERF-070: unchanged knowledge ingest must not ship embedding blobs.

`radon-knowledge` loads every stored chunk before it decides a document is
unchanged. The skip only needs presence flags. The 384-d and 2048-d vectors
are needed only when a document is actually reprocessed. Shipping both blobs
for a fully skipped corpus is the hourly memory spike.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import libsql_experimental as libsql

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from knowledge import ingest as ingest_mod  # noqa: E402
from knowledge.embed import EMBEDDING_DIM, EMBEDDING_DIM_V2  # noqa: E402
from knowledge.schema import KnowledgeDoc  # noqa: E402
from knowledge.store import upsert_documents  # noqa: E402

_MIGRATION = _SCRIPTS / "db" / "migrations" / "0028_knowledge.sql"
_MIGRATION_V2 = _SCRIPTS / "db" / "migrations" / "0087_knowledge_embedding_v2.sql"
_BOOTSTRAP = (
    "CREATE TABLE IF NOT EXISTS schema_migrations "
    "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
)
N_DOCS = 8
_ONE_VECTOR_BYTES = EMBEDDING_DIM * 4 + EMBEDDING_DIM_V2 * 4


def _split(sql: str) -> list[str]:
    stripped = "\n".join(re.sub(r"^\s*--.*$", "", line) for line in sql.splitlines())
    return [part.strip() for part in re.split(r";\s*$", stripped, flags=re.MULTILINE) if part.strip()]


class _Replay:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _BlobCounter:
    """Counts embedding-column bytes returned by SELECTs. Presence probes do not count."""

    def __init__(self, inner):
        self.inner = inner
        self.blob_bytes = 0
        self.blob_selects = 0
        self.presence_selects = 0

    def execute(self, sql, *args):
        result = self.inner.execute(sql, *args)
        if not isinstance(sql, str) or not sql.lstrip().upper().startswith("SELECT"):
            return result
        rows = result.fetchall()
        if _ships_embedding_blob(sql):
            self.blob_selects += 1
            self.blob_bytes += sum(
                len(cell) for row in rows for cell in row
                if isinstance(cell, (bytes, bytearray, memoryview))
            )
        elif "embedding IS NOT NULL" in sql or "embedding_v2 IS NOT NULL" in sql:
            self.presence_selects += 1
        return _Replay(rows)

    def commit(self):
        self.inner.commit()

    def rollback(self):
        self.inner.rollback()


def _ships_embedding_blob(sql: str) -> bool:
    stripped = re.sub(r"embedding_v2\s+IS\s+NOT\s+NULL", "", sql, flags=re.I)
    stripped = re.sub(r"embedding\s+IS\s+NOT\s+NULL", "", stripped, flags=re.I)
    return bool(re.search(r"\bembedding(_v2)?\b", stripped, flags=re.I))


def _db():
    conn = libsql.connect(":memory:")
    conn.execute(_BOOTSTRAP)
    for stmt in _split(_MIGRATION.read_text(encoding="utf-8")):
        conn.execute(stmt)
    for stmt in _split(_MIGRATION_V2.read_text(encoding="utf-8")):
        conn.execute(stmt)
    conn.commit()
    return conn


def _doc(index: int, *, content: str | None = None) -> KnowledgeDoc:
    body = content if content is not None else f"body {index}"
    return KnowledgeDoc(
        source="newsfeed",
        scope="research",
        doc_key=f"post-{index}",
        chunk_ix=0,
        title=f"Title {index}",
        summary=f"summary {index}",
        content=body,
        metadata={"tags": ["NVDA"]},
        embedding=[0.1] * EMBEDDING_DIM,
        embedding_v2=[0.2] * EMBEDDING_DIM_V2,
    )


def _module(docs):
    from types import SimpleNamespace

    return SimpleNamespace(SOURCE="newsfeed", SCOPE="research", fetch=lambda _db: iter(docs))


def _seed(conn, docs):
    upsert_documents(conn, docs)


def test_unchanged_corpus_ships_no_embedding_blobs(monkeypatch):
    docs = [_doc(i) for i in range(N_DOCS)]
    conn = _db()
    _seed(conn, docs)
    counter = _BlobCounter(conn)
    monkeypatch.setattr(ingest_mod, "get_embedder", lambda: (lambda texts: (_ for _ in ()).throw(AssertionError("re-embedded"))))
    monkeypatch.setattr(ingest_mod, "embed_passages", lambda texts: (_ for _ in ()).throw(AssertionError("re-embedded v2")))

    result = ingest_mod.ingest_source(counter, _module(docs), distill_enabled=True, embed_enabled=True)

    assert result["skipped_docs"] == N_DOCS
    assert counter.blob_bytes == 0, (
        f"shipped {counter.blob_bytes} embedding bytes across {counter.blob_selects} selects"
    )
    assert counter.blob_selects == 0
    assert counter.presence_selects >= 1


def test_one_changed_document_ships_only_that_documents_vectors(monkeypatch):
    stored = [_doc(i) for i in range(N_DOCS)]
    conn = _db()
    _seed(conn, stored)
    counter = _BlobCounter(conn)
    # Connectors do not carry vectors. Only the changed document is reprocessed.
    fetched = []
    for index in range(N_DOCS):
        doc = _doc(index, content="body 3 changed" if index == 3 else None)
        doc.embedding = None
        doc.embedding_v2 = None
        fetched.append(doc)
    embeds = []

    def embed_batch(texts):
        embeds.append(list(texts))
        return [[0.3] * EMBEDDING_DIM for _ in texts]

    # embedding_text prefers the summary, so a content-only change reuses the
    # stored vector when distill does not run. Stub distill so the re-embed
    # does not depend on a live model key.
    monkeypatch.setattr(
        ingest_mod,
        "distill",
        lambda title, content, **kwargs: {"summary": f"distilled: {content}", "tickers": []},
    )
    monkeypatch.setattr(ingest_mod, "get_embedder", lambda: embed_batch)
    monkeypatch.setattr(ingest_mod, "embed_passages", lambda texts: [[0.4] * EMBEDDING_DIM_V2 for _ in texts])

    result = ingest_mod.ingest_source(counter, _module(fetched), distill_enabled=True, embed_enabled=True)

    assert result["skipped_docs"] == N_DOCS - 1
    assert counter.blob_bytes == _ONE_VECTOR_BYTES
    assert len(embeds) == 1
