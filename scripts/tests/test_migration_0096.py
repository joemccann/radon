"""0096 creates newsfeed_explanations keyed by post_id + content_hash."""
from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "db/migrations"
MIGRATION = ROOT / "0096_newsfeed_explanations.sql"


def test_migration_file_stamps_version_96():
    sql = MIGRATION.read_text()
    assert "CREATE TABLE IF NOT EXISTS newsfeed_explanations" in sql
    assert "PRIMARY KEY (post_id, content_hash)" in sql
    assert "INSERT OR IGNORE INTO schema_migrations" in sql
    assert "VALUES (96," in sql
    assert "em dash" not in sql.lower()


def test_migration_applies_on_fresh_sqlite():
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    connection.executescript(MIGRATION.read_text())
    cols = {row[1] for row in connection.execute("PRAGMA table_info(newsfeed_explanations)")}
    assert cols == {
        "post_id",
        "content_hash",
        "explanation",
        "model",
        "provider",
        "has_image",
        "created_at",
    }
    versions = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
    assert 96 in versions
    connection.execute(
        "INSERT INTO newsfeed_explanations "
        "(post_id, content_hash, explanation, model, provider, has_image, created_at) "
        "VALUES ('p1', 'abc', 'plain English', 'claude-opus-5', 'anthropic', 0, '2026-10-18T00:00:00Z')"
    )
    row = connection.execute(
        "SELECT explanation, has_image FROM newsfeed_explanations WHERE post_id = 'p1'"
    ).fetchone()
    assert row == ("plain English", 0)
    connection.close()
