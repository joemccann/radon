"""Shared model ladder: Joe order, failover, text JSON and vision paths."""
from __future__ import annotations

import json

import pytest

from clients.model_ladder import (
    MODEL_LADDER_ORDER,
    MODEL_LADDER_TIERS,
    ModelLadderExhausted,
    ModelResponseError,
    complete_multimodal_json,
    extract_via_vision,
    wired_providers,
)


ROWS = [{"underlying": "E-Mini S&P 500 Index", "position_today": 0.45}]
OBJ = {"candidates": [{"title": "test"}]}
PROMPT = "extract"
PNG = b"\x89PNG-fake"


class _Resp:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        if isinstance(payload, str):
            self.text = payload
            self._json = None
        else:
            self._json = payload
            self.text = json.dumps(payload)

    def json(self):
        if self._json is not None:
            return self._json
        return json.loads(self.text)


def _anthropic_ok(rows=None):
    return _Resp(200, {"content": [{"type": "text", "text": json.dumps(rows or ROWS)}]})


def _anthropic_obj_ok(obj=None):
    return _Resp(
        200,
        {
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": json.dumps(obj or OBJ)}],
        },
    )


def _openai_ok(rows=None):
    return _Resp(200, {"choices": [{"message": {"content": json.dumps(rows or ROWS)}}]})


def _openai_obj_ok(obj=None):
    return _Resp(200, {"choices": [{"message": {"content": json.dumps(obj or OBJ)}}]})


def _credit_low():
    return _Resp(
        400,
        {
            "error": {
                "message": (
                    "Your credit balance is too low to access the Anthropic API. "
                    "Please go to Plans & Billing to upgrade or purchase credits."
                ),
            }
        },
    )


ALL_KEYS = {
    "ANTHROPIC_API_KEY": "sk-ant-test",
    "XAI_API_KEY": "xai-test",
    "OPENAI_API_KEY": "sk-openai-test",
    "GEMINI_API_KEY": "gem-test",
    "NVIDIA_API_KEY": "nvapi-test",
    "CEREBRAS_API_KEY": "csk-test",
}


class _Router:
    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[str] = []

    def __call__(self, url, *, headers=None, json=None, timeout=None, stream=False):
        self.calls.append(url)
        for needle, resp in self.routes.items():
            if needle in url:
                return resp() if callable(resp) else resp
        return _Resp(599, {"error": {"message": f"unmocked {url}"}})


class TestLadderContract:
    def test_order_is_joes_exact_bands(self):
        assert MODEL_LADDER_ORDER == (
            "anthropic",
            "grok",
            "cursor",
            "codex",
            "gemini",
            "nvidia",
            "cerebras",
        )
        assert MODEL_LADDER_TIERS["nvidia"] == "nvidia"
        assert MODEL_LADDER_TIERS["cerebras"] == "cerebras"

    def test_cursor_is_never_wired(self):
        assert "cursor" not in wired_providers(ALL_KEYS)


class TestVisionPath:
    def test_anthropic_credit_falls_to_grok(self):
        router = _Router(
            {"api.anthropic.com": _credit_low(), "api.x.ai": _openai_ok()}
        )
        result = extract_via_vision(PNG, PROMPT, env=ALL_KEYS, post=router)
        assert result.provider == "grok"
        assert result.rows == ROWS


class TestTextJsonPath:
    def test_anthropic_credit_falls_to_grok_for_json(self):
        router = _Router(
            {"api.anthropic.com": _credit_low(), "api.x.ai": _openai_obj_ok()}
        )
        result = complete_multimodal_json(
            "evaluate",
            env=ALL_KEYS,
            post=router,
            stream_anthropic=False,
        )
        assert result.provider == "grok"
        assert result.data == OBJ

    def test_incomplete_anthropic_raises_response_error(self):
        router = _Router(
            {
                "api.anthropic.com": _Resp(
                    200,
                    {
                        "stop_reason": "max_tokens",
                        "content": [{"type": "text", "text": "{}"}],
                    },
                )
            }
        )
        with pytest.raises(ModelResponseError, match="complete"):
            complete_multimodal_json(
                "evaluate",
                env={"ANTHROPIC_API_KEY": "a"},
                post=router,
                stream_anthropic=False,
            )


class TestResearchReviewerFailover:
    def test_reviewer_falls_through_anthropic_credit(self):
        from research.model import Reviewer

        router = _Router(
            {"api.anthropic.com": _credit_low(), "api.x.ai": _openai_obj_ok()}
        )
        from types import SimpleNamespace

        reviewer = Reviewer(
            env={"ANTHROPIC_API_KEY": "a", "XAI_API_KEY": "x"},
            session=SimpleNamespace(post=router),
        )
        assert reviewer.ask("evaluate") == OBJ

    def test_exhausted_message_is_safe(self):
        router = _Router({"api.anthropic.com": _credit_low()})
        with pytest.raises(ModelLadderExhausted) as exc:
            complete_multimodal_json(
                "evaluate",
                env={"ANTHROPIC_API_KEY": "a"},
                post=router,
                stream_anthropic=False,
            )
        assert "credit_balance" in str(exc.value)
        assert "top up" not in str(exc.value).lower()


class TestAnthropicHttpxStream:
    def test_httpx_post_rejects_stream_kwarg(self):
        import httpx

        with pytest.raises(TypeError, match="unexpected keyword argument 'stream'"):
            httpx.post("https://example.invalid", json={}, timeout=0.1, stream=True)

    def test_default_adapter_streams_via_client_send_not_httpx_post(self, monkeypatch):
        """Production Reviewer() has no session; _default_post must not use httpx.post(stream=)."""
        import httpx

        from research.model import Reviewer

        closed: list[str] = []
        post_calls: list[dict] = []

        def guarded_post(*_args, **kwargs):
            post_calls.append(kwargs)
            if "stream" in kwargs:
                raise TypeError("post() got an unexpected keyword argument 'stream'")
            raise AssertionError("non-stream httpx.post should not run on the Reviewer path")

        class Response:
            status_code = 200

            def iter_bytes(self, _chunk_size):
                yield json.dumps(
                    {
                        "stop_reason": "end_turn",
                        "content": [{"type": "text", "text": json.dumps(OBJ)}],
                    }
                ).encode()

            def close(self):
                closed.append("response")

        class Client:
            def __init__(self, *, timeout):
                assert timeout == (10.0, 120.0)

            def build_request(self, method, url, *, headers, json):
                assert method == "POST"
                assert "api.anthropic.com" in url
                return object()

            def send(self, request, *, stream):
                assert stream is True
                return Response()

            def close(self):
                closed.append("client")

        monkeypatch.setattr(httpx, "post", guarded_post)
        monkeypatch.setattr(httpx, "Client", Client)
        reviewer = Reviewer(env={"ANTHROPIC_API_KEY": "a"})
        assert reviewer.ask("evaluate") == OBJ
        assert post_calls == []
        assert closed == ["response", "client"]


class TestCodexTokenParam:
    def test_gpt55_chat_uses_max_completion_tokens(self):
        captured: list[dict] = []

        def post(url, *, headers=None, json=None, timeout=None, stream=False):
            captured.append(json)
            return _openai_obj_ok()

        result = complete_multimodal_json(
            "evaluate",
            env={"OPENAI_API_KEY": "sk-openai-test"},
            post=post,
            stream_anthropic=False,
        )
        assert result.provider == "codex"
        assert captured[0]["model"] == "gpt-5.5"
        assert captured[0]["max_completion_tokens"] == 6000
        assert "max_tokens" not in captured[0]

    def test_gpt55_vision_uses_max_completion_tokens(self):
        captured: list[dict] = []

        def post(url, *, headers=None, json=None, timeout=None, stream=False):
            captured.append(json)
            return _openai_ok()

        result = extract_via_vision(
            PNG, PROMPT, env={"OPENAI_API_KEY": "sk-openai-test"}, post=post
        )
        assert result.provider == "codex"
        assert captured[0]["model"] == "gpt-5.5"
        assert captured[0]["max_completion_tokens"] == 4096
        assert "max_tokens" not in captured[0]

    def test_grok_compat_path_keeps_max_tokens(self):
        captured: list[dict] = []

        def post(url, *, headers=None, json=None, timeout=None, stream=False):
            captured.append(json)
            return _openai_obj_ok()

        result = complete_multimodal_json(
            "evaluate",
            env={"XAI_API_KEY": "xai-test"},
            post=post,
            stream_anthropic=False,
        )
        assert result.provider == "grok"
        assert captured[0]["max_tokens"] == 6000
        assert "max_completion_tokens" not in captured[0]


class TestCerebrasModelId:
    def test_default_is_not_archived_scout(self):
        from clients.model_ladder import _model_for

        vision = _model_for("cerebras", {}, kind="vision")
        text = _model_for("cerebras", {}, kind="text")
        assert "llama-4-scout" not in vision
        assert "llama-4-scout" not in text
        assert vision == "qwen-3.8-27b"
        assert text == "qwen-3.8-27b"

    def test_reviewer_path_uses_public_multimodal_id(self):
        captured: list[dict] = []

        def post(url, *, headers=None, json=None, timeout=None, stream=False):
            captured.append(json)
            assert "api.cerebras.ai" in url
            return _openai_obj_ok()

        result = complete_multimodal_json(
            "evaluate",
            env={"CEREBRAS_API_KEY": "csk-test"},
            post=post,
            stream_anthropic=False,
        )
        assert result.provider == "cerebras"
        assert captured[0]["model"] == "qwen-3.8-27b"
        assert "llama-4-scout" not in captured[0]["model"]
        assert captured[0]["reasoning_effort"] == "none"
