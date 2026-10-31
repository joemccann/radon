"""PERF-035: PDF policy context must cost ONE Turso /v2/pipeline round-trip.

`load_context` reads watchlist, the latest portfolio snapshot, and approved
research-rule rows. Those statements are independent; three sequential
`hrana_execute` POSTs paid three Turso TLS round-trips on every
`radon-marketdesk-pdf` cycle (the unit that failed at probe time).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parent
for _p in (_TESTS.parent, _TESTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from _pipeline_budget_fake import PipelineRecorder  # noqa: E402


def _rows_for(sql: str):
    if "FROM watchlist" in sql:
        return [("AAPL",)]
    if "FROM portfolio_snapshots" in sql:
        return [(json.dumps([{"ticker": "AAPL"}]),)]
    if "FROM research_rule_proposals" in sql:
        return [("series_deny", "Calendar")]
    raise AssertionError(f"unexpected sql: {sql}")


def test_load_context_uses_one_pipeline_round_trip(monkeypatch: pytest.MonkeyPatch):
    from api import db_http
    from research import marketdesk_pdf_policy as policy

    monkeypatch.setenv("TURSO_DB_URL", "libsql://example.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "token")
    recorder = PipelineRecorder(_rows_for)
    monkeypatch.setattr(db_http.urllib.request, "urlopen", recorder)

    context = policy.load_context()

    assert context.tickers == {"AAPL"}
    assert context.rules == {"series_deny": {"Calendar"}}
    assert len(recorder.posts) == 1, (
        f"Turso /v2/pipeline POSTs {len(recorder.posts)} "
        "(want 1 for watchlist + snapshot + approved rules)"
    )
    kinds = [r["type"] for r in recorder.posts[0]["requests"]]
    assert kinds == ["execute", "execute", "execute", "close"]
