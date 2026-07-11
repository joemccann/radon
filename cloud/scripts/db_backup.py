#!/usr/bin/env python3
"""Nightly full dump of the canonical Turso store (DUR-13).

Dumps EVERY user table by iterating sqlite_master -- no hand-picked table
list, so migrations that add tables are captured automatically. Output is
portable SQL (schema + INSERTs, sqlite3-.dump-style), gzip'd and
date-stamped under backups/db/. Retains RETENTION_DAYS days on-box.

Heartbeats the ``service_health`` row ``db-backup`` on EVERY run -- ok with
size + duration detail, error with the failure summary. A backup timer with
no liveness signal is the canonical silently-dead backup
(feedback_service_health_heartbeat).

Runs as a STANDALONE oneshot via radon-db-backup.timer; the libsql GIL
constraint applies to the FastAPI event loop, not here. The dump reads
through the main repo's scripts.db.client.get_db() (direct-to-cloud
libsql); the heartbeat uses the bounded stdlib libSQL HTTP pipeline (same
pattern as drift_audit.py) so a wedged libsql client can never block the
liveness signal. libsql_experimental has no client-side timeouts (DUR-09),
so the real bound is the unit's TimeoutStartSec.

Restore runbook: main repo docs/cloud-services.md "DB backup & restore".
"""
from __future__ import annotations

import gzip
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SERVICE_NAME = "db-backup"
RETENTION_DAYS = 30
TURSO_TIMEOUT = 10
SUMMARY_CAP = 300
# Rows per SELECT page. Direct-to-cloud throughput is ~1 MB/s and
# portfolio_snapshots rows are ~12 KB, so 500 rows ≈ 6 MB / ~7 s per page —
# bounded memory instead of a ~700 MB fetchall on the fattest table.
BATCH_SIZE = 500

REPO_ROOT = Path(os.environ.get("RADON_REPO_ROOT", "/home/radon/radon"))
BACKUP_DIR = Path(
    os.environ.get("RADON_DB_BACKUP_DIR", "/home/radon/radon-cloud/backups/db")
)

_INTERNAL_PREFIXES = ("sqlite_", "libsql_", "_litestream")


# ---------------------------------------------------------------------------
# Pure dump helpers (unit-tested in tests/test_db_backup.py)
# ---------------------------------------------------------------------------


def sql_literal(value) -> str:
    """Render a Python value as a SQLite SQL literal (sqlite3 .dump style)."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value:  # NaN has no SQL literal
            return "NULL"
        if value in (float("inf"), float("-inf")):
            return "9.0e999" if value > 0 else "-9.0e999"
        return repr(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "X'" + bytes(value).hex() + "'"
    return "'" + str(value).replace("'", "''") + "'"


def build_insert(table: str, row) -> str:
    values = ",".join(sql_literal(v) for v in row)
    return f'INSERT INTO "{table}" VALUES ({values});'


def is_internal_object(name: str) -> bool:
    """SQLite/libSQL bookkeeping objects that a restore must not recreate."""
    return name.startswith(_INTERNAL_PREFIXES)


def select_prunable(entries, now_secs: float, retention_days: int = RETENTION_DAYS):
    """Names of dump files (``*.sql.gz`` ONLY) older than the retention
    window. ``entries`` is an iterable of (name, mtime_secs)."""
    cutoff = now_secs - retention_days * 86400
    return [
        name
        for name, mtime in entries
        if name.endswith(".sql.gz") and mtime < cutoff
    ]


def fetch_rows(result) -> list:
    """Rows from either result shape: ``.rows`` (Hrana-style wrappers) or
    ``.fetchall()`` (libsql_experimental Cursor / sqlite3)."""
    rows = getattr(result, "rows", None)
    if rows is not None:
        return list(rows)
    return result.fetchall()


def iter_table_rows(db, table: str, batch_size: int):
    """Yield every row of ``table`` in bounded pages (ORDER BY rowid LIMIT/
    OFFSET). WITHOUT ROWID tables fall back to a single unpaged SELECT —
    none of Radon's fat snapshot tables are WITHOUT ROWID."""
    try:
        offset = 0
        while True:
            page = fetch_rows(db.execute(
                f'SELECT * FROM "{table}" ORDER BY rowid LIMIT {batch_size} OFFSET {offset}'
            ))
            yield from page
            if len(page) < batch_size:
                return
            offset += batch_size
    except Exception:
        yield from fetch_rows(db.execute(f'SELECT * FROM "{table}"'))


def dump_database(db, out, batch_size: int = BATCH_SIZE) -> dict:
    """Write a full SQL dump of ``db`` (anything with ``.execute(sql)``
    returning rows per :func:`fetch_rows`) to text stream ``out``.

    Emits tables (CREATE + paged per-row INSERTs) first, then indexes /
    views / triggers, inside one transaction. Returns {"tables": n, "rows": n}.
    """
    master = fetch_rows(db.execute(
        "SELECT name, type, sql FROM sqlite_master WHERE sql IS NOT NULL"
    ))
    tables = [(n, s) for n, t, s in master if t == "table" and not is_internal_object(n)]
    other_objects = [
        (n, s) for n, t, s in master if t != "table" and not is_internal_object(n)
    ]

    out.write(f"-- radon turso dump {datetime.now(timezone.utc).isoformat()}\n")
    out.write("PRAGMA foreign_keys=OFF;\n")
    out.write("BEGIN TRANSACTION;\n")

    total_rows = 0
    for name, create_sql in tables:
        started = time.monotonic()
        out.write(create_sql.rstrip(";") + ";\n")
        table_rows = 0
        for row in iter_table_rows(db, name, batch_size):
            out.write(build_insert(name, row) + "\n")
            table_rows += 1
        total_rows += table_rows
        print(
            f"  {name}: {table_rows} rows in {time.monotonic() - started:.1f}s",
            file=sys.stderr,
            flush=True,
        )

    for _name, create_sql in other_objects:
        out.write(create_sql.rstrip(";") + ";\n")

    out.write("COMMIT;\n")
    return {"tables": len(tables), "rows": total_rows}


# ---------------------------------------------------------------------------
# service_health heartbeat (stdlib libSQL HTTP pipeline -- bounded, no libsql)
# ---------------------------------------------------------------------------

_UPSERT_SQL = (
    "INSERT INTO service_health (service, state, last_attempt_started_at, "
    "last_attempt_finished_at, last_error, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
    "ON CONFLICT(service) DO UPDATE SET state = excluded.state, "
    "last_attempt_started_at = COALESCE(excluded.last_attempt_started_at, service_health.last_attempt_started_at), "
    "last_attempt_finished_at = COALESCE(excluded.last_attempt_finished_at, service_health.last_attempt_finished_at), "
    "last_error = excluded.last_error, "
    "updated_at = excluded.updated_at"
)


def http_url_from_libsql(url: str) -> str:
    if url.startswith("libsql://"):
        return "https://" + url[len("libsql://"):]
    if url.startswith("wss://"):
        return "https://" + url[len("wss://"):]
    return url


def _hrana_arg(value):
    return {"type": "null"} if value is None else {"type": "text", "value": value}


def write_service_health(state: str, detail: dict | None, started_at: str) -> None:
    db_url = os.environ.get("TURSO_DB_URL", "")
    token = os.environ.get("TURSO_AUTH_TOKEN", "")
    origin = http_url_from_libsql(db_url)
    if not origin or not token:
        raise RuntimeError("TURSO_DB_URL / TURSO_AUTH_TOKEN missing from environment")
    now = datetime.now(timezone.utc).isoformat()
    payload = json.dumps(
        {
            "requests": [
                {
                    "type": "execute",
                    "stmt": {
                        "sql": _UPSERT_SQL,
                        "args": [
                            _hrana_arg(SERVICE_NAME),
                            _hrana_arg(state),
                            _hrana_arg(started_at),
                            _hrana_arg(now),
                            _hrana_arg(json.dumps(detail) if detail else None),
                            _hrana_arg(now),
                        ],
                    },
                },
                {"type": "close"},
            ]
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        origin.rstrip("/") + "/v2/pipeline",
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + token,
        },
    )
    with urllib.request.urlopen(req, timeout=TURSO_TIMEOUT) as resp:
        body = json.loads(resp.read(1_048_576).decode("utf-8"))
    first = body["results"][0]
    if first.get("type") != "ok":
        raise RuntimeError(f"service_health upsert rejected: {json.dumps(first)[:300]}")


# ---------------------------------------------------------------------------
# Backup run
# ---------------------------------------------------------------------------


def _open_cloud_db():
    """Direct-to-cloud libsql connection via the main repo's client."""
    sys.path.insert(0, str(REPO_ROOT))
    from scripts.db.client import get_db  # deferred: tests never import libsql

    return get_db()


def run_backup() -> dict:
    started = time.monotonic()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    final_path = BACKUP_DIR / f"radon-{stamp}.sql.gz"
    tmp_path = BACKUP_DIR / f".radon-{stamp}.sql.gz.tmp"

    db = _open_cloud_db()
    try:
        with gzip.open(tmp_path, "wt", encoding="utf-8") as out:
            stats = dump_database(db, out)
        os.replace(tmp_path, final_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    now = time.time()
    entries = [(p.name, p.stat().st_mtime) for p in BACKUP_DIR.iterdir() if p.is_file()]
    pruned = select_prunable(entries, now)
    for name in pruned:
        (BACKUP_DIR / name).unlink(missing_ok=True)

    size = final_path.stat().st_size
    duration = round(time.monotonic() - started, 1)
    return {
        "summary": (
            f"dumped {stats['tables']} tables / {stats['rows']} rows -> "
            f"{final_path.name} ({size} bytes) in {duration}s; pruned {len(pruned)}"
        )[:SUMMARY_CAP],
        "path": str(final_path),
        "size_bytes": size,
        "duration_secs": duration,
        "tables": stats["tables"],
        "rows": stats["rows"],
        "pruned": len(pruned),
    }


def main() -> int:
    started_at = datetime.now(timezone.utc).isoformat()
    try:
        detail = run_backup()
    except Exception as exc:  # noqa: BLE001 - backup crash must still heartbeat
        crash = {"summary": f"backup failed: {exc.__class__.__name__}: {exc}"[:SUMMARY_CAP]}
        print(crash["summary"], file=sys.stderr)
        try:
            write_service_health("error", crash, started_at)
        except Exception as write_exc:  # noqa: BLE001
            print(f"service_health write failed: {write_exc}", file=sys.stderr)
        return 1

    print(f"db-backup: {detail['summary']}")
    try:
        write_service_health("ok", detail, started_at)
    except Exception as exc:  # noqa: BLE001 - bounded write, surface the failure
        print(f"service_health write failed: {exc}", file=sys.stderr)
        return 1
    print("service_health row written: db-backup = ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
