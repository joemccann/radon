"""One MarketDesk ingest cycle: poll -> claim -> heal -> PDF/images -> copy -> publish -> Dropbox outbox -> health.

Timer entry point (radon-marketdesk.timer, every 5 minutes), run by path:
    python scripts/research/marketdesk_job.py

Idempotency: post id is a pure function of the article id, the post timestamp is the
article's own publication time, the Turso ledger (marketdesk_articles) is checked before
publish, and research_post_sources heals a crash between publish and the ledger write.
Publish happens before the Dropbox upload; the upload is an outbox that never blocks the feed.
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if sys.path and sys.path[0] and Path(sys.path[0]).resolve() == _HERE:
    sys.path.pop(0)  # run by path: do not let scripts/research/*.py shadow top-level modules
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from dataclasses import dataclass, field  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from typing import Any, Callable  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402
import argparse  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import signal  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402

from api.db_http import DbHttpError  # noqa: E402
from research.assets import URL_PREFIX  # noqa: E402
from research.marketdesk_copy import scrub, summary_problems  # noqa: E402
from research.publish import stable_post_id  # noqa: E402

SERVICE = "marketdesk-research"
DROPBOX_SERVICE = "marketdesk-dropbox"
DEFAULT_MAX_PER_CYCLE = 5
DEFAULT_LOOKBACK_HOURS = 48
# No new article starts after this much of the cycle; TimeoutStartSec=270 bounds the whole run.
ARTICLE_START_BUDGET_SECONDS = 150
MAX_IMAGES_STORED = 40
MAX_FIGURES = 12
# Figures are attached to the copy request (captions are read off them); larger PNGs stay extra images.
ATTACH_MAX_BYTES = 3_750_000
MAX_SUMMARY = 20
MAX_SUMMARY_CHARS = 500  # UTF-16 code units: the web parser measures String.length
MAX_TAGS = 12
UPLOAD_BATCH = 10
ERROR_MAX_CHARS = 300
DEFAULT_PUBLISHER = "MarketDesk"
ARTICLE_URL = "https://marketdesk.ai/library/browse/"
NY = ZoneInfo("America/New_York")
NO_CREDS_MESSAGE = "Dropbox write credentials absent; uploads skipped"
FORBIDDEN_PUBLISHERS = re.compile(r"market\s*ear|zero\s*hedge|zerohedge|themarketear|tyler\s+durden", re.I)
_HEALTH_RANK = {"ok": 0, "paused": 1, "error": 2}


class CycleTerminated(Exception):
    """SIGTERM (systemd TimeoutStartSec or a stop). Never swallowed per article: the lease stays and counts."""


class ArticleRejected(RuntimeError):
    """The article cannot be published as scraped ('no_summary'). Spends an attempt."""


class ArticlesFailed(RuntimeError):
    """Raised after a cycle where nothing published but something failed, or an article was retired."""


class ListingGap(RuntimeError):
    """The library listing does not reach back to the newest known article: items may have been missed."""


class CycleError(RuntimeError):
    """A cycle failure whose original message was multi-line or oversized (e.g. a Playwright call log)."""


@dataclass
class Deps:
    """Every collaborator of one cycle. ``default_deps()`` wires the real modules lazily."""
    store: Any
    publish: Callable
    store_asset: Callable
    read_asset: Callable
    expire_stale_held: Callable
    sync_playwright: Callable
    launch_browser: Callable
    open_session: Callable
    storage_state: Callable
    persist_storage: Callable
    list_latest: Callable
    fetch_article: Callable
    download_pdf: Callable
    download_images: Callable
    write_copy: Callable
    writer_from_env: Callable
    marketdesk_path: Callable
    service_cycle: Callable
    record_health: Callable
    clock: Callable = field(default=time.monotonic)


def default_deps() -> Deps:
    from playwright.sync_api import sync_playwright
    from db import writer as db_writer
    from db.service_cycle import service_cycle
    from research import marketdesk_auth, marketdesk_copy, marketdesk_scrape, marketdesk_store, publish
    from research.assets import read_asset, store_asset
    from research.dropbox_writer import DropboxWriter, marketdesk_path

    def record_health(service, state, *, started_at=None, finished_at=None, error=None):
        ensure = getattr(db_writer, "ensure_no_replica_for_writers", None)
        if ensure is not None:
            ensure()
        db_writer.record_service_health(service, state, started_at=started_at, finished_at=finished_at, error=error)

    return Deps(
        store=marketdesk_store, publish=publish.publish, store_asset=store_asset, read_asset=read_asset,
        expire_stale_held=publish.expire_stale_held, sync_playwright=sync_playwright,
        launch_browser=marketdesk_scrape.launch_browser, open_session=marketdesk_auth.open_session,
        storage_state=marketdesk_auth.existing_storage_state,
        persist_storage=marketdesk_auth.persist_storage_state, list_latest=marketdesk_scrape.list_latest,
        fetch_article=marketdesk_scrape.fetch_article, download_pdf=marketdesk_scrape.download_pdf,
        download_images=marketdesk_scrape.download_images, write_copy=marketdesk_copy.write_copy,
        writer_from_env=DropboxWriter.from_env, marketdesk_path=marketdesk_path,
        service_cycle=service_cycle, record_health=record_health,
    )


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _positive_int(env, name: str, default: int) -> int:
    try:
        value = int(str(env.get(name, "")).strip())
    except ValueError:
        return default
    return value if value > 0 else default


def _safe_error(exc: BaseException) -> str:
    """First line only, bounded. Playwright appends a call log (request/response headers, cookies)."""
    lines = str(exc).strip().splitlines()
    return ((lines[0].strip() if lines else "") or type(exc).__name__)[:ERROR_MAX_CHARS]


def _sanitized(exc: BaseException) -> BaseException:
    safe = _safe_error(exc)
    return exc if str(exc).strip() == safe else CycleError(safe)


def _log(message: str) -> None:
    print(f"[marketdesk] {message}", file=sys.stderr)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _utf16_cut(text: str, limit: int) -> str:
    """Longest prefix of at most ``limit`` UTF-16 code units, never splitting a surrogate pair."""
    out, used = [], 0
    for char in text:
        width = 2 if ord(char) > 0xFFFF else 1
        if used + width > limit:
            break
        out.append(char)
        used += width
    return "".join(out)


def _clean_summary(items) -> list[str]:
    """Summary bullets reach the feed verbatim, so they get the copy path's publisher/URL/phrase filter."""
    cleaned = []
    for item in items or []:
        if not isinstance(item, str):
            continue
        text = re.sub(r"\s+", " ", scrub(item)).strip()
        if not text or summary_problems(text):
            continue
        cleaned.append(_utf16_cut(text, MAX_SUMMARY_CHARS))
        if len(cleaned) == MAX_SUMMARY:
            break
    return cleaned


def _publisher(article) -> str | None:
    """Underlying publisher scraped from the article; never a Market Ear / ZeroHedge label."""
    value = getattr(article, "publisher", None)
    if not isinstance(value, str) or not value.strip() or FORBIDDEN_PUBLISHERS.search(value):
        return None
    return value.strip()[:120]


def _tags(copy_tags) -> list[str]:
    """MARKETDESK first: the feed shows four chips, and it is the source filter."""
    tags: list[str] = ["MARKETDESK"]
    for tag in copy_tags or []:
        if isinstance(tag, str) and re.fullmatch(r"[A-Z0-9][A-Z0-9&-]{0,63}", tag) and tag not in tags:
            tags.append(tag)
    return tags[:MAX_TAGS]


def build_post(ref, *, summary: list[str], publisher: str | None, pdf_url: str, sha: str,
               figure_urls: list[str], image_ids: list[str], extra_urls: list[str], copy) -> dict:
    stamp = _utc(ref.published_at)
    day = stamp.astimezone(NY).date().isoformat()
    captions = getattr(copy, "captions", None) or {}
    figures = [{"url": url, "caption": str(captions.get(image_id, ""))}
               for url, image_id in zip(figure_urls, image_ids)]
    return {
        "id": stable_post_id("marketdesk:" + ref.article_id, "article"),
        "title": copy.title,
        "content": copy.content,
        "timestamp": stamp.isoformat(),
        "images": [figure["url"] for figure in figures],
        "tags": _tags(getattr(copy, "tags", [])),
        "source": {
            "kind": "marketdesk",
            "publisher": publisher or DEFAULT_PUBLISHER,
            "articleId": ref.article_id,
            "articleUrl": ARTICLE_URL + ref.article_id,
            "contentHash": sha,
            "documentDate": day,
            "folderDate": day,
            "pages": [1],
            "url": pdf_url,
            "figures": figures,
            "summary": summary,
            "extraImages": extra_urls,
        },
    }


def _process(ref, *, page, context, workdir: Path, deps: Deps, counts: dict) -> None:
    store = deps.store
    post_id = stable_post_id("marketdesk:" + ref.article_id, "article")
    existing = store.post_source(post_id)
    if existing is not None:  # crash between publish and ledger write: record, never re-publish
        store.record_published(ref.article_id, post_id, str(existing.get("url") or ""),
                               str(existing.get("contentHash") or ""))
        counts["healed"] += 1
        return
    article = deps.fetch_article(page, ref)
    summary = _clean_summary(getattr(article, "summary", None))
    if not summary:  # summary selector drift: never publish copy written from page text alone
        raise ArticleRejected("no_summary")
    dest = workdir / ref.article_id
    dest.mkdir(parents=True, exist_ok=True)
    pdf = Path(deps.download_pdf(context, article, dest))
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    pdf_url = deps.store_asset(pdf)
    stored: list[tuple[Path, str]] = []
    for path in deps.download_images(context, article, dest, limit=MAX_IMAGES_STORED):
        url = deps.store_asset(path)
        if url not in {u for _, u in stored}:
            stored.append((Path(path), url))
    stored = stored[:MAX_IMAGES_STORED]
    figures = [(path, url) for path, url in stored if path.stat().st_size <= ATTACH_MAX_BYTES][:MAX_FIGURES]
    figure_urls = [url for _, url in figures]
    extra_urls = [url for _, url in stored if url not in figure_urls]
    image_ids = [f"i{n}" for n in range(1, len(figures) + 1)]
    publisher = _publisher(article)
    copy = deps.write_copy(title=getattr(article, "title", None) or ref.title, publisher=publisher,
                           summary=summary, body_text=getattr(article, "body_text", "") or "",
                           images=[(image_id, path) for image_id, (path, _) in zip(image_ids, figures)])
    post = build_post(ref, summary=summary, publisher=publisher, pdf_url=pdf_url, sha=sha,
                      figure_urls=figure_urls, image_ids=image_ids, extra_urls=extra_urls, copy=copy)
    deps.publish(post)
    store.record_published(ref.article_id, post["id"], pdf_url, sha)
    counts["published"] += 1


def _listing_gap(refs, newest_known: datetime | None) -> bool:
    """The oldest listed item is newer than every article the ledger knows: the page may not reach back."""
    if not refs or newest_known is None:
        return False
    return min(_utc(ref.published_at) for ref in refs) > newest_known


def _scrape(*, env, deps: Deps, workdir: Path, counts: dict, notes: dict) -> None:
    store = deps.store
    started = deps.clock()
    storage = deps.storage_state(env)
    max_per_cycle = _positive_int(env, "MARKETDESK_MAX_PER_CYCLE", DEFAULT_MAX_PER_CYCLE)
    lookback = _positive_int(env, "MARKETDESK_LOOKBACK_HOURS", DEFAULT_LOOKBACK_HOURS)
    with deps.sync_playwright() as pw:
        browser = deps.launch_browser(pw)
        try:
            context = browser.new_context(**({"storage_state": storage} if storage else {}))
            try:
                page = context.new_page()
                deps.open_session(context, page, env=env)
                refs = deps.list_latest(page)
                counts["listed"] = len(refs)
                notes["gap"] = _listing_gap(refs, store.newest_known_published_at())
                todo = store.unseen(refs, lookback_hours=lookback)[:max_per_cycle]
                counts["unseen"] = len(todo)
                for index, ref in enumerate(todo):
                    if deps.clock() - started > ARTICLE_START_BUDGET_SECONDS:
                        counts["deferred"] += len(todo) - index
                        break
                    if not store.claim(ref):
                        counts["skipped"] += 1
                        continue
                    try:
                        _process(ref, page=page, context=context, workdir=workdir, deps=deps, counts=counts)
                    except (DbHttpError, CycleTerminated):
                        raise  # Turso down / killed: the lease stays and the next claim counts it
                    except Exception as exc:  # noqa: BLE001 - one bad article never fails the cycle
                        error = _safe_error(exc)
                        notes["last_error"] = error
                        if getattr(exc, "transient", False):
                            store.park(ref.article_id, error)
                            counts["parked"] += 1
                            _log(f"article {ref.article_id} parked: {error}")
                            if str(exc) == "unauthenticated":
                                raise  # the session is gone: every later article would fail the same way
                            continue
                        attempts = store.record_failure(ref.article_id, error)
                        counts["failed"] += 1
                        if attempts >= store.MAX_ATTEMPTS:
                            counts["retired"] += 1
                        _log(f"article {ref.article_id} failed (attempt {attempts}): {error}")
                try:
                    deps.persist_storage(context, env)
                except Exception as exc:  # noqa: BLE001 - next cycle re-authenticates
                    _log(f"session save failed: {type(exc).__name__}")
            finally:
                context.close()
        finally:
            browser.close()


def _upload_outbox(*, env, deps: Deps, workdir: Path, counts: dict) -> tuple[str, dict | None]:
    """Upload published PDFs to the operator's Dropbox. Returns the marketdesk-dropbox health row."""
    store = deps.store
    try:
        writer = deps.writer_from_env(env)
    except Exception as exc:  # noqa: BLE001 - incomplete creds are an operator error, never a pause
        return "error", {"message": _safe_error(exc)}
    rows = store.pending_uploads(limit=UPLOAD_BATCH)
    if writer is None:
        for row in rows:
            if row.get("dropbox_status") is None:
                store.record_dropbox(row["article_id"], "skipped_no_creds", None)
            counts["upload_skipped"] += 1
        return "paused", {"message": NO_CREDS_MESSAGE}
    if not rows:
        return "ok", None
    try:
        writer.connect()
    except CycleTerminated:
        raise
    except Exception as exc:  # noqa: BLE001
        return "error", {"message": f"Dropbox connect failed: {_safe_error(exc)}"}
    root = getattr(writer, "root", None)
    upload_dir = workdir / "upload"
    upload_dir.mkdir(parents=True, exist_ok=True)
    failed = 0
    for row in rows:
        article_id = row["article_id"]
        try:
            asset = str(row.get("pdf_asset") or "")
            if not asset.startswith(URL_PREFIX):
                raise ValueError("ledger row has no stored PDF asset")
            local = upload_dir / f"{article_id}.pdf"
            local.write_bytes(deps.read_asset(asset[len(URL_PREFIX):].split("#", 1)[0]))
            published_at = datetime.fromisoformat(str(row["published_at"]).replace("Z", "+00:00"))
            remote = deps.marketdesk_path(published_at, row.get("title") or "", article_id,
                                          **({"root": root} if root else {}))
            result = writer.upload(local, remote)
        except (DbHttpError, CycleTerminated):
            raise
        except Exception as exc:  # noqa: BLE001 - retried next cycle
            store.record_dropbox(article_id, "failed", None, error=_safe_error(exc))
            counts["upload_failed"] += 1
            failed += 1
            _log(f"upload {article_id} failed: {_safe_error(exc)}")
            continue
        store.record_dropbox(article_id, "uploaded", result)
        counts["uploaded"] += 1
    if failed:
        noun = "upload" if failed == 1 else "uploads"
        return "error", {"message": f"{failed} {noun} failed; retried next cycle"}
    return "ok", None


def _articles_failed(counts: dict, notes: dict) -> ArticlesFailed | None:
    bad = counts["failed"] + counts["parked"]
    good = counts["published"] + counts["healed"]
    if not counts["retired"] and not (bad and not good):
        return None
    message = f"{bad} of {bad + good} articles failed"
    if counts["retired"]:
        message += f"; {counts['retired']} retired (attempts spent, never retried)"
    if notes.get("last_error"):
        message += f"; last error: {notes['last_error']}"
    return ArticlesFailed(message[:ERROR_MAX_CHARS])



def run_cycle(*, env=os.environ, deps: Deps | None = None) -> dict:
    """Outbox first (a slow scrape can never starve uploads), then the scrape, then the outbox again
    for anything just published. Health: marketdesk-dropbox gets the worse of the two outbox runs."""
    deps = deps or default_deps()
    counts = dict.fromkeys(("listed", "unseen", "published", "healed", "failed", "parked", "retired",
                            "skipped", "deferred", "uploaded", "upload_skipped", "upload_failed"), 0)
    notes: dict = {}
    workdir = Path(tempfile.mkdtemp(prefix="marketdesk-"))
    try:
        with deps.service_cycle(SERVICE, market_hours_class="continuous") as cycle:
            try:
                deps.expire_stale_held()
            except CycleTerminated:
                raise
            except Exception as exc:  # noqa: BLE001 - best effort; keeps held expiry alive after the watcher retired
                _log(f"held expiry skipped: {type(exc).__name__}")
            started = _iso_now()
            outbox: list[tuple[str, dict | None]] = []
            failure: BaseException | None = None

            def drain() -> None:
                nonlocal failure
                try:
                    outbox.append(_upload_outbox(env=env, deps=deps, workdir=workdir, counts=counts))
                except Exception as exc:  # noqa: BLE001 - recorded below; the cycle fails after health is written
                    outbox.append(("error", {"message": f"Dropbox outbox failed: {_safe_error(exc)}"}))
                    if failure is None:
                        failure = exc

            try:
                drain()
                if not isinstance(failure, CycleTerminated):
                    try:
                        _scrape(env=env, deps=deps, workdir=workdir, counts=counts, notes=notes)
                    except Exception as exc:  # noqa: BLE001 - re-raised after the outbox health row
                        failure = failure or exc
                    if not isinstance(failure, CycleTerminated) and counts["published"] + counts["healed"]:
                        drain()
            finally:
                state, error = max(outbox, key=lambda row: _HEALTH_RANK[row[0]]) if outbox else (
                    "error", {"message": "Dropbox outbox did not run"})
                try:
                    deps.record_health(DROPBOX_SERVICE, state, started_at=started,
                                       finished_at=_iso_now(), error=error)
                except Exception as exc:  # noqa: BLE001 - telemetry never masks the cycle outcome
                    _log(f"{DROPBOX_SERVICE} health write failed: {type(exc).__name__}")
            if failure is not None:
                raise _sanitized(failure) from None
            problem = _articles_failed(counts, notes)
            if problem is not None:
                raise problem
            if notes.get("gap"):
                raise ListingGap("listing_gap: the library listing does not reach the newest known article; "
                                 "items between them may have been missed")
            cycle.finished_at = _iso_now()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return counts


def _terminate(signum, frame):  # noqa: ARG001 - signal handler signature
    raise CycleTerminated("terminated by SIGTERM: the cycle exceeded its time budget or was stopped")


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description="Run one MarketDesk research ingest cycle.").parse_args([] if argv is None else argv)
    previous = signal.signal(signal.SIGTERM, _terminate)
    try:
        counts = run_cycle()
    except Exception as exc:  # noqa: BLE001 - service_cycle already wrote the error row
        _log(f"cycle failed: {_safe_error(exc)}")
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous)
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
