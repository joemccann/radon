"""Pure-function tests for scripts/db/migrate.py.

We avoid live Turso here — the heavy lifting is all in _split_statements
and _list_migrations. The end-to-end path is verified by running the
script twice on Hetzner during deploy (idempotency check).
"""
from __future__ import annotations

import os
import subprocess
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


class TestMigrateDemoTarget:
    """2026-08-26 P1: radon-demo-mirror wrote equibles_13f_snapshots /
    equibles_filing_forensics into a demo Turso still at schema_migrations
    max=26. Prod migrate.py only targets TURSO_DB_URL; nothing advanced the
    demo schema, so dest writes failed with 'no such table'."""

    def test_resolve_target_demo_uses_demo_env(self, migrate_module, monkeypatch):
        monkeypatch.setenv(
            "TURSO_DEMO_DB_URL", "libsql://radon-demo-joemccann.aws-us-west-2.turso.io"
        )
        monkeypatch.setenv("TURSO_DEMO_AUTH_TOKEN", "demo-tok")
        monkeypatch.setenv("TURSO_DB_URL", "libsql://radon-joemccann.aws-us-west-2.turso.io")
        monkeypatch.setenv("TURSO_AUTH_TOKEN", "prod-tok")
        url, token = migrate_module.resolve_target(demo=True)
        assert "radon-demo" in url
        assert token == "demo-tok"

    def test_resolve_target_demo_refuses_prod_marker(self, migrate_module, monkeypatch):
        monkeypatch.setenv(
            "TURSO_DEMO_DB_URL", "libsql://radon-joemccann.aws-us-west-2.turso.io"
        )
        monkeypatch.setenv("TURSO_DEMO_AUTH_TOKEN", "tok")
        with pytest.raises(SystemExit, match="REFUSING"):
            migrate_module.resolve_target(demo=True)

    def test_resolve_target_demo_requires_demo_marker(self, migrate_module, monkeypatch):
        monkeypatch.setenv(
            "TURSO_DEMO_DB_URL", "libsql://some-other-db.aws-us-west-2.turso.io"
        )
        monkeypatch.setenv("TURSO_DEMO_AUTH_TOKEN", "tok")
        with pytest.raises(SystemExit, match="radon-demo"):
            migrate_module.resolve_target(demo=True)

    def test_resolve_target_prod_default_unchanged(self, migrate_module, monkeypatch):
        monkeypatch.setenv("TURSO_DB_URL", "libsql://radon-joemccann.turso.io")
        monkeypatch.setenv("TURSO_AUTH_TOKEN", "prod-tok")
        url, token = migrate_module.resolve_target(demo=False)
        assert url.endswith("radon-joemccann.turso.io")
        assert token == "prod-tok"

    def test_apply_pending_creates_equibles_tables_from_real_migrations(
        self, migrate_module, monkeypatch, tmp_path
    ):
        """Reproduce the 2026-08-26 topology: demo at v26, pending includes
        0043/0045; after apply the mirrored tables must exist."""
        d = tmp_path / "migrations"
        d.mkdir()
        (d / "0043_equibles_smart_money_13f.sql").write_text(
            "CREATE TABLE IF NOT EXISTS equibles_13f_snapshots (\n"
            "  ticker TEXT NOT NULL, report_date TEXT NOT NULL,\n"
            "  scan_time TEXT NOT NULL, payload TEXT NOT NULL,\n"
            "  PRIMARY KEY (ticker, report_date)\n"
            ");\n"
            "INSERT OR IGNORE INTO schema_migrations (version, applied_at) "
            "VALUES (43, datetime('now'));\n"
        )
        (d / "0045_equibles_filing_forensics.sql").write_text(
            "CREATE TABLE IF NOT EXISTS equibles_filing_forensics (\n"
            "  ticker TEXT PRIMARY KEY, as_of TEXT NOT NULL,\n"
            "  flag_count INTEGER NOT NULL, data_complete INTEGER NOT NULL,\n"
            "  payload TEXT NOT NULL, recorded_at TEXT NOT NULL\n"
            ");\n"
            "INSERT OR IGNORE INTO schema_migrations (version, applied_at) "
            "VALUES (45, datetime('now'));\n"
        )
        monkeypatch.setattr(migrate_module, "MIGRATIONS_DIR", d)

        tables: set[str] = set()
        applied: set[int] = {26}

        class FakeDb:
            def execute(self, sql, params=None):
                text = " ".join(str(sql).split())
                if text.startswith("CREATE TABLE IF NOT EXISTS schema_migrations"):
                    return self
                if text.startswith("SELECT version FROM schema_migrations"):
                    self._rows = [(v,) for v in sorted(applied)]
                    return self
                if text.startswith("CREATE TABLE IF NOT EXISTS equibles_13f_snapshots"):
                    tables.add("equibles_13f_snapshots")
                    return self
                if text.startswith("CREATE TABLE IF NOT EXISTS equibles_filing_forensics"):
                    tables.add("equibles_filing_forensics")
                    return self
                if text.startswith("INSERT OR IGNORE INTO schema_migrations"):
                    version = int(params[0]) if params else None
                    if version is None:
                        # From the migration file's own INSERT ... VALUES (N, ...)
                        import re
                        m = re.search(r"VALUES\s*\((\d+)", text, re.I)
                        version = int(m.group(1)) if m else None
                    if version is not None:
                        applied.add(version)
                    return self
                return self

            def fetchall(self):
                return list(getattr(self, "_rows", []))

            def commit(self):
                return None

        db = FakeDb()
        # Match libsql cursor chaining: execute(...).fetchall()
        original_execute = db.execute

        def execute_and_chain(sql, params=None):
            original_execute(sql, params)
            return db

        db.execute = execute_and_chain  # type: ignore[method-assign]

        pending = migrate_module.apply_pending_migrations(db)
        assert pending == 2
        assert "equibles_13f_snapshots" in tables
        assert "equibles_filing_forensics" in tables
        assert 43 in applied and 45 in applied


class TestMigrateEntrypointArgv:
    """T-202: `main(argv=None)` deliberately parses `[]` so library callers and
    pytest's own sys.argv cannot leak into argparse. That makes the ENTRYPOINT
    the only thing that forwards `--demo`: revert `main(sys.argv[1:])` to a bare
    `main()` and `radon-demo-mirror`'s ExecStartPre silently migrates PROD, exits
    0, and the demo DB stays at its old schema_migrations max — the 2026-08-26 P1
    recurs behind a green preflight. Nothing tested that forwarding, so these two
    pin it: one on `main` itself, one on the real `__main__` entrypoint.
    """

    def test_main_with_demo_argv_targets_demo_db(self, migrate_module, monkeypatch):
        monkeypatch.setenv(
            "TURSO_DEMO_DB_URL", "libsql://radon-demo-joemccann.aws-us-west-2.turso.io"
        )
        monkeypatch.setenv("TURSO_DEMO_AUTH_TOKEN", "demo-tok")
        monkeypatch.setenv("TURSO_DB_URL", "libsql://radon-joemccann.aws-us-west-2.turso.io")
        monkeypatch.setenv("TURSO_AUTH_TOKEN", "prod-tok")

        connected: list[tuple[str, str]] = []

        def fake_connect(libsql, url, token):
            connected.append((url, token))
            return MagicMock()

        monkeypatch.setattr(migrate_module, "_connect_with_retry", fake_connect)
        monkeypatch.setattr(migrate_module, "apply_pending_migrations", lambda db: 0)
        monkeypatch.setitem(sys.modules, "libsql_experimental", MagicMock())

        migrate_module.main(["--demo"])

        assert len(connected) == 1
        url, token = connected[0]
        assert "radon-demo" in url, f"--demo did not reach argparse; connected to {url!r}"
        assert token == "demo-tok"

    def test_module_entrypoint_forwards_demo_flag(self, tmp_path):
        """Run the file the systemd unit runs. A `libsql_experimental` stub on
        PYTHONPATH records the URL instead of opening a socket, so no live DB is
        touched and no retry ladder is slept through."""
        repo_root = Path(__file__).resolve().parent.parent.parent
        recorded = tmp_path / "connected_url.txt"
        stub_dir = tmp_path / "stub"
        stub_dir.mkdir()
        (stub_dir / "libsql_experimental.py").write_text(
            "def connect(url, auth_token=None):\n"
            f"    open({str(recorded)!r}, 'w').write(url)\n"
            # Wording matters: `_is_transport_error` retries anything mentioning
            # a connection, which would sleep the 2/5/15 ladder here.
            "    raise RuntimeError('stub libsql: no live socket in tests')\n",
            encoding="utf-8",
        )

        env = dict(os.environ)
        env["PYTHONPATH"] = str(stub_dir)
        env["TURSO_DEMO_DB_URL"] = "libsql://radon-demo-joemccann.aws-us-west-2.turso.io"
        env["TURSO_DEMO_AUTH_TOKEN"] = "demo-tok"
        # Fake prod creds too: if the entrypoint drops `--demo`, it must fail
        # against this placeholder, never against the real production Turso.
        env["TURSO_DB_URL"] = "libsql://radon-joemccann-PLACEHOLDER.invalid"
        env["TURSO_AUTH_TOKEN"] = "prod-tok-placeholder"

        result = subprocess.run(
            [sys.executable, str(repo_root / "scripts" / "db" / "migrate.py"), "--demo"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(repo_root),
            timeout=120,
        )

        assert recorded.exists(), (
            "migrate.py never reached the connect step; "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        url = recorded.read_text(encoding="utf-8")
        assert "radon-demo" in url, (
            "`python migrate.py --demo` did not resolve the demo target — the "
            f"entrypoint dropped the flag and connected to {url!r}"
        )
        assert "target=demo" in result.stdout
