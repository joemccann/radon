"""Unit tests for scripts/cash_flow_sync.py — type classifier + date normalizer."""
from __future__ import annotations

import sys
from pathlib import Path

# Make scripts/ importable.
SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cash_flow_sync import _classify, _normalize_date


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
