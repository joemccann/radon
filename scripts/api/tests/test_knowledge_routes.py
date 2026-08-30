"""Route tests for the knowledge retrieval surfaces (Phase 2).

POST /knowledge/search and GET /knowledge/prior-evals: authenticated,
retrieval offloaded to a thread, embedder degradation to FTS-only, compact
truncation, and input validation. hybrid_search and get_embedder are stubbed
so no model downloads and no DB is touched.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


EMBEDDING = [0.25] * 384


@pytest.fixture
def client(monkeypatch):
    from scripts.api import auth, server

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "test_mode", False)
    return TestClient(server.app)


@pytest.fixture
def anon_client(monkeypatch):
    """Anonymous remote caller: no trusted-local bypass, no key, no JWT."""
    from scripts.api import auth, server

    monkeypatch.setenv("CLERK_JWKS_URL", "https://example.test/.well-known/jwks.json")
    monkeypatch.delenv("RADON_AUTH_DISABLED", raising=False)
    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: False)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: False)
    monkeypatch.setattr(server, "verify_api_key", lambda request: None)

    async def _deny_jwt(request):
        raise HTTPException(status_code=401, detail="Invalid token")

    monkeypatch.setattr(server, "verify_clerk_jwt", _deny_jwt)
    monkeypatch.setattr(auth, "verify_clerk_jwt", _deny_jwt)
    return TestClient(server.app)


def _kb_row(**overrides) -> dict:
    row = {
        "id": 7,
        "source": "docs",
        "scope": "ops",
        "doc_key": "docs/cloud-services.md",
        "chunk_ix": 3,
        "title": "Cloud services runbook",
        "summary": "Hetzner services and deploy",
        "content": "c" * 2000,
        "metadata": {"path": "docs/cloud-services.md"},
        "created_at": "2026-07-01T00:00:00Z",
        "last_activity_at": "2026-07-15T00:00:00Z",
        "score": 0.031,
        "neighbors": [
            {"chunk_ix": 2, "content": "n" * 900},
            {"chunk_ix": 4, "content": "m" * 900},
            {"chunk_ix": 5, "content": "extra neighbor"},
        ],
    }
    row.update(overrides)
    return row


@pytest.fixture
def stubbed_retrieval(monkeypatch):
    """Stub the embedder + hybrid_search; records the hybrid_search call."""
    from scripts.api import server

    calls: list[dict] = []

    def fake_hybrid_search(db, query, *, query_embedding=None, scopes=None,
                           sources=None, limit=10, rerank=None, **_kwargs):
        calls.append({
            "query": query,
            "query_embedding": query_embedding,
            "scopes": scopes,
            "sources": sources,
            "limit": limit,
            "rerank": rerank,
        })
        return [_kb_row()]

    monkeypatch.setattr(server, "hybrid_search", fake_hybrid_search)
    monkeypatch.setattr(server, "get_embedder", lambda: lambda texts: [EMBEDDING for _ in texts])
    return calls


# ── auth ─────────────────────────────────────────────────────────────


def test_search_requires_auth(anon_client):
    response = anon_client.post("/knowledge/search", json={"query": "relay farm down"})
    assert response.status_code == 401


def test_prior_evals_requires_auth(anon_client):
    response = anon_client.get("/knowledge/prior-evals?ticker=EWY")
    assert response.status_code == 401


def test_knowledge_paths_not_auth_exempt():
    from scripts.api.server import AUTH_EXEMPT_PATHS

    assert "/knowledge/search" not in AUTH_EXEMPT_PATHS
    assert "/knowledge/prior-evals" not in AUTH_EXEMPT_PATHS


# ── /knowledge/search ────────────────────────────────────────────────


def test_search_happy_path_hybrid(client, stubbed_retrieval):
    response = client.post(
        "/knowledge/search",
        json={"query": "relay farm down", "scopes": ["ops"], "sources": ["incidents"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["retrieval"] == "hybrid"
    assert stubbed_retrieval == [{
        "query": "relay farm down",
        "query_embedding": EMBEDDING,
        "scopes": ["ops"],
        "sources": ["incidents"],
        "limit": 8,
        "rerank": None,
    }]

    (row,) = body["results"]
    assert row == {
        "source": "docs",
        "scope": "ops",
        "doc_key": "docs/cloud-services.md",
        "chunk_ix": 3,
        "title": "Cloud services runbook",
        "summary": "Hetzner services and deploy",
        "content": "c" * 2000,
        "metadata": {"path": "docs/cloud-services.md"},
        "score": 0.031,
        "last_activity_at": "2026-07-15T00:00:00Z",
        "neighbors": [
            {"chunk_ix": 2, "content": "n" * 900},
            {"chunk_ix": 4, "content": "m" * 900},
            {"chunk_ix": 5, "content": "extra neighbor"},
        ],
    }


def test_search_fts_only_when_embedder_unavailable(client, stubbed_retrieval, monkeypatch):
    from scripts.api import server

    monkeypatch.setattr(server, "get_embedder", lambda: None)

    response = client.post("/knowledge/search", json={"query": "checkpoint stalls"})

    assert response.status_code == 200
    assert response.json()["retrieval"] == "fts-only"
    assert stubbed_retrieval[0]["query_embedding"] is None


def test_search_fts_only_when_embedding_raises(client, stubbed_retrieval, monkeypatch):
    from scripts.api import server

    def broken_embedder(texts):
        raise RuntimeError("onnx exploded")

    monkeypatch.setattr(server, "get_embedder", lambda: broken_embedder)

    response = client.post("/knowledge/search", json={"query": "checkpoint stalls"})

    assert response.status_code == 200
    assert response.json()["retrieval"] == "fts-only"
    assert stubbed_retrieval[0]["query_embedding"] is None


def test_search_compact_truncates_content_and_neighbors(client, stubbed_retrieval):
    response = client.post(
        "/knowledge/search", json={"query": "relay farm down", "compact": True}
    )

    assert response.status_code == 200
    (row,) = response.json()["results"]
    assert row["content"] == "c" * 1200
    assert len(row["neighbors"]) == 2
    assert row["neighbors"][0] == {"chunk_ix": 2, "content": "n" * 400}
    assert row["neighbors"][1] == {"chunk_ix": 4, "content": "m" * 400}


def test_search_limit_clamped_to_20(client, stubbed_retrieval):
    response = client.post(
        "/knowledge/search", json={"query": "relay farm down", "limit": 99}
    )

    assert response.status_code == 200
    assert stubbed_retrieval[0]["limit"] == 20


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"query": ""},
        {"query": "   "},
        {"query": "q" * 501},
        {"query": 42},
    ],
)
def test_search_rejects_invalid_query_with_422(client, stubbed_retrieval, body):
    response = client.post("/knowledge/search", json=body)

    assert response.status_code == 422
    assert response.json()["detail"] == "query must be a string of 1-500 characters"
    assert stubbed_retrieval == []


def test_search_rejects_non_list_scopes_with_422(client, stubbed_retrieval):
    response = client.post(
        "/knowledge/search", json={"query": "ok", "scopes": "ops"}
    )

    assert response.status_code == 422
    assert stubbed_retrieval == []


def test_search_rejects_oversized_filter_lists_with_422(client, stubbed_retrieval):
    response = client.post(
        "/knowledge/search", json={"query": "ok", "scopes": ["ops"] * 11}
    )

    assert response.status_code == 422
    assert stubbed_retrieval == []


def test_search_rejects_overlong_filter_values_with_422(client, stubbed_retrieval):
    response = client.post(
        "/knowledge/search", json={"query": "ok", "sources": ["x" * 65]}
    )

    assert response.status_code == 422
    assert stubbed_retrieval == []


def test_search_maps_db_failure_to_sanitized_503(client, monkeypatch):
    from scripts.api import server

    def _raise(*_args, **_kwargs):
        # server.db_http, not scripts.api.db_http: the dual sys.path entries
        # make those two DISTINCT module objects — raise the class the route
        # actually catches.
        raise server.db_http.DbHttpError("Bearer secret-token leaked")

    monkeypatch.setattr(server, "hybrid_search", _raise)
    monkeypatch.setattr(server, "get_embedder", lambda: None)

    response = client.post("/knowledge/search", json={"query": "relay farm down"})

    assert response.status_code == 503
    assert response.json() == {"detail": "Knowledge retrieval is unavailable"}
    assert "secret" not in response.text.lower()


# ── transient-DB resilience (incident 2026-07-20: one hrana flake → 503,
# no server-side log, assistant fabricated lessons) ──────────────────


@pytest.fixture
def no_backoff(monkeypatch):
    from scripts.api import server

    monkeypatch.setattr(server, "_KNOWLEDGE_RETRY_BACKOFF_SECS", 0.0)


def test_retry_budget_is_bounded():
    from scripts.api import server

    assert server._KNOWLEDGE_RETRIEVAL_ATTEMPTS == 2
    assert 0 < server._KNOWLEDGE_RETRY_BACKOFF_SECS <= 0.5


def test_search_retries_once_on_transient_db_error(client, monkeypatch, no_backoff):
    from scripts.api import server

    calls: list[str] = []

    def flaky_hybrid_search(db, query, **_kwargs):
        calls.append(query)
        if len(calls) == 1:
            raise server.db_http.DbHttpError("timeout: _ssl.c:980 read timed out")
        return [_kb_row()]

    monkeypatch.setattr(server, "hybrid_search", flaky_hybrid_search)
    monkeypatch.setattr(server, "get_embedder", lambda: None)

    response = client.post("/knowledge/search", json={"query": "relay farm down"})

    assert response.status_code == 200
    assert calls == ["relay farm down", "relay farm down"]
    assert response.json()["retrieval"] == "fts-only"


def test_prior_evals_retries_once_on_transient_db_error(client, monkeypatch, no_backoff):
    from scripts.api import server

    calls: list[str] = []

    def flaky_hybrid_search(db, query, **_kwargs):
        calls.append(query)
        if len(calls) == 1:
            raise server.db_http.DbHttpError("timeout: _ssl.c:980 read timed out")
        return [_kb_row(source="journal", doc_key="trade_log:6")]

    monkeypatch.setattr(server, "hybrid_search", flaky_hybrid_search)
    monkeypatch.setattr(server, "get_embedder", lambda: None)

    response = client.get("/knowledge/prior-evals?ticker=EWY")

    assert response.status_code == 200
    assert calls == ["EWY", "EWY"]


def test_search_retry_exhausted_returns_503_and_logs_route(
    client, monkeypatch, no_backoff, caplog
):
    import logging

    from scripts.api import server

    calls: list[str] = []

    def always_down(db, query, **_kwargs):
        calls.append(query)
        raise server.db_http.DbHttpError("HranaError: stream reset")

    monkeypatch.setattr(server, "hybrid_search", always_down)
    monkeypatch.setattr(server, "get_embedder", lambda: None)

    with caplog.at_level(logging.WARNING, logger="radon.api"):
        response = client.post("/knowledge/search", json={"query": "relay farm down"})

    assert response.status_code == 503
    assert response.json() == {"detail": "Knowledge retrieval is unavailable"}
    assert calls == ["relay farm down", "relay farm down"]  # exactly one retry

    [record] = [r for r in caplog.records if "retrieval failed" in r.getMessage()]
    message = record.getMessage()
    assert "/knowledge/search" in message
    assert "after 2 attempts" in message
    assert "HranaError: stream reset" in message


def test_prior_evals_retry_exhausted_logs_its_own_route(
    client, monkeypatch, no_backoff, caplog
):
    import logging

    from scripts.api import server

    def always_down(db, query, **_kwargs):
        raise server.db_http.DbHttpError("HranaError: stream reset")

    monkeypatch.setattr(server, "hybrid_search", always_down)
    monkeypatch.setattr(server, "get_embedder", lambda: None)

    with caplog.at_level(logging.WARNING, logger="radon.api"):
        response = client.get("/knowledge/prior-evals?ticker=EWY")

    assert response.status_code == 503
    assert response.json() == {"detail": "Knowledge retrieval is unavailable"}

    [record] = [r for r in caplog.records if "retrieval failed" in r.getMessage()]
    assert "/knowledge/prior-evals" in record.getMessage()


def test_retry_exhausted_log_is_scrubbed_of_secrets(
    client, monkeypatch, no_backoff, caplog
):
    """The log line carries only the DbHttpError message (already scrubbed by
    db_http — type + message, never SQL text or tokens)."""
    import logging

    from scripts.api import server

    def always_down(db, query, **_kwargs):
        raise server.db_http.DbHttpError("HTTPError: HTTP Error 503: Service Unavailable")

    monkeypatch.setattr(server, "hybrid_search", always_down)
    monkeypatch.setattr(server, "get_embedder", lambda: None)

    with caplog.at_level(logging.WARNING, logger="radon.api"):
        response = client.post("/knowledge/search", json={"query": "relay farm down"})

    assert response.status_code == 503
    [record] = [r for r in caplog.records if "retrieval failed" in r.getMessage()]
    assert "relay farm down" not in record.getMessage()  # no query text either


def test_search_test_mode_returns_empty_results(client, stubbed_retrieval, monkeypatch):
    from scripts.api import server

    monkeypatch.setattr(server, "test_mode", True)

    response = client.post("/knowledge/search", json={"query": "relay farm down"})

    assert response.status_code == 200
    assert response.json() == {"results": [], "retrieval": "fts-only"}
    assert stubbed_retrieval == []


# ── /knowledge/prior-evals ───────────────────────────────────────────


def test_prior_evals_searches_journal_and_evals_for_ticker(client, stubbed_retrieval):
    from scripts.api import server

    response = client.get("/knowledge/prior-evals?ticker=ewy")

    assert response.status_code == 200
    body = response.json()
    assert body["ticker"] == "EWY"
    assert body["retrieval"] == "hybrid"
    assert len(body["results"]) == 1
    assert stubbed_retrieval == [{
        "query": "EWY",
        "query_embedding": EMBEDDING,
        "scopes": None,
        "sources": ["journal", "evals"],
        "limit": 8,
        "rerank": server._thesis_first,
    }]


def test_prior_evals_compact_truncates(client, stubbed_retrieval):
    response = client.get("/knowledge/prior-evals?ticker=EWY&compact=true")

    assert response.status_code == 200
    (row,) = response.json()["results"]
    assert row["content"] == "c" * 1200
    assert len(row["neighbors"]) == 2


@pytest.mark.parametrize("ticker", ["TOOLONG", "BR-K", "SPY%24", ""])
def test_prior_evals_rejects_invalid_ticker_with_400(client, stubbed_retrieval, ticker):
    response = client.get(f"/knowledge/prior-evals?ticker={ticker}")

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid ticker"
    assert stubbed_retrieval == []


def test_prior_evals_missing_ticker_is_422(client, stubbed_retrieval):
    response = client.get("/knowledge/prior-evals")

    assert response.status_code == 422
    assert stubbed_retrieval == []


def test_prior_evals_limit_clamped(client, stubbed_retrieval):
    response = client.get("/knowledge/prior-evals?ticker=EWY&limit=50")

    assert response.status_code == 200
    assert stubbed_retrieval[0]["limit"] == 20


def test_prior_evals_test_mode_returns_empty_results(client, stubbed_retrieval, monkeypatch):
    from scripts.api import server

    monkeypatch.setattr(server, "test_mode", True)

    response = client.get("/knowledge/prior-evals?ticker=EWY")

    assert response.status_code == 200
    assert response.json() == {"ticker": "EWY", "results": [], "retrieval": "fts-only"}
    assert stubbed_retrieval == []


# ── prior-evals thesis-first rerank (incident 2026-07-20: bare-ticker
# BM25 let raw IB fill rows crowd every thesis doc out of the top-6) ──


def _scored(score: float, source: str, doc_key: str) -> tuple[float, dict]:
    return (score, {"source": source, "doc_key": doc_key, "title": doc_key})


def test_thesis_first_orders_thesis_docs_before_fill_rows():
    """Pool shaped like the EWY incident: fill rows outscore every thesis."""
    from scripts.api.server import _thesis_first

    fill_bag = _scored(0.02590, "journal", "0001505f.6a4fa63b.01.01")
    fill_call = _scored(0.02538, "journal", "9885993847")
    garch_eval = _scored(0.01639, "evals", "garch-convergence-china-etf-2026-03-24.html")
    thesis_rr = _scored(0.01587, "journal", "trade_log:640")
    thesis_old = _scored(0.01471, "journal", "trade_log:6")

    reranked = _thesis_first([fill_bag, fill_call, garch_eval, thesis_rr, thesis_old])

    assert reranked == [garch_eval, thesis_rr, thesis_old, fill_bag, fill_call]


def test_thesis_first_preserves_score_order_within_each_band():
    from scripts.api.server import _thesis_first

    rows = [
        _scored(0.030, "journal", "fill-a"),
        _scored(0.020, "evals", "eval-a"),
        _scored(0.019, "journal", "trade_log:1"),
        _scored(0.018, "journal", "fill-b"),
        _scored(0.010, "evals", "eval-b"),
    ]

    reranked = _thesis_first(rows)

    assert [pair[1]["doc_key"] for pair in reranked] == [
        "eval-a", "trade_log:1", "eval-b", "fill-a", "fill-b",
    ]


def test_thesis_first_is_identity_when_no_thesis_docs():
    from scripts.api.server import _thesis_first

    rows = [_scored(0.03, "journal", "fill-a"), _scored(0.02, "journal", "fill-b")]

    assert _thesis_first(rows) == rows


def test_search_route_does_not_rerank(client, stubbed_retrieval):
    response = client.post("/knowledge/search", json={"query": "EWY risk reversal"})

    assert response.status_code == 200
    assert stubbed_retrieval[0]["rerank"] is None


# ── embedder cache path (serving from FastAPI must not re-download) ──


def _import_failure():
    raise ImportError("no fastembed in tests")


def test_build_embedder_defaults_fastembed_cache_to_user_cache(monkeypatch, capsys):
    from knowledge import embed as embed_mod

    monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)
    monkeypatch.setattr(embed_mod, "_import_text_embedding", _import_failure)

    assert embed_mod._build_embedder() is None
    assert os.environ["FASTEMBED_CACHE_PATH"] == os.path.expanduser("~/.cache/fastembed")
    monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)


def test_build_embedder_keeps_explicit_cache_path(monkeypatch, capsys):
    from knowledge import embed as embed_mod

    monkeypatch.setenv("FASTEMBED_CACHE_PATH", "/custom/fastembed-cache")
    monkeypatch.setattr(embed_mod, "_import_text_embedding", _import_failure)

    assert embed_mod._build_embedder() is None
    assert os.environ["FASTEMBED_CACHE_PATH"] == "/custom/fastembed-cache"


# ── FTS-only fallback + startup warm (2026-08-30 03:05Z post-deploy 503s) ──


def test_search_retries_fts_only_after_hybrid_db_error(client, monkeypatch, no_backoff):
    """The vector leg is the statement that blows the Hrana bound under
    load; the retry must drop it instead of re-running it."""
    from scripts.api import server

    embeddings: list = []

    def flaky_hybrid_search(db, query, *, query_embedding=None, **_kwargs):
        embeddings.append(query_embedding)
        if len(embeddings) == 1:
            raise server.db_http.DbHttpError("timeout: _ssl.c:980 read timed out")
        return [_kb_row()]

    monkeypatch.setattr(server, "hybrid_search", flaky_hybrid_search)
    monkeypatch.setattr(server, "get_embedder", lambda: lambda texts: [EMBEDDING for _ in texts])

    response = client.post("/knowledge/search", json={"query": "3 DTE risk reversals"})

    assert response.status_code == 200
    assert embeddings == [EMBEDDING, None]
    assert response.json()["retrieval"] == "fts-only"


@pytest.mark.asyncio
async def test_warm_knowledge_embedder_on_startup_loads_off_loop(monkeypatch):
    from scripts.api import server

    calls: list[str] = []
    monkeypatch.setattr(server, "test_mode", False)
    monkeypatch.setattr(server, "get_embedder", lambda: calls.append("built") or (lambda t: []))

    await server._warm_knowledge_embedder_on_startup()

    assert calls == ["built"]


@pytest.mark.asyncio
async def test_warm_knowledge_embedder_skipped_in_test_mode(monkeypatch):
    from scripts.api import server

    monkeypatch.setattr(server, "test_mode", True)
    monkeypatch.setattr(server, "get_embedder", lambda: pytest.fail("demo VM must not load the model"))

    await server._warm_knowledge_embedder_on_startup()


def test_lifespan_schedules_knowledge_embedder_warm():
    source = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    assert "asyncio.create_task(_warm_knowledge_embedder_on_startup())" in source
