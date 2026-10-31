"""Gateway-owned native NVIDIA OCR transport and retry policy."""
from __future__ import annotations

import base64

import io

import os

import random

import re

import threading

import time

from concurrent.futures import ThreadPoolExecutor, as_completed

_V2_PROMPT = "</s><s><predict_bbox><predict_classes><output_markdown><predict_text_in_pic>"

_CLASS_RE = re.compile(r"<class_([A-Za-z0-9_-]+)>")

_BBOX_TAG = re.compile(
    r"<bbox>\s*([0-9.eE+-]+)\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)\s*</bbox>\s*"
)

_NOISE = re.compile(r"</?s>|<predict_[a-z_]+>|<output_markdown>|<\|[^>]*\|>")

NVIDIA_CHAT_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

class PageParseError(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason

def _request_body(model: str, data_uri: str) -> dict:
    if _adapter(model) == "v2":
        return {
            "model": model,
            "temperature": 0,
            "max_tokens": 3500,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": _V2_PROMPT},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }],
        }
    return {
        "model": model,
        "temperature": 0,
        "max_tokens": 4096,
        "tools": [{"type": "function", "function": {"name": "markdown_bbox"}}],
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": data_uri}},
            ],
        }],
    }

def _adapter(model: str) -> str:
    if "nemotron-parse-2" in model:
        return "v2"
    return "v1"

class _CapturingPost:
    def __init__(self, post):
        self._post = post
        self.headers: dict = {}

    def __call__(self, url, **kwargs):
        response = self._post(url, **kwargs)
        self.headers = dict(getattr(response, "headers", {}) or {})
        return response

def _post_with_retries(model, body, *, post, sleep, monotonic, jitter, deadline):
    from .engine import (
        _classify_http_failure, _default_post, _nvidia_limiter, _nvidia_max_wait, _request, acquire_model_slot, scoped_timeout,
    )

    sender = _CapturingPost(post or _default_post)
    # One NVIDIA key, one host-wide pace shared with the ladder, embeddings and
    # the nightly fx loops (scripts/nvidia_rate_limit.py).
    limiter = _nvidia_limiter(os.environ, post or _default_post)
    key = os.environ.get("NVIDIA_API_KEY", "").strip() or ("test-key" if post else "")
    if not key:
        raise PageParseError("missing_api_key")
    headers = {"authorization": f"Bearer {key}", "content-type": "application/json"}
    timeout = _float_env("RADON_RESEARCH_PARSE_PAGE_TIMEOUT_S", 25.0)
    retries = _int_env("RADON_RESEARCH_PARSE_MAX_RETRIES", 2, 0, 5)
    delay = 0.5
    last = "empty"
    for attempt in range(retries + 1):
        scoped_timeout(timeout)
        if _clock(monotonic) >= deadline:
            raise PageParseError("budget")
        if limiter is not None and not acquire_model_slot(limiter, _nvidia_max_wait(os.environ)):
            raise PageParseError("http_429")
        try:
            status, text, payload = _request(sender, NVIDIA_CHAT_URL, headers, body, timeout=timeout)
        except RuntimeError as exc:
            last = "timeout" if "timeout" in str(exc).lower() else "network"
            if attempt >= retries or not _retryable(last):
                raise PageParseError(last) from exc
            _sleep(min(delay, 8.0) + _jitter(jitter), sleep)
            delay = min(delay * 2, 8.0)
            continue
        if limiter is not None:
            if status == 429:
                limiter.note_429(_retry_after(sender.headers))
            elif status in (401, 403):
                limiter.note_auth_failure(status)
            elif status == 200:
                limiter.note_success()
        failure = _failure_reason(model, status, text or "", payload, _classify_http_failure)
        if failure is None:
            return payload
        last = failure
        if attempt >= retries or not _retryable(failure):
            raise PageParseError(failure)
        retry_after = _retry_after(sender.headers)
        wait = min(retry_after, 8.0) if retry_after is not None else min(delay, 8.0)
        _sleep(max(0.0, wait) + _jitter(jitter), sleep)
        delay = min(delay * 2, 8.0)
    raise PageParseError(last)

def _retryable(reason: str) -> bool:
    return reason in {"degraded", "empty", "length", "timeout", "network"} or reason == "http_429" or reason.startswith("http_5")

def _failure_reason(model, status, text, payload, classify) -> str | None:
    if status == 429:
        return "http_429"
    if status == 400 and "degraded" in text.lower():
        return "degraded"
    if status != 200 or not isinstance(payload, dict):
        return "degraded" if "degraded" in text.lower() else classify(status, text)
    finish = _finish_reason(payload)
    if finish == "length":
        return "length"
    if _adapter(model) == "v2":
        content = _openai_text(payload)
        if _meaningful(content):
            return None
        return "empty"
    if _v1_blocks(payload) is None:
        content = _openai_text(payload)
        return "malformed" if _meaningful(content) else "empty"
    return None

def _finish_reason(payload) -> str | None:
    choices = payload.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return None
    return choices[0].get("finish_reason")

def _v1_blocks(payload):
    choices = payload.get("choices") or []
    if not choices:
        return None
    message = (choices[0] or {}).get("message") or {}
    calls = message.get("tool_calls") or []
    if not calls:
        return None
    arguments = ((calls[0] or {}).get("function") or {}).get("arguments")
    parsed = _loads_json(arguments)
    blocks = _flatten_blocks(parsed)
    return blocks or None

def _loads_json(value, depth: int = 0):
    import json
    if isinstance(value, str) and depth < 2:
        try:
            return _loads_json(json.loads(value), depth + 1)
        except json.JSONDecodeError:
            return None
    return value

def _flatten_blocks(parsed) -> list:
    if isinstance(parsed, dict) and any(key in parsed for key in ("bbox", "text", "type")):
        return [parsed]
    if isinstance(parsed, dict):
        found = []
        for value in parsed.values():
            found.extend(_flatten_blocks(value))
        return found
    if isinstance(parsed, list):
        found = []
        for item in parsed:
            found.extend(_flatten_blocks(item))
        return found
    return []

def _meaningful(text: str) -> bool:
    cleaned = _NOISE.sub("", text or "")
    cleaned = _CLASS_RE.sub("", cleaned)
    cleaned = re.sub(r"<(?:[xy]_)?-?\d+(?:\.\d+)?>", "", cleaned)
    cleaned = _BBOX_TAG.sub("", cleaned)
    return bool(cleaned.strip())

def _openai_text(payload: dict) -> str:
    from .engine import _text_from_openai
    return _text_from_openai(payload)

def _retry_after(headers: dict):
    for key, value in (headers or {}).items():
        if str(key).lower() == "retry-after":
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None

def _clock(monotonic):
    return monotonic() if monotonic is not None else time.monotonic()

def _jitter(jitter):
    if jitter is not None:
        return float(jitter())
    return random.uniform(0, 0.25)

def _sleep(seconds: float, sleep):
    from .engine import scoped_timeout
    seconds = scoped_timeout(seconds)
    if seconds <= 0:
        return
    if sleep is not None:
        sleep(seconds)
    else:
        time.sleep(seconds)

def _int_env(name: str, default: int, low: int, high: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(low, min(value, high))

def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default
