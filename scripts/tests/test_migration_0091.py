"""0091 adds held_at / expired_at and backfills from updated_at."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from db import migrate

ROOT = Path(__file__).resolve().parents[1] / "db/migrations"


def _schema(connection):
    connection.execute(
        "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )


def _apply(connection, name):
    connection.executescript((ROOT / name).read_text())


def _columns(connection):
    return {row[1] for row in connection.execute("PRAGMA table_info(research_outcomes)")}


def _seed_legacy(connection):
    cols = ("work_key,file_id,file_name,publisher,series,doc_type,folder_date,document_date,"
            "outcome,reason_codes,drafts_json,posts,pipeline,updated_at")
    rows = [
        ("held-1", "id:h", "h.pdf", "GS", "x", "research", "2026-09-28", "2026-09-28",
         "held", '["NO_CANDIDATES"]', "[]", 0, "v2", "2026-09-28T18:00:00+00:00"),
        ("exp-1", "id:e", "e.pdf", "GS", "x", "research", "2026-09-24", "2026-09-24",
         "dropped", '["NO_CANDIDATES","HELD_EXPIRED"]', "[]", 0, "v2", "2026-09-28T12:00:00+00:00"),
        ("dup-1", "id:d", "d.pdf", "GS", "x", "research", "2026-09-27", "2026-09-27",
         "dropped", '["DUPLICATE_OF_PUBLISHED"]', "[]", 0, "v2", "2026-09-28T11:00:00+00:00"),
        ("pub-1", "id:p", "p.pdf", "GS", "x", "research", "2026-09-28", "2026-09-28",
         "published", "[]", "[]", 2, "v2", "2026-09-28T10:00:00+00:00"),
    ]
    connection.executemany(f"INSERT INTO research_outcomes ({cols}) VALUES ({','.join('?' * 14)})", rows)


def test_migration_adds_nullable_hold_columns_on_a_fresh_sqlite():
    connection = sqlite3.connect(":memory:")
    _schema(connection)
    _apply(connection, "0079_research_outcomes.sql")
    _apply(connection, "0082_research_outcomes_context.sql")
    _apply(connection, "0091_research_outcomes_hold_times.sql")
    cols = _columns(connection)
    assert "held_at" in cols and "expired_at" in cols
    info = {row[1]: row for row in connection.execute("PRAGMA table_info(research_outcomes)")}
    assert info["held_at"][3] == 0 and info["expired_at"][3] == 0  # not NOT NULL
    versions = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
    assert 91 in versions
    connection.close()


def test_backfill_sets_expired_at_on_held_expired_and_held_at_on_held():
    connection = sqlite3.connect(":memory:")
    _schema(connection)
    _apply(connection, "0079_research_outcomes.sql")
    _apply(connection, "0082_research_outcomes_context.sql")
    _seed_legacy(connection)
    _apply(connection, "0091_research_outcomes_hold_times.sql")
    rows = {row[0]: row[1:] for row in connection.execute(
        "SELECT work_key, outcome, held_at, expired_at, updated_at FROM research_outcomes"
    )}
    assert rows["held-1"] == ("held", "2026-09-28T18:00:00+00:00", None, "2026-09-28T18:00:00+00:00")
    assert rows["exp-1"] == ("dropped", None, "2026-09-28T12:00:00+00:00", "2026-09-28T12:00:00+00:00")
    assert rows["dup-1"] == ("dropped", None, None, "2026-09-28T11:00:00+00:00")
    assert rows["pub-1"] == ("published", None, None, "2026-09-28T10:00:00+00:00")
    connection.close()


def test_replay_of_add_column_is_not_fatal():
    connection = sqlite3.connect(":memory:")
    _schema(connection)
    _apply(connection, "0079_research_outcomes.sql")
    _apply(connection, "0091_research_outcomes_hold_times.sql")
    sql = (ROOT / "0091_research_outcomes_hold_times.sql").read_text()
    for stmt in migrate._split_statements(sql):
        migrate._execute_statement(connection, "0091_research_outcomes_hold_times.sql", stmt)
    assert "held_at" in _columns(connection)
    connection.close()
