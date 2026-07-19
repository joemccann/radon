"""Docs connector — docs/*.md (scope research) plus tasks/lessons.md (scope
ops). Chunks on top-level headings (fence-aware, so `# comment` lines inside
```bash blocks never split a section), then ~4000-char paragraph splits."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

from ..schema import KnowledgeDoc
from ._text import CHUNK_CHARS, chunk_text, mtime_utc_iso

SOURCE = "docs"
SCOPE = "research"
LESSONS_SCOPE = "ops"

_REPO_ROOT = Path(__file__).resolve().parents[3]
DOCS_DIR = _REPO_ROOT / "docs"
LESSONS_PATH = _REPO_ROOT / "tasks" / "lessons.md"

_HEADING = re.compile(r"^(#{1,2})\s+(.*)")


def fetch(db) -> Iterator[KnowledgeDoc]:
    for path in sorted(DOCS_DIR.glob("*.md")):
        yield from _file_docs(path, doc_key=f"docs/{path.name}", scope=SCOPE)
    if LESSONS_PATH.exists():
        yield from _file_docs(LESSONS_PATH, doc_key="tasks/lessons.md", scope=LESSONS_SCOPE)


def _file_docs(path: Path, doc_key: str, scope: str) -> Iterator[KnowledgeDoc]:
    text = path.read_text(encoding="utf-8", errors="replace")
    mtime = mtime_utc_iso(path)
    chunk_ix = 0
    for heading, section in _split_sections(text):
        for piece in chunk_text(section, CHUNK_CHARS):
            yield KnowledgeDoc(
                source=SOURCE,
                scope=scope,
                doc_key=doc_key,
                chunk_ix=chunk_ix,
                title=heading or path.name,
                content=piece,
                metadata=_metadata(doc_key, heading),
                created_at=mtime,
                last_activity_at=mtime,
            )
            chunk_ix += 1


def _split_sections(text: str) -> list[tuple[str | None, str]]:
    sections: list[tuple[str | None, str]] = []
    heading: str | None = None
    buffer: list[str] = []
    in_fence = False

    def flush():
        section = "\n".join(buffer).strip()
        if section:
            sections.append((heading, section))

    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            buffer.append(line)
            continue
        match = None if in_fence else _HEADING.match(line)
        if match:
            flush()
            heading = match.group(2).strip()
            buffer = [line]
        else:
            buffer.append(line)
    flush()
    return sections


def _metadata(doc_key: str, heading: str | None) -> dict:
    metadata = {"path": doc_key}
    if heading:
        metadata["heading"] = heading
    return metadata
