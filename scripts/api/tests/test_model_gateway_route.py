"""Private API bridge preserves native payloads and existing authentication perimeter."""
import asyncio
import importlib
import json
import threading
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request


def module():
    return importlib.import_module('api.routes.model_gateway')


def app_client():
    app = FastAPI()
    app.include_router(module().router)
    return TestClient(app)


def test_chat_preserves_images_tools_and_budget(monkeypatch):
    seen = []
    def send(operation, payload, **kwargs):
        seen.append((operation, payload, kwargs))
        return {'provider': 'anthropic', 'content': [{'type': 'text', 'text': 'ok'}]}
    monkeypatch.setattr(module().gateway, 'request', send)
    body = {'messages': [{'role': 'user', 'content': [{'type':'image','source':{'type':'base64','media_type':'image/png','data':'AA=='}}]}], 'tools':[{'name':'lookup','input_schema':{'type':'object'}}], 'timeoutMs':45000}
    reply = app_client().post('/model-gateway/chat', json=body)
    assert reply.status_code == 200
    assert seen[0][:2] == ('chat', body)
    assert seen[0][2]['timeout_seconds'] == 50
    assert callable(seen[0][2]['cancelled'])


@pytest.mark.parametrize('operation', ['refresh','refresh-status','validate-credential','native','refresh-catalog','unknown'])
def test_management_and_native_protocols_not_exposed(operation):
    assert app_client().post('/model-gateway/'+operation, json={}).status_code == 404


@pytest.mark.parametrize('raw', ['[]', 'NaN', '{"timeoutMs":NaN}', '{'])
def test_reject_malformed_or_nonfinite_without_transport(monkeypatch,raw):
    monkeypatch.setattr(module().gateway,'request',lambda *a,**k:pytest.fail('invalid request reached service'))
    assert app_client().post('/model-gateway/chat',content=raw,headers={'content-type':'application/json'}).status_code == 400


def test_inventory_and_safe_errors(monkeypatch):
    from model_gateway.client import GatewayError
    def send(operation,*args,**kwargs):
        if operation=='models': return {'models':[],'source':'gateway'}
        raise GatewayError('exhausted','SECRET provider body',503,attempted=['grok:http_402'])
    monkeypatch.setattr(module().gateway,'request',send)
    assert app_client().get('/model-gateway/models').json()['models']==[]
    reply=app_client().post('/model-gateway/chat',json={})
    assert reply.status_code==503
    assert reply.json()=={'error':{'code':'exhausted','attempted':['grok:http_402']}}
    assert 'SECRET' not in reply.text


def test_body_limit_before_transport(monkeypatch):
    monkeypatch.setattr(module(),'MAX_REQUEST_BYTES',32)
    monkeypatch.setattr(module().gateway,'request',lambda *a,**k:pytest.fail('oversize reached service'))
    assert app_client().post('/model-gateway/chat',json={'text':'x'*40}).status_code==413


def test_gateway_is_registered_but_never_auth_exempt():
    source=(Path(__file__).parents[1]/'server.py').read_text()
    assert 'app.include_router(model_gateway_router)' in source
    exempt=source.split('AUTH_EXEMPT_PATHS = {',1)[1].split('}',1)[0]
    assert 'model-gateway' not in exempt


@pytest.mark.asyncio
async def test_disconnect_cancels_inflight_service(monkeypatch):
    m=module()
    started=threading.Event(); stopped=threading.Event()
    def send(*args, cancelled, **kwargs):
        started.set()
        assert stopped.wait(2)
        assert cancelled()
        from model_gateway.client import GatewayError
        raise GatewayError('cancelled',status=499)
    monkeypatch.setattr(m.gateway,'request',send)
    class Gone:
        async def is_disconnected(self):
            if started.is_set():
                stopped.set()
                return True
            return False
    response=await m._send(Gone(),'chat',{})
    assert response.status_code==499


@pytest.mark.parametrize('verb,path',[('get','/model-gateway/models'),('post','/model-gateway/chat')])
def test_public_unauthenticated_call_stops_at_real_perimeter(monkeypatch,verb,path):
    from scripts.api import server
    monkeypatch.setattr(server,'is_trusted_local_request',lambda request:False)
    monkeypatch.setattr(server,'verify_api_key',lambda request:None)
    monkeypatch.delenv('CLERK_JWKS_URL',raising=False)
    monkeypatch.delenv('RADON_AUTH_DISABLED',raising=False)
    monkeypatch.setattr(module().gateway,'request',lambda *a,**k:pytest.fail('unauthenticated inference'))
    reply=getattr(TestClient(server.app),verb)(path)
    assert reply.status_code==503


@pytest.mark.parametrize('operation,payload,expected',[('forecast',{},305),('x-search',{},305),('ocr',{'timeout_seconds':240},245),('clairic',{},17)])
def test_api_budget_matches_specialized_service_contract(operation,payload,expected):
    assert module()._budget(operation,payload)==expected
