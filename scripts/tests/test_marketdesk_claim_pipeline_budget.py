"""PERF-045: claiming one MarketDesk article must cost ONE Turso /v2/pipeline POST.

`claim` inserts the article, counts an interrupted lease, retires spent
attempts, reads status, and writes the new lease. Those five statements
used to be five sequential `hrana_execute` POSTs (one TLS round-trip
each) on every `radon-marketdesk` article. They are ordered, but they
share one article_id and belong in one pipeline.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

_TESTS = Path(__file__).resolve().parent
for _p in (_TESTS.parent, _TESTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from _pipeline_budget_fake import PipelineRecorder  # noqa: E402


def _rows_for(sql: str):
    if "SELECT status, attempts" in sql:
        return [("pending", 0)]
    return []


def test_claim_uses_one_pipeline_round_trip(monkeypatch: pytest.MonkeyPatch):
    from api import db_http
    from research import marketdesk_store as store

    monkeypatch.setenv("TURSO_DB_URL", "libsql://example.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "token")
    recorder = PipelineRecorder(_rows_for)
    monkeypatch.setattr(db_http.urllib.request, "urlopen", recorder)

    ref = SimpleNamespace(
        article_id="4AJs5Adl2Yp",
        title="Fed cuts",
        url="https://marketdesk.ai/library/browse/4AJs5Adl2Yp",
        published_at=datetime(2026, 10, 26, 14, 0, tzinfo=timezone.utc),
    )
    assert store.claim(ref) is True

    assert len(recorder.posts) == 1, (
        f"Turso /v2/pipeline POSTs {len(recorder.posts)} "
        "(want 1 for insert + interrupt + fail + select + lease)"
    )
    kinds = [r["type"] for r in recorder.posts[0]["requests"]]
    assert kinds == ["execute", "execute", "execute", "execute", "execute", "close"]
