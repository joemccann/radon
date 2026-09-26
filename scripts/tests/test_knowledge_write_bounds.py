"""Hrana write cap after 2048-d embedding_v2.

A document commit must survive an 8 MiB pipeline cap: vectors are bound
once, oversized documents split into an atomic text commit plus bounded
vector follow-ups, and one source failure stays in ``errors``.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from knowledge import http_db, ingest
from knowledge.backfill_v2 import backfill_missing_v2
from knowledge.embed import EMBEDDING_DIM, EMBEDDING_DIM_V2
from knowledge.schema import KnowledgeDoc
from knowledge.store import upsert_documents
from test_knowledge_embed_v2 import _db as _v2_db
from test_knowledge_http import AtomicServer


def _raw(key="one", **kwargs):
    values = dict(source="docs", scope="ops", doc_key=key, content="durable")
    values.update(kwargs)
    return KnowledgeDoc(**values)


def _vector_args(payload):
    found = []
    for _url, body, _timeout in payload:
        for request in body["requests"]:
            steps = request.get("batch", {}).get("steps", [])
            for step in steps:
                for arg in step.get("stmt", {}).get("args", []):
                    if arg.get("type") == "blob":
                        found.append(arg["base64"])
                    elif arg.get("type") == "text" and arg.get("value", "").startswith("["):
                        found.append(arg["value"])
    return found


@pytest.fixture
def server(monkeypatch, tmp_path):
    monkeypatch.setenv("TURSO_DB_URL", "libsql://knowledge-test.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "fake-test-token")
    monkeypatch.setattr(http_db, "_refuse_pytest_pollution", lambda: None)
    path = tmp_path / "knowledge.sqlite"
    fake = AtomicServer(path)
    fake.db.execute("ALTER TABLE knowledge ADD COLUMN embedding_v2 BLOB")
    monkeypatch.setattr(http_db.urllib.request, "build_opener", lambda *a: fake)
    return fake


def test_oversized_document_ingests(server, monkeypatch):
    """(a) a document whose serialized size is over the cap ingests."""
    monkeypatch.setattr(http_db, "MAX_REQUEST_BYTES", 32_000)
    chunks = [
        _raw(
            chunk_ix=i,
            content=f"chunk-{i}",
            embedding=[0.25] * EMBEDDING_DIM,
            embedding_v2=[0.5] * EMBEDDING_DIM_V2,
        )
        for i in range(4)
    ]

    def fetch(_db):
        return iter(chunks)

    module = SimpleNamespace(SOURCE="docs", SCOPE="ops", fetch=fetch)
    result = ingest.ingest_source(
        http_db.Connection(),
        module,
        distill_enabled=False,
        embed_enabled=False,
        db_factory=http_db.Connection,
    )
    assert result["inserted"] == 4
    rows = server.db.execute(
        "SELECT chunk_ix, embedding IS NOT NULL, embedding_v2 IS NOT NULL "
        "FROM knowledge ORDER BY chunk_ix"
    ).fetchall()
    assert rows == [(i, 1, 1) for i in range(4)]
    assert server.db.execute("SELECT count(*) FROM knowledge_fts").fetchone()[0] == 4
    assert len(_vector_args(server.calls)) == 8


def test_size_error_splits_and_retries(server, monkeypatch):
    """(b) a simulated Hrana size error splits instead of failing."""
    original = http_db.Connection.execute_transaction
    calls = {"n": 0}

    def flaky(self, statements):
        calls["n"] += 1
        wide = []
        for _sql, args in statements:
            for arg in args:
                if isinstance(arg, (bytes, bytearray)) and len(arg) >= EMBEDDING_DIM_V2 * 4:
                    wide.append(arg)
                elif isinstance(arg, str) and arg.count(",") >= EMBEDDING_DIM_V2 - 1:
                    wide.append(arg)
        if len(wide) > 1:
            raise http_db.HranaHttpError("Hrana request exceeds bounded size")
        return original(self, statements)

    monkeypatch.setattr(http_db.Connection, "execute_transaction", flaky)
    docs = [
        _raw(
            chunk_ix=i,
            content=f"part-{i}",
            embedding=[0.2] * 4,
            embedding_v2=[0.3] * EMBEDDING_DIM_V2,
        )
        for i in range(4)
    ]
    result = upsert_documents(http_db.Connection(), docs)
    assert result["inserted"] == 4
    assert calls["n"] > 1
    stored = server.db.execute(
        "SELECT count(*) FROM knowledge WHERE embedding_v2 IS NOT NULL"
    ).fetchone()[0]
    assert stored == 4


def test_each_vector_is_sent_once(server):
    """(c) each vector is bound once per chunk."""
    docs = [
        _raw(
            chunk_ix=i,
            content=f"body-{i}-unique",
            embedding=[0.25 + i] * EMBEDDING_DIM,
            embedding_v2=[0.5 + i] * 8,
        )
        for i in range(3)
    ]
    upsert_documents(http_db.Connection(), docs)
    blobs = _vector_args(server.calls)
    assert len(blobs) == 6  # 384-d and v2, once each, three chunks


def test_backfill_respects_byte_bound(monkeypatch):
    """(d) backfill_v2 keeps each write request under the byte budget."""
    from knowledge.bounded_write import vector_payload
    from knowledge.http_db import transaction_request_bytes
    from knowledge.store import _BACKFILL_EMBEDDING_V2_SQL

    db = _v2_db()
    upsert_documents(db, [
        KnowledgeDoc(source="docs", scope="ops", doc_key=f"n{i}", content=f"row {i}")
        for i in range(4)
    ])
    vector = [0.1] * EMBEDDING_DIM_V2
    one = transaction_request_bytes([
        (_BACKFILL_EMBEDDING_V2_SQL, (vector_payload(vector), 1)),
    ])
    monkeypatch.setattr(http_db, "write_budget_bytes", lambda: one)
    begins = []

    class _Counting:
        def execute(self, sql, args=()):
            if str(sql).lstrip().upper().startswith("BEGIN"):
                begins.append(1)
            return db.execute(sql, args)

        def __getattr__(self, name):
            return getattr(db, name)

    def embed(texts):
        return [vector for _ in texts]

    result = backfill_missing_v2(_Counting(), embed, batch_size=16, min_interval=0, progress=lambda _line: None)
    assert result["updated"] == 4
    assert result["remaining"] == 0
    assert len(begins) == 4


def test_one_oversized_row_does_not_fail_the_source(server, monkeypatch):
    monkeypatch.setattr(http_db, "MAX_REQUEST_BYTES", 8_000)

    def fetch(_db):
        yield _raw(key="ok", content="small", embedding=[0.1] * 8)
        yield _raw(key="huge", content="x" * 20_000, embedding=[0.1] * 8)

    module = SimpleNamespace(SOURCE="docs", SCOPE="ops", fetch=fetch)
    result = ingest.ingest_source(
        http_db.Connection(),
        module,
        distill_enabled=False,
        embed_enabled=False,
        db_factory=http_db.Connection,
    )
    assert result["inserted"] == 1
    assert result["row_errors"][0]["doc_key"] == "huge"
    assert server.db.execute("SELECT doc_key FROM knowledge").fetchall() == [("ok",)]


def test_v2_existing_page_stays_under_the_response_cap():
    limit = ingest._existing_page_limit(True)
    budget = http_db.MAX_RESPONSE_BYTES - http_db.MAX_RESPONSE_BYTES // 8
    assert limit < ingest._EXISTING_BATCH_ROWS
    assert limit * ingest._EXISTING_ASSUMED_V2_ROW_BYTES <= budget


def test_presend_size_error_leaves_the_handle_usable(server, monkeypatch):
    db = http_db.Connection()
    monkeypatch.setattr(http_db, "MAX_REQUEST_BYTES", 32)
    with pytest.raises(http_db.HranaHttpError, match="bounded size"):
        db.execute_transaction([("SELECT 1", ()), ("SELECT 2", ())])
    monkeypatch.setattr(http_db, "MAX_REQUEST_BYTES", 8 * 1024 * 1024)
    db.execute_transaction([("SELECT 1", ())])
    assert server.calls


def test_backfill_row_over_the_cap_does_not_abort(monkeypatch):
    db = _v2_db()
    upsert_documents(db, [
        KnowledgeDoc(source="docs", scope="ops", doc_key=f"n{i}", content=f"row {i}")
        for i in range(2)
    ])
    monkeypatch.setattr(http_db, "write_budget_bytes", lambda: 1)

    def embed(texts):
        return [[0.1] * EMBEDDING_DIM_V2 for _ in texts]

    result = backfill_missing_v2(db, embed, batch_size=8, min_interval=0, progress=lambda _line: None)
    assert result["updated"] == 0
    assert result["remaining"] == 2
    assert [item["id"] for item in result["row_errors"]] == [1, 2]


def test_one_source_failure_is_reported_in_errors(monkeypatch, capsys):
    """(e) a failure in one source is still reported in errors."""
    import contextlib

    import db.service_cycle as service_cycle_mod
    import knowledge.sources as sources_mod

    def fetch_ok(_db):
        yield KnowledgeDoc(source="docs", scope="ops", doc_key="k", content="body")

    def fetch_bad(_db):
        raise RuntimeError("docs connector exploded")

    monkeypatch.setattr(ingest, "_fresh_db", lambda: object())
    monkeypatch.setattr(ingest, "_SOURCE_RETRY_BACKOFF_SECS", 0)
    monkeypatch.setattr(service_cycle_mod, "service_cycle", lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(sources_mod, "ALL_SOURCES", {
        "docs": SimpleNamespace(SOURCE="docs", SCOPE="ops", fetch=fetch_ok),
        "incidents": SimpleNamespace(SOURCE="incidents", SCOPE="ops", fetch=fetch_bad),
    })

    def ingest_one(_db, module, **_kwargs):
        if module.SOURCE == "incidents":
            raise RuntimeError("docs connector exploded")
        return {"source": module.SOURCE, "inserted": 1}

    monkeypatch.setattr(ingest, "ingest_source", ingest_one)
    with pytest.raises(RuntimeError, match="incidents"):
        ingest.main(["--source", "all", "--no-distill", "--no-embed"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "incidents" in payload["errors"]
    assert "docs" in payload["sources"]
    assert "docs" not in payload["errors"]
