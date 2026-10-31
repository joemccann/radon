"""Credential renewal stays bounded, deduplicated and never pushes login on inference."""
import threading
import time
import pytest
from model_gateway.lifecycle import RefreshManager
from model_gateway.client import GatewayError


def manager():
    return RefreshManager({name:threading.RLock() for name in ('anthropic','grok','codex','antigravity','nvidia','cerebras')})


@pytest.mark.parametrize('payload',[{'mode':'shell'},{'providers':['IB_PASSWORD']},{'providers':[]},{'providers':'grok'},{'mode':'once','mode2':'reauth'}])
def test_unknown_or_empty_renewal_contract_denied(payload):
    with pytest.raises(GatewayError): manager().start(payload)


def test_one_job_native_renewal_and_safe_status(monkeypatch):
    import subscription_tokens as tokens
    entered=threading.Event(); release=threading.Event()
    reports=[]
    def run(mode,names,runtime):
        reports.append((mode,names))
        entered.set(); assert release.wait(2)
        return {'exit_code':0,'providers':[{'provider':names[0],'state':'valid','secret':'do-not-return'}]}
    monkeypatch.setattr(tokens,'run',run)
    monkeypatch.setattr(tokens,'record_heartbeat',lambda *a,**k:None)
    m=manager(); result=m.start({'mode':'once','providers':['grok']})
    assert entered.wait(1)
    assert m.start({'mode':'once','providers':['grok']})['job_id']==result['job_id']
    release.set()
    deadline=time.monotonic()+2
    while m.status({'job_id':result['job_id']})['status']!='complete' and time.monotonic()<deadline: time.sleep(.005)
    status=m.status({'job_id':result['job_id']})
    assert status['exit_code']==0
    assert status['result']=={'providers':[{'provider':'grok','state':'valid'}]}
    assert 'started' not in status
    assert reports==[('once',['grok'])]


def test_inference_trigger_cannot_launch_interactive_login(monkeypatch):
    import subscription_tokens as tokens
    complete=threading.Event(); seen=[]
    from types import SimpleNamespace
    def make_runtime(**kwargs):
        assert "login" in kwargs, "inference renewal must explicitly disable login"
        return SimpleNamespace(login=kwargs["login"],env={})
    def run(mode,names,runtime):
        seen.append(runtime.login(tokens.PROVIDERS['grok'],runtime.env))
        complete.set()
        return {'exit_code':1,'providers':[]}
    monkeypatch.setattr(tokens,'Runtime',make_runtime)
    monkeypatch.setattr(tokens,'run',run)
    monkeypatch.setattr(tokens,'default_login',lambda *a,**k:pytest.fail('inference triggered phone/login'))
    monkeypatch.setattr(tokens,'record_heartbeat',lambda *a,**k:None)
    m=manager(); m.request_refresh('grok')
    assert complete.wait(2) and seen==[False]
