"""Evals connector — substantive reports/*.html rendered to text. Tweet
share-cards and other low-text artifacts are skipped per the corpus recon;
extraction is stdlib html.parser, chunking ~4000 chars at paragraph
boundaries."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

from ..schema import KnowledgeDoc
from ._text import CHUNK_CHARS, chunk_text, extract_html_text, mtime_utc_iso

SOURCE = "evals"
SCOPE = "trading"

_REPO_ROOT = Path(__file__).resolve().parents[3]
REPORTS_DIR = _REPO_ROOT / "reports"

SKIP_PREFIXES = ("tweet-",)
MIN_TEXT_CHARS = 200

_REPORT_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def fetch(db) -> Iterator[KnowledgeDoc]:
    for path in sorted(REPORTS_DIR.glob("*.html")):
        if path.name.startswith(SKIP_PREFIXES):
            continue
        yield from _report_docs(path)


def prunable_doc_keys(doc_keys: Iterator[str] | list[str]) -> list[str]:
    """Vanished-key authority: none. reports/ is gitignored and host-local,
    and every host holds a DIFFERENT subset (the laptop generates most evals,
    the VPS a few), so no host is authoritative over the shared corpus. A
    present-but-partial reports/ on the VPS pruned 177 laptop-generated docs
    on 2026-07-19; evals never prune, stale rows are harmless."""
    return []


def _report_docs(path: Path) -> Iterator[KnowledgeDoc]:
    title, text = extract_html_text(path.read_text(encoding="utf-8", errors="replace"))
    if len(text) < MIN_TEXT_CHARS:
        return
    mtime = mtime_utc_iso(path)
    metadata = _metadata(path)
    for chunk_ix, chunk in enumerate(chunk_text(text, CHUNK_CHARS)):
        yield KnowledgeDoc(
            source=SOURCE,
            scope=SCOPE,
            doc_key=path.name,
            chunk_ix=chunk_ix,
            title=title or path.stem,
            content=chunk,
            metadata=metadata,
            created_at=mtime,
            last_activity_at=mtime,
        )


def _metadata(path: Path) -> dict:
    metadata = {"path": f"reports/{path.name}"}
    date_match = _REPORT_DATE.search(path.name)
    if date_match:
        metadata["report_date"] = date_match.group(1)
    return metadata
