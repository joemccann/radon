"""Service-owned native protocols for CLI clients with advanced tool schemas.

The request body is retained verbatim apart from the selected fallback model.
Only providers implementing that wire protocol participate; a custom grammar is
never converted into a different tool schema. Streams are buffered and checked
for a complete terminal event before any bytes return to the CLI.
"""
from __future__ import annotations

import copy
import json
import math
import os
from collections.abc import Mapping
from typing import Any, Callable

from . import engine
from .client import GatewayError

_MAX_BYTES = 8_000_000
# SSE is JSON-wrapped by the gateway; leave room for escaping in the 4MiB wire cap.
_MAX_RESPONSE_BYTES = 2_000_000
# Safe-only metadata captured from the exact pinned Claude 2.1.311 client.
_CLAUDE_NATIVE_BETAS = (
    "claude-code-20250219", "interleaved-thinking-2025-05-14",
    "context-management-2025-06-27", "mid-conversation-tool-changes-2026-07-01",
    "thinking-token-count-2026-05-13", "advanced-tool-use-2025-11-20", "effort-2025-11-24",
)
_PROTOCOLS = {"responses", "messages", "chat_completions"}
_URLS = {
    ("responses", "codex"): engine._CHATGPT_CODEX_RESPONSES_URL,
    ("responses", "grok"): "https://api.x.ai/v1/responses",
    ("messages", "anthropic"): "https://api.anthropic.com/v1/messages",
    ("chat_completions", "grok"): "https://api.x.ai/v1/chat/completions",
    ("chat_completions", "nvidia"): "https://integrate.api.nvidia.com/v1/chat/completions",
    ("chat_completions", "cerebras"): "https://api.cerebras.ai/v1/chat/completions",
}


def _usage(value: Any, *keys: str) -> bool:
    return isinstance(value, dict) and all(type(value.get(key)) is int and value[key] >= 0 for key in keys)


def _events(raw: str) -> tuple[list[dict], bool]:
    events, done = [], False
    # SSE allows multiple data lines per event, comments, and CRLF line endings.
    normalized = raw.replace("\r\n", "\n")
    # EOF does not dispatch an unfinished SSE event. Never synthesize the
    # delimiter: a CLI would otherwise receive a terminal JSON frame that its
    # own stream parser cannot dispatch. Trailing comments remain harmless.
    tail = normalized.rsplit("\n\n", 1)[-1]
    if any(line.startswith("data:") for line in tail.split("\n")):
        raise ValueError("unterminated native stream event")
    data: list[str] = []
    for line in normalized.split("\n"):
        if line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
        elif not line and data:
            value = "\n".join(data)
            data = []
            if value == "[DONE]":
                done = True
                continue
            try:
                event = json.loads(value)
            except (ValueError, TypeError):
                raise ValueError("invalid native stream") from None
            if not isinstance(event, dict):
                raise ValueError("invalid native stream")
            if done:
                raise ValueError("event after native stream termination")
            events.append(event)
    if not events:
        raise ValueError("empty native stream")
    return events, done


def _validate_stream(protocol: str, raw: str, request: dict) -> None:
    events, done = _events(raw)
    terminal, started, stopped, usage = False, False, False, False
    for event in events:
        kind = event.get("type", "")
        if event.get("error") is not None or kind in {"error", "response.failed", "response.incomplete", "response.error"}:
            raise ValueError("failed native stream")
        if protocol == "responses":
            if terminal:
                raise ValueError("event after native response completion")
            if kind == "response.completed":
                response = event.get("response")
                if not isinstance(response, dict) or response.get("status") != "completed" or response.get("error") or not _usage(response.get("usage"), "input_tokens", "output_tokens"):
                    raise ValueError("incomplete native response")
                if str(request.get("model", "")).startswith("gpt-") and response.get("model") != request["model"]:
                    raise ValueError("native reviewer model identity mismatch")
                terminal = True
        elif protocol == "messages":
            if stopped:
                raise ValueError("event after native message stop")
            if kind == "message_start":
                if started or not _usage((event.get("message") or {}).get("usage"), "input_tokens", "output_tokens"):
                    raise ValueError("invalid native message start")
                started = True
            elif kind == "message_delta":
                reason = (event.get("delta") or {}).get("stop_reason")
                if reason is not None:
                    if reason not in {"end_turn", "tool_use", "stop_sequence"}:
                        raise ValueError("incomplete native message")
                    terminal = True
                if _usage(event.get("usage"), "output_tokens"):
                    usage = True
            elif kind == "message_stop":
                stopped = True
        else:
            choices = event.get("choices", [])
            if not isinstance(choices, list):
                raise ValueError("invalid native completion choices")
            for choice in choices:
                if not isinstance(choice, dict):
                    raise ValueError("invalid native completion choice")
                reason = choice.get("finish_reason")
                if reason is not None:
                    if reason not in {"stop", "tool_calls", "function_call"}:
                        raise ValueError("incomplete native completion")
                    terminal = True
            usage = usage or _usage(event.get("usage"), "prompt_tokens", "completion_tokens")
    if protocol == "messages" and not (started and stopped and terminal and usage):
        raise ValueError("incomplete native message stream")
    if protocol == "responses" and not terminal:
        raise ValueError("incomplete native response stream")
    if protocol == "chat_completions":
        wants_usage = bool((request.get("stream_options") or {}).get("include_usage"))
        if not (terminal and done) or (wants_usage and not usage):
            raise ValueError("incomplete native completion stream")


def _validate_json(protocol: str, doc: Any) -> None:
    if not isinstance(doc, dict) or doc.get("error") is not None:
        raise ValueError("invalid native response")
    if protocol == "responses":
        if doc.get("status") != "completed" or not _usage(doc.get("usage"), "input_tokens", "output_tokens"):
            raise ValueError("incomplete native response")
    elif protocol == "messages":
        if doc.get("stop_reason") not in {"end_turn", "tool_use", "stop_sequence"} or not _usage(doc.get("usage"), "input_tokens", "output_tokens"):
            raise ValueError("incomplete native message")
    else:
        choices = doc.get("choices")
        if not isinstance(choices, list) or not choices or any(not isinstance(row, dict) or row.get("finish_reason") not in {"stop", "tool_calls", "function_call"} for row in choices) or not _usage(doc.get("usage"), "prompt_tokens", "completion_tokens"):
            raise ValueError("incomplete native completion")


def _providers(protocol: str, request: dict) -> list[str]:
    if protocol == "messages":
        return ["anthropic"]
    if protocol == "chat_completions":
        return [name for name in engine.MODEL_LADDER_ORDER if name in {"grok", "nvidia", "cerebras"}]
    if str(request.get("model", "")).startswith("gpt-"):
        # An explicit GPT identity (including independent policy reviewers)
        # cannot be silently replaced by one shared fallback model.
        return ["codex"]
    tools = request.get("tools", [])
    if not isinstance(tools, list) or any(not isinstance(tool, dict) for tool in tools):
        raise GatewayError("invalid_request", "Invalid native tools", 400)
    # Codex's custom grammar, tool search and built-in tools have no verified
    # equivalent on Grok. Preserve them without a lossy compatibility rewrite.
    if any(tool.get("type") != "function" for tool in tools):
        return ["codex"]
    return [name for name in engine.MODEL_LADDER_ORDER if name in {"grok", "codex"}]


def complete(payload: dict, *, post: Callable | None = None, env: Mapping[str, str] | None = None) -> dict:
    if not isinstance(payload, dict) or set(payload) - {"protocol", "request", "stream", "timeout_seconds"}:
        raise GatewayError("invalid_request", "Invalid native operation fields", 400)
    protocol, request, stream = payload.get("protocol"), payload.get("request"), payload.get("stream")
    timeout = payload.get("timeout_seconds", 270)
    if not isinstance(protocol, str) or protocol not in _PROTOCOLS or not isinstance(request, dict) or type(stream) is not bool or request.get("stream", False) is not stream:
        raise GatewayError("invalid_request", "Invalid native protocol or stream", 400)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise GatewayError("invalid_request", "Invalid native deadline", 400)
    requested_model = request.get("model")
    if not isinstance(requested_model, str) or not requested_model or len(requested_model) > 256:
        raise GatewayError("invalid_request", "Invalid native model", 400)
    # Metadata/tool arguments are opaque application data; they never become
    # transport headers, credentials, URLs or environment overrides.
    try:
        if len(json.dumps(request, allow_nan=False).encode()) > _MAX_BYTES:
            raise ValueError("request too large")
    except (ValueError, TypeError, RecursionError):
        raise GatewayError("invalid_request", "Invalid native request body", 400) from None
    providers = _providers(protocol, request)
    source = dict(os.environ if env is None else env)
    for prefix, keys in (("claude-", ("RADON_RESEARCH_MODEL", "ANTHROPIC_MODEL")), ("grok-", ("XAI_MODEL",)), ("gpt-", ("OPENAI_MODEL",))):
        if requested_model.startswith(prefix):
            for key in keys:
                source[key] = requested_model
    sender = post or engine._default_post

    def call(name: str, auth: engine.AuthMaterial, model: str):
        body = copy.deepcopy(request)
        body["model"] = model
        if name == "anthropic":
            headers = engine._anthropic_headers(auth.token, subscription=auth.kind == "subscription")
            headers["anthropic-beta"] = ",".join(dict.fromkeys([*headers.get("anthropic-beta", "").split(","), *_CLAUDE_NATIVE_BETAS]))
            headers["x-app"] = "cli"
            headers["user-agent"] = "claude-cli/2.1.311 (external, sdk-cli)"
            system = body.get("system")
            identity = engine._CLAUDE_CODE_SYSTEM_PREFIX
            if not (isinstance(system, list) and system and isinstance(system[0], dict) and system[0].get("text") == identity):
                body["system"] = ([{"type": "text", "text": identity}] + system) if isinstance(system, list) else engine._anthropic_system(system or "", subscription=True)
        elif name == "codex":
            if auth.kind != "subscription":
                raise RuntimeError("unsupported_auth")
            headers = engine._chatgpt_headers(auth)
        else:
            headers = {"authorization": f"Bearer {auth.token}", "content-type": "application/json"}
        status, raw, _ = engine._request_raw(sender, _URLS[(protocol, name)], headers, body, timeout=timeout, max_bytes=_MAX_RESPONSE_BYTES)
        if not 200 <= status < 300:
            return status, raw, None
        try:
            if stream:
                _validate_stream(protocol, raw, body)
                result = {"status": status, "body": raw, "content_type": "text/event-stream"}
            else:
                doc = json.loads(raw)
                _validate_json(protocol, doc)
                if protocol == "responses" and str(body["model"]).startswith("gpt-") and doc.get("model") != body["model"]:
                    raise ValueError("native reviewer model identity mismatch")
                result = {"status": status, "body": doc, "content_type": "application/json"}
        except (ValueError, TypeError, AttributeError):
            raise RuntimeError("incomplete_native_response") from None
        return status, "", {"native": result}

    result, _text, provider, model, attempted = engine._run_ladder(
        env=source, post=sender, kind="text", log_prefix="native", providers=providers,
        call_provider=call, parse_success=lambda _name, doc: (doc["native"], ""))
    return {**result, "provider": provider, "model": model, "attempted": list(attempted)}
