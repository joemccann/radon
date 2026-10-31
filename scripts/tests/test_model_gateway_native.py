"""Native CLI protocol support preserves tool semantics behind service-owned auth."""
from __future__ import annotations
import copy
import importlib
import json
import pytest
from model_gateway import engine
from model_gateway.client import GatewayError


def event(value):
    return "data: " + json.dumps(value) + "\n\n"


def responses_end(model="gpt-6.1-sol"):
    return event({"type": "response.completed", "response": {"status": "completed", "model": model, "usage": {"input_tokens": 1, "output_tokens": 1}}})


class Response:
    def __init__(self, raw, status=200):
        self.raw, self.status_code, self.closed = raw.encode(), status, False
    def iter_content(self, _size):
        yield self.raw
    def close(self):
        self.closed = True


def module(monkeypatch, providers):
    native = importlib.import_module("model_gateway.native")
    monkeypatch.setattr(engine, "_auth_for", lambda name, env: engine.AuthMaterial("FAKE-service-grant", "subscription" if name in {"codex", "anthropic", "grok"} else "api_key", "test", "fake-account") if name in providers else None)
    return native


def test_codex_custom_grammar_and_tool_search_are_forwarded_unchanged(monkeypatch):
    native = module(monkeypatch, {"codex"})
    request = {"model": "gpt-6.1-sol", "input": [], "stream": True, "store": False,
               "tools": [{"type": "custom", "name": "apply_patch", "format": {"type": "grammar", "syntax": "lark", "definition": "start: /.+/"}}, {"type": "tool_search"}],
               "reasoning": {"effort": "medium"}, "prompt_cache_key": "cache-fixture", "client_metadata": {"turn": "t"}}
    original = copy.deepcopy(request)
    seen = []
    response = Response(responses_end())
    result = native.complete({"protocol": "responses", "request": request, "stream": True}, post=lambda url, **kw: seen.append((url, kw)) or response, env={})
    assert request == original
    assert seen[0][1]["json"] == original
    assert seen[0][0] == engine._CHATGPT_CODEX_RESPONSES_URL
    assert seen[0][1]["headers"]["authorization"] == "Bearer FAKE-service-grant"
    assert result["body"] == responses_end()
    assert result["content_type"] == "text/event-stream"
    assert response.closed


@pytest.mark.parametrize("raw", [responses_end().rstrip("\n"), event({"type": "response.output_text.delta", "delta": "partial"}), event({"type": "response.completed", "response": {"status": "incomplete"}}), responses_end()+event({"type": "error", "message": "FAKE-provider-sensitive"})])
def test_responses_never_forward_partial_or_failed_streams(monkeypatch, raw):
    native = module(monkeypatch, {"codex"})
    with pytest.raises((GatewayError, engine.ModelLadderExhausted, engine.ModelResponseError)):
        native.complete({"protocol": "responses", "request": {"model": "gpt-6.1-sol", "input": [], "stream": True}, "stream": True}, post=lambda *a, **k: Response(raw), env={})


def test_claude_thinking_cache_blocks_and_context_management_are_preserved(monkeypatch):
    native = module(monkeypatch, {"anthropic"})
    request = {"model": "claude-opus-5", "stream": True, "max_tokens": 2048,
        "system": [{"type": "text", "text": engine._CLAUDE_CODE_SYSTEM_PREFIX, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": "review"}],
        "thinking": {"type": "adaptive"}, "context_management": {"edits": []},
        "output_config": {"effort": "medium"}, "tools": [{"name": "Read", "input_schema": {"type": "object"}}]}
    raw = event({"type": "message_start", "message": {"usage": {"input_tokens": 1, "output_tokens": 0}}}) + event({"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 1}}) + event({"type": "message_stop"})
    seen = []
    result = native.complete({"protocol": "messages", "request": request, "stream": True}, post=lambda url, **kw: seen.append(kw) or Response(raw), env={})
    assert seen[0]["json"] == request
    assert result["body"] == raw


def test_chat_quota_failure_moves_to_next_capable_rung(monkeypatch):
    native = module(monkeypatch, {"grok", "nvidia"})
    urls = []
    raw = event({"choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}) + "data: [DONE]\n\n"
    def post(url, **kwargs):
        urls.append(url)
        return Response('{"error":{"message":"spending limit"}}', 402) if "api.x.ai" in url else Response(raw)
    result = native.complete({"protocol": "chat_completions", "request": {"model": "grok-4.6", "messages": [{"role": "user", "content": "x"}], "stream": True, "tools": [{"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}]}, "stream": True}, post=post, env={})
    assert result["provider"] == "nvidia"
    assert len(urls) == 2


@pytest.mark.parametrize("payload", [{"protocol": "execute", "request": {}, "stream": True}, {"protocol": "responses", "request": {}, "stream": True, "headers": {"Authorization": "caller-secret"}}, {"protocol": "responses", "request": {"model": "gpt-6.1-sol", "stream": False}, "stream": True}])
def test_native_never_accepts_unknown_transport_or_inconsistent_stream(payload, monkeypatch):
    native = module(monkeypatch, {"codex"})
    with pytest.raises(GatewayError):
        native.complete(payload, post=lambda *a, **k: pytest.fail("must refuse before provider HTTP"), env={})


def test_explicit_gpt_native_model_cannot_be_replaced_by_a_grok_reviewer(monkeypatch):
    native = module(monkeypatch, {"grok", "codex"})
    urls=[]
    result=native.complete({"protocol":"responses","request":{"model":"gpt-6-luna","input":[],"stream":True,"tools":[]},"stream":True}, post=lambda url,**kw:urls.append(url) or Response(responses_end("gpt-6-luna")),env={})
    assert result["provider"]=="codex" and result["model"]=="gpt-6-luna"
    assert urls == [engine._CHATGPT_CODEX_RESPONSES_URL]


def test_claude_native_headers_include_verified_pinned_advanced_feature_betas(monkeypatch):
    native=module(monkeypatch,{"anthropic"})
    seen=[]
    raw=event({"type":"message_start","message":{"usage":{"input_tokens":1,"output_tokens":0}}})+event({"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":1}})+event({"type":"message_stop"})
    native.complete({"protocol":"messages","request":{"model":"claude-opus-5","messages":[],"stream":True},"stream":True},post=lambda url,**kw:seen.append(kw["headers"]) or Response(raw),env={})
    beta=set(seen[0]["anthropic-beta"].split(","))
    assert {"context-management-2025-06-27","mid-conversation-tool-changes-2026-07-01","thinking-token-count-2026-05-13","advanced-tool-use-2025-11-20","effort-2025-11-24"} <= beta
    assert seen[0]["x-app"]=="cli"
