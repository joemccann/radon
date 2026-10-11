"""PERF-001: GET /cash-flows must cost ONE Turso /v2/pipeline round-trip.

The route reads `cash_flows` rows and the `service_health` sync-status row;
both statements belong in a single HTTP pipeline (was 2 sequential POSTs).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parent
for _p in (_TESTS.parent, _TESTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from _pipeline_budget_fake import PipelineRecorder  # noqa: E402


def _rows_for(sql: str):
    if "FROM cash_flows" in sql:
        return [("T1", "2099-01-01", "Deposit", 100.0, "USD", None, "Deposit", "2099-01-02T00:00:00Z")]
    if "FROM service_health" in sql:
        return [("ok", "2099-01-02T00:00:00Z", None)]
    raise AssertionError(f"unexpected sql: {sql}")


def test_cash_flows_uses_one_pipeline_round_trip(monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient
    from api import auth, db_http, server

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    # server import strips host Turso creds (scripts/conftest.py); set after.
    monkeypatch.setenv("TURSO_DB_URL", "libsql://example.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "token")
    recorder = PipelineRecorder(_rows_for)
    monkeypatch.setattr(db_http.urllib.request, "urlopen", recorder)

    resp = TestClient(server.app).get("/cash-flows?days=90")

    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1, body
    assert body["rows"][0]["id"] == "T1"
    assert len(recorder.posts) == 1
    kinds = [r["type"] for r in recorder.posts[0]["requests"]]
    assert kinds == ["execute", "execute", "close"]
