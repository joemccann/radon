"""Tests for scripts/db_backup.py pure logic (no Turso access)."""

import importlib.util
import io
import pathlib
import re
import sqlite3
import sys
import time
import types

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


class TestRel185LocalRetentionValve:
    """REL-185 (R-517): a sustained B2 outage must not grow the local dump
    dir without bound — a hard count valve prunes the oldest over the cap
    even when nothing is off-box-confirmed, and says so distinctly."""

    def _aged_entries(self, n: int) -> list[tuple[str, float]]:
        now = time.time()
        return [
            (f"radon-{i:03d}.sql.gz", now - (100 - i) * 86_400)
            for i in range(n)
        ]

    def test_unconfirmed_dumps_over_the_cap_are_pruned_oldest_first(self):
        entries = self._aged_entries(100)
        now = time.time()
        # Nothing confirmed: the age-based prune keeps everything (R-445)...
        assert db_backup.select_prunable(entries, now, offbox=set()) == []
        # ...but the valve bounds the count.
        valve = db_backup.select_hard_valve(entries)
        assert len(valve) == 100 - db_backup.LOCAL_DUMP_HARD_CAP
        assert valve[0] == "radon-000.sql.gz"  # oldest first
        kept = {name for name, _ in entries} - set(valve)
        assert f"radon-099.sql.gz" in kept  # newest always kept

    def test_under_the_cap_the_valve_is_inert(self):
        entries = self._aged_entries(db_backup.LOCAL_DUMP_HARD_CAP)
        assert db_backup.select_hard_valve(entries) == []

    def test_non_dump_files_never_counted_or_pruned(self):
        entries = self._aged_entries(5) + [("stray.txt", 0.0)] * 40
        assert db_backup.select_hard_valve(entries) == []


_STREAM_NOT_FOUND = (
    'Hrana: `api error: `status=404 Not Found, '
    'body={"error":"stream not found: b4866f02:2ef17"}`'
)


class _StreamNotFound(ValueError):
    def __init__(self):
        super().__init__(_STREAM_NOT_FOUND)


class _DeadStreamDb(_FakeDb):
    """The production libsql handle: once the server drops the Hrana stream,
    every later page on THIS object 404s. Recovery has to be a new object."""

    def __init__(self, conn):
        super().__init__(conn)
        self.pages = 0

    def execute(self, sql, params=()):
        if "rowid >" in sql:
            self.pages += 1
            raise _StreamNotFound()
        return super().execute(sql, params)


class _OnePageThenDeadDb(_FakeDb):
    """Serves a single keyset page, then the stream 404s. A fresh instance
    starts that budget over, which is what a new libsql connection does."""

    def __init__(self, conn):
        super().__init__(conn)
        self.pages = 0

    def execute(self, sql, params=()):
        if "rowid >" in sql:
            self.pages += 1
            if self.pages > 1:
                raise _StreamNotFound()
        return super().execute(sql, params)


def _reopen_factory(conn, cls, opened: list):
    def reopen():
        fresh = cls(conn)
        opened.append(fresh)
        return fresh

    return reopen


class TestTransientHranaStreamRetry:
    """Page 4076d7d9: radon-db-backup.service held one Hrana stream for the
    whole dump. ~10 min in, the next page of ai_cycle_observations raised
    ValueError `stream not found` and the oneshot exited 1. The rows already
    written must not be replayed, and the dead singleton must not be reused."""

    def test_stream_not_found_mid_table_resumes_on_a_fresh_connection(self):
        src = _make_source_db()
        opened: list = []
        out = io.StringIO()
        stats = db_backup.dump_database(
            _DeadStreamDb(src),
            out,
            batch_size=1,
            reconnect=_reopen_factory(src, _FakeDb, opened),
            sleep=lambda _delay: None,
        )

        assert opened, "retry reused the dead stream instead of opening a fresh one"
        assert stats == {"tables": 2, "rows": 3}
        assert out.getvalue().count('INSERT INTO "journal"') == 2

    def test_later_pages_get_a_fresh_attempt_budget(self):
        src = _make_source_db()
        src.execute("INSERT INTO journal (ticker, note, qty) VALUES ('NVDA', 'x', 1)")
        src.execute("INSERT INTO journal (ticker, note, qty) VALUES ('AMD', 'y', 1)")
        src.commit()
        opened: list = []
        out = io.StringIO()
        stats = db_backup.dump_database(
            _OnePageThenDeadDb(src),
            out,
            batch_size=1,
            reconnect=_reopen_factory(src, _OnePageThenDeadDb, opened),
            sleep=lambda _delay: None,
        )

        assert stats["rows"] == 5
        assert out.getvalue().count('INSERT INTO "journal"') == 4
        assert len(opened) >= 2

    def test_persistent_stream_not_found_still_fails_without_replaying_rows(self):
        src = _make_source_db()
        opened: list = []
        out = io.StringIO()
        with pytest.raises(ValueError, match="stream not found"):
            db_backup.dump_database(
                _DeadStreamDb(src),
                out,
                batch_size=1,
                reconnect=_reopen_factory(src, _DeadStreamDb, opened),
                sleep=lambda _delay: None,
            )
        assert len(opened) == db_backup.DB_STREAM_ATTEMPTS - 1
        assert out.getvalue().count('INSERT INTO "journal"') == 1

    def test_non_transient_page_error_is_not_retried(self):
        src = _make_source_db()
        opened: list = []
        out = io.StringIO()
        with pytest.raises(RuntimeError, match="late page failure"):
            db_backup.dump_database(
                _LatePageFailureDb(src),
                out,
                batch_size=1,
                reconnect=_reopen_factory(src, _FakeDb, opened),
                sleep=lambda _delay: None,
            )
        assert opened == []
        assert out.getvalue().count('INSERT INTO "journal"') == 1

    def test_run_backup_resumes_through_reopen_cloud_db(self, monkeypatch, tmp_path):
        monkeypatch.setattr(db_backup, "BACKUP_DIR", tmp_path)
        for key in (
            "RADON_ARCHIVE_S3_ENDPOINT",
            "RADON_ARCHIVE_S3_BUCKET",
            "RADON_ARCHIVE_S3_ACCESS_KEY_ID",
            "RADON_ARCHIVE_S3_SECRET_ACCESS_KEY",
            "RADON_DB_BACKUP_S3_ENDPOINT",
            "RADON_DB_BACKUP_S3_BUCKET",
            "RADON_DB_BACKUP_S3_ACCESS_KEY_ID",
            "RADON_DB_BACKUP_S3_SECRET_ACCESS_KEY",
        ):
            monkeypatch.delenv(key, raising=False)
        src = _make_source_db()
        opened: list = []

        class _DieOnServiceHealth(_FakeDb):
            def execute(self, sql, params=()):
                if 'FROM "service_health"' in sql:
                    raise _StreamNotFound()
                return super().execute(sql, params)

        monkeypatch.setattr(db_backup, "_open_cloud_db", lambda: _DieOnServiceHealth(src))
        monkeypatch.setattr(
            db_backup, "_reopen_cloud_db", _reopen_factory(src, _FakeDb, opened)
        )

        detail = db_backup.run_backup()

        assert opened, "run_backup retried on the dead singleton"
        assert detail["rows"] == 3
        dumps = list(tmp_path.glob("*.sql.gz"))
        assert len(dumps) == 1


def test_reopen_cloud_db_resets_the_singleton_before_connecting(monkeypatch):
    order: list[str] = []
    sentinel = object()
    fake = types.ModuleType("scripts.db.client")

    def reset_connection():
        order.append("reset")

    def get_db():
        order.append("get")
        return sentinel

    fake.reset_connection = reset_connection
    fake.get_db = get_db
    monkeypatch.setitem(sys.modules, "scripts.db.client", fake)

    assert db_backup._reopen_cloud_db() is sentinel
    assert order == ["reset", "get"]


def test_vector_indexes_are_not_portable_dump_objects():
    """libsql_vector_idx is recreated by migrations, including embedding_v2."""
    kept = "CREATE INDEX idx_journal_ticker ON journal(ticker)"
    v1 = "CREATE INDEX idx_knowledge_embedding ON knowledge(libsql_vector_idx(embedding))"
    v2 = "CREATE INDEX idx_knowledge_embedding_v2 ON knowledge(libsql_vector_idx(embedding_v2))"
    assert db_backup._is_portable_object(kept) is True
    assert db_backup._is_portable_object(v1) is False
    assert db_backup._is_portable_object(v2) is False
