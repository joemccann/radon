"""One MarketDesk PDF admission window per managed cycle.

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
import random
import re
import shutil
import signal
import sys
import tempfile
from types import SimpleNamespace

from lib.checkpoint import CheckpointedJob
from research.marketdesk_auth import PREVIEW_QUOTA, MarketDeskAuthError, MarketDeskError
from research.marketdesk_pdf_policy import SelectionUnavailable
from research.marketdesk_pdf_rate import FLOOR_SECONDS, JITTER_MAX_BPS
from utils.atomic_io import atomic_save, verified_load

SERVICE = "marketdesk-pdf"
MAX_PDF_BYTES = 100 * 1024 * 1024
MIN_INTERVAL_SECONDS = FLOOR_SECONDS
BACKOFF_SECONDS = 900
PROVIDER_BACKOFF_SECONDS = 21600
# Allowance and a transient provider 5xx are already durable error rows.
# Exit 1 makes every timer fire a new oneshot Result=exit-code and a P1 page.
# not_pdf, http_404, unauthenticated, and other item deferrals still fail the oneshot.
# http_429 and http_transport keep the error row and exit 0 while the embargo holds.
ALLOWANCE_DEFERRALS = frozenset({PREVIEW_QUOTA, "download_quota"})
# A persisted embargo is already the error row. Exit 1 would page on every fire.
QUIET_DEFERRALS = ALLOWANCE_DEFERRALS | {"http_429", "http_transport"}
PROVIDER_5XX = re.compile(r"http_5\d\d\Z")
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
    """The persisted controller owns the pace. A stale env value is not a floor."""
    del env
    return MIN_INTERVAL_SECONDS


def _jitter_bps():
    return random.SystemRandom().randrange(0, JITTER_MAX_BPS + 1)


def _code(error):
    value = str(error)
    return value if isinstance(error, MarketDeskError) and re.fullmatch(r"[a-z0-9_]{1,64}", value) else (
        "unauthenticated" if isinstance(error, MarketDeskAuthError) else "download_failed")


def _deferral_code(exc):
    prefix = "PDF archive deferred: "
    message = str(exc)
    if not message.startswith(prefix):
        return ""
    code = message[len(prefix):]
    return code if re.fullmatch(r"[a-z0-9_]{1,64}", code) else ""


def _quiet_oneshot(exc):
    """True when the error row is the page and the oneshot must exit 0."""
    if str(exc).startswith("PDF archive backlog:"):
        return True
    code = _deferral_code(exc)
    return code in QUIET_DEFERRALS or PROVIDER_5XX.fullmatch(code) is not None


def _is_db_transport(exc):
    """True only for a chained Turso wire error. Message text is not evidence.

    service_cycle has already written the error row. Exit 0 lets the next
    timer retry. A statement error has no transport cause and stays fatal.
    """
    from api.db_http import DbHttpError
    from db.hrana_http import HranaHttpError, is_transport_error
    if isinstance(exc, HranaHttpError):
        return is_transport_error(exc)
    if not isinstance(exc, DbHttpError) or exc.__cause__ is None:
        return False
    return is_transport_error(exc.__cause__)


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


def _account_code(code):
    return code in {PREVIEW_QUOTA, "download_quota", "http_429", "unauthenticated"}


def _retry_after(exc):
    value = getattr(exc, "retry_after", None)
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _claim(deps, env):
    if hasattr(deps.store, "claim_batch"):
        return list(deps.store.claim_batch(now=deps.now(), jitter_bps=_jitter_bps()))
    work = deps.store.claim_next(now=deps.now(), interval_seconds=_interval(env))
    return [work] if work is not None else []


def _fetch(deps, env, work, receipts):
    """Download or reuse a verified receipt. Returns (saved, provider_success)."""
    receipt_path = receipts / (work.article_id + ".json")
    saved = _receipt(receipt_path, deps)
    if saved is not None:
        return saved, False
    temporary = Path(tempfile.mkdtemp(prefix="pdf-", dir=receipts))
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
        return saved, True
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def run_cycle(*, env=os.environ, deps=None):
    deps = deps or default_deps()
    counts = {"seeded": 0, "attached": 0, "downloaded": 0, "throttled": 0}
    with deps.service_cycle(SERVICE, market_hours_class="continuous") as cycle:
        counts["seeded"] = deps.store.seed_missing(now=deps.now())
        counts["attached"] += deps.store.attach_downloads(now=deps.now())
        try:
            batch = _claim(deps, env)
        except SelectionUnavailable:
            raise DownloadDeferred("PDF archive deferred: book_unavailable") from None
        if not batch:
            counts["throttled"] = 1
            blocked = deps.store.blocked()
            if blocked:
                raise DownloadDeferred("PDF archive deferred: " + blocked)
            breach = getattr(deps.store, "backlog_breach", lambda **_k: None)(now=deps.now())
            if breach:
                raise DownloadDeferred(breach)
            return counts
        root = deps.root()
        receipts = _receipts_root(root)
        checkpoint = CheckpointedJob(root / "jobs" / "marketdesk-pdfs", "marketdesk-pdfs")
        account = None
        fatal = None
        quiet = None
        verified = 0
        for index, work in enumerate(batch):
            try:
                # The receipt is written before the WAL/DB. If SIGKILL lands between
                # either acknowledgement, a fresh process verifies the saved bytes.
                saved, provider_success = _fetch(deps, env, work, receipts)
            except (MarketDeskError, MarketDeskAuthError, OSError, ValueError) as exc:
                code = _code(exc)
                delay = 3600 if code == "unauthenticated" else BACKOFF_SECONDS
                deps.store.retry(work, code, now=deps.now(), backoff_seconds=delay, retry_after=_retry_after(exc))
                checkpoint.record_error(work.article_id, code)
                if _account_code(code):
                    release = getattr(deps.store, "release", None)
                    if release is not None:
                        for pending in batch[index + 1:]:
                            release(pending, now=deps.now())
                    account = code
                    break
                if code in QUIET_DEFERRALS or PROVIDER_5XX.fullmatch(code):
                    quiet = quiet or code
                else:
                    fatal = fatal or code
                continue
            if not checkpoint.is_done(work.article_id):
                checkpoint.record_finding(work.article_id, saved)
            if deps.store.record_download(work, saved["asset"], saved["digest"], now=deps.now(), provider_success=provider_success):
                counts["downloaded"] += 1
                verified += int(provider_success)
                counts["attached"] += deps.store.attach_downloads(now=deps.now())
            checkpoint.set_cursor({"last_article_id": work.article_id})
            checkpoint.checkpoint()
        if verified and account is None and hasattr(deps.store, "note_success"):
            deps.store.note_success(now=deps.now())
        if account is not None:
            raise DownloadDeferred("PDF archive deferred: " + account)
        if fatal is not None:
            raise DownloadDeferred("PDF archive deferred: " + fatal)
        if quiet and not verified:
            raise DownloadDeferred("PDF archive deferred: " + quiet)
        breach = getattr(deps.store, "backlog_breach", lambda **_k: None)(now=deps.now())
        if breach:
            raise DownloadDeferred(breach)
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
    except DownloadDeferred as exc:
        # Only our explicit public error code is safe to print.
        print("[marketdesk-pdf] " + str(exc), file=sys.stderr)
        return 0 if _quiet_oneshot(exc) else 1
    except Exception as exc:
        if _is_db_transport(exc):
            # Cause type only. URLError text can carry a request URL.
            named = exc.__cause__ if exc.__cause__ is not None else exc
            print("[marketdesk-pdf] cycle transport non-fatal: " + type(named).__name__, file=sys.stderr)
            return 0
        print("[marketdesk-pdf] " + type(exc).__name__, file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    raise SystemExit(main())
