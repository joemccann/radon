"""Tests for exit_order_service.py — expiry extraction from order data."""
import json
import pytest
import sqlite3
from unittest.mock import patch

from exit_order_service import extract_expiry, load_pending_exits, update_trade_log


def _journal_db(*payloads: dict) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE journal (
            trade_id TEXT PRIMARY KEY,
            payload TEXT,
            filled_at TEXT,
            written_at TEXT
        )
        """
    )
    for idx, payload in enumerate(payloads, start=1):
        conn.execute(
            "INSERT INTO journal (trade_id, payload, filled_at, written_at) VALUES (?, ?, ?, ?)",
            (f"trade-{idx}", json.dumps(payload), "2026-06-01", f"2026-06-01T00:00:0{idx}Z"),
        )
    conn.commit()
    return conn


class TestExtractExpiry:
    """Test expiry extraction from order/trade data."""

    def test_expiry_from_legs_field(self):
        """When legs have an 'expiry' field, use it directly."""
        order = {
            "ticker": "GOOG",
            "legs": [
                {"type": "Long Call", "strike": 315, "expiry": "2026-04-17"},
                {"type": "Short Call", "strike": 340, "expiry": "2026-04-17"},
            ],
        }
        assert extract_expiry(order) == "20260417"

    def test_expiry_from_legs_yyyymmdd_format(self):
        """Legs with expiry already in YYYYMMDD format."""
        order = {
            "ticker": "AAPL",
            "legs": [
                {"type": "Long Call", "strike": 200, "expiry": "20260515"},
                {"type": "Short Call", "strike": 220, "expiry": "20260515"},
            ],
        }
        assert extract_expiry(order) == "20260515"

    def test_expiry_from_contract_description(self):
        """Parse expiry from contract string like 'GOOG Apr 17, 2026 $315/$340 Call Spread'."""
        order = {
            "ticker": "GOOG",
            "contract": "GOOG Apr 17, 2026 $315/$340 Call Spread",
            "legs": [
                {"type": "Long Call", "strike": 315},
                {"type": "Short Call", "strike": 340},
            ],
        }
        assert extract_expiry(order) == "20260417"

    def test_expiry_from_contract_month_name_formats(self):
        """Various month names in contract description."""
        order = {
            "ticker": "SPY",
            "contract": "SPY Jan 15, 2027 $500/$520 Call Spread",
            "legs": [
                {"type": "Long Call", "strike": 500},
                {"type": "Short Call", "strike": 520},
            ],
        }
        assert extract_expiry(order) == "20270115"

    def test_expiry_from_contract_mar_format(self):
        """Test Mar abbreviation in contract description."""
        order = {
            "ticker": "EWY",
            "contract": "EWY Mar 13, 2026 $148/$140 Put Spread",
            "legs": [
                {"type": "Long Put", "strike": 148},
                {"type": "Short Put", "strike": 140},
            ],
        }
        assert extract_expiry(order) == "20260313"

    def test_fallback_when_no_expiry_available(self):
        """When no expiry can be parsed, return None and let caller handle fallback."""
        order = {
            "ticker": "XYZ",
            "legs": [
                {"type": "Long Call", "strike": 100},
                {"type": "Short Call", "strike": 120},
            ],
        }
        assert extract_expiry(order) is None

    def test_empty_legs(self):
        """Empty legs list returns None."""
        order = {"ticker": "XYZ", "legs": []}
        assert extract_expiry(order) is None

    def test_no_legs_key(self):
        """Missing legs key returns None (unless contract has info)."""
        order = {"ticker": "XYZ"}
        assert extract_expiry(order) is None

    def test_mixed_legs_first_expiry_wins(self):
        """If only first leg has expiry, still extract it."""
        order = {
            "ticker": "GOOG",
            "legs": [
                {"type": "Long Call", "strike": 315, "expiry": "2026-04-17"},
                {"type": "Short Call", "strike": 340},
            ],
        }
        assert extract_expiry(order) == "20260417"


class TestJournalBackedExitOrders:
    def test_load_pending_exits_reads_journal_rows(self):
        db = _journal_db(
            {
                "id": 42,
                "ticker": "GOOG",
                "structure": "Call Spread",
                "contract": "GOOG Apr 17, 2026 $315/$340 Call Spread",
                "contracts": 1,
                "fill_price": 2.5,
                "legs": [{"type": "Long Call", "expiry": "2026-04-17"}],
                "exit_orders": {"target": {"status": "PENDING_MANUAL", "target_price": 4.0}},
            },
            {
                "id": 43,
                "ticker": "AAPL",
                "exit_orders": {"target": {"status": "ACTIVE", "target_price": 2.0}},
            },
        )

        with patch("db.client.get_db", return_value=db):
            pending = load_pending_exits()

        assert len(pending) == 1
        assert pending[0]["trade_id"] == 42
        assert pending[0]["journal_trade_id"] == "trade-1"
        assert pending[0]["target_price"] == 4.0

    def test_update_trade_log_updates_journal_payload(self):
        db = _journal_db(
            {
                "id": 42,
                "ticker": "GOOG",
                "exit_orders": {"target": {"status": "PENDING_MANUAL", "target_price": 4.0}},
            }
        )

        with patch("db.client.get_db", return_value=db):
            assert update_trade_log(42, 99, "ACTIVE") is True

        raw = db.execute("SELECT payload FROM journal WHERE trade_id = ?", ("trade-1",)).fetchone()[0]
        payload = json.loads(raw)
        assert payload["exit_orders"]["target"]["status"] == "ACTIVE"
        assert payload["exit_orders"]["target"]["order_id"] == 99
        assert payload["exit_orders"]["target"]["target_price"] == 4.0
