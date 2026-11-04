"""The gateway owns traversal, budgets, private transport and credential boundaries."""
from __future__ import annotations
import base64
import json
import os
import threading
from pathlib import Path
import pytest
from model_gateway.client import GatewayError, request


def service(monkeypatch, *, clock=None):
    from model_gateway.server import Gateway
    from model_gateway import engine
    monkeypatch.setattr(engine, "_auth_for", lambda name, env: None if name == "cursor" else engine.AuthMaterial("fixture", "subscription", "test"))
    monkeypatch.setattr(engine, "_model_for", lambda name, env, **kw: name + "-model")
    return Gateway(clock=clock) if clock else Gateway()


def answer(name, data):
    text = json.dumps(data)
    return 200, "", {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"} if name == "anthropic" else {"choices": [{"message": {"content": text}}]}


def test_gateway_preserves_rejected_result_continuation_without_repeating_provider(monkeypatch):
    gateway = service(monkeypatch)
    from model_gateway import engine
    calls = []
    def call(name, *a, **kw):
        calls.append(name)
        return answer(name, {"accepted": name == "anthropic"})
    monkeypatch.setattr(engine, "_call_text_provider", call)
    first = gateway.handle("json", {"instruction": "facts", "require_end_turn": True})
    second = gateway.handle("json", {"instruction": "facts", "require_end_turn": True, "continuation": first["continuation"]})
    assert calls == ["antigravity", "anthropic"]
    assert second["data"] == {"accepted": True}
    with pytest.raises(GatewayError, match="continuation"):
        gateway.handle("json", {"instruction": "facts", "require_end_turn": True, "continuation": first["continuation"]})


def test_gateway_refuses_continuation_for_changed_prompt(monkeypatch):
    gateway = service(monkeypatch)
    from model_gateway import engine
    monkeypatch.setattr(engine, "_call_text_provider", lambda name, *a, **kw: answer(name, {}))
    first = gateway.handle("json", {"instruction": "facts"})
    with pytest.raises(GatewayError):
        gateway.handle("json", {"instruction": "different", "continuation": first["continuation"]})


@pytest.mark.parametrize("extra", [{"env": {"NVIDIA_API_KEY": "secret"}}, {"url": "http://127.0.0.1/private"}, {"path": "/etc/passwd"}, {"providers": ["invented"]}, {"read_timeout": float("nan")}, {"max_tokens": True}])
def test_gateway_refuses_untrusted_routing_or_unbounded_fields(monkeypatch, extra):
    gateway = service(monkeypatch)
    with pytest.raises(GatewayError) as error:
        gateway.handle("json", {"instruction": "facts", **extra})
    assert error.value.status == 400


def test_gateway_budget_is_shared_across_fallback_and_continuation(monkeypatch):
    now = [0.0]
    gateway = service(monkeypatch, clock=lambda: now[0])
    from model_gateway import engine
    calls = []
    def call(name, *a, **kw):
        calls.append(name)
        assert 0 < engine.scoped_timeout(120) < 10
        now[0] += 11
        raise RuntimeError("network:ReadTimeout")
    monkeypatch.setattr(engine, "_call_text_provider", call)
    with pytest.raises(GatewayError) as error:
        gateway.handle("json", {"instruction": "facts", "read_timeout": 10})
    assert error.value.code == "deadline_exceeded"
    assert calls == ["antigravity"]


def test_gateway_never_drops_image_to_use_text_only_provider(monkeypatch):
    gateway = service(monkeypatch)
    from model_gateway import engine
    import io
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (1, 1)).save(buffer, "PNG")
    calls = []
    def call(name, auth, model, system, instruction, images, *a, **kw):
        calls.append(name)
        assert images and images[0][0] == "chart"
        return answer(name, {"ok": True})
    monkeypatch.setattr(engine, "_call_text_provider", call)
    reply = gateway.handle("json", {"instruction": "facts", "images": [{"label": "chart", "media_type": "image/png", "data": base64.b64encode(buffer.getvalue()).decode()}]})
    assert calls == ["anthropic"] and reply["data"]["ok"]


def test_private_service_real_socket_round_trip_and_invalid_operation(monkeypatch):
    import tempfile
    from model_gateway.server import GatewayServer
    gateway = service(monkeypatch)
    with tempfile.TemporaryDirectory(prefix="mg-") as directory:
        path = str(Path(directory) / "gateway.sock")
        server = GatewayServer(path, gateway)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        monkeypatch.setenv("RADON_MODEL_GATEWAY_SOCKET", path)
        try:
            result = request("status", {}, timeout_seconds=2)
            assert result["status"] == "ok" and "providers" in result
            assert os.stat(path).st_mode & 0o077 == 0
            with pytest.raises(GatewayError):
                request("json", {"instruction": "x", "env": {}}, timeout_seconds=2)
            with pytest.raises(GatewayError):
                request("http://untrusted", {}, timeout_seconds=2)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


def test_nvidia_admission_is_bounded_and_cancelled_before_another_claim():
    from model_gateway import engine
    cancelled=[False];waits=[]
    class Limiter:
        def acquire(self,wait):
            waits.append(wait);cancelled[0]=True;return False
    with engine.attempt_scope(deadline=1.1,seen=set(),clock=lambda:1.0,cancelled=lambda:cancelled[0]):
        with pytest.raises(engine.ModelResponseError) as error:
            engine.acquire_model_slot(Limiter(),15)
    assert error.value.code=='cancelled' and waits==[0]


def test_real_uds_json_inference_does_not_wait_for_peer_disconnect(monkeypatch):
    import tempfile
    from model_gateway.server import GatewayServer
    from model_gateway import engine
    gateway=service(monkeypatch)
    monkeypatch.setattr(engine,'_call_text_provider',lambda name,*a,**k:answer(name,{'ok':True}))
    with tempfile.TemporaryDirectory(prefix='mg-',dir='/tmp') as directory:
        path=str(Path(directory)/'s');server=GatewayServer(path,gateway)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        monkeypatch.setenv('RADON_MODEL_GATEWAY_SOCKET',path)
        try:
            assert request('json',{'instruction':'facts'},timeout_seconds=1)['data']=={'ok':True}
        finally:server.shutdown();server.server_close()


def test_nvidia_wait_expiring_attempt_slice_allows_next_rung(monkeypatch):
    from model_gateway import engine
    now=[0.0]
    class Limiter:
        def acquire(self,wait):return False
    monkeypatch.setattr(engine.time,'sleep',lambda seconds:now.__setitem__(0,now[0]+seconds))
    with engine.attempt_scope(deadline=10,seen=set(),clock=lambda:now[0]) as scope:
        scope.attempt_deadline=.1
        assert engine.acquire_model_slot(Limiter(),15) is False
    assert now[0] <= .1


@pytest.mark.parametrize('requested,expected', [(None,270),(.1,.1),(270,270)])
def test_native_operation_uses_one_requested_total_budget(monkeypatch, requested, expected):
    from model_gateway import engine, native
    gateway=service(monkeypatch,clock=lambda:100.0)
    observed=[]
    def complete(payload):
        observed.append(engine._ATTEMPT_STATE.get().deadline)
        return {'status':200,'body':{},'content_type':'application/json'}
    monkeypatch.setattr(native,'complete',complete)
    payload={'protocol':'responses','request':{'model':'gpt-fixture'},'stream':False}
    if requested is not None:
        payload['timeout_seconds']=requested
    gateway.handle('native',payload)
    assert observed==[100.0+expected]


@pytest.mark.parametrize('value', [0,-1,True,float('nan'),float('inf'),301,'1'])
def test_native_operation_rejects_invalid_total_budget_before_dispatch(monkeypatch,value):
    from model_gateway import native
    gateway=service(monkeypatch)
    called=[]
    monkeypatch.setattr(native,'complete',lambda payload:called.append(payload))
    with pytest.raises(GatewayError) as error:
        gateway.handle('native',{'protocol':'responses','request':{'model':'gpt-fixture'},'stream':False,'timeout_seconds':value})
    assert error.value.status==400
    assert called==[]


def test_stream_drips_cannot_renew_the_total_deadline():
    from model_gateway import engine
    now = [0.0]

    class Body:
        status_code = 200

        def iter_content(self, _chunk):
            now[0] = 0.0
            yield b'{"ok":'
            now[0] = 1.0
            yield b'true}'

        def close(self):
            pass

    def post(url, **kwargs):
        return Body()

    with engine.attempt_scope(deadline=0.5, seen=set(), clock=lambda: now[0]):
        with pytest.raises(engine.ModelResponseError) as error:
            engine._request(post, 'http://gateway.test/', {}, {}, timeout=30, stream=True, max_bytes=100)
    assert error.value.code == 'deadline_exceeded'


def test_credential_admission_spends_the_original_request_budget(monkeypatch):
    from model_gateway import credentials,engine
    now=[100.0]
    gateway=service(monkeypatch,clock=lambda:now[0])
    deadlines=[]
    def refresh():
        state=engine._ATTEMPT_STATE.get()
        assert state is not None
        deadlines.append(state.deadline)
        now[0]+=5
    monkeypatch.setattr(credentials,'refresh_keys',refresh)
    def call(name,*args,**kwargs):
        assert engine._ATTEMPT_STATE.get().deadline==110.0
        return answer(name,{'ok':True})
    monkeypatch.setattr(engine,'_call_text_provider',call)
    assert gateway.handle('json',{'instruction':'facts','read_timeout':10})['data']=={'ok':True}
    assert deadlines==[110.0]


def test_disconnected_request_never_enters_credential_lookup(monkeypatch):
    from model_gateway import credentials
    gateway=service(monkeypatch)
    called=[]
    monkeypatch.setattr(credentials,'refresh_keys',lambda:called.append(True))
    with pytest.raises(GatewayError) as error:
        gateway.handle('json',{'instruction':'facts'},cancelled=lambda:True)
    assert error.value.code=='cancelled'
    assert called==[]


@pytest.fixture
def fresh_rungs(monkeypatch):
    from model_gateway import engine
    monkeypatch.setattr(engine, "_dead_until", {})


def test_first_rung_keeps_the_budget_the_live_fallbacks_do_not_need(monkeypatch, fresh_rungs):
    """Four keyed rungs: the first may spend all but 20s per later rung (was 120/6 = 20s)."""
    gateway = service(monkeypatch, clock=lambda: 0.0)
    from model_gateway import engine
    monkeypatch.setattr(engine, "_auth_for", lambda name, env: None if name in {"cursor", "nvidia", "cerebras"}
                        else engine.AuthMaterial("fixture", "subscription", "test"))
    seen = []

    def call(name, *a, **kw):
        seen.append(engine.scoped_timeout(300))
        return answer(name, {"ok": True})

    monkeypatch.setattr(engine, "_call_text_provider", call)
    gateway.handle("json", {"instruction": "facts", "read_timeout": 120})
    assert seen[0] == pytest.approx(60.0)


def test_rung_that_failed_permanently_stops_reserving_budget(monkeypatch, fresh_rungs):
    now = [0.0]
    gateway = service(monkeypatch, clock=lambda: now[0])
    from model_gateway import engine
    monkeypatch.setattr(engine, "_dead_clock", lambda: now[0])

    def run(winner):
        seen = []

        def call(name, *a, **kw):
            if name in {"nvidia", "cerebras"}:
                return 404, '{"status":404,"title":"Not Found"}', {"status": 404}
            seen.append((name, engine.scoped_timeout(300)))
            return answer(name, {"ok": True}) if name == winner else (503, "busy", {"error": "busy"})

        monkeypatch.setattr(engine, "_call_text_provider", call)
        payload = {"instruction": "facts", "read_timeout": 120,
                   "providers": ["antigravity", "nvidia", "anthropic", "grok", "codex", "cerebras"]}
        if winner is None:
            with pytest.raises(GatewayError):
                gateway.handle("json", payload)
        else:
            gateway.handle("json", payload)
        return seen

    # Six keyed rungs, none known dead: an even split. Every rung fails once.
    assert run(None)[0] == ("antigravity", pytest.approx(20.0))
    # nvidia and cerebras answered 404: four live rungs remain, still attempted.
    assert run("antigravity")[0] == ("antigravity", pytest.approx(60.0))
    # The memory expires.
    now[0] += engine._DEAD_RUNG_TTL_S + 1
    assert run("antigravity")[0] == ("antigravity", pytest.approx(20.0))


def test_dead_rung_is_still_attempted(monkeypatch, fresh_rungs):
    gateway = service(monkeypatch, clock=lambda: 0.0)
    from model_gateway import engine
    engine._dead_until["codex"] = float("inf")
    calls = []

    def call(name, *a, **kw):
        calls.append(name)
        return answer(name, {"ok": True}) if name == "codex" else (503, "busy", {"error": "busy"})

    monkeypatch.setattr(engine, "_call_text_provider", call)
    assert gateway.handle("json", {"instruction": "facts", "read_timeout": 120})["provider"] == "codex"
    assert "codex" in calls and "codex" not in engine._dead_until


def test_transient_failure_does_not_mark_a_rung_dead(monkeypatch, fresh_rungs):
    gateway = service(monkeypatch, clock=lambda: 0.0)
    from model_gateway import engine

    def call(name, *a, **kw):
        if name == "antigravity":
            raise RuntimeError("network:ReadTimeout")
        if name == "anthropic":
            return 503, "busy", {"error": "busy"}
        return answer(name, {"ok": True})

    monkeypatch.setattr(engine, "_call_text_provider", call)
    gateway.handle("json", {"instruction": "facts", "read_timeout": 120})
    assert engine._dead_until == {}


def test_rung_slice_never_starves_and_last_rung_gets_everything():
    from model_gateway import engine
    assert engine._rung_slice(120, 1) == 120
    assert engine._rung_slice(120, 4) == pytest.approx(60)
    assert engine._rung_slice(40, 4) == pytest.approx(10)  # even-split floor
    assert engine._rung_slice(0, 3) > 0


def test_rejected_continuation_gives_each_later_rung_its_minimum(monkeypatch, fresh_rungs):
    """A caller-rejected first draft must not leave the next rungs a few seconds."""
    now = [0.0]
    gateway = service(monkeypatch, clock=lambda: now[0])
    from model_gateway import engine
    from model_gateway.contracts import MIN_FALLBACK_SLICE_S
    monkeypatch.setattr(engine.provider_cooldown, "rate_limited", lambda *_a, **_k: False)
    slices = []

    def call(name, *a, **kw):
        slices.append((name, engine.scoped_timeout(300)))
        if name == "antigravity":
            now[0] += 79  # uses the generous first slice, then the caller rejects it
            return answer(name, {"ok": True})
        if name == "grok":
            return 503, "busy", {"error": "busy"}
        return answer(name, {"ok": True})

    monkeypatch.setattr(engine, "_call_text_provider", call)
    payload = {"instruction": "facts", "read_timeout": 120,
               "providers": ["antigravity", "grok", "codex"]}
    first = gateway.handle("json", payload)
    assert slices[0] == ("antigravity", pytest.approx(80))  # success-path slice, unchanged
    gateway.handle("json", {**payload, "continuation": first["continuation"]})
    fallback = [(name, timeout) for name, timeout in slices if name != "antigravity"]
    assert [name for name, _timeout in fallback] == ["grok", "codex"]
    assert all(timeout >= MIN_FALLBACK_SLICE_S - 0.01 for _name, timeout in fallback)


def test_in_engine_rejected_draft_does_not_starve_later_rungs(monkeypatch, fresh_rungs):
    now = [0.0]
    from model_gateway import engine
    from model_gateway.contracts import MIN_FALLBACK_SLICE_S
    monkeypatch.setattr(engine, "_auth_for", lambda name, env: engine.AuthMaterial("fixture", "subscription", "test"))
    monkeypatch.setattr(engine, "_model_for", lambda name, env, **kw: name + "-model")
    monkeypatch.setattr(engine.provider_cooldown, "rate_limited", lambda *_a, **_k: False)
    slices = []

    def call(name, *a, **kw):
        slices.append((name, engine.scoped_timeout(300)))
        if name == "anthropic":
            now[0] += 70
            return answer(name, {"ok": False})
        if name == "grok":
            return 503, "busy", {"error": "busy"}
        return answer(name, {"ok": True})

    monkeypatch.setattr(engine, "_call_text_provider", call)
    with engine.attempt_scope(deadline=120, seen=set(), clock=lambda: now[0]):
        result = engine.complete_multimodal_json(
            "facts", providers=["anthropic", "grok", "codex"], read_timeout=120,
            accept=lambda obj: obj.get("ok") is True,
        )
    assert slices[0] == ("anthropic", pytest.approx(80))  # success-path slice: 120s minus 20s for each later rung
    assert result.provider == "codex"
    assert all(timeout >= MIN_FALLBACK_SLICE_S - 0.01 for name, timeout in slices if name != "anthropic")
