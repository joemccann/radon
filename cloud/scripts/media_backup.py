#!/usr/bin/env python3
"""Nightly backup of the media.radon.run static tree to Backblaze B2.

Caddy serves ``https://media.radon.run`` from
``/home/radon/radon-cloud/media`` (see cloud/caddy/Caddyfile). The
newsfeed scraper rsyncs immutable content-derived filenames into that
directory. This oneshot mirrors the tree off-box so a VPS disk loss
does not wipe newsfeed images.

Transport: S3-compatible API via boto3 (same path as portfolio cold-
archive). Default target is the existing ``radon-archive`` B2 bucket
under prefix ``media/``. Credentials default to ``RADON_ARCHIVE_S3_*``;
optional ``RADON_MEDIA_BACKUP_S3_*`` overrides endpoint/bucket/keys for a
dedicated application key; optional ``RADON_MEDIA_BACKUP_PREFIX``
overrides the object prefix only.

Fail-closed: missing credentials abort the run (unit ends failed) so a
silent unconfigured backup never looks green.

Heartbeat: ``service_health`` row ``media-backup`` on every run (ok with
file counts / bytes, error with summary). 48h freshness window —
matches ``db-backup`` / ``portfolio-archive``.

Usage::

    media_backup.py [--media-dir DIR] [--dry-run] [--max-files N]
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

SERVICE_NAME = "media-backup"
TURSO_TIMEOUT = 10
SUMMARY_CAP = 300
DEFAULT_MEDIA_DIR = Path(
    os.environ.get("RADON_MEDIA_DIR", "/home/radon/radon-cloud/media")
)
DEFAULT_PREFIX = "media/"

# Skip junk that should never be restored as public media.
_SKIP_NAME_PREFIXES = (".",)
_SKIP_SUFFIXES = (".tmp", ".part", ".download")


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested in cloud/tests/test_media_backup.py)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LocalFile:
    """One on-disk media file relative to the media root."""

    relpath: str
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class RemoteObject:
    """One remote object under the backup prefix (size only for compare)."""

    key: str
    size: int


def normalize_endpoint(url: str) -> str:
    """B2 console often copies host-only; boto3 requires a scheme."""
    ep = (url or "").strip()
    if ep and not ep.startswith(("http://", "https://")):
        return "https://" + ep
    return ep


def s3_config_from_env(env: dict[str, str] | None = None) -> dict[str, str] | None:
    """Resolve B2/S3 config for the media backup.

    Preference order per field:
      1. ``RADON_MEDIA_BACKUP_S3_*`` when set
      2. fall back to ``RADON_ARCHIVE_S3_*`` (shared radon-archive bucket)

    Prefix: ``RADON_MEDIA_BACKUP_PREFIX`` else ``media/`` (never inherits
    the portfolio ``portfolio_snapshots/`` prefix).

    Returns ``None`` when endpoint/bucket/key/secret are incomplete —
    caller must fail closed.
    """
    env = env if env is not None else dict(os.environ)

    def pick(media_key: str, archive_key: str) -> str:
        return (env.get(media_key) or env.get(archive_key) or "").strip()

    required = {
        "endpoint_url": normalize_endpoint(
            pick("RADON_MEDIA_BACKUP_S3_ENDPOINT", "RADON_ARCHIVE_S3_ENDPOINT")
        ),
        "bucket": pick("RADON_MEDIA_BACKUP_S3_BUCKET", "RADON_ARCHIVE_S3_BUCKET"),
        "access_key": pick(
            "RADON_MEDIA_BACKUP_S3_ACCESS_KEY_ID", "RADON_ARCHIVE_S3_ACCESS_KEY_ID"
        ),
        "secret_key": pick(
            "RADON_MEDIA_BACKUP_S3_SECRET_ACCESS_KEY",
            "RADON_ARCHIVE_S3_SECRET_ACCESS_KEY",
        ),
    }
    if not all(required.values()):
        return None

    region = pick("RADON_MEDIA_BACKUP_S3_REGION", "RADON_ARCHIVE_S3_REGION") or "auto"
    prefix = (env.get("RADON_MEDIA_BACKUP_PREFIX") or DEFAULT_PREFIX).strip()
    if prefix and not prefix.endswith("/"):
        prefix = prefix + "/"
    required["region"] = region
    required["prefix"] = prefix or DEFAULT_PREFIX
    return required


def should_skip_name(name: str) -> bool:
    """True for temp/hidden basenames that must not enter the backup."""
    if not name or name in (".", ".."):
        return True
    if any(name.startswith(p) for p in _SKIP_NAME_PREFIXES):
        return True
    lower = name.lower()
    return any(lower.endswith(s) for s in _SKIP_SUFFIXES)


def object_key_for(prefix: str, relpath: str) -> str:
    """Join S3 prefix with a POSIX relative path (no leading slash)."""
    clean_prefix = prefix.lstrip("/")
    if clean_prefix and not clean_prefix.endswith("/"):
        clean_prefix = clean_prefix + "/"
    rel = relpath.replace("\\", "/").lstrip("/")
    return clean_prefix + rel


def iter_local_files(media_dir: Path) -> list[LocalFile]:
    """Walk ``media_dir`` and return sorted file metadata (relative paths)."""
    if not media_dir.is_dir():
        raise FileNotFoundError(f"media dir missing or not a directory: {media_dir}")
    out: list[LocalFile] = []
    root = media_dir.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune hidden dirs in-place so walk does not descend.
        dirnames[:] = [d for d in dirnames if not should_skip_name(d)]
        for name in filenames:
            if should_skip_name(name):
                continue
            full = Path(dirpath) / name
            if not full.is_file() or full.is_symlink():
                # Skip symlinks: backup must not follow arbitrary targets.
                if full.is_symlink():
                    continue
                if not full.is_file():
                    continue
            try:
                st = full.stat()
            except OSError:
                continue
            rel = full.relative_to(root).as_posix()
            out.append(LocalFile(relpath=rel, size=st.st_size, mtime_ns=st.st_mtime_ns))
    out.sort(key=lambda f: f.relpath)
    return out


def plan_uploads(
    local: Iterable[LocalFile],
    remote_by_key: dict[str, RemoteObject],
    prefix: str,
) -> list[LocalFile]:
    """Files that are missing remotely or differ in size (re-upload).

    Size-only compare is enough for content-derived immutable media
    filenames; a rare rewrite of the same name with a new size re-uploads.
    """
    planned: list[LocalFile] = []
    for item in local:
        key = object_key_for(prefix, item.relpath)
        remote = remote_by_key.get(key)
        if remote is None or remote.size != item.size:
            planned.append(item)
    return planned


def content_type_for(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


# ---------------------------------------------------------------------------
# Side-effecting layer
# ---------------------------------------------------------------------------


def _s3_client(cfg: dict[str, str]):
    import boto3  # lazy: unit tests never import it

    return boto3.client(
        "s3",
        endpoint_url=cfg["endpoint_url"],
        aws_access_key_id=cfg["access_key"],
        aws_secret_access_key=cfg["secret_key"],
        region_name=cfg["region"],
    )


def list_remote_objects(client, bucket: str, prefix: str) -> dict[str, RemoteObject]:
    """Index every object under ``prefix`` as ``{key: RemoteObject}``."""
    out: dict[str, RemoteObject] = {}
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        resp = client.list_objects_v2(**kwargs)
        for obj in resp.get("Contents") or []:
            key = obj["Key"]
            out[key] = RemoteObject(key=key, size=int(obj["Size"]))
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
        if not token:
            break
    return out


def upload_file(client, path: Path, bucket: str, key: str) -> None:
    extra = {"ContentType": content_type_for(path)}
    client.upload_file(
        str(path),
        bucket,
        key,
        ExtraArgs=extra,
    )


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


def http_url_from_libsql(url: str) -> str:
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
    origin = http_url_from_libsql(db_url)
    if not origin or not token:
        # Media backup itself does not need Turso; missing liveness creds
        # only skip the heartbeat so a local dry-run still works.
        print("  service_health skipped: TURSO_DB_URL / TURSO_AUTH_TOKEN missing", file=sys.stderr)
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
    except Exception as exc:  # liveness write must never crash the backup
        print(f"  service_health heartbeat failed: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_backup(
    media_dir: Path,
    s3_cfg: dict[str, str],
    *,
    dry_run: bool = False,
    max_files: int | None = None,
    client=None,
) -> dict:
    """Upload local media files missing/changed in B2. Returns a summary dict."""
    local = iter_local_files(media_dir)
    if max_files is not None:
        local = local[: max(0, max_files)]

    if client is None and not dry_run:
        client = _s3_client(s3_cfg)

    prefix = s3_cfg["prefix"]
    bucket = s3_cfg["bucket"]

    if dry_run:
        # Cheap local-only plan: treat remote as empty so dry-run reports
        # every file as would-upload without needing network.
        remote: dict[str, RemoteObject] = {}
    else:
        remote = list_remote_objects(client, bucket, prefix)

    planned = plan_uploads(local, remote, prefix)
    uploaded = 0
    bytes_uploaded = 0
    for item in planned:
        if dry_run:
            uploaded += 1
            bytes_uploaded += item.size
            continue
        key = object_key_for(prefix, item.relpath)
        upload_file(client, media_dir / item.relpath, bucket, key)
        uploaded += 1
        bytes_uploaded += item.size

    local_bytes = sum(f.size for f in local)
    return {
        "media_dir": str(media_dir),
        "bucket": bucket,
        "prefix": prefix,
        "local_files": len(local),
        "local_bytes": local_bytes,
        "remote_objects": len(remote),
        "uploaded": uploaded,
        "bytes_uploaded": bytes_uploaded,
        "skipped_unchanged": len(local) - len(planned),
        "dry_run": dry_run,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--media-dir", type=Path, default=DEFAULT_MEDIA_DIR)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list local plan only; no network (remote treated empty)",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=None,
        help="cap local files processed (smoke / catch-up throttle)",
    )
    args = parser.parse_args(argv)

    started_at = datetime.now(timezone.utc).isoformat()
    t0 = time.monotonic()
    s3_cfg = s3_config_from_env()

    if s3_cfg is None:
        detail = {
            "summary": (
                "media-backup fail-closed: set RADON_ARCHIVE_S3_* "
                "(or RADON_MEDIA_BACKUP_S3_*) for B2 credentials"
            )[:SUMMARY_CAP]
        }
        print(detail["summary"], file=sys.stderr)
        write_service_health("error", detail, started_at)
        return 1

    try:
        summary = run_backup(
            args.media_dir,
            s3_cfg,
            dry_run=args.dry_run,
            max_files=args.max_files,
        )
    except Exception as exc:  # noqa: BLE001 — always heartbeat
        detail = {
            "summary": f"media-backup failed: {exc.__class__.__name__}: {exc}"[:SUMMARY_CAP]
        }
        print(detail["summary"], file=sys.stderr)
        write_service_health("error", detail, started_at)
        return 1

    summary["elapsed_s"] = round(time.monotonic() - t0, 2)
    summary["summary"] = (
        f"media {summary['local_files']} files / {summary['local_bytes']} B; "
        f"uploaded {summary['uploaded']} ({summary['bytes_uploaded']} B); "
        f"skipped {summary['skipped_unchanged']}"
    )[:SUMMARY_CAP]
    print(json.dumps({"ok": True, **summary}, indent=2))
    write_service_health("ok", summary, started_at)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
