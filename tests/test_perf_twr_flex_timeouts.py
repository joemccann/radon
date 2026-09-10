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
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import scripts.perf_twr_builder as builder  # noqa: E402


def _freeze_embargo_clock(monkeypatch, when: datetime) -> None:
    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return when if tz is None else when.astimezone(tz)

    monkeypatch.setattr("utils.flex_embargo.datetime", _FrozenDateTime)


@pytest.fixture(autouse=True)
def _no_flex_embargo(request, monkeypatch):
    if "lockout" in request.node.name or "sidecar" in request.node.name:
        return
    monkeypatch.setattr("utils.flex_embargo.raise_if_blocked", lambda: None)


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


def test_r13_a_flex_error_on_getstatement_aborts_instead_of_polling(
    monkeypatch, tmp_path
):
    """1018/1025 XML is not 'not ready'. Polling it for 420s is how a
    locked token stays locked. Abort after the first error body."""
    slept: list[float] = []
    monkeypatch.setattr(builder._time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(
        "utils.flex_embargo.SIDECAR", tmp_path / "flex_token_embargo.json"
    )
    monkeypatch.setattr("utils.flex_embargo._heartbeat", lambda *a, **k: None)

    sent = "<FlexStatementResponse><ReferenceCode>REF1</ReferenceCode></FlexStatementResponse>"
    locked = (
        "<FlexStatementResponse><Status>Fail</Status>"
        "<ErrorCode>1025</ErrorCode>"
        "<ErrorMessage>Too many failed attempts. Please review your "
        "configuration.</ErrorMessage></FlexStatementResponse>"
    )
    calls = {"n": 0}

    def _fake_urlopen(url, timeout=None):  # noqa: ANN001
        calls["n"] += 1
        return _Resp(sent if calls["n"] == 1 else locked)

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    with pytest.raises(RuntimeError, match="1025"):
        builder.fetch_flex_xml("tok", "1442520")

    assert calls["n"] == 2  # SendRequest + one GetStatement, not ~29
    assert slept == [3.0]


def test_r13_flex_statement_response_is_not_a_ready_statement(monkeypatch, tmp_path):
    """`<FlexStatement` is a prefix of `<FlexStatementResponse`. A 1025
    envelope must not count as a generated statement."""
    monkeypatch.setattr(builder._time, "sleep", lambda s: None)
    monkeypatch.setattr(
        "utils.flex_embargo.SIDECAR", tmp_path / "flex_token_embargo.json"
    )
    monkeypatch.setattr("utils.flex_embargo._heartbeat", lambda *a, **k: None)

    sent = "<FlexStatementResponse><ReferenceCode>REF1</ReferenceCode></FlexStatementResponse>"
    locked = (
        "<FlexStatementResponse><Status>Fail</Status>"
        "<ErrorCode>1025</ErrorCode>"
        "<ErrorMessage>Too many failed attempts.</ErrorMessage>"
        "</FlexStatementResponse>"
    )
    calls = {"n": 0}

    def _fake_urlopen(url, timeout=None):  # noqa: ANN001
        calls["n"] += 1
        return _Resp(sent if calls["n"] == 1 else locked)

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    with pytest.raises(RuntimeError, match="1025"):
        builder.fetch_flex_xml("tok", "1442520")
    assert calls["n"] == 2


def test_r13_a_live_lockout_skips_sendrequest(monkeypatch, tmp_path):
    """Saturday 07:30 TWR must not poke a token cash-flow-sync already locked."""
    from utils.flex_embargo import FlexTokenLocked, record_lockout

    _freeze_embargo_clock(
        monkeypatch, datetime(2026, 8, 23, 22, 0, tzinfo=timezone.utc)
    )
    monkeypatch.setattr(
        "utils.flex_embargo.SIDECAR", tmp_path / "flex_token_embargo.json"
    )
    monkeypatch.setattr("utils.flex_embargo._heartbeat", lambda *a, **k: None)
    record_lockout("1025", now=datetime(2026, 8, 21, 13, 58, 26, tzinfo=timezone.utc))

    def _boom(url, timeout=None):  # noqa: ANN001
        raise AssertionError("SendRequest during 1025 lockout")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    with pytest.raises(FlexTokenLocked):
        builder.fetch_flex_xml("tok", "1442520")


def test_r13_missing_sidecar_reconstructs_live_1025_and_skips_sendrequest(
    monkeypatch, tmp_path
):
    """Monday 07:30 ET TWR must not poke the token when only Turso holds 1025."""
    import json

    from utils.flex_embargo import FlexTokenLocked

    _freeze_embargo_clock(
        monkeypatch, datetime(2026, 8, 23, 22, 0, tzinfo=timezone.utc)
    )
    monkeypatch.setattr(
        "utils.flex_embargo.SIDECAR", tmp_path / "flex_token_embargo.json"
    )
    monkeypatch.setattr("utils.flex_embargo._heartbeat", lambda *a, **k: None)

    live_row = (
        "error",
        "2026-08-21T13:58:28.298780Z",
        json.dumps({
            "message": (
                "ERR: Flex SendRequest failed (code 1025): "
                "Too many failed attempts. Please review your configuration."
            ),
            "class": "permanent",
            "next_attempt_at": "2026-08-24T12:00:00+00:00",
        }),
    )

    def fake(sql, args=(), timeout=None):  # noqa: ANN001
        key = args[0] if args else None
        if key == "cash-flow-sync":
            return [live_row]
        return []

    import db.hrana_http as hrana_mod

    monkeypatch.setattr(hrana_mod, "hrana_execute", fake)
    monkeypatch.setattr(hrana_mod, "hrana_query", fake)

    twr = datetime(2026, 8, 24, 11, 30, tzinfo=timezone.utc)

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001
            if tz is None:
                return twr.replace(tzinfo=None)
            return twr.astimezone(tz)

    monkeypatch.setattr("utils.flex_embargo.datetime", _Frozen)

    calls = {"n": 0}

    def _boom(url, timeout=None):  # noqa: ANN001
        calls["n"] += 1
        raise AssertionError("SendRequest during 1025 lockout")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    with pytest.raises(FlexTokenLocked):
        builder.fetch_flex_xml("tok", "1442520")
    assert calls["n"] == 0
