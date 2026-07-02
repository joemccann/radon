#!/usr/bin/env python3
"""Journal expiry canonicalization — ISO YYYY-MM-DD must never reach rows.

Journal option rows key web lot-matching (web/lib/blotter/fromJournal.ts) on
the RAW expiry string, so an ISO "2026-06-26" row silently splits a position
from its "20260626" counterpart. 22 ISO rows were hand-normalized in prod on
2026-07-02 (10 from the backfill script whose executed_orders payloads carry
ISO expiry, 12 organic). The shared helper lives in clients.journal_basis and
is applied at the writer chokepoint (db.writer.upsert_journal_entry) so every
Python emitter is covered.
"""

from __future__ import annotations

import importlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from clients.journal_basis import normalize_expiry_compact  # noqa: E402


class TestNormalizeExpiryCompact:
    def test_iso_becomes_compact(self):
        assert normalize_expiry_compact("2026-06-26") == "20260626"

    def test_compact_passes_through(self):
        assert normalize_expiry_compact("20260626") == "20260626"

    def test_none_passes_through(self):
        assert normalize_expiry_compact(None) is None

    def test_futures_contract_month_passes_through(self):
        assert normalize_expiry_compact("202607") == "202607"

    def test_non_date_string_passes_through(self):
        assert normalize_expiry_compact("N/A") == "N/A"


def _journal_only_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE journal (
          trade_id TEXT PRIMARY KEY,
          payload TEXT NOT NULL,
          filled_at TEXT,
          written_at TEXT
        )
        """
    )
    conn.commit()
    return conn


@pytest.fixture
def writer_with_sqlite(monkeypatch):
    conn = _journal_only_db()

    import db.client as client_mod

    monkeypatch.setattr(client_mod, "get_db", lambda: conn)
    monkeypatch.setattr(client_mod, "_cached", conn, raising=False)

    import db.writer as writer_mod

    writer_mod = importlib.reload(writer_mod)
    return writer_mod, conn


class TestWriterChokepoint:
    def test_upsert_journal_entry_compacts_iso_expiry(self, writer_with_sqlite):
        writer_mod, conn = writer_with_sqlite
        payload = {
            "ticker": "META",
            "action": "SELL_TO_OPEN",
            "contracts": 5,
            "strike": 625.0,
            "right": "C",
            "expiry": "2026-06-26",
            "ib_exec_id": "iso.exec.1",
        }

        writer_mod.upsert_journal_entry("iso.exec.1", payload, filled_at="2026-06-25")

        stored = json.loads(
            conn.execute("SELECT payload FROM journal WHERE trade_id = ?", ("iso.exec.1",)).fetchone()[0]
        )
        assert stored["expiry"] == "20260626"

    def test_upsert_journal_entry_leaves_compact_and_optionless_rows_alone(self, writer_with_sqlite):
        writer_mod, conn = writer_with_sqlite
        option_row = {
            "ticker": "META",
            "action": "BUY_OPTION",
            "contracts": 5,
            "expiry": "20260626",
            "ib_exec_id": "compact.exec.1",
        }
        stock_row = {
            "ticker": "SPY",
            "action": "BUY",
            "shares": 100,
            "ib_exec_id": "stk.exec.1",
        }

        writer_mod.upsert_journal_entry("compact.exec.1", option_row, filled_at="2026-06-25")
        writer_mod.upsert_journal_entry("stk.exec.1", stock_row, filled_at="2026-06-25")

        stored_option = json.loads(
            conn.execute(
                "SELECT payload FROM journal WHERE trade_id = ?", ("compact.exec.1",)
            ).fetchone()[0]
        )
        stored_stock = json.loads(
            conn.execute(
                "SELECT payload FROM journal WHERE trade_id = ?", ("stk.exec.1",)
            ).fetchone()[0]
        )
        assert stored_option["expiry"] == "20260626"
        assert "expiry" not in stored_stock
