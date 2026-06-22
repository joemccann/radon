"""F14 — workflow graph persistence (Turso save/load/list).

Runs against an in-memory SQLite loaded with migrations 0001 + 0019, with
``db.client.get_db`` patched to the connection. No network, no real Turso. The
disk mirror is redirected at a tmp dir so the real ``data/workflow_graphs/`` is
never touched (cf. feedback_test_pollution_to_production).
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path
from typing import Iterator

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_MIGRATIONS = [
    _SCRIPTS_DIR / "db" / "migrations" / "0001_init.sql",
    _SCRIPTS_DIR / "db" / "migrations" / "0019_workflow_graphs.sql",
]


def _split_statements(sql: str) -> list[str]:
    stripped = "\n".join(re.sub(r"^\s*--.*$", "", line) for line in sql.splitlines())
    return [s.strip() for s in re.split(r";\s*$", stripped, flags=re.MULTILINE) if s.strip()]


@pytest.fixture
def db_conn(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(":memory:")
    for migration in _MIGRATIONS:
        for stmt in _split_statements(migration.read_text(encoding="utf-8")):
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

    import workflow.graphs as graphs_mod
    monkeypatch.setattr(graphs_mod, "_DISK_DIR", tmp_path / "workflow_graphs")

    try:
        yield conn
    finally:
        conn.close()


SAMPLE_GRAPH = {
    "nodes": [
        {"id": "n1", "type": "data-source", "params": {"source": "scanner"}},
        {"id": "n2", "type": "filter", "params": {"expression": "score >= 60"}},
        {"id": "n3", "type": "notify", "params": {"channel": "service_health"}},
    ],
    "edges": [{"from": "n1", "to": "n2"}, {"from": "n2", "to": "n3"}],
}


def test_save_then_load_roundtrips_the_graph(db_conn):
    from workflow.graphs import load_graph, save_graph

    record = save_graph("user_1", "Accumulation alerts", SAMPLE_GRAPH)
    loaded = load_graph(record["id"])

    assert loaded is not None
    assert loaded["name"] == "Accumulation alerts"
    assert loaded["graph"] == SAMPLE_GRAPH


def test_save_with_existing_id_updates_in_place(db_conn):
    from workflow.graphs import load_graph, save_graph

    first = save_graph("user_1", "v1", SAMPLE_GRAPH)
    save_graph("user_1", "v2", {"nodes": [], "edges": []}, graph_id=first["id"])

    loaded = load_graph(first["id"])
    assert loaded["name"] == "v2"
    assert loaded["graph"] == {"nodes": [], "edges": []}

    # still a single row — update, not insert
    count = db_conn.execute("SELECT COUNT(*) FROM workflow_graphs").fetchone()[0]
    assert count == 1


def test_list_graphs_is_user_scoped(db_conn):
    from workflow.graphs import list_graphs, save_graph

    save_graph("user_1", "mine", SAMPLE_GRAPH)
    save_graph("user_2", "theirs", SAMPLE_GRAPH)

    mine = list_graphs("user_1")
    assert [g["name"] for g in mine] == ["mine"]


def test_record_run_caches_last_execution(db_conn):
    from workflow.graphs import record_run, save_graph

    record = save_graph("user_1", "g", SAMPLE_GRAPH)
    record_run(record["id"], ok=False, summary={"blocked_by": "n2", "blocked_gate": "convexity"})

    row = db_conn.execute(
        "SELECT last_run_ok, last_run_summary FROM workflow_graphs WHERE id = ?",
        (record["id"],),
    ).fetchone()
    assert row[0] == 0
    assert "convexity" in row[1]
