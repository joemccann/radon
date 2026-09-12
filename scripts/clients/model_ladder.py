"""Shared HTTP model ladder for Radon multimodal and text JSON callers.

Joe's exact order (2026-09-10). Do not leave a band until it is exhausted or
unavailable:

  1. subscription: anthropic -> grok -> cursor -> codex -> gemini
  2. nvidia (free NIM endpoints)
  3. cerebras (cheap paid, last)

Cursor has no vision HTTP path in Radon; it is recorded as unwired and the rest
of the subscription band still runs. Only a full-cascade miss is an ops_only /
hard fail — never "top up Anthropic" while another keyed provider remains.

Weekend bash CLI ladders (``RADON_WEEKEND_MODEL_LADDER``) are a sibling pattern
for Claude Code subprocesses; they are documented separately and not consolidated
here.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

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

_DEFAULT_VISION_MODELS = {
    "anthropic": "claude-haiku-4-5-20251001",
    "grok": "grok-4.6",
    "codex": "gpt-5.5",
    "gemini": "gemini-2.5-flash",
    "nvidia": "meta/llama-3.2-11b-vision-instruct",
    "cerebras": "llama-4-scout-17b-16e-instruct",
}

_DEFAULT_TEXT_MODELS = {
    "anthropic": "claude-sonnet-4-6",
    "grok": "grok-4.6",
    "codex": "gpt-5.5",
    "gemini": "gemini-2.5-flash",
    "nvidia": "meta/llama-3.3-70b-instruct",
    "cerebras": "llama-4-scout-17b-16e-instruct",
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


def _env_get(env: Mapping[str, str], *names: str) -> str:
    for name in names:
        value = (env.get(name) or "").strip()
        if value:
            return value
    return ""


def _first_env_model(env: Mapping[str, str], *names: str, default: str) -> str:
    return _env_get(env, *names) or default


def wired_providers(env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Return ladder providers that have a real HTTP path and a key."""
    src = env if env is not None else os.environ
    wired: list[str] = []
    for name in MODEL_LADDER_ORDER:
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


def _model_for(name: str, env: Mapping[str, str], *, kind: str = "vision") -> str:
    defaults = _DEFAULT_VISION_MODELS if kind == "vision" else _DEFAULT_TEXT_MODELS
    if name == "anthropic":
        if kind == "text":
            return _first_env_model(
                env,
                "RADON_RESEARCH_MODEL",
                "ANTHROPIC_MODEL",
                default=defaults["anthropic"],
            )
        return _first_env_model(
            env, "ANTHROPIC_VISION_MODEL", "ANTHROPIC_MODEL", default=defaults["anthropic"]
        )
    if name == "grok":
        return _first_env_model(env, "XAI_MODEL", "GROK_MODEL", default=defaults["grok"])
    if name == "codex":
        if kind == "vision":
            return _first_env_model(
                env, "OPENAI_VISION_MODEL", "OPENAI_MODEL", default=defaults["codex"]
            )
        return _first_env_model(env, "OPENAI_MODEL", default=defaults["codex"])
    if name == "gemini":
        if kind == "vision":
            return _first_env_model(
                env, "GEMINI_VISION_MODEL", "GEMINI_MODEL", default=defaults["gemini"]
            )
        return _first_env_model(env, "GEMINI_MODEL", default=defaults["gemini"])
    if name == "nvidia":
        return _first_env_model(
            env, "NVIDIA_VISION_MODEL", "NVIDIA_MODEL", default=defaults["nvidia"]
        )
    if name == "cerebras":
        return _first_env_model(
            env, "CEREBRAS_VISION_MODEL", "CEREBRAS_MODEL", default=defaults["cerebras"]
        )
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
    stream: bool = False,
    max_bytes: int = 0,
) -> tuple[int, str, Any]:
    try:
        kwargs: dict[str, Any] = {"headers": headers, "json": body, "timeout": timeout}
        if stream:
            kwargs["stream"] = True
        resp = post(url, **kwargs)
    except Exception as exc:  # noqa: BLE001 — network is a hard provider fail
        raise RuntimeError(f"network:{type(exc).__name__}") from exc

    if stream and max_bytes and hasattr(resp, "iter_content"):
        raw = bytearray()
        try:
            for chunk in resp.iter_content(65536):
                raw.extend(chunk)
                if len(raw) > max_bytes:
                    raise ModelResponseError(
                        "response_too_large",
                        "Research reviewer response exceeds limit",
                    )
            text = raw.decode("utf-8", errors="replace")
            payload = json.loads(text)
            status = int(getattr(resp, "status_code", 0) or 0)
        finally:
            close = getattr(resp, "close", None)
            if callable(close):
                close()
        return status, text, payload

    text = getattr(resp, "text", "") or ""
    try:
        payload = resp.json()
    except Exception:
        payload = None
    return int(getattr(resp, "status_code", 0) or 0), text, payload


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


def _labeled_images_to_b64(
    images: Sequence[tuple[str, Path | bytes]],
) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for label, source in images:
        raw = source if isinstance(source, bytes) else Path(source).read_bytes()
        out.append((label, base64.b64encode(raw).decode("utf-8")))
    return out


def _anthropic_multimodal_body(
    model: str,
    system: str,
    instruction: str,
    labeled_b64: Sequence[tuple[str, str]],
    *,
    max_tokens: int,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    for label, b64 in labeled_b64:
        content.extend(
            [
                {"type": "text", "text": label},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": b64,
                    },
                },
            ]
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


def _openai_multimodal_body(
    model: str,
    system: str,
    instruction: str,
    labeled_b64: Sequence[tuple[str, str]],
    *,
    max_tokens: int,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    for label, b64 in labeled_b64:
        content.extend(
            [
                {"type": "text", "text": label},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                },
            ]
        )
    content.append({"type": "text", "text": instruction})
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": content})
    return {"model": model, "max_tokens": max_tokens, "messages": messages}


def _gemini_multimodal_body(
    model: str,
    system: str,
    instruction: str,
    labeled_b64: Sequence[tuple[str, str]],
    *,
    max_tokens: int,
) -> tuple[str, dict[str, Any]]:
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent"
    )
    parts: list[dict[str, Any]] = []
    for label, b64 in labeled_b64:
        parts.append({"text": label})
        parts.append({"inline_data": {"mime_type": "image/png", "data": b64}})
    parts.append({"text": instruction})
    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    return url, body


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


def _call_text_provider(
    name: str,
    api_key: str,
    model: str,
    system: str,
    instruction: str,
    labeled_b64: Sequence[tuple[str, str]],
    post: Callable[..., Any],
    *,
    max_tokens: int,
    stream_anthropic: bool,
    read_timeout: float,
    max_response_bytes: int,
) -> tuple[int, str, Any]:
    if name == "anthropic":
        body = _anthropic_multimodal_body(
            model, system, instruction, labeled_b64, max_tokens=max_tokens
        )
        return _request(
            post,
            "https://api.anthropic.com/v1/messages",
            {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            body,
            timeout=(10.0, read_timeout),
            stream=stream_anthropic,
            max_bytes=max_response_bytes if stream_anthropic else 0,
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
                model, system, instruction, labeled_b64, max_tokens=max_tokens
            ),
            timeout=read_timeout,
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
                model, system, instruction, labeled_b64, max_tokens=max_tokens
            ),
            timeout=read_timeout,
        )
    if name == "gemini":
        url, body = _gemini_multimodal_body(
            model, system, instruction, labeled_b64, max_tokens=max_tokens
        )
        return _request(
            post,
            f"{url}?key={api_key}",
            {"content-type": "application/json"},
            body,
            timeout=read_timeout,
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
                model, system, instruction, labeled_b64, max_tokens=max_tokens
            ),
            timeout=read_timeout,
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
                model, system, instruction, labeled_b64, max_tokens=max_tokens
            ),
            timeout=read_timeout,
        )
    raise RuntimeError(f"{name}:unwired")


def _extract_text(name: str, payload: dict[str, Any]) -> str:
    if name == "anthropic":
        return _text_from_anthropic(payload)
    if name == "gemini":
        return _text_from_gemini(payload)
    return _text_from_openai(payload)


def _run_ladder(
    *,
    env: Mapping[str, str],
    post: Callable[..., Any],
    kind: str,
    log_prefix: str,
    call_provider: Callable[[str, str, str], tuple[int, str, Any]],
    parse_success: Callable[[str, dict[str, Any]], Any],
    model_override: str | None = None,
    providers: Sequence[str] | None = None,
) -> tuple[Any, str, str, str, tuple[str, ...]]:
    attempted: list[str] = []
    skipped: list[str] = []
    order = providers or MODEL_LADDER_ORDER

    for name in order:
        if name == "cursor":
            skipped.append("cursor:unwired")
            logger.info("%s skip provider=cursor reason=unwired", log_prefix)
            continue
        api_key = _key_for(name, env)
        if not api_key:
            skipped.append(f"{name}:no_key")
            continue
        model = model_override if name == "anthropic" and model_override else _model_for(
            name, env, kind=kind
        )
        try:
            status, raw, payload = call_provider(name, api_key, model)
        except RuntimeError as exc:
            code = str(exc) or "network"
            attempted.append(f"{name}:{code}")
            logger.warning("%s provider=%s failed %s", log_prefix, name, code)
            continue

        body_text = raw if isinstance(raw, str) else ""
        if payload is None or _is_hard_fail(status, body_text):
            code = _classify_http_failure(status, body_text)
            attempted.append(f"{name}:{code}")
            logger.warning(
                "%s provider=%s model=%s failed %s", log_prefix, name, model, code
            )
            continue

        if not isinstance(payload, dict):
            attempted.append(f"{name}:unparseable")
            logger.warning("%s provider=%s model=%s unparseable", log_prefix, name, model)
            continue

        try:
            parsed, text = parse_success(name, payload)
        except ModelResponseError:
            raise
        except (ValueError, TypeError, KeyError):
            attempted.append(f"{name}:unparseable")
            logger.warning("%s provider=%s model=%s unparseable", log_prefix, name, model)
            continue

        if parsed is None:
            attempted.append(f"{name}:unparseable")
            logger.warning("%s provider=%s model=%s unparseable", log_prefix, name, model)
            continue

        logger.info("%s won provider=%s model=%s", log_prefix, name, model)
        return parsed, text, name, model, tuple(attempted + [f"{name}:ok"])

    tried = " ".join(attempted) or "none"
    skip = " ".join(skipped) or "none"
    if not attempted:
        raise ModelLadderExhausted(
            f"Model ladder exhausted: no keyed provider. "
            f"order={' -> '.join(MODEL_LADDER_ORDER)}; skipped={skip}."
        )
    raise ModelLadderExhausted(
        f"Model ladder exhausted after trying every keyed provider. "
        f"tried={tried}; skipped={skip}."
    )


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

    def call_provider(name: str, api_key: str, model: str) -> tuple[int, str, Any]:
        return _call_vision_provider(name, api_key, model, b64, prompt, sender)

    def parse_success(name: str, payload: dict[str, Any]) -> tuple[Any, str]:
        text = _extract_text(name, payload)
        rows = parse_json_rows(text)
        return rows, text

    parsed, _text, provider, model, attempted = _run_ladder(
        env=src,
        post=sender,
        kind="vision",
        log_prefix="vision",
        call_provider=call_provider,
        parse_success=parse_success,
    )
    return VisionResult(
        rows=parsed,
        provider=provider,
        model=model,
        attempted=attempted,
    )


def complete_multimodal_json(
    instruction: str,
    images: Sequence[tuple[str, Path | bytes]] = (),
    *,
    system: str = "",
    env: Mapping[str, str] | None = None,
    post: Callable[..., Any] | None = None,
    model_override: str | None = None,
    max_tokens: int = 6000,
    stream_anthropic: bool = True,
    read_timeout: float = 120.0,
    max_response_bytes: int = 2_000_000,
    require_end_turn: bool = True,
) -> LadderResult:
    """Run multimodal JSON completion through the shared ladder."""
    src = env if env is not None else os.environ
    sender = post or _default_post
    labeled_b64 = _labeled_images_to_b64(images)

    def call_provider(name: str, api_key: str, model: str) -> tuple[int, str, Any]:
        use_stream = stream_anthropic and name == "anthropic"
        return _call_text_provider(
            name,
            api_key,
            model,
            system,
            instruction,
            labeled_b64,
            sender,
            max_tokens=max_tokens,
            stream_anthropic=use_stream,
            read_timeout=read_timeout,
            max_response_bytes=max_response_bytes,
        )

    def parse_success(name: str, payload: dict[str, Any]) -> tuple[Any, str]:
        if require_end_turn and name == "anthropic" and payload.get("stop_reason") != "end_turn":
            raise ModelResponseError(
                "incomplete",
                "Research reviewer did not finish a complete response",
            )
        text = _extract_text(name, payload)
        obj = parse_json_object(text)
        return obj, text

    parsed, text, provider, model, attempted = _run_ladder(
        env=src,
        post=sender,
        kind="text",
        log_prefix="research",
        call_provider=call_provider,
        parse_success=parse_success,
        model_override=model_override,
    )
    return LadderResult(
        data=parsed,
        text=text,
        provider=provider,
        model=model,
        attempted=attempted,
    )


__all__ = [
    "MODEL_LADDER_ORDER",
    "MODEL_LADDER_TIERS",
    "LadderResult",
    "ModelLadderExhausted",
    "VisionResult",
    "complete_multimodal_json",
    "extract_via_vision",
    "parse_json_object",
    "parse_json_rows",
    "safe_error_message",
    "wired_providers",
]
