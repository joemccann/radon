"""Shared text helpers for connectors — HTML-to-text extraction, chunking at
natural boundaries, and file-mtime timestamps. Stdlib only."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

CHUNK_CHARS = 4000

_BLANK_LINE_RUN = re.compile(r"\n\s*\n")
_INLINE_WHITESPACE = re.compile(r"\s+")


def chunk_text(text: str, limit: int = CHUNK_CHARS) -> list[str]:
    """Split text into chunks of at most `limit` chars, preferring paragraph
    boundaries, falling back to line boundaries, hard-slicing only when a
    single line exceeds the limit."""
    units = _boundable_units(text, limit)
    chunks: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}\n\n{unit}" if current else unit
        if len(candidate) > limit and current:
            chunks.append(current)
            current = unit
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def mtime_utc_iso(path: Path) -> str:
    stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return stamp.isoformat().replace("+00:00", "Z")


def extract_html_text(html: str) -> tuple[str | None, str]:
    """Return (title, body_text) with scripts/styles dropped, block elements
    separated by newlines, and whitespace normalized."""
    parser = _HtmlTextExtractor()
    parser.feed(html)
    parser.close()
    title = _INLINE_WHITESPACE.sub(" ", "".join(parser.title_parts)).strip() or None
    return title, _normalize_lines("".join(parser.body_parts))


def _boundable_units(text: str, limit: int) -> list[str]:
    units: list[str] = []
    for paragraph in _BLANK_LINE_RUN.split(text):
        paragraph = paragraph.strip("\n").rstrip()
        if not paragraph.strip():
            continue
        if len(paragraph) <= limit:
            units.append(paragraph)
            continue
        for line in paragraph.split("\n"):
            while len(line) > limit:
                units.append(line[:limit])
                line = line[limit:]
            if line.strip():
                units.append(line)
    return units


def _normalize_lines(raw: str) -> str:
    normalized: list[str] = []
    for line in raw.split("\n"):
        line = _INLINE_WHITESPACE.sub(" ", line).strip()
        if line:
            normalized.append(line)
        elif normalized and normalized[-1] != "":
            normalized.append("")
    while normalized and normalized[-1] == "":
        normalized.pop()
    return "\n".join(normalized)


class _HtmlTextExtractor(HTMLParser):
    _SKIP_TAGS = {"script", "style"}
    _BLOCK_TAGS = {
        "p", "div", "section", "article", "table", "tr", "li", "ul", "ol",
        "blockquote", "pre", "br", "hr",
        "h1", "h2", "h3", "h4", "h5", "h6",
    }
    _CELL_TAGS = {"td", "th"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self.title_parts: list[str] = []
        self.body_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self._BLOCK_TAGS:
            self.body_parts.append("\n")
        elif tag in self._CELL_TAGS:
            self.body_parts.append(" ")

    def handle_endtag(self, tag):
        if tag in self._SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag == "title":
            self._in_title = False
        elif tag in self._BLOCK_TAGS:
            self.body_parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
        else:
            self.body_parts.append(data)
