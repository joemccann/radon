"""Shared CTA / MenthorQ vision cascade.

Joe's exact order (2026-09-10). Do not leave a band until it is
exhausted or unavailable:

  1. subscription: anthropic -> grok -> cursor -> codex -> gemini
  2. nvidia (free NIM endpoints)
  3. cerebras (cheap paid, last)

Cursor has no vision HTTP path in Radon; it is recorded as unwired and
the rest of the subscription band still runs. Only a full-cascade miss
is an ops_only / hard fail — never "top up Anthropic" while another
keyed provider remains.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional

logger = logging.getLogger(__name__)

VISION_CASCADE_ORDER = (
    "anthropic",
    "grok",
    "cursor",
    "codex",
    "gemini",
    "nvidia",
    "cerebras",
)

VISION_CASCADE_TIERS = {
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


class VisionCascadeExhausted(RuntimeError):
    """Every keyed cascade provider failed or none were keyed."""


@dataclass(frozen=True)
class VisionResult:
    rows: list[dict[str, Any]]
    provider: str
    model: str
    attempted: tuple[str, ...] = field(default_factory=tuple)


def _env_get(env: Mapping[str, str], *names: str) -> str:
    for name in names:
        value = (env.get(name) or "").strip()
        if value:
            return value
    return ""


def _first_env_model(env: Mapping[str, str], *names: str, default: str) -> str:
    return _env_get(env, *names) or default


def wired_vision_providers(env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Return cascade providers that have a real HTTP path and a key."""
    src = env if env is not None else os.environ
    wired: list[str] = []
    for name in VISION_CASCADE_ORDER:
        if name == "cursor":
            continue
        if _key_for(name, src):
            wired.append(name)
    return tuple(wired)


def _key_for(name: str, env: Mapping[str, str]) -> str:
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


def _model_for(name: str, env: Mapping[str, str]) -> str:
    if name == "anthropic":
        return _first_env_model(env, "ANTHROPIC_VISION_MODEL", "ANTHROPIC_MODEL", default=_DEFAULT_MODELS["anthropic"])
    if name == "grok":
        return _first_env_model(env, "XAI_MODEL", "GROK_MODEL", default=_DEFAULT_MODELS["grok"])
    if name == "codex":
        return _first_env_model(env, "OPENAI_VISION_MODEL", "OPENAI_MODEL", default=_DEFAULT_MODELS["codex"])
    if name == "gemini":
        return _first_env_model(env, "GEMINI_VISION_MODEL", "GEMINI_MODEL", default=_DEFAULT_MODELS["gemini"])
    if name == "nvidia":
        return _first_env_model(env, "NVIDIA_VISION_MODEL", "NVIDIA_MODEL", default=_DEFAULT_MODELS["nvidia"])
    if name == "cerebras":
        return _first_env_model(env, "CEREBRAS_VISION_MODEL", "CEREBRAS_MODEL", default=_DEFAULT_MODELS["cerebras"])
    return name


def _classify_http_failure(status: int, body: str) -> str:
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


def _is_hard_fail(status: int, body: str) -> bool:
    if status != 200:
        return True
    lowered = body.lower()
    return any(marker in lowered for marker in _CREDIT_MARKERS)


def parse_json_rows(text: str) -> Optional[list[dict[str, Any]]]:
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1] if "\n" in cleaned else cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()
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


def _anthropic_body(model: str, b64: str, prompt: str) -> dict[str, Any]:
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


def _openai_body(model: str, b64: str, prompt: str) -> dict[str, Any]:
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


def _gemini_body(model: str, b64: str, prompt: str) -> tuple[str, dict[str, Any]]:
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


def _default_post(url: str, *, headers: dict[str, str], json: dict[str, Any], timeout: float):
    import httpx

    return httpx.post(url, headers=headers, json=json, timeout=timeout)


def _request(
    post: Callable[..., Any],
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
) -> tuple[int, str, Any]:
    try:
        resp = post(url, headers=headers, json=body, timeout=60.0)
    except Exception as exc:  # noqa: BLE001 — network is a hard provider fail
        raise RuntimeError(f"network:{type(exc).__name__}") from exc
    text = getattr(resp, "text", "") or ""
    try:
        payload = resp.json()
    except Exception:
        payload = None
    return int(getattr(resp, "status_code", 0) or 0), text, payload


def extract_via_vision(
    png_bytes: bytes,
    prompt: str,
    *,
    env: Mapping[str, str] | None = None,
    post: Callable[..., Any] | None = None,
) -> VisionResult:
    src = env if env is not None else os.environ
    sender = post or _default_post
    b64 = base64.b64encode(png_bytes).decode("utf-8")
    attempted: list[str] = []
    skipped: list[str] = []

    for name in VISION_CASCADE_ORDER:
        if name == "cursor":
            skipped.append("cursor:unwired")
            logger.info("CTA vision skip provider=cursor reason=unwired")
            continue
        api_key = _key_for(name, src)
        if not api_key:
            skipped.append(f"{name}:no_key")
            continue
        model = _model_for(name, src)
        try:
            status, raw, payload = _call_provider(
                name, api_key, model, b64, prompt, sender
            )
        except RuntimeError as exc:
            code = str(exc) or "network"
            attempted.append(f"{name}:{code}")
            logger.warning("CTA vision provider=%s failed %s", name, code)
            continue

        body_text = raw if isinstance(raw, str) else ""
        if payload is None or _is_hard_fail(status, body_text):
            code = _classify_http_failure(status, body_text)
            attempted.append(f"{name}:{code}")
            logger.warning(
                "CTA vision provider=%s model=%s failed %s", name, model, code
            )
            continue

        extractor = {
            "anthropic": _text_from_anthropic,
            "gemini": _text_from_gemini,
        }.get(name, _text_from_openai)
        text = extractor(payload if isinstance(payload, dict) else {})
        rows = parse_json_rows(text)
        if rows is None:
            attempted.append(f"{name}:unparseable")
            logger.warning("CTA vision provider=%s model=%s unparseable", name, model)
            continue

        logger.info(
            "CTA vision won provider=%s model=%s rows=%d", name, model, len(rows)
        )
        return VisionResult(
            rows=rows,
            provider=name,
            model=model,
            attempted=tuple(attempted + [f"{name}:ok"]),
        )

    tried = " ".join(attempted) or "none"
    skip = " ".join(skipped) or "none"
    if not attempted:
        raise VisionCascadeExhausted(
            "Vision cascade exhausted: no keyed CTA vision provider. "
            f"order={' -> '.join(VISION_CASCADE_ORDER)}; skipped={skip}."
        )
    raise VisionCascadeExhausted(
        "Vision cascade exhausted after trying every keyed provider. "
        f"tried={tried}; skipped={skip}."
    )


def _call_provider(
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
            _anthropic_body(model, b64, prompt),
        )
    if name == "grok":
        return _request(
            post,
            "https://api.x.ai/v1/chat/completions",
            {
                "authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
            _openai_body(model, b64, prompt),
        )
    if name == "codex":
        return _request(
            post,
            "https://api.openai.com/v1/chat/completions",
            {
                "authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
            _openai_body(model, b64, prompt),
        )
    if name == "gemini":
        url, body = _gemini_body(model, b64, prompt)
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
            _openai_body(model, b64, prompt),
        )
    if name == "cerebras":
        return _request(
            post,
            "https://api.cerebras.ai/v1/chat/completions",
            {
                "authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
            _openai_body(model, b64, prompt),
        )
    raise RuntimeError(f"{name}:unwired")


__all__ = [
    "VISION_CASCADE_ORDER",
    "VISION_CASCADE_TIERS",
    "VisionCascadeExhausted",
    "VisionResult",
    "extract_via_vision",
    "parse_json_rows",
    "wired_vision_providers",
]
