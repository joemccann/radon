"""Shared HTTP model ladder: Joe order, credit fallthrough, research wiring."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from clients.model_ladder import (
    MODEL_LADDER_ORDER,
    MODEL_LADDER_TIERS,
    ladder_order,
    run_vision_extract_ladder,
    wired_providers,
)
from research.model import ModelError, Reviewer


ROWS = [{"underlying": "E-Mini S&P 500 Index", "position_today": 0.45}]
PROMPT = "extract"
PNG = b"\x89PNG-fake"
ALL_KEYS = {
    "ANTHROPIC_API_KEY": "sk-ant-test",
    "XAI_API_KEY": "xai-test",
    "OPENAI_API_KEY": "sk-openai-test",
    "GEMINI_API_KEY": "gem-test",
    "NVIDIA_API_KEY": "nvapi-test",
    "CEREBRAS_API_KEY": "csk-test",
}


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


def _anthropic_json_ok(value=None):
    payload = value or {"candidates": []}
    return _Resp(
        200,
        {
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": json.dumps(payload)}],
        },
    )


def _openai_ok(rows=None):
    return _Resp(200, {"choices": [{"message": {"content": json.dumps(rows or ROWS)}}]})


def _openai_json_ok(value=None):
    payload = value or {"candidates": []}
    return _Resp(200, {"choices": [{"message": {"content": json.dumps(payload)}}]})


def _credit_low():
    return _Resp(
        400,
        {
            "error": {
                "type": "invalid_request_error",
                "message": (
                    "Your credit balance is too low to access the Anthropic API. "
                    "Please go to Plans & Billing to upgrade or purchase credits."
                ),
            }
        },
    )


class _Router:
    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[str] = []

    def __call__(self, url, *, headers=None, json=None, timeout=None):
        self.calls.append(url)
        for needle, resp in self.routes.items():
            if needle in url:
                return resp() if callable(resp) else resp
        return _Resp(599, {"error": {"message": f"unmocked {url}"}})


class _Session:
    def __init__(self, router):
        self.router = router

    def post(self, url, *, headers=None, json=None, timeout=None, stream=False):
        return _StreamResp(self.router(url, headers=headers, json=json, timeout=timeout))


class _StreamResp:
    def __init__(self, resp: _Resp):
        self.status_code = resp.status_code
        self._body = resp.text.encode()
        self.text = resp.text

    def iter_content(self, _chunk_size):
        yield self._body

    def close(self):
        return None


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
        assert MODEL_LADDER_TIERS["anthropic"] == "subscription"

    def test_env_override_order(self):
        env = {"RADON_HTTP_MODEL_LADDER": "grok nvidia"}
        assert ladder_order(env) == ("grok", "nvidia")

    def test_cursor_is_never_wired(self):
        assert "cursor" not in wired_providers(ALL_KEYS)


class TestVisionFailover:
    def test_anthropic_credit_falls_to_grok(self):
        router = _Router(
            {"api.anthropic.com": _credit_low(), "api.x.ai": _openai_ok()}
        )
        result = run_vision_extract_ladder(PNG, PROMPT, env=ALL_KEYS, post=router)
        assert result.provider == "grok"
        assert result.rows == ROWS


class TestResearchReviewerLadder:
    def test_credit_on_anthropic_falls_to_grok(self, tmp_path):
        chart = tmp_path / "chart.png"
        chart.write_bytes(b"png")
        router = _Router(
            {
                "api.anthropic.com": _credit_low(),
                "api.x.ai": _openai_json_ok({"candidates": [{"title": "ok"}]}),
            }
        )
        reviewer = Reviewer(env=ALL_KEYS, session=_Session(router))
        value = reviewer.ask("evaluate", [("page", chart)])
        assert value == {"candidates": [{"title": "ok"}]}
        assert reviewer.provider == "grok"

    def test_exhausted_message_is_safe_not_class_name_only(self):
        router = _Router({"api.anthropic.com": _credit_low()})
        env = {"ANTHROPIC_API_KEY": "a"}
        with pytest.raises(ModelError) as exc:
            Reviewer(env=env, session=_Session(router)).ask("evaluate")
        msg = str(exc.value)
        assert "Model ladder exhausted" in msg
        assert "credit_balance" in msg
        assert msg != "ModelError"

    def test_requires_at_least_one_key(self):
        with pytest.raises(ModelError, match="No keyed model provider"):
            Reviewer(env={})


class TestVisionCascadeWrapper:
    def test_wrapper_delegates_to_shared_core(self):
        from clients import vision_cascade

        assert vision_cascade.VISION_CASCADE_ORDER is MODEL_LADDER_ORDER
        router = _Router({"api.anthropic.com": _anthropic_ok()})
        result = vision_cascade.extract_via_vision(PNG, PROMPT, env=ALL_KEYS, post=router)
        assert result.provider == "anthropic"
