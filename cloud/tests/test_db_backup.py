"""Tests for scripts/db_backup.py pure logic (no Turso access)."""

import importlib.util
import io
import pathlib
import sqlite3

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "db_backup", ROOT / "scripts" / "db_backup.py"
    )
    module = importlib.util.module_from_spec(spec)
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
        entries = [("radon-edge.sql.gz", now - 30 * DAY + 60)]
        assert db_backup.select_prunable(entries, now) == []

    def test_never_touches_non_dump_files(self):
        now = 1_000_000 * DAY
        entries = [("README.md", now - 400 * DAY), ("dump.sql", now - 400 * DAY)]
        assert db_backup.select_prunable(entries, now) == []

    def test_respects_custom_retention(self):
        now = 1_000_000 * DAY
        entries = [("radon-x.sql.gz", now - 8 * DAY)]
        assert db_backup.select_prunable(entries, now, retention_days=7) == ["radon-x.sql.gz"]


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
