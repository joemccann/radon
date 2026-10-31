"""Gateway-only model operations whose capabilities are not interchangeable.

The public protocol selects fixed operations, never URLs, headers or provider
credentials. Embedding spaces and native document/search schemas stay explicit.
"""
from __future__ import annotations

import base64
import math
import os
import re
import threading
from typing import Any

_LOCAL_EMBEDDER = None
_LOCAL_EMBED_LOCK = threading.Lock()


def _fields(payload: dict, allowed: set[str]) -> None:
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError("unknown or malformed operation fields")


def _texts(value: Any) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= 256:
        raise ValueError("texts must contain between 1 and 256 strings")
    if any(not isinstance(item, str) or len(item) > 200_000 for item in value):
        raise ValueError("invalid embedding text")
    if sum(map(len, value)) > 1_000_000:
        raise ValueError("embedding input exceeds operation limit")
    return value


def _bounded(value: Any, low: float, high: float, name: str) -> float:
    if type(value) not in (float, int) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"invalid {name}")
    return float(value)


def _vectors(vectors: Any, count: int, dimensions: int) -> list[list[float]]:
    if not isinstance(vectors, list) or len(vectors) != count:
        raise ValueError("embedding count mismatch")
    for vector in vectors:
        if not isinstance(vector, list) or len(vector) != dimensions:
            raise ValueError("embedding space mismatch")
        if any(type(item) not in (int, float) or not math.isfinite(item) for item in vector):
            raise ValueError("non-finite embedding")
    return vectors


def embed(payload: dict) -> dict:
    from . import _embedding_backend as backend
    _fields(payload, {"texts", "dimensions", "input_type"})
    texts = _texts(payload.get("texts"))
    dimensions = payload.get("dimensions")
    if type(dimensions) is not int or dimensions not in (384, 2048):
        raise ValueError("embedding dimensions must identify the 384 or 2048 space")
    input_type = payload.get("input_type")
    if input_type not in {"query", "passage"}:
        raise ValueError("embedding input_type must be query or passage")
    if dimensions == 2048:
        vectors = backend._nvidia_embed(texts, input_type=input_type, post=None)
        model = backend.EMBEDDING_MODEL_V2
    else:
        global _LOCAL_EMBEDDER
        with _LOCAL_EMBED_LOCK:
            if _LOCAL_EMBEDDER is None:
                _LOCAL_EMBEDDER = backend._build_embedder()
            local = _LOCAL_EMBEDDER
        if local is None:
            raise RuntimeError("local embedding model unavailable")
        vectors = local(texts)
        model = backend.EMBEDDING_MODEL
    return {"vectors": _vectors(vectors, len(texts), dimensions), "dimensions": dimensions, "model": model}


def ocr(payload: dict) -> dict:
    from . import _ocr_backend as backend
    _fields(payload, {"model", "image", "timeout_seconds"})
    model = payload.get("model")
    configured = {part.strip() for part in os.environ.get("RADON_RESEARCH_PARSE_MODELS", "").split(",") if part.strip()}
    allowed = {"nvidia/nemotron-parse", "nvidia/nemotron-parse-2.0", "nvidia/nemotron-parse-2"} | configured
    if not isinstance(model, str) or model not in allowed:
        raise ValueError("unsupported document parser model")
    encoded = payload.get("image")
    if not isinstance(encoded, str) or len(encoded) > 7_000_000:
        raise ValueError("invalid OCR image")
    raw = base64.b64decode(encoded, validate=True)
    if not raw or len(raw) > 5_000_000:
        raise ValueError("OCR image exceeds byte limit")
    timeout = _bounded(payload.get("timeout_seconds", 100), 0.1, 270, "OCR deadline")
    body = backend._request_body(model, "data:image/png;base64," + encoded)
    result = backend._post_with_retries(model, body, post=None, sleep=None,
                                       monotonic=None, jitter=None,
                                       deadline=backend._clock(None) + timeout)
    return {"payload": result}


def x_search(payload: dict) -> dict:
    from . import _x_search_backend as backend
    _fields(payload, {"account", "days"})
    account = payload.get("account")
    days = payload.get("days", 3)
    if not isinstance(account, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,15}", account):
        raise ValueError("invalid X account")
    if type(days) is not int or not 1 <= days <= 31:
        raise ValueError("days must be between 1 and 31")
    return {"payload": backend.xai_search(account, days=days)}


def forecast_available() -> bool:
    from . import _forecast_backend as backend
    return backend.is_available()


def forecast(payload: dict) -> dict:
    from . import _forecast_backend as backend
    _fields(payload, {"series", "horizon", "quantile_levels", "future_covariates", "past_covariates", "model_id", "device"})
    series = payload.get("series")
    if not isinstance(series, list) or not 8 <= len(series) <= 100_000:
        raise ValueError("invalid forecasting series length")
    for value in series:
        _bounded(value, -1e20, 1e20, "forecast value")
    horizon = payload.get("horizon")
    if type(horizon) is not int or not 1 <= horizon <= 366:
        raise ValueError("invalid forecast horizon")
    model = payload.get("model_id", backend.DEFAULT_MODEL_ID)
    if model != backend.DEFAULT_MODEL_ID:
        raise ValueError("unsupported forecasting model")
    if payload.get("device") not in (None, "cpu"):
        raise ValueError("unsupported forecasting device")
    result = backend.forecast_quantiles(series, horizon,
        quantile_levels=payload.get("quantile_levels", backend.DEFAULT_QUANTILE_LEVELS),
        future_covariates=payload.get("future_covariates"),
        past_covariates=payload.get("past_covariates"), model_id=model, device="cpu")
    return {"forecast": result.to_dict()}


def validate_credential(payload: dict) -> dict:
    from . import engine
    _fields(payload, {"provider", "candidate"})
    provider, candidate = payload.get("provider"), payload.get("candidate")
    endpoints = {"nvidia": "https://integrate.api.nvidia.com/v1/models",
                 "cerebras": "https://api.cerebras.ai/v1/models"}
    if provider not in endpoints:
        raise ValueError("unsupported credential provider")
    if not isinstance(candidate, str) or not 1 <= len(candidate) <= 4096 or "\n" in candidate or "\r" in candidate:
        raise ValueError("invalid credential candidate")
    import requests
    response = requests.get(endpoints[provider], headers={"Authorization": "Bearer " + candidate}, timeout=engine.scoped_timeout(10))
    try:
        code = response.status_code
    finally:
        response.close()
    if 200 <= code < 300:
        return {"status": "valid", "message": ""}
    if code in (401, 403):
        return {"status": "invalid", "message": f"{provider} rejected the credential (HTTP {code})"}
    return {"status": "error", "message": f"{provider} answered HTTP {code}"}


def refresh_catalog(payload: dict) -> dict:
    from . import _catalog_backend as backend
    _fields(payload, {"scan_time", "previous"})
    stamp = payload.get("scan_time")
    if stamp is not None and (not isinstance(stamp, str) or not re.fullmatch(r"[0-9TZ:.+\-]{10,40}", stamp)):
        raise ValueError("invalid catalog timestamp")
    previous = payload.get("previous", {})
    if not isinstance(previous, dict) or len(previous) > 8:
        raise ValueError("invalid previous model catalog")
    for provider, row in previous.items():
        if provider not in backend.PROVIDERS or not isinstance(row, dict):
            raise ValueError("invalid previous provider row")
        if any(not isinstance(value, str) or len(value) > 512 for value in row.values()):
            raise ValueError("invalid previous catalog values")
    stamp = stamp or backend._iso_utc_now()
    rows, failed = [], {}
    for provider in backend.PROVIDERS:
        key = backend.provider_key(provider)
        if not key:
            continue
        row, failure = backend.resolve_provider_row(provider, key, stamp, previous)
        if row is not None:
            rows.append(row)
        if failure is not None:
            failed[provider] = failure
    catalog = backend.build_output(rows=rows, scan_time=stamp, preferred=backend.preferred_provider())
    return {"catalog": catalog, "rows": rows, "failed": failed}



def clairic(payload: dict) -> dict:
    from . import _clairic_backend as backend
    _fields(payload, {"state", "folder_date", "include_publisher"})
    state = payload.get("state")
    folder = payload.get("folder_date")
    include = payload.get("include_publisher", False)
    if not isinstance(state, str) or not state.strip() or len(state) > backend.PASS_TOKENS * backend.CHARS_PER_TOKEN:
        raise ValueError("invalid Clairic document state")
    if folder is not None and (not isinstance(folder, str) or not backend._ISO_RE.fullmatch(folder)):
        raise ValueError("invalid Clairic reference date")
    if type(include) is not bool:
        raise ValueError("invalid Clairic publisher flag")
    body = {"model": "clairic", "state": state, "fields": {"report_date": backend.REPORT_DATE_FIELD}}
    if include:
        body["fields"]["publisher"] = backend.PUBLISHER_FIELD
    if folder is not None:
        body["reference_date"] = folder
    value, confidence = backend.fetch_request(body)
    return {"date": value, "confidence": confidence}


def dispatch(operation: str, payload: dict) -> dict:
    operations = {"embed": embed, "ocr": ocr, "x-search": x_search,
                  "forecast": forecast, "clairic": clairic, "validate-credential": validate_credential,
                  "refresh-catalog": refresh_catalog}
    if operation not in operations:
        raise ValueError("unsupported specialized model operation")
    return operations[operation](payload)


_INVENTORY_LOCK = threading.Lock()
_INVENTORY_METADATA: dict[str, list[dict]] = {}
_INVENTORY_FAILURES: dict[str, str] = {}
_INVENTORY_STAMP = None


def _metadata_rows(provider: str, models: list[dict], stamp: str) -> list[dict]:
    """Normalize only facts present in provider metadata; never guess contexts."""
    rows = []
    for model in models[:2000]:
        if not isinstance(model, dict):
            continue
        identifier = model.get("id")
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,255}", identifier):
            continue
        if provider == "anthropic":
            if not identifier.startswith("claude-") or model.get("type") != "model":
                continue
            native = model.get("capabilities")
            image = isinstance(native, dict) and isinstance(native.get("image_input"), dict) and native["image_input"].get("supported") is True
        else:
            if not identifier.startswith("grok-") or any(word in identifier for word in ("image", "video", "voice", "audio", "embed")):
                continue
            inputs, outputs = model.get("input_modalities"), model.get("output_modalities")
            if (inputs is not None and (not isinstance(inputs, list) or "text" not in inputs)) or (outputs is not None and (not isinstance(outputs, list) or "text" not in outputs)):
                continue
            image = isinstance(inputs, list) and "image" in inputs
        display = model.get("display_name") or model.get("name")
        row = {"id": identifier, "provider": provider, "label": display if isinstance(display, str) and len(display) <= 256 else identifier,
               "refreshedAt": stamp, "source": "provider-metadata", "capabilities": {}}
        if image:
            row["capabilities"]["images"] = True
        native_caps = model.get("capabilities")
        if isinstance(native_caps, dict):
            for source_key, output_key in (("tool_use", "tools"), ("structured_output", "structuredOutput")):
                capability = native_caps.get(source_key)
                if isinstance(capability, dict) and type(capability.get("supported")) is bool:
                    row["capabilities"][output_key] = capability["supported"]
        for key in ("max_input_tokens", "context_length", "context_window"):
            value = model.get(key)
            if type(value) is int and 0 < value <= 100_000_000:
                row["contextWindow"] = value
                break
        created = model.get("created_at")
        if isinstance(created, str) and len(created) <= 64:
            row["createdAt"] = created
        rows.append(row)
    return rows


def _codex_metadata_rows(stamp: str) -> list[dict]:
    """Read only safe capacity fields from the service-owned native CLI cache."""
    import json
    import stat
    from pathlib import Path
    folder = Path.home() / ".codex"
    path = folder / "models_cache.json"
    try:
        info = folder.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, os.getuid()} or info.st_mode & 0o022:
            return []
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid not in {0, os.getuid()} or info.st_mode & 0o022 or info.st_size > 4_000_000:
                return []
            raw = stream.read(4_000_001)
        if len(raw) > 4_000_000:
            return []
        models = json.loads(raw).get("models")
        if not isinstance(models, list):
            return []
        rows = []
        for model in models[:2000]:
            if not isinstance(model, dict):
                continue
            name = model.get("slug")
            window, percent = model.get("context_window"), model.get("effective_context_window_percent")
            if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,255}", name)
                    or type(window) is not int or not 16_384 < window <= 100_000_000
                    or type(percent) is not int or not 1 <= percent <= 100):
                continue
            rows.append({"id": name, "provider": "openai", "label": name, "refreshedAt": stamp,
                         "source": "cli-metadata", "contextWindow": window,
                         "effectiveContextWindowPercent": percent, "capabilities": {}})
        return rows
    except (OSError, ValueError, TypeError, AttributeError):
        return []


def model_inventory(*, refresh: bool = False) -> dict:
    """Cheap cached full metadata inventory, with immediately available defaults.

    Only explicit refresh performs metadata HTTP. The gateway schedules this in
    the background and after the existing catalog refresh job. Failed discovery
    retains the last successful metadata without inventing model IDs or sizes.
    ChatGPT's subscription grant has no verified model-list scope, so its
    configured default is marked as such instead of claiming discovery.
    """
    from datetime import datetime, timezone
    from . import engine, _catalog_backend as backend
    import copy
    global _INVENTORY_STAMP, _INVENTORY_FAILURES
    stamp = datetime.now(timezone.utc).isoformat()
    if refresh:
        found, failed = {}, {}
        for provider, fetch in (("anthropic", backend.fetch_anthropic_models), ("xai", backend.fetch_xai_models)):
            key = backend.provider_key(provider)
            if not key:
                continue
            try:
                with backend._provider_budget():
                    raw = fetch(key)
                if not isinstance(raw, list):
                    raise ValueError("invalid model metadata")
                found[provider] = _metadata_rows(provider, raw, stamp)
            except Exception:
                failed[provider] = "metadata_unavailable"
        with _INVENTORY_LOCK:
            _INVENTORY_METADATA.update(found)
            _INVENTORY_FAILURES = failed
            _INVENTORY_STAMP = stamp
    aliases = {"grok": "xai", "codex": "openai"}
    with _INVENTORY_LOCK:
        metadata = copy.deepcopy(_INVENTORY_METADATA)
        failed = dict(_INVENTORY_FAILURES)
        refreshed = _INVENTORY_STAMP or stamp
    rows, seen = [], set()
    default = ""
    for name in engine.wired_providers():
        provider = aliases.get(name, name)
        selected = engine._model_for(name, os.environ, kind="text", auth_kind="subscription") or ("antigravity-default" if name == "antigravity" else "")
        if not selected:
            continue
        if not default:
            default = selected
        existing = _codex_metadata_rows(stamp) if name == "codex" else metadata.get(provider, [])
        by_id = {row["id"]: row for row in existing}
        seed = {"id": selected, "label": selected, "provider": provider, "refreshedAt": refreshed, "source": "configured",
                "capabilities": {"structuredOutput": True, "tools": name in {"anthropic", "grok"}}}
        # Selected/default row first; every discovered supported model follows.
        for row in [by_id.get(selected, seed), *existing]:
            if row["id"] not in seen:
                seen.add(row["id"])
                rows.append(row)
    return {"models": rows, "defaultId": default, "source": "gateway", "refreshedAt": refreshed, "failures": failed}
