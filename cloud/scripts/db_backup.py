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

Off-box copy (B2): after the local dump lands, every ``*.sql.gz`` in
BACKUP_DIR that is missing from the ``radon-archive`` bucket under
``db_backups/`` is uploaded, so the first run backfills the whole local
window and later runs push only the new night. Credentials default to
``RADON_ARCHIVE_S3_*`` with optional ``RADON_DB_BACKUP_S3_*`` overrides,
exactly like media_backup.py.

DELIBERATE DIVERGENCE from media_backup.py, which fails CLOSED: here the
local gzip is the critical path and the upload is not. A wedged or
unconfigured B2 never deletes, truncates, or skips the on-box dump -- the
run still writes the file, then reports the upload failure through the
journal and an ``error`` service_health heartbeat (exit 1). Fail-degraded
in that one direction only.

Remote retention is REMOTE_RETENTION_DAYS, deliberately longer than the
on-box RETENTION_DAYS; off-boxing only the local window would buy nothing.
A local dump past RETENTION_DAYS is unlinked only once B2 holds it (the
off-box leg runs first); with no B2 config at all the prune is age-only.

Restore runbook: main repo docs/cloud-services.md "DB backup & restore".
"""
from __future__ import annotations

import gzip
import json
import os
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SERVICE_NAME = "db-backup"
# Operator call 2026-08-29: 7 days on-box. Thirty days of ~570 MB dumps
# held 13 G of the 75 G root fs the night it hit 100% and every gate failed;
# B2 keeps a year, so the local window only needs to cover a restore of a
# very recent dump without a download.
RETENTION_DAYS = 7
# Off-box copies outlive the on-box window by design -- a year of nightly
# dumps in B2 is the whole reason for pushing them off a 75 GB root fs.
# Local RETENTION_DAYS stays the operator's call and is untouched by this.
REMOTE_RETENTION_DAYS = 365
DEFAULT_S3_PREFIX = "db_backups/"
TURSO_TIMEOUT = 10
SUMMARY_CAP = 300
# Wall-clock ceiling for the upload leg. The unit allows TimeoutStartSec
# 19500s and a healthy dump eats ~1500s, so 3600s leaves ample headroom
# while capping a slow first backfill (30 x ~530 MB): whatever does not fit
# is deferred to tomorrow's run, which resumes where this one stopped.
UPLOAD_BUDGET_SECS = 3600
# Bounded transport: botocore has NO default socket timeout, so a wedged B2
# would otherwise hang until TimeoutStartSec kills the whole unit.
S3_CONNECT_TIMEOUT = 30
S3_READ_TIMEOUT = 300
S3_MAX_ATTEMPTS = 3
S3_RETRY_DELAY_SECS = 1.0
# Botocore Config retries can be spent (or skipped on an injected client).
# Page 29c8a560: two consecutive 576 MB PUTs died on ConnectionClosedError
# after the dump had already landed; the unit then paged P1.
_TRANSIENT_S3_ERROR_NAMES = frozenset(
    {
        "ConnectionClosedError",
        "EndpointConnectionError",
        "ConnectTimeoutError",
        "ReadTimeoutError",
        "ResponseStreamingError",
    }
)
MULTIPART_CHUNK_BYTES = 64 * 1024 * 1024
MULTIPART_CONCURRENCY = 4
# Plausibility floor. `dump_database` reports whatever `sqlite_master` gave
# it, so an empty read (a credential rotation pointing at a fresh DB, a
# libsql read returning no rows) produces a valid ~120-byte gzip. Promoting
# it prunes the local window of real dumps and pushes the empty artifact
# off-box, with the heartbeat still green. Same floor, same reason, as
# lib/vixts_math.py:MIN_SERIES_ROWS.
MIN_DUMP_TABLES = 1
MIN_DUMP_ROWS = 1
# Rows per SELECT page. Direct-to-cloud throughput is ~1 MB/s and
# portfolio_snapshots rows are ~12 KB, so 500 rows ≈ 6 MB / ~7 s per page —
# bounded memory instead of a ~700 MB fetchall on the fattest table.
BATCH_SIZE = 500

def _path_env(key: str, default: str) -> Path:
    """`os.environ.get(key, default)` returns "" for a SET-BUT-EMPTY variable,
    and `Path("")` is the CURRENT DIRECTORY.

    `radon-db-backup.service` loads /etc/radon/env wholesale, so a blanked or
    trailing-edit `RADON_DB_BACKUP_DIR=` line resolved BACKUP_DIR to
    `WorkingDirectory=/home/radon/radon` — `mkdir(exist_ok=True)` succeeded
    silently, the dump landed in the live repo checkout, the retention loop
    unlinked every *.sql.gz older than RETENTION_DAYS IN THE REPO, and sync_offbox
    uploaded whatever it found there. R-344.
    """
    raw = (os.environ.get(key) or "").strip()
    return Path(raw or default)


REPO_ROOT = _path_env("RADON_REPO_ROOT", "/home/radon/radon")
BACKUP_DIR = _path_env("RADON_DB_BACKUP_DIR", "/home/radon/radon-cloud/backups/db")


def assert_safe_backup_dir(path: Path, repo_root: Path = None) -> Path:
    """Refuse a backup dir that could put the retention unlink in the repo.

    Called before any mkdir, any dump and any unlink. R-344.
    """
    root = REPO_ROOT if repo_root is None else repo_root
    candidate = Path(path)
    if not candidate.is_absolute():
        raise ValueError(f"backup dir must be absolute, got {candidate!r}")
    resolved = candidate.resolve() if candidate.exists() else candidate
    root_resolved = root.resolve() if root.exists() else root
    if resolved == root_resolved or root_resolved in resolved.parents:
        raise ValueError(
            f"refusing a backup dir inside the repo checkout: {resolved} "
            f"(the retention pass unlinks *.sql.gz there)"
        )
    return candidate

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


def select_prunable(
    entries,
    now_secs: float,
    retention_days: int = RETENTION_DAYS,
    offbox: set[str] | None = None,
):
    """Names of dump files (``*.sql.gz`` ONLY) older than the retention
    window. ``entries`` is an iterable of (name, mtime_secs).

    ``offbox`` is the set of dump names B2 is confirmed to hold. When given,
    a dump is prunable only if it is in that set: age alone never unlinks the
    last copy while an off-box leg is configured. ``None`` means no off-box
    leg exists and the prune is age-only. R-445.
    """
    cutoff = now_secs - retention_days * 86400
    return [
        name
        for name, mtime in entries
        if name.endswith(".sql.gz")
        and mtime < cutoff
        and (offbox is None or name in offbox)
    ]


# REL-185 (R-517): the hard count valve. R-445 makes the age prune keep every
# unconfirmed dump — correct per-night, unbounded under a sustained B2 outage
# (100 nights = 100 dumps on a small disk). Beyond this many local dumps the
# oldest are unlinked EVEN unconfirmed, and the cycle says so distinctly.
LOCAL_DUMP_HARD_CAP = 30


def select_hard_valve(entries, cap: int = LOCAL_DUMP_HARD_CAP):
    """Oldest dump names beyond ``cap``, regardless of off-box confirmation."""
    dumps = sorted(
        ((name, mtime) for name, mtime in entries if name.endswith(".sql.gz")),
        key=lambda item: item[1],
    )
    overflow = len(dumps) - cap
    if overflow <= 0:
        return []
    return [name for name, _mtime in dumps[:overflow]]


@dataclass(frozen=True)
class RemoteObject:
    """One object under the off-box dump prefix (size + mtime for compare)."""

    key: str
    size: int
    mtime: float


def normalize_endpoint(url: str) -> str:
    """B2 console often copies host-only; boto3 requires a scheme."""
    ep = (url or "").strip()
    if ep and not ep.startswith(("http://", "https://")):
        return "https://" + ep
    return ep


def s3_config_from_env(env: dict[str, str] | None = None) -> dict[str, str] | None:
    """Resolve B2/S3 config for the off-box dump copy.

    Preference order per field:
      1. ``RADON_DB_BACKUP_S3_*`` when set (dedicated application key)
      2. fall back to ``RADON_ARCHIVE_S3_*`` (shared radon-archive bucket)

    Prefix: ``RADON_DB_BACKUP_PREFIX`` else ``db_backups/``. It never
    inherits ``RADON_ARCHIVE_S3_PREFIX`` -- dumps must not land in the
    portfolio_snapshots tree.

    Returns ``None`` when endpoint/bucket/key/secret are incomplete.
    """
    env = env if env is not None else dict(os.environ)

    def pick(db_key: str, archive_key: str) -> str:
        return (env.get(db_key) or env.get(archive_key) or "").strip()

    cfg = {
        "endpoint_url": normalize_endpoint(
            pick("RADON_DB_BACKUP_S3_ENDPOINT", "RADON_ARCHIVE_S3_ENDPOINT")
        ),
        "bucket": pick("RADON_DB_BACKUP_S3_BUCKET", "RADON_ARCHIVE_S3_BUCKET"),
        "access_key": pick(
            "RADON_DB_BACKUP_S3_ACCESS_KEY_ID", "RADON_ARCHIVE_S3_ACCESS_KEY_ID"
        ),
        "secret_key": pick(
            "RADON_DB_BACKUP_S3_SECRET_ACCESS_KEY",
            "RADON_ARCHIVE_S3_SECRET_ACCESS_KEY",
        ),
    }
    if not all(cfg.values()):
        return None

    prefix = (env.get("RADON_DB_BACKUP_PREFIX") or DEFAULT_S3_PREFIX).strip()
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    cfg["region"] = pick("RADON_DB_BACKUP_S3_REGION", "RADON_ARCHIVE_S3_REGION") or "auto"
    cfg["prefix"] = prefix or DEFAULT_S3_PREFIX
    return cfg


def object_key_for(prefix: str, name: str) -> str:
    """Join the S3 prefix with a dump basename (no leading slash)."""
    clean = prefix.lstrip("/")
    if clean and not clean.endswith("/"):
        clean += "/"
    return clean + name.lstrip("/")


def is_dump_name(name: str) -> bool:
    """Only completed dumps ship off-box (never the ``.tmp`` in-progress
    file, which starts with a dot and ends ``.tmp``)."""
    return name.endswith(".sql.gz") and not name.startswith(".")


def select_uploadable(local_entries, remote_by_key, prefix: str) -> list[str]:
    """Dump names missing off-box or differing in size, NEWEST FIRST.

    ``local_entries`` is an iterable of ``(name, size_bytes)``. Newest-first
    means tonight's dump is uploaded before any backfill of the older
    window, so a budget-truncated run always protects the newest data.
    Size compare is enough: a dump filename carries its UTC timestamp and is
    never rewritten once ``os.replace`` has landed it.
    """
    planned = []
    for name, size in local_entries:
        if not is_dump_name(name):
            continue
        remote = remote_by_key.get(object_key_for(prefix, name))
        if remote is None or remote.size != size:
            planned.append(name)
    return sorted(planned, reverse=True)


def select_remote_prunable(
    entries, now_secs: float, retention_days: int = REMOTE_RETENTION_DAYS
) -> list[str]:
    """Off-box object keys (``*.sql.gz`` ONLY) past the remote window.

    ``entries`` is an iterable of ``(key, mtime_secs)``. Separate from
    :func:`select_prunable` on purpose: remote keeps a far longer tail.
    """
    cutoff = now_secs - retention_days * 86400
    return [
        key for key, mtime in entries if key.endswith(".sql.gz") and mtime < cutoff
    ]


def fetch_rows(result) -> list:
    """Rows from either result shape: ``.rows`` (Hrana-style wrappers) or
    ``.fetchall()`` (libsql_experimental Cursor / sqlite3)."""
    rows = getattr(result, "rows", None)
    if rows is not None:
        return list(rows)
    return result.fetchall()


def iter_table_rows(db, table: str, batch_size: int):
    """Yield a stable rowid keyset, never replaying an already-emitted page.

    A WITHOUT ROWID table may fall back to one unpaged read only when the
    first rowid query fails. Once any page has been emitted, failure is fatal:
    replaying the whole table would silently duplicate the prefix.
    """
    last_rowid = None
    emitted = False
    while True:
        where = "" if last_rowid is None else f" WHERE rowid > {int(last_rowid)}"
        try:
            page = fetch_rows(db.execute(
                f'SELECT rowid, * FROM "{table}"{where} ORDER BY rowid LIMIT {batch_size}'
            ))
        except Exception:
            if emitted:
                raise
            yield from fetch_rows(db.execute(f'SELECT * FROM "{table}"'))
            return
        if not page:
            return
        for row in page:
            last_rowid = row[0]
            emitted = True
            yield row[1:]
        if len(page) < batch_size:
            return


def _is_virtual_table(sql: str) -> bool:
    return sql.lstrip().upper().startswith("CREATE VIRTUAL TABLE")


def _is_virtual_shadow(name: str, virtual_names: set[str]) -> bool:
    return any(name.startswith(f"{virtual_name}_") for virtual_name in virtual_names)


def _is_portable_object(sql: str) -> bool:
    """Provider-only indexes/functions must be recreated by migrations."""
    lowered = sql.lower()
    return "libsql_vector_idx(" not in lowered


def dump_database(db, out, batch_size: int = BATCH_SIZE) -> dict:
    """Write a full SQL dump of ``db`` (anything with ``.execute(sql)``
    returning rows per :func:`fetch_rows`) to text stream ``out``.

    Emits tables (CREATE + paged per-row INSERTs) first, then indexes /
    views / triggers, inside one transaction. Returns {"tables": n, "rows": n}.
    """
    db.execute("BEGIN TRANSACTION")
    try:
        master = fetch_rows(db.execute(
            "SELECT name, type, sql FROM sqlite_master WHERE sql IS NOT NULL"
        ))
        virtual_names = {
            n for n, t, s in master if t == "table" and _is_virtual_table(s)
        }
        tables = [
            (n, s)
            for n, t, s in master
            if t == "table"
            and not is_internal_object(n)
            and not _is_virtual_shadow(n, virtual_names)
        ]
        other_objects = [
            (n, s)
            for n, t, s in master
            if t != "table"
            and not is_internal_object(n)
            and _is_portable_object(s)
        ]

        out.write(f"-- radon turso dump {datetime.now(timezone.utc).isoformat()}\n")
        out.write("PRAGMA foreign_keys=OFF;\n")
        out.write("BEGIN TRANSACTION;\n")

        total_rows = 0
        table_names = {name for name, _sql in tables}
        for name, create_sql in tables:
            started = time.monotonic()
            out.write(create_sql.rstrip(";") + ";\n")
            table_rows = 0
            # The production FTS mirror is derivative. Restoring it from the
            # canonical knowledge rows avoids dumping provider-generated
            # shadow state and is deterministic across SQLite/libSQL.
            if name == "knowledge_fts" and "knowledge" in table_names:
                out.write(
                    'INSERT INTO "knowledge_fts"(rowid,title,summary,content) '
                    'SELECT rowid,title,summary,content FROM "knowledge";\n'
                )
            else:
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
    finally:
        db.execute("ROLLBACK")


# ---------------------------------------------------------------------------
# Off-box copy to Backblaze B2 (S3 API) -- never the critical path
# ---------------------------------------------------------------------------


def is_transient_s3_error(exc: BaseException) -> bool:
    """True for retryable B2/S3 transport faults (not auth / AccessDenied)."""
    if type(exc).__name__ in _TRANSIENT_S3_ERROR_NAMES:
        return True
    text = str(exc).lower()
    return "connection was closed" in text or "connection reset" in text


def call_s3_with_retry(fn, *, attempts: int | None = None, sleep=None):
    """Run ``fn`` up to ``S3_MAX_ATTEMPTS`` on transient S3 errors."""
    n = S3_MAX_ATTEMPTS if attempts is None else max(1, int(attempts))
    sleeper = time.sleep if sleep is None else sleep
    last: BaseException | None = None
    for i in range(n):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — classify, then re-raise
            last = exc
            if not is_transient_s3_error(exc) or i + 1 >= n:
                raise
            sleeper(S3_RETRY_DELAY_SECS)
    raise last  # pragma: no cover


def _s3_client(cfg: dict[str, str]):
    import boto3  # lazy: unit tests and credential-less runs never import it
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=cfg["endpoint_url"],
        aws_access_key_id=cfg["access_key"],
        aws_secret_access_key=cfg["secret_key"],
        region_name=cfg["region"],
        config=Config(
            connect_timeout=S3_CONNECT_TIMEOUT,
            read_timeout=S3_READ_TIMEOUT,
            retries={"max_attempts": S3_MAX_ATTEMPTS, "mode": "standard"},
        ),
    )


def _transfer_config():
    """Multipart so a ~500 MB dump uploads in bounded, retryable chunks."""
    from boto3.s3.transfer import TransferConfig

    return TransferConfig(
        multipart_threshold=MULTIPART_CHUNK_BYTES,
        multipart_chunksize=MULTIPART_CHUNK_BYTES,
        max_concurrency=MULTIPART_CONCURRENCY,
        use_threads=True,
    )


def _object_mtime(obj) -> float:
    stamp = obj.get("LastModified")
    try:
        return float(stamp.timestamp())
    except Exception:  # noqa: BLE001 - an unparseable stamp must never prune
        return float("inf")


def list_remote_dumps(client, bucket: str, prefix: str) -> dict[str, RemoteObject]:
    """Index every object under ``prefix`` as ``{key: RemoteObject}``."""
    out: dict[str, RemoteObject] = {}
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        resp = call_s3_with_retry(lambda k=kwargs: client.list_objects_v2(**k))
        for obj in resp.get("Contents") or []:
            key = obj["Key"]
            out[key] = RemoteObject(
                key=key, size=int(obj["Size"]), mtime=_object_mtime(obj)
            )
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
        if not token:
            break
    return out


def _confirm_upload(client, bucket: str, key: str) -> int:
    """Bytes the object actually holds in the bucket. Raises when absent."""
    head = call_s3_with_retry(lambda: client.head_object(Bucket=bucket, Key=key))
    return int(head["ContentLength"])


def sync_offbox(
    backup_dir: Path,
    cfg: dict[str, str],
    *,
    client=None,
    now: float | None = None,
    budget_secs: float = UPLOAD_BUDGET_SECS,
    clock=time.monotonic,
) -> dict:
    """Push every local dump missing from B2, then prune the remote tail.

    Idempotent: an object already present at the same size is skipped, so a
    re-run costs one LIST. Resumable: uploads run newest-first and stop when
    ``budget_secs`` is spent, leaving the rest for the next nightly run --
    that is how a local backlog (thirty dumps at the 2026-08-27 cutover)
    backfills without any one run overrunning TimeoutStartSec.
    ``confirmed`` in the summary names every local dump B2 holds at the
    expected size once the run ends: already present, or uploaded and
    confirmed tonight. Deferred and failed dumps are not in it.
    """
    transfer = None
    if client is None:
        # Real transport only: an injected client is a test double, and
        # building a TransferConfig would import boto3 for nothing.
        client = _s3_client(cfg)
        transfer = _transfer_config()

    bucket = cfg["bucket"]
    prefix = cfg["prefix"]
    local = [
        (path.name, path.stat().st_size)
        for path in backup_dir.iterdir()
        if path.is_file()
    ]
    remote = list_remote_dumps(client, bucket, prefix)
    planned = select_uploadable(local, remote, prefix)

    started = clock()
    uploaded = 0
    bytes_uploaded = 0
    deferred = 0
    confirmed: set[str] = set()
    for index, name in enumerate(planned):
        if clock() - started >= budget_secs:
            deferred = len(planned) - index
            break
        path = backup_dir / name
        kwargs = {"Config": transfer} if transfer is not None else {}
        key = object_key_for(prefix, name)
        expected = path.stat().st_size
        call_s3_with_retry(
            lambda: client.upload_file(str(path), bucket, key, **kwargs)
        )
        # CONFIRM before counting, and RAISE on a mismatch rather than
        # recording it — an upload failure already propagates here by design,
        # heartbeats `error` and exits non-zero, and a short landing is an
        # upload failure that the API call happened not to report. It was
        # counted as `b2 1/1` with state=ok, and `select_uploadable` re-uploads
        # only on a size DIFFERENCE, so the corruption was never detected while
        # the local original ran down its local retention. R-372.
        landed = _confirm_upload(client, bucket, key)
        if landed != expected:
            raise RuntimeError(
                f"off-box upload of {key} landed at {landed} bytes, "
                f"expected {expected}; local dump retained"
            )
        uploaded += 1
        bytes_uploaded += expected
        confirmed.add(name)

    prune_now = time.time() if now is None else now
    prunable = select_remote_prunable(
        [(obj.key, obj.mtime) for obj in remote.values()], prune_now
    )
    for key in prunable:
        call_s3_with_retry(lambda k=key: client.delete_object(Bucket=bucket, Key=k))

    dumps = [name for name, _size in local if is_dump_name(name)]
    confirmed.update(name for name in dumps if name not in planned)
    return {
        "bucket": bucket,
        "prefix": prefix,
        "local_dumps": len(dumps),
        "planned": len(planned),
        "uploaded": uploaded,
        "bytes_uploaded": bytes_uploaded,
        "deferred": deferred,
        "skipped_present": len(dumps) - len(planned),
        "remote_pruned": len(prunable),
        "confirmed": sorted(confirmed),
    }


def run_offbox(backup_dir: Path) -> tuple[dict | None, str | None]:
    """Best-effort off-box leg. Returns ``(summary, error)``; NEVER raises.

    The local dump has already landed by the time this runs, and nothing
    here may remove or rewrite it. A failure is reported, not fatal to the
    artifact -- see the module docstring on the deliberate divergence from
    media_backup.py.
    """
    cfg = s3_config_from_env()
    if cfg is None:
        return None, (
            "off-box upload skipped: B2 credentials missing (set "
            "RADON_ARCHIVE_S3_* or RADON_DB_BACKUP_S3_*)"
        )
    try:
        return sync_offbox(backup_dir, cfg), None
    except Exception as exc:  # noqa: BLE001 - upload is not the critical path
        return None, f"{exc.__class__.__name__}: {exc}"


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


def assert_plausible_dump(stats: dict) -> None:
    """Raise before an implausible dump is promoted, pruned against or shipped.

    Checked while the dump is still the ``.tmp`` file, so a failure leaves
    yesterday's dumps untouched, nothing uploaded, and no empty artifact on
    disk for a LATER run's backfill to push off-box. `main` turns the raise
    into an ``error`` heartbeat and exit 1.
    """
    if stats["tables"] < MIN_DUMP_TABLES or stats["rows"] < MIN_DUMP_ROWS:
        raise RuntimeError(
            f"implausible dump: {stats['tables']} tables / {stats['rows']} rows "
            f"(floor {MIN_DUMP_TABLES} tables / {MIN_DUMP_ROWS} rows); "
            "refusing to promote, prune or upload"
        )


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
        assert_plausible_dump(stats)
        os.replace(tmp_path, final_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    assert_safe_backup_dir(BACKUP_DIR)
    size = final_path.stat().st_size

    # Off-box leg BEFORE the local prune and non-fatal: the artifact above is
    # already durable, and a dump may leave the box only once B2 holds it.
    # R-445: pruning on mtime first meant eight nights of AccessDenied (paged
    # each night, never retried) unlinked the night-1 dump unheard.
    offbox, offbox_error = run_offbox(BACKUP_DIR)
    if s3_config_from_env() is None:
        confirmed = None  # no off-box leg configured: age is the only signal
    else:
        # A summary without the listing (a raise, a double) confirms nothing.
        confirmed = set((offbox or {}).get("confirmed") or ())

    now = time.time()
    entries = [(p.name, p.stat().st_mtime) for p in BACKUP_DIR.iterdir() if p.is_file()]
    pruned = select_prunable(entries, now, offbox=confirmed)
    for name in pruned:
        (BACKUP_DIR / name).unlink(missing_ok=True)
    # REL-185 (R-517): hard valve — bound the local dir even when B2 has
    # confirmed nothing.
    remaining = [(n, m) for n, m in entries if n not in set(pruned)]
    valve_pruned = select_hard_valve(remaining)
    for name in valve_pruned:
        (BACKUP_DIR / name).unlink(missing_ok=True)

    duration = round(time.monotonic() - started, 1)
    summary = (
        f"dumped {stats['tables']} tables / {stats['rows']} rows -> "
        f"{final_path.name} ({size} bytes) in {duration}s; pruned {len(pruned)}"
    )
    if valve_pruned:
        summary += (
            f"; LOCAL RETENTION VALVE: pruned {len(valve_pruned)} unconfirmed "
            f"dump(s) over the {LOCAL_DUMP_HARD_CAP} cap (B2 outage?)"
        )
    if offbox_error:
        summary += f"; b2 FAILED: {offbox_error[:160]}"
    else:
        summary += (
            f"; b2 {offbox['uploaded']}/{offbox['planned']} "
            f"({offbox['bytes_uploaded']} B), deferred {offbox['deferred']}, "
            f"remote pruned {offbox['remote_pruned']}"
        )
    detail = {
        "summary": summary[:SUMMARY_CAP],
        "path": str(final_path),
        "size_bytes": size,
        "duration_secs": duration,
        "tables": stats["tables"],
        "rows": stats["rows"],
        "pruned": len(pruned),
        "valve_pruned": len(valve_pruned),
        "offbox_error": offbox_error,
    }
    if offbox:
        detail.update(
            {
                "offbox_bucket": offbox["bucket"],
                "offbox_prefix": offbox["prefix"],
                "offbox_uploaded": offbox["uploaded"],
                "offbox_bytes_uploaded": offbox["bytes_uploaded"],
                "offbox_deferred": offbox["deferred"],
                "offbox_remote_pruned": offbox["remote_pruned"],
            }
        )
    return detail


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

    # The dump itself succeeded; only the off-box copy can still be red.
    state = "error" if detail.get("offbox_error") else "ok"
    print(
        f"db-backup: {detail['summary']}",
        file=sys.stderr if state == "error" else sys.stdout,
    )
    try:
        write_service_health(state, detail, started_at)
    except Exception as exc:  # noqa: BLE001 - bounded write, surface the failure
        print(f"service_health write failed: {exc}", file=sys.stderr)
        return 1
    print(f"service_health row written: db-backup = {state}")
    return 1 if state == "error" else 0


if __name__ == "__main__":
    sys.exit(main())
