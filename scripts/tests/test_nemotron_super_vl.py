"""Hosted Super VL opt-in preserves the ladder's existing response contracts."""
from __future__ import annotations

import json

import pytest

from clients.model_ladder import (
    accept_tags_payload,
    complete_multimodal_json,
    complete_text_json,
    extract_via_vision,
)

pytestmark = pytest.mark.usefixtures("isolated_model_credentials")

SUPER_VL = "nvidia/nemotron-3.5-super-vl-120b-a12b"
LLAMA_11B = "meta/llama-3.2-11b-vision-instruct"
PNG = b"\x89PNG-test"
ROWS = [{"underlying": "SPX", "position_today": -0.45}]
OBJECT = {"tags": ["MACRO", "OPTIONS", "VOLATILITY"]}
ENV = {"NVIDIA_API_KEY": "nvapi-test", "RADON_LADDER_NO_AUTH_FILES": "1"}


class Response:
    """Bounded transport response, deliberately not an API SSE stream."""

    def __init__(self, status, payload):
        self.status_code = status
        self.raw = json.dumps(payload).encode()
        self.closed = False

    def iter_content(self, _chunk_size):
        yield self.raw

    def close(self):
        self.closed = True


def completion(value):
    return Response(200, {"choices": [{"message": {"content": json.dumps(value)}}]})


class Capture:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []

    def __call__(self, url, *, headers, json, timeout, stream=False):
        self.calls.append({"url": url, "body": json, "transport_stream": stream})
        return next(self.responses)


def assert_super_vl_call(call):
    assert call["url"] == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert call["body"]["model"] == SUPER_VL
    assert call["body"]["reasoning_effort"] == "none"
    assert call["body"]["stream"] is False
    # HTTP transport streaming still enforces the response byte cap.
    assert call["transport_stream"] is True


def assert_legacy_call(call):
    assert "reasoning_effort" not in call["body"]
    assert "stream" not in call["body"]


def test_super_vl_single_image_preserves_array_contract_and_closes_response():
    response = completion(ROWS)
    post = Capture(response)
    result = extract_via_vision(
        PNG, "extract numeric rows", env={**ENV, "NVIDIA_VISION_MODEL": SUPER_VL}, post=post
    )
    assert result.rows == ROWS
    assert result.model == SUPER_VL
    assert result.provider == "nvidia"
    assert_super_vl_call(post.calls[0])
    content = post.calls[0]["body"]["messages"][0]["content"]
    assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert content[1]["text"] == "extract numeric rows"
    assert response.closed


def test_super_vl_multimodal_preserves_object_and_source_labels():
    post = Capture(completion(OBJECT))
    result = complete_multimodal_json(
        "review the cited chart", images=[("source page 3", PNG)], system="ground claims",
        env={**ENV, "NVIDIA_VISION_MODEL": SUPER_VL}, post=post,
    )
    assert result.data == OBJECT
    assert result.model == SUPER_VL
    assert_super_vl_call(post.calls[0])
    messages = post.calls[0]["body"]["messages"]
    assert messages[0] == {"role": "system", "content": "ground claims"}
    assert messages[1]["content"][0] == {"type": "text", "text": "source page 3"}
    assert messages[1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_super_vl_explicit_text_pin_preserves_tag_validator():
    post = Capture(completion(OBJECT))
    result = complete_text_json(
        "tag this", env={**ENV, "NVIDIA_TEXT_MODEL": SUPER_VL}, post=post,
        accept=accept_tags_payload,
    )
    assert result.data == OBJECT
    assert result.model == SUPER_VL
    assert_super_vl_call(post.calls[0])


@pytest.mark.parametrize("kind", ["single", "multimodal"])
def test_super_vl_failure_retains_legacy_vision_fallback_without_new_controls(kind):
    value = ROWS if kind == "single" else OBJECT
    post = Capture(Response(503, {"error": {"message": "capacity"}}), completion(value))
    env = {**ENV, "NVIDIA_VISION_MODEL": SUPER_VL}
    if kind == "single":
        result = extract_via_vision(PNG, "extract", env=env, post=post)
        assert result.rows == ROWS
    else:
        result = complete_multimodal_json("review", images=[("page", PNG)], env=env, post=post)
        assert result.data == OBJECT
    assert result.model == LLAMA_11B
    assert len(post.calls) == 2
    assert_super_vl_call(post.calls[0])
    assert post.calls[1]["body"]["model"] == LLAMA_11B
    assert_legacy_call(post.calls[1])


@pytest.mark.parametrize("kind", ["single", "multimodal", "text"])
def test_existing_defaults_do_not_receive_super_vl_controls(kind):
    post = Capture(completion(ROWS if kind == "single" else OBJECT))
    if kind == "single":
        result = extract_via_vision(PNG, "extract", env=ENV, post=post)
    elif kind == "multimodal":
        result = complete_multimodal_json("review", images=[("page", PNG)], env=ENV, post=post)
    else:
        result = complete_text_json("tag", env=ENV, post=post)
    expected = "nvidia/nemotron-3-super-120b-a12b" if kind == "text" else "meta/llama-3.2-90b-vision-instruct"
    assert result.model == expected
    assert_legacy_call(post.calls[0])


def test_other_provider_does_not_receive_controls_even_with_super_vl_model_pin():
    post = Capture(completion(OBJECT))
    result = complete_text_json(
        "tag", env={"XAI_API_KEY": "x-test", "RADON_LADDER_ALLOW_PREPAID": "1",
                    "RADON_LADDER_NO_AUTH_FILES": "1", "XAI_MODEL": SUPER_VL}, post=post,
    )
    assert result.provider == "grok"
    assert "api.x.ai" in post.calls[0]["url"]
    assert_legacy_call(post.calls[0])


@pytest.mark.parametrize("bad_content", [None, "not json", '{"tags": []}'])
def test_super_vl_invalid_final_content_cannot_bypass_semantic_validation(bad_content):
    invalid = Response(200, {"choices": [{"message": {
        "content": bad_content, "reasoning_content": json.dumps(OBJECT),
    }}]})
    post = Capture(invalid, completion(OBJECT))
    result = complete_multimodal_json(
        "tag the chart", images=[("page", PNG)],
        env={**ENV, "NVIDIA_VISION_MODEL": SUPER_VL}, post=post, accept=accept_tags_payload,
    )
    assert result.data == OBJECT
    assert result.model == LLAMA_11B
    assert len(post.calls) == 2
    assert_super_vl_call(post.calls[0])
    assert_legacy_call(post.calls[1])
