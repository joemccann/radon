"""Shared HTTP model ladder for Radon multimodal callers.

Joe's exact order (2026-09-10). Do not leave a band until it is
exhausted or unavailable:

  1. subscription: anthropic -> grok -> cursor -> codex -> gemini
  2. nvidia (free NIM endpoints)
  3. cerebras (cheap paid, last)

Cursor has no vision/multimodal HTTP path in Radon; it is recorded as
unwired and the rest of the subscription band still runs. Only a full-cascade
miss is an ops_only / hard fail — never "top up Anthropic" while another
keyed provider remains.

Weekend CLI ladders (`RADON_WEEKEND_MODEL_LADDER`) are a sibling shape and
out of scope here unless bridged trivially.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence, Union

logger = logging.getLogger(__name__)

MODEL_LADDER_ORDER = (
    "anthropic",
    "grok",
    "cursor",
    "codex",
    "gemini",
    "nvidia",
    "cerebras",
)

MODEL_LADDER_TIERS = {
    "anthropic": "subscription",
    "grok": "subscription",
    "cursor": "subscription",
    "codex": "subscription",
    "gemini": "subscription",
    "nvidia": "nvidia",
    "cerebras": "cerebras",
}

_ANTHROPIC_KEYS = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_API_KEY", "CLAUDE_API_KEY")
_GROK_KEYS = ("XAI_API_KEY", "GROK_API_KEY")
_CODEX_KEYS = ("OPENAI_API_KEY",)
_GEMINI_KEYS = ("GEMINI_API_KEY",)
_NVIDIA_KEYS = ("NVIDIA_API_KEY",)
_CEREBRAS_KEYS = ("CEREBRAS_API_KEY",)

_CREDIT_MARKERS = (
    "credit balance",
    "credit_balance",
    "too low",
    "plans & billing",
    "billing",
    "quota",
    "insufficient_quota",
    "rate limit",
    "rate_limit",
    "overloaded",
    "capacity",
    "payment required",
    "exceeded your current quota",
)

_DEFAULT_MODELS = {
    "anthropic": "claude-haiku-4-5-20251001",
    "grok": "grok-4.6",
    "codex": "gpt-5.5",
    "gemini": "gemini-2.5-flash",
    "nvidia": "meta/llama-3.2-11b-vision-instruct",
    "cerebras": "llama-4-scout-17b-16e-instruct",
}

_DEFAULT_RESEARCH_MODELS = {
    "anthropic": "claude-sonnet-4-6",
    "grok": "grok-4.6",
    "codex": "gpt-5.5",
    "gemini": "gemini-2.5-flash",
    "nvidia": "meta/llama-3.2-11b-vision-instruct",
    "cerebras": "llama-4-scout-17b-16e-instruct",
}


class ModelLadderExhausted(RuntimeError):
    """Every keyed ladder provider failed or none were keyed."""


class LadderAbort(RuntimeError):
    """Client-side stop that must not fall through to the next rung."""


@dataclass(frozen=True)
class LadderAttempt:
    provider: str
    code: str


@dataclass(frozen=True)
class VisionExtractResult:
    rows: list[dict[str, Any]]
    provider: str
    model: str
    attempted: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class JsonMultimodalResult:
    value: dict[str, Any]
    provider: str
    model: str
    attempted: tuple[str, ...] = field(default_factory=tuple)


ImageInput = tuple[str, Union[Path, bytes, str]]


def _env_get(env: Mapping[str, str], *names: str) -> str:
    for name in names:
        value = (env.get(name) or "").strip()
        if value:
            return value
    return ""


def _first_env_model(env: Mapping[str, str], *names: str, default: str) -> str:
    return _env_get(env, *names) or default


def ladder_order(env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    src = env if env is not None else os.environ
    override = _env_get(src, "RADON_HTTP_MODEL_LADDER")
    if not override:
        return MODEL_LADDER_ORDER
    names = tuple(part.strip() for part in override.split() if part.strip())
    return names or MODEL_LADDER_ORDER


def wired_providers(env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Return ladder providers that have a real HTTP path and a key."""
    src = env if env is not None else os.environ
    wired: list[str] = []
    for name in ladder_order(src):
        if name == "cursor":
            continue
        if key_for(name, src):
            wired.append(name)
    return tuple(wired)


def key_for(name: str, env: Mapping[str, str]) -> str:
    if name == "anthropic":
        return _env_get(env, *_ANTHROPIC_KEYS)
    if name == "grok":
        return _env_get(env, *_GROK_KEYS)
    if name == "codex":
        return _env_get(env, *_CODEX_KEYS)
    if name == "gemini":
        return _env_get(env, *_GEMINI_KEYS)
    if name == "nvidia":
        return _env_get(env, *_NVIDIA_KEYS)
    if name == "cerebras":
        return _env_get(env, *_CEREBRAS_KEYS)
    return ""


def model_for(
    name: str,
    env: Mapping[str, str],
    *,
    profile: str = "vision",
) -> str:
    defaults = _DEFAULT_RESEARCH_MODELS if profile == "research" else _DEFAULT_MODELS
    if name == "anthropic":
        if profile == "research":
            return _first_env_model(
                env,
                "RADON_RESEARCH_MODEL",
                "ANTHROPIC_RESEARCH_MODEL",
                "ANTHROPIC_MODEL",
                default=defaults["anthropic"],
            )
        return _first_env_model(
            env, "ANTHROPIC_VISION_MODEL", "ANTHROPIC_MODEL", default=defaults["anthropic"]
        )
    if name == "grok":
        suffix = "_RESEARCH_MODEL" if profile == "research" else "_MODEL"
        return _first_env_model(
            env, f"XAI{suffix}", f"GROK{suffix}", default=defaults["grok"]
        )
    if name == "codex":
        suffix = "_RESEARCH_MODEL" if profile == "research" else "_MODEL"
        return _first_env_model(
            env, f"OPENAI{suffix}", default=defaults["codex"]
        )
    if name == "gemini":
        suffix = "_RESEARCH_MODEL" if profile == "research" else "_MODEL"
        return _first_env_model(
            env, f"GEMINI{suffix}", default=defaults["gemini"]
        )
    if name == "nvidia":
        suffix = "_RESEARCH_MODEL" if profile == "research" else "_MODEL"
        return _first_env_model(
            env, f"NVIDIA{suffix}", default=defaults["nvidia"]
        )
    if name == "cerebras":
        suffix = "_RESEARCH_MODEL" if profile == "research" else "_MODEL"
        return _first_env_model(
            env, f"CEREBRAS{suffix}", default=defaults["cerebras"]
        )
    return name


def classify_http_failure(status: int, body: str) -> str:
    lowered = body.lower()
    if "credit balance" in lowered or "credit_balance" in lowered or "too low" in lowered:
        return "credit_balance"
    if any(marker in lowered for marker in _CREDIT_MARKERS):
        return "quota_or_billing"
    if status in {401, 402, 403}:
        return f"http_{status}"
    if status == 429:
        return "http_429"
    if status >= 500:
        return f"http_{status}"
    if status != 200:
        return f"http_{status}"
    return "provider_error"


def is_hard_fail(status: int, body: str) -> bool:
    if status != 200:
        return True
    lowered = body.lower()
    return any(marker in lowered for marker in _CREDIT_MARKERS)


def safe_failure_message(status: int, body: str) -> str:
    code = classify_http_failure(status, body)
    if status != 200:
        return f"HTTP {status} {code}"
    return code


def parse_json_rows(text: str) -> Optional[list[dict[str, Any]]]:
    cleaned = _strip_fence(text)
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
    cleaned = _strip_fence(text)
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


def _strip_fence(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1] if "\n" in cleaned else cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()


def _text_from_anthropic(payload: dict[str, Any]) -> str:
    for block in payload.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            return str(block.get("text") or "")
    return ""


def _text_from_openai(payload: dict[str, Any]) -> str:
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


def _text_from_gemini(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        return ""
    parts = ((candidates[0] or {}).get("content") or {}).get("parts") or []
    return "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict))


def _load_image_bytes(source: Union[Path, bytes, str]) -> bytes:
    if isinstance(source, bytes):
        return source
    path = Path(source)
    return path.read_bytes()


def _anthropic_vision_body(model: str, b64: str, prompt: str) -> dict[str, Any]:
    return {
        "model": model,
        "max_tokens": 4096,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    }


def _anthropic_multimodal_body(
    model: str,
    images: Sequence[ImageInput],
    instruction: str,
    *,
    system: str | None,
    max_tokens: int,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    for label, source in images:
        raw = _load_image_bytes(source)
        b64 = base64.b64encode(raw).decode("utf-8")
        content.append({"type": "text", "text": label})
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": b64,
                },
            }
        )
    content.append({"type": "text", "text": instruction})
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": content}],
    }
    if system:
        body["system"] = system
    return body


def _openai_vision_body(model: str, b64: str, prompt: str) -> dict[str, Any]:
    return {
        "model": model,
        "max_tokens": 4096,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    }


def _openai_multimodal_body(
    model: str,
    images: Sequence[ImageInput],
    instruction: str,
    *,
    system: str | None,
    max_tokens: int,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    for label, source in images:
        raw = _load_image_bytes(source)
        b64 = base64.b64encode(raw).decode("utf-8")
        content.append({"type": "text", "text": label})
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            }
        )
    content.append({"type": "text", "text": instruction})
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": content})
    return {"model": model, "max_tokens": max_tokens, "messages": messages}


def _gemini_vision_body(model: str, b64: str, prompt: str) -> tuple[str, dict[str, Any]]:
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent"
    )
    body = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"inline_data": {"mime_type": "image/png", "data": b64}},
                    {"text": prompt},
                ],
            }
        ],
        "generationConfig": {"maxOutputTokens": 4096},
    }
    return url, body


def _gemini_multimodal_body(
    model: str,
    images: Sequence[ImageInput],
    instruction: str,
    *,
    system: str | None,
    max_tokens: int,
) -> tuple[str, dict[str, Any]]:
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent"
    )
    parts: list[dict[str, Any]] = []
    if system:
        parts.append({"text": system})
    for label, source in images:
        raw = _load_image_bytes(source)
        b64 = base64.b64encode(raw).decode("utf-8")
        parts.append({"text": label})
        parts.append({"inline_data": {"mime_type": "image/png", "data": b64}})
    parts.append({"text": instruction})
    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    return url, body


def _default_post(url: str, *, headers: dict[str, str], json: dict[str, Any], timeout: float):
    import httpx

    return httpx.post(url, headers=headers, json=json, timeout=timeout)


def _request(
    post: Callable[..., Any],
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    *,
    timeout: float = 60.0,
) -> tuple[int, str, Any]:
    try:
        resp = post(url, headers=headers, json=body, timeout=timeout)
    except LadderAbort:
        raise
    except Exception as exc:  # noqa: BLE001 — network is a hard provider fail
        raise RuntimeError(f"network:{type(exc).__name__}") from exc
    text = getattr(resp, "text", "") or ""
    try:
        payload = resp.json()
    except Exception:
        payload = None
    return int(getattr(resp, "status_code", 0) or 0), text, payload


def _call_vision_provider(
    name: str,
    api_key: str,
    model: str,
    b64: str,
    prompt: str,
    post: Callable[..., Any],
) -> tuple[int, str, Any]:
    if name == "anthropic":
        return _request(
            post,
            "https://api.anthropic.com/v1/messages",
            {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            _anthropic_vision_body(model, b64, prompt),
        )
    if name == "grok":
        return _request(
            post,
            "https://api.x.ai/v1/chat/completions",
            {
                "authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
            _openai_vision_body(model, b64, prompt),
        )
    if name == "codex":
        return _request(
            post,
            "https://api.openai.com/v1/chat/completions",
            {
                "authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
            _openai_vision_body(model, b64, prompt),
        )
    if name == "gemini":
        url, body = _gemini_vision_body(model, b64, prompt)
        return _request(
            post,
            f"{url}?key={api_key}",
            {"content-type": "application/json"},
            body,
        )
    if name == "nvidia":
        return _request(
            post,
            "https://integrate.api.nvidia.com/v1/chat/completions",
            {
                "authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
            _openai_vision_body(model, b64, prompt),
        )
    if name == "cerebras":
        return _request(
            post,
            "https://api.cerebras.ai/v1/chat/completions",
            {
                "authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
            _openai_vision_body(model, b64, prompt),
        )
    raise RuntimeError(f"{name}:unwired")


def _call_multimodal_provider(
    name: str,
    api_key: str,
    model: str,
    images: Sequence[ImageInput],
    instruction: str,
    *,
    system: str | None,
    max_tokens: int,
    post: Callable[..., Any],
    timeout: float,
) -> tuple[int, str, Any]:
    if name == "anthropic":
        return _request(
            post,
            "https://api.anthropic.com/v1/messages",
            {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            _anthropic_multimodal_body(
                model, images, instruction, system=system, max_tokens=max_tokens
            ),
            timeout=timeout,
        )
    if name == "grok":
        return _request(
            post,
            "https://api.x.ai/v1/chat/completions",
            {
                "authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
            _openai_multimodal_body(
                model, images, instruction, system=system, max_tokens=max_tokens
            ),
            timeout=timeout,
        )
    if name == "codex":
        return _request(
            post,
            "https://api.openai.com/v1/chat/completions",
            {
                "authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
            _openai_multimodal_body(
                model, images, instruction, system=system, max_tokens=max_tokens
            ),
            timeout=timeout,
        )
    if name == "gemini":
        url, body = _gemini_multimodal_body(
            model, images, instruction, system=system, max_tokens=max_tokens
        )
        return _request(
            post,
            f"{url}?key={api_key}",
            {"content-type": "application/json"},
            body,
            timeout=timeout,
        )
    if name == "nvidia":
        return _request(
            post,
            "https://integrate.api.nvidia.com/v1/chat/completions",
            {
                "authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
            _openai_multimodal_body(
                model, images, instruction, system=system, max_tokens=max_tokens
            ),
            timeout=timeout,
        )
    if name == "cerebras":
        return _request(
            post,
            "https://api.cerebras.ai/v1/chat/completions",
            {
                "authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
            _openai_multimodal_body(
                model, images, instruction, system=system, max_tokens=max_tokens
            ),
            timeout=timeout,
        )
    raise RuntimeError(f"{name}:unwired")


def _extract_text(name: str, payload: dict[str, Any]) -> str:
    extractor = {
        "anthropic": _text_from_anthropic,
        "gemini": _text_from_gemini,
    }.get(name, _text_from_openai)
    return extractor(payload)


def _exhausted_message(
    attempted: list[str],
    skipped: list[str],
    *,
    purpose: str,
) -> str:
    tried = " ".join(attempted) or "none"
    skip = " ".join(skipped) or "none"
    order = " -> ".join(ladder_order())
    if not attempted:
        return (
            f"Model ladder exhausted: no keyed {purpose} provider. "
            f"order={order}; skipped={skip}."
        )
    return (
        f"Model ladder exhausted after trying every keyed provider. "
        f"tried={tried}; skipped={skip}."
    )


def run_vision_extract_ladder(
    png_bytes: bytes,
    prompt: str,
    *,
    env: Mapping[str, str] | None = None,
    post: Callable[..., Any] | None = None,
    log_prefix: str = "vision",
) -> VisionExtractResult:
    src = env if env is not None else os.environ
    sender = post or _default_post
    b64 = base64.b64encode(png_bytes).decode("utf-8")
    attempted: list[str] = []
    skipped: list[str] = []

    for name in ladder_order(src):
        if name == "cursor":
            skipped.append("cursor:unwired")
            logger.info("%s skip provider=cursor reason=unwired", log_prefix)
            continue
        api_key = key_for(name, src)
        if not api_key:
            skipped.append(f"{name}:no_key")
            continue
        model = model_for(name, src, profile="vision")
        try:
            status, raw, payload = _call_vision_provider(
                name, api_key, model, b64, prompt, sender
            )
        except RuntimeError as exc:
            code = str(exc) or "network"
            attempted.append(f"{name}:{code}")
            logger.warning("%s provider=%s failed %s", log_prefix, name, code)
            continue

        body_text = raw if isinstance(raw, str) else ""
        if payload is None or is_hard_fail(status, body_text):
            code = classify_http_failure(status, body_text)
            attempted.append(f"{name}:{code}")
            logger.warning(
                "%s provider=%s model=%s failed %s", log_prefix, name, model, code
            )
            continue

        text = _extract_text(name, payload if isinstance(payload, dict) else {})
        rows = parse_json_rows(text)
        if rows is None:
            attempted.append(f"{name}:unparseable")
            logger.warning(
                "%s provider=%s model=%s unparseable", log_prefix, name, model
            )
            continue

        logger.info(
            "%s won provider=%s model=%s rows=%d", log_prefix, name, model, len(rows)
        )
        return VisionExtractResult(
            rows=rows,
            provider=name,
            model=model,
            attempted=tuple(attempted + [f"{name}:ok"]),
        )

    raise ModelLadderExhausted(_exhausted_message(attempted, skipped, purpose="vision"))


def run_json_multimodal_ladder(
    instruction: str,
    images: Sequence[ImageInput] = (),
    *,
    system: str | None = None,
    env: Mapping[str, str] | None = None,
    post: Callable[..., Any] | None = None,
    max_tokens: int = 6000,
    timeout: float = 120.0,
    validate_anthropic_stop: Callable[[dict[str, Any]], str | None] | None = None,
    log_prefix: str = "multimodal",
) -> JsonMultimodalResult:
    src = env if env is not None else os.environ
    sender = post or _default_post
    attempted: list[str] = []
    skipped: list[str] = []

    for name in ladder_order(src):
        if name == "cursor":
            skipped.append("cursor:unwired")
            logger.info("%s skip provider=cursor reason=unwired", log_prefix)
            continue
        api_key = key_for(name, src)
        if not api_key:
            skipped.append(f"{name}:no_key")
            continue
        model = model_for(name, src, profile="research")
        try:
            status, raw, payload = _call_multimodal_provider(
                name,
                api_key,
                model,
                images,
                instruction,
                system=system,
                max_tokens=max_tokens,
                post=sender,
                timeout=timeout,
            )
        except RuntimeError as exc:
            code = str(exc) or "network"
            attempted.append(f"{name}:{code}")
            logger.warning("%s provider=%s failed %s", log_prefix, name, code)
            continue

        body_text = raw if isinstance(raw, str) else ""
        if payload is None or is_hard_fail(status, body_text):
            code = classify_http_failure(status, body_text)
            attempted.append(f"{name}:{code}")
            logger.warning(
                "%s provider=%s model=%s failed %s", log_prefix, name, model, code
            )
            continue

        if name == "anthropic" and validate_anthropic_stop and isinstance(payload, dict):
            stop_error = validate_anthropic_stop(payload)
            if stop_error:
                attempted.append(f"{name}:{stop_error}")
                logger.warning(
                    "%s provider=%s model=%s failed %s",
                    log_prefix,
                    name,
                    model,
                    stop_error,
                )
                continue

        text = _extract_text(name, payload if isinstance(payload, dict) else {})
        value = parse_json_object(text)
        if value is None:
            attempted.append(f"{name}:unparseable")
            logger.warning(
                "%s provider=%s model=%s unparseable", log_prefix, name, model
            )
            continue

        logger.info("%s won provider=%s model=%s", log_prefix, name, model)
        return JsonMultimodalResult(
            value=value,
            provider=name,
            model=model,
            attempted=tuple(attempted + [f"{name}:ok"]),
        )

    raise ModelLadderExhausted(
        _exhausted_message(attempted, skipped, purpose="multimodal")
    )


__all__ = [
    "MODEL_LADDER_ORDER",
    "MODEL_LADDER_TIERS",
    "JsonMultimodalResult",
    "LadderAbort",
    "ModelLadderExhausted",
    "VisionExtractResult",
    "classify_http_failure",
    "is_hard_fail",
    "key_for",
    "ladder_order",
    "model_for",
    "parse_json_object",
    "parse_json_rows",
    "run_json_multimodal_ladder",
    "run_vision_extract_ladder",
    "safe_failure_message",
    "wired_providers",
]
