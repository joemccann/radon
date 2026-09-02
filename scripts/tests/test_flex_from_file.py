"""--from-file ingest. No Flex Web Service."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
REPO = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ACTIVITY = FIXTURES / "cash_transactions_flex_ytd_detail_sample.xml"
TRADES = FIXTURES / "flex_trade_confirm_sample.xml"


def test_journal_from_file_rejects_activity_xml():
    import journal_rehydrate

    result = journal_rehydrate.rehydrate(xml_text=ACTIVITY.read_text())
    assert result["ok"] is False
    assert result["imported"] == 0
    assert "not_trade_statement" in str(result.get("error"))


def test_journal_from_file_parses_trade_xml_without_network(monkeypatch):
    import journal_rehydrate
    import db.writer as writer

    monkeypatch.setattr(writer, "upsert_journal_entry", MagicMock())
    result = journal_rehydrate.rehydrate(xml_text=TRADES.read_text(), existing={"trades": []})
    assert result["ok"] is True
    assert result["executions_seen"] == 1


def test_twr_from_file_rejects_trade_xml():
    import perf_twr_builder

    with pytest.raises(RuntimeError, match="not_activity_statement"):
        perf_twr_builder.build_and_persist(from_file=str(TRADES), persist=False)


def test_twr_from_file_uses_activity_xml_without_sendrequest(monkeypatch):
    import perf_twr_builder

    monkeypatch.setattr(perf_twr_builder, "_fetch_nav_document", lambda: (_ for _ in ()).throw(AssertionError("SendRequest")))
    monkeypatch.setattr(perf_twr_builder, "load_benchmark_closes", lambda *a, **k: {})
    monkeypatch.setattr(perf_twr_builder, "get_risk_free_rate", lambda **k: (0.0, "test"))
    payload = perf_twr_builder.build_and_persist(from_file=str(ACTIVITY), persist=False)
    assert payload.get("nav_source") == "flex_from_file"
    assert payload.get("nav_as_of") or payload.get("period_end")


def test_twr_from_file_parses_flows_from_the_file_without_a_token(monkeypatch):
    """The sFTP unit runs on a stripped env with no IB_FLEX_TOKEN by design
    (docs/flex-sftp-setup.md). `resolve_flows` demanded a token before looking
    at the statement already in hand, so every nightly activity file built
    `flows_status: failed (flex_not_configured)`, degraded, and was rejected
    (2026-09-02, radon-flex-pull)."""
    import perf_twr_builder

    monkeypatch.delenv("IB_FLEX_TOKEN", raising=False)
    monkeypatch.delenv("IB_FLEX_FLOWS_QUERY_ID", raising=False)
    monkeypatch.delenv("IB_FLEX_NAV_QUERY_ID", raising=False)
    monkeypatch.setattr(perf_twr_builder, "fetch_flex_xml", lambda *a, **k: (_ for _ in ()).throw(AssertionError("SendRequest")))
    monkeypatch.setattr(perf_twr_builder, "load_benchmark_closes", lambda *a, **k: {})
    monkeypatch.setattr(perf_twr_builder, "get_risk_free_rate", lambda **k: (0.0, "test"))
    payload = perf_twr_builder.build_and_persist(from_file=str(ACTIVITY), persist=False)
    assert payload.get("nav_source") == "flex_from_file"
    assert payload.get("flows_status") != "failed", payload.get("warnings")
    assert not [w for w in payload.get("warnings", []) if w.get("code") == "FLOWS_FETCH_FAILED"]


def test_ingest_does_not_import_gdcdyn():
    source = (SCRIPTS / "flex_delivery_ingest.py").read_text()
    assert "gdcdyn" not in source
    assert "FlexReport(" not in source
