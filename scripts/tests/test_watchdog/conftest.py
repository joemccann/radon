"""Shared fixtures for the watchdog test suite.

Every watchdog test runs against an in-memory SQLite with the same
0001 + 0006 schema the production code expects. Mocks the libsql
client transparently so writers / readers don't need a network.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path
from typing import Iterator

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_MIGRATIONS = [
    _SCRIPTS_DIR / "db" / "migrations" / "0001_init.sql",
    _SCRIPTS_DIR / "db" / "migrations" / "0006_watchdog_state.sql",
]


def _split_statements(sql: str) -> list[str]:
    stripped = "\n".join(re.sub(r"^\s*--.*$", "", line) for line in sql.splitlines())
    return [s.strip() for s in re.split(r";\s*$", stripped, flags=re.MULTILINE) if s.strip()]


@pytest.fixture(autouse=True)
def _isolate_digest_state(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Point the DUR-14 P2/P3 digest state file at a per-test tmp dir so
    dispatching non-P1 outcomes in tests never writes the real
    data/watchdog_digest_state.json
    (cf. feedback_test_pollution_to_production)."""
    from watchdog import notify

    monkeypatch.setattr(
        notify, "DIGEST_STATE_PATH", tmp_path / "watchdog_digest_state.json"
    )


@pytest.fixture
def db_conn(monkeypatch: pytest.MonkeyPatch) -> Iterator[sqlite3.Connection]:
    """In-memory sqlite with init + watchdog schema applied; patched in
    as the singleton libsql client.
    """
    conn = sqlite3.connect(":memory:")
    for migration in _MIGRATIONS:
        sql = migration.read_text(encoding="utf-8")
        for stmt in _split_statements(sql):
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError as exc:
                if "duplicate column" in str(exc):
                    continue
                raise
    conn.commit()

    import db.client as client_mod
    monkeypatch.setattr(client_mod, "_cached", conn, raising=False)
    monkeypatch.setattr(client_mod, "get_db", lambda: conn)

    import importlib
    import db.writer as writer_mod
    importlib.reload(writer_mod)

    try:
        yield conn
    finally:
        conn.close()
