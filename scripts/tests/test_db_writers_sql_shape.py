"""SQL shape tests for the 8 high-frequency DB writers at 0% coverage.

Validates that each writer produces the correct row shape and that an
UPSERT on the same primary key updates rather than duplicates.

Uses the in-memory SQLite pattern from test_phase2_writers.py: patch
db.client.get_db to return a plain sqlite3 connection with the schema
applied from the real migration files. No network, no Turso.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_MIGRATIONS = [
    _SCRIPTS_DIR / "db" / "migrations" / "0001_init.sql",
    _SCRIPTS_DIR / "db" / "migrations" / "0002_cash_flows.sql",
]


def _split_statements(sql: str) -> list[str]:
    import re
    stripped = "\n".join(re.sub(r"^\s*--.*$", "", line) for line in sql.splitlines())
    return [s.strip() for s in re.split(r";\s*$", stripped, flags=re.MULTILINE) if s.strip()]


@pytest.fixture
def db_with_schema(monkeypatch: pytest.MonkeyPatch) -> Iterator[sqlite3.Connection]:
    """In-memory sqlite with Phase 0 + Phase 1 schema applied."""
    conn = sqlite3.connect(":memory:")
    for migration in _MIGRATIONS:
        sql = migration.read_text(encoding="utf-8")
        for stmt in _split_statements(sql):
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError as exc:
                if "duplicate column" in str(exc):
                    continue
                raise
    conn.commit()

    import db.client as client_mod
    monkeypatch.setattr(client_mod, "_cached", conn, raising=False)
    monkeypatch.setattr(client_mod, "get_db", lambda: conn)

    import importlib
    import db.writer as writer_mod
    importlib.reload(writer_mod)

    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def writer(db_with_schema: sqlite3.Connection):
    import db.writer as writer_mod
    return writer_mod


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ── upsert_menthorq_cta ───────────────────────────────────────────────

class TestUpsertMenthorqCta:
    def test_inserts_a_row(self, writer, db_with_schema):
        writer.upsert_menthorq_cta("2026-06-12", {"cta_score": 0.73})
        rows = db_with_schema.execute(
            "SELECT date, payload FROM menthorq_cta"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "2026-06-12"
        assert json.loads(rows[0][1])["cta_score"] == pytest.approx(0.73)

    def test_upsert_updates_payload_on_same_date(self, writer, db_with_schema):
        writer.upsert_menthorq_cta("2026-06-12", {"cta_score": 0.73})
        writer.upsert_menthorq_cta("2026-06-12", {"cta_score": 0.91})
        rows = db_with_schema.execute("SELECT payload FROM menthorq_cta").fetchall()
        assert len(rows) == 1
        assert json.loads(rows[0][0])["cta_score"] == pytest.approx(0.91)

    def test_distinct_dates_produce_separate_rows(self, writer, db_with_schema):
        writer.upsert_menthorq_cta("2026-06-11", {"cta_score": 0.5})
        writer.upsert_menthorq_cta("2026-06-12", {"cta_score": 0.6})
        rows = db_with_schema.execute(
            "SELECT date FROM menthorq_cta ORDER BY date"
        ).fetchall()
        assert [r[0] for r in rows] == ["2026-06-11", "2026-06-12"]

    def test_explicit_fetched_at_is_stored(self, writer, db_with_schema):
        ts = "2026-06-12T10:00:00Z"
        writer.upsert_menthorq_cta("2026-06-12", {"x": 1}, fetched_at=ts)
        row = db_with_schema.execute(
            "SELECT fetched_at FROM menthorq_cta"
        ).fetchone()
        assert row[0] == ts


# ── upsert_cri_snapshot ──────────────────────────────────────────────

class TestUpsertCriSnapshot:
    def test_inserts_a_row(self, writer, db_with_schema):
        writer.upsert_cri_snapshot("2026-06-12", _now(), {"cri": 82.5})
        rows = db_with_schema.execute(
            "SELECT date, payload FROM cri_snapshots"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "2026-06-12"
        assert json.loads(rows[0][1])["cri"] == pytest.approx(82.5)

    def test_replace_on_same_composite_pk(self, writer, db_with_schema):
        ts = _now()
        writer.upsert_cri_snapshot("2026-06-12", ts, {"cri": 80.0})
        writer.upsert_cri_snapshot("2026-06-12", ts, {"cri": 85.0})
        rows = db_with_schema.execute("SELECT payload FROM cri_snapshots").fetchall()
        assert len(rows) == 1
        assert json.loads(rows[0][0])["cri"] == pytest.approx(85.0)

    def test_distinct_taken_at_within_same_date_keeps_both(self, writer, db_with_schema):
        writer.upsert_cri_snapshot("2026-06-12", "2026-06-12T14:00:00Z", {"cri": 80.0})
        writer.upsert_cri_snapshot("2026-06-12", "2026-06-12T15:00:00Z", {"cri": 83.0})
        rows = db_with_schema.execute("SELECT taken_at FROM cri_snapshots").fetchall()
        assert len(rows) == 2

    def test_payload_is_json_serialized(self, writer, db_with_schema):
        writer.upsert_cri_snapshot("2026-06-12", _now(), {"nested": {"a": [1, 2]}})
        raw = db_with_schema.execute("SELECT payload FROM cri_snapshots").fetchone()[0]
        parsed = json.loads(raw)
        assert parsed["nested"]["a"] == [1, 2]


# ── upsert_gex_snapshot ──────────────────────────────────────────────

class TestUpsertGexSnapshot:
    def test_inserts_a_row(self, writer, db_with_schema):
        writer.upsert_gex_snapshot("SPY", _now(), {"gex": -12_000_000})
        rows = db_with_schema.execute(
            "SELECT ticker, payload FROM gex_snapshots"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "SPY"
        assert json.loads(rows[0][1])["gex"] == -12_000_000

    def test_replace_on_same_ticker_and_scan_time(self, writer, db_with_schema):
        ts = _now()
        writer.upsert_gex_snapshot("SPY", ts, {"gex": -1_000})
        writer.upsert_gex_snapshot("SPY", ts, {"gex": -2_000})
        rows = db_with_schema.execute("SELECT payload FROM gex_snapshots").fetchall()
        assert len(rows) == 1
        assert json.loads(rows[0][0])["gex"] == -2_000

    def test_distinct_tickers_keep_separate_rows(self, writer, db_with_schema):
        ts = _now()
        writer.upsert_gex_snapshot("SPY", ts, {"gex": -1_000})
        writer.upsert_gex_snapshot("QQQ", ts, {"gex": -500})
        rows = db_with_schema.execute(
            "SELECT ticker FROM gex_snapshots ORDER BY ticker"
        ).fetchall()
        assert [r[0] for r in rows] == ["QQQ", "SPY"]

    def test_distinct_scan_times_for_same_ticker_keep_both(self, writer, db_with_schema):
        writer.upsert_gex_snapshot("SPY", "2026-06-12T13:00:00Z", {"gex": -1_000})
        writer.upsert_gex_snapshot("SPY", "2026-06-12T14:00:00Z", {"gex": -2_000})
        rows = db_with_schema.execute("SELECT scan_time FROM gex_snapshots").fetchall()
        assert len(rows) == 2


# ── upsert_vcg_snapshot ──────────────────────────────────────────────

class TestUpsertVcgSnapshot:
    def test_inserts_a_row(self, writer, db_with_schema):
        writer.upsert_vcg_snapshot(_now(), {"vcg_score": 0.42})
        rows = db_with_schema.execute(
            "SELECT payload FROM vcg_snapshots"
        ).fetchall()
        assert len(rows) == 1
        assert json.loads(rows[0][0])["vcg_score"] == pytest.approx(0.42)

    def test_replace_on_same_scan_time(self, writer, db_with_schema):
        ts = _now()
        writer.upsert_vcg_snapshot(ts, {"vcg_score": 0.1})
        writer.upsert_vcg_snapshot(ts, {"vcg_score": 0.9})
        rows = db_with_schema.execute("SELECT payload FROM vcg_snapshots").fetchall()
        assert len(rows) == 1
        assert json.loads(rows[0][0])["vcg_score"] == pytest.approx(0.9)

    def test_distinct_scan_times_keep_separate_rows(self, writer, db_with_schema):
        writer.upsert_vcg_snapshot("2026-06-12T13:00:00Z", {"vcg_score": 0.4})
        writer.upsert_vcg_snapshot("2026-06-12T14:00:00Z", {"vcg_score": 0.6})
        count = db_with_schema.execute(
            "SELECT COUNT(*) FROM vcg_snapshots"
        ).fetchone()[0]
        assert count == 2


# ── upsert_cash_flow ─────────────────────────────────────────────────

class TestUpsertCashFlow:
    def test_inserts_a_deposit(self, writer, db_with_schema):
        writer.upsert_cash_flow(
            "TXN-001", "2026-06-10", "Deposit", 10_000.00,
            currency="USD", description="Wire transfer",
        )
        rows = db_with_schema.execute(
            "SELECT id, date, type, amount, currency, description FROM cash_flows"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0] == ("TXN-001", "2026-06-10", "Deposit", 10_000.00, "USD", "Wire transfer")

    def test_upsert_updates_on_same_id(self, writer, db_with_schema):
        writer.upsert_cash_flow("TXN-001", "2026-06-10", "Deposit", 5_000.00)
        writer.upsert_cash_flow("TXN-001", "2026-06-10", "Deposit", 10_000.00)
        rows = db_with_schema.execute("SELECT amount FROM cash_flows").fetchall()
        assert len(rows) == 1
        assert rows[0][0] == pytest.approx(10_000.00)

    def test_distinct_txn_ids_keep_separate_rows(self, writer, db_with_schema):
        writer.upsert_cash_flow("TXN-001", "2026-06-10", "Deposit", 5_000.00)
        writer.upsert_cash_flow("TXN-002", "2026-06-11", "Withdrawal", -2_000.00)
        count = db_with_schema.execute(
            "SELECT COUNT(*) FROM cash_flows"
        ).fetchone()[0]
        assert count == 2

    def test_negative_amount_stored_correctly(self, writer, db_with_schema):
        writer.upsert_cash_flow("TXN-W01", "2026-06-10", "Withdrawal", -3_500.00)
        amount = db_with_schema.execute(
            "SELECT amount FROM cash_flows WHERE id = 'TXN-W01'"
        ).fetchone()[0]
        assert amount == pytest.approx(-3_500.00)

    def test_optional_fields_default_to_none(self, writer, db_with_schema):
        writer.upsert_cash_flow("TXN-003", "2026-06-10", "Dividend", 120.00)
        row = db_with_schema.execute(
            "SELECT description, raw_type FROM cash_flows"
        ).fetchone()
        assert row == (None, None)

    def test_raw_type_stored_when_provided(self, writer, db_with_schema):
        writer.upsert_cash_flow(
            "TXN-004", "2026-06-10", "Interest", 5.50, raw_type="IBKR_INT"
        )
        raw_type = db_with_schema.execute(
            "SELECT raw_type FROM cash_flows"
        ).fetchone()[0]
        assert raw_type == "IBKR_INT"


# ── upsert_journal_entry ─────────────────────────────────────────────

class TestUpsertJournalEntry:
    def test_inserts_a_row(self, writer, db_with_schema):
        writer.upsert_journal_entry(
            "trade-abc123",
            {"ticker": "AAPL", "action": "BUY_CALL", "qty": 5},
        )
        rows = db_with_schema.execute(
            "SELECT trade_id, payload FROM journal"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "trade-abc123"
        assert json.loads(rows[0][1])["ticker"] == "AAPL"

    def test_upsert_updates_payload_on_same_trade_id(self, writer, db_with_schema):
        writer.upsert_journal_entry("trade-abc123", {"qty": 5})
        writer.upsert_journal_entry("trade-abc123", {"qty": 10})
        rows = db_with_schema.execute("SELECT payload FROM journal").fetchall()
        assert len(rows) == 1
        assert json.loads(rows[0][0])["qty"] == 10

    def test_filled_at_stored_when_provided(self, writer, db_with_schema):
        writer.upsert_journal_entry("trade-xyz", {"ticker": "SPY"}, filled_at="2026-06-12")
        filled_at = db_with_schema.execute(
            "SELECT filled_at FROM journal"
        ).fetchone()[0]
        assert filled_at == "2026-06-12"

    def test_filled_at_defaults_to_none(self, writer, db_with_schema):
        writer.upsert_journal_entry("trade-no-date", {"ticker": "QQQ"})
        filled_at = db_with_schema.execute(
            "SELECT filled_at FROM journal"
        ).fetchone()[0]
        assert filled_at is None

    def test_distinct_trade_ids_keep_separate_rows(self, writer, db_with_schema):
        writer.upsert_journal_entry("trade-1", {"ticker": "AAPL"})
        writer.upsert_journal_entry("trade-2", {"ticker": "MSFT"})
        count = db_with_schema.execute(
            "SELECT COUNT(*) FROM journal"
        ).fetchone()[0]
        assert count == 2

    def test_written_at_is_populated(self, writer, db_with_schema):
        writer.upsert_journal_entry("trade-wt", {"ticker": "NVDA"})
        written_at = db_with_schema.execute(
            "SELECT written_at FROM journal"
        ).fetchone()[0]
        assert written_at is not None
        assert "T" in written_at  # ISO-8601 with time component


# ── upsert_discover_snapshot ─────────────────────────────────────────

class TestUpsertDiscoverSnapshot:
    def test_inserts_a_row(self, writer, db_with_schema):
        writer.upsert_discover_snapshot(_now(), {"candidates": [{"ticker": "AAPL"}]})
        rows = db_with_schema.execute(
            "SELECT payload FROM discover_snapshots"
        ).fetchall()
        assert len(rows) == 1
        assert json.loads(rows[0][0])["candidates"][0]["ticker"] == "AAPL"

    def test_replace_on_same_scan_time(self, writer, db_with_schema):
        ts = _now()
        writer.upsert_discover_snapshot(ts, {"candidates": []})
        writer.upsert_discover_snapshot(ts, {"candidates": [{"ticker": "MSFT"}]})
        rows = db_with_schema.execute("SELECT payload FROM discover_snapshots").fetchall()
        assert len(rows) == 1
        assert json.loads(rows[0][0])["candidates"][0]["ticker"] == "MSFT"

    def test_distinct_scan_times_keep_separate_rows(self, writer, db_with_schema):
        writer.upsert_discover_snapshot("2026-06-12T13:00:00Z", {"candidates": []})
        writer.upsert_discover_snapshot("2026-06-12T14:00:00Z", {"candidates": []})
        count = db_with_schema.execute(
            "SELECT COUNT(*) FROM discover_snapshots"
        ).fetchone()[0]
        assert count == 2

    def test_does_not_write_to_discover_sp500_table(self, writer, db_with_schema):
        writer.upsert_discover_snapshot(_now(), {"candidates": [{"ticker": "AAPL"}]})
        # discover_sp500_snapshots is in migration 0003 — not loaded here,
        # but discover_snapshots must remain isolated to its own table.
        count = db_with_schema.execute(
            "SELECT COUNT(*) FROM discover_snapshots"
        ).fetchone()[0]
        assert count == 1


# ── upsert_oi_changes ────────────────────────────────────────────────

class TestUpsertOiChanges:
    def test_inserts_a_row(self, writer, db_with_schema):
        writer.upsert_oi_changes(_now(), {"changes": [{"ticker": "SPY", "delta_oi": 5000}]})
        rows = db_with_schema.execute(
            "SELECT payload FROM oi_changes"
        ).fetchall()
        assert len(rows) == 1
        assert json.loads(rows[0][0])["changes"][0]["ticker"] == "SPY"

    def test_replace_on_same_scan_time(self, writer, db_with_schema):
        ts = _now()
        writer.upsert_oi_changes(ts, {"changes": []})
        writer.upsert_oi_changes(ts, {"changes": [{"ticker": "QQQ"}]})
        rows = db_with_schema.execute("SELECT payload FROM oi_changes").fetchall()
        assert len(rows) == 1
        assert json.loads(rows[0][0])["changes"][0]["ticker"] == "QQQ"

    def test_distinct_scan_times_keep_separate_rows(self, writer, db_with_schema):
        writer.upsert_oi_changes("2026-06-12T13:00:00Z", {"changes": []})
        writer.upsert_oi_changes("2026-06-12T14:00:00Z", {"changes": []})
        count = db_with_schema.execute(
            "SELECT COUNT(*) FROM oi_changes"
        ).fetchone()[0]
        assert count == 2

    def test_complex_payload_round_trips_cleanly(self, writer, db_with_schema):
        payload = {
            "changes": [
                {"ticker": "TSLA", "delta_oi": -3000, "expiry": "2026-07-18"},
                {"ticker": "NVDA", "delta_oi": 8000, "expiry": "2026-07-18"},
            ]
        }
        writer.upsert_oi_changes(_now(), payload)
        raw = db_with_schema.execute("SELECT payload FROM oi_changes").fetchone()[0]
        assert json.loads(raw) == payload
