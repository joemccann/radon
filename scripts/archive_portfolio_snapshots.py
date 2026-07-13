#!/usr/bin/env python3
"""Cold-archive portfolio_snapshots older than the hot-retention window.

Tiered storage. Only the newest portfolio_snapshots row is ever read at
runtime (ORDER BY taken_at DESC LIMIT 1), but the full intraday history is
worth keeping. So: keep ~30 days live in Turso and move everything older into
append-only, gzip'd, monthly JSONL partitions
(``archive/portfolio_snapshots/YYYY-MM.jsonl.gz``), each uploaded to off-box
S3-compatible object storage, then deleted from Turso.

Mirrors db_backup.py conventions: stdlib gzip, batched paging (never a
fetchall on the fat table), bounded stdlib ``service_health`` heartbeat (the
libsql client is never used for liveness), standalone oneshot whose real wall
bound is the unit's ``TimeoutStartSec``.

Crash-safe ordering — a crash never loses a row:
  1. cutoff = now - RETENTION_DAYS. Rows are only ever inserted at "now", so
     nothing new ever lands < cutoff after this instant.
  2. Page rows WHERE taken_at < cutoff (Rust client), grouped by month.
  3. Merge each month into its .jsonl.gz, deduped + sorted by taken_at, written
     atomically (temp + os.replace).
  4. Upload each touched partition off-box (REQUIRED unless
     --allow-delete-without-upload).
  5. Verify every fetched taken_at is present in its on-disk partition.
  6. DELETE FROM portfolio_snapshots WHERE taken_at < cutoff.

A crash after step 3 but before step 6 simply leaves the rows in BOTH places;
re-running re-archives them (dedup makes the merge a no-op) and proceeds.

Usage::

    archive_portfolio_snapshots.py [--retention-days 30] [--archive-dir DIR]
        [--batch-size 500] [--dry-run] [--no-delete]
        [--allow-delete-without-upload]

Off-box upload is configured entirely via environment
(``RADON_ARCHIVE_S3_*``); see ``s3_config_from_env``.
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

SERVICE_NAME = "portfolio-archive"
RETENTION_DAYS = 30
BATCH_SIZE = 500
TURSO_TIMEOUT = 10
SUMMARY_CAP = 300

DEFAULT_ARCHIVE_DIR = Path(
    os.environ.get(
        "RADON_ARCHIVE_DIR",
        "/home/radon/radon-cloud/archive/portfolio_snapshots",
    )
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested in scripts/tests/test_archive_portfolio_snapshots.py)
# ---------------------------------------------------------------------------
def compute_cutoff_iso(now: datetime, retention_days: int) -> str:
    """ISO timestamp ``retention_days`` before ``now``, in the ``taken_at``
    format (``YYYY-MM-DDTHH:MM:SS.ffffffZ``) so the string compare against the
    TEXT PK is a correct chronological range."""
    cutoff = now.astimezone(timezone.utc) - timedelta(days=retention_days)
    return cutoff.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def month_key(taken_at: str) -> str:
    """``2026-05`` partition key from a ``taken_at`` timestamp string."""
    return taken_at[:7]


def group_by_month(rows: Iterable[tuple[str, str]]) -> dict[str, list[tuple[str, str]]]:
    """Group ``(taken_at, payload)`` rows by ``YYYY-MM``."""
    grouped: dict[str, list[tuple[str, str]]] = {}
    for taken_at, payload in rows:
        grouped.setdefault(month_key(taken_at), []).append((taken_at, payload))
    return grouped


def _payload_to_jsonable(payload: str) -> Any:
    """Embed the stored payload as parsed JSON for a clean, queryable archive;
    fall back to the raw string if it isn't valid JSON (lossless either way)."""
    try:
        return json.loads(payload)
    except (TypeError, ValueError):
        return payload


def rows_to_jsonl_bytes(rows: Iterable[tuple[str, str]]) -> bytes:
    """Serialize ``(taken_at, payload)`` rows to newline-delimited JSON bytes,
    one object per line: ``{"taken_at": ..., "payload": <parsed>}``."""
    buf = io.StringIO()
    for taken_at, payload in rows:
        json.dump({"taken_at": taken_at, "payload": _payload_to_jsonable(payload)}, buf)
        buf.write("\n")
    return buf.getvalue().encode("utf-8")


def read_partition_rows(path: Path) -> dict[str, str]:
    """Read an existing ``.jsonl.gz`` partition into ``{taken_at: line_json}``.

    Returns the raw JSON text per row keyed by taken_at, so a merge can dedup
    by key without re-parsing payloads. Missing file -> empty dict.
    """
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                taken_at = json.loads(line)["taken_at"]
            except (ValueError, KeyError, TypeError):
                continue
            out[taken_at] = line
    return out


def merge_partition_bytes(existing: dict[str, str], new_rows: Iterable[tuple[str, str]]) -> bytes:
    """Merge ``new_rows`` into an existing ``{taken_at: line}`` map (new rows
    win on conflict), returning gzip-ready, taken_at-sorted JSONL bytes."""
    merged = dict(existing)
    for taken_at, payload in new_rows:
        line = json.dumps(
            {"taken_at": taken_at, "payload": _payload_to_jsonable(payload)}
        )
        merged[taken_at] = line
    ordered = "\n".join(merged[k] for k in sorted(merged))
    return (ordered + "\n").encode("utf-8") if ordered else b""


def s3_config_from_env(env: dict[str, str] | None = None) -> dict[str, str] | None:
    """Off-box object-storage config from ``RADON_ARCHIVE_S3_*``.

    Returns ``None`` when the required keys are absent (upload disabled).
    Required: ENDPOINT, BUCKET, ACCESS_KEY_ID, SECRET_ACCESS_KEY.
    Optional: REGION (default ``auto`` for R2), PREFIX (default
    ``portfolio_snapshots/``).
    """
    env = env if env is not None else dict(os.environ)
    required = {
        "endpoint_url": env.get("RADON_ARCHIVE_S3_ENDPOINT", ""),
        "bucket": env.get("RADON_ARCHIVE_S3_BUCKET", ""),
        "access_key": env.get("RADON_ARCHIVE_S3_ACCESS_KEY_ID", ""),
        "secret_key": env.get("RADON_ARCHIVE_S3_SECRET_ACCESS_KEY", ""),
    }
    if not all(required.values()):
        return None
    # B2 console often copies host-only; boto3 requires a scheme.
    ep = required["endpoint_url"].strip()
    if ep and not ep.startswith(("http://", "https://")):
        required["endpoint_url"] = "https://" + ep
    required["region"] = env.get("RADON_ARCHIVE_S3_REGION", "auto")
    required["prefix"] = env.get("RADON_ARCHIVE_S3_PREFIX", "portfolio_snapshots/")
    return required


# ---------------------------------------------------------------------------
# Side-effecting layer (DB / disk / S3) — integration-tested with mocks
# ---------------------------------------------------------------------------
def fetch_archivable_rows(db, cutoff: str, batch_size: int) -> Iterator[tuple[str, str]]:
    """Yield ``(taken_at, payload)`` for rows with ``taken_at < cutoff`` in
    bounded, keyset-paginated pages (no OFFSET drift, no fat fetchall)."""
    last = ""
    while True:
        page = _fetch_rows(
            db.execute(
                """
                SELECT taken_at, payload FROM portfolio_snapshots
                WHERE taken_at < ? AND taken_at > ?
                ORDER BY taken_at ASC LIMIT ?
                """,
                (cutoff, last, batch_size),
            )
        )
        if not page:
            return
        for taken_at, payload in page:
            yield taken_at, payload
        last = page[-1][0]
        if len(page) < batch_size:
            return


def _fetch_rows(result) -> list:
    rows = getattr(result, "rows", None)
    if rows is not None:
        return list(rows)
    return result.fetchall()


def write_partition_atomic(path: Path, data: bytes) -> None:
    """Atomically write gzip'd ``data`` to ``path`` (temp + os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def upload_partition(path: Path, key: str, cfg: dict[str, str]) -> None:
    """Upload one partition file to S3-compatible storage (lazy boto3)."""
    import boto3  # lazy: unit tests and disk-only runs never import it

    client = boto3.client(
        "s3",
        endpoint_url=cfg["endpoint_url"],
        aws_access_key_id=cfg["access_key"],
        aws_secret_access_key=cfg["secret_key"],
        region_name=cfg["region"],
    )
    client.upload_file(str(path), cfg["bucket"], key)


# ---------------------------------------------------------------------------
# service_health heartbeat — bounded stdlib libSQL HTTP pipeline (no libsql)
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


def _http_url_from_libsql(url: str) -> str:
    if url.startswith("libsql://"):
        return "https://" + url[len("libsql://") :]
    if url.startswith("wss://"):
        return "https://" + url[len("wss://") :]
    return url


def _hrana_arg(value):
    return {"type": "null"} if value is None else {"type": "text", "value": value}


def write_service_health(state: str, detail: dict | None, started_at: str) -> None:
    db_url = os.environ.get("TURSO_DB_URL", "")
    token = os.environ.get("TURSO_AUTH_TOKEN", "")
    origin = _http_url_from_libsql(db_url)
    if not origin or not token:
        return
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
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
    )
    try:
        with urllib.request.urlopen(req, timeout=TURSO_TIMEOUT) as resp:
            resp.read(1_048_576)
    except Exception as exc:  # liveness write must never crash the archive run
        print(f"  service_health heartbeat failed: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def run_archive(
    db,
    *,
    cutoff: str,
    archive_dir: Path,
    batch_size: int,
    s3_cfg: dict[str, str] | None,
    do_delete: bool,
    allow_delete_without_upload: bool,
    delete_fn,
) -> dict:
    """Archive + (optionally) delete rows older than ``cutoff``. Returns a
    summary dict. ``delete_fn(cutoff) -> int`` performs the Turso DELETE."""
    rows = list(fetch_archivable_rows(db, cutoff, batch_size))
    if not rows:
        return {"archived": 0, "months": [], "uploaded": 0, "deleted": 0}

    grouped = group_by_month(rows)
    months_written: list[str] = []
    uploaded = 0
    for month in sorted(grouped):
        path = archive_dir / f"{month}.jsonl.gz"
        existing = read_partition_rows(path)
        merged = merge_partition_bytes(existing, grouped[month])
        write_partition_atomic(path, merged)
        months_written.append(month)

        # Verify every fetched taken_at for this month is now on disk.
        on_disk = set(read_partition_rows(path))
        fetched = {ta for ta, _ in grouped[month]}
        missing = fetched - on_disk
        if missing:
            raise RuntimeError(
                f"archive verify failed for {month}: {len(missing)} rows missing on disk"
            )

        if s3_cfg is not None:
            key = s3_cfg["prefix"].lstrip("/") + f"{month}.jsonl.gz"
            upload_partition(path, key, s3_cfg)
            uploaded += 1

    if not do_delete:
        return {"archived": len(rows), "months": months_written, "uploaded": uploaded, "deleted": 0}

    if s3_cfg is None and not allow_delete_without_upload:
        raise RuntimeError(
            "refusing to delete: off-box upload not configured "
            "(set RADON_ARCHIVE_S3_* or pass --allow-delete-without-upload)"
        )

    deleted = delete_fn(cutoff)
    return {
        "archived": len(rows),
        "months": months_written,
        "uploaded": uploaded,
        "deleted": deleted,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retention-days", type=int, default=RETENTION_DAYS)
    parser.add_argument("--archive-dir", type=Path, default=DEFAULT_ARCHIVE_DIR)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--dry-run", action="store_true", help="report what would be archived; touch nothing")
    parser.add_argument("--no-delete", action="store_true", help="archive + upload but keep rows in Turso")
    parser.add_argument("--allow-delete-without-upload", action="store_true")
    args = parser.parse_args(argv)

    sys.path.insert(0, str(REPO_ROOT))
    started_at = datetime.now(timezone.utc).isoformat()
    t0 = time.monotonic()
    cutoff = compute_cutoff_iso(datetime.now(timezone.utc), args.retention_days)

    from scripts.db.client import get_db
    from scripts.db.writer import delete_portfolio_snapshots_before

    db = get_db()

    if args.dry_run:
        # Cheap indexed aggregate — never materialize the (fat) payloads.
        counts = _fetch_rows(
            db.execute(
                """
                SELECT substr(taken_at, 1, 7) AS m, COUNT(*)
                FROM portfolio_snapshots WHERE taken_at < ?
                GROUP BY m ORDER BY m
                """,
                (cutoff,),
            )
        )
        months = {m: c for m, c in counts}
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "cutoff": cutoff,
                    "archivable": sum(months.values()),
                    "months": months,
                    "s3_configured": s3_config_from_env() is not None,
                },
                indent=2,
            )
        )
        return 0

    try:
        summary = run_archive(
            db,
            cutoff=cutoff,
            archive_dir=args.archive_dir,
            batch_size=args.batch_size,
            s3_cfg=s3_config_from_env(),
            do_delete=not args.no_delete,
            allow_delete_without_upload=args.allow_delete_without_upload,
            delete_fn=delete_portfolio_snapshots_before,
        )
    except Exception as exc:
        detail = {"cutoff": cutoff, "error": str(exc)[:SUMMARY_CAP]}
        write_service_health("error", detail, started_at)
        print(json.dumps({"ok": False, **detail}), file=sys.stderr)
        raise

    summary["cutoff"] = cutoff
    summary["elapsed_s"] = round(time.monotonic() - t0, 2)
    write_service_health("ok", summary, started_at)
    print(json.dumps({"ok": True, **summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
