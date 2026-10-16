"""2026-10-16: radon-flex-pull Result=exit-code on a duplicate coverage read.

Page c96336153e8157df025f71943e1824c9, 11:35Z. The 07:30 ET oneshot had
already applied today's activity statement. The coverage read for an
applied Trade_History duplicate then raised
`RemoteDisconnected: Remote end closed connection without response`.
ingest_xml reported that as coverage_unverified / operator
reconciliation, the puller set failed, and the oneshot exited 1
(NRestarts=0). The 08:30 ET retry exited 0. :8321/health/lite stayed
authenticated. A dropped read is not a row gap.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import flex_delivery_ingest as ingest  # noqa: E402
import flex_sftp_pull as pull  # noqa: E402
from db.hrana_http import HranaHttpError  # noqa: E402
from test_flex_sftp_pull import FakeSftp, _ssh_config  # noqa: E402

ET = ZoneInfo("America/New_York")
# Friday 07:35 ET. Last completed session is Thursday 2026-10-15.
NOW = datetime(2026, 10, 16, 7, 35, tzinfo=ET)
TRADE_NAME = "demo.Trade_History.20261015.20261015.xml.pgp"
# Verbatim HranaHttpError text from the 11:30Z unit journal.
REMOTE_DISCONNECTED = (
    "RemoteDisconnected: Remote end closed connection without response"
)


def _trade_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<FlexQueryResponse queryName="Trade Confirmation" type="TC">'
        '<FlexStatements count="1">'
        '<FlexStatement accountId="demo" fromDate="20261015" toDate="20261015"'
        ' period="LastBusinessDay">'
        "<Trades></Trades>"
        "</FlexStatement></FlexStatements></FlexQueryResponse>"
    )


def _drive(tmp_path, monkeypatch, coverage):
    beats: list[tuple] = []
    monkeypatch.setattr(pull, "_heartbeat", lambda state, error=None: beats.append((state, error)))
    monkeypatch.setattr(ingest, "claim_flex_delivery", lambda *a, **k: False)
    monkeypatch.setattr(ingest, "flex_delivery_status", lambda _d: "applied")
    monkeypatch.setattr(ingest, "_apply_classified", lambda *a, **k: pytest.fail("duplicate reapplied"))
    monkeypatch.setattr(ingest, "delivery_rows_present", coverage)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    xml = _trade_xml()
    code = pull.run(
        config=_ssh_config(tmp_path / "ssh_config"),
        inbox=inbox,
        runner=FakeSftp({TRADE_NAME: xml.encode()}),
        decrypt=lambda data, **k: data.decode(),
        now=NOW,
    )
    return code, beats


def test_remote_disconnect_on_applied_trade_duplicate_does_not_fail_the_oneshot(tmp_path, monkeypatch):
    """A dropped read that confirms on retry must not page or replay ingest."""
    calls = []

    def coverage(*_a, **_k):
        calls.append(1)
        if len(calls) == 1:
            raise HranaHttpError(REMOTE_DISCONNECTED)
        return True

    code, beats = _drive(tmp_path, monkeypatch, coverage)
    assert code == 0
    assert beats[-1][0] == "ok"
    assert len(calls) == 2
    assert "coverage_unread" not in str(beats[-1][1])


def test_statement_error_on_applied_trade_duplicate_still_fails_the_oneshot(tmp_path, monkeypatch):
    def coverage(*_a, **_k):
        raise HranaHttpError("no such column: journal.payload")

    code, beats = _drive(tmp_path, monkeypatch, coverage)
    assert code == 1
    assert beats[-1][0] == "error"
    assert "one or more files rejected" in str(beats[-1][1])


def test_coverage_gap_on_applied_trade_duplicate_still_fails_the_oneshot(tmp_path, monkeypatch):
    code, beats = _drive(tmp_path, monkeypatch, lambda *_a, **_k: False)
    assert code == 1
    assert beats[-1][0] == "error"
    assert "one or more files rejected" in str(beats[-1][1])


def _patch_applied_duplicate(monkeypatch, coverage):
    monkeypatch.setattr(ingest, "claim_flex_delivery", lambda *a, **k: False)
    monkeypatch.setattr(ingest, "flex_delivery_status", lambda _d: "applied")
    monkeypatch.setattr(ingest, "_apply_classified", lambda *a, **k: pytest.fail("duplicate reapplied"))
    monkeypatch.setattr(ingest, "delivery_rows_present", coverage)


def test_persistent_disconnect_returns_unverified_without_replay(monkeypatch):
    calls = []
    def coverage(*_a, **_k):
        calls.append(1)
        raise HranaHttpError(REMOTE_DISCONNECTED)

    _patch_applied_duplicate(monkeypatch, coverage)
    result = ingest.ingest_xml(_trade_xml(), source_path="Trade_History.20261015.xml")
    assert result["ok"] is False
    assert result["outcome"] == "coverage_unverified"
    assert result["persistence_confirmed"] is False
    assert result["classified_as"] == ingest.TRADES
    assert "operator reconciliation" in result.get("error", "")
    assert len(calls) == ingest._COVERAGE_READ_ATTEMPTS


def test_hrana_read_timeout_confirms_on_retry(monkeypatch):
    calls = []
    def coverage(*_a, **_k):
        calls.append(1)
        if len(calls) == 1:
            raise HranaHttpError("TimeoutError: The read operation timed out")
        return True

    _patch_applied_duplicate(monkeypatch, coverage)
    result = ingest.ingest_xml(_trade_xml())
    assert result["outcome"] == "duplicate"
    assert result["ok"] is True
    assert result["persistence_confirmed"] is True
    assert len(calls) == 2


def test_verification_budget_exhaustion_stays_unverified(monkeypatch):
    def coverage(*_a, **_k):
        raise TimeoutError("duplicate coverage verification budget exhausted")

    _patch_applied_duplicate(monkeypatch, coverage)
    result = ingest.ingest_xml(_trade_xml())
    assert result["ok"] is False
    assert result["outcome"] == "coverage_unverified"


def test_activity_remote_disconnect_does_not_mark_cash_flow_sync_error(monkeypatch):
    xml = (
        '<FlexQueryResponse><FlexStatements>'
        '<FlexStatement accountId="demo" fromDate="20261015" toDate="20261015">'
        '<EquitySummaryByReportDateInBase accountId="demo" reportDate="20261015" total="1"/>'
        '<CashTransactions></CashTransactions>'
        '<Transfers></Transfers>'
        '</FlexStatement></FlexStatements></FlexQueryResponse>'
    )
    health: list = []
    calls = []
    monkeypatch.setattr(ingest, "_heartbeat_cash_flow_sync", lambda *a, **k: health.append(a))

    def coverage(*_a, **_k):
        calls.append(1)
        if len(calls) == 1:
            raise HranaHttpError(REMOTE_DISCONNECTED)
        return True

    _patch_applied_duplicate(monkeypatch, coverage)
    result = ingest.ingest_xml(xml)
    assert result["outcome"] == "duplicate"
    assert result["persistence_confirmed"] is True
    assert result["classified_as"] == ingest.ACTIVITY
    assert health == [("ok",)]
    assert len(calls) == 3
