"""MarketDesk ladder failures: unreadable Anthropic, Codex 429, NVIDIA 400/404, Cerebras 413.

Mocked HTTP only. No live provider calls.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from clients import model_ladder
from clients.model_ladder import (
    MODEL_LADDER_ORDER,
    ModelLadderExhausted,
    ModelResponseError,
    complete_multimodal_json,
)

pytestmark = pytest.mark.usefixtures("isolated_model_credentials")

OBJ = {"title": "desk", "content": "one fact"}
PNG = b"\x89PNG-fake"


class _Resp:
    def __init__(self, status_code: int, payload, headers=None):
        self.status_code = status_code
        self.headers = headers or {}
        if isinstance(payload, str):
            self.text = payload
            self._json = None
        else:
            self._json = payload
            self.text = json.dumps(payload)

    def json(self):
        if self._json is None:
            return json.loads(self.text)
        return self._json


def _grant_env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    (home / ".grok").mkdir(parents=True)
    (home / ".grok" / "auth.json").write_text(json.dumps({"access_token": "grok-grant"}))
    (home / ".codex").mkdir()
    (home / ".codex" / "auth.json").write_text(json.dumps(
        {"auth_mode": "chatgpt", "tokens": {"access_token": "codex-grant", "account_id": "acct"}}
    ))
    return {
        "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-test",
        "HOME": str(home),
        "CODEX_HOME": str(home / ".codex"),
        "NVIDIA_API_KEY": "nvapi-test",
        "CEREBRAS_API_KEY": "csk-test",
        "RADON_PROVIDER_COOLDOWN_STATE": str(tmp_path / "cooldown.json"),
    }


def _openai(obj=None):
    return _Resp(200, {"choices": [{"message": {"content": json.dumps(obj or OBJ)}}]})


def test_anthropic_joins_later_text_blocks_and_ignores_thinking():
    payload = {
        "stop_reason": "end_turn",
        "content": [
            {"type": "thinking", "thinking": "{not the answer}"},
            {"type": "text", "text": "draft:"},
            {"type": "text", "text": json.dumps(OBJ)},
        ],
    }
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        return _Resp(200, payload)

    result = complete_multimodal_json(
        "write", env={"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-test"}, post=post, stream_anthropic=False,
    )
    assert result.provider == "anthropic"
    assert result.data == OBJ
    assert calls == ["https://api.anthropic.com/v1/messages"]
    assert "{not the answer}" not in result.text


def test_anthropic_reads_a_text_span_list():
    payload = {
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": [{"type": "text", "text": json.dumps(OBJ)}]}],
    }

    def post(url, **kwargs):
        return _Resp(200, payload)

    result = complete_multimodal_json(
        "write", env={"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-test"}, post=post, stream_anthropic=False,
    )
    assert result.data == OBJ


def test_anthropic_sse_reply_is_read_as_a_message():
    raw = "\n".join([
        'event: message_start',
        'data: {"type":"message_start","message":{"type":"message","content":[],"stop_reason":null}}',
        'event: content_block_start',
        'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
        'event: content_block_delta',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":' + json.dumps(json.dumps(OBJ)) + '}}',
        'event: message_delta',
        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":12}}',
        'event: message_stop',
        'data: {"type":"message_stop"}',
        "",
    ]).encode()

    class Stream:
        status_code = 200
        headers = {}
        closed = False

        def iter_content(self, _size):
            yield raw

        def close(self):
            self.closed = True

    response = Stream()

    def post(url, **kwargs):
        assert kwargs["stream"] is True
        return response

    result = complete_multimodal_json(
        "write", env={"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-test"}, post=post,
    )
    assert result.data == OBJ
    assert response.closed


def test_unreadable_anthropic_does_not_fail_the_job_when_failover_is_on(tmp_path):
    env = _grant_env(tmp_path)
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        if "anthropic.com" in url:
            class Broken:
                status_code = 200
                headers = {}
                text = '{"stop_reason":'

                def iter_content(self, _size):
                    yield self.text.encode()

                def close(self):
                    return None

            return Broken()
        if "api.x.ai" in url:
            return _openai()
        return _Resp(599, {"error": "unexpected"})

    result = complete_multimodal_json(
        "write", env=env, post=post, failover_response_errors=True,
    )
    assert result.provider == "grok"
    assert "anthropic:response_unreadable" in result.attempted
    assert any("api.x.ai" in url for url in calls)


def test_incomplete_anthropic_fails_over_instead_of_ending_the_ladder(tmp_path):
    env = _grant_env(tmp_path)
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        if "anthropic.com" in url:
            return _Resp(200, {
                "stop_reason": "max_tokens",
                "usage": {"input_tokens": 10, "output_tokens": 3000},
                "content": [{"type": "text", "text": "{"}],
            })
        if "api.x.ai" in url:
            return _openai()
        return _Resp(599, {"error": "unexpected"})

    result = complete_multimodal_json(
        "write", env=env, post=post, stream_anthropic=False, failover_response_errors=True,
        require_end_turn=True,
    )
    assert result.provider == "grok"
    assert "anthropic:incomplete" in result.attempted
    with pytest.raises(ModelResponseError):
        complete_multimodal_json(
            "write", env={"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-test"}, post=post,
            stream_anthropic=False, require_end_turn=True,
        )


def test_codex_429_cooldown_skips_later_calls_until_the_reset(tmp_path, monkeypatch):
    import clients.provider_cooldown as provider_cooldown

    env = _grant_env(tmp_path)
    env.pop("CLAUDE_CODE_OAUTH_TOKEN")
    now = {"t": 1_700_000_000.0}
    monkeypatch.setattr(provider_cooldown, "_now", lambda: now["t"])
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        if "chatgpt.com" in url:
            return _Resp(
                429,
                {"error": {"message": "usage limit reached. Try again in 3d. sk-ant-oat-secretvalue"}},
                headers={"retry-after": "259200"},
            )
        if "integrate.api.nvidia.com" in url:
            return _openai()
        return _Resp(599, {"error": "unexpected"})

    first = complete_multimodal_json("write", env=env, post=post, stream_anthropic=False)
    assert first.provider == "nvidia"
    assert any("chatgpt.com" in url for url in calls)
    assert "sk-ant-oat-secretvalue" not in (tmp_path / "cooldown.json").read_text()
    calls.clear()
    now["t"] += 3600
    second = complete_multimodal_json("write", env=env, post=post, stream_anthropic=False)
    assert second.provider == "nvidia"
    assert not any("chatgpt.com" in url for url in calls)
    assert "codex:rate_limited_cooldown" in second.attempted
    now["t"] += 4 * 24 * 3600
    calls.clear()
    third = complete_multimodal_json("write", env=env, post=post, stream_anthropic=False)
    assert any("chatgpt.com" in url for url in calls)
    assert third.provider == "nvidia"


def test_nvidia_404_retries_the_documented_model_url_and_400_is_classified(tmp_path):
    env = {"NVIDIA_API_KEY": "nvapi-test", "HOME": str(tmp_path / "home"), "RADON_LADDER_NO_AUTH_FILES": "1"}
    (tmp_path / "home").mkdir()
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs["json"]["model"]))
        if url.endswith("/chat/completions") and kwargs["json"]["model"].endswith("90b-vision-instruct"):
            return _Resp(404, {"error": {"message": "model not found"}})
        if "llama-3.2-90b-vision-instruct" in url and "/chat/completions" not in url:
            return _Resp(200, {"choices": [{"message": {"content": json.dumps([{"ok": 1}])}}]})
        if kwargs["json"]["model"].endswith("11b-vision-instruct"):
            return _Resp(400, {"error": {"message": "invalid image payload"}})
        return _Resp(599, {"error": "unexpected"})

    result = model_ladder.extract_via_vision(PNG, "extract", env=env, post=post)
    assert result.provider == "nvidia"
    assert result.model == "meta/llama-3.2-90b-vision-instruct"
    assert calls[0][0] == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert calls[1][0] == "https://integrate.api.nvidia.com/v1/meta/llama-3.2-90b-vision-instruct"
    assert len(calls) == 2


def test_nvidia_model_miss_on_both_urls_is_model_not_found_and_the_next_rung_wins(tmp_path):
    env = _grant_env(tmp_path)
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        if "integrate.api.nvidia.com" in url:
            return _Resp(400, {"error": {"message": "unknown model id"}})
        if "api.cerebras.ai" in url:
            return _openai()
        return _Resp(503, {"error": {"message": "down"}})

    result = complete_multimodal_json(
        "write", images=[("chart", PNG)], env=env, post=post, stream_anthropic=False,
    )
    assert result.provider == "cerebras"
    assert "nvidia:model_not_found" in result.attempted
    dedicated = [url for url in calls if "llama-3.2" in url and "/chat/completions" not in url]
    assert dedicated, calls


def test_cerebras_skips_an_oversize_image_request_and_a_413_is_not_retried(tmp_path):
    env = _grant_env(tmp_path)
    env["CEREBRAS_MAX_IMAGES"] = "2"
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        if "api.cerebras.ai" in url:
            return _Resp(413, {"error": {"message": "Total request size exceeds maximum"}})
        return _Resp(503, {"error": {"message": "down"}})

    images = [("c0", PNG)]
    with pytest.raises(ModelLadderExhausted) as caught:
        complete_multimodal_json("write", images=images, env=env, post=post, stream_anthropic=False)
    assert "cerebras:request_too_large" in str(caught.value)
    assert sum("api.cerebras.ai" in url for url in calls) == 1
    calls.clear()
    with pytest.raises(ModelLadderExhausted) as again:
        complete_multimodal_json("write", images=images, env=env, post=post, stream_anthropic=False)
    assert "cerebras:request_too_large" in str(again.value)
    assert not any("api.cerebras.ai" in url for url in calls)


def test_cerebras_does_not_send_when_the_image_count_exceeds_its_limit(tmp_path):
    wide = _grant_env(tmp_path)
    wide["CEREBRAS_MAX_IMAGES"] = "2"
    wide_calls = []

    def wide_post(url, **kwargs):
        wide_calls.append(url)
        return _Resp(503, {"error": {"message": "down"}})

    with pytest.raises(ModelLadderExhausted) as oversized:
        complete_multimodal_json(
            "write", images=[("a", PNG), ("b", PNG), ("c", PNG)], env=wide, post=wide_post,
            stream_anthropic=False,
        )
    assert "cerebras:request_too_large" in str(oversized.value)
    assert not any("api.cerebras.ai" in url for url in wide_calls)


def test_ladder_order_is_explicit_and_cerebras_stays_last():
    env = {"RADON_MODEL_LADDER_ORDER": "nvidia, grok, cerebras, no-such, anthropic"}
    assert model_ladder.ladder_order(env) == ("nvidia", "grok", "cerebras", "anthropic")
    assert model_ladder._last_resort_last(model_ladder.ladder_order(env))[-1] == "cerebras"
    assert model_ladder.ladder_order({}) == MODEL_LADDER_ORDER


def test_timeout_cooldown_skips_the_provider_for_the_rest_of_the_process(tmp_path):
    env = _grant_env(tmp_path)
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        if "anthropic.com" in url:
            raise TimeoutError("slow")
        if "api.x.ai" in url:
            return _openai()
        return _Resp(599, {"error": "unexpected"})

    first = complete_multimodal_json(
        "write", env=env, post=post, stream_anthropic=False, cooldown_timeouts=True,
    )
    assert first.provider == "grok"
    calls.clear()
    second = complete_multimodal_json(
        "write", env=env, post=post, stream_anthropic=False, cooldown_timeouts=True,
    )
    assert second.provider == "grok"
    assert not any("anthropic.com" in url for url in calls)
    assert "anthropic:timeout_cooldown" in second.attempted


def test_antigravity_image_skip_is_explicit_and_not_an_invented_http_api(tmp_path, monkeypatch):
    home = tmp_path / "agy"
    token = home / ".gemini" / "antigravity-cli"
    token.mkdir(parents=True)
    (token / "antigravity-oauth-token").write_text("synthetic-grant")
    monkeypatch.setattr(model_ladder.shutil, "which", lambda name: "/usr/bin/agy" if name == "agy" else None)
    monkeypatch.setattr(model_ladder.subprocess, "run", lambda *a, **k: pytest.fail("agy must not run for images"))
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        if "integrate.api.nvidia.com" in url:
            return _openai()
        return _Resp(503, {"error": {"message": "down"}})

    result = complete_multimodal_json(
        "write", images=[("chart", PNG)],
        env={"HOME": str(home), "NVIDIA_API_KEY": "nvapi-test", "ANTIGRAVITY_CLI": "/bin/true", "PATH": "/usr/bin"},
        post=post, stream_anthropic=False,
    )
    assert result.provider == "nvidia"
    assert "antigravity:image_unsupported" in result.attempted
    assert not any("generativelanguage" in url for url in calls)
