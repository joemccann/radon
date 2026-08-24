"""REL-063 / R-153 (P1) — a partially-applied ALTER TABLE bricks the API.

Migration 0050 is the only real `ALTER TABLE` in 54 migrations (0003's hit is
the comment warning against exactly this). The runner executes statements one
at a time and records the version only AFTER the loop, so a connection drop
or a kill between the committed `ADD COLUMN disk_pct` and the version row
leaves version 50 unrecorded. The next run re-applies 0050, gets
`duplicate column name: disk_pct`, and the `raise` aborts `main()` — so 0051
credit_spread, 0052 ivrank, 0053 iei_hyg and 0054 trin never apply.
`migrate.py` is `radon-api`'s `ExecStartPre`, so the API then fails to start
on every boot: a full control-plane outage plus four indicator writers
failing on missing tables, until someone repairs it by hand.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from db import migrate


class TestAlreadyAppliedStatements:
    @pytest.mark.parametrize(
        "message",
        [
            "duplicate column name: disk_pct",
            "table host_metrics already exists",
            "index idx_trin_ts already exists",
        ],
    )
    def test_an_already_applied_statement_is_not_fatal(self, message):
        assert migrate._is_already_applied(Exception(message)) is True

    @pytest.mark.parametrize(
        "message",
        [
            "no such table: host_metrics",
            "syntax error near ALTER",
            "SQLITE_BUSY: database is locked",
        ],
    )
    def test_a_real_failure_is_still_fatal(self, message):
        assert migrate._is_already_applied(Exception(message)) is False


class TestVersionIsRecordedWithTheStatements:
    def test_the_version_insert_shares_one_commit_with_the_ddl(self):
        """R-153: the runner committed the DDL, THEN recorded the version in a
        second commit. Anything that killed it in between made the migration
        un-replayable."""
        import inspect

        source = inspect.getsource(migrate.main)
        body = source[source.index("for version, name, path in pending"):]
        insert_at = body.index("INSERT OR IGNORE INTO schema_migrations")
        assert body[:insert_at].count("db.commit()") == 0, (
            "the DDL is still committed before the version row is even issued"
        )

    def test_a_partially_applied_migration_replays_and_records(self, monkeypatch, tmp_path):
        """The whole failure mode, end to end: 0050's ADD COLUMN landed but
        its version row did not, so the next boot must complete it and go on
        to apply 0051-0054 rather than aborting the runner."""
        applied_versions: list[int] = []
        executed: list[str] = []

        class _Cursor:
            def __init__(self, rows):
                self._rows = rows

            def fetchall(self):
                return self._rows

        class _Db:
            def execute(self, sql, args=()):
                if "SELECT version FROM schema_migrations" in sql:
                    return _Cursor([])
                if "INSERT OR IGNORE INTO schema_migrations" in sql:
                    applied_versions.append(args[0])
                    return _Cursor([])
                executed.append(sql)
                if "ADD COLUMN" in sql:
                    raise RuntimeError("duplicate column name: disk_pct")
                return _Cursor([])

            def commit(self):
                return None

        monkeypatch.setattr(migrate, "_connect_with_retry", lambda *a, **k: _Db())
        monkeypatch.setattr(
            migrate,
            "_list_migrations",
            lambda: [
                (50, "0050_alter.sql", _write(tmp_path, "0050", "ALTER TABLE t ADD COLUMN disk_pct REAL;")),
                (51, "0051_next.sql", _write(tmp_path, "0051", "CREATE TABLE credit_spread_history (a TEXT);")),
            ],
        )
        monkeypatch.setenv("TURSO_DB_URL", "libsql://x")
        monkeypatch.setenv("TURSO_AUTH_TOKEN", "t")
        monkeypatch.setitem(sys.modules, "libsql", type("L", (), {})())

        migrate.main()

        assert applied_versions == [50, 51], (
            "the runner aborted on the replayed ALTER, so every later "
            f"migration was skipped: {applied_versions}"
        )
        assert any("credit_spread_history" in stmt for stmt in executed)


def _write(tmp_path: Path, name: str, sql: str) -> Path:
    path = tmp_path / f"{name}.sql"
    path.write_text(sql, encoding="utf-8")
    return path
