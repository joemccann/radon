#!/usr/bin/env python3
"""Daily archive of research assets and cached downloads to Backblaze B2, then prune.

``<root>/assets/<sha256>.<ext>`` is content-addressed and served live through
``research.assets.read_asset``. That reader falls back to B2 on a local miss
(bounded, SHA-256 verified), which is what makes pruning a served file safe;
``research.archive`` (evidence only) predates it and leaves assets alone.

Assets: every asset is uploaded to ``<prefix>assets/<name>`` with Content-MD5
and its SHA-256 in object metadata, then HEAD-confirmed (size and SHA-256
equal to the name). An object that already confirms is not re-read or
re-uploaded. A local copy is unlinked only when it confirmed in THIS run, its
last use (newer of mtime and atime, taken before any read) is more than
``HOT_WINDOW_DAYS`` old, and it still has the size and mtime that confirmed.

Downloads: ``<root>/downloads/*.pdf`` are Dropbox-reader caches. A file that
pending or processing work still needs (its computed download name, or the
``ingestion.pdf`` path of that work) is never touched. Otherwise a file that
is byte-identical to an asset (present locally, or confirmed in B2) is
unlinked; a unique file past the hot window is uploaded to
``<prefix>downloads/<name>``, confirmed, then unlinked. The in-flight set is
re-read right before deleting. No ``state.sqlite`` means no download pruning.

Pending references: before anything is pruned, the job loads the SHA-256 of
every file unfinished work still points at, and keeps those files whatever
their age: MarketDesk articles not yet terminal or still in the Dropbox upload
outbox (``marketdesk_store.pending_uploads``' predicate), MarketDesk PDF queue
rows not yet attached to their post, in-flight MarketDesk PDF receipts, and
pending research outbox payloads. Those units hold no B2 credentials, so they
could not read a pruned file back. If any lookup fails, the run still uploads
and confirms but prunes nothing and heartbeats error.

Runs inside the research container (``radon-app-runtime``), the only place
the private root is writable. Heartbeat: ``service_health`` row
``research-asset-archive`` (ok/error with bytes uploaded and pruned).

Usage::

    asset_archive.py [--root DIR] [--dry-run] [--no-prune] [--hot-days 30]
        [--max-bytes N] [--workers N] [--deadline-secs N]
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
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

try:
    from research import archive
    from research.assets import ASSET_RE, archive_key
except ImportError:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from research import archive
    from research.assets import ASSET_RE, archive_key

SERVICE_NAME = "research-asset-archive"
DEFAULT_ROOT = "/var/lib/radon/research"
#: Local copies stay this long after last use; older ones live only in B2 and
#: are read back on demand. 30 days of assets is ~4.2G (2026-10-31).
HOT_WINDOW_DAYS = 30
#: A download younger than this may still be mid-write by the reader.
MIN_SETTLE_SECS = 3600
SETTLED = archive.SETTLED
DOWNLOAD_RE = re.compile(r"[a-f0-9]{64}\.pdf\Z")
DIGEST_RE = re.compile(r"[a-f0-9]{64}")
ASSET_URL_RE = re.compile(r"/api/newsfeed/research/files/([a-f0-9]{64})\.(?:png|pdf|json)")
#: Unfinished MarketDesk work. The Dropbox clause is marketdesk_store.pending_uploads'
#: predicate; a PDF queue row is unfinished until attach_downloads links its post.
MARKETDESK_REFERENCES_SQL = (
    "SELECT pdf_sha256, pdf_asset FROM marketdesk_articles WHERE (pdf_sha256 IS NOT NULL OR pdf_asset IS NOT NULL) "
    "AND (status NOT IN ('published','failed','held','dropped') OR (status IN ('published','held','dropped') "
    "AND (dropbox_status IS NULL OR dropbox_status IN ('skipped_no_creds','failed')))) "
    "UNION ALL SELECT pdf_sha256, pdf_asset FROM marketdesk_pdf_queue "
    "WHERE (pdf_sha256 IS NOT NULL OR pdf_asset IS NOT NULL) AND (status!='downloaded' OR attached_post_id IS NULL)"
)
DEFAULT_WORKERS = archive.DEFAULT_WORKERS
#: Stop starting new files here so the run exits clean (and heartbeats) inside
#: the unit's TimeoutStartSec; uploads are idempotent, the next run resumes.
DEFAULT_DEADLINE_SECS = 2 * 3600
DEFAULT_MAX_BYTES = 20 * 1024 ** 3
CHUNK = archive.CHUNK
SUMMARY_ERRORS = 20
_NOATIME = getattr(os, "O_NOATIME", 0)


@dataclass(frozen=True)
class Item:
    path: Path
    name: str
    size: int
    mtime_ns: int
    last_use: float


def _item(path: Path) -> Item | None:
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
        return None
    return Item(path, path.name, info.st_size, info.st_mtime_ns, max(info.st_mtime, info.st_atime))


def _digest(path: Path) -> archive.LocalFile:
    """MD5 + SHA-256 without refreshing atime (O_NOATIME: we own the files)."""
    md5, sha = hashlib.md5(), hashlib.sha256()
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | _NOATIME)
    except PermissionError:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        for block in iter(lambda: stream.read(CHUNK), b""):
            md5.update(block)
            sha.update(block)
    return archive.LocalFile(path, path.name, info.st_size, info.st_mtime_ns,
                             base64.b64encode(md5.digest()).decode(), sha.hexdigest())


def _confirmed(client, bucket: str, key: str, size: int, sha256: str) -> bool:
    return archive._verify_object(client, bucket, key, archive.LocalFile(Path(key), key, size, 0, "", sha256))


def _unlink_if_unchanged(path: Path, size: int, mtime_ns: int) -> bool:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    if stat.S_ISREG(info.st_mode) and info.st_size == size and info.st_mtime_ns == mtime_ns:
        os.unlink(path)
        return True
    return False


def _in_flight_downloads(root: Path) -> set[str] | None:
    """Download names that pending/processing work still needs; None without state."""
    path = root / "state.sqlite"
    if not path.is_file():
        return None
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    try:
        placeholders = ",".join("?" * len(SETTLED))
        rows = db.execute(
            f"SELECT w.metadata, i.pdf FROM work w LEFT JOIN ingestion i ON i.work_key = w.key "
            f"WHERE w.status NOT IN ({placeholders})", tuple(SETTLED)).fetchall()
    finally:
        db.close()
    names: set[str] = set()
    for metadata, pdf in rows:
        if pdf:
            names.add(Path(pdf).name)
        try:
            meta = json.loads(metadata)
            names.add(hashlib.sha256((meta["path_lower"] + "\0" + meta["rev"]).encode()).hexdigest() + ".pdf")
        except (TypeError, ValueError, KeyError):
            continue
    return names


def _digests(*values) -> set[str]:
    found: set[str] = set()
    for value in values:
        if isinstance(value, str):
            if DIGEST_RE.fullmatch(value):
                found.add(value)
            found.update(ASSET_URL_RE.findall(value))
    return found


def _marketdesk_references() -> set[str]:
    """SHA-256 of every file unfinished MarketDesk work references (Turso)."""
    from api.db_http import hrana_execute
    refs: set[str] = set()
    for row in hrana_execute(MARKETDESK_REFERENCES_SQL):
        refs |= _digests(*row)
    return refs


def _local_references(root: Path) -> set[str]:
    """Pending research outbox payloads and in-flight MarketDesk PDF receipts."""
    refs: set[str] = set()
    path = root / "state.sqlite"
    if path.is_file():
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
        try:
            for (payload,) in db.execute("SELECT payload FROM outbox WHERE status='pending'"):
                refs |= _digests(payload)
        finally:
            db.close()
    receipts = root / "marketdesk" / "pdf-receipts"
    if receipts.is_dir() and not receipts.is_symlink():
        for entry in os.scandir(receipts):
            if entry.name.endswith(".json") and entry.is_file(follow_symlinks=False):
                refs |= _digests(Path(entry.path).read_text(errors="replace"))
    return refs


class _Run:
    def __init__(self, cfg, client, *, now, dry_run, prune, hot_days, max_bytes, deadline_secs, clock):
        self.cfg, self.client = cfg, client
        self.cutoff = now.timestamp() - hot_days * 86400
        self.settle = now.timestamp() - MIN_SETTLE_SECS
        self.dry_run, self.prune = dry_run, prune
        self.max_bytes, self.deadline_secs, self.clock = max_bytes, deadline_secs, clock
        self.started = clock()
        self.lock = threading.Lock()
        self.reserved = 0
        self.confirmed: set[str] = set()
        self.referenced: set[str] = set()
        self.summary = {
            "cutoff": datetime.fromtimestamp(self.cutoff, timezone.utc).isoformat(), "dry_run": dry_run,
            "assets_seen": 0, "confirmed_assets": 0, "cold_assets": 0, "pruned_assets": 0,
            "downloads_seen": 0, "duplicate_downloads": 0, "archived_downloads": 0, "pruned_downloads": 0,
            "uploaded_files": 0, "uploaded_bytes": 0, "pruned_bytes": 0, "error_count": 0, "errors": [],
            "skipped": {"hot": 0, "in_flight": 0, "referenced": 0, "unsafe": 0, "deadline": 0, "budget": 0,
                        "no_state": 0},
        }

    def count(self, field: str, amount: int = 1) -> None:
        with self.lock:
            self.summary[field] += amount

    def skip(self, reason: str) -> None:
        with self.lock:
            self.summary["skipped"][reason] += 1

    def error(self, name: str, exc: BaseException) -> None:
        with self.lock:
            self.summary["error_count"] += 1
            self.summary["errors"].append({"file": name, "error": f"{type(exc).__name__}: {exc}"[:200]})

    def out_of_time(self) -> bool:
        return self.deadline_secs is not None and self.clock() - self.started >= self.deadline_secs

    def reserve(self, size: int) -> bool:
        with self.lock:
            if self.reserved + size > self.max_bytes:
                return False
            self.reserved += size
            return True

    def ensure(self, key: str, path: Path, size: int, sha256: str,
               local: archive.LocalFile | None = None) -> bool | None:
        """True once ``key`` confirms. None when the upload budget is spent."""
        if _confirmed(self.client, self.cfg["bucket"], key, size, sha256):
            return True
        if not self.reserve(size):
            self.skip("budget")
            return None
        local = local or _digest(path)
        if local.sha256 != sha256 or local.size != size:
            raise ValueError("local bytes do not match the expected size and SHA-256")
        archive._upload(self.client, self.cfg["bucket"], key, local)
        if not archive._verify_object(self.client, self.cfg["bucket"], key, local):
            raise RuntimeError(f"verification failed for {key}")
        self.count("uploaded_files")
        self.count("uploaded_bytes", size)
        return True


def _asset_one(run: _Run, item: Item, remove: list) -> None:
    cold = item.last_use < run.cutoff
    if cold:
        run.count("cold_assets")
    if run.dry_run:
        return
    if run.out_of_time():
        run.skip("deadline")
        return
    try:
        ok = run.ensure(archive_key(run.cfg, item.name), item.path, item.size, item.name.split(".")[0])
    except Exception as exc:  # noqa: BLE001 - one bad file must not stop the run
        run.error(item.name, exc)
        return
    if not ok:
        return
    run.count("confirmed_assets")
    with run.lock:
        run.confirmed.add(item.name)
    if not cold:
        run.skip("hot")
    elif item.name.split(".")[0] in run.referenced:
        run.skip("referenced")
    elif run.prune:
        with run.lock:
            remove.append(item)


def _download_one(run: _Run, item: Item, in_flight: set[str], remove: list) -> None:
    if item.name in in_flight:
        run.skip("in_flight")
        return
    if item.last_use >= run.settle:
        run.skip("hot")
        return
    if run.dry_run:
        return
    if run.out_of_time():
        run.skip("deadline")
        return
    try:
        local = _digest(item.path)
        if local.size != item.size or local.mtime_ns != item.mtime_ns:
            raise ValueError("file changed while archiving")
        asset = local.sha256 + ".pdf"
        # An asset confirmed this run (every healthy local one) or already in B2.
        duplicate = asset in run.confirmed or _confirmed(
            run.client, run.cfg["bucket"], archive_key(run.cfg, asset), local.size, local.sha256)
        if local.sha256 in run.referenced:
            run.skip("referenced")
            return
        if duplicate:
            run.count("duplicate_downloads")
        elif item.last_use >= run.cutoff:
            run.skip("hot")
            return
        else:
            ok = run.ensure(f"{run.cfg['prefix']}downloads/{item.name}", item.path, item.size, local.sha256, local)
            if not ok:
                return
            run.count("archived_downloads")
    except Exception as exc:  # noqa: BLE001
        run.error(item.name, exc)
        return
    if run.prune:
        with run.lock:
            remove.append(item)


def _scan(directory: Path, pattern: re.Pattern, run: _Run) -> list[Item]:
    items = []
    if not directory.is_dir():
        return items
    for entry in sorted(os.scandir(directory), key=lambda e: e.name):
        item = _item(Path(entry.path)) if pattern.fullmatch(entry.name) else None
        if item is None:
            run.skip("unsafe")
            continue
        items.append(item)
    return items


def run_archive(root: Path, cfg: dict, *, client, now: datetime | None = None, dry_run: bool = False,
                prune: bool = True, hot_days: int = HOT_WINDOW_DAYS, max_bytes: int = DEFAULT_MAX_BYTES,
                workers: int = DEFAULT_WORKERS, deadline_secs: float | None = None,
                clock=time.monotonic) -> dict:
    root = Path(root)
    run = _Run(cfg, client, now=(now or datetime.now(timezone.utc)), dry_run=dry_run, prune=prune,
               hot_days=hot_days, max_bytes=max_bytes, deadline_secs=deadline_secs, clock=clock)
    summary = run.summary
    # Fail closed: an unknown reference set means nothing may be deleted.
    try:
        run.referenced = _marketdesk_references() | _local_references(root)
    except Exception as exc:  # noqa: BLE001
        run.error("pending-reference lookup", exc)
        run.prune = False

    assets = _scan(root / "assets", ASSET_RE, run)
    summary["assets_seen"] = len(assets)
    remove: list[Item] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(lambda item: _asset_one(run, item, remove), assets))
    for item in remove:
        if _unlink_if_unchanged(item.path, item.size, item.mtime_ns):
            summary["pruned_assets"] += 1
            summary["pruned_bytes"] += item.size

    downloads = _scan(root / "downloads", DOWNLOAD_RE, run)
    summary["downloads_seen"] = len(downloads)
    in_flight = _in_flight_downloads(root) if downloads else set()
    if in_flight is None:
        summary["skipped"]["no_state"] += 1
    elif downloads:
        remove = []
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            list(pool.map(lambda item: _download_one(run, item, in_flight, remove), downloads))
        # Work can be requeued while we uploaded: re-read before deleting.
        current = _in_flight_downloads(root) if remove else set()
        for item in remove:
            if current is None or item.name in current:
                summary["skipped"]["in_flight"] += 1
                continue
            if _unlink_if_unchanged(item.path, item.size, item.mtime_ns):
                summary["pruned_downloads"] += 1
                summary["pruned_bytes"] += item.size

    summary["errors"] = summary["errors"][:SUMMARY_ERRORS]
    return summary


def write_service_health(state: str, detail: dict | None, started_at: str) -> None:
    try:
        from db.hrana_http import write_service_health_http
        # Like media_backup: the ok row carries the byte counts too.
        write_service_health_http(SERVICE_NAME, state, started_at=started_at,
                                  finished_at=datetime.now(timezone.utc).isoformat(), error=detail)
    except Exception as exc:  # noqa: BLE001 - liveness must never fail the job
        print(f"  service_health heartbeat failed: {exc}", file=sys.stderr)


def _brief(summary: dict) -> dict:
    keys = ("uploaded_files", "uploaded_bytes", "pruned_assets", "pruned_downloads", "pruned_bytes",
            "confirmed_assets", "assets_seen", "downloads_seen", "duplicate_downloads", "archived_downloads",
            "error_count", "errors", "skipped")
    return {key: summary[key] for key in keys}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=os.environ.get("RADON_RESEARCH_DIR", DEFAULT_ROOT))
    parser.add_argument("--dry-run", action="store_true", help="count candidates; no B2 calls, no deletes")
    parser.add_argument("--no-prune", action="store_true", help="upload and confirm, keep local copies")
    parser.add_argument("--hot-days", type=int, default=HOT_WINDOW_DAYS)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--deadline-secs", type=int, default=DEFAULT_DEADLINE_SECS)
    args = parser.parse_args(argv)
    started_at = datetime.now(timezone.utc).isoformat()

    cfg = archive.s3_config_from_env()
    if cfg is None and not args.dry_run:
        detail = {"message": "RADON_ARCHIVE_S3_* incomplete; refusing to prune without an off-box copy"}
        print(detail["message"], file=sys.stderr)
        write_service_health("error", detail, started_at)
        return 2
    try:
        summary = run_archive(
            Path(args.root), cfg or {"bucket": "", "prefix": archive.DEFAULT_PREFIX},
            client=None if args.dry_run else archive._s3_client(cfg), dry_run=args.dry_run,
            prune=not args.no_prune, hot_days=max(1, args.hot_days), max_bytes=max(0, args.max_bytes),
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
    if summary["error_count"]:
        write_service_health("error", {"message": f"{summary['error_count']} research files failed to archive",
                                       **_brief(summary)}, started_at)
        return 1
    write_service_health("ok", _brief(summary), started_at)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
