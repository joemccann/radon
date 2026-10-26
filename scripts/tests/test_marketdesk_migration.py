"""0102 creates the marketdesk_articles ledger and retires the dropbox-research health row."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from db.migrate import _split_statements  # noqa: E402

ROOT = Path(__file__).resolve().parents[1] / "db/migrations"
MIGRATION = ROOT / "0102_marketdesk_articles.sql"
COLUMNS = {
    "article_id", "post_id", "status", "title", "article_url", "published_at", "pdf_asset", "pdf_sha256",
    "dropbox_status", "dropbox_path", "dropbox_id", "dropbox_rev", "dropbox_content_hash", "attempts",
    "last_error", "first_seen_at", "updated_at",
}


@pytest.fixture
def connection():
    conn = sqlite3.connect(":memory:")
    conn.executescript((ROOT / "0001_init.sql").read_text())
    conn.executescript((ROOT / "0071_research_post_sources.sql").read_text())
    conn.execute("INSERT INTO service_health(service,state,updated_at) VALUES ('dropbox-research','ok','2026-10-01')")
    conn.execute("INSERT INTO service_health(service,state,updated_at) VALUES ('research-archive','ok','2026-10-01')")
    conn.executescript(MIGRATION.read_text())
    yield conn
    conn.close()


def _insert(conn, **overrides):
    row = {"article_id": "4AJs5Adl2Yp", "article_url": "https://marketdesk.ai/library/browse/4AJs5Adl2Yp",
           "published_at": "2026-10-26T13:00:00+00:00", "first_seen_at": "2026-10-26T13:05:00+00:00",
           "updated_at": "2026-10-26T13:05:00+00:00"}
    row.update(overrides)
    conn.execute(f"INSERT INTO marketdesk_articles ({','.join(row)}) VALUES ({','.join('?' * len(row))})",
                 tuple(row.values()))


def test_file_stamps_version_99_and_has_no_inline_comments():
    sql = MIGRATION.read_text()
    assert "CREATE TABLE IF NOT EXISTS marketdesk_articles" in sql
    assert "VALUES (102," in sql
    assert "—" not in sql
    # migrate.py strips only whole-line comments before splitting on ';' at end of line.
    for line in sql.splitlines():
        if "--" in line:
            assert line.strip().startswith("--"), line


def test_runner_split_yields_complete_statements():
    statements = _split_statements(MIGRATION.read_text())
    assert any(s.lstrip().startswith("CREATE TABLE IF NOT EXISTS marketdesk_articles") for s in statements)
    assert any("DELETE FROM service_health" in s for s in statements)
    conn = sqlite3.connect(":memory:")
    conn.executescript((ROOT / "0001_init.sql").read_text())
    for statement in statements:
        conn.execute(statement)
    assert {r[1] for r in conn.execute("PRAGMA table_info(marketdesk_articles)")} == COLUMNS


def test_columns_defaults_and_version(connection):
    assert {r[1] for r in connection.execute("PRAGMA table_info(marketdesk_articles)")} == COLUMNS
    _insert(connection)
    row = connection.execute("SELECT status, attempts, title, dropbox_status FROM marketdesk_articles").fetchone()
    assert row == ("pending", 0, "", None)
    assert 102 in {r[0] for r in connection.execute("SELECT version FROM schema_migrations")}
    index = {r[1] for r in connection.execute("PRAGMA index_list(marketdesk_articles)")}
    assert "idx_marketdesk_articles_upload" in index


def test_retired_health_row_deleted_others_kept(connection):
    services = {r[0] for r in connection.execute("SELECT service FROM service_health")}
    assert "dropbox-research" not in services
    assert "research-archive" in services


@pytest.mark.parametrize("column,value", [("status", "done"), ("dropbox_status", "uploading")])
def test_check_constraints_reject_bad_status(connection, column, value):
    with pytest.raises(sqlite3.IntegrityError):
        _insert(connection, **{column: value})


@pytest.mark.parametrize("value", ["uploaded", "skipped_no_creds", "failed"])
def test_dropbox_statuses_accepted(connection, value):
    _insert(connection, dropbox_status=value)


def test_article_id_is_primary_key(connection):
    _insert(connection)
    with pytest.raises(sqlite3.IntegrityError):
        _insert(connection)


def test_reapply_is_idempotent(connection):
    connection.executescript(MIGRATION.read_text())
