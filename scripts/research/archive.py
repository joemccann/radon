#!/usr/bin/env python3
"""Weekly archive of private research evidence to Backblaze B2, then prune.

``<root>/evidence/<work key>/`` holds per-document extraction artifacts
(evidence.json, page markdown, review.json). The pipeline reads them only
while a document is being processed; afterwards only the offline harness
does. They are the bulk of the private research volume, so this job moves
settled ones off-box.

Scope is evidence only. ``assets/`` is served live to subscribers
(``/api/newsfeed/research/files/``) and ``downloads/`` holds the cached PDFs
that queued reviews hash-check. Neither is touched here: their archive and
prune is ``research.asset_archive`` (daily), which only became safe once
``research.assets.read_asset`` learned to read a pruned asset back from B2
and the downloads pass learned to skip files that pending work still needs.

A directory is a candidate only when:
  * every entry is a regular file or directory (no symlinks or specials),
  * its newest mtime is older than ``compute_cutoff`` (a rolling
    ``--min-age-days`` back from now; the cutoff no longer snaps to the
    month start, which left a month of evidence on the app host disk), and
  * its work row in ``state.sqlite`` is settled (complete, published,
    deleted, superseded) or absent. Pending or processing work is skipped.

Each file goes to ``<prefix>evidence/<YYYY-MM of newest mtime>/<key>/<rel>``
with ``Content-MD5`` (B2 rejects a body that does not match) and its SHA-256
in object metadata. A HEAD then confirms size and SHA-256 before a file
counts as verified; an object that already verifies is not re-uploaded, so
re-runs are idempotent. Deletion happens only after every file in the
directory verified, the work row is re-read and still settled, and each file
still has the size and mtime that was verified. Anything else stays on disk.

Runs inside the research container (``radon-app-runtime``), the only place
the private root is readable. Heartbeat: ``service_health`` row
``research-archive``.

Usage::

    archive.py [--root DIR] [--dry-run] [--no-prune] [--min-age-days 7]
        [--max-dirs N] [--max-bytes N]
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sqlite3
import stat
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

SERVICE_NAME = "research-archive"
DEFAULT_ROOT = "/var/lib/radon/research"
DEFAULT_PREFIX = "research/"
SETTLED = frozenset({"complete", "published", "deleted", "superseded"})
KEY_RE = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}\Z")
DEFAULT_MIN_AGE_DAYS = 7
DEFAULT_MAX_DIRS = 20_000
#: Per-file B2 round trips (HEAD, PUT, HEAD) dominate; serial ran ~1 GiB/hour
#: on evidence dirs of many small pages and hit the unit's 4h timeout.
DEFAULT_WORKERS = 16
#: Stop starting new dirs here so the run exits clean (and heartbeats) before
#: TimeoutStartSec kills it; the next run resumes, uploads are idempotent.
DEFAULT_DEADLINE_SECS = 3 * 3600
DEFAULT_MAX_BYTES = 20 * 1024 ** 3
MAX_FILES_PER_DIR = 5_000
MAX_FILE_BYTES = 1024 ** 3
CHUNK = 1024 * 1024
SUMMARY_ERRORS = 20
S3_CONNECT_TIMEOUT = 30
S3_READ_TIMEOUT = 300
S3_MAX_ATTEMPTS = 3
S3_RETRY_DELAY_SECS = 1.0
_TRANSIENT = frozenset({"ConnectionClosedError", "EndpointConnectionError", "ConnectTimeoutError",
                        "ReadTimeoutError", "ResponseStreamingError"})


@dataclass(frozen=True)
class LocalFile:
    path: Path
    rel: str
    size: int
    mtime_ns: int
    md5_b64: str = ""
    sha256: str = ""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def compute_cutoff(now: datetime, min_age_days: int) -> datetime:
    """Rolling cutoff: nothing newer than ``min_age_days`` ago qualifies.

    The archive key still groups objects by the month of the directory's
    newest mtime, so a weekly run and the old monthly run lay out the bucket
    identically."""
    return now.astimezone(timezone.utc) - timedelta(days=min_age_days)


def s3_config_from_env(env: dict | None = None) -> dict | None:
    """Shared ``RADON_ARCHIVE_S3_*`` credentials, research-owned prefix.

    ``RADON_ARCHIVE_S3_PREFIX`` belongs to the portfolio archive and is never
    inherited. Returns None when the credentials are incomplete.
    """
    env = dict(os.environ) if env is None else env
    endpoint = (env.get("RADON_ARCHIVE_S3_ENDPOINT") or "").strip()
    if endpoint and not endpoint.startswith(("http://", "https://")):
        endpoint = "https://" + endpoint
    cfg = {
        "endpoint_url": endpoint,
        "bucket": (env.get("RADON_ARCHIVE_S3_BUCKET") or "").strip(),
        "access_key": (env.get("RADON_ARCHIVE_S3_ACCESS_KEY_ID") or "").strip(),
        "secret_key": (env.get("RADON_ARCHIVE_S3_SECRET_ACCESS_KEY") or "").strip(),
    }
    if not all(cfg.values()):
        return None
    prefix = (env.get("RADON_RESEARCH_ARCHIVE_PREFIX") or DEFAULT_PREFIX).strip().strip("/")
    cfg["prefix"] = (prefix or DEFAULT_PREFIX.strip("/")) + "/"
    cfg["region"] = (env.get("RADON_ARCHIVE_S3_REGION") or "").strip() or "auto"
    return cfg


def _s3_client(cfg: dict, *, connect_timeout: float = S3_CONNECT_TIMEOUT,
               read_timeout: float = S3_READ_TIMEOUT, max_attempts: int = S3_MAX_ATTEMPTS):
    import boto3  # lazy: tests and dry runs never import it
    from botocore.config import Config

    return boto3.client(
        "s3", endpoint_url=cfg["endpoint_url"], aws_access_key_id=cfg["access_key"],
        aws_secret_access_key=cfg["secret_key"], region_name=cfg["region"],
        config=Config(connect_timeout=connect_timeout, read_timeout=read_timeout,
                      retries={"max_attempts": max_attempts, "mode": "standard"},
                      max_pool_connections=DEFAULT_WORKERS,
                      # Content-MD5 is the integrity check; skip the optional
                      # CRC trailer so only headers B2 documents are sent.
                      request_checksum_calculation="when_required",
                      response_checksum_validation="when_required"),
    )


def _retry(fn):
    for attempt in range(S3_MAX_ATTEMPTS):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - classify, then re-raise
            if type(exc).__name__ not in _TRANSIENT or attempt + 1 >= S3_MAX_ATTEMPTS:
                raise
            time.sleep(S3_RETRY_DELAY_SECS)
    raise AssertionError("unreachable")  # pragma: no cover


def _is_not_found(exc: BaseException) -> bool:
    code = str(((getattr(exc, "response", None) or {}).get("Error") or {}).get("Code", ""))
    return code in {"404", "NoSuchKey", "NotFound"}


# ---------------------------------------------------------------------------
# Local scan
# ---------------------------------------------------------------------------


def _settled_status(root: Path) -> dict[str, str]:
    path = root / "state.sqlite"
    if not path.is_file():
        raise FileNotFoundError(f"research state missing: {path}")
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    try:
        return {key: status for key, status in db.execute("SELECT key, status FROM work")}
    finally:
        db.close()


def _status_of(root: Path, key: str) -> str | None:
    db = sqlite3.connect(f"file:{root / 'state.sqlite'}?mode=ro", uri=True, timeout=30)
    try:
        row = db.execute("SELECT status FROM work WHERE key=?", (key,)).fetchone()
    finally:
        db.close()
    return row[0] if row else None


def _walk(directory: Path) -> list[LocalFile] | None:
    """Every regular file under ``directory``; None if anything is unsafe."""
    files: list[LocalFile] = []
    stack = [directory]
    while stack:
        current = stack.pop()
        with os.scandir(current) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    stack.append(Path(entry.path))
                elif stat.S_ISREG(info.st_mode) and info.st_size <= MAX_FILE_BYTES:
                    files.append(LocalFile(Path(entry.path), Path(entry.path).relative_to(directory).as_posix(),
                                           info.st_size, info.st_mtime_ns))
                else:
                    return None
                if len(files) > MAX_FILES_PER_DIR:
                    return None
    return sorted(files, key=lambda f: f.rel)


def _digest(local: LocalFile) -> LocalFile:
    md5, sha = hashlib.md5(), hashlib.sha256()
    with open(local.path, "rb") as stream:
        for block in iter(lambda: stream.read(CHUNK), b""):
            md5.update(block)
            sha.update(block)
    return LocalFile(local.path, local.rel, local.size, local.mtime_ns,
                     base64.b64encode(md5.digest()).decode(), sha.hexdigest())


# ---------------------------------------------------------------------------
# Upload + verify
# ---------------------------------------------------------------------------


def _verify_object(client, bucket: str, key: str, local: LocalFile) -> bool:
    try:
        head = _retry(lambda: client.head_object(Bucket=bucket, Key=key))
    except Exception as exc:  # noqa: BLE001
        if _is_not_found(exc):
            return False
        raise
    metadata = {k.lower(): v for k, v in (head.get("Metadata") or {}).items()}
    return int(head.get("ContentLength", -1)) == local.size and metadata.get("sha256") == local.sha256


def _upload(client, bucket: str, key: str, local: LocalFile) -> None:
    def put():
        with open(local.path, "rb") as body:
            return client.put_object(Bucket=bucket, Key=key, Body=body, ContentLength=local.size,
                                     ContentMD5=local.md5_b64, Metadata={"sha256": local.sha256})
    _retry(put)


def _archive_one(client, cfg: dict, key: str, month: str, item: LocalFile) -> tuple[LocalFile, int]:
    local = _digest(item)
    object_key = f"{cfg['prefix']}evidence/{month}/{key}/{local.rel}"
    if _verify_object(client, cfg["bucket"], object_key, local):
        return local, 0
    _upload(client, cfg["bucket"], object_key, local)
    if not _verify_object(client, cfg["bucket"], object_key, local):
        raise RuntimeError(f"verification failed for {object_key}")
    return local, 1


def _archive_dir(client, cfg: dict, key: str, month: str, files: list[LocalFile],
                 workers: int = DEFAULT_WORKERS) -> tuple[list[LocalFile], int, int]:
    """Upload what is missing; return (verified files, files uploaded, bytes uploaded).

    Raises on the first file that cannot be verified, so a partial directory
    never reaches the prune step.
    """
    verified, uploaded, uploaded_bytes = [], 0, 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(_archive_one, client, cfg, key, month, item) for item in files]
        # result() re-raises the first failure; the pool still drains, and the
        # caller never prunes a directory with an unverified file.
        for future in futures:
            local, sent = future.result()
            verified.append(local)
            uploaded += sent
            uploaded_bytes += local.size if sent else 0
    return verified, uploaded, uploaded_bytes


def _prune(directory: Path, verified: list[LocalFile]) -> bool:
    """Unlink files still identical to what was verified; True if the whole
    directory is gone."""
    for local in verified:
        try:
            info = os.lstat(local.path)
        except FileNotFoundError:
            continue
        if stat.S_ISREG(info.st_mode) and info.st_size == local.size and info.st_mtime_ns == local.mtime_ns:
            os.unlink(local.path)
    for current, _dirs, _files in sorted(os.walk(directory, topdown=False), key=lambda t: -len(t[0])):
        try:
            os.rmdir(current)
        except OSError:
            pass
    return not directory.exists()


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_archive(root: Path, cfg: dict, *, client, now: datetime | None = None, dry_run: bool = False,
                prune: bool = True, min_age_days: int = DEFAULT_MIN_AGE_DAYS,
                max_dirs: int = DEFAULT_MAX_DIRS, max_bytes: int = DEFAULT_MAX_BYTES,
                workers: int = DEFAULT_WORKERS, deadline_secs: float | None = None,
                clock=time.monotonic) -> dict:
    root = Path(root)
    started = clock()
    now = now or _utcnow()
    cutoff_ns = int(compute_cutoff(now, min_age_days).timestamp() * 1_000_000_000)
    evidence = root / "evidence"
    summary = {"cutoff": compute_cutoff(now, min_age_days).isoformat(), "dry_run": dry_run,
               "candidate_dirs": 0, "candidate_bytes": 0, "archived_dirs": 0, "uploaded_files": 0,
               "uploaded_bytes": 0, "pruned_dirs": 0, "pruned_bytes": 0, "errors": [],
               "skipped": {"recent": 0, "in_flight": 0, "unsafe": 0, "budget": 0, "deadline": 0}}
    if not evidence.is_dir():
        return summary  # research never ran on this host
    statuses = _settled_status(root)

    names = sorted(entry.name for entry in os.scandir(evidence))
    for name in names:
        directory = evidence / name
        info = os.lstat(directory)
        if not stat.S_ISDIR(info.st_mode) or not KEY_RE.fullmatch(name):
            summary["skipped"]["unsafe"] += 1
            continue
        status = statuses.get(name)
        if status is not None and status not in SETTLED:
            summary["skipped"]["in_flight"] += 1
            continue
        files = _walk(directory)
        if files is None:
            summary["skipped"]["unsafe"] += 1
            continue
        newest = max(f.mtime_ns for f in files) if files else info.st_mtime_ns
        if newest >= cutoff_ns:
            summary["skipped"]["recent"] += 1
            continue
        size = sum(f.size for f in files)
        if summary["candidate_dirs"] >= max_dirs or summary["candidate_bytes"] + size > max_bytes:
            summary["skipped"]["budget"] += 1
            continue
        summary["candidate_dirs"] += 1
        summary["candidate_bytes"] += size
        if dry_run:
            continue
        if deadline_secs is not None and clock() - started >= deadline_secs:
            summary["skipped"]["deadline"] += 1
            continue

        month = datetime.fromtimestamp(newest / 1e9, timezone.utc).strftime("%Y-%m")
        try:
            verified, uploaded, uploaded_bytes = _archive_dir(client, cfg, name, month, files, workers)
        except Exception as exc:  # noqa: BLE001 - one bad dir must not stop the run
            summary["errors"].append({"key": name, "error": f"{type(exc).__name__}: {exc}"[:200]})
            continue
        summary["archived_dirs"] += 1
        summary["uploaded_files"] += uploaded
        summary["uploaded_bytes"] += uploaded_bytes
        if not prune:
            continue
        current = _status_of(root, name)
        if current is not None and current not in SETTLED:
            summary["skipped"]["in_flight"] += 1
            continue
        if _prune(directory, verified):
            summary["pruned_dirs"] += 1
            summary["pruned_bytes"] += size

    summary["errors"] = summary["errors"][:SUMMARY_ERRORS]
    return summary


def write_service_health(state: str, detail: dict | None, started_at: str) -> None:
    try:
        from db.hrana_http import write_service_health_http
        write_service_health_http(SERVICE_NAME, state, started_at=started_at,
                                  finished_at=_utcnow().isoformat(), error=detail if state != "ok" else None)
    except Exception as exc:  # noqa: BLE001 - liveness must never fail the job
        print(f"  service_health heartbeat failed: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=os.environ.get("RADON_RESEARCH_DIR", DEFAULT_ROOT))
    parser.add_argument("--dry-run", action="store_true", help="list candidates; no B2 calls, no deletes")
    parser.add_argument("--no-prune", action="store_true", help="upload and verify, keep local copies")
    parser.add_argument("--min-age-days", type=int, default=DEFAULT_MIN_AGE_DAYS)
    parser.add_argument("--max-dirs", type=int, default=DEFAULT_MAX_DIRS)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--deadline-secs", type=int, default=DEFAULT_DEADLINE_SECS)
    args = parser.parse_args(argv)
    started_at = _utcnow().isoformat()

    cfg = s3_config_from_env()
    if cfg is None and not args.dry_run:
        detail = {"message": "RADON_ARCHIVE_S3_* incomplete; refusing to prune without an off-box copy"}
        print(detail["message"], file=sys.stderr)
        write_service_health("error", detail, started_at)
        return 2
    try:
        summary = run_archive(
            Path(args.root), cfg or {"bucket": "", "prefix": DEFAULT_PREFIX},
            client=None if args.dry_run else _s3_client(cfg), now=_utcnow(), dry_run=args.dry_run,
            prune=not args.no_prune, min_age_days=max(0, args.min_age_days),
            max_dirs=max(0, args.max_dirs), max_bytes=max(0, args.max_bytes),
            workers=max(1, args.workers), deadline_secs=max(0, args.deadline_secs))
    except Exception as exc:  # noqa: BLE001
        detail = {"message": f"{type(exc).__name__}: {exc}"[:300]}
        print(detail["message"], file=sys.stderr)
        if not args.dry_run:
            write_service_health("error", detail, started_at)
        return 1
    print(json.dumps(summary, sort_keys=True))
    if args.dry_run:
        return 0
    if summary["errors"]:
        write_service_health("error", {"message": f"{len(summary['errors'])} evidence dirs failed to archive",
                                       **summary}, started_at)
        return 1
    write_service_health("ok", summary, started_at)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
