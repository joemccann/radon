"""Pure-function tests for scripts/db/migrate.py.

We avoid live Turso here — the heavy lifting is all in _split_statements
and _list_migrations. The end-to-end path is verified by running the
script twice on Hetzner during deploy (idempotency check).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def migrate_module(monkeypatch: pytest.MonkeyPatch):
    """Import the module fresh per test (it has module-level dotenv calls)."""
    monkeypatch.setenv("RADON_DB_NO_REPLICA", "1")
    repo_root = Path(__file__).resolve().parent.parent.parent
    scripts_dir = repo_root / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import importlib
    import db.migrate as m  # type: ignore[import-not-found]
    importlib.reload(m)
    return m


class TestSplitStatements:
    def test_empty_string_returns_empty_list(self, migrate_module):
        assert migrate_module._split_statements("") == []

    def test_single_statement(self, migrate_module):
        sql = "CREATE TABLE foo (id INTEGER);"
        assert migrate_module._split_statements(sql) == ["CREATE TABLE foo (id INTEGER)"]

    def test_multiple_statements(self, migrate_module):
        sql = "CREATE TABLE foo (id INTEGER);\nCREATE INDEX foo_idx ON foo(id);"
        result = migrate_module._split_statements(sql)
        assert result == [
            "CREATE TABLE foo (id INTEGER)",
            "CREATE INDEX foo_idx ON foo(id)",
        ]

    def test_strips_line_comments(self, migrate_module):
        sql = "-- top comment\nCREATE TABLE foo (id INTEGER);\n-- mid\nINSERT INTO foo VALUES (1);"
        result = migrate_module._split_statements(sql)
        assert len(result) == 2
        assert "CREATE TABLE foo" in result[0]
        assert "INSERT INTO foo" in result[1]
        assert "comment" not in " ".join(result)

    def test_preserves_inline_dashes_inside_string_literals(self, migrate_module):
        # We strip *line-leading* comments; a `--` mid-statement should survive
        sql = "INSERT INTO t VALUES ('foo--bar');"
        result = migrate_module._split_statements(sql)
        assert "foo--bar" in result[0]

    def test_handles_trailing_whitespace_after_semicolon(self, migrate_module):
        sql = "CREATE TABLE foo (id INTEGER);   \n\n"
        assert migrate_module._split_statements(sql) == ["CREATE TABLE foo (id INTEGER)"]


class TestListMigrations:
    def test_returns_only_numbered_sql_files_in_order(self, migrate_module, monkeypatch, tmp_path):
        d = tmp_path / "migrations"
        d.mkdir()
        (d / "0002_b.sql").write_text("--")
        (d / "0001_a.sql").write_text("--")
        (d / "0010_j.sql").write_text("--")
        (d / "README.md").write_text("--")
        (d / "weird.sql").write_text("--")  # no leading number — skipped
        monkeypatch.setattr(migrate_module, "MIGRATIONS_DIR", d)
        rows = migrate_module._list_migrations()
        assert [r[0] for r in rows] == [1, 2, 10]

    def test_exits_with_error_when_directory_missing(self, migrate_module, monkeypatch, tmp_path):
        monkeypatch.setattr(migrate_module, "MIGRATIONS_DIR", tmp_path / "does-not-exist")
        with pytest.raises(SystemExit):
            migrate_module._list_migrations()

    def test_real_migrations_directory_lists_at_least_two(self, migrate_module):
        # Sanity check against the real on-disk migrations.
        rows = migrate_module._list_migrations()
        # At Phase 0 we have 0001_init + 0002_cash_flows; future phases
        # extend this list. Assert >= 2 (won't break as we add migrations).
        assert len(rows) >= 2
        # Must be sorted
        versions = [r[0] for r in rows]
        assert versions == sorted(versions)


DNS_BLIP = ValueError(
    "Hrana: dns error: failed to lookup address information: Try again"
)
SQL_SYNTAX_ERROR = ValueError('near "CREATEX": syntax error')


@pytest.fixture
def recorded_sleeps(migrate_module, monkeypatch):
    """Capture backoff sleeps instead of actually waiting."""
    sleeps: list[float] = []
    monkeypatch.setattr(migrate_module.time, "sleep", sleeps.append)
    return sleeps


class TestIsTransportError:
    @pytest.mark.parametrize("message", [
        "Hrana: dns error: failed to lookup address information",
        "Hrana: stream closed",
        "connection refused",
        "request timed out",
        "operation timeout",
    ])
    def test_transport_class_messages_match(self, migrate_module, message):
        assert migrate_module._is_transport_error(ValueError(message)) is True

    @pytest.mark.parametrize("message", [
        'near "CREATEX": syntax error',
        "no such table: schema_migrations_typo",
        "UNIQUE constraint failed: schema_migrations.version",
    ])
    def test_sql_and_schema_errors_do_not_match(self, migrate_module, message):
        assert migrate_module._is_transport_error(ValueError(message)) is False


class TestConnectWithRetry:
    """The 2026-06-12 incident: a transient Turso DNS blip raised
    ValueError("Hrana: dns error") from libsql.connect at radon-api
    ExecStartPre and hard-failed startup with no retry."""

    def test_transient_dns_error_then_success_succeeds_with_backoff(
        self, migrate_module, recorded_sleeps
    ):
        good_db = MagicMock(name="db")
        libsql = MagicMock()
        libsql.connect.side_effect = [DNS_BLIP, good_db]

        db = migrate_module._connect_with_retry(libsql, "libsql://x", "tok")

        assert db is good_db
        assert recorded_sleeps == [2]
        assert libsql.connect.call_count == 2

    def test_transient_error_on_bootstrap_execute_is_retried(
        self, migrate_module, recorded_sleeps
    ):
        bad_db = MagicMock(name="bad_db")
        bad_db.execute.side_effect = DNS_BLIP
        good_db = MagicMock(name="good_db")
        libsql = MagicMock()
        libsql.connect.side_effect = [bad_db, good_db]

        db = migrate_module._connect_with_retry(libsql, "libsql://x", "tok")

        assert db is good_db
        assert recorded_sleeps == [2]
        good_db.execute.assert_called_once()
        good_db.commit.assert_called_once()

    def test_non_transport_error_fails_immediately_without_retry(
        self, migrate_module, recorded_sleeps
    ):
        bad_db = MagicMock(name="bad_db")
        bad_db.execute.side_effect = SQL_SYNTAX_ERROR
        libsql = MagicMock()
        libsql.connect.return_value = bad_db

        with pytest.raises(ValueError, match="syntax error"):
            migrate_module._connect_with_retry(libsql, "libsql://x", "tok")

        assert recorded_sleeps == []
        assert libsql.connect.call_count == 1

    def test_exhausted_retries_raise_after_full_backoff_ladder(
        self, migrate_module, recorded_sleeps
    ):
        libsql = MagicMock()
        libsql.connect.side_effect = DNS_BLIP

        with pytest.raises(ValueError, match="dns error"):
            migrate_module._connect_with_retry(libsql, "libsql://x", "tok")

        assert recorded_sleeps == [2, 5, 15]
        assert libsql.connect.call_count == 4

    def test_backoff_ladder_is_2_5_15(self, migrate_module):
        assert tuple(migrate_module.RETRY_BACKOFF_SECONDS) == (2, 5, 15)
