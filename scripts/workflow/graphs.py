"""Turso persistence for workflow graphs (with JSON disk fallback).

Graphs are saved per-user into ``workflow_graphs`` (migration 0019). A JSON
mirror under ``data/workflow_graphs/`` is written alongside so the canvas keeps
working when the DB is unavailable — the same disk-fallback contract used across
the script tree. All functions are best-effort: a DB hiccup degrades to disk
rather than raising.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
_DISK_DIR = _PROJECT_DIR / "data" / "workflow_graphs"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _disk_path(graph_id: str) -> Path:
    return _DISK_DIR / f"{graph_id}.json"


def _write_disk(record: dict) -> None:
    try:
        _DISK_DIR.mkdir(parents=True, exist_ok=True)
        _disk_path(record["id"]).write_text(json.dumps(record, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001 — disk mirror is best-effort
        pass


def save_graph(
    user_id: str,
    name: str,
    graph: dict,
    *,
    graph_id: Optional[str] = None,
) -> dict:
    """Insert or update a graph. Returns the stored record. Writes the disk
    mirror unconditionally and the DB row best-effort."""
    record_id = graph_id or str(uuid.uuid4())
    now = _now_iso()
    graph_json = json.dumps(graph)
    record = {
        "id": record_id,
        "user_id": user_id,
        "name": name,
        "graph": graph,
        "updated_at": now,
    }
    _write_disk(record)

    try:
        from db.client import get_db

        db = get_db()
        db.execute(
            """
            INSERT INTO workflow_graphs (id, user_id, name, graph, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                graph = excluded.graph,
                updated_at = excluded.updated_at
            """,
            (record_id, user_id, name, graph_json, now, now),
        )
        db.commit()
    except Exception:  # noqa: BLE001 — DB write best-effort, disk already mirrored
        pass

    return record


def load_graph(graph_id: str) -> Optional[dict]:
    """Load a single graph by id, preferring the DB then the disk mirror."""
    db_record = _load_graph_from_db(graph_id)
    if db_record is not None:
        return db_record
    return _load_graph_from_disk(graph_id)


def _load_graph_from_db(graph_id: str) -> Optional[dict]:
    try:
        from db.client import get_db

        db = get_db()
        cursor = db.execute(
            "SELECT id, user_id, name, graph, updated_at FROM workflow_graphs WHERE id = ?",
            (graph_id,),
        )
        rows = cursor.fetchall() if hasattr(cursor, "fetchall") else list(cursor)
        if not rows:
            return None
        return _row_to_record(rows[0])
    except Exception:  # noqa: BLE001
        return None


def _load_graph_from_disk(graph_id: str) -> Optional[dict]:
    path = _disk_path(graph_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def list_graphs(user_id: str) -> list[dict]:
    """List a user's graphs (id + name + updated_at), DB-first."""
    try:
        from db.client import get_db

        db = get_db()
        cursor = db.execute(
            "SELECT id, name, updated_at FROM workflow_graphs WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        )
        rows = cursor.fetchall() if hasattr(cursor, "fetchall") else list(cursor)
        return [{"id": r[0], "name": r[1], "updated_at": r[2]} for r in rows]
    except Exception:  # noqa: BLE001
        return _list_graphs_from_disk(user_id)


def _list_graphs_from_disk(user_id: str) -> list[dict]:
    if not _DISK_DIR.is_dir():
        return []
    out = []
    for path in _DISK_DIR.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if record.get("user_id") == user_id:
            out.append({"id": record["id"], "name": record["name"], "updated_at": record.get("updated_at")})
    out.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
    return out


def record_run(graph_id: str, ok: bool, summary: dict[str, Any]) -> None:
    """Cache the last execution summary on the graph row (best-effort)."""
    try:
        from db.client import get_db

        db = get_db()
        db.execute(
            "UPDATE workflow_graphs SET last_run_at = ?, last_run_ok = ?, last_run_summary = ? WHERE id = ?",
            (_now_iso(), 1 if ok else 0, json.dumps(summary), graph_id),
        )
        db.commit()
    except Exception:  # noqa: BLE001
        pass


def _row_to_record(row: Any) -> dict:
    graph_raw = row[3]
    try:
        graph = json.loads(graph_raw) if isinstance(graph_raw, str) else graph_raw
    except Exception:  # noqa: BLE001
        graph = {"nodes": [], "edges": []}
    return {
        "id": row[0],
        "user_id": row[1],
        "name": row[2],
        "graph": graph,
        "updated_at": row[4],
    }
