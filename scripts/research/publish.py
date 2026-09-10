"""Atomic canonical posts + provenance publication; never writes posts.json."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from html import unescape
import json
import re
import unicodedata

from api.db_http import hrana_execute, hrana_transaction
from research.assets import ASSET_RE, URL_PREFIX, read_asset, store_asset


def validate_rendered_copy(title, content, publisher, figures, tags):
    """Hold invalid authored copy without changing verified claims or evidence."""
    values = [title, content, publisher, *tags, *(figure.get('caption', '') for figure in figures)]
    if any('\u2014' in unescape(value) for value in values if isinstance(value, str)):
        raise ValueError('Rendered research copy must not contain em dashes')
    if any(re.search(r'zero[\s\-–—_]*hedge', ''.join(char for char in unicodedata.normalize('NFKC', value) if unicodedata.category(char) != 'Cf'), re.I)
           for value in values if isinstance(value, str)):
        raise ValueError('Rendered research copy must attribute the original provider only')


def stable_post_id(file_id: str, finding_key: str) -> str:
    """A finding retains its ID across retries and corrected document revisions."""
    if not file_id or not finding_key:
        raise ValueError("file ID and stable finding key are required")
    return "research-" + hashlib.sha256(json.dumps([file_id, finding_key]).encode()).hexdigest()[:32]


def _asset_url(value: str, extension: str | None = None) -> str:
    if not isinstance(value, str) or not value.startswith(URL_PREFIX):
        raise ValueError("research media must use authenticated asset URLs")
    name = value[len(URL_PREFIX):].split("#", 1)[0]
    if not ASSET_RE.fullmatch(name) or (extension and not name.endswith("." + extension)):
        raise ValueError("invalid research asset URL")
    read_asset(name)  # Do not publish broken or tampered evidence.
    return value


def publish(post: dict) -> str:
    """Validate and commit one normalized post. Outbox owns retries."""
    post_id = post.get("id", "")
    if not re.fullmatch(r"research-[a-f0-9]{32,64}", post_id):
        raise ValueError("research post ID must use the reserved stable namespace")
    title, content = post.get("title"), post.get("content")
    if not isinstance(title, str) or not title.strip() or len(title) > 500:
        raise ValueError("research title required (maximum 500 characters)")
    if not isinstance(content, str) or not content.strip() or len(content) > 30000:
        raise ValueError("research body required (maximum 30000 characters)")
    stamp = datetime.fromisoformat(post["timestamp"].replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("publication timestamp must include timezone")
    source = post.get("source", {})
    if not isinstance(source, dict) or source.get("kind") != "dropbox":
        raise ValueError("Dropbox provenance required")
    for key in ("publisher", "fileId", "revision", "contentHash", "documentDate", "folderDate"):
        if not isinstance(source.get(key), str) or not source[key].strip():
            raise ValueError(f"source {key} required")
    for key in ("documentDate", "folderDate"):
        datetime.strptime(source[key], "%Y-%m-%d")
    if not re.fullmatch(r"[a-f0-9]{64}", source["contentHash"]):
        raise ValueError("source content hash must be Dropbox SHA-256")
    pages = source.get("pages")
    if not isinstance(pages, list) or not pages or any(type(p) is not int or p < 1 for p in pages):
        raise ValueError("one-based source pages required")
    _asset_url(source.get("url"), "pdf")
    if "evidenceUrl" in source:
        _asset_url(source["evidenceUrl"], "json")
        manifest_name = source["evidenceUrl"][len(URL_PREFIX):].split("#", 1)[0]
        manifest = json.loads(read_asset(manifest_name))
        pdf_name = source["url"][len(URL_PREFIX):].split("#", 1)[0]
        if manifest["source_sha256"] != pdf_name.removesuffix(".pdf"):
            raise ValueError("evidence manifest must cite the original source PDF")
        if any(page > len(manifest["pages"]) for page in pages):
            raise ValueError("source pages exceed evidence manifest")
    images = post.get("images", [])
    figures = source.get("figures", [])
    if not isinstance(images, list) or len(images) > 12 or not isinstance(figures, list):
        raise ValueError("invalid images or figures")
    for url in images:
        _asset_url(url, "png")
    for figure in figures:
        if not isinstance(figure, dict) or type(figure.get("page")) is not int or figure.get("page") not in pages or not isinstance(figure.get("caption"), str) or not figure["caption"].strip():
            raise ValueError("every figure needs a cited page and caption")
        _asset_url(figure.get("url"), "png")
    if images != [figure["url"] for figure in figures]:
        raise ValueError("images must match ordered source figures")
    tags = post.get("tags", [])
    if not isinstance(tags, list) or not tags or len(tags) > 12 or any(not isinstance(tag, str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9&-]{0,63}", tag) for tag in tags):
        raise ValueError("normalized research tags required")
    validate_rendered_copy(title, content, source["publisher"], figures, tags)
    now = datetime.now(timezone.utc).isoformat()
    sql = """INSERT INTO posts (id,title,content,timestamp,images,raw_images,tags,tags_text,tags_vision,created_at,updated_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
      title=excluded.title,content=excluded.content,timestamp=excluded.timestamp,images=excluded.images,
      tags=excluded.tags,updated_at=excluded.updated_at"""
    args = (post_id, title, content, stamp.astimezone(timezone.utc).isoformat(), json.dumps(images), "[]",
            json.dumps(tags), json.dumps(tags), "[]", now, now)
    hrana_transaction([(sql, args),
                       ("INSERT INTO research_post_sources(post_id,provenance_json) VALUES (?,?) "
                        "ON CONFLICT(post_id) DO UPDATE SET provenance_json=excluded.provenance_json",
                        (post_id, json.dumps(source, separators=(",", ":"))))])
    return post_id


def recent_posts(days: int = 90) -> list[dict]:
    """Bounded keyset reads; incomplete novelty history is an error, never silence."""
    if type(days) is not int or not 1 <= days <= 90:
        raise ValueError("novelty lookback must be 1..90 days")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    cursor, posts = "", []
    for _ in range(100):
        rows = hrana_execute("SELECT id,title,content,timestamp,tags FROM posts WHERE timestamp>=? AND id>? ORDER BY id LIMIT 200", (cutoff, cursor))
        if not rows:
            return posts
        for row in rows:
            posts.append(dict(zip(("id", "title", "content", "timestamp", "tags"), row)))
            posts[-1]["tags"] = json.loads(posts[-1]["tags"] or "[]")
        cursor = rows[-1][0]
    raise RuntimeError("novelty history exceeds bounded pagination")
