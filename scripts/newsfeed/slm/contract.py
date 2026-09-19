"""Pinned prompt contract, schema, and tag normalisation for the SLM tagger.

Radon SLM tagger: internal specialist, not a SotA replacement for open research.

BODY_CHAR_LIMIT is imported by the dataset builder and pinned against
``scripts/newsfeed/tagger.js`` (``.slice(0, 1500)``). Do not re-type 1500.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable, Sequence

HONESTY_LABEL = (
    "Radon SLM tagger: internal specialist, not a SotA replacement for open research."
)

SLM_SYSTEM = (
    "You tag Market Ear posts for the Radon dashboard. Return exactly 3 "
    "UPPERCASE tags as JSON: {\"tags\":[\"A\",\"B\",\"C\"]}. Multi-word tags "
    "are UPPERCASE-KEBAB-CASE. Prefer a specific technical signal, then the "
    "instrument, then the sector, then the theme. No prose."
)

SLM_BASE_ID = "Qwen/Qwen2.5-1.5B-Instruct"
SLM_TAGGER_RUNG = "slm-tagger"
SLM_TAGGER_NAME = "radon-slm-tagger"
SLM_CHAT_COMPLETIONS_PATH = "/v1/chat/completions"
SLM_DEFAULT_URL = "http://127.0.0.1:8331"
SLM_DEFAULT_TIMEOUT_S = 12
SLM_MAX_TOKENS = 64
SLM_TEMPERATURE = 0
BODY_CHAR_LIMIT = 1500
MIN_BODY_CHARS = 40

SLM_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["tags"],
    "additionalProperties": False,
    "properties": {
        "tags": {
            "type": "array",
            "minItems": 3,
            "maxItems": 3,
            "items": {
                "type": "string",
                "minLength": 1,
                "maxLength": 40,
                "pattern": "^[A-Z0-9&-]+$",
            },
        }
    },
}

SLM_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "slm_tags",
        "schema": SLM_JSON_SCHEMA,
        "strict": True,
    },
}

SOURCES = ["turso.posts"]

SLM_MODES = ("off", "shadow", "prefer", "primary")

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Scheme-agnostic URL stripper. The dataset module must not mention http(s)
# URLs as data sources; this pattern is PII redaction only.
URL_RE = re.compile(r"(?i)\b(?:[a-z][a-z0-9+.\-]*)://[^\s<>\"']+")

_PUNCT_WRAP = re.compile(r"""^[#"'`(\[]+|[.,!?:;"'`)\]]+$""")


def prompt_contract_sha256() -> str:
    blob = json.dumps(
        {
            "system": SLM_SYSTEM,
            "schema": SLM_JSON_SCHEMA,
            "body_char_limit": BODY_CHAR_LIMIT,
            "base": SLM_BASE_ID,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def build_user_prompt(title: str, content: str) -> str:
    """Byte-identical to ``buildUserPrompt`` in tagger.js."""
    return f"Title: {title or ''}\nBody: {(content or '')[:BODY_CHAR_LIMIT]}"


def normalise_single_tag(raw: Any) -> str | None:
    """Python twin of ``__normaliseSingleTag`` in tagger.js."""
    if not isinstance(raw, str):
        return None
    tag = raw.strip()
    if not tag:
        return None
    tag = _PUNCT_WRAP.sub("", tag)
    if not tag:
        return None
    tag = tag.upper()
    tag = re.sub(r"[\s_]+", "-", tag)
    tag = re.sub(r"[^A-Z0-9\-&]", "", tag)
    tag = re.sub(r"-+", "-", tag).strip("-")
    return tag or None


def normalise_tags(raw: Any) -> list[str]:
    """Python twin of ``__normaliseTags`` in tagger.js."""
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        tag = normalise_single_tag(item)
        if not tag or tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
    return out


def strip_pii(text: str) -> tuple[str, int, int]:
    """Strip emails and URLs. Returns (cleaned, email_count, url_count)."""
    emails = EMAIL_RE.findall(text or "")
    urls = URL_RE.findall(text or "")
    cleaned = EMAIL_RE.sub("", text or "")
    cleaned = URL_RE.sub("", cleaned)
    return cleaned, len(emails), len(urls)


def collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def taxonomy_index(tags: Iterable[str]) -> dict[str, str]:
    """case-insensitive tag -> canonical taxonomy spelling."""
    index: dict[str, str] = {}
    for tag in tags:
        if not isinstance(tag, str):
            continue
        key = tag.strip().upper()
        if key:
            index[key] = tag.strip()
    return index


def classify_slm_tags(
    obj: Any, taxonomy: Sequence[str]
) -> tuple[str, list[str] | None]:
    """HR-3 validator. Returns (code, tags_or_none).

    Codes: ``ok``, ``unparseable``, ``abstain:count``,
    ``abstain:out_of_taxonomy``, ``abstain:empty``.
    """
    if not isinstance(obj, dict):
        return "unparseable", None
    tags = obj.get("tags")
    if not isinstance(tags, list):
        return "unparseable", None
    if len(tags) == 0:
        return "abstain:empty", None
    normalised = normalise_tags(tags)
    if len(normalised) != 3:
        return "abstain:count", None
    index = taxonomy_index(taxonomy)
    unknown = [t for t in normalised if t.upper() not in index]
    if unknown:
        return "abstain:out_of_taxonomy", None
    canonical = [index[t.upper()] for t in normalised]
    return "ok", canonical
