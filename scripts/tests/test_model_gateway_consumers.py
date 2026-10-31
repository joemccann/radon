"""App model consumers cross the private gateway without provider credentials."""
from __future__ import annotations

import base64
from pathlib import Path

import pytest


def test_json_accept_rejection_continues_on_server(monkeypatch):
    from clients import model_ladder
    calls = []
    replies = [dict(data={"ok": False}, text='{"ok": false}', provider="antigravity", model="a", attempted=["antigravity:ok"], continuation="opaque-next"),
               dict(data={"ok": True}, text='{"ok": true}', provider="anthropic", model="b", attempted=["antigravity:rejected", "anthropic:ok"])]
    def request(operation, payload, **kwargs):
        calls.append((operation, payload.copy()))
        return replies.pop(0)
    monkeypatch.setattr(model_ladder, "gateway_request", request)
    result = model_ladder.complete_text_json("test", accept=lambda data: data["ok"])
    assert result.provider == "anthropic"
    assert calls[0][0] == calls[1][0] == "json"
    assert calls[1][1]["continuation"] == "opaque-next"
    assert "providers" not in calls[1][1] or calls[1][1]["providers"] is None


def test_json_payload_never_inherits_model_credentials(monkeypatch):
    from clients import model_ladder
    seen = []
    monkeypatch.setattr(model_ladder, "gateway_request", lambda op, payload, **kw: seen.append(payload) or {"data": {}, "text": "{}", "provider": "antigravity", "model": ""})
    model_ladder.complete_text_json("test", env={"NVIDIA_API_KEY": "do-not-send", "HOME": "/secret"})
    assert "do-not-send" not in repr(seen)
    assert "env" not in seen[0]


def test_images_are_encoded_locally_and_preserved(monkeypatch, tmp_path):
    from clients import model_ladder
    seen = []
    image = tmp_path / "chart.png"
    image.write_bytes(b"source-image-bytes")
    monkeypatch.setattr(model_ladder, "gateway_request", lambda op, payload, **kw: seen.append(payload) or {"data": {}, "text": "{}", "provider": "anthropic", "model": "m"})
    model_ladder.complete_multimodal_json("test", [("chart", image)])
    assert seen[0]["images"][0] == {"label": "chart", "media_type": "image/png", "data": base64.b64encode(image.read_bytes()).decode()}
    assert str(image) not in repr(seen)


def test_rejected_final_response_is_exhaustion(monkeypatch):
    from clients import model_ladder
    monkeypatch.setattr(model_ladder, "gateway_request", lambda *a, **kw: {"data": {"ok": False}, "text": "{}", "provider": "antigravity", "model": ""})
    with pytest.raises(model_ladder.ModelLadderExhausted):
        model_ladder.complete_text_json("test", accept=lambda data: False)


def test_vision_keeps_result_contract(monkeypatch):
    from clients import model_ladder
    seen = []
    monkeypatch.setattr(model_ladder, "gateway_request", lambda op, payload, **kw: seen.append((op, payload)) or {"rows": [{"name": "SPX"}], "provider": "nvidia", "model": "vision", "attempted": ["nvidia:ok"]})
    result = model_ladder.extract_via_vision(b"png", "extract")
    assert result.rows == [{"name": "SPX"}]
    assert result.attempted == ("nvidia:ok",)
    assert seen[0][0] == "vision"


def test_embeddings_keep_spaces_explicit(monkeypatch):
    from knowledge import embed
    calls = []
    monkeypatch.setattr(embed, "gateway_request", lambda op, payload, **kw: calls.append((op, payload)) or {"vectors": [[1.0] * payload["dimensions"]]})
    assert len(embed.embed_query(["query"])[0]) == 2048
    assert len(embed._gateway_local_embed(["passage"])[0]) == 384
    assert calls[0][1]["input_type"] == "query"
    assert [item[1]["dimensions"] for item in calls] == [2048, 384]


def test_cta_uses_text_gateway_and_preserves_remaining_budget(monkeypatch):
    from utils import cta_llm
    seen = []
    monkeypatch.setattr(cta_llm, "gateway_request", lambda op, payload, **kw: seen.append((op, payload, kw)) or {"text": "verified copy"})
    result = cta_llm._anthropic_caller(system="sys", user="facts", model="claude", env={"NVIDIA_API_KEY": "never"}, timeout=4)
    assert result == "verified copy"
    assert seen[0][0] == "chat"
    assert seen[0][2]["timeout_seconds"] == 4
    assert "never" not in repr(seen)


def test_x_search_uses_capability_operation(monkeypatch):
    import fetch_x_xai
    calls = []
    monkeypatch.setattr(fetch_x_xai, "gateway_request", lambda op, payload, **kw: calls.append((op, payload)) or {"payload": {"output": []}})
    assert fetch_x_xai.xai_search("some_account", days=3) == {"output": []}
    assert calls[0][0] == "x-search"
    assert calls[0][1]["account"] == "some_account"


@pytest.mark.parametrize("code,error", [("exhausted", "ModelLadderExhausted"), ("incomplete", "ModelResponseError"), ("response_too_large", "ModelResponseError"), ("unavailable", "ModelLadderExhausted")])
def test_gateway_failures_preserve_consumer_error_types(monkeypatch, code, error):
    from clients import model_ladder
    from model_gateway.client import GatewayError
    def request(*a, **kw):
        raise GatewayError(code)
    monkeypatch.setattr(model_ladder, "gateway_request", request)
    with pytest.raises(getattr(model_ladder, error)):
        model_ladder.complete_text_json("test")


@pytest.mark.parametrize("reply", [{}, {"data": {}, "text": 1, "provider": "a", "model": "m"}, {"data": {}, "text": "{}", "provider": "", "model": "m"}, {"data": {}, "text": "{}", "provider": "a", "model": "m", "attempted": [1]}])
def test_malformed_gateway_envelopes_fail_closed(monkeypatch, reply):
    from clients import model_ladder
    monkeypatch.setattr(model_ladder, "gateway_request", lambda *a, **kw: reply)
    with pytest.raises(model_ladder.ModelResponseError):
        model_ladder.complete_text_json("test")


def test_ocr_consumer_preserves_image_and_uses_gateway(monkeypatch):
    from research import nemotron_parse
    seen = []
    monkeypatch.setattr(nemotron_parse, "gateway_request", lambda op, payload, **kw: seen.append((op, payload)) or {"payload": {"choices": []}})
    body = {"messages": [{"content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,cG5n"}}]}]}
    assert nemotron_parse._post_with_retries("nvidia/nemotron-parse", body, post=None, sleep=None, monotonic=lambda: 1, jitter=None, deadline=10) == {"choices": []}
    assert seen[0][0] == "ocr"
    assert seen[0][1]["image"] == "cG5n"
    assert seen[0][1]["timeout_seconds"] == 9


def test_research_reviewer_uses_gateway_inventory_and_json(monkeypatch):
    from clients import model_ladder
    from research.model import Reviewer
    calls = []
    def request(operation, payload, **kwargs):
        calls.append(operation)
        if operation == "status":
            return {"providers": ["antigravity"]}
        return {"data": {"candidates": []}, "text": '{"candidates":[]}', "provider": "antigravity", "model": ""}
    monkeypatch.setattr(model_ladder, "gateway_request", request)
    assert Reviewer(env={}).ask_text("select") == {"candidates": []}
    assert calls == ["status", "status", "json"]


def test_clairic_consumer_does_not_read_candidate_credentials(monkeypatch):
    from research import clairic
    calls = []
    monkeypatch.setattr(clairic, "gateway_request", lambda op, payload, **kw: calls.append((op, payload)) or {"date": "2026-10-17", "confidence": 0.95})
    assert clairic.fetch_report_date({1: "Report published October 17, 2026"}, "2026-10-17", env={"CLOUDFLARE_API_TOKEN": "must-stay-local"}) == ("2026-10-17", 0.95)
    assert calls[0][0] == "clairic"
    assert "must-stay-local" not in repr(calls)


def test_exhaustion_preserves_safe_content_vs_outage_classification(monkeypatch):
    from clients import model_ladder
    from model_gateway.client import GatewayError
    from research.marketdesk_copy import _every_rung_rejected
    for attempted, content_only in [(("antigravity:unparseable",), True), (("antigravity:unparseable", "anthropic:http_429"), False)]:
        def request(*a, **kw):
            raise GatewayError("exhausted", attempted=attempted)
        monkeypatch.setattr(model_ladder, "gateway_request", request)
        with pytest.raises(model_ladder.ModelLadderExhausted) as error:
            model_ladder.complete_text_json("test")
        assert _every_rung_rejected(str(error.value)) is content_only


@pytest.mark.parametrize("reply", [{"rows": [], "provider": "", "model": "m"}, {"rows": [], "provider": "nvidia", "model": 1}, {"rows": [1], "provider": "nvidia", "model": "m"}, {"rows": [], "provider": "nvidia", "model": "m", "attempted": [1]}])
def test_vision_rejects_malformed_gateway_metadata(monkeypatch, reply):
    from clients import model_ladder
    monkeypatch.setattr(model_ladder, "gateway_request", lambda *a, **kw: reply)
    with pytest.raises(model_ladder.ModelResponseError):
        model_ladder.extract_via_vision(b"image", "extract")


def test_vision_maps_safe_gateway_exhaustion(monkeypatch):
    from clients import model_ladder
    from model_gateway.client import GatewayError
    def fail(*a, **kw):
        raise GatewayError("exhausted", attempted=["grok:quota_or_billing"])
    monkeypatch.setattr(model_ladder, "gateway_request", fail)
    with pytest.raises(model_ladder.ModelLadderExhausted, match="grok:quota_or_billing"):
        model_ladder.extract_via_vision(b"image", "extract")


@pytest.mark.parametrize('dimensions,input_type',[(2048,'passage'),(2048,'query'),(384,'passage')])
def test_embedding_facades_chunk_large_calls_without_changing_spaces_or_order(monkeypatch,dimensions,input_type):
    from knowledge import embed
    texts=[str(index) for index in range(257)];calls=[]
    def request(operation,payload,**kwargs):
        assert operation=='embed'
        assert 1<=len(payload['texts'])<=16
        assert payload['dimensions']==dimensions and payload['input_type']==input_type
        calls.append(list(payload['texts']))
        return {'vectors':[[float(text),*([0.]*(dimensions-1))] for text in payload['texts']]}
    monkeypatch.setattr(embed,'gateway_request',request)
    vectors=embed._gateway_local_embed(texts) if dimensions==384 else embed._nvidia_embed(texts,input_type=input_type)
    assert [text for batch in calls for text in batch]==texts
    assert len(calls)==17 and [vector[0] for vector in vectors]==list(range(257))
    assert all(len(vector)==dimensions for vector in vectors)


def test_embedding_later_batch_failure_never_returns_partial_or_mixed_space(monkeypatch):
    from knowledge import embed
    calls=[]
    def request(operation,payload,**kwargs):
        calls.append(payload)
        dimensions=2048 if len(calls)==1 else 384
        return {'vectors':[[0.]*dimensions for _ in payload['texts']]}
    monkeypatch.setattr(embed,'gateway_request',request)
    with pytest.raises(RuntimeError,match='dimension mismatch'):
        embed._nvidia_embed([str(i) for i in range(33)],input_type='passage')
    assert len(calls)==2 and all(call['dimensions']==2048 for call in calls)
