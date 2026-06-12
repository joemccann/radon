"""Content tests for scripts/db/migrations/0011_service_health_events.sql (DUR-11).

The migration ships triggers, and trigger bodies contain interior ``;``
characters — migrate.py/_split_statements (and migrate.ts) split on /;\\s*$/m,
so a trigger formatted with ``INSERT ...;`` at end-of-line would be split
mid-body and fail at radon-api ExecStartPre (= failed deploy). These tests pin
the load-bearing formatting and the idempotency contract (the migration is
also applied manually to prod ahead of the deploy, so every statement must be
IF NOT EXISTS).

Live trigger semantics were verified against production Turso on 2026-06-12
via a throwaway _dur11_smoke table (AFTER INSERT + AFTER UPDATE with compound
WHEN both supported; ok->ok heartbeat upserts suppressed; nothing left behind).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MIGRATION = _REPO_ROOT / "scripts" / "db" / "migrations" / "0011_service_health_events.sql"


@pytest.fixture
def split_statements(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RADON_DB_NO_REPLICA", "1")
    scripts_dir = _REPO_ROOT / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import db.migrate as m  # type: ignore[import-not-found]
    return m._split_statements


@pytest.fixture
def statements(split_statements):
    return split_statements(MIGRATION.read_text(encoding="utf-8"))


class TestMigration0011Content:
    def test_migration_file_exists_with_version_11(self):
        assert MIGRATION.is_file()
        assert MIGRATION.name.startswith("0011_")

    def test_creates_events_table_index_and_two_triggers(self, statements):
        kinds = [s.split()[0:3] for s in statements]
        joined = "\n".join(statements)
        assert sum("CREATE TABLE" in s for s in statements) == 1
        assert sum("CREATE INDEX" in s for s in statements) == 1
        assert sum("CREATE TRIGGER" in s for s in statements) == 2
        assert "service_health_events" in joined
        assert "AFTER INSERT ON service_health" in joined
        assert "AFTER UPDATE ON service_health" in joined

    def test_every_create_is_if_not_exists(self, statements):
        creates = [s for s in statements if s.startswith("CREATE")]
        assert creates, "no CREATE statements found"
        for s in creates:
            assert "IF NOT EXISTS" in s, f"not idempotent: {s[:80]}"

    def test_trigger_statements_survive_the_semicolon_splitter(self, statements):
        """Each trigger must come out of _split_statements as ONE statement
        with a balanced BEGIN/END — i.e. no interior ``;`` at end-of-line."""
        triggers = [s for s in statements if "CREATE TRIGGER" in s]
        assert len(triggers) == 2
        for t in triggers:
            assert "BEGIN" in t and "END" in t, f"trigger split mid-body: {t[:120]}"
            assert "INSERT INTO service_health_events" in t

    def test_update_trigger_is_transition_gated_with_deploy_passthrough(self, statements):
        update_trigger = next(s for s in statements if "AFTER UPDATE" in s)
        assert "WHEN" in update_trigger
        assert "OLD.state != NEW.state" in update_trigger
        # Deploy markers re-upsert with state='ok' every time; without this arm
        # only the first deploy would ever land in history.
        assert "NEW.service = 'deploy'" in update_trigger

    def test_index_covers_service_and_created_at(self, statements):
        index = next(s for s in statements if "CREATE INDEX" in s)
        assert "service_health_events" in index
        assert "service" in index and "created_at" in index

    def test_records_schema_version_11(self, statements):
        recorder = next((s for s in statements if "schema_migrations" in s), None)
        assert recorder is not None
        assert "INSERT OR IGNORE" in recorder
        assert "11" in recorder
