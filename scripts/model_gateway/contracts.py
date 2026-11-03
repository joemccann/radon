"""Credential-free public contracts shared by gateway clients and engines."""
from __future__ import annotations
import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

MODEL_LADDER_ORDER = (
    "antigravity",
    "anthropic",
    "grok",
    "cursor",
    "codex",
    "nvidia",
    "cerebras",
)

MODEL_LADDER_TIERS = {
    "anthropic": "subscription",
    "grok": "subscription",
    "cursor": "subscription",
    "codex": "subscription",
    "antigravity": "subscription",
    "nvidia": "nvidia",
    "cerebras": "cerebras",
    "slm-tagger": "local",
}

_SECRET_PATTERN = re.compile(
    r"(sk-[a-zA-Z0-9_-]{8,}|xai-[a-zA-Z0-9_-]{8,}|nvapi-[a-zA-Z0-9_-]{8,}|"
    r"csk-[a-zA-Z0-9_-]{8,}|Bearer\s+[a-zA-Z0-9._-]{8,})",
    re.IGNORECASE,
)

class ModelLadderExhausted(RuntimeError):
    """Every keyed ladder provider failed or none were keyed."""

class ModelResponseError(RuntimeError):
    """Non-failover response failure (truncation, oversize, malformed envelope)."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)

@dataclass(frozen=True)
class LadderResult:
    """Successful ladder completion with parsed JSON payload."""

    data: Any
    text: str
    provider: str
    model: str
    attempted: tuple[str, ...] = field(default_factory=tuple)

@dataclass(frozen=True)
class VisionResult:
    rows: list[dict[str, Any]]
    provider: str
    model: str
    attempted: tuple[str, ...] = field(default_factory=tuple)

def safe_error_message(error: Exception, *, max_len: int = 240) -> str:
    """Return a short, credential-free error string for persistence/logging."""
    msg = str(error).strip()
    if msg:
        msg = _SECRET_PATTERN.sub("[redacted]", msg)
        msg = " ".join(msg.split())
        if len(msg) > max_len:
            msg = msg[: max_len - 3] + "..."
        if msg:
            return msg
    return type(error).__name__

def parse_json_rows(text: str) -> Optional[list[dict[str, Any]]]:
    cleaned = _strip_fences(text)
    if not cleaned:
        return None
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(parsed, list):
        return None
    return parsed

def parse_json_object(text: str) -> Optional[dict[str, Any]]:
    cleaned = _strip_fences(text)
    if not cleaned:
        return None
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(parsed, dict):
        return None
    return parsed

def _strip_fences(text: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1] if "\n" in cleaned else cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()

def accept_tags_payload(obj: Any) -> bool:
    """Newsfeed text-tagger contract: JSON object with at least 3 tags."""
    tags = obj.get("tags") if isinstance(obj, dict) else None
    return isinstance(tags, list) and len(tags) >= 3

def accept_distill_payload(obj: Any) -> bool:
    """Knowledge distill contract: JSON object with a non-empty summary."""
    if not isinstance(obj, dict):
        return False
    summary = obj.get("summary")
    return isinstance(summary, str) and bool(summary.strip())


def text_from_openai(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = (choices[0] or {}).get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text") or ""))
            elif isinstance(part, str):
                parts.append(part)
        return "".join(parts)
    return ""


class ClairicError(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason

class MissingCredentials(ClairicError):
    def __init__(self):
        super().__init__("missing_credentials")
