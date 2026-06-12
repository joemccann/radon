"""Tests for the FastAPI /llm-token-index endpoint.

Pins the contract the Next.js proxy + UI hook depend on:
  - empty table → empty list (NOT 404)
  - non-empty table → rows sorted ASC by date
  - days param clamps + flows through to the bounded hrana read
  - 5-min TTL cache short-circuits repeat reads

The route reads Turso via ``api.db_http.hrana_execute`` (bounded HTTP
pipeline, rows as tuples) — sync libsql is banned in the API process
(DUR-09, ``test_no_sync_libsql_in_api.py``).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# Make scripts/ importable so we can monkeypatch the DB seam
SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

_HRANA = "api.db_http.hrana_execute"

# DESC by date — exactly what the route's SQL returns; the route reverses
# to ASC for chart consumption.
_DESC_ROWS = [
    ("2026-05-19", 1.10, 16.50, 1),
    ("2026-05-18", 1.05, 15.75, 1),
    ("2026-05-17", 1.0, 15.0, 1),
]


@pytest.fixture(autouse=True)
def reset_route_cache(monkeypatch):
    """The endpoint memoises results in a module-level dict; clear between tests.

    Also coerce the auth middleware into treating us as a localhost peer
    (TestClient defaults to client.host="testclient" which the tailnet
    check rejects — that's intentional for the prod path but blocks unit
    tests). Same shim every other server test uses.
    """
    from scripts.api import server, auth
    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    server._llm_token_index_cache["data"] = None
    server._llm_token_index_cache["fetched_at"] = 0.0
    server._llm_token_index_cache["days"] = 0
    yield


@pytest.fixture
def client():
    from scripts.api.server import app
    return TestClient(app)


def test_empty_table_returns_empty_list(client):
    """No rows persisted yet → 200 + empty rows array, not 404."""
    with patch(_HRANA, return_value=[]):
        response = client.get("/llm-token-index")

    assert response.status_code == 200
    body = response.json()
    assert body["rows"] == []
    assert body["count"] == 0
    assert body["days"] == 180  # default


def test_returns_rows_sorted_asc(client):
    with patch(_HRANA, return_value=list(_DESC_ROWS)):
        response = client.get("/llm-token-index?days=90")

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 3
    assert body["days"] == 90
    # The DB read is DESC LIMIT n; the route reverses to ASC.
    assert [row["date"] for row in body["rows"]] == [
        "2026-05-17", "2026-05-18", "2026-05-19",
    ]
    assert body["rows"][0] == {
        "date": "2026-05-17",
        "index_value": 1.0,
        "raw_avg_usd": 15.0,
        "methodology_version": 1,
    }


def test_days_param_flows_through_to_reader(client):
    """Verify the days query param is honoured (not silently ignored)."""
    seen: dict[str, object] = {}

    def fake_hrana(sql, args=(), timeout=None):
        seen["args"] = tuple(args)
        return []

    with patch(_HRANA, side_effect=fake_hrana):
        client.get("/llm-token-index?days=30")
    assert seen["args"] == (30,)


def test_days_out_of_range_returns_422(client):
    """FastAPI Query validation: ge=1, le=3650."""
    response = client.get("/llm-token-index?days=0")
    assert response.status_code == 422
    response = client.get("/llm-token-index?days=99999")
    assert response.status_code == 422


def test_db_error_degrades_gracefully(client):
    """DB blip → empty list payload (200), not a 500. Watchdog will see
    the gap in service_health and alert separately."""
    from api import db_http

    with patch(_HRANA, side_effect=db_http.DbHttpError("timed out")):
        response = client.get("/llm-token-index")

    assert response.status_code == 200
    body = response.json()
    assert body["rows"] == []
    assert body["count"] == 0


def test_cache_short_circuits_repeat_call(client):
    """5-min TTL — second call inside the window doesn't re-hit the DB."""
    with patch(_HRANA, return_value=list(_DESC_ROWS)) as mock:
        client.get("/llm-token-index?days=180")
        client.get("/llm-token-index?days=180")
        client.get("/llm-token-index?days=180")

    assert mock.call_count == 1


def test_cache_key_includes_days_param(client):
    """Different `days` params bypass the cache so /llm-token-index?days=30
    and ?days=180 don't poison each other's payloads."""
    with patch(_HRANA, return_value=list(_DESC_ROWS)) as mock:
        client.get("/llm-token-index?days=180")
        client.get("/llm-token-index?days=30")  # different key → re-fetch
        client.get("/llm-token-index?days=30")  # same as above → cache hit

    assert mock.call_count == 2
