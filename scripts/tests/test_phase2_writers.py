"""Phase 2 writer helpers — tests against an in-memory SQLite DB.

The production writers go to libsql cloud; here we exercise the SQL
shape against an in-memory sqlite3 to verify schema correctness +
idempotency without any network.

Each test installs a fake `db.client.get_db` that hands out the in-mem
connection, then calls the upsert function and asserts row state.
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

# Apply migrations 0001 + 0003 to the in-memory DB so the tables exist.
_MIGRATIONS = [
    _SCRIPTS_DIR / "db" / "migrations" / "0001_init.sql",
    _SCRIPTS_DIR / "db" / "migrations" / "0002_cash_flows.sql",
    _SCRIPTS_DIR / "db" / "migrations" / "0003_phase2_snapshots.sql",
]


def _split_statements(sql: str) -> list[str]:
    import re
    stripped = "\n".join(re.sub(r"^\s*--.*$", "", line) for line in sql.splitlines())
    return [s.strip() for s in re.split(r";\s*$", stripped, flags=re.MULTILINE) if s.strip()]


@pytest.fixture
def db_with_schema(monkeypatch: pytest.MonkeyPatch) -> Iterator[sqlite3.Connection]:
    """In-memory sqlite with Phase 0 + 2 schema applied."""
    conn = sqlite3.connect(":memory:")
    for migration in _MIGRATIONS:
        sql = migration.read_text(encoding="utf-8")
        for stmt in _split_statements(sql):
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError as exc:
                # ALTER TABLE on a fresh DB will fail because the column
                # doesn't pre-exist — but Phase 2 dropped the ALTER.
                # Anything else is a real failure.
                if "duplicate column" in str(exc):
                    continue
                raise
    conn.commit()

    # Patch db.client.get_db to return our in-mem conn.
    import db.client as client_mod
    monkeypatch.setattr(client_mod, "_cached", conn, raising=False)
    monkeypatch.setattr(client_mod, "get_db", lambda: conn)

    # Make sure db.writer's internal `from .client import get_db` picks
    # up the patched function. Reload the writer module.
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


# ── scanner_snapshots ────────────────────────────────────────────────

class TestScannerSnapshot:
    def test_inserts_a_row(self, writer, db_with_schema):
        writer.upsert_scanner_snapshot(_now(), {"signals": [{"ticker": "AAPL", "score": 12}]})
        rows = db_with_schema.execute("SELECT scan_time, payload FROM scanner_snapshots").fetchall()
        assert len(rows) == 1
        payload = json.loads(rows[0][1])
        assert payload["signals"][0]["ticker"] == "AAPL"

    def test_replaces_on_same_scan_time(self, writer, db_with_schema):
        ts = _now()
        writer.upsert_scanner_snapshot(ts, {"signals": []})
        writer.upsert_scanner_snapshot(ts, {"signals": [{"ticker": "MSFT"}]})
        rows = db_with_schema.execute("SELECT payload FROM scanner_snapshots").fetchall()
        assert len(rows) == 1
        assert json.loads(rows[0][0])["signals"][0]["ticker"] == "MSFT"

    def test_distinct_scan_times_keep_both(self, writer, db_with_schema):
        writer.upsert_scanner_snapshot("2026-05-07T15:00:00Z", {"signals": []})
        writer.upsert_scanner_snapshot("2026-05-07T15:30:00Z", {"signals": []})
        rows = db_with_schema.execute("SELECT scan_time FROM scanner_snapshots").fetchall()
        assert len(rows) == 2


# ── flow_analysis_snapshots ──────────────────────────────────────────

class TestFlowAnalysisSnapshot:
    def test_inserts(self, writer, db_with_schema):
        writer.upsert_flow_analysis_snapshot(_now(), {"interp": {"AAPL": 1.5}})
        rows = db_with_schema.execute("SELECT payload FROM flow_analysis_snapshots").fetchall()
        assert json.loads(rows[0][0])["interp"]["AAPL"] == 1.5


# ── performance_snapshots ────────────────────────────────────────────

class TestPerformanceTables:
    def test_performance_snapshot_replace_on_same_taken_at(self, writer, db_with_schema):
        ts = _now()
        writer.upsert_performance_snapshot(ts, {"twr_ytd": 0.12})
        writer.upsert_performance_snapshot(ts, {"twr_ytd": 0.18})
        rows = db_with_schema.execute("SELECT payload FROM performance_snapshots").fetchall()
        assert len(rows) == 1
        assert json.loads(rows[0][0])["twr_ytd"] == 0.18


# ── analyst_ratings (zombie schema bound) ────────────────────────────

class TestAnalystRatings:
    """The analyst_ratings helper existed since 0001_init but had no caller
    until Phase 2.5 wired fetch_analyst_ratings.py to it. Existing helper
    stores ticker as-passed; callers are expected to upper() before
    handing in."""

    def test_inserts(self, writer, db_with_schema):
        ts = _now()
        writer.upsert_analyst_ratings("AAPL", ts, {"buy_pct": 75})
        rows = db_with_schema.execute(
            "SELECT ticker, fetched_at, payload FROM analyst_ratings"
        ).fetchall()
        assert rows[0][0] == "AAPL"
        assert json.loads(rows[0][2])["buy_pct"] == 75

    def test_replace_on_same_ticker_and_fetched_at(self, writer, db_with_schema):
        ts = _now()
        writer.upsert_analyst_ratings("AAPL", ts, {"buy_pct": 50})
        writer.upsert_analyst_ratings("AAPL", ts, {"buy_pct": 75})
        rows = db_with_schema.execute("SELECT payload FROM analyst_ratings").fetchall()
        assert len(rows) == 1
        assert json.loads(rows[0][0])["buy_pct"] == 75


# ── ensure_no_replica_for_writers helper ─────────────────────────────

class TestEnsureNoReplicaForWriters:
    def test_sets_env_var_when_unset(self, writer, monkeypatch):
        import os
        monkeypatch.delenv("RADON_DB_NO_REPLICA", raising=False)
        writer.ensure_no_replica_for_writers()
        assert os.environ.get("RADON_DB_NO_REPLICA") == "1"

    def test_does_not_override_existing_value(self, writer, monkeypatch):
        import os
        monkeypatch.setenv("RADON_DB_NO_REPLICA", "0")
        writer.ensure_no_replica_for_writers()
        # setdefault semantics — pre-existing value preserved
        assert os.environ.get("RADON_DB_NO_REPLICA") == "0"
