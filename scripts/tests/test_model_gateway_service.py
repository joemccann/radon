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
    import http.server
    import time
    from model_gateway import engine
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length','0')))
            self.send_response(200)
            self.send_header('Content-Length','30')
            self.end_headers()
            try:
                for _ in range(30):
                    self.wfile.write(b' ')
                    self.wfile.flush()
                    time.sleep(.025)
            except (BrokenPipeError,ConnectionResetError):
                pass
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
    server.daemon_threads=True
    worker=threading.Thread(target=server.serve_forever,daemon=True)
    worker.start()
    try:
        started=time.monotonic()
        with engine.attempt_scope(deadline=started+.15,seen=set()):
            with pytest.raises(engine.ModelResponseError) as error:
                engine._request(engine._default_post,f'http://127.0.0.1:{server.server_port}/',{}, {},timeout=.12,stream=True,max_bytes=100)
        assert error.value.code=='deadline_exceeded'
        assert time.monotonic()-started<.45
    finally:
        server.shutdown();server.server_close();worker.join(timeout=1)


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
