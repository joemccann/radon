"""Unit tests for scripts/cash_flow_sync.py — classifier, date normalizer, parser."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Make scripts/ importable.
SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import cash_flow_sync
from cash_flow_sync import _classify, _normalize_date, parse_cash_transactions

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "cash_transactions_flex_sample.xml"


class TestClassify:
    """Map IB's free-form `type` strings to our normalized buckets."""

    def test_explicit_deposit(self):
        assert _classify("Deposit", 35_000.0) == "Deposit"

    def test_explicit_withdrawal(self):
        assert _classify("Withdrawal", -35_000.0) == "Withdrawal"

    def test_combined_label_uses_amount_sign(self):
        # "Deposits/Withdrawals" is one label IB uses for combined rows
        assert _classify("Deposits/Withdrawals", 35_000.0) == "Deposit"
        assert _classify("Deposits/Withdrawals", -35_000.0) == "Withdrawal"

    def test_dividend(self):
        assert _classify("Dividends", 245.50) == "Dividend"
        assert _classify("Payment In Lieu Of Dividend", 5.0) == "Dividend"

    def test_withholding_tax(self):
        assert _classify("Withholding Tax", -36.83) == "WithholdingTax"

    def test_interest(self):
        assert _classify("Broker Interest Received", 12.34) == "Interest"
        assert _classify("Credit Interest", 7.50) == "Interest"
        assert _classify("Debit Interest", -3.25) == "Interest"

    def test_fee(self):
        assert _classify("Other Fees", -1.50) == "Fee"
        assert _classify("Commission Adjustments", -0.75) == "Fee"

    def test_unknown_falls_back_to_other(self):
        assert _classify("Some Strange Label", 100.0) == "Other"

    def test_empty_label(self):
        assert _classify("", 100.0) == "Other"


class TestNormalizeDate:
    """IB sometimes returns YYYYMMDD (compact), sometimes YYYY-MM-DD (ISO)."""

    def test_compact_to_iso(self):
        assert _normalize_date("20260504") == "2026-05-04"

    def test_already_iso(self):
        assert _normalize_date("2026-05-04") == "2026-05-04"

    def test_iso_with_extra_time_suffix(self):
        # IB sometimes appends `;12:00:00` or similar — we only want the date
        assert _normalize_date("2026-05-04;12:00:00") == "2026-05-04"

    def test_compact_with_time_suffix(self):
        assert _normalize_date("20260504;120000") == "2026-05-04"

    def test_empty(self):
        assert _normalize_date("") == ""
        assert _normalize_date(None) == ""  # type: ignore[arg-type]

    def test_short_garbage_passthrough(self):
        # Don't pretend to know how to fix garbage — pass it through
        assert _normalize_date("xyz") == "xyz"


# Every expected value below is hand-derived from
# fixtures/cash_transactions_flex_sample.xml. Never regenerate it by calling
# parse_cash_transactions — that would pin whatever the parser happens to do.
#
# The fixture holds 20 CashTransaction elements. Four are dropped:
#   * MSFT dividend 910    — no transactionID (IBKR aggregate shape)
#   * WITHDRAWAL FEE -2    — no transactionID, aggregates the two -1 fees
#   * DISBURSEMENT -102000 — no transactionID, aggregates -42000 + -60000
#   * ZERO AMOUNT -> "0"   — amount == 0
# 20 - 4 = 16 rows survive.
EXPECTED_ROWS = [
    {"id": "35731598269", "date": "2025-10-21", "type": "Deposit", "amount": 152.94,
     "currency": "USD", "description": "ADJUSTMENT: DEPOSIT ADVANCE",
     "raw_type": "Deposits/Withdrawals"},
    {"id": "35830439490", "date": "2025-10-27", "type": "Deposit", "amount": 100000.0,
     "currency": "USD", "description": "CASH RECEIPTS / ELECTRONIC FUND TRANSFERS",
     "raw_type": "Deposits/Withdrawals"},
    {"id": "35830439491", "date": "2025-10-27", "type": "Withdrawal", "amount": -152.94,
     "currency": "USD", "description": "CANCELLATION",
     "raw_type": "Deposits/Withdrawals"},
    {"id": "35909440684", "date": "2025-10-29", "type": "Fee", "amount": -1.5,
     "currency": "USD",
     "description": "MARKET DATA: AMEX (CTA TAPE B) LEVEL 1 FOR OCT 2025",
     "raw_type": "Other Fees"},
    {"id": "37069558207", "date": "2025-12-31", "type": "Dividend", "amount": 69.09,
     "currency": "USD",
     "description": "SPXL(US25459W8626) CASH DIVIDEND USD 0.17186 PER SHARE (Ordinary Dividend)",
     "raw_type": "Dividends"},
    {"id": "37069558209", "date": "2025-12-31", "type": "Dividend", "amount": 32.48,
     "currency": "USD",
     "description": "SPXL(US25459W8626) PAYMENT IN LIEU OF DIVIDEND (Ordinary Dividend)",
     "raw_type": "Payment In Lieu Of Dividends"},
    {"id": "36054840804", "date": "2025-11-05", "type": "Interest", "amount": 81.48,
     "currency": "USD", "description": "USD CREDIT INT FOR OCT-2025",
     "raw_type": "Broker Interest Received"},
    {"id": "36054840844", "date": "2025-11-05", "type": "Interest", "amount": -0.95,
     "currency": "USD", "description": "USD DEBIT INT FOR OCT-2025",
     "raw_type": "Broker Interest Paid"},
    # raw_type "Other Fees" wins over the "WITHDRAWAL FEE" description — only
    # `type` is classified, never the free-text description.
    {"id": "40930382794", "date": "2026-06-24", "type": "Fee", "amount": -1.0,
     "currency": "USD", "description": "WITHDRAWAL FEE", "raw_type": "Other Fees"},
    {"id": "40915218485", "date": "2026-06-24", "type": "Fee", "amount": -1.0,
     "currency": "USD", "description": "WITHDRAWAL FEE", "raw_type": "Other Fees"},
    {"id": "40915218486", "date": "2026-06-24", "type": "Withdrawal", "amount": -42000.0,
     "currency": "USD", "description": "DISBURSEMENT INITIATED BY ACCOUNT HOLDER",
     "raw_type": "Deposits/Withdrawals"},
    {"id": "40930382802", "date": "2026-06-24", "type": "Withdrawal", "amount": -60000.0,
     "currency": "USD", "description": "DISBURSEMENT INITIATED BY ACCOUNT HOLDER",
     "raw_type": "Deposits/Withdrawals"},
    # Same transactionID on all three — IBKR really does reuse it. No dedupe.
    {"id": "41191444701", "date": "2026-07-06", "type": "Interest", "amount": -23.71,
     "currency": "USD", "description": "USD BORROW FEES FOR JUN-2026",
     "raw_type": "Broker Interest Paid"},
    {"id": "41191444701", "date": "2026-07-06", "type": "Interest", "amount": 61.89,
     "currency": "USD",
     "description": "USD IBKR MANAGED SECURITIES (SYEP) FOR JUN-2026",
     "raw_type": "Broker Interest Received"},
    {"id": "41191444701", "date": "2026-07-06", "type": "Interest", "amount": 182.03,
     "currency": "USD", "description": "USD SHORT CREDIT INTEREST FOR JUN-2026",
     "raw_type": "Broker Interest Received"},
    # No reportDate — date falls back to dateTime "20260708;120000".
    {"id": "99999999998", "date": "2026-07-08", "type": "Fee", "amount": -0.75,
     "currency": "USD", "description": "LATE FEE, NO REPORT DATE",
     "raw_type": "Other Fees"},
]


class TestParseCashTransactions:
    """The parser is pure: a saved Flex statement in, rows out, no I/O.

    Every test here reads a committed fixture. Nothing may touch the Flex
    Web Service — the account sits behind a sliding-window throttle and one
    stray SendRequest costs a week of cash-flow sync.
    """

    def _parse_fixture(self) -> list[dict]:
        return parse_cash_transactions(FIXTURE.read_text(encoding="utf-8"))

    def test_makes_no_network_call(self):
        """Hard guard: urlopen must never be reached from the parse path."""
        with patch.object(cash_flow_sync, "urlopen", MagicMock()) as mock_urlopen, \
             patch.object(cash_flow_sync, "time", MagicMock()):
            self._parse_fixture()
        mock_urlopen.assert_not_called()

    def test_row_count(self):
        # 20 CashTransaction elements - 3 with no transactionID - 1 zero-amount = 16
        assert len(self._parse_fixture()) == 16

    def test_rows_match_expected_exactly(self):
        assert self._parse_fixture() == EXPECTED_ROWS

    def test_preserves_document_order(self):
        ids = [r["id"] for r in self._parse_fixture()]
        assert ids == [r["id"] for r in EXPECTED_ROWS]

    def test_emitted_keys_are_the_writer_contract(self):
        expected_keys = {"id", "date", "type", "amount", "currency", "description", "raw_type"}
        for row in self._parse_fixture():
            assert set(row) == expected_keys

    def test_skips_ibkr_aggregate_rows_without_transaction_id(self):
        """2026-06-24 carries -42,000 and -60,000 detail rows plus a -102,000
        aggregate with no transactionID. Parsing the aggregate double-counts
        the day."""
        rows = self._parse_fixture()
        june24_withdrawals = [
            r for r in rows if r["date"] == "2026-06-24" and r["type"] == "Withdrawal"
        ]
        assert len(june24_withdrawals) == 2
        # -42000.00 + -60000.00 = -102000.00, which is exactly the amount on the
        # skipped aggregate row. Seeing -204000.00 here means the skip regressed.
        assert sum(r["amount"] for r in june24_withdrawals) == -102000.0

        june24_fees = [r for r in rows if r["date"] == "2026-06-24" and r["type"] == "Fee"]
        assert len(june24_fees) == 2
        # -1.00 + -1.00 = -2.00, the amount on the skipped aggregate fee row.
        assert sum(r["amount"] for r in june24_fees) == -2.0

    def test_skips_zero_amount_rows(self):
        assert "99999999999" not in {r["id"] for r in self._parse_fixture()}

    def test_keeps_every_row_sharing_a_duplicated_transaction_id(self):
        """IBKR reuses 41191444701 across three distinct 2026-07-06 rows."""
        shared = [r for r in self._parse_fixture() if r["id"] == "41191444701"]
        assert len(shared) == 3
        # -23.71 + 61.89 + 182.03 = 220.21
        assert round(sum(r["amount"] for r in shared), 2) == 220.21

    def test_bucket_breakdown(self):
        by_type: dict[str, int] = {}
        for row in self._parse_fixture():
            by_type[row["type"]] = by_type.get(row["type"], 0) + 1
        # Deposit 2 + Withdrawal 3 + Fee 4 + Dividend 2 + Interest 5 = 16
        assert by_type == {
            "Deposit": 2,
            "Withdrawal": 3,
            "Fee": 4,
            "Dividend": 2,
            "Interest": 5,
        }

    def test_empty_statement_yields_no_rows(self):
        empty = (
            "<FlexQueryResponse><FlexStatements count='1'>"
            "<FlexStatement accountId='U0000000'><CashTransactions/>"
            "</FlexStatement></FlexStatements></FlexQueryResponse>"
        )
        assert parse_cash_transactions(empty) == []


class TestFetchDelegatesToParser:
    """`fetch_cash_transactions` keeps its public behaviour and reuses the
    parser rather than carrying a second copy of the loop."""

    def test_fetch_returns_parser_output_for_the_same_xml(self):
        send_ref = (
            "<FlexStatementResponse><Status>Success</Status>"
            "<ReferenceCode>1234567890</ReferenceCode></FlexStatementResponse>"
        )
        statement = FIXTURE.read_text(encoding="utf-8")

        def _resp(body: str) -> MagicMock:
            resp = MagicMock()
            resp.read.return_value = body.encode("utf-8")
            return resp

        with patch.object(cash_flow_sync, "urlopen") as mock_urlopen, \
             patch.object(cash_flow_sync.time, "sleep"):
            mock_urlopen.side_effect = [_resp(send_ref), _resp(statement)]
            rows = cash_flow_sync.fetch_cash_transactions(
                "tok", "qid", max_polls=2, poll_sleep=0
            )

        assert rows == EXPECTED_ROWS
        assert rows == parse_cash_transactions(statement)
