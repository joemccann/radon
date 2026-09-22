"""Newsfeed connector with original research provenance and legacy media fallback."""
from __future__ import annotations

import json
import re
from typing import Iterator

from ..schema import KnowledgeDoc

SOURCE = "newsfeed"
SCOPE = "research"

_TICKER_TAG = re.compile(r"[A-Z]{1,5}$")

# Bounded id-cursor pagination: one unbounded SELECT of every post (full
# bodies + image JSON) exceeds what Turso's HTTP pipeline will forward —
# newsfeed ingest 502'd for 11 hours on 2026-07-19 while smaller sources
# converged fine. Ordering matches the old ORDER BY id exactly.
_BATCH_ROWS = 200

_BATCH_SQL = (
    "SELECT id, title, content, timestamp, tags, images, NULL FROM posts "
    "WHERE id > ? ORDER BY id LIMIT ?"
)
_RESEARCH_BATCH_SQL = (
    "SELECT p.id, p.title, p.content, p.timestamp, p.tags, p.images, s.provenance_json "
    "FROM posts p LEFT JOIN research_post_sources s ON s.post_id = p.id "
    "WHERE p.id > ? ORDER BY p.id LIMIT ?"
)


def _post_rows(db) -> Iterator[tuple]:
    # Older databases can still ingest legacy posts before migration 71.
    has_sources = bool(db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_post_sources'"
    ).fetchall())
    sql = _RESEARCH_BATCH_SQL if has_sources else _BATCH_SQL
    cursor = ""
    while True:
        batch = db.execute(sql, (cursor, _BATCH_ROWS)).fetchall()
        if not batch:
            return
        yield from batch
        cursor = batch[-1][0]


def fetch(db) -> Iterator[KnowledgeDoc]:
    for post_id, title, body, timestamp, tags_json, images_json, provenance_json in _post_rows(db):
        content = _merge_title_and_body(title, body)
        if not content:
            continue
        tags = _json_list(tags_json)
        images = _json_list(images_json)
        provenance = json.loads(provenance_json) if provenance_json else None
        if provenance is not None and not isinstance(provenance, dict):
            raise ValueError("invalid research provenance")
        yield KnowledgeDoc(
            source=SOURCE,
            scope=SCOPE,
            doc_key=post_id,
            content=content,
            title=title,
            metadata={
                "tags": tags,
                "tickers": [tag for tag in tags if _TICKER_TAG.fullmatch(str(tag))],
                "url": provenance.get("url") if provenance else (images[0] if images else None),
                **({"source": provenance} if provenance else {}),
            },
            created_at=timestamp,
            last_activity_at=timestamp,
        )


def _merge_title_and_body(title: str | None, body: str | None) -> str:
    return "\n\n".join(part for part in ((title or "").strip(), (body or "").strip()) if part)


def _json_list(raw: str | None) -> list:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []
