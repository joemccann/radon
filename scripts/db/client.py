"""libSQL client + embedded replica for Python schedulers.

Mirror of `web/lib/db.ts`. Same Turso DB, same replica file path.
Reads hit the local SQLite file (fast); writes stream to the cloud.

Usage:
    from scripts.db.client import get_db, sync_db
    db = get_db()
    db.execute("INSERT OR REPLACE INTO service_health(service, state, updated_at) "
               "VALUES (?, ?, datetime('now'))", ("cri-scan", "ok"))
    rows = db.execute("SELECT payload FROM cri_snapshots ORDER BY taken_at DESC LIMIT 1").rows

The first call to `get_db()` creates and back-fills the replica from the
cloud (one-time, ~5s). Subsequent reads are SQLite-direct.

In tests / CI we skip the embedded replica (no replica path) and use
the cloud client directly. Set `RADON_DB_NO_REPLICA=1` to force the
direct path.
"""

from __future__ import annotations

import os
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


def get_db() -> object:
    """Return a process-wide singleton libSQL connection.

    Uses the embedded replica unless RADON_DB_NO_REPLICA is set or
    we're running under pytest. Returns a `libsql_experimental.Connection`
    with `.execute(sql, params)`, `.executemany`, `.commit()`, `.sync()`.
    """
    global _cached
    if _cached is not None:
        return _cached

    with _lock:
        if _cached is not None:
            return _cached

        url, token = _read_env()
        use_replica = (
            not os.environ.get("RADON_DB_NO_REPLICA")
            and not os.environ.get("PYTEST_CURRENT_TEST")
        )

        if use_replica:
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
