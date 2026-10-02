"""REL-021b / R-047: standalone scan-snapshot migration records its version."""

import sqlite3
from pathlib import Path

from db.migrate import _split_statements


def test_standalone_scan_snapshot_migration_records_version_and_preserves_rows():
    migration = Path(__file__).parents[1] / "db/migrations/0026_scan_snapshots.sql"
    statements = _split_statements(migration.read_text())
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        for statement in statements:
            db.execute(statement)
        # No migrate.py bookkeeping step: the direct SQL application itself
        # must record completion, even if the external runner stops here.
        assert db.execute("SELECT version FROM schema_migrations").fetchall() == [(26,)]
        db.execute("INSERT INTO scan_snapshots VALUES ('scanner', '2026-10-02', '{}')")
        for statement in statements:
            db.execute(statement)
        assert db.execute("SELECT version FROM schema_migrations").fetchall() == [(26,)]
        assert db.execute("SELECT payload FROM scan_snapshots").fetchall() == [("{}",)]
