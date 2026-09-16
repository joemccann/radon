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


def test_twr_from_file_keeps_stored_nav_history(monkeypatch):
    """The nightly sFTP statement carries one or a few sessions. Building the
    page from that statement alone replaced the 2025-12-31.. history with a
    2026-09-11..09-14 window (N=1) on 2026-09-15. The statement must extend the
    stored series, not replace it."""
    import perf_twr_builder

    monkeypatch.setattr(perf_twr_builder, "fetch_flex_xml", lambda *a, **k: (_ for _ in ()).throw(AssertionError("SendRequest")))
    monkeypatch.setattr(perf_twr_builder, "load_benchmark_closes", lambda *a, **k: {})
    monkeypatch.setattr(perf_twr_builder, "get_risk_free_rate", lambda **k: (0.0, "test"))
    statement = perf_twr_builder._resolution_from_file(str(ACTIVITY))
    first_statement_date = min(statement.by_date)
    history = {"2000-01-03": 100_000.0, "2000-01-04": 100_500.0}
    monkeypatch.setattr(
        perf_twr_builder,
        "get_nav_snapshots",
        lambda **k: perf_twr_builder.NavResolution(dict(history), "turso", (), None),
    )

    payload = perf_twr_builder.build_and_persist(from_file=str(ACTIVITY), persist=False)

    assert payload.get("period_start") == "2000-01-03", (payload.get("period_start"), first_statement_date)
    assert payload.get("nav_as_of") == max(statement.by_date)


def test_ingest_does_not_import_gdcdyn():
    source = (SCRIPTS / "flex_delivery_ingest.py").read_text()
    assert "gdcdyn" not in source
    assert "FlexReport(" not in source


def test_nightly_statement_preserves_verified_historical_external_flows(monkeypatch):
    """Extending NAV must also retain the observed deposits behind that NAV.

    A short, flow-free statement cannot turn a prior $80k deposit into a
    return or quarantine the session as an unexplained NAV jump.
    """
    import perf_twr_builder as ptb

    xml = """<FlexQueryResponse><FlexStatements>
      <FlexStatement accountId="U1" fromDate="20260115" toDate="20260116">
        <CashTransactions /><Transfers />
      </FlexStatement>
    </FlexStatements></FlexQueryResponse>"""
    statement = ptb.NavResolution(
        {"2026-01-14": 181000.0, "2026-01-15": 182000.0, "2026-01-16": 183000.0},
        "flex_from_file", (), ptb.FlexDocument("from-file", xml),
    )
    monkeypatch.setattr(ptb, "_resolution_from_file", lambda _path: statement)
    monkeypatch.setattr(ptb, "get_nav_snapshots", lambda **_kw: ptb.NavResolution(
        {"2026-01-12": 100000.0, "2026-01-13": 180000.0, "2026-01-14": 181000.0}, "turso",
    ))
    monkeypatch.setattr(ptb, "load_flows_from_turso", lambda: {"2026-01-13": 80000.0})
    monkeypatch.setattr(ptb, "load_flows_coverage_state", lambda: ("2026-01-14", True))
    monkeypatch.setattr(ptb, "flow_divergence_warnings", lambda: [])
    monkeypatch.setattr(ptb, "load_benchmark_closes", lambda *_a, **_kw: {})
    monkeypatch.setattr(ptb, "get_risk_free_rate", lambda **_kw: (0.0, ""))
    monkeypatch.setattr(ptb, "sessions_behind", lambda *_a, **_kw: 0)
    monkeypatch.setattr(ptb, "fetch_flex_xml", lambda *_a, **_kw: pytest.fail("SendRequest"))

    payload = ptb.build_and_persist(from_file="nightly.xml", persist=False)

    deposit_day = next(row for row in payload["subperiods"] if row["date"] == "2026-01-13")
    assert deposit_day["c"] == 80000.0
    assert deposit_day["r"] == 0.0
    assert payload["counts"]["n_suspect"] == 0
    assert payload["equity"]["net_external_flows"] == 80000.0
    assert payload["equity"]["investment_pnl"] == 3000.0
    assert payload["nav_as_of"] == "2026-01-16"
