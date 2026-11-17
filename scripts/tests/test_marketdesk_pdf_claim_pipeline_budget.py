"""PERF-059: claiming a PDF batch must cost TWO Turso /v2/pipeline POSTs.

`claim_batch` classifies stale rows, parks filtered/exhausted work, stamps
jitter, and leases. After `prepare_selection` those writes do not need
Python between them. They used to be six or seven sequential
`hrana_execute` POSTs (one TLS round-trip each) on every
`radon-marketdesk-pdf` timer fire, including an empty queue.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parent
for _p in (_TESTS.parent, _TESTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from _pipeline_budget_fake import PipelineRecorder  # noqa: E402


def _rows_for(sql: str):
    if "FROM marketdesk_pdf_throttle" in sql and "SELECT interval_seconds" in sql:
        return [(900, 1, 0, 0, 0, 0, 0)]
    return []


def test_claim_batch_uses_two_pipeline_round_trips(monkeypatch: pytest.MonkeyPatch):
    from api import db_http
    from research import marketdesk_pdf_store as store

    monkeypatch.setenv("TURSO_DB_URL", "libsql://example.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "token")
    monkeypatch.setattr(store.policy, "load_context", lambda: store.policy.Context(frozenset(), {}))
    recorder = PipelineRecorder(_rows_for)
    monkeypatch.setattr(db_http.urllib.request, "urlopen", recorder)

    assert store.claim_batch(now=datetime(2026, 10, 27, 17, tzinfo=timezone.utc), jitter_bps=0) == []

    assert len(recorder.posts) == 2, (
        f"Turso /v2/pipeline POSTs {len(recorder.posts)} "
        "(want 2: stale-selection SELECT + one pipeline for disposition, jitter, lease)"
    )
    kinds = [[item["type"] for item in post["requests"]] for post in recorder.posts]
    assert kinds[0] == ["execute", "close"]
    assert kinds[1][0] == "execute"
    assert kinds[1][-1] == "close"
    assert kinds[1].count("execute") >= 4
