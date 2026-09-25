#!/usr/bin/env python3.13
"""Apply pending Turso migrations from scripts/db/migrations/*.sql.

Python parallel of `scripts/db/migrate.ts`. Same semantics — reads numbered
SQL files in lex order, skips versions already in `schema_migrations`,
applies the rest. Used on Hetzner where Bun isn't installed and the
existing Python venv is the path of least resistance.

Idempotent: running twice with no new migrations is a no-op.

Usage:
    python3.13 scripts/db/migrate.py
    python3.13 scripts/db/migrate.py --demo
    python3.13 scripts/db/migrate.py --boot    # radon-api startup: never blocks boot

Env (prod, default): TURSO_DB_URL + TURSO_AUTH_TOKEN.
Env (--demo): TURSO_DEMO_DB_URL + TURSO_DEMO_AUTH_TOKEN. Refuses any URL
containing the production marker; requires the demo marker. radon-demo-mirror
runs this as ExecStartPre so mirrored tables exist before the Node write.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
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

# radon-api runs this ahead of uvicorn. libsql has no socket timeout, so a
# Turso brownout (2026-09-25) held boot for 156s, failed the deploy gate and
# the rollback gate, and left production down. Past this deadline the API
# boots without the check; deploy.sh's preflight_database refuses teardown
# while Turso is unhealthy, so a deploy never skips a pending migration.
BOOT_DEADLINE_SECONDS = float(os.environ.get("RADON_MIGRATE_BOOT_DEADLINE", "20"))

EXIT_DATABASE_UNREACHABLE = 75  # EX_TEMPFAIL

SCHEMA_MARKER_DIR = Path(os.environ.get("RADON_SCHEMA_MARKER_DIR", _PROJECT_DIR / "data"))

_TRANSPORT_ERROR_MARKERS = ("hrana", "dns", "timeout", "timed out", "connection")

# Same markers the market/newsfeed demo mirrors use — refuse to migrate prod
# when invoked as --demo, and require the demo DB name.
_PROD_URL_MARKER = "radon-joemccann"
_DEMO_URL_MARKER = "radon-demo"

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


def finished_within_deadline(command: list[str], deadline_seconds: float) -> bool:
    """Run ``command`` in a child process; False when the database is
    unreachable (child still hung at the deadline, or it exited
    EXIT_DATABASE_UNREACHABLE). Any other failure exits with the child's code.

    A child, not a thread: a hung libsql connect holds the GIL, so no
    in-process timer can fire. The child can always be killed."""
    try:
        returncode = subprocess.run(command, timeout=deadline_seconds).returncode
    except subprocess.TimeoutExpired:
        return False
    if returncode == 0:
        return True
    if returncode == EXIT_DATABASE_UNREACHABLE:
        return False
    sys.exit(returncode)


def _this_script(*flags: str) -> list[str]:
    return [sys.executable, str(Path(__file__).resolve()), *flags]


def _unless_unreachable(work) -> None:
    """Run ``work``; transport-class failure exits EXIT_DATABASE_UNREACHABLE."""
    try:
        work()
    except Exception as exc:
        if not _is_transport_error(exc):
            raise
        sys.stderr.write(f"[migrate] database unreachable ({exc})\n")
        sys.exit(EXIT_DATABASE_UNREACHABLE)


def _schema_marker(label: str) -> Path:
    return SCHEMA_MARKER_DIR / f"schema_version.{label}"


def _newest_migration_version() -> int:
    return max(version for version, _, _ in _list_migrations())


def record_current_schema(label: str) -> None:
    """Best effort: a missing marker only means a brownout boot fails closed."""
    marker = _schema_marker(label)
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"{_newest_migration_version()}\n", encoding="utf-8")
    except OSError as exc:
        sys.stderr.write(f"[migrate] could not record schema version ({exc})\n")


def is_schema_known_current(label: str) -> bool:
    try:
        recorded = int(_schema_marker(label).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    return recorded >= _newest_migration_version()


def _migrate(libsql, url: str, token: str, label: str) -> None:
    apply_pending_migrations(_connect_with_retry(libsql, url, token))
    record_current_schema(label)


def boot(label: str, target_flags: list[str]) -> None:
    """Service startup: never wait on the database past the deadline."""
    if finished_within_deadline(_this_script(*target_flags), BOOT_DEADLINE_SECONDS):
        return
    if not is_schema_known_current(label):
        sys.stderr.write(
            f"[migrate] no answer within {BOOT_DEADLINE_SECONDS:g}s and the schema "
            "is not known current; refusing to boot\n"
        )
        sys.exit(EXIT_DATABASE_UNREACHABLE)
    sys.stderr.write(
        f"[migrate] no answer within {BOOT_DEADLINE_SECONDS:g}s; schema known current, "
        "booting without the check\n"
    )


def _list_migrations() -> list[tuple[int, str, Path]]:
    if not MIGRATIONS_DIR.is_dir():
        sys.stderr.write(f"No migrations directory at {MIGRATIONS_DIR}\n")
        sys.exit(1)
    rows: list[tuple[int, str, Path]] = []
    seen: dict[int, str] = {}
    pattern = re.compile(r"^(\d+)_.*\.sql$")
    for entry in sorted(MIGRATIONS_DIR.iterdir()):
        match = pattern.match(entry.name)
        if not match:
            continue
        version = int(match.group(1))
        prior = seen.get(version)
        if prior is not None:
            raise SystemExit(
                f"[migrate] duplicate version {version}: {prior} and {entry.name}"
            )
        seen[version] = entry.name
        rows.append((version, entry.name, entry))
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


def resolve_target(*, demo: bool) -> tuple[str, str]:
    """Return (url, token) for prod (default) or the isolated demo Turso."""
    if demo:
        url = os.environ.get("TURSO_DEMO_DB_URL")
        token = os.environ.get("TURSO_DEMO_AUTH_TOKEN")
        if not url or not token:
            sys.stderr.write(
                "TURSO_DEMO_DB_URL and TURSO_DEMO_AUTH_TOKEN must be set "
                "for --demo (the SEPARATE demo Turso DB).\n"
            )
            sys.exit(2)
        if _PROD_URL_MARKER in url:
            raise SystemExit(
                "REFUSING TO MIGRATE: --demo target URL "
                f"({url!r}) contains the production marker {_PROD_URL_MARKER!r}."
            )
        if _DEMO_URL_MARKER not in url:
            raise SystemExit(
                "REFUSING TO MIGRATE: --demo target URL "
                f"({url!r}) is missing the demo marker {_DEMO_URL_MARKER!r}."
            )
        return url, token

    url = os.environ.get("TURSO_DB_URL")
    token = os.environ.get("TURSO_AUTH_TOKEN")
    if not url or not token:
        sys.stderr.write(
            "TURSO_DB_URL and TURSO_AUTH_TOKEN must be set "
            "(see web/.env or root .env).\n"
        )
        sys.exit(1)
    return url, token


def apply_pending_migrations(db) -> int:
    """Apply pending numbered SQL migrations to an open connection.

    Returns the number of migration files applied. Idempotent.
    """
    applied = {
        row[0] for row in db.execute("SELECT version FROM schema_migrations").fetchall()
    }
    migrations = _list_migrations()
    pending = [m for m in migrations if m[0] not in applied]

    if not pending:
        print(
            f"[migrate] nothing to apply — {len(applied)} migration(s) already at latest"
        )
        return 0

    print(f"[migrate] applying {len(pending)} migration(s)")
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
            "INSERT OR IGNORE INTO schema_migrations (version, applied_at) "
            "VALUES (?, datetime('now'))",
            (version,),
        )
        db.commit()

    print("[migrate] done")
    return len(pending)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Apply migrations to TURSO_DEMO_* (demo.radon.run), never prod",
    )
    parser.add_argument(
        "--boot",
        action="store_true",
        help="Service startup: bounded by BOOT_DEADLINE_SECONDS",
    )
    # Explicit argv (including []) so library callers / tests are not polluted
    # by the process's sys.argv (pytest injects the test file path).
    args = parser.parse_args([] if argv is None else argv)

    url, token = resolve_target(demo=args.demo)

    try:
        import libsql_experimental as libsql  # type: ignore[import-untyped]
    except ImportError:
        sys.stderr.write(
            "libsql_experimental is not installed in this venv. "
            "Run: pip install libsql-experimental\n"
        )
        sys.exit(1)

    label = "demo" if args.demo else "prod"
    print(f"[migrate] target={label} → {url}")
    if args.boot:
        boot(label, ["--demo"] if args.demo else [])
        return
    _unless_unreachable(lambda: _migrate(libsql, url, token, label))


if __name__ == "__main__":
    # main() parses [] when argv is None, so this forwarding IS the only
    # thing that lets --demo reach argparse. Dropping it makes
    # radon-demo-mirror's ExecStartPre migrate prod and exit 0.
    # Pinned by TestMigrateEntrypointArgv in scripts/tests/test_migrate.py.
    main(sys.argv[1:])
