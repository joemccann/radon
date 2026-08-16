"""Chunked multi-row writer for cash_flows (plan item C17).

`cash_flow_sync` writes 264 rows as 264 sequential single-row upserts.
Over the Hrana HTTP transport that is 264 round trips on one stream, which
the Turso I/O-bounding rule (scripts/CLAUDE.md) says will 502 and, once
degraded, keep 502ing. There is no try/except around that loop: one 502 at
row 200 aborts the run non-zero, the daemon handler reads it as a soft
failure, and five minutes later it spends ANOTHER Flex SendRequest even
though the fetch already succeeded.

This pins the batched writer. It must produce byte-identical final state to
the per-row loop, including the last-write-wins behaviour for the rows that
share a transactionID — SQLite refuses to UPSERT the same row twice inside
one INSERT statement, so the dedupe is load-bearing, not cosmetic.

The duplicate-id data loss itself (three real 2026-07-06 interest rows
collapsing to one, $38.18 destroyed) is plan item C12 and is deliberately
NOT fixed here — it needs a key-shape decision from the operator. What is
fixed is that the loss is now COUNTED and reported instead of silent.

No network, no Turso: in-memory sqlite with the real migration applied.
"""
from __future__ import annotations

import importlib
import re
import sqlite3
import sys
from pathlib import Path
from typing import Iterator

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_MIGRATION = _SCRIPTS_DIR / "db" / "migrations" / "0002_cash_flows.sql"


def _statements(sql: str) -> list[str]:
    stripped = "\n".join(re.sub(r"^\s*--.*$", "", line) for line in sql.splitlines())
    return [s.strip() for s in re.split(r";\s*$", stripped, flags=re.MULTILINE) if s.strip()]


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(":memory:")
    for stmt in _statements(_MIGRATION.read_text(encoding="utf-8")):
        conn.execute(stmt)
    conn.commit()

    import db.client as client_mod

    monkeypatch.setattr(client_mod, "_cached", conn, raising=False)
    monkeypatch.setattr(client_mod, "get_db", lambda: conn)

    import db.writer as writer_mod

    importlib.reload(writer_mod)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def writer(db: sqlite3.Connection):
    import db.writer as writer_mod

    return writer_mod


def _row(txn_id: str, date: str, amount: float, **over) -> dict:
    return {
        "id": txn_id,
        "date": date,
        "type": over.get("type", "Interest"),
        "amount": amount,
        "currency": "USD",
        "description": over.get("description"),
        "raw_type": over.get("raw_type", "Broker Interest Received"),
    }


class TestUpsertCashFlowRows:
    def test_writes_every_row(self, writer, db):
        writer.upsert_cash_flow_rows(
            [_row("1", "2026-07-06", 10.0), _row("2", "2026-07-07", -5.0)]
        )
        stored = db.execute("SELECT id, amount FROM cash_flows ORDER BY id").fetchall()
        assert stored == [("1", 10.0), ("2", -5.0)]

    def test_is_idempotent_on_a_replay(self, writer, db):
        rows = [_row("1", "2026-07-06", 10.0), _row("2", "2026-07-07", -5.0)]
        writer.upsert_cash_flow_rows(rows)
        writer.upsert_cash_flow_rows(rows)
        assert db.execute("SELECT COUNT(*) FROM cash_flows").fetchone()[0] == 2

    def test_a_revised_amount_updates_in_place(self, writer, db):
        writer.upsert_cash_flow_rows([_row("1", "2026-07-06", 10.0)])
        writer.upsert_cash_flow_rows([_row("1", "2026-07-06", 11.5)])
        assert db.execute("SELECT amount FROM cash_flows").fetchone()[0] == 11.5

    def test_duplicate_ids_in_one_batch_do_not_raise(self, writer, db):
        """The real 2026-07-06 trio: SQLite raises "UNIQUE constraint" /
        "cannot UPSERT a row a second time" if a multi-row INSERT carries
        the same conflict target twice."""
        trio = [
            _row("41191444701", "2026-07-06", -23.71),
            _row("41191444701", "2026-07-06", 61.89),
            _row("41191444701", "2026-07-06", 182.03),
        ]
        dropped = writer.upsert_cash_flow_rows(trio)

        stored = db.execute("SELECT id, amount FROM cash_flows").fetchall()
        # Last write wins — identical to today's per-row loop. C12 widens
        # the key so all three survive; that is an operator decision.
        assert stored == [("41191444701", 182.03)]
        assert dropped == 2

    def test_reports_zero_dropped_when_ids_are_unique(self, writer, db):
        assert writer.upsert_cash_flow_rows(
            [_row("1", "2026-07-06", 1.0), _row("2", "2026-07-06", 2.0)]
        ) == 0

    def test_batches_instead_of_one_statement_per_row(self, writer, db, monkeypatch):
        """264 sequential single-row upserts is 264 Hrana round trips."""
        executed: list[str] = []

        class _CountingConnection:
            def execute(self, sql, *args):
                if sql.strip().upper().startswith("INSERT"):
                    executed.append(sql)
                return db.execute(sql, *args)

            def commit(self):
                return db.commit()

        monkeypatch.setattr(writer, "get_db", lambda: _CountingConnection())
        rows = [_row(str(i), "2026-07-06", float(i)) for i in range(500)]
        writer.upsert_cash_flow_rows(rows)

        assert len(executed) == 2  # 500 rows at a 400-row chunk
        assert db.execute("SELECT COUNT(*) FROM cash_flows").fetchone()[0] == 500

    def test_empty_input_writes_nothing(self, writer, db):
        assert writer.upsert_cash_flow_rows([]) == 0
        assert db.execute("SELECT COUNT(*) FROM cash_flows").fetchone()[0] == 0
