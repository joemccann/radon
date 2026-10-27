"""One MarketDesk ingest cycle: sign in -> feedback -> list -> claim -> heal -> PDF -> relevance -> images -> copy ->
publish -> Dropbox outbox.

Timer entry point (radon-marketdesk.timer, every 5 minutes), run by path:
    python scripts/research/marketdesk_job.py

Idempotency: post id is a pure function of the article id, the post timestamp is the
article's own publication time, the Turso ledger (marketdesk_articles) is checked before
publish, and research_post_sources heals a crash between publish and the ledger write.
Publish happens before the Dropbox upload; the upload is an outbox that never blocks the feed. The
Dropbox path mirrors the library folder chain stored in the post provenance.

Relevance (research.marketdesk_filter) applies the old Dropbox PDF pipeline's gates (research.intake v2) before any
copy call: identity, force-included desks, triage (approved operator rules, the operator book), publisher 'unknown',
near-duplicates, then one SELECT under intake's policy. Held and dropped articles are terminal with a reason code,
mirrored to research_outcomes for the Held tab, and their PDF still goes to the Dropbox outbox. An operator
"should have published" vote re-queues the article with its note (research.feedback protocol, Turso state).

MarketDesk lists an item before it has summarised it (empty summary, MarketDesk's "Document Missing"
placeholder). Such an item is parked as ``summary_pending`` without spending an attempt, does not count
against the per-cycle cap, and fails as ``no_summary`` once it is older than MARKETDESK_SUMMARY_WAIT_HOURS.
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
from research.dropbox import DropboxError  # noqa: E402
from research.marketdesk_auth import PREVIEW_QUOTA  # noqa: E402
from research.marketdesk_copy import scrub  # noqa: E402
from research.marketdesk_filter import decide as relevance, fingerprint_index  # noqa: E402
from research.publish import stable_post_id  # noqa: E402

SERVICE = "marketdesk-research"
DROPBOX_SERVICE = "marketdesk-dropbox"
DEFAULT_MAX_PER_CYCLE = 5
DEFAULT_LOOKBACK_HOURS = 48
DEFAULT_SUMMARY_WAIT_HOURS = 6
# No new article starts after this much of the cycle; TimeoutStartSec=270 bounds the whole run.
ARTICLE_START_BUDGET_SECONDS = 150
MAX_IMAGES_STORED = 40
MAX_FIGURES = 12
# Figures are attached to the copy request (captions are read off them); larger PNGs stay extra images.
ATTACH_MAX_BYTES = 3_750_000
MAX_SUMMARY = 20
MAX_SUMMARY_CHARS = 3000  # Input-only facts; never silently trim away a trailing hedge.
MAX_TAGS = 12
BODY_MAX = 12_000
REQUEUE_BATCH = 5
RECENT_TITLES = 30
UPLOAD_BATCH = 10
ERROR_MAX_CHARS = 300
DEFAULT_PUBLISHER = "MarketDesk"
ARTICLE_URL = "https://marketdesk.ai/library/browse/"
NY = ZoneInfo("America/New_York")
NO_CREDS_MESSAGE = "Dropbox write credentials absent; uploads skipped"
FORBIDDEN_PUBLISHERS = re.compile(r"market\s*ear|zero\s*hedge|zerohedge|themarketear|tyler\s+durden", re.I)
_HEALTH_RANK = {"ok": 0, "paused": 1, "error": 2}
EXIT_STOPPED = 143  # 128+SIGTERM; the unit maps it with SuccessExitStatus=143. Not 75: radon-app-runtime run
# exits 75 itself when an orphan container survives, and that must stay a failure.


class CycleTerminated(BaseException):
    """SIGTERM control flow, outside provider-error catches. Preserve the pending lease on stop."""


class ArticleRejected(RuntimeError):
    """The article cannot be published as scraped ('no_summary'). Spends an attempt."""


class SummaryPending(RuntimeError):
    """MarketDesk has not summarised the item yet. Parked; spends no attempt and is not a failure."""


class ArticlesFailed(RuntimeError):
    """Raised after a cycle where nothing published but something failed, or an article was retired."""


class ListingGap(RuntimeError):
    """The library listing does not reach back to the newest known article: items may have been missed."""


class CycleError(RuntimeError):
    """A cycle failure whose original message was multi-line or oversized."""


@dataclass
class Deps:
    """Every collaborator of one cycle. ``default_deps()`` wires the real modules lazily."""
    store: Any
    publish: Callable
    store_asset: Callable
    read_asset: Callable
    expire_stale_held: Callable
    open_session: Callable
    list_latest: Callable
    fetch_article: Callable
    download_images: Callable
    write_copy: Callable
    certify: Callable
    writer_from_env: Callable
    marketdesk_path: Callable
    service_cycle: Callable
    record_health: Callable
    select: Callable
    book_tickers: Callable
    recent_titles: Callable
    record_outcome: Callable
    feedback_rows: Callable
    research_root: Callable
    enqueue_pdf: Callable = field(default=lambda ref: None)
    clock: Callable = field(default=time.monotonic)


def default_deps() -> Deps:
    from db import writer as db_writer
    from db.service_cycle import service_cycle
    from api.db_http import hrana_execute
    from research import book, feedback, marketdesk_auth, marketdesk_copy, marketdesk_filter, marketdesk_scrape
    from research import marketdesk_store, marketdesk_pdf_store, publish
    from research.assets import read_asset, store_asset
    from research.dropbox_writer import DropboxWriter, marketdesk_path

    def record_health(service, state, *, started_at=None, finished_at=None, error=None):
        ensure = getattr(db_writer, "ensure_no_replica_for_writers", None)
        if ensure is not None:
            ensure()
        db_writer.record_service_health(service, state, started_at=started_at, finished_at=finished_at, error=error)

    def recent_titles():
        rows = hrana_execute("SELECT title, timestamp FROM posts ORDER BY timestamp DESC LIMIT ?", (RECENT_TITLES,))
        return [{"title": r[0], "timestamp": r[1]} for r in rows]

    def research_root():
        return Path(os.environ.get("RADON_RESEARCH_DIR", "/var/lib/radon/research"))

    def certify(post, copy):
        return marketdesk_copy.certify(post, copy, audit_root=research_root())

    return Deps(
        store=marketdesk_store, publish=publish.publish, store_asset=store_asset, read_asset=read_asset,
        expire_stale_held=publish.expire_stale_held, open_session=marketdesk_auth.open_session,
        list_latest=marketdesk_scrape.list_latest,
        fetch_article=marketdesk_scrape.fetch_article,
        download_images=marketdesk_scrape.download_images, write_copy=marketdesk_copy.write_copy,
        certify=certify, writer_from_env=DropboxWriter.from_env,
        marketdesk_path=marketdesk_path,
        service_cycle=service_cycle, record_health=record_health,
        select=marketdesk_filter.ladder_select, book_tickers=book.tickers, recent_titles=recent_titles,
        record_outcome=publish.record_outcome, feedback_rows=feedback.fetch_rows, research_root=research_root,
        enqueue_pdf=marketdesk_pdf_store.enqueue,
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
    """First line only, bounded: a multi-line message may carry request detail."""
    lines = str(exc).strip().splitlines()
    return ((lines[0].strip() if lines else "") or type(exc).__name__)[:ERROR_MAX_CHARS]


def _sanitized(exc: BaseException) -> BaseException:
    safe = _safe_error(exc)
    return exc if str(exc).strip() == safe else CycleError(safe)


def _log(message: str) -> None:
    print(f"[marketdesk] {message}", file=sys.stderr)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _clean_summary(items) -> list[str]:
    """Bounded source facts for the rewrite; they are never rendered as a second bullet list."""
    cleaned = []
    for item in items or []:
        if not isinstance(item, str):
            continue
        text = re.sub(r"\s+", " ", scrub(item)).strip()
        if not text:
            continue
        if len(text) > MAX_SUMMARY_CHARS:
            raise ArticleRejected("summary_too_large")
        cleaned.append(text)
        if len(cleaned) > MAX_SUMMARY:
            raise ArticleRejected("summary_too_large")
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


def _folders(value) -> list[str]:
    return [f for f in value if isinstance(f, str) and f.strip()][:12] if isinstance(value, (list, tuple)) else []


def build_post(ref, *, publisher: str | None, sha: str,
               figure_urls: list[str], image_ids: list[str], extra_urls: list[str], copy,
               folders: list[str] | None = None, file_name: str | None = None) -> dict:
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
            "evidenceKind": "article",
            "contentHash": sha,
            "documentDate": day,
            "folderDate": day,
            "pages": [],
            "url": ARTICLE_URL + ref.article_id,
            "figures": figures,
            "extraImages": extra_urls,
            "folders": _folders(folders),
            "fileName": str(file_name or ref.title or "")[:300],
        },
    }


def _record_outcome(deps: Deps, work: dict, review: dict) -> None:
    try:
        deps.record_outcome(work, review)
    except CycleTerminated:
        raise
    except Exception as exc:  # noqa: BLE001 - the Held-tab mirror never fails an article (intake does the same)
        _log(f"outcome mirror for {work['key']} failed: {type(exc).__name__}")


def _process(ref, *, session, workdir: Path, deps: Deps, counts: dict, summary_wait_hours: int) -> None:
    store = deps.store
    post_id = stable_post_id("marketdesk:" + ref.article_id, "article")
    existing = store.post_source(post_id)
    if existing is not None:  # crash between publish and ledger write: record, never re-publish
        legacy = existing.get("evidenceKind") != "article"
        store.record_published(ref.article_id, post_id, existing.get("url") if legacy else None,
                               existing.get("contentHash") if legacy else None)
        counts["healed"] += 1
        return
    article = deps.fetch_article(session, ref)
    summary = _clean_summary(getattr(article, "summary", None))
    if not summary:  # never publish copy without MarketDesk's own summary of the PDF
        age = datetime.now(timezone.utc) - _utc(ref.published_at)
        if age.total_seconds() < summary_wait_hours * 3600:
            raise SummaryPending("summary_pending")
        raise ArticleRejected("no_summary")
    dest = workdir / ref.article_id
    dest.mkdir(parents=True, exist_ok=True)
    # Summary text is a logical source block, never a claim that a PDF page was read.
    pages = {1: "\n".join([str(getattr(article, "title", None) or ref.title), *summary])}
    created = None
    evidence = {"articleId": ref.article_id, "publishedAt": _utc(ref.published_at).isoformat(),
                "title": getattr(article, "title", None) or ref.title, "publisher": _publisher(article),
                "summary": summary, "images": list(getattr(article, "image_urls", []) or [])}
    sha = hashlib.sha256(json.dumps(evidence, sort_keys=True, ensure_ascii=False,
                                   separators=(",", ":")).encode()).hexdigest()
    title = getattr(article, "title", None) or ref.title
    folders = _folders(getattr(article, "folders", None))
    folder_date = _utc(ref.published_at).astimezone(NY).date().isoformat()
    root = Path(deps.research_root())
    decision = relevance(
        article_id=ref.article_id, title=title, folders=folders, folder_date=folder_date, pages=pages,
        created=created, summary=summary, root=root, select=deps.select, book_tickers=deps.book_tickers,
        recent=deps.recent_titles(), index=fingerprint_index(root, store.published_fingerprints()),
        note=store.note_for(ref.article_id), evidence_kind="article")
    review = decision.review
    review["document"].update(source_url=ref.url, evidence_kind="article", page_count=0)
    work = {"key": "marketdesk:" + ref.article_id, "folder_date": folder_date,
            "metadata": {"id": ref.article_id, "name": f"{title}.pdf"}}
    publisher = _publisher(article)
    if decision.outcome != "publish":
        store.record_decision(ref.article_id, decision.outcome, decision.reason_code, pdf_asset=None,
                              pdf_sha256=None, fingerprint=decision.fingerprint, publisher=decision.identity.publisher,
                              folders=folders, file_name=title)
        review["outcome"] = decision.outcome
        _record_outcome(deps, work, review)
        counts[decision.outcome] += 1
        _log(f"article {ref.article_id} {decision.outcome}: {decision.reason_code}")
        return
    stored: list[tuple[Path, str]] = []
    for path in deps.download_images(session, article, dest, limit=MAX_IMAGES_STORED):
        url = deps.store_asset(path)
        if url not in {u for _, u in stored}:
            stored.append((Path(path), url))
    stored = stored[:MAX_IMAGES_STORED]
    figures = [(path, url) for path, url in stored if path.stat().st_size <= ATTACH_MAX_BYTES][:MAX_FIGURES]
    figure_urls = [url for _, url in figures]
    extra_urls = [url for _, url in stored if url not in figure_urls]
    image_ids = [f"i{n}" for n in range(1, len(figures) + 1)]
    body_text = "\n\n".join(pages[number] for number in sorted(pages))[:BODY_MAX]
    copy = deps.write_copy(title=title, publisher=publisher, summary=summary, body_text=body_text,
                           images=[(image_id, path) for image_id, (path, _) in zip(image_ids, figures)],
                           finding=decision.candidate)
    post = build_post(ref, publisher=publisher, sha=sha,
                      figure_urls=figure_urls, image_ids=image_ids, extra_urls=extra_urls, copy=copy,
                      folders=folders, file_name=getattr(article, "title", None))
    post = deps.certify(post, copy)  # research.publish refuses copy without its Humanizer certificate
    deps.publish(post)
    store.record_published(ref.article_id, post["id"], None, None, fingerprint=decision.fingerprint,
                           publisher=decision.identity.publisher, folders=folders, file_name=title)
    review["posts"] = [post["id"]]
    _record_outcome(deps, work, review)
    counts["published"] += 1


def _apply_feedback(*, deps: Deps, counts: dict) -> None:
    """Operator votes: refresh SELECT examples and approved rules, then re-queue "should have published" items."""
    from research import feedback, learn
    try:
        rows = deps.feedback_rows()
    except CycleTerminated:
        raise
    except Exception as exc:  # noqa: BLE001 - a fetch failure changes nothing (research.feedback contract)
        _log(f"feedback skipped: {type(exc).__name__}")
        return
    try:
        learn.sync(deps.research_root(), rows)
    except CycleTerminated:
        raise
    except Exception as exc:  # noqa: BLE001 - examples and proposals are best effort; re-queue still runs
        _log(f"learn sync skipped: {type(exc).__name__}")
    counts["requeued"] += feedback.apply(deps.store, fetch=lambda: rows)


def _listing_gap(refs, newest_known: datetime | None) -> bool:
    """The oldest listed item is newer than every article the ledger knows: the page may not reach back."""
    if not refs or newest_known is None:
        return False
    return min(_utc(ref.published_at) for ref in refs) > newest_known


def _excluded(ref) -> bool:
    """The Market Ear / ZeroHedge items: radon-newsfeed already carries that feed."""
    institution = getattr(ref, "institution", None)
    return isinstance(institution, str) and bool(FORBIDDEN_PUBLISHERS.search(institution))


def _scrape(*, env, deps: Deps, workdir: Path, counts: dict, notes: dict) -> None:
    store = deps.store
    started = deps.clock()
    max_per_cycle = _positive_int(env, "MARKETDESK_MAX_PER_CYCLE", DEFAULT_MAX_PER_CYCLE)
    lookback = _positive_int(env, "MARKETDESK_LOOKBACK_HOURS", DEFAULT_LOOKBACK_HOURS)
    summary_wait = _positive_int(env, "MARKETDESK_SUMMARY_WAIT_HOURS", DEFAULT_SUMMARY_WAIT_HOURS)
    _apply_feedback(deps=deps, counts=counts)
    session = deps.open_session(env)
    refs = deps.list_latest(session)
    counts["listed"] = len(refs)
    notes["gap"] = _listing_gap(refs, store.newest_known_published_at())
    kept = [ref for ref in refs if not _excluded(ref)]
    counts["excluded"] = len(refs) - len(kept)
    discovered = store.unseen(kept, lookback_hours=lookback)
    store.discover(discovered)
    for ref in discovered:
        deps.enqueue_pdf(ref)
    todo = store.pending(limit=max(100, max_per_cycle))
    counts["unseen"] = len(todo)
    attempted = 0
    for index, ref in enumerate(todo):
        if attempted >= max_per_cycle:
            break
        if deps.clock() - started > ARTICLE_START_BUDGET_SECONDS:
            counts["deferred"] += len(todo) - index
            break
        if not store.claim(ref):
            counts["skipped"] += 1
            continue
        try:
            _process(ref, session=session, workdir=workdir, deps=deps, counts=counts,
                     summary_wait_hours=summary_wait)
            attempted += 1
        except SummaryPending as exc:
            store.park(ref.article_id, str(exc))
            counts["pending"] += 1
        except (DbHttpError, CycleTerminated):
            raise  # Turso down / killed: the lease stays and the next claim counts it
        except Exception as exc:  # noqa: BLE001 - one bad article never fails the cycle
            error = _safe_error(exc)
            if error == PREVIEW_QUOTA:  # every later PDF bounces the same way until marketdesk.ai resets it
                store.park(ref.article_id, error)
                counts["quota_deferred"] += len(todo) - index
                _log(f"preview allowance spent: {counts['quota_deferred']} articles wait for the reset")
                break
            attempted += 1
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
            source = store.post_source(str(row.get("post_id") or "")) if row.get("post_id") else None
            source = source or {}
            try:
                ledger_folders = json.loads(row.get("folders_json") or "[]")
            except (TypeError, ValueError):
                ledger_folders = []
            kwargs = {"folders": _folders(source.get("folders") or ledger_folders), **({"root": root} if root else {})}
            name = str(source.get("fileName") or row.get("file_name") or row.get("title") or "")
            remote = deps.marketdesk_path(published_at, name, article_id, **kwargs)
            try:
                result = writer.upload(local, remote)
            except DropboxError as exc:
                if getattr(exc, "status", None) != 409:
                    raise
                # Same name, different bytes (a re-issued note): keep both, the second carries the id.
                result = writer.upload(local, deps.marketdesk_path(published_at, name, article_id,
                                                                   suffix_id=True, **kwargs))
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
    counts = dict.fromkeys(("listed", "excluded", "unseen", "pending", "published", "held", "dropped", "requeued",
                            "healed", "failed", "parked", "retired", "skipped", "deferred", "quota_deferred", "uploaded",
                            "upload_skipped", "upload_failed"), 0)
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
                    if (not isinstance(failure, CycleTerminated)
                            and counts["published"] + counts["healed"] + counts["held"] + counts["dropped"]):
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
    except CycleTerminated as exc:  # a stop (deploy stop-clean): 143 + SuccessExitStatus=143 keeps it out of `failed`
        _log(f"cycle stopped: {_safe_error(exc)}")
        return EXIT_STOPPED
    except Exception as exc:  # noqa: BLE001 - service_cycle already wrote the error row
        _log(f"cycle failed: {_safe_error(exc)}")
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous)
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
