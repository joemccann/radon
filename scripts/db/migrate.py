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

    db = libsql.connect(url, auth_token=token)

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version    INTEGER PRIMARY KEY,
          applied_at TEXT    NOT NULL
        )
        """
    )
    db.commit()

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
                sys.stderr.write(f"[migrate] FAILED on statement:\n{stmt[:200]}\n\n")
                raise
        db.commit()
        # The migration file's own INSERT INTO schema_migrations may already
        # record the version; if not, record it ourselves. INSERT OR IGNORE
        # keeps both code paths idempotent.
        db.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, datetime('now'))",
            (version,),
        )
        db.commit()

    print("[migrate] done")


if __name__ == "__main__":
    main()
