"""One separately throttled MarketDesk PDF archive attempt per managed cycle.

Articles use provider metadata/charts and never wait here. The Turso queue owns
admission/backoff across workers. Private, fsynced asset receipts and the shared
long-horizon WAL heal a kill between local storage and the Turso acknowledgement.
No guessed endpoints or allowance bypasses, and no raw provider errors.
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.pop(0)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sys
import tempfile
from types import SimpleNamespace

from lib.checkpoint import CheckpointedJob
from research.marketdesk_auth import PREVIEW_QUOTA, MarketDeskAuthError, MarketDeskError
from research.marketdesk_pdf_policy import SelectionUnavailable
from utils.atomic_io import atomic_save, verified_load

SERVICE = "marketdesk-pdf"
MAX_PDF_BYTES = 100 * 1024 * 1024
MIN_INTERVAL_SECONDS = 1800
BACKOFF_SECONDS = 900
PROVIDER_BACKOFF_SECONDS = 21600
ID = re.compile(r"[A-Za-z0-9]{8,16}\Z")
HASH = re.compile(r"[a-f0-9]{64}\Z")
ASSET_PREFIX = "/api/newsfeed/research/files/"


class DownloadDeferred(RuntimeError):
    """A durable retry exists; error health must remain visible through backoff."""


class CycleTerminated(Exception):
    """Leave the durable lease for recovery on the next eligible cycle."""


def download_pdf(session, article_id):
    """Use the existing authenticated PDF operation; preserve its provider refusal.

    The provider routes /blob through its preview file server and may exhaust
    that allowance even when its separate downloads flag is true. This worker
    does not invent another route or turn that refusal into success.
    """
    if not ID.fullmatch(article_id):
        raise MarketDeskError("bad_ref")
    # The allowance is account-wide. Do not issue a PDF request (or repeated
    # login/PDF retries) when the provider already declares it exhausted.
    if not session.previews_available():
        raise MarketDeskError(PREVIEW_QUOTA)
    from research.marketdesk_scrape import download_pdf as fetch_pdf
    with tempfile.TemporaryDirectory(prefix="marketdesk-pdf-source-") as temporary:
        path = fetch_pdf(session, SimpleNamespace(ref=SimpleNamespace(article_id=article_id)), Path(temporary))
        return path.read_bytes()


def default_deps():
    from db.service_cycle import service_cycle
    from research import assets, marketdesk_auth, marketdesk_pdf_store
    return SimpleNamespace(store=marketdesk_pdf_store, open_session=marketdesk_auth.open_session,
                           download=download_pdf, store_asset=assets.store_asset, read_asset=assets.read_asset,
                           service_cycle=service_cycle,
                           root=lambda: Path(os.environ.get("RADON_RESEARCH_DIR", "/var/lib/radon/research")),
                           now=lambda: datetime.now(timezone.utc))


def _interval(env):
    try:
        return max(MIN_INTERVAL_SECONDS, min(int(env.get("MARKETDESK_PDF_MIN_INTERVAL_SECONDS", MIN_INTERVAL_SECONDS)), 86400))
    except (ValueError, TypeError):
        return MIN_INTERVAL_SECONDS


def _code(error):
    value = str(error)
    return value if isinstance(error, MarketDeskError) and re.fullmatch(r"[a-z0-9_]{1,64}", value) else (
        "unauthenticated" if isinstance(error, MarketDeskAuthError) else "download_failed")


def _receipts_root(root):
    directory = root / "marketdesk" / "pdf-receipts"
    for path in (root, root / "marketdesk", directory):
        if path.is_symlink():
            raise ValueError("invalid PDF receipt directory")
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path, 0o700)
    return directory


def _receipt(path, deps):
    if path.is_symlink() or not path.is_file():
        return None
    try:
        if path.stat().st_size > 4096:
            return None
        result = verified_load(str(path))
        digest, asset = result.get("digest"), result.get("asset")
        if not isinstance(digest, str) or not HASH.fullmatch(digest) or asset != ASSET_PREFIX + digest + ".pdf":
            return None
        data = deps.read_asset(digest + ".pdf")
        if hashlib.sha256(data).hexdigest() != digest or not data.startswith(b"%PDF-"):
            return None
        return result
    except (ValueError, TypeError, OSError, AttributeError):
        return None


def run_cycle(*, env=os.environ, deps=None):
    deps = deps or default_deps()
    counts = {"seeded": 0, "attached": 0, "downloaded": 0, "throttled": 0}
    with deps.service_cycle(SERVICE, market_hours_class="continuous") as cycle:
        counts["seeded"] = deps.store.seed_missing(now=deps.now())
        counts["attached"] += deps.store.attach_downloads(now=deps.now())
        try:
            work = deps.store.claim_next(now=deps.now(), interval_seconds=_interval(env))
        except SelectionUnavailable:
            raise DownloadDeferred("PDF archive deferred: book_unavailable") from None
        if work is None:
            counts["throttled"] = 1
            blocked = deps.store.blocked()
            if blocked:
                raise DownloadDeferred("PDF archive deferred: " + blocked)
            return counts
        root = deps.root()
        receipts = _receipts_root(root)
        receipt_path = receipts / (work.article_id + ".json")
        checkpoint = CheckpointedJob(root / "jobs" / "marketdesk-pdfs", "marketdesk-pdfs")
        # The receipt is written before the WAL/DB. If SIGKILL lands between
        # either acknowledgement, a fresh process verifies the saved bytes.
        saved = _receipt(receipt_path, deps)
        provider_success = saved is None
        if saved is None:
            temporary = Path(tempfile.mkdtemp(prefix="pdf-", dir=receipts))
            try:
                try:
                    session = deps.open_session(env)
                    data = deps.download(session, work.article_id)
                    if not isinstance(data, bytes) or not data.startswith(b"%PDF-"):
                        raise MarketDeskError("not_pdf")
                    if len(data) > MAX_PDF_BYTES:
                        raise MarketDeskError("too_large")
                    local = temporary / (work.article_id + ".pdf")
                    with local.open("wb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.chmod(local, 0o600)
                    asset = deps.store_asset(local)
                    digest = hashlib.sha256(data).hexdigest()
                    if asset != ASSET_PREFIX + digest + ".pdf":
                        raise ValueError("stored PDF digest mismatch")
                    saved = {"asset": asset, "digest": digest}
                    atomic_save(str(receipt_path), saved)
                except (MarketDeskError, MarketDeskAuthError, OSError, ValueError) as exc:
                    code = _code(exc)
                    delay = PROVIDER_BACKOFF_SECONDS if code in {PREVIEW_QUOTA, "download_quota", "http_429"} else (3600 if code == "unauthenticated" else BACKOFF_SECONDS)
                    deps.store.retry(work, code, now=deps.now(), backoff_seconds=delay)
                    checkpoint.record_error(work.article_id, code)
                    raise DownloadDeferred("PDF archive deferred: " + code) from None
            finally:
                shutil.rmtree(temporary, ignore_errors=True)
        if not checkpoint.is_done(work.article_id):
            checkpoint.record_finding(work.article_id, saved)
        if deps.store.record_download(work, saved["asset"], saved["digest"], now=deps.now(), provider_success=provider_success):
            counts["downloaded"] = 1
            counts["attached"] += deps.store.attach_downloads(now=deps.now())
        checkpoint.set_cursor({"last_article_id": work.article_id})
        checkpoint.checkpoint()
        cycle.finished_at = deps.now().isoformat()
    return counts


def main(argv=None):
    argparse.ArgumentParser(description="Run one throttled MarketDesk PDF archive attempt.").parse_args(argv)
    def stop(signum, frame):
        raise CycleTerminated()
    previous = signal.signal(signal.SIGTERM, stop)
    try:
        print(json.dumps(run_cycle(), sort_keys=True))
        return 0
    except CycleTerminated:
        return 143
    except Exception as exc:
        # Only our explicit public error code is safe to print.
        message = str(exc) if isinstance(exc, DownloadDeferred) else type(exc).__name__
        print("[marketdesk-pdf] " + message, file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    raise SystemExit(main())
