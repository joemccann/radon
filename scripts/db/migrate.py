#!/usr/bin/env python3.13
"""Apply pending Turso migrations from scripts/db/migrations/*.sql.

Python parallel of `scripts/db/migrate.ts`. Same semantics — reads numbered
SQL files in lex order, skips versions already in `schema_migrations`,
applies the rest. Used on Hetzner where Bun isn't installed and the
existing Python venv is the path of least resistance.

Idempotent: running twice with no new migrations is a no-op.

Usage:
    python3.13 scripts/db/migrate.py

Env: TURSO_DB_URL + TURSO_AUTH_TOKEN required (loaded from .env / web/.env).
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

_PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]
    load_dotenv(_PROJECT_DIR / ".env")
    load_dotenv(_PROJECT_DIR / ".env.ib-mode")
    load_dotenv(_PROJECT_DIR / "web" / ".env")
except Exception:
    pass

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

RETRY_BACKOFF_SECONDS = (2, 5, 15)

_TRANSPORT_ERROR_MARKERS = ("hrana", "dns", "timeout", "timed out", "connection")

_BOOTSTRAP_SQL = """
    CREATE TABLE IF NOT EXISTS schema_migrations (
      version    INTEGER PRIMARY KEY,
      applied_at TEXT    NOT NULL
    )
    """


def _is_transport_error(exc: BaseException) -> bool:
    """libsql_experimental raises bare ValueError for everything, so classify
    by message: Hrana/dns/timeout/connection failures are retryable transport
    blips (the 2026-06-12 incident was ValueError("Hrana: dns error") on a
    transient Turso DNS failure); SQL/schema errors are not."""
    message = str(exc).lower()
    return any(marker in message for marker in _TRANSPORT_ERROR_MARKERS)


def _connect_with_retry(libsql, url: str, token: str):
    """Connect and bootstrap schema_migrations, retrying transport-class
    failures with RETRY_BACKOFF_SECONDS between attempts. This runs as
    radon-api's ExecStartPre — a hard failure here blocks service startup,
    so transient network blips must not be fatal. Non-transport errors
    (SQL syntax, schema) propagate immediately."""
    remaining_delays = list(RETRY_BACKOFF_SECONDS)
    while True:
        try:
            db = libsql.connect(url, auth_token=token)
            db.execute(_BOOTSTRAP_SQL)
            db.commit()
            return db
        except Exception as exc:
            if not _is_transport_error(exc) or not remaining_delays:
                raise
            delay = remaining_delays.pop(0)
            sys.stderr.write(
                f"[migrate] transport error ({exc}); retrying in {delay}s\n"
            )
            time.sleep(delay)


def _list_migrations() -> list[tuple[int, str, Path]]:
    if not MIGRATIONS_DIR.is_dir():
        sys.stderr.write(f"No migrations directory at {MIGRATIONS_DIR}\n")
        sys.exit(1)
    rows: list[tuple[int, str, Path]] = []
    pattern = re.compile(r"^(\d+)_.*\.sql$")
    for entry in sorted(MIGRATIONS_DIR.iterdir()):
        match = pattern.match(entry.name)
        if not match:
            continue
        rows.append((int(match.group(1)), entry.name, entry))
    return rows


def _split_statements(sql: str) -> list[str]:
    """Strip line comments, then split on `;` keeping non-empty statements.
    libSQL doesn't support multi-statement execute() in one call."""
    stripped_lines = [re.sub(r"^\s*--.*$", "", line) for line in sql.splitlines()]
    stripped = "\n".join(stripped_lines)
    parts = re.split(r";\s*$", stripped, flags=re.MULTILINE)
    return [s.strip() for s in parts if s.strip()]


# Substrings SQLite/libsql use for "this object is already there", which on a
# REPLAY is the applied state rather than a failure (R-153).
_ALREADY_APPLIED_MARKERS = (
    "duplicate column name",
    "already exists",
)


def _is_already_applied(exc: BaseException) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in _ALREADY_APPLIED_MARKERS)


def main() -> None:
    url = os.environ.get("TURSO_DB_URL")
    token = os.environ.get("TURSO_AUTH_TOKEN")
    if not url or not token:
        sys.stderr.write(
            "TURSO_DB_URL and TURSO_AUTH_TOKEN must be set "
            "(see web/.env or root .env).\n"
        )
        sys.exit(1)

    try:
        import libsql_experimental as libsql  # type: ignore[import-untyped]
    except ImportError:
        sys.stderr.write(
            "libsql_experimental is not installed in this venv. "
            "Run: pip install libsql-experimental\n"
        )
        sys.exit(1)

    db = _connect_with_retry(libsql, url, token)

    applied = {row[0] for row in db.execute("SELECT version FROM schema_migrations").fetchall()}
    migrations = _list_migrations()
    pending = [m for m in migrations if m[0] not in applied]

    if not pending:
        print(f"[migrate] nothing to apply — {len(applied)} migration(s) already at latest")
        return

    print(f"[migrate] applying {len(pending)} migration(s) → {url}")
    for version, name, path in pending:
        print(f"[migrate] → {name}")
        sql = path.read_text(encoding="utf-8")
        for stmt in _split_statements(sql):
            try:
                db.execute(stmt)
            except Exception as exc:
                # R-153: 0050 is the only real ALTER TABLE in the set. A kill
                # between its committed ADD COLUMN and the version row left
                # version 50 unrecorded, so the next run replayed it, hit
                # `duplicate column name` and ABORTED — taking 0051-0054 with
                # it. migrate.py is radon-api's ExecStartPre, so that is a
                # control-plane outage on every boot until a hand repair.
                # A statement whose object already exists IS the applied
                # state; anything else still fails loudly.
                if _is_already_applied(exc):
                    sys.stderr.write(
                        f"[migrate] {name}: statement already applied, continuing "
                        f"({exc})\n"
                    )
                    continue
                sys.stderr.write(f"[migrate] FAILED on statement:\n{stmt[:200]}\n\n")
                raise
        # The migration file's own INSERT INTO schema_migrations may already
        # record the version; if not, record it ourselves. INSERT OR IGNORE
        # keeps both code paths idempotent. Issued BEFORE the commit so the
        # version lands with the statements, not in a second round trip that
        # a dropped connection can lose.
        db.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, datetime('now'))",
            (version,),
        )
        db.commit()

    print("[migrate] done")


if __name__ == "__main__":
    main()
