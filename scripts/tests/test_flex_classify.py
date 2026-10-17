"""Fail-closed Flex classifier. Activity vs Trade Confirmation."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

from lib.flex_classify import ACTIVITY, TRADES, FlexClassifyError, classify_flex_xml

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ACTIVITY_XML = (FIXTURES / "cash_transactions_flex_ytd_detail_sample.xml").read_text()
TRADES_XML = (FIXTURES / "flex_trade_confirm_sample.xml").read_text()


def test_activity_fixture_is_1442520():
    assert classify_flex_xml(ACTIVITY_XML) == ACTIVITY


def test_trade_fixture_is_1422766():
    assert classify_flex_xml(TRADES_XML) == TRADES


def test_activity_writer_rejects_trade_fixture():
    assert classify_flex_xml(TRADES_XML) != ACTIVITY


def test_trade_writer_rejects_activity_fixture():
    assert classify_flex_xml(ACTIVITY_XML) != TRADES


def test_nav_plus_trade_is_activity_not_journal():
    xml = ACTIVITY_XML.replace("</FlexStatement>", "<Trades><Trade symbol='X' /></Trades></FlexStatement>")
    assert classify_flex_xml(xml) == ACTIVITY


def test_missing_transfers_is_reject():
    xml = ACTIVITY_XML.replace("<Transfers>", "<NoTransfers>").replace("</Transfers>", "</NoTransfers>")
    xml = xml.replace("<Transfer ", "<NotTransfer ")
    with pytest.raises(FlexClassifyError, match="ambiguous"):
        classify_flex_xml(xml)


def test_empty_transfers_section_is_activity():
    """A session with no ACATS is still 1442520."""
    xml = (
        "<FlexQueryResponse><FlexStatements>"
        "<FlexStatement>"
        "<EquitySummaryInBase>"
        '<EquitySummaryByReportDateInBase reportDate="20260105" total="1" />'
        "</EquitySummaryInBase>"
        "<CashTransactions></CashTransactions>"
        "<Transfers></Transfers>"
        "</FlexStatement></FlexStatements></FlexQueryResponse>"
    )
    assert classify_flex_xml(xml) == ACTIVITY


def test_garbage_is_reject():
    with pytest.raises(FlexClassifyError, match="unreadable"):
        classify_flex_xml("not xml")


def test_empty_trades_section_is_trades():
    """A session with no fills is still 1422766."""
    xml = (
        "<FlexQueryResponse><FlexStatements>"
        "<FlexStatement>"
        "<Trades></Trades>"
        "</FlexStatement></FlexStatements></FlexQueryResponse>"
    )
    assert classify_flex_xml(xml) == TRADES


# xml.etree.ElementTree expands internal entities. Statement text reaches the
# classifier from `--file`, `--inbox` and the sFTP delivery, so an internal DTD
# with nested entities inflates in memory inside the ingest process. Attacker
# control over that channel is weak -- IB is the upstream and the inbox is
# operator-fed -- so the realistic impact is ingest availability, which is
# exactly why rejecting the declaration outright costs nothing: a Flex
# statement never carries one.
BILLION_LAUGHS = (
    '<?xml version="1.0"?>\n'
    "<!DOCTYPE FlexQueryResponse [\n"
    '  <!ENTITY a "aaaaaaaaaa">\n'
    '  <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">\n'
    '  <!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">\n'
    "]>\n"
    "<FlexQueryResponse><FlexStatements><FlexStatement>"
    "<CashTransactions>&c;</CashTransactions>"
    "</FlexStatement></FlexStatements></FlexQueryResponse>"
)


def test_a_doctype_declaration_is_refused_by_the_classifier():
    with pytest.raises(FlexClassifyError, match="doctype"):
        classify_flex_xml(BILLION_LAUGHS)


def test_a_doctype_declaration_is_refused_by_the_metadata_reader():
    from lib.flex_classify import statement_metadata

    with pytest.raises(FlexClassifyError, match="doctype"):
        statement_metadata(BILLION_LAUGHS)


def test_the_real_fixtures_still_parse():
    from lib.flex_classify import statement_metadata

    for xml in (ACTIVITY_XML, TRADES_XML):
        assert statement_metadata(xml)["account_id"]
