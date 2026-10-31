"""Central normalized chat adapters. Tool execution remains with the authorized caller."""
from __future__ import annotations
import json
import os
import re
from . import engine
from .client import GatewayError

ALIASES = {"xai": "grok", "openai": "codex"}
WEB_NAMES = {"grok": "xai", "codex": "openai"}
FIELDS = {"messages", "system", "tools", "toolChoice", "reasoningEffort", "model", "provider", "maxTokens", "timeoutMs", "fallback"}


def validate(request):
    if set(request) - FIELDS:
        raise GatewayError("invalid_request", "Unknown chat fields", 400)
    messages = request.get("messages")
    if not isinstance(messages, list) or not 1 <= len(messages) <= 128:
        raise GatewayError("invalid_request", "Invalid chat messages", 400)
    for message in messages:
        if not isinstance(message, dict) or set(message) != {"role", "content"} or message["role"] not in {"user", "assistant"}:
            raise GatewayError("invalid_request", "Invalid chat turn", 400)
        content = message["content"]
        if isinstance(content, str):
            if len(content) > 1_000_000:
                raise GatewayError("invalid_request", "Chat turn exceeds limit", 400)
            continue
        if not isinstance(content, list) or len(content) > 128:
            raise GatewayError("invalid_request", "Invalid chat content", 400)
        for block in content:
            if not isinstance(block, dict) or block.get("type") not in {"text", "image", "tool_use", "tool_result"}:
                raise GatewayError("invalid_request", "Invalid chat block", 400)
            kind = block["type"]
            if kind == "text" and (set(block) != {"type", "text"} or not isinstance(block["text"], str)):
                raise GatewayError("invalid_request", "Invalid text block", 400)
            if kind == "image":
                source = block.get("source")
                if not isinstance(source, dict) or set(block) != {"type", "source"} or set(source) != {"type", "media_type", "data"} or source["type"] != "base64":
                    raise GatewayError("invalid_request", "Invalid chat image", 400)
                from .server import decode_image
                decode_image(source["data"], source["media_type"])
            if kind == "tool_use" and (set(block) != {"type", "id", "name", "input"} or not isinstance(block["input"], dict) or not isinstance(block["id"], str) or not isinstance(block["name"], str)):
                raise GatewayError("invalid_request", "Invalid tool call", 400)
            if kind == "tool_result" and (set(block) != {"type", "tool_use_id", "content"} or not isinstance(block["tool_use_id"], str) or not isinstance(block["content"], str)):
                raise GatewayError("invalid_request", "Invalid tool result", 400)
    tools = request.get("tools") or []
    if not isinstance(tools, list) or len(tools) > 128:
        raise GatewayError("invalid_request", "Invalid chat tools", 400)
    for tool in tools:
        if not isinstance(tool, dict) or set(tool) - {"name", "description", "input_schema"} or not isinstance(tool.get("input_schema"), dict) or not isinstance(tool.get("name"), str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}", tool["name"]):
            raise GatewayError("invalid_request", "Invalid chat tool schema", 400)
    if request.get("toolChoice") not in (None, "auto", "none", "required") or request.get("reasoningEffort") not in (None, "low", "medium", "high"):
        raise GatewayError("invalid_request", "Invalid chat option", 400)
    if "system" in request and (not isinstance(request["system"], str) or len(request["system"]) > 200_000):
        raise GatewayError("invalid_request", "Invalid chat instruction", 400)
    if "fallback" in request and type(request["fallback"]) is not bool:
        raise GatewayError("invalid_request", "Invalid chat fallback option", 400)
    tokens = request.get("maxTokens", 1200)
    if type(tokens) is not int or not 1 <= tokens <= 32_000:
        raise GatewayError("invalid_request", "Invalid chat token limit", 400)


def openai_messages(request):
    result = [{"role": "system", "content": request["system"]}] if request.get("system") else []
    for message in request["messages"]:
        if isinstance(message["content"], str):
            result.append(dict(message))
            continue
        blocks = message["content"]
        # Retain all blocks, including text/images beside tool results.
        content = []
        tool_uses = []
        tool_results = []
        for block in blocks:
            if block["type"] == "text":
                content.append({"type": "text", "text": block["text"]})
            elif block["type"] == "image":
                source = block["source"]
                content.append({"type": "image_url", "image_url": {"url": f'data:{source["media_type"]};base64,{source["data"]}'}})
            elif block["type"] == "tool_use":
                tool_uses.append({"id": block["id"], "type": "function", "function": {"name": block["name"], "arguments": json.dumps(block["input"])}})
            else:
                tool_results.append({"role": "tool", "tool_call_id": block["tool_use_id"], "content": block["content"]})
        if content or tool_uses:
            value = content if any(block["type"] == "image_url" for block in content) else "".join(block["text"] for block in content)
            result.append({"role": message["role"], "content": value or None, **({"tool_calls": tool_uses} if tool_uses else {})})
        result.extend(tool_results)
    return result


def _native_call(name, auth, model, request):
    max_tokens = request.get("maxTokens", 1200)
    tools = request.get("tools") or []
    system = request.get("system", "")
    timeout = engine.scoped_timeout(request.get("timeoutMs", 45_000) / 1000)
    if name == "antigravity":
        instruction = "\n\n".join(message["role"] + ": " + (message["content"] if isinstance(message["content"], str) else "\n".join(b["text"] for b in message["content"] if b["type"] == "text")) for message in request["messages"])
        return engine._antigravity_complete(auth.token, {}, model, system, instruction, [])
    if name == "anthropic":
        body = {"model": model, "max_tokens": max_tokens, "messages": request["messages"],
                "system": engine._anthropic_system(system, subscription=True)}
        if tools:
            body["tools"] = tools
            if request.get("toolChoice"):
                body["tool_choice"] = {"type": {"auto": "auto", "required": "any", "none": "none"}[request["toolChoice"]]}
        return engine._request(engine._default_post, "https://api.anthropic.com/v1/messages", engine._anthropic_headers(auth.token, subscription=True), body,
                               timeout=timeout, stream=True, max_bytes=2_000_000)
    if name == "codex":
        # Preserve native message roles and every image in the subscription Responses API.
        messages = []
        for message in request["messages"]:
            content = message["content"]
            blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content
            items = []
            for block in blocks:
                if block["type"] == "text":
                    items.append({"type": "output_text" if message["role"] == "assistant" else "input_text", "text": block["text"]})
                elif block["type"] == "image":
                    source = block["source"]
                    items.append({"type": "input_image", "image_url": f'data:{source["media_type"]};base64,{source["data"]}'})
            messages.append({"role": message["role"], "content": items})
        body = {"model": model, "input": messages, "stream": True, "store": False}
        if system:
            body["instructions"] = system
        if request.get("reasoningEffort"):
            body["reasoning"] = {"effort": request["reasoningEffort"]}
        return engine._request_sse_responses(engine._default_post, engine._CHATGPT_CODEX_RESPONSES_URL, engine._chatgpt_headers(auth), body, timeout=timeout,max_bytes=2_000_000)
    urls = {"grok": "https://api.x.ai/v1/chat/completions", "nvidia": "https://integrate.api.nvidia.com/v1/chat/completions", "cerebras": "https://api.cerebras.ai/v1/chat/completions"}
    body = {"model": model, "max_tokens": max_tokens, "messages": openai_messages(request)}
    if tools:
        body["tools"] = [{"type": "function", "function": {"name": tool["name"], "description": tool.get("description", ""), "parameters": tool["input_schema"]}} for tool in tools]
        if request.get("toolChoice"):
            body["tool_choice"] = request["toolChoice"]
    if request.get("reasoningEffort") and name == "grok":
        body["reasoning_effort"] = request["reasoningEffort"]
    if name == "cerebras":
        body.update(engine._CEREBRAS_REQUEST_EXTRAS)
    return engine._request(engine._default_post, urls[name], {"authorization": "Bearer " + auth.token, "content-type": "application/json"}, body, timeout=timeout,stream=True,max_bytes=2_000_000)


def complete(request, *, catalog):
    validate(request)
    images = any(isinstance(message["content"], list) and any(block["type"] == "image" for block in message["content"]) for message in request["messages"])
    tool_history = any(isinstance(message["content"], list) and any(block["type"] in {"tool_use", "tool_result"} for block in message["content"]) for message in request["messages"])
    tool_request = bool(request.get("tools")) or tool_history
    provider = ALIASES.get(request.get("provider"), request.get("provider"))
    if provider is not None and provider not in engine.MODEL_LADDER_ORDER:
        raise GatewayError("invalid_request", "Unknown chat provider", 400)
    model = request.get("model")
    known = {row["id"]: ALIASES.get(row["provider"], row["provider"]) for row in catalog["models"]}
    if model is not None and model not in known:
        raise GatewayError("invalid_request", "Unknown catalog model", 400)
    if model is not None:
        selected = known[model]
        if provider is not None and selected != provider:
            raise GatewayError("invalid_request", "Model and provider do not match", 400)
        provider = selected
    order = list(engine.MODEL_LADDER_ORDER)
    if provider is not None:
        order = [provider] + [name for name in order if name != provider]
    if images:
        order = [name for name in order if name not in {"antigravity", "cursor", "slm-tagger"}]
    if tool_request:
        # These existing native tool adapters are verified. Text-only rungs
        # cannot silently discard tool schemas or earlier tool results.
        order = [name for name in order if name in {"anthropic", "grok"}]
    if request.get("fallback") is False:
        if provider is not None and provider not in order:
            raise GatewayError("unsupported_capability", "Selected model cannot carry this request", 422)
        order = order[:1]
    if not order:
        raise GatewayError("unsupported_capability", "No model supports this request", 422)

    def call(name, auth, default_model):
        chosen = model if name == provider and model != "antigravity-default" else default_model
        return _native_call(name, auth, chosen or "", request)

    def parse(name, payload):
        if name == "anthropic":
            text = "".join(block.get("text", "") for block in payload.get("content", []) if isinstance(block, dict) and block.get("type") == "text")
            calls = [{"id": block.get("id"), "name": block.get("name"), "input": block.get("input")} for block in payload.get("content", []) if isinstance(block, dict) and block.get("type") == "tool_use"]
            stop = payload.get("stop_reason")
            usage = payload.get("usage", {})
            input_tokens, output_tokens = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
            if stop not in {"end_turn", "tool_use"}:
                return None, text
        else:
            choice = (payload.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            text = message.get("content") or ""
            calls = []
            for item in message.get("tool_calls") or []:
                try:
                    arguments = json.loads(item.get("function", {}).get("arguments", "{}"))
                except (ValueError, TypeError):
                    return None, ""
                calls.append({"id": item.get("id"), "name": item.get("function", {}).get("name"), "input": arguments})
            stop = choice.get("finish_reason", "completed" if name == "antigravity" else None)
            if stop not in {"stop", "tool_calls", "completed"}:
                return None, text
            usage = payload.get("usage", {})
            input_tokens, output_tokens = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
        names = {tool["name"] for tool in request.get("tools") or []}
        if not isinstance(text, str) or any(not isinstance(call["id"], str) or not call["id"] or call["name"] not in names or not isinstance(call["input"], dict) for call in calls):
            return None, ""
        if request.get("toolChoice") == "none" and calls or request.get("toolChoice") == "required" and not calls:
            return None, ""
        if not text and not calls:
            return None, ""
        result = {"provider": WEB_NAMES.get(name, name), "model": payload.get("model") or (model if name == provider else engine._model_for(name, os.environ, kind="text", auth_kind="subscription")) or "antigravity-default",
                  "text": text, "stopReason": stop, "usage": {"inputTokens": input_tokens, "outputTokens": output_tokens}}
        if calls:
            result["toolCalls"] = calls
        return result, text

    parsed, _, winner, actual_model, attempted = engine._run_ladder(env=os.environ, post=engine._default_post, kind="text",log_prefix="model-gateway-chat",call_provider=call,parse_success=parse,providers=order,nvidia_vision=images)
    parsed["model"] = model if winner == provider and model != "antigravity-default" else actual_model or "antigravity-default"
    parsed["usedFallback"] = winner != order[0]
    return parsed
