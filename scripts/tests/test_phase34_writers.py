"""Phase 3 + 4 writer helpers — tests against an in-memory SQLite.

Mirrors test_phase2_writers.py shape: build the schema in-mem, patch
db.client.get_db, exercise the writer.
"""
from __future__ import annotations

import json
import re
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
    _SCRIPTS_DIR / "db" / "migrations" / "0003_phase2_snapshots.sql",
    _SCRIPTS_DIR / "db" / "migrations" / "0004_orders_and_ephemeral.sql",
]


def _split_statements(sql: str) -> list[str]:
    stripped = "\n".join(re.sub(r"^\s*--.*$", "", line) for line in sql.splitlines())
    return [s.strip() for s in re.split(r";\s*$", stripped, flags=re.MULTILINE) if s.strip()]


@pytest.fixture
def db_with_schema(monkeypatch: pytest.MonkeyPatch) -> Iterator[sqlite3.Connection]:
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
def writer(db_with_schema):
    import db.writer as writer_mod
    return writer_mod


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ── open_orders + executed_orders (Phase 3) ──────────────────────────

class TestOpenOrders:
    def test_upsert_inserts_new_row(self, writer, db_with_schema):
        writer.upsert_open_order(9001, {"action": "BUY", "symbol": "AAPL"})
        rows = db_with_schema.execute(
            "SELECT perm_id, payload FROM open_orders"
        ).fetchall()
        assert rows[0][0] == 9001
        assert json.loads(rows[0][1])["symbol"] == "AAPL"

    def test_upsert_replaces_on_same_perm_id(self, writer, db_with_schema):
        writer.upsert_open_order(9001, {"action": "BUY", "qty": 5})
        writer.upsert_open_order(9001, {"action": "BUY", "qty": 10})
        rows = db_with_schema.execute("SELECT payload FROM open_orders").fetchall()
        assert len(rows) == 1
        assert json.loads(rows[0][0])["qty"] == 10

    def test_replace_open_orders_for_session_clears_old(self, writer, db_with_schema):
        writer.upsert_open_order(1, {"a": 1})
        writer.upsert_open_order(2, {"a": 2})
        writer.upsert_open_order(3, {"a": 3})
        # Sync now returns only orders [4, 5]; old should be deleted.
        writer.replace_open_orders_for_session([
            (4, {"a": 4}),
            (5, {"a": 5}),
        ])
        perm_ids = sorted(
            r[0] for r in db_with_schema.execute("SELECT perm_id FROM open_orders").fetchall()
        )
        assert perm_ids == [4, 5]

    def test_replace_open_orders_with_empty_clears_table(self, writer, db_with_schema):
        writer.upsert_open_order(1, {"a": 1})
        writer.replace_open_orders_for_session([])
        rows = db_with_schema.execute("SELECT * FROM open_orders").fetchall()
        assert rows == []


class TestExecutedOrders:
    def test_upsert_with_perm_id(self, writer, db_with_schema):
        writer.upsert_executed_order(
            "exec-1",
            {"side": "BOT", "qty": 10, "avgPrice": 10.0},
            "2026-05-06T18:17:23+00:00",
            perm_id=9001,
        )
        rows = db_with_schema.execute(
            "SELECT exec_id, perm_id, fill_time FROM executed_orders"
        ).fetchall()
        assert rows[0] == ("exec-1", 9001, "2026-05-06T18:17:23+00:00")

    def test_upsert_without_perm_id_stores_null(self, writer, db_with_schema):
        writer.upsert_executed_order("exec-2", {"side": "SLD"}, "2026-05-06T18:00:00Z")
        rows = db_with_schema.execute(
            "SELECT perm_id FROM executed_orders WHERE exec_id = ?", ("exec-2",)
        ).fetchall()
        assert rows[0][0] is None

    def test_replace_on_same_exec_id(self, writer, db_with_schema):
        writer.upsert_executed_order("e1", {"qty": 5}, "2026-05-06T18:00:00Z")
        writer.upsert_executed_order("e1", {"qty": 10}, "2026-05-06T18:00:00Z")
        rows = db_with_schema.execute("SELECT payload FROM executed_orders").fetchall()
        assert len(rows) == 1
        assert json.loads(rows[0][0])["qty"] == 10

    def test_index_supports_fill_time_desc_order(self, writer, db_with_schema):
        writer.upsert_executed_order("a", {}, "2026-05-06T15:00:00Z")
        writer.upsert_executed_order("b", {}, "2026-05-06T18:00:00Z")
        writer.upsert_executed_order("c", {}, "2026-05-06T17:00:00Z")
        rows = db_with_schema.execute(
            "SELECT exec_id FROM executed_orders ORDER BY fill_time DESC"
        ).fetchall()
        assert [r[0] for r in rows] == ["b", "c", "a"]


# ── daemon_state (Phase 4) ───────────────────────────────────────────

class TestDaemonState:
    def test_inserts_new_handler(self, writer, db_with_schema):
        writer.upsert_daemon_state("fill_monitor", last_run=_now(), last_status="ok")
        rows = db_with_schema.execute(
            "SELECT handler, last_status FROM daemon_state"
        ).fetchall()
        assert rows == [("fill_monitor", "ok")]

    def test_partial_update_preserves_last_run(self, writer, db_with_schema):
        original_run = _now()
        writer.upsert_daemon_state("fill_monitor", last_run=original_run, last_status="ok")
        # Subsequent call without last_run should preserve the existing one
        writer.upsert_daemon_state("fill_monitor", last_status="error", last_error="boom")
        rows = db_with_schema.execute(
            "SELECT last_run, last_status, last_error FROM daemon_state"
        ).fetchall()
        assert rows[0] == (original_run, "error", "boom")

    def test_clears_last_error_on_explicit_none(self, writer, db_with_schema):
        writer.upsert_daemon_state("h", last_status="error", last_error="boom")
        writer.upsert_daemon_state("h", last_status="ok", last_error=None)
        rows = db_with_schema.execute(
            "SELECT last_status, last_error FROM daemon_state"
        ).fetchall()
        assert rows[0] == ("ok", None)


# ── app_config (Phase 4) ─────────────────────────────────────────────

class TestAppConfig:
    def test_set_and_get(self, writer):
        writer.upsert_app_config("flex_token_expires", "2026-08-15")
        assert writer.get_app_config("flex_token_expires") == "2026-08-15"

    def test_get_missing_returns_none(self, writer):
        assert writer.get_app_config("nonexistent") is None

    def test_replace_on_same_key(self, writer, db_with_schema):
        writer.upsert_app_config("k", "v1")
        writer.upsert_app_config("k", "v2")
        rows = db_with_schema.execute("SELECT value FROM app_config").fetchall()
        assert rows == [("v2",)]


# ── watchlist (Phase 4) ──────────────────────────────────────────────

class TestWatchlist:
    def test_uppercase_and_insert(self, writer, db_with_schema):
        writer.upsert_watchlist_ticker("aapl", sector="Technology", source="x_accounts")
        rows = db_with_schema.execute(
            "SELECT ticker, sector, source FROM watchlist"
        ).fetchall()
        assert rows == [("AAPL", "Technology", "x_accounts")]

    def test_partial_update_preserves_existing_fields(self, writer, db_with_schema):
        writer.upsert_watchlist_ticker("AAPL", sector="Technology", source="initial")
        # Update with sector=None should keep "Technology"
        writer.upsert_watchlist_ticker("AAPL", source="x_sync")
        rows = db_with_schema.execute(
            "SELECT sector, source FROM watchlist"
        ).fetchall()
        assert rows == [("Technology", "x_sync")]


# ── ticker_lookup_cache (Phase 4) ────────────────────────────────────

class TestTickerLookupCache:
    def test_upsert_normalizes_query(self, writer, db_with_schema):
        writer.upsert_ticker_lookup_cache("aapl", "AAPL,Apple Inc.,STK", "2026-05-08T00:00:00Z")
        rows = db_with_schema.execute(
            "SELECT query, result FROM ticker_lookup_cache"
        ).fetchall()
        assert rows == [("AAPL", "AAPL,Apple Inc.,STK")]


# ── reconciliation_log (Phase 4) ─────────────────────────────────────

class TestReconciliationLog:
    def test_inserts(self, writer, db_with_schema):
        writer.upsert_reconciliation_log("2026-05-06T20:00:00Z", {"diffs": []})
        rows = db_with_schema.execute(
            "SELECT snapshot_at, payload FROM reconciliation_log"
        ).fetchall()
        assert rows[0][0] == "2026-05-06T20:00:00Z"
        assert json.loads(rows[0][1]) == {"diffs": []}


class _AutocommitFlakyDb:
    """Direct-to-cloud faithful fake: every statement commits immediately
    (Hrana autocommits per request) and the Nth execute raises — the
    mid-replace transport failure shape."""

    def __init__(self, fail_on_execute: int = 10**9):
        self.conn = sqlite3.connect(":memory:")
        self.conn.isolation_level = None  # autocommit, like the HTTP pipeline
        self.conn.execute(
            """
            CREATE TABLE open_orders (
              perm_id     INTEGER PRIMARY KEY,
              payload     TEXT    NOT NULL,
              updated_at  TEXT    NOT NULL
            )
            """
        )
        self.fail_on_execute = fail_on_execute
        self.calls = 0

    def execute(self, sql, params=()):
        self.calls += 1
        if self.calls >= self.fail_on_execute:
            raise RuntimeError("hrana: upstream forward failed (502)")
        return self.conn.execute(sql, params)

    def commit(self):
        pass  # autocommit

    def perm_ids(self):
        return sorted(r[0] for r in self.conn.execute("SELECT perm_id FROM open_orders"))


class TestReplaceOpenOrdersAtomicity:
    """T-024: DELETE-all + N INSERTs over an autocommitting pipeline left
    /orders EMPTY (indistinguishable from a flat book) when any statement
    failed mid-replace — the operator can double-place against invisible
    working orders."""

    def _seed(self, db, ids=(1, 2, 3)):
        for pid in ids:
            db.conn.execute(
                "INSERT INTO open_orders (perm_id, payload, updated_at) VALUES (?, ?, ?)",
                (pid, json.dumps({"a": pid}), "2026-08-07T00:00:00Z"),
            )

    def _patched_writer(self, monkeypatch, db):
        import db.writer as writer_mod
        monkeypatch.setattr(writer_mod, "get_db", lambda: db)
        return writer_mod

    def test_failure_on_first_write_statement_keeps_prior_snapshot(self, monkeypatch):
        db = _AutocommitFlakyDb(fail_on_execute=1)
        self._seed(db)
        writer_mod = self._patched_writer(monkeypatch, db)

        with pytest.raises(RuntimeError):
            writer_mod.replace_open_orders_for_session([(4, {"a": 4}), (5, {"a": 5})])

        assert db.perm_ids() == [1, 2, 3], (
            "a mid-replace failure must never leave /orders empty or partial-"
            f"only; got {db.perm_ids()}"
        )

    def test_failure_mid_replace_never_leaves_the_table_empty(self, monkeypatch):
        db = _AutocommitFlakyDb(fail_on_execute=2)
        self._seed(db)
        writer_mod = self._patched_writer(monkeypatch, db)

        with pytest.raises(RuntimeError):
            writer_mod.replace_open_orders_for_session([(4, {"a": 4}), (5, {"a": 5})])

        survivors = db.perm_ids()
        assert survivors, "table went EMPTY mid-replace — invisible working orders"
        assert set(survivors) & {1, 2, 3, 4, 5}, f"unexpected content: {survivors}"

    def test_happy_path_replaces_and_updates_payloads(self, monkeypatch):
        db = _AutocommitFlakyDb()
        self._seed(db)
        writer_mod = self._patched_writer(monkeypatch, db)

        writer_mod.replace_open_orders_for_session([(2, {"a": "new2"}), (4, {"a": 4})])

        assert db.perm_ids() == [2, 4]
        payload2 = json.loads(
            db.conn.execute("SELECT payload FROM open_orders WHERE perm_id = 2").fetchone()[0]
        )
        assert payload2 == {"a": "new2"}

    def test_empty_snapshot_still_clears_the_table(self, monkeypatch):
        db = _AutocommitFlakyDb()
        self._seed(db)
        writer_mod = self._patched_writer(monkeypatch, db)

        writer_mod.replace_open_orders_for_session([])

        assert db.perm_ids() == []
