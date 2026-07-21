"""RV-ratio writer family — migration 0029 + db.writer helpers.

Exercises the SQL shape against an in-memory sqlite3 (the
test_phase2_writers.py pattern): apply the migration, patch
db.client.get_db, assert idempotent upserts and per-symbol snapshot
replacement. No network, no real Turso (db.client refuses real
connections under PYTEST_CURRENT_TEST anyway).
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_MIGRATION = _SCRIPTS_DIR / "db" / "migrations" / "0029_rv_ratio.sql"


def _split_statements(sql: str) -> list[str]:
    stripped = "\n".join(re.sub(r"^\s*--.*$", "", line) for line in sql.splitlines())
    return [s.strip() for s in re.split(r";\s*$", stripped, flags=re.MULTILINE) if s.strip()]


@pytest.fixture
def db_with_schema(monkeypatch: pytest.MonkeyPatch) -> Iterator[sqlite3.Connection]:
    """In-memory sqlite with schema_migrations bootstrap + 0029 applied."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version INTEGER PRIMARY KEY,
          applied_at TEXT NOT NULL
        )
        """
    )
    for stmt in _split_statements(_MIGRATION.read_text(encoding="utf-8")):
        conn.execute(stmt)
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
def writer(db_with_schema):
    import db.writer as writer_mod
    return writer_mod


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _session(days_back: int) -> str:
    """Window-relative session date (today-minus-N), never hardcoded."""
    return (date.today() - timedelta(days=days_back)).isoformat()


def _rows(days_back: list[int], close_base: float = 100.0, source: str = "yahoo"):
    return [
        {"date": _session(n), "close": close_base + i, "source": source}
        for i, n in enumerate(days_back)
    ]


# ── migration shape ──────────────────────────────────────────────────

class TestMigration0029:
    def test_self_records_version_29(self, db_with_schema):
        versions = [
            r[0] for r in db_with_schema.execute("SELECT version FROM schema_migrations").fetchall()
        ]
        assert versions == [29]

    def test_reapply_is_idempotent(self, db_with_schema):
        for stmt in _split_statements(_MIGRATION.read_text(encoding="utf-8")):
            db_with_schema.execute(stmt)
        count = db_with_schema.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
        assert count == 1


# ── price_history_daily ──────────────────────────────────────────────

class TestUpsertPriceHistoryRows:
    def test_inserts_batched_rows(self, writer, db_with_schema):
        writer.upsert_price_history_rows("SMH", _rows([4, 3, 2]))
        rows = db_with_schema.execute(
            "SELECT symbol, date, close, source FROM price_history_daily ORDER BY date"
        ).fetchall()
        assert len(rows) == 3
        assert all(r[0] == "SMH" for r in rows)
        assert [r[1] for r in rows] == [_session(4), _session(3), _session(2)]
        assert rows[0][3] == "yahoo"

    def test_replaces_on_same_symbol_and_date(self, writer, db_with_schema):
        writer.upsert_price_history_rows("SMH", [{"date": _session(2), "close": 100.0, "source": "yahoo"}])
        writer.upsert_price_history_rows("SMH", [{"date": _session(2), "close": 101.5, "source": "ib"}])
        rows = db_with_schema.execute(
            "SELECT close, source FROM price_history_daily WHERE symbol = 'SMH'"
        ).fetchall()
        assert rows == [(101.5, "ib")]

    def test_symbols_are_disjoint(self, writer, db_with_schema):
        writer.upsert_price_history_rows("SMH", _rows([2]))
        writer.upsert_price_history_rows("SPY", _rows([2], close_base=500.0))
        rows = db_with_schema.execute(
            "SELECT symbol, close FROM price_history_daily ORDER BY symbol"
        ).fetchall()
        assert rows == [("SMH", 100.0), ("SPY", 500.0)]

    def test_stamps_fetched_at(self, writer, db_with_schema):
        writer.upsert_price_history_rows("SMH", _rows([2]))
        fetched_at = db_with_schema.execute(
            "SELECT fetched_at FROM price_history_daily"
        ).fetchone()[0]
        assert fetched_at
        # UTC ISO with the house Z suffix.
        assert fetched_at.endswith("Z")

    def test_empty_rows_is_a_noop(self, writer, db_with_schema):
        writer.upsert_price_history_rows("SMH", [])
        count = db_with_schema.execute("SELECT COUNT(*) FROM price_history_daily").fetchone()[0]
        assert count == 0


# ── rv_ratio_snapshots ───────────────────────────────────────────────

class TestUpsertRvRatioSnapshot:
    def test_inserts_a_row(self, writer, db_with_schema):
        ts = _now()
        writer.upsert_rv_ratio_snapshot("SMH", ts, {"schema_version": 1, "symbol": "SMH"})
        rows = db_with_schema.execute(
            "SELECT symbol, taken_at, payload FROM rv_ratio_snapshots"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "SMH"
        assert rows[0][1] == ts
        assert json.loads(rows[0][2])["symbol"] == "SMH"

    def test_latest_snapshot_replaces_per_symbol(self, writer, db_with_schema):
        writer.upsert_rv_ratio_snapshot("SMH", _now(), {"stats": {"last": 1.10}})
        writer.upsert_rv_ratio_snapshot("SMH", _now(), {"stats": {"last": 1.48}})
        rows = db_with_schema.execute(
            "SELECT payload FROM rv_ratio_snapshots WHERE symbol = 'SMH'"
        ).fetchall()
        assert len(rows) == 1
        assert json.loads(rows[0][0])["stats"]["last"] == 1.48

    def test_distinct_symbols_keep_both(self, writer, db_with_schema):
        writer.upsert_rv_ratio_snapshot("SMH", _now(), {"symbol": "SMH"})
        writer.upsert_rv_ratio_snapshot("XLE", _now(), {"symbol": "XLE"})
        symbols = sorted(
            r[0] for r in db_with_schema.execute("SELECT symbol FROM rv_ratio_snapshots").fetchall()
        )
        assert symbols == ["SMH", "XLE"]
