"""PERF-002: `_read_orders_snapshot_from_db` must cost ONE Turso
/v2/pipeline round-trip (open_orders + executed_orders in one pipeline;
was 2 concurrent POSTs)."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parent
for _p in (_TESTS.parent, _TESTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from _pipeline_budget_fake import PipelineRecorder  # noqa: E402

_FILL = "2999-01-01T00:00:00Z"


def _rows_for(sql: str):
    if "FROM open_orders" in sql:
        return []
    if "FROM executed_orders" in sql:
        return [(json.dumps({"symbol": "AAPL", "orderId": 7}), _FILL)]
    raise AssertionError(f"unexpected sql: {sql}")


def test_orders_snapshot_uses_one_pipeline_round_trip(monkeypatch: pytest.MonkeyPatch):
    from api import db_http, server

    # server import strips host Turso creds (scripts/conftest.py); set after.
    monkeypatch.setenv("TURSO_DB_URL", "libsql://example.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "token")
    recorder = PipelineRecorder(_rows_for)
    monkeypatch.setattr(db_http.urllib.request, "urlopen", recorder)

    snapshot = asyncio.run(server._read_orders_snapshot_from_db())

    assert snapshot["executed_count"] == 1
    assert snapshot["executed_orders"][0]["symbol"] == "AAPL"
    assert snapshot["last_sync"] == _FILL
    assert len(recorder.posts) == 1
    kinds = [r["type"] for r in recorder.posts[0]["requests"]]
    assert kinds == ["execute", "execute", "close"]
