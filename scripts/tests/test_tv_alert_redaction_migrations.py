"""TradingView alert redaction migrations 0084-0086, executed against SQLite.

Each migration must strip the webhook secret from stored raw bodies, keep the
parsed columns, and be idempotent.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent


class TestTradingViewRedactionMigrations:
    """Migrations 0084-0086 run against an offline SQLite copy. No other test
    executes them."""

    def test_migration_0084_redacts_top_level_secret_idempotently(self):
        with sqlite3.connect(":memory:") as db:
            db.executescript("CREATE TABLE tv_alert_events (raw_body TEXT, symbol TEXT);"
                             "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT);")
            db.executemany("INSERT INTO tv_alert_events VALUES (?, ?)", [
                ('{"secret":"fixture-only","symbol":"TEST"}', "TEST"),
                ('secret=fixture-only invalid json', "TEST"),
            ])
            migration = (_ROOT / "scripts/db/migrations/0084_redact_tv_alert_raw_body.sql").read_text()
            db.executescript(migration)
            rows = db.execute("SELECT raw_body, symbol FROM tv_alert_events").fetchall()
            assert all("fixture-only" not in raw and symbol == "TEST" for raw, symbol in rows)
            assert json.loads(rows[0][0])["symbol"] == "TEST"
            assert rows[1][0].startswith("[REDACTED PRE-0084")
            db.executescript(migration)
            assert db.execute("SELECT raw_body, symbol FROM tv_alert_events").fetchall() == rows


    def test_tradingview_migration_0085_redacts_nested_secret(self):
        with sqlite3.connect(":memory:") as db:
            db.executescript("CREATE TABLE tv_alert_events (raw_body TEXT, symbol TEXT);"
                             "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT);")
            db.executemany("INSERT INTO tv_alert_events VALUES (?, ?)", [
                # 0084 only strips a TOP-LEVEL $.secret; this one is nested.
                ('{"payload":{"secret":"fixture-only"},"symbol":"TEST"}', "TEST"),
                # A row 0084 already redacted must not be touched again.
                ('{"secret":"[REDACTED]","symbol":"TEST"}', "TEST"),
            ])
            migration_0084 = (_ROOT / "scripts/db/migrations/0084_redact_tv_alert_raw_body.sql").read_text()
            migration_0085 = (
                _ROOT / "scripts/db/migrations/0085_redact_tv_alert_raw_body_nested_secret.sql"
            ).read_text()
            db.executescript(migration_0084)
            db.executescript(migration_0085)
            rows = db.execute("SELECT raw_body, symbol FROM tv_alert_events").fetchall()
            assert all("fixture-only" not in raw for raw, _symbol in rows)
            assert rows[0][0].startswith("[REDACTED PRE-0085")
            assert rows[1][0] == '{"secret":"[REDACTED]","symbol":"TEST"}'
            db.executescript(migration_0085)
            assert db.execute("SELECT raw_body, symbol FROM tv_alert_events").fetchall() == rows

    def test_tradingview_migration_0086_redacts_nested_secret_behind_redacted_top_level(self):
        names = ("0084_redact_tv_alert_raw_body.sql",
                 "0085_redact_tv_alert_raw_body_nested_secret.sql",
                 "0086_redact_tv_alert_raw_body_residual_secret.sql")
        with sqlite3.connect(":memory:") as db:
            db.executescript("CREATE TABLE tv_alert_events (raw_body TEXT, symbol TEXT);"
                             "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT);")
            db.executemany("INSERT INTO tv_alert_events VALUES (?, ?)", [
                # Top-level AND nested secret: 0084 rewrites the top level, so
                # 0085's top-level-IS-NULL guard skips the row.
                ('{"secret":"fixture-only","payload":{"secret":"fixture-only"},"symbol":"TEST"}', "TEST"),
                ('{"secret":"fixture-only","note":"secret=fixture-only","symbol":"TEST"}', "TEST"),
                # Fully redacted rows (top level and nested) stay intact.
                ('{"secret":"[REDACTED]","symbol":"TEST"}', "TEST"),
                ('{"secret":"[REDACTED]","payload":{"secret":"[REDACTED]"},"symbol":"TEST"}', "TEST"),
            ])
            for name in names:
                db.executescript((_ROOT / "scripts/db/migrations" / name).read_text())
            rows = db.execute("SELECT raw_body, symbol FROM tv_alert_events").fetchall()
            assert all("fixture-only" not in raw for raw, _symbol in rows)
            assert rows[0][0].startswith("[REDACTED PRE-0086")
            assert rows[1][0].startswith("[REDACTED PRE-0086")
            assert rows[2][0] == '{"secret":"[REDACTED]","symbol":"TEST"}'
            assert rows[3][0] == '{"secret":"[REDACTED]","payload":{"secret":"[REDACTED]"},"symbol":"TEST"}'
            db.executescript((_ROOT / "scripts/db/migrations" / names[2]).read_text())
            assert db.execute("SELECT raw_body, symbol FROM tv_alert_events").fetchall() == rows
