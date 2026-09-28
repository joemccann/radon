"""Structured IR PR descriptions. Pickup and autopush refuse placeholders.

Grok's commit body is the source of truth. A missing, empty, or placeholder
section must not become a GitHub PR. Pickup and the responder's autopush
path both call :func:`validate_ir_description` before create or edit.
"""

from __future__ import annotations

import re
from typing import Iterable

REQUIRED_SECTIONS = (
    "What broke",
    "Root cause",
    "What changed",
    "How it was verified",
    "Risk and rollback",
    "Still open",
)

SECTION_MIN_CHARS = 24
PLACEHOLDER_MARKERS = (
    "todo",
    "grok incident fix on",
)
PAGE_ID_RE = re.compile(r"\b[a-f0-9]{32}\b", re.I)
_HEADING_RE = re.compile(r"^#{1,3}\s*(.+?)\s*$")
_MD_BULLET = re.compile(r"^[-*]\s+")
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")


class IrDescriptionError(ValueError):
    """Commit or PR body is missing a required IR section."""


def _norm_heading(raw: str) -> str:
    text = raw.strip().rstrip(":.")
    text = _MD_BOLD.sub(r"\1", text)
    return " ".join(text.split())


def _heading_key(raw: str) -> str | None:
    norm = _norm_heading(raw)
    lowered = {name.lower(): name for name in REQUIRED_SECTIONS}
    return lowered.get(norm.lower())


def parse_ir_sections(text: str) -> dict[str, str]:
    """Split markdown on the required IR headings. Unknown headings are ignored."""
    sections: dict[str, list[str]] = {name: [] for name in REQUIRED_SECTIONS}
    current: str | None = None
    for raw_line in (text or "").splitlines():
        heading = _HEADING_RE.match(raw_line.strip())
        if heading:
            key = _heading_key(heading.group(1))
            if key:
                current = key
                continue
        if current:
            sections[current].append(raw_line)
    return {name: "\n".join(lines).strip() for name, lines in sections.items()}


def _collapsed(text: str) -> str:
    return " ".join((text or "").split())


def is_placeholder(text: str, *, branch: str | None = None) -> bool:
    """True when a section is empty, a TODO, or just the branch name."""
    collapsed = _collapsed(text)
    if not collapsed:
        return True
    lower = collapsed.lower()
    if any(marker in lower for marker in PLACEHOLDER_MARKERS):
        return True
    if branch:
        name = branch.strip().lower()
        slug = name.removeprefix("fix/")
        if lower in {name, slug, f"fix {slug}", f"fix: {slug}"}:
            return True
        if name and name in lower and len(collapsed) <= len(name) + 8:
            return True
    return len(collapsed) < SECTION_MIN_CHARS


def validate_ir_description(
    text: str,
    *,
    branch: str | None = None,
) -> dict[str, str]:
    """Return parsed sections or raise :class:`IrDescriptionError`."""
    if not (text or "").strip():
        raise IrDescriptionError("IR description is empty")
    sections = parse_ir_sections(text)
    missing = [name for name in REQUIRED_SECTIONS if not sections.get(name)]
    if missing:
        raise IrDescriptionError(
            "IR description missing sections: " + ", ".join(missing)
        )
    bad = [
        name
        for name in REQUIRED_SECTIONS
        if is_placeholder(sections[name], branch=branch)
    ]
    if bad:
        raise IrDescriptionError(
            "IR description has placeholder or short sections: " + ", ".join(bad)
        )
    return sections


def title_from_sections(sections: dict[str, str]) -> str:
    """First What-broke line, markdown stripped, for the PR title."""
    body = sections.get("What broke") or ""
    for line in body.splitlines():
        cleaned = _MD_BULLET.sub("", line.strip())
        cleaned = _MD_BOLD.sub(r"\1", cleaned)
        cleaned = cleaned.split(":", 1)[-1].strip() if cleaned.lower().startswith("symptom") else cleaned
        if cleaned:
            return cleaned
    return "incident-response fix"


def extract_page_id(text: str) -> str | None:
    match = PAGE_ID_RE.search(text or "")
    return match.group(0).lower() if match else None


def compose_ir_pr_body(
    sections: dict[str, str],
    *,
    page: dict | None = None,
    ci_urls: Iterable[str] | None = None,
) -> str:
    """Render the six IR sections plus optional page facts and CI links."""
    merged = dict(sections)
    extras: list[str] = []
    if page:
        page_id = page.get("page_id")
        if page_id:
            extras.append(f"Page `{page_id}`.")
        severity = page.get("severity")
        if severity:
            extras.append(f"Severity {severity}.")
        first_seen = page.get("paged_at")
        if first_seen:
            extras.append(f"First seen {first_seen}.")
        result = (page.get("result") or "").strip()
        if result:
            extras.append(f"Ledger result: {result}.")
    if extras:
        broke = merged.get("What broke") or ""
        prefix = " ".join(extras)
        merged["What broke"] = f"{prefix}\n\n{broke}".strip()
    urls = [url.strip() for url in (ci_urls or []) if url and url.startswith("http")]
    if urls:
        verified = merged.get("How it was verified") or ""
        link_lines = "\n".join(f"- {url}" for url in urls)
        merged["How it was verified"] = f"{verified}\n\nCI:\n{link_lines}".strip()
    parts = []
    for name in REQUIRED_SECTIONS:
        parts.append(f"## {name}\n\n{merged[name].strip()}\n")
    return "\n".join(parts).rstrip() + "\n"


def description_from_commit(
    commit_message: str,
    *,
    branch: str | None = None,
    page: dict | None = None,
    ci_urls: Iterable[str] | None = None,
) -> tuple[str, str]:
    """Return ``(title_summary, body)`` from a grok commit message."""
    sections = validate_ir_description(commit_message, branch=branch)
    body = compose_ir_pr_body(sections, page=page, ci_urls=ci_urls)
    validate_ir_description(body, branch=branch)
    return title_from_sections(sections), body
