"""2048-d knowledge embeddings: NVIDIA input types, fallback, backfill, migration order."""
from __future__ import annotations

import json
import re
import struct
import sys
from pathlib import Path

import libsql_experimental as libsql
import pytest

_PROJECT = Path(__file__).resolve().parents[2]
_SCRIPTS = _PROJECT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from knowledge.backfill_v2 import backfill_missing_v2  # noqa: E402
from knowledge.embed import (  # noqa: E402
    EMBEDDING_DIM, EMBEDDING_DIM_V2, EMBEDDING_MODEL_V2, embed_passages, embed_query,
    resolve_query_vector,
)
from knowledge.ingest import _embed_docs, _restore_unchanged_enrichment  # noqa: E402
from knowledge.retrieve import _vector_top_k_search  # noqa: E402
from knowledge.schema import KnowledgeDoc  # noqa: E402
from knowledge.store import upsert_documents  # noqa: E402

_MIGRATION = _SCRIPTS / "db" / "migrations" / "0028_knowledge.sql"
_MIGRATION_V2 = _SCRIPTS / "db" / "migrations" / "0087_knowledge_embedding_v2.sql"
_BOOTSTRAP = (
    "CREATE TABLE IF NOT EXISTS schema_migrations "
    "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
)


def _split(sql: str) -> list[str]:
    stripped = "\n".join(re.sub(r"^\s*--.*$", "", line) for line in sql.splitlines())
    return [part.strip() for part in re.split(r";\s*$", stripped, flags=re.MULTILINE) if part.strip()]


class _Response:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload)
        self.headers = {}

    def json(self):
        return self._payload


def _db():
    conn = libsql.connect(":memory:")
    conn.execute(_BOOTSTRAP)
    for stmt in _split(_MIGRATION.read_text(encoding="utf-8")):
        conn.execute(stmt)
    for stmt in _split(_MIGRATION_V2.read_text(encoding="utf-8")):
        conn.execute(stmt)
    conn.commit()
    return conn


def _vectors(count, dim=EMBEDDING_DIM_V2, status=200):
    def post(url, **kwargs):
        body = kwargs["json"]
        post.bodies.append(body)
        if status != 200:
            return _Response(status, {"error": "unavailable"})
        data = [{"index": index, "embedding": [float(index)] * dim} for index in range(len(body["input"]))]
        return _Response(200, {"data": data})

    post.bodies = []
    return post


def test_query_and_passage_input_types(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    post = _vectors(1)
    query = embed_query(["what failed"], post=post)
    passage = embed_passages(["the relay failed"], post=post)
    assert post.bodies[0]["input_type"] == "query"
    assert post.bodies[1]["input_type"] == "passage"
    assert post.bodies[0]["model"] == EMBEDDING_MODEL_V2
    assert post.bodies[0]["dimensions"] == EMBEDDING_DIM_V2
    assert len(query[0]) == EMBEDDING_DIM_V2
    assert len(passage[0]) == EMBEDDING_DIM_V2


def test_non_2048_payload_is_rejected(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    with pytest.raises(RuntimeError, match="2048"):
        embed_passages(["row"], post=_vectors(1, dim=384))


def test_nvidia_failure_falls_back_to_bge_then_fts(monkeypatch):
    monkeypatch.setenv("RADON_KB_EMBED_BACKEND", "nvidia")
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    monkeypatch.setattr("knowledge.embed.time.sleep", lambda _seconds: None)
    local_calls = []

    def local(texts):
        local_calls.append(list(texts))
        return [[0.25] * EMBEDDING_DIM]

    vector = resolve_query_vector("relay", local_embedder=local, post=_vectors(1, status=503))
    assert vector == [0.25] * EMBEDDING_DIM
    assert local_calls == [["relay"]]

    def broken(texts):
        raise RuntimeError("onnx")

    assert resolve_query_vector("relay", local_embedder=broken, post=_vectors(1, status=503)) is None


def test_local_backend_does_not_call_nvidia(monkeypatch):
    monkeypatch.setenv("RADON_KB_EMBED_BACKEND", "local")

    def post(url, **kwargs):
        raise AssertionError("nvidia called")

    vector = resolve_query_vector("relay", local_embedder=lambda texts: [[1.0] * EMBEDDING_DIM], post=post)
    assert vector == [1.0] * EMBEDDING_DIM


def test_vector_search_picks_the_index_for_the_query_width():
    class _Db:
        def __init__(self):
            self.sql = []

        def execute(self, sql, args=()):
            self.sql.append(sql)
            return self

        def fetchall(self):
            return []

    wide = _Db()
    _vector_top_k_search(wide, [0.0] * EMBEDDING_DIM_V2, 4)
    assert "vector_top_k('idx_knowledge_embedding_v2'" in wide.sql[0]
    narrow = _Db()
    _vector_top_k_search(narrow, [0.0] * EMBEDDING_DIM, 4)
    assert "vector_top_k('idx_knowledge_embedding'" in narrow.sql[0]
    assert "embedding_v2" not in narrow.sql[0]


def test_migration_creates_v2_index_before_data():
    sql = _MIGRATION_V2.read_text(encoding="utf-8")
    assert sql.index("CREATE INDEX IF NOT EXISTS idx_knowledge_embedding_v2") < sql.index("schema_migrations")
    assert "INSERT INTO knowledge" not in sql
    db = _db()
    names = {row[0] for row in db.execute("SELECT name FROM sqlite_master").fetchall()}
    assert "idx_knowledge_embedding_v2" in names
    upsert_documents(db, [KnowledgeDoc(
        source="docs", scope="ops", doc_key="note", content="after the index",
        embedding_v2=[0.0] * EMBEDDING_DIM_V2,
    )])
    stored = db.execute("SELECT embedding_v2 IS NOT NULL FROM knowledge").fetchone()
    assert stored[0] in (1, True)


def test_backfill_is_dry_run_resumable_and_idempotent():
    db = _db()
    upsert_documents(db, [
        KnowledgeDoc(source="docs", scope="ops", doc_key=f"n{i}", content=f"row {i}")
        for i in range(3)
    ])

    def embed(texts):
        embed.calls += 1
        return [[0.1] * EMBEDDING_DIM_V2 for _ in texts]

    embed.calls = 0
    dry = backfill_missing_v2(db, embed, dry_run=True, progress=lambda _line: None)
    assert dry == {"candidates": 3, "updated": 0, "remaining": 3, "dry_run": True}
    assert embed.calls == 0
    first = backfill_missing_v2(db, embed, batch_size=2, limit=2, min_interval=0, progress=lambda _line: None)
    assert first["updated"] == 2
    assert db.execute("SELECT COUNT(*) FROM knowledge WHERE embedding_v2 IS NULL").fetchone()[0] == 1
    second = backfill_missing_v2(db, embed, batch_size=2, progress=lambda _line: None)
    assert second["updated"] == 1 and second["remaining"] == 0
    third = backfill_missing_v2(db, embed, progress=lambda _line: None)
    assert third["updated"] == 0 and third["remaining"] == 0


def test_ingest_reuses_only_matching_widths(monkeypatch):
    doc = KnowledgeDoc(
        source="docs", scope="ops", doc_key="note", content="same body",
        title="Note", summary="same",
    )
    blob384 = struct.pack(f"<{EMBEDDING_DIM}f", *([0.5] * EMBEDDING_DIM))
    blob2048 = struct.pack(f"<{EMBEDDING_DIM_V2}f", *([0.25] * EMBEDDING_DIM_V2))
    stored = {
        doc.doc_key: {0: type("Chunk", (), {})()}
    }
    from knowledge.ingest import _StoredChunk
    stored[doc.doc_key][0] = _StoredChunk(
        doc.content, doc.title, None, doc.summary, blob384, doc.content_hash(), blob2048,
    )
    _restore_unchanged_enrichment([doc], stored)
    assert doc.embedding == [0.5] * EMBEDDING_DIM
    assert doc.embedding_v2 == [0.25] * EMBEDDING_DIM_V2

    other = KnowledgeDoc(
        source="docs", scope="ops", doc_key="note", content="same body",
        title="Note", summary="same",
    )
    short = struct.pack("<4f", 1, 2, 3, 4)
    stored[doc.doc_key][0] = _StoredChunk(
        other.content, other.title, None, other.summary, short, other.content_hash(), short,
    )
    _restore_unchanged_enrichment([other], stored)
    assert other.embedding is None and other.embedding_v2 is None

    monkeypatch.setenv("RADON_KB_EMBED_DUAL_WRITE", "1")
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    fresh = KnowledgeDoc(source="docs", scope="ops", doc_key="new", content="body")

    def passages(texts, post=None):
        assert texts == ["body"]
        return [[0.7] * EMBEDDING_DIM_V2]

    monkeypatch.setattr("knowledge.ingest.embed_passages", passages)
    _embed_docs([fresh], lambda texts: [[0.2] * EMBEDDING_DIM])
    assert fresh.embedding == [0.2] * EMBEDDING_DIM
    assert len(fresh.embedding_v2) == EMBEDDING_DIM_V2
