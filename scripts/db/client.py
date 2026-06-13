"""libSQL client for Python schedulers.

Mirror of `web/lib/db.ts`. Same Turso DB, same direct-to-cloud default.

Usage:
    from scripts.db.client import get_db, sync_db
    db = get_db()
    db.execute("INSERT OR REPLACE INTO service_health(service, state, updated_at) "
               "VALUES (?, ?, datetime('now'))", ("cri-scan", "ok"))
    rows = db.execute("SELECT payload FROM cri_snapshots ORDER BY taken_at DESC LIMIT 1").fetchall()

Embedded replicas were retired on 2026-05-20 after WAL conflicts between
multi-writer hosts (feedback_libsql_replica_one_writer.md). The safe
default is a direct cloud connection; opening a replica requires an
explicit `RADON_DB_USE_REPLICA=1` opt-in and logs a loud warning. The
legacy `RADON_DB_NO_REPLICA=1` kill switch still forces the direct path.

TIMEOUT REALITY (DUR-09, libsql_experimental 0.0.55 — the version pinned
on prod): `libsql.connect()` exposes NO connect/execute/commit timeout
(signature: database, isolation_level, check_same_thread, uri, sync_url,
sync_interval, auth_token, encryption_key, autocommit), and the native
calls hold the GIL while blocked — so a thread-timeout wrapper around
them is a lie (the "timed-out" call keeps starving the process). DO NOT
add one. The bound for the subprocess/daemon consumers of this module
must come from process-level supervision instead:
  - subprocess scans: the FastAPI `run_script` timeout already kills them
  - systemd services/oneshots (monitor daemon, watchdog, timers): set
    `RuntimeMaxSec=` on the radon-cloud unit (precedent: 2fbc73f /
    TimeoutStartSec=60 on radon-ib-watchdog). Recommended: RuntimeMaxSec
    sized to ~3x the unit's normal cycle for oneshots/timers.
The FastAPI process must NEVER import this module at all — it uses the
bounded HTTP pipeline in scripts/api/db_http.py (enforced by the
test_no_sync_libsql_in_api.py lint).
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Optional

try:
    import libsql_experimental as libsql  # type: ignore[import-untyped]
except ImportError as exc:
    raise ImportError(
        "libsql_experimental is not installed. "
        "Run `python3.13 -m pip install libsql-experimental` or add to requirements.txt."
    ) from exc


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_REPLICA_PATH = _PROJECT_ROOT / "data" / "replica.db"

_lock = threading.Lock()
_cached: Optional[object] = None  # libsql_experimental.Connection


def _read_env() -> tuple[str, str]:
    url = os.environ.get("TURSO_DB_URL")
    token = os.environ.get("TURSO_AUTH_TOKEN")
    if not url or not token:
        raise RuntimeError(
            "TURSO_DB_URL and TURSO_AUTH_TOKEN must be set "
            "(see web/.env or root .env). Plan §0."
        )
    return url, token


def _replica_opted_in() -> bool:
    """The retired embedded replica is opt-in ONLY: explicit
    RADON_DB_USE_REPLICA=1, not killed by the legacy RADON_DB_NO_REPLICA
    switch, and never under pytest."""
    return (
        os.environ.get("RADON_DB_USE_REPLICA") == "1"
        and not os.environ.get("RADON_DB_NO_REPLICA")
        and not os.environ.get("PYTEST_CURRENT_TEST")
    )


def _warn_replica_opt_in() -> None:
    print(
        "[radon-db] WARNING: RADON_DB_USE_REPLICA=1 — opening the RETIRED "
        f"libsql embedded replica at {_REPLICA_PATH}. Only one process per "
        "host may hold it (WalConflict). Direct-to-cloud has been the "
        "default since 2026-05-20; see feedback_libsql_replica_one_writer.md.",
        file=sys.stderr,
        flush=True,
    )


def get_db() -> object:
    """Return a process-wide singleton libSQL connection.

    Connects direct-to-cloud unless the replica is explicitly opted in
    via RADON_DB_USE_REPLICA=1 (see `_replica_opted_in`). Returns a
    `libsql_experimental.Connection` with `.execute(sql, params)`,
    `.executemany`, `.commit()`, `.sync()`.

    Test-pollution guard: if pytest is the caller (PYTEST_CURRENT_TEST is
    set) and the test hasn't explicitly opted in via RADON_DB_TEST_WRITE_OK,
    refuse to open a real connection. Every test that needs DB access
    must either monkeypatch `get_db` (see test_watchdog/conftest.py and
    test_phase2_writers.py) or set the override. This prevents a missed
    mock from silently writing phantom rows to production Turso — the
    failure mode that surfaced 2026-05-14 as MagicMock contracts being
    persisted to the production journal table.
    """
    global _cached
    if _cached is not None:
        return _cached

    with _lock:
        if _cached is not None:
            return _cached

        if (
            os.environ.get("PYTEST_CURRENT_TEST")
            and os.environ.get("RADON_DB_TEST_WRITE_OK") != "1"
        ):
            raise RuntimeError(
                "db.client.get_db() called from a test without a monkeypatch. "
                "Mock get_db in your fixture (see test_watchdog/conftest.py) "
                "or set RADON_DB_TEST_WRITE_OK=1 for explicit integration tests. "
                "Refusing to open a production connection from pytest."
            )

        url, token = _read_env()

        if _replica_opted_in():
            _warn_replica_opt_in()
            _REPLICA_PATH.parent.mkdir(parents=True, exist_ok=True)
            conn = libsql.connect(
                str(_REPLICA_PATH),
                sync_url=url,
                auth_token=token,
            )
            # Initial back-fill so the first read sees server state.
            conn.sync()
            _cached = conn
        else:
            _cached = libsql.connect(url, auth_token=token)

    return _cached


def sync_db() -> None:
    """Force a sync from cloud → replica. Call at startup if you need
    the freshest possible read; not required during steady-state since
    libSQL syncs in the background.
    """
    db = get_db()
    if hasattr(db, "sync"):
        db.sync()  # type: ignore[attr-defined]


def reset_for_tests() -> None:
    """Drop the cached client (test seam)."""
    global _cached
    with _lock:
        _cached = None
