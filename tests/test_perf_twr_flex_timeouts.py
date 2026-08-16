"""R13 — the Flex fetch had a caller-side deadline shorter than the work.

Live on 2026-08-16, after the BOD and alpha fixes had deployed correctly, the
payload still could not regenerate at all::

    FLOWS_FETCH_FAILED: fetch_failed: The read operation timed out

The GetStatement socket read timeout was 30s and the poll budget was a flat
30 x 3s = 90s. Query 1442520 now carries three sections, and the Transfers
section the operator added made the statement slow enough to blow both.

Exactly the shape of the cash_flow_sync 180s wall: a caller-side deadline
shorter than the work it is waiting on, in a second script. The builder
degraded correctly rather than publishing, which is why this surfaced as a
stuck page rather than a wrong number -- the integrity gates did their job and
the timeout was the actual defect.
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import scripts.perf_twr_builder as builder  # noqa: E402


def test_r13_the_read_timeout_is_not_thirty_seconds():
    import inspect

    assert builder.FLEX_READ_TIMEOUT_SECONDS >= 120.0
    src = inspect.getsource(builder.fetch_flex_xml)
    # The GetStatement call must not carry a hardcoded 30s socket timeout.
    assert "timeout=30" not in src
    assert "timeout=read_timeout" in src


def test_r13_the_poll_budget_matches_cash_flow_syncs():
    """Both scripts wait on the same service, so one budget, not two opinions."""
    import scripts.cash_flow_sync as cfs

    assert builder.FLEX_POLL_BUDGET_SECONDS == cfs.FLEX_POLL_BUDGET_SECONDS


class _Resp:
    def __init__(self, body: str) -> None:
        self._body = body.encode()

    def read(self) -> bytes:
        return self._body


def test_r13_polling_is_bounded_by_the_budget_not_the_poll_count(monkeypatch):
    """Capped-exponential, stopping at the budget rather than at a poll count."""
    slept: list[float] = []
    monkeypatch.setattr(builder._time, "sleep", lambda s: slept.append(s))

    sent = "<FlexStatementResponse><ReferenceCode>REF1</ReferenceCode></FlexStatementResponse>"
    calls = {"n": 0}

    def _fake_urlopen(url, timeout=None):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] == 1:
            return _Resp(sent)
        return _Resp("<not ready/>")  # never becomes ready

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    with pytest.raises(RuntimeError, match="not ready within"):
        builder.fetch_flex_xml("tok", "1442520")

    # 3, 6, 12, then capped at 15, and the total never exceeds the budget.
    assert slept[:3] == [3.0, 6.0, 12.0]
    assert max(slept) == 15.0
    assert sum(slept) <= builder.FLEX_POLL_BUDGET_SECONDS


def test_r13_a_ready_statement_still_returns_immediately(monkeypatch):
    """Widening the budget must not slow down the normal path."""
    slept: list[float] = []
    monkeypatch.setattr(builder._time, "sleep", lambda s: slept.append(s))

    sent = "<FlexStatementResponse><ReferenceCode>REF1</ReferenceCode></FlexStatementResponse>"
    ready = '<FlexQueryResponse><FlexStatements count="1"><FlexStatement/></FlexStatements></FlexQueryResponse>'
    calls = {"n": 0}

    def _fake_urlopen(url, timeout=None):  # noqa: ANN001
        calls["n"] += 1
        return _Resp(sent if calls["n"] == 1 else ready)

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    assert "<FlexStatement" in builder.fetch_flex_xml("tok", "1442520")
    # One 3s poll, not a walk up the ladder.
    assert slept == [3.0]
