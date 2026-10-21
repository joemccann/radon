"""Real SQLite schema/arity/idempotence with the direct-cloud transport replaced."""
import sqlite3
from pathlib import Path
import json

import pytest
from db import writer
from test_high_low_line import sample

MIGRATION = Path(__file__).parents[1] / "db/migrations/0101_high_low_line.sql"


def database():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)")
    db.executescript(MIGRATION.read_text())
    db.executescript(MIGRATION.read_text())
    return db


def test_migration_fields_keys_and_desc_index():
    db = database()
    names = {row[1]: row for row in db.execute("PRAGMA table_info(high_low_line_history)")}
    assert set(names) == set(sample()["series"][0]) | {"interval", "recorded_at"}
    assert names["interval"][5] == 1 and names["date"][5] == 2
    assert db.execute("SELECT version FROM schema_migrations").fetchall() == [(101,)]
    info = db.execute("PRAGMA index_xinfo(idx_high_low_line_history_date)").fetchall()
    assert [(row[2], row[3]) for row in info[:2]] == [("interval", 0), ("date", 1)]
    db.close()


def test_bounded_batched_writer_exact_arity_and_idempotent_revisions(monkeypatch):
    db, data, calls = database(), sample(), []
    def execute(sql, args):
        calls.append((sql, args))
        db.execute(sql, args)
    monkeypatch.setattr(writer, "_hrana_execute", execute)
    monkeypatch.setattr(writer, "get_db", lambda: pytest.fail("unbounded sync database used"))
    monkeypatch.setattr(writer, "_PRICE_HISTORY_INSERT_CHUNK_ROWS", 17)
    writer.upsert_high_low_line_rows(data["series"], interval="daily", recorded_at=data["scan_time"])
    assert len(calls) == 4
    assert all(sql.count("?") == len(args) and len(args) <= 17 * 16 for sql, args in calls)
    writer.upsert_high_low_line_rows(data["weekly_series"], interval="weekly", recorded_at=data["scan_time"])
    assert db.execute("SELECT count(*) FROM high_low_line_history").fetchone()[0] == data["count"] + data["weekly_count"]
    revised = {**data["series"][0], "highs": .0001, "cumulative": 1234.0001}
    writer.upsert_high_low_line_rows([revised], interval="daily", recorded_at="revised")
    assert db.execute("SELECT highs, cumulative, recorded_at FROM high_low_line_history WHERE interval='daily' ORDER BY date LIMIT 1").fetchone() == (.0001, 1234.0001, "revised")
    writer.upsert_high_low_line_rows([], interval="daily")
    with pytest.raises(ValueError): writer.upsert_high_low_line_rows([], interval="monthly")
    db.close()


def test_bounded_snapshot_transport_keeps_retention_and_payload(monkeypatch):
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE scan_snapshots (service TEXT, scan_time TEXT, payload TEXT, PRIMARY KEY(service, scan_time))")
    monkeypatch.setattr(writer, "_hrana_execute", lambda sql, args: db.execute(sql, args))
    monkeypatch.setattr(writer, "get_db", lambda: pytest.fail("unbounded sync database used"))
    monkeypatch.setattr(writer, "SCAN_SNAPSHOT_KEEP", 2)
    for i in range(4): writer.upsert_scan_snapshot("high-low-line", str(i), {"count": i}, bounded=True)
    assert db.execute("SELECT scan_time FROM scan_snapshots ORDER BY scan_time").fetchall() == [("2",), ("3",)]
    assert json.loads(db.execute("SELECT payload FROM scan_snapshots WHERE scan_time='3'").fetchone()[0]) == {"count": 3}
    db.close()


def test_weekly_forming_date_advances_without_duplicate_week(monkeypatch):
    db = database()
    monkeypatch.setattr(writer, "_hrana_execute", lambda sql, args: db.execute(sql, args))
    row = {**sample()["weekly_series"][0], "period_start": "2026-10-19", "period_end": "2026-10-23"}
    for day, net, complete in (("2026-10-20", -12, False), ("2026-10-21", 9, False), ("2026-10-23", 44, True)):
        row.update(date=day, net=net, period_complete=complete, cumulative=100 + net)
        writer.upsert_high_low_line_rows([row], interval="weekly", recorded_at=day)
    assert db.execute("SELECT date, net, cumulative, period_complete, recorded_at FROM high_low_line_history").fetchall() == [("2026-10-23", 44, 144, 1, "2026-10-23")]
    db.close()


def test_writer_retains_no_production_under_pytest_guard(monkeypatch):
    import db.hrana_http as http
    monkeypatch.delenv("RADON_DB_TEST_WRITE_OK", raising=False)
    monkeypatch.setattr(http, "read_env", lambda: pytest.fail("Credentials must not be read in tests"))
    with pytest.raises(http.HranaHttpError, match="refusing a real Turso connection under pytest"):
        writer.upsert_high_low_line_rows(sample()["series"][:1], interval="daily")
