"""Newsfeed connector — one doc per themarketear `posts` row. Content is the
headline plus body text. Posts carry no canonical source URL, so metadata.url
is the first mirrored media.radon.run image when present; tickers are the
short all-caps tags (heuristic — the table has no ticker column)."""
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
    "SELECT id, title, content, timestamp, tags, images FROM posts "
    "WHERE id > ? ORDER BY id LIMIT ?"
)


def _post_rows(db) -> Iterator[tuple]:
    cursor = ""
    while True:
        batch = db.execute(_BATCH_SQL, (cursor, _BATCH_ROWS)).fetchall()
        if not batch:
            return
        yield from batch
        cursor = batch[-1][0]


def fetch(db) -> Iterator[KnowledgeDoc]:
    for post_id, title, body, timestamp, tags_json, images_json in _post_rows(db):
        content = _merge_title_and_body(title, body)
        if not content:
            continue
        tags = _json_list(tags_json)
        images = _json_list(images_json)
        yield KnowledgeDoc(
            source=SOURCE,
            scope=SCOPE,
            doc_key=post_id,
            content=content,
            title=title,
            metadata={
                "tags": tags,
                "tickers": [tag for tag in tags if _TICKER_TAG.fullmatch(str(tag))],
                "url": images[0] if images else None,
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
