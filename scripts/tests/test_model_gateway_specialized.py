"""Fixed-capability service operations never accept arbitrary provider transport."""
from __future__ import annotations
import base64
import pytest
from model_gateway import specialized


@pytest.mark.parametrize("operation", ["embed", "ocr", "x-search", "forecast", "validate-credential", "refresh-catalog"])
def test_specialized_rejects_unknown_transport_fields(operation):
    with pytest.raises(ValueError):
        specialized.dispatch(operation, {"url": "http://169.254.169.254/"})


def test_unknown_operation_refused():
    with pytest.raises(ValueError):
        specialized.dispatch("execute", {})


@pytest.mark.parametrize("dimensions", [True, 12, "384", None])
def test_embedding_spaces_cannot_be_invented(dimensions):
    with pytest.raises(ValueError):
        specialized.embed({"texts": ["text"], "dimensions": dimensions, "input_type": "query"})


def test_embedding_spaces_never_fallback_to_another_model(monkeypatch):
    from model_gateway import _embedding_backend as backend
    calls = []
    monkeypatch.setattr(backend, "_nvidia_embed", lambda *a, **kw: calls.append(kw) or [[0.0] * 384])
    with pytest.raises(ValueError, match="space"):
        specialized.embed({"texts": ["query"], "dimensions": 2048, "input_type": "query"})
    assert calls[0]["input_type"] == "query"


def test_local_embedding_model_is_cached_in_service(monkeypatch):
    from model_gateway import _embedding_backend as backend
    calls = []
    monkeypatch.setattr(specialized, "_LOCAL_EMBEDDER", None)
    monkeypatch.setattr(backend, "_build_embedder", lambda: calls.append(True) or (lambda texts: [[0.0] * 384 for _ in texts]))
    for _ in range(2):
        result = specialized.embed({"texts": ["text"], "dimensions": 384, "input_type": "passage"})
        assert result["dimensions"] == 384
    assert calls == [True]


def test_ocr_native_payload_and_deadline_preserved(monkeypatch):
    from model_gateway import _ocr_backend as backend
    seen = []
    monkeypatch.setattr(backend, "_clock", lambda _: 10)
    monkeypatch.setattr(backend, "_post_with_retries", lambda *args, **kw: seen.append((args, kw)) or {"choices": []})
    result = specialized.ocr({"model": "nvidia/nemotron-parse", "image": base64.b64encode(b"png").decode(), "timeout_seconds": 20})
    assert result == {"payload": {"choices": []}}
    assert seen[0][1]["deadline"] == 30
    assert seen[0][0][1]["tools"][0]["function"]["name"] == "markdown_bbox"


@pytest.mark.parametrize("account", ["../../file", "a" * 16, "@user", "http://x"])
def test_search_account_is_not_a_url_or_path(account):
    with pytest.raises(ValueError):
        specialized.x_search({"account": account})


def test_native_search_keeps_provider_envelope(monkeypatch):
    from model_gateway import _x_search_backend as backend
    monkeypatch.setattr(backend, "xai_search", lambda account, days: {"output": [{"text": account}], "days": days})
    assert specialized.x_search({"account": "user", "days": 2})["payload"]["days"] == 2


def test_forecast_optional_runtime_is_advertised(monkeypatch):
    from model_gateway import _forecast_backend as backend
    monkeypatch.setattr(backend, "is_available", lambda: False)
    assert specialized.forecast_available() is False


@pytest.mark.parametrize("payload", [{"provider": "other", "candidate": "x"}, {"provider": "nvidia", "candidate": "x\nInjected: header"}])
def test_credential_validation_cannot_target_arbitrary_provider(payload):
    with pytest.raises(ValueError):
        specialized.validate_credential(payload)


def test_full_inventory_keeps_metadata_and_does_not_fabricate_context(monkeypatch):
    monkeypatch.setattr(specialized, "_codex_metadata_rows", lambda stamp: [])
    from model_gateway import engine, _catalog_backend as backend
    monkeypatch.setattr(engine, "wired_providers", lambda: ("anthropic", "grok", "codex"))
    monkeypatch.setattr(engine, "_model_for", lambda name, *a, **k: {"anthropic": "claude-opus-5", "grok": "grok-4.7", "codex": "gpt-6.1-sol"}[name])
    monkeypatch.setattr(backend, "provider_key", lambda name: "service-grant" if name in {"anthropic", "xai"} else None)
    monkeypatch.setattr(backend, "fetch_anthropic_models", lambda key: [{"id": "claude-opus-5", "type": "model", "display_name": "Opus", "max_input_tokens": 100000, "capabilities": {"image_input": {"supported": True}}}, {"id": "claude-haiku-4", "type": "model"}])
    monkeypatch.setattr(backend, "fetch_xai_models", lambda key: [{"id": "grok-4.7", "input_modalities": ["text", "image"], "output_modalities": ["text"]}, {"id": "grok-4.6", "input_modalities": ["text"], "output_modalities": ["text"]}, {"id": "grok-image", "input_modalities": ["text"], "output_modalities": ["image"]}])
    result = specialized.model_inventory(refresh=True)
    rows = {row["id"]: row for row in result["models"]}
    assert set(rows) == {"claude-opus-5", "claude-haiku-4", "grok-4.7", "grok-4.6", "gpt-6.1-sol"}
    assert rows["claude-opus-5"]["contextWindow"] == 100000
    assert "contextWindow" not in rows["grok-4.7"]
    assert rows["gpt-6.1-sol"]["source"] == "configured"
    assert rows["claude-haiku-4"]["source"] == "provider-metadata"
    assert "images" not in rows["claude-haiku-4"]["capabilities"]
    monkeypatch.setattr(backend, "fetch_xai_models", lambda *a: pytest.fail("cached inventory cannot probe providers"))
    assert specialized.model_inventory()["models"] == result["models"]


def test_inventory_refresh_failure_keeps_previous_metadata(monkeypatch):
    from model_gateway import engine, _catalog_backend as backend
    monkeypatch.setattr(engine, "wired_providers", lambda: ("grok",))
    monkeypatch.setattr(engine, "_model_for", lambda *a, **k: "grok-configured")
    monkeypatch.setattr(backend, "provider_key", lambda name: "service-grant" if name == "xai" else None)
    monkeypatch.setattr(backend, "fetch_xai_models", lambda key: [{"id": "grok-latest", "input_modalities": ["text"], "output_modalities": ["text"]}])
    assert any(row["id"] == "grok-latest" for row in specialized.model_inventory(refresh=True)["models"])
    monkeypatch.setattr(backend, "fetch_xai_models", lambda key: (_ for _ in ()).throw(RuntimeError("sensitive-provider-body")))
    result = specialized.model_inventory(refresh=True)
    assert any(row["id"] == "grok-latest" for row in result["models"])
    assert result["failures"] == {"xai": "metadata_unavailable"}
    assert "sensitive-provider-body" not in str(result)


def test_clairic_native_timeout_is_clamped_by_service_scope(monkeypatch):
    import json
    from model_gateway import _clairic_backend as backend, engine
    seen = []
    monkeypatch.setattr(backend, "_credentials", lambda env: ("account", "service-grant"))
    class Reply:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, limit): return json.dumps({"result": {"report_date": {"value": "2026-10-30", "confidence": 0.9}}}).encode()
    def opener(request, **kwargs):
        seen.append(kwargs["timeout"])
        return Reply()
    # Payload shape is separately covered; only transport deadline matters here.
    monkeypatch.setattr(backend, "_report_date", lambda payload: (None, 0.0))
    with engine.attempt_scope(deadline=2.5, seen=set(), clock=lambda: 1.0):
        backend.fetch_request({"state": "x"}, opener=opener)
    assert seen == [1.5]


def test_embedding_retry_stops_on_cancel_before_another_quota_slot(monkeypatch):
    from model_gateway import _embedding_backend as backend, engine
    state = {"cancelled": False}
    attempts = []
    class Limiter:
        def acquire(self, timeout):
            attempts.append("slot")
            return True
    def request(*a, **kw):
        state["cancelled"] = True
        raise RuntimeError("network")
    monkeypatch.setattr(backend.time, "sleep", lambda seconds: None)
    with engine.attempt_scope(deadline=20, seen=set(), clock=lambda: 0, cancelled=lambda: state["cancelled"]):
        with pytest.raises(engine.ModelResponseError, match="cancelled"):
            backend._embed_with_retries(None, {}, {}, request, lambda *a: "network", str, limiter=Limiter())
    assert attempts == ["slot"]


def test_catalog_thread_budget_checks_paginated_work_without_signals(monkeypatch):
    import threading
    from model_gateway import _catalog_backend as backend
    now=[0.0]
    monkeypatch.setattr(backend,'PROVIDER_BUDGET_S',1.0)
    monkeypatch.setattr(backend,'_budget_clock',lambda:now[0],raising=False)
    monkeypatch.setattr(backend.threading,'current_thread',lambda:object())
    monkeypatch.setattr(backend.signal,'signal',lambda *a:pytest.fail('worker must not install process signal handlers'))
    with pytest.raises(TimeoutError):
        with backend._provider_budget():
            now[0]=2.0
            backend._remaining_budget(30)


def test_catalog_metadata_bytes_are_bounded_and_closed(monkeypatch):
    import urllib.request
    from model_gateway import _catalog_backend as backend
    closed=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*a):closed.append(True)
        def read1(self,size):return b'x'*size
        def read(self,size=-1):pytest.fail('metadata cannot use unbounded read')
    monkeypatch.setattr(urllib.request,'urlopen',lambda *a,**kw:Response())
    with pytest.raises(ValueError,match='limit'):
        backend._http_get_json('https://provider.invalid/models',{})
    assert closed==[True]
