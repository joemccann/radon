"""Normalized chat keeps tool/image capability and terminal completion semantics."""
import pytest
from model_gateway import chat, engine
from model_gateway.contracts import ModelLadderExhausted
from model_gateway.client import GatewayError

CATALOG={'models':[{'id':'claude-model','provider':'anthropic'},{'id':'grok-model','provider':'xai'}]}
TOOL={'name':'lookup','description':'Look up facts','input_schema':{'type':'object'}}

def harness(monkeypatch,outputs):
    calls=[]
    def run(**kwargs):
        for name in kwargs['providers']:
            if name not in outputs: continue
            value,text=kwargs['parse_success'](name,outputs[name])
            calls.append(name)
            if value is not None: return value,text,name,name+'-default',[name+':ok']
        raise ModelLadderExhausted('exhausted')
    monkeypatch.setattr(engine,'_run_ladder',run)
    return calls


def test_incomplete_anthropic_falls_through_to_tool_capable_provider(monkeypatch):
    calls=harness(monkeypatch,{'anthropic':{'content':[{'type':'text','text':'partial'}],'stop_reason':'max_tokens'},'grok':{'choices':[{'message':{'content':'','tool_calls':[{'id':'call1','function':{'name':'lookup','arguments':'{"ticker":"AAPL"}'}}]},'finish_reason':'tool_calls'}]}})
    value=chat.complete({'messages':[{'role':'user','content':'Find it'}],'tools':[TOOL],'toolChoice':'required'},catalog=CATALOG)
    assert calls==['anthropic','grok']
    assert value['provider']=='xai' and value['toolCalls'][0]['input']=={'ticker':'AAPL'}
    assert value['usedFallback'] is True


def test_plain_text_uses_antigravity_first(monkeypatch):
    calls=harness(monkeypatch,{'antigravity':{'choices':[{'message':{'content':'answer'},'finish_reason':'stop'}]}})
    assert chat.complete({'messages':[{'role':'user','content':'Hello'}]},catalog=CATALOG)['text']=='answer'
    assert calls==['antigravity']


def test_unknown_or_incapable_selection_is_rejected(monkeypatch):
    harness(monkeypatch,{})
    with pytest.raises(GatewayError,match='Unknown catalog'):
        chat.complete({'messages':[{'role':'user','content':'x'}],'model':'invented'},catalog=CATALOG)
    with pytest.raises(GatewayError) as error:
        chat.complete({'messages':[{'role':'user','content':'x'}],'tools':[TOOL],'provider':'antigravity','fallback':False},catalog=CATALOG)
    assert error.value.code=='unsupported_capability'


def test_mixed_tool_history_preserves_text_images_and_calls():
    source={'type':'base64','media_type':'image/png','data':'AA=='}
    result=chat.openai_messages({'messages':[{'role':'assistant','content':[{'type':'text','text':'Checking'},{'type':'tool_use','id':'c1','name':'lookup','input':{'x':1}}]},{'role':'user','content':[{'type':'text','text':'Chart'},{'type':'image','source':source},{'type':'tool_result','tool_use_id':'c1','content':'42'}]}]})
    assert result[0]['content']=='Checking' and result[0]['tool_calls'][0]['function']['arguments']=='{"x": 1}'
    assert result[1]['content'][1]['image_url']['url']=='data:image/png;base64,AA=='
    assert result[2]=={'role':'tool','tool_call_id':'c1','content':'42'}


def test_anthropic_native_tools_and_subscription_headers(monkeypatch):
    seen=[]
    monkeypatch.setattr(engine,'_request',lambda *a,**k:seen.append((a,k)) or {})
    auth=engine.AuthMaterial('secret','subscription','fixture')
    chat._native_call('anthropic',auth,'claude-model',{'messages':[{'role':'user','content':'x'}],'system':'rules','tools':[TOOL],'toolChoice':'required'})
    args,kwargs=seen[0]
    assert args[1]=='https://api.anthropic.com/v1/messages'
    assert args[3]['tools']==[TOOL] and args[3]['tool_choice']=={'type':'any'}
    assert kwargs['stream'] is True
    assert args[2]['authorization']=='Bearer secret'


@pytest.mark.parametrize('bad',[{'maxTokens':True},{'fallback':'yes'},{'tools':[{'name':'bad name','input_schema':{}}]},{'toolChoice':'sometimes'},{'headers':{'Authorization':'bad'}}])
def test_malformed_chat_cannot_reach_transport(monkeypatch,bad):
    monkeypatch.setattr(engine,'_run_ladder',lambda **k:pytest.fail('invalid reached ladder'))
    with pytest.raises(GatewayError):
        chat.complete({'messages':[{'role':'user','content':'x'}],**bad},catalog=CATALOG)
