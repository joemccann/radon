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


class _StreamingResponse:
    """Production-shaped response: parsing before streaming is a test failure."""

    def __init__(self, payload: dict, *, chunks: list[bytes] | None = None):
        self.status_code = 200
        self._chunks = chunks or [json.dumps(payload).encode()]
        self.closed = False

    def iter_content(self, _chunk_size):
        return iter(self._chunks)

    @property
    def text(self):
        pytest.fail("response text was materialized before enforcing the byte cap")

    def json(self):
        pytest.fail("response JSON was materialized before enforcing the byte cap")

    def close(self):
        self.closed = True


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

    @pytest.mark.parametrize(
        ("env", "needle", "payload"),
        [
            ({"ANTHROPIC_API_KEY": "a"}, "api.anthropic.com", {"stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps(OBJ)}]}),
            ({"XAI_API_KEY": "x"}, "api.x.ai", {"choices": [{"message": {"content": json.dumps(OBJ)}}]}),
            ({"OPENAI_API_KEY": "o"}, "api.openai.com", {"choices": [{"message": {"content": json.dumps(OBJ)}}]}),
            ({"GEMINI_API_KEY": "g"}, "generativelanguage.googleapis.com", {"candidates": [{"content": {"parts": [{"text": json.dumps(OBJ)}]}}]}),
            ({"NVIDIA_API_KEY": "n"}, "integrate.api.nvidia.com", {"choices": [{"message": {"content": json.dumps(OBJ)}}]}),
            ({"CEREBRAS_API_KEY": "c"}, "api.cerebras.ai", {"choices": [{"message": {"content": json.dumps(OBJ)}}]}),
        ],
    )
    def test_every_text_provider_streams_and_closes_before_parsing(self, env, needle, payload):
        response = _StreamingResponse(payload)
        calls = []

        def post(url, **kwargs):
            calls.append((url, kwargs))
            assert needle in url
            return response

        assert complete_multimodal_json("evaluate", env=env, post=post).data == OBJ
        assert calls[0][1]["stream"] is True
        assert response.closed

    def test_oversized_streaming_response_never_materializes_text_or_json(self):
        response = _StreamingResponse(
            {}, chunks=[b"x" * 17, b"x" * 17]
        )

        with pytest.raises(ModelResponseError, match="exceeds limit"):
            complete_multimodal_json(
                "evaluate",
                env={"ANTHROPIC_API_KEY": "a"},
                post=lambda *_args, **_kwargs: response,
                max_response_bytes=32,
            )
        assert response.closed


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
