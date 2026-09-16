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


@pytest.fixture
def nightly_builder(monkeypatch):
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
    monkeypatch.setattr(ptb, "load_flows_from_turso", lambda **_kw: {"2026-01-13": 80000.0})
    monkeypatch.setattr(ptb, "load_flow_coverage_dates", lambda: {"2026-01-13", "2026-01-14"})
    monkeypatch.setattr(ptb, "load_flows_coverage_state", lambda: ("2026-01-14", True))
    monkeypatch.setattr(ptb, "flow_divergence_warnings", lambda: [])
    monkeypatch.setattr(ptb, "load_benchmark_closes", lambda *_a, **_kw: {})
    monkeypatch.setattr(ptb, "get_risk_free_rate", lambda **_kw: (0.0, ""))
    monkeypatch.setattr(ptb, "sessions_behind", lambda *_a, **_kw: 0)
    monkeypatch.setattr(ptb, "fetch_flex_xml", lambda *_a, **_kw: pytest.fail("SendRequest"))

    return ptb


def test_nightly_statement_preserves_verified_historical_external_flows(nightly_builder):
    payload = nightly_builder.build_and_persist(from_file="nightly.xml", persist=False)

    deposit_day = next(row for row in payload["subperiods"] if row["date"] == "2026-01-13")
    assert deposit_day["c"] == 80000.0
    assert deposit_day["r"] == 0.0
    assert payload["counts"]["n_suspect"] == 0
    assert payload["equity"]["net_external_flows"] == 80000.0
    assert payload["equity"]["investment_pnl"] == 3000.0
    assert payload["nav_as_of"] == "2026-01-16"


def test_nightly_statement_replaces_overlap_including_verified_zero(nightly_builder, monkeypatch):
    monkeypatch.setattr(nightly_builder, "load_flows_from_turso", lambda **_kw: {
        "2026-01-13": 80000.0, "2026-01-15": 25000.0,
    })
    payload = nightly_builder.build_and_persist(from_file="nightly.xml", persist=False)
    assert next(row for row in payload["subperiods"] if row["date"] == "2026-01-15")["c"] == 0.0
    assert payload["equity"]["net_external_flows"] == 80000.0


def test_nightly_statement_corrects_overlap_without_counting_twice(nightly_builder, monkeypatch):
    ptb = nightly_builder
    resolution = ptb._resolution_from_file("nightly.xml")
    xml = resolution.document.xml.replace("<CashTransactions />", """
      <CashTransactions><CashTransaction type="Deposits/Withdrawals"
        reportDate="20260115" amount="1000" /></CashTransactions>""")
    monkeypatch.setattr(ptb, "_resolution_from_file", lambda _path: ptb._replace(
        resolution, document=ptb.FlexDocument("from-file", xml),
    ))
    monkeypatch.setattr(ptb, "load_flows_from_turso", lambda **_kw: {
        "2026-01-13": 80000.0, "2026-01-15": 2000.0,
    })
    payload = ptb.build_and_persist(from_file="nightly.xml", persist=False)
    assert next(row for row in payload["subperiods"] if row["date"] == "2026-01-15")["c"] == 1000.0
    assert payload["equity"]["net_external_flows"] == 81000.0


@pytest.mark.parametrize("covered", [None, set(), {"2026-01-14"}])
def test_nightly_statement_cannot_invent_historical_zero_flows(nightly_builder, monkeypatch, covered):
    monkeypatch.setattr(nightly_builder, "load_flow_coverage_dates", lambda: covered)
    payload = nightly_builder.build_and_persist(from_file="nightly.xml", persist=False)
    assert payload["flows_status"] == "failed"
    assert payload["twr"] is None
    assert payload["nav_as_of"] == "2026-01-16"
    assert len(payload["series"]) == 5
    assert any(w["context"].get("reason") == "historical_flow_coverage_unverified" for w in payload["warnings"])


def test_nightly_statement_cannot_use_unavailable_historical_ledger(nightly_builder, monkeypatch):
    monkeypatch.setattr(nightly_builder, "load_flows_from_turso", lambda **_kw: None)
    payload = nightly_builder.build_and_persist(from_file="nightly.xml", persist=False)
    assert payload["flows_status"] == "failed"
    assert payload["twr"] is None


def test_nightly_statement_can_extend_verified_history_without_deposits(nightly_builder, monkeypatch):
    ptb = nightly_builder
    monkeypatch.setattr(ptb, "get_nav_snapshots", lambda **_kw: ptb.NavResolution(
        {"2026-01-12": 179000.0, "2026-01-13": 180000.0, "2026-01-14": 181000.0}, "turso",
    ))
    monkeypatch.setattr(ptb, "load_flows_from_turso", lambda **_kw: {})
    payload = ptb.build_and_persist(from_file="nightly.xml", persist=False)
    assert payload["flows_status"] == "empty_verified"
    assert payload["counts"]["n_suspect"] == 0
    assert payload["equity"]["net_external_flows"] == 0.0


@pytest.mark.parametrize("replacement", ["", 'fromDate="20260117" toDate="20260116"'])
def test_nightly_statement_requires_valid_declared_coverage(nightly_builder, monkeypatch, replacement):
    ptb = nightly_builder
    resolution = ptb._resolution_from_file("nightly.xml")
    xml = resolution.document.xml.replace('fromDate="20260115" toDate="20260116"', replacement)
    monkeypatch.setattr(ptb, "_resolution_from_file", lambda _path: ptb._replace(
        resolution, document=ptb.FlexDocument("from-file", xml),
    ))
    payload = ptb.build_and_persist(from_file="nightly.xml", persist=False)
    assert payload["flows_status"] == "failed"
    assert payload["twr"] is None


def test_historical_coverage_query_distinguishes_unavailable_from_empty(monkeypatch):
    import perf_twr_builder as ptb

    monkeypatch.setattr(ptb, "_query_turso_strict", lambda _sql: [])
    assert ptb.load_flow_coverage_dates() == set()
    monkeypatch.setattr(ptb, "_query_turso_strict", lambda _sql: None)
    assert ptb.load_flow_coverage_dates() is None
    monkeypatch.setattr(ptb, "_query_turso_strict", lambda _sql: (_ for _ in ()).throw(RuntimeError("offline")))
    assert ptb.load_flow_coverage_dates() is None


def test_historical_ledger_allows_verified_empty_only_after_successful_query(monkeypatch):
    import perf_twr_builder as ptb

    monkeypatch.setattr(ptb, "_query_turso", lambda _sql: [])
    assert ptb.load_flows_from_turso() is None
    assert ptb.load_flows_from_turso(allow_empty=True) == {}
    monkeypatch.setattr(ptb, "_query_turso", lambda _sql: None)
    assert ptb.load_flows_from_turso(allow_empty=True) is None


def test_statement_zero_corrections_are_mirrored_but_unverified_zeros_are_not():
    import perf_twr_builder as ptb

    rows = ptb._external_flow_rows({"subperiods": [
        {"date": "2026-01-13", "c": 80000.0, "r": 0.0, "cum_r": 0.0},
        {"date": "2026-01-14", "c": 0.0, "r": 0.01, "cum_r": 0.01},
        {"date": "2026-01-15", "c": 0.0, "r": None, "cum_r": 0.01},
        {"date": "2026-01-16", "c": 0.0, "r": 0.01, "cum_r": None},
    ]}, "ALL")
    assert [(row["report_date"], row["amount"]) for row in rows] == [
        ("2026-01-13", 80000.0), ("2026-01-14", 0.0),
    ]


def test_corrected_statement_publishes_zero_for_next_mirror_only_build(nightly_builder, monkeypatch):
    ptb = nightly_builder
    monkeypatch.setattr(ptb, "load_flows_from_turso", lambda **_kw: {
        "2026-01-13": 80000.0, "2026-01-15": 25000.0,
    })
    payload = ptb.build_and_persist(from_file="nightly.xml", persist=False)
    mirrored = {row["report_date"]: row["amount"] for row in ptb._external_flow_rows(payload, "ALL")}
    assert mirrored["2026-01-15"] == 0.0
    assert mirrored["2026-01-13"] == 80000.0


@pytest.mark.parametrize("case,reason", [
    ("ambiguous", "statement_flow_coverage_ambiguous"),
    ("incomplete", "statement_flow_sections_incomplete"),
    ("outside", "statement_flow_outside_coverage"),
])
def test_statement_merge_fails_closed_on_ambiguous_or_partial_evidence(nightly_builder, monkeypatch, case, reason):
    ptb = nightly_builder
    resolution = ptb._resolution_from_file("nightly.xml")
    xml = resolution.document.xml
    if case == "ambiguous":
        xml = xml.replace("</FlexStatements>", '''<FlexStatement accountId="U2"
          fromDate="20260116" toDate="20260116"><CashTransactions /><Transfers />
          </FlexStatement></FlexStatements>''')
    else:
        report_date = "20260113" if case == "outside" else "20260115"
        xml = xml.replace("<CashTransactions />", f'''<CashTransactions>
          <CashTransaction type="Deposits/Withdrawals" reportDate="{report_date}" amount="1000" />
          </CashTransactions>''')
        if case == "incomplete":
            xml = xml.replace("<Transfers />", "")
    monkeypatch.setattr(ptb, "_resolution_from_file", lambda _path: ptb._replace(
        resolution, document=ptb.FlexDocument("from-file", xml),
    ))
    payload = ptb.build_and_persist(from_file="nightly.xml", persist=False)
    assert payload["twr"] is None
    assert any(w["context"].get("reason") == reason for w in payload["warnings"])


def test_statement_merge_retains_historical_disagreement_gate(nightly_builder, monkeypatch):
    ptb = nightly_builder
    monkeypatch.setattr(ptb, "flow_divergence_warnings", lambda: [
        ptb._warning("FLOWS_SOURCE_DISAGREEMENT", "warn", "Historical mismatch", report_date="2026-01-13"),
        ptb._warning("FLOWS_SOURCE_DISAGREEMENT", "warn", "Corrected by statement", report_date="2026-01-15"),
    ])
    payload = ptb.build_and_persist(from_file="nightly.xml", persist=False)
    conflicts = [w for w in payload["warnings"] if w["code"] == "FLOWS_SOURCE_DISAGREEMENT"]
    assert [w["context"]["report_date"] for w in conflicts] == ["2026-01-13"]
    assert payload["status"] == "stale"
