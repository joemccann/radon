"""Content tests for scripts/db/migrations/0012_host_metrics.sql (DUR-12).

migrate.py:_split_statements (and migrate.ts) split on /;\\s*$/m, so every
statement must terminate with a semicolon at end-of-line and contain no
interior end-of-line semicolons. The migration also gets applied manually to
prod ahead of the deploy (DUR-11 precedent), so every CREATE must be
IF NOT EXISTS-idempotent — a failed migration at radon-api ExecStartPre is a
failed deploy.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MIGRATION = _REPO_ROOT / "scripts" / "db" / "migrations" / "0012_host_metrics.sql"

EXPECTED_COLUMNS = (
    "taken_at",
    "cpu_pct",
    "mem_used_mb",
    "mem_avail_mb",
    "load1",
    "swap_used_mb",
    "loop_lag_ms",
    "units_json",
)


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


class TestMigration0012Content:
    def test_migration_file_exists_with_version_12(self):
        assert MIGRATION.is_file()
        assert MIGRATION.name.startswith("0012_")

    def test_creates_table_index_and_version_recorder(self, statements):
        assert sum("CREATE TABLE" in s for s in statements) == 1
        assert sum("CREATE INDEX" in s for s in statements) == 1
        joined = "\n".join(statements)
        assert "host_metrics" in joined

    def test_every_create_is_if_not_exists(self, statements):
        creates = [s for s in statements if s.startswith("CREATE")]
        assert creates, "no CREATE statements found"
        for s in creates:
            assert "IF NOT EXISTS" in s, f"not idempotent: {s[:80]}"

    def test_table_carries_every_sampler_column(self, statements):
        table = next(s for s in statements if "CREATE TABLE" in s)
        for column in EXPECTED_COLUMNS:
            assert column in table, f"missing column: {column}"

    def test_index_covers_taken_at(self, statements):
        index = next(s for s in statements if "CREATE INDEX" in s)
        assert "host_metrics" in index
        assert "taken_at" in index

    def test_statements_survive_the_semicolon_splitter(self, statements):
        """No statement may be split mid-body — each must be a complete,
        independently executable statement (libSQL executes one at a time)."""
        for s in statements:
            assert s.upper().startswith(("CREATE", "INSERT")), (
                f"fragment leaked from the splitter: {s[:80]}"
            )

    def test_records_schema_version_12(self, statements):
        recorder = next((s for s in statements if "schema_migrations" in s), None)
        assert recorder is not None
        assert "INSERT OR IGNORE" in recorder
        assert "12" in recorder
