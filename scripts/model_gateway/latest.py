"""Resolve a ladder model without a pinned snapshot id.

Order, for every rung including the paused ones:

1. An env override, when set.
2. A provider alias or account default the CLI or API itself moves.
3. The model id the catalog refresh stored, which
   ``select_latest_flagship`` chose as the newest chat flagship by
   release date.

Cursor stays unwired and has no model id.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

# Empty string: omit --model and let the account default apply.
LATEST_ALIAS: dict[str, str] = {
    "antigravity": "",
    # xAI documents a family alias that tracks the newest stable Grok.
    # The catalog fixture records it on the current flagship as grok-latest.
    "grok": "grok-latest",
}

_DISCOVERED_MODELS: dict[str, str] = {}
_DISCOVERED_LOCK = threading.Lock()


def remember_discovered_models(provider: str, models: list[dict[str, Any]]) -> None:
    """Keep successful metadata resolution across failed inventory refreshes."""
    chosen = select_latest_flagship(provider, models)
    if chosen is not None:
        with _DISCOVERED_LOCK:
            _DISCOVERED_MODELS[provider] = _model_id(chosen)


_CATALOG_PROVIDER = {
    "anthropic": "anthropic",
    "grok": "xai",
    "codex": "openai",
    "nvidia": "nvidia",
    "cerebras": "cerebras",
    "cursor": "cursor",
}

_HOSTED_SKIP = re.compile(
    r"(mini|nano|luna|fast|haiku|embed|rerank|scout|instruct|image|video|voice|audio|tts|transcribe|realtime|moderation)",
    re.IGNORECASE,
)


def release_epoch(model: Mapping[str, Any]) -> float:
    """Release instant from created_at or a numeric created field. Missing is 0."""
    for key in ("created_at", "released_at", "release_date", "created"):
        value = model.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str) and value.strip():
            try:
                stamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            except ValueError:
                continue
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            return stamp.timestamp()
    return 0.0


def _model_id(model: Mapping[str, Any]) -> str:
    raw = model.get("id") if model.get("id") is not None else model.get("model_id")
    return raw.strip() if isinstance(raw, str) else ""


def _hosted_flagship(model: Mapping[str, Any], *, kind: str) -> bool:
    model_id = _model_id(model)
    if not model_id or _HOSTED_SKIP.search(model_id):
        return False
    if kind == "vision":
        inputs = model.get("input_modalities")
        if isinstance(inputs, list) and inputs and "image" not in inputs:
            return False
    return True


def select_latest_flagship(
    provider: str, models: list[dict[str, Any]], *, kind: str = "text"
) -> dict[str, Any] | None:
    """Newest chat flagship by release date.

    A later release beats a higher version number. Dated snapshots, cheap
    tiers and non-chat SKUs are the same exclusions the catalog selectors
    already apply. Ties fall through to that selector.
    """
    from . import _catalog_backend as catalog

    native = {"grok": "xai", "codex": "openai"}.get(provider, provider)
    selector = catalog.SELECTORS.get(native)
    eligible: list[dict[str, Any]] = []
    for model in models:
        if not isinstance(model, dict) or not _model_id(model):
            continue
        if selector is not None:
            chosen = selector([model])
            if chosen is not None and _model_id(chosen) == _model_id(model):
                eligible.append(model)
        elif native in {"nvidia", "cerebras"} and _hosted_flagship(model, kind=kind):
            eligible.append(model)
    if not eligible:
        return None
    newest = max(release_epoch(model) for model in eligible)
    tied = [model for model in eligible if release_epoch(model) == newest]
    if len(tied) == 1 or selector is None:
        return min(tied, key=_model_id)
    return selector(tied) or min(tied, key=_model_id)


def _catalog_path() -> Path:
    from . import _catalog_backend as catalog

    return catalog.LLM_MODELS_JSON


def stored_model_id(provider: str) -> str:
    """Model id the last catalog refresh wrote for ``provider``, or ""."""
    want = _CATALOG_PROVIDER.get(provider, provider)
    try:
        payload = json.loads(_catalog_path().read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    models = payload.get("models")
    if not isinstance(models, list):
        return ""
    for row in models:
        if not isinstance(row, dict) or row.get("provider") != want:
            continue
        model_id = row.get("model_id")
        if isinstance(model_id, str) and model_id.strip():
            return model_id.strip()
    return ""


def _codex_cache_model() -> str:
    """Newest flagship in the Codex CLI model cache, when that file exists."""
    path = Path.home() / ".codex" / "models_cache.json"
    try:
        if not path.is_file() or path.stat().st_size > 4_000_000:
            return ""
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ""
    models = raw.get("models") if isinstance(raw, dict) else None
    if not isinstance(models, list):
        return ""
    rows = []
    for model in models:
        if not isinstance(model, dict):
            continue
        slug = model.get("slug") or model.get("id")
        if not isinstance(slug, str) or not slug.strip():
            continue
        rows.append({
            "id": slug.strip(),
            "object": "model",
            "created": model.get("created") or model.get("created_at") or 0,
            "shutdown_date": model.get("shutdown_date"),
        })
    chosen = select_latest_flagship("codex", rows)
    return _model_id(chosen) if chosen else ""


def resolved_default(provider: str, *, kind: str = "text") -> str:
    """Alias, account default, or the catalog's newest flagship. Never a source pin."""
    if provider in LATEST_ALIAS:
        return LATEST_ALIAS[provider]
    if provider == "cursor":
        return ""
    native = _CATALOG_PROVIDER.get(provider, provider)
    with _DISCOVERED_LOCK:
        discovered = _DISCOVERED_MODELS.get(native, "")
    if discovered:
        return discovered
    stored = stored_model_id(provider)
    if stored:
        return stored
    if provider == "codex":
        return _codex_cache_model()
    return ""
