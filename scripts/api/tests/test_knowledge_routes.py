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
                           sources=None, limit=10, **_kwargs):
        calls.append({
            "query": query,
            "query_embedding": query_embedding,
            "scopes": scopes,
            "sources": sources,
            "limit": limit,
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


def test_search_test_mode_returns_empty_results(client, stubbed_retrieval, monkeypatch):
    from scripts.api import server

    monkeypatch.setattr(server, "test_mode", True)

    response = client.post("/knowledge/search", json={"query": "relay farm down"})

    assert response.status_code == 200
    assert response.json() == {"results": [], "retrieval": "fts-only"}
    assert stubbed_retrieval == []


# ── /knowledge/prior-evals ───────────────────────────────────────────


def test_prior_evals_searches_journal_and_evals_for_ticker(client, stubbed_retrieval):
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
