"""Tests for scripts/db_backup.py pure logic (no Turso access)."""

import importlib.util
import io
import pathlib
import re
import sqlite3
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load_module():
    name = "db_backup"
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "scripts" / "db_backup.py"
    )
    module = importlib.util.module_from_spec(spec)
    # dataclasses require the module to be present in sys.modules during class
    # creation when loaded via spec_from_file_location.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


db_backup = _load_module()

DAY = 86400


class _FakeResult:
    def __init__(self, rows):
        self.rows = rows


class _FakeDb:
    """Adapter giving sqlite3 the ``.execute(...).rows`` shape of
    libsql_experimental.Connection (what get_db() returns in prod)."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        return _FakeResult(self._conn.execute(sql, params).fetchall())


class _LatePageFailureDb(_FakeDb):
    def execute(self, sql, params=()):
        if "rowid >" in sql and "LIMIT 1" in sql:
            raise RuntimeError("late page failure")
        return super().execute(sql, params)


class TestSqlLiteral:
    def test_none_is_null(self):
        assert db_backup.sql_literal(None) == "NULL"

    def test_integer(self):
        assert db_backup.sql_literal(42) == "42"

    def test_float_round_trips(self):
        text = db_backup.sql_literal(1.005)
        assert float(text) == 1.005

    def test_string_quotes_doubled(self):
        assert db_backup.sql_literal("it's") == "'it''s'"

    def test_string_with_newline_preserved(self):
        assert db_backup.sql_literal("a\nb") == "'a\nb'"

    def test_bytes_hex_blob(self):
        assert db_backup.sql_literal(b"\x00\xff") == "X'00ff'"


class TestBuildInsert:
    def test_quotes_table_and_joins_values(self):
        stmt = db_backup.build_insert("journal", (1, "AAPL", None))
        assert stmt == 'INSERT INTO "journal" VALUES (1,\'AAPL\',NULL);'


class TestInternalObjects:
    def test_sqlite_internal_tables_skipped(self):
        assert db_backup.is_internal_object("sqlite_sequence") is True
        assert db_backup.is_internal_object("sqlite_stat1") is True

    def test_libsql_internal_tables_skipped(self):
        assert db_backup.is_internal_object("libsql_wasm_func_table") is True

    def test_user_tables_kept(self):
        assert db_backup.is_internal_object("journal") is False
        assert db_backup.is_internal_object("service_health") is False


class TestSelectPrunable:
    def test_prunes_dumps_older_than_retention(self):
        now = 1_000_000 * DAY
        entries = [
            ("radon-old.sql.gz", now - 31 * DAY),
            ("radon-new.sql.gz", now - 1 * DAY),
        ]
        assert db_backup.select_prunable(entries, now) == ["radon-old.sql.gz"]

    def test_keeps_dump_inside_retention_boundary(self):
        now = 1_000_000 * DAY
        entries = [("radon-edge.sql.gz", now - db_backup.RETENTION_DAYS * DAY + 60)]
        assert db_backup.select_prunable(entries, now) == []

    def test_local_retention_is_the_operator_window(self):
        # 2026-08-29: 7 days on-box, B2 holds a year. Thirty days of ~570 MB
        # dumps were 13 G of the 75 G root fs the night it filled.
        assert db_backup.RETENTION_DAYS == 7
        assert db_backup.REMOTE_RETENTION_DAYS > db_backup.RETENTION_DAYS

    def test_never_touches_non_dump_files(self):
        now = 1_000_000 * DAY
        entries = [("README.md", now - 400 * DAY), ("dump.sql", now - 400 * DAY)]
        assert db_backup.select_prunable(entries, now) == []

    def test_respects_custom_retention(self):
        now = 1_000_000 * DAY
        entries = [("radon-x.sql.gz", now - 8 * DAY)]
        assert db_backup.select_prunable(entries, now, retention_days=7) == ["radon-x.sql.gz"]

    def test_offbox_names_gate_the_prune_when_given(self):
        # R-445: with a B2 config present, age alone never unlinks; the dump
        # must also be in the confirmed off-box set.
        now = 1_000_000 * DAY
        entries = [("radon-a.sql.gz", now - 9 * DAY), ("radon-b.sql.gz", now - 8 * DAY)]
        assert db_backup.select_prunable(entries, now, offbox={"radon-b.sql.gz"}) == [
            "radon-b.sql.gz"
        ]
        assert db_backup.select_prunable(entries, now, offbox=set()) == []
        assert db_backup.select_prunable(entries, now, offbox=None) == [
            "radon-a.sql.gz",
            "radon-b.sql.gz",
        ]


class TestRetentionTextMatchesTheWindow:
    def test_no_thirty_day_local_window_claims_remain(self):
        # 1cb81bc9 cut RETENTION_DAYS to 7; five docstrings and comments kept
        # describing a 30-day / 30-dump local window. R-445.
        source = (ROOT / "scripts" / "db_backup.py").read_text(encoding="utf-8")
        stale = [
            line.strip()
            for line in source.splitlines()
            if re.search(r"\b30[- ](day|dump)s?\b", line)
        ]
        assert stale == []


def _make_source_db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT,
            note TEXT,
            qty REAL,
            blob_col BLOB
        );
        CREATE TABLE service_health (service TEXT PRIMARY KEY, state TEXT);
        CREATE INDEX idx_journal_ticker ON journal(ticker);
        CREATE VIEW v_open AS SELECT ticker FROM journal WHERE qty > 0;
        """
    )
    conn.execute(
        "INSERT INTO journal (ticker, note, qty, blob_col) VALUES (?, ?, ?, ?)",
        ("AAPL", "it's a 'test'\nline2", 1.5, b"\x01\x02"),
    )
    conn.execute(
        "INSERT INTO journal (ticker, note, qty, blob_col) VALUES (?, ?, ?, ?)",
        ("MSFT", None, -2.25, None),
    )
    conn.execute(
        "INSERT INTO service_health VALUES ('db-backup', 'ok')"
    )
    conn.commit()
    return conn


class TestDumpRoundTrip:
    def test_round_trip_preserves_rows_and_schema(self):
        src = _make_source_db()
        out = io.StringIO()
        stats = db_backup.dump_database(_FakeDb(src), out)

        restored = sqlite3.connect(":memory:")
        restored.executescript(out.getvalue())

        assert restored.execute("SELECT COUNT(*) FROM journal").fetchone()[0] == 2
        assert restored.execute("SELECT COUNT(*) FROM service_health").fetchone()[0] == 1

        row = restored.execute(
            "SELECT ticker, note, qty, blob_col FROM journal WHERE id = 1"
        ).fetchone()
        assert row == ("AAPL", "it's a 'test'\nline2", 1.5, b"\x01\x02")

        objects = {
            (r[0], r[1])
            for r in restored.execute("SELECT type, name FROM sqlite_master").fetchall()
        }
        assert ("index", "idx_journal_ticker") in objects
        assert ("view", "v_open") in objects

        assert stats["tables"] == 2
        assert stats["rows"] == 3

    def test_round_trip_with_paging_forced(self):
        """batch_size=1 forces the LIMIT/OFFSET paging path across multiple
        pages; every row must appear exactly once."""
        src = _make_source_db()
        out = io.StringIO()
        stats = db_backup.dump_database(_FakeDb(src), out, batch_size=1)

        restored = sqlite3.connect(":memory:")
        restored.executescript(out.getvalue())
        assert restored.execute("SELECT COUNT(*) FROM journal").fetchone()[0] == 2
        assert stats == {"tables": 2, "rows": 3}

    def test_round_trip_with_cursor_shaped_db(self):
        """libsql_experimental.Connection.execute returns a sqlite3-style
        Cursor (``.fetchall()``, no ``.rows``) — the shape the first prod
        run failed on. A raw sqlite3 connection reproduces it exactly."""
        src = _make_source_db()
        out = io.StringIO()
        stats = db_backup.dump_database(src, out)  # no _FakeDb wrapper

        restored = sqlite3.connect(":memory:")
        restored.executescript(out.getvalue())
        assert restored.execute("SELECT COUNT(*) FROM journal").fetchone()[0] == 2
        assert stats == {"tables": 2, "rows": 3}

    def test_dump_never_emits_internal_tables(self):
        src = _make_source_db()  # AUTOINCREMENT creates sqlite_sequence
        out = io.StringIO()
        db_backup.dump_database(_FakeDb(src), out)
        text = out.getvalue()
        assert "CREATE TABLE sqlite_sequence" not in text
        assert 'INSERT INTO "sqlite_sequence"' not in text

    def test_production_fts_dump_round_trips_without_shadow_collisions(self):
        src = sqlite3.connect(":memory:")
        src.executescript(
            """
            CREATE TABLE knowledge (title TEXT, summary TEXT, content TEXT);
            CREATE VIRTUAL TABLE knowledge_fts USING fts5(title, summary, content);
            INSERT INTO knowledge VALUES ('A', 'B', 'market structure');
            INSERT INTO knowledge_fts(rowid, title, summary, content)
              SELECT rowid, title, summary, content FROM knowledge;
            """
        )
        src.commit()
        out = io.StringIO()
        db_backup.dump_database(src, out)
        text = out.getvalue()
        assert "CREATE TABLE 'knowledge_fts_data'" not in text
        assert "CREATE TABLE 'knowledge_fts_idx'" not in text

        restored = sqlite3.connect(":memory:")
        restored.executescript(text)
        assert restored.execute(
            "SELECT COUNT(*) FROM knowledge_fts WHERE knowledge_fts MATCH 'market'"
        ).fetchone()[0] == 1

    def test_late_page_failure_aborts_without_replaying_rows(self):
        src = _make_source_db()
        out = io.StringIO()
        with pytest.raises(RuntimeError, match="late page failure"):
            db_backup.dump_database(_LatePageFailureDb(src), out, batch_size=1)
        assert out.getvalue().count('INSERT INTO "journal"') == 1

    def test_dump_uses_source_transaction(self):
        src = _make_source_db()
        statements: list[str] = []

        class TrackingDb(_FakeDb):
            def execute(self, sql, params=()):
                statements.append(sql)
                return super().execute(sql, params)

        db_backup.dump_database(TrackingDb(src), io.StringIO(), batch_size=1)
        assert statements[0] == "BEGIN TRANSACTION"
        assert statements[-1] == "ROLLBACK"
