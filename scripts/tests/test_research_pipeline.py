import copy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from research.pipeline import EvidenceError, Pipeline, comparison_posts, review_passed, validate_candidate
from research.model import ModelError, Reviewer
from research.state import State
from research.worker import discover, flush_outbox


def candidate():
    return {'title':'Yen flow changes', 'content':'JPM reports new hedge demand.', 'publisher':'JPMorgan',
            'claim_key':'yen-flow', 'document_date':'2026-09-04', 'pages':[1], 'tags':['JPY'],
            'text_only':False,'figures':[{'page':1,'crop':[.1,.2,.9,.8],'caption':'JPY, September4'}]}


def crop_gates():
    return {'figures':[{'index':0,'complete':True,'chart_titles':['JPY demand'],
        'axis_labels':['JPY','September'],'legend_labels':[],'source_labels':['JPMorgan'],
        'missing_or_clipped':[],'unrelated_prose':False,'reason':'Visible full chart'}]}


def gates():
    return dict.fromkeys(('supported','material_new_evidence','dates_verified','charts_complete','not_market_ear','no_unresolved_conflicts'),True) | {'reason':'Verified original evidence'}


@pytest.mark.parametrize('field,value', [('document_date','2027-01-01'),('document_date',None),('pages',[0]),('pages',[True]),('pages',[]),('tags',['bad tag']),('figures',[]),('title','')])
def test_invalid_evidence_cannot_publish(field,value):
    item=candidate();item[field]=value
    with pytest.raises(EvidenceError):validate_candidate(item,2,'2026-09-07')


@pytest.mark.parametrize('field', ['title', 'content', 'publisher', 'caption'])
@pytest.mark.parametrize('dash', ['\u2014', '&mdash;', '&#8212;', '&#x2014;'])
def test_candidate_holds_authored_em_dashes_before_evidence_review(field, dash):
    item = candidate()
    target = item['figures'][0] if field == 'caption' else item
    target[field] = f'Flows {dash} new demand'
    with pytest.raises(EvidenceError, match='em dash'):
        validate_candidate(item, 2, '2026-09-07')


def test_candidate_preserves_literal_source_quotes_with_em_dashes():
    item = candidate()
    item['date_evidence'] = {'source_quote': 'Report\u2014September 4, 2026'}
    original = copy.deepcopy(item)
    assert validate_candidate(item, 2, '2026-09-07') == original
    assert item == original


@pytest.mark.parametrize('crop', [[0,0,1,float('nan')],[0,0,1,float('inf')],[-.1,0,.9,1],[.5,.5,.5,.6],[0,0,.01,.01]])
def test_crop_geometry_is_finite_and_meaningful(crop):
    item=candidate();item['figures'][0]['crop']=crop
    with pytest.raises(EvidenceError):validate_candidate(item,2,'2026-09-07')


def test_text_only_explicit_and_every_gate_boolean():
    item=candidate();item.update(figures=[],text_only=True)
    assert validate_candidate(item,2,'2026-09-07')==item
    assert review_passed(gates())
    for key in gates():
        bad=gates();bad[key]='true' if key!='reason' else ''
        assert not review_passed(bad)


def test_novelty_shortlist_includes_matching_older_claim():
    posts=[{'id':str(i),'title':'unrelated','content':'something'} for i in range(200)]
    posts[-1].update(title='Yen flow changes',content='JPM reports new hedge demand.')
    assert any(p['id']=='199' for p in comparison_posts(candidate(),posts,limit=1))


def test_pipeline_cannot_publish_before_independent_visual_checks(tmp_path):
    item=candidate(); responses=[{'candidates':[item]},crop_gates(),gates() | {'charts_complete':False}]
    calls=[]
    reviewer=SimpleNamespace(model='test',ask=lambda prompt,images: calls.append((prompt,images)) or responses.pop(0))
    def extract(pdf,out):
        (out/'page.md').write_text('Report datedSeptember4. Back then markets priced 74bps. At time of writing markets price 72bps.')
        return {'page_count':1,'source_sha256':'a'*64,'pages':[{'page_number':1,'markdown_file':'page.md'}]}
    def render(pdf,out,pages,dpi,crop=None):
        out.mkdir(parents=True,exist_ok=True);(out/'image.png').write_bytes(b'image')
        return [{'page_number':p,'image_file':'image.png','width':800,'height':1100} for p in pages]
    publisher=SimpleNamespace(store_asset=lambda _:pytest.fail('Unverified assets must not be published'))
    pipe=Pipeline(tmp_path,reviewer,publisher,render,extract)
    work={'key':'key','folder_date':'2026-09-07','metadata':{'name':'report.pdf','id':'id:one','rev':'r1','content_hash':'a'*64}}
    assert pipe.process(work,tmp_path/'report.pdf',[])==[]
    assert len(calls)==3 and len(calls[2][1])==2
    assert len(calls[1][1])==1 and "Final chart crop" in calls[1][1][0][0]
    assert 'charts_complete' in (tmp_path/'evidence/key/review.json').read_text()
    assert 'EXTRACTED TEXT OF CITED ORIGINAL PAGES' in calls[2][0]
    assert 'Back then markets priced 74bps. At time of writing markets price 72bps.' in calls[2][0]


def test_model_rejects_truncation_and_never_exposes_remote_errors():
    response=SimpleNamespace(status_code=200,iter_content=lambda _: [json.dumps({'stop_reason':'max_tokens','content':[{'type':'text','text':'{}'}]}).encode()],close=lambda:None)
    session=SimpleNamespace(post=lambda *a,**k:response)
    with pytest.raises(ModelError,match='complete'):Reviewer('secret',session=session).ask('test')
    response.status_code=401
    with pytest.raises(ModelError,match='HTTP 401'):Reviewer('secret',session=session).ask('test')


def test_outbox_failure_does_not_acknowledge(tmp_path):
    root=tmp_path/'private';root.mkdir(mode=0o700);state=State(root/'state.sqlite')
    e={'.tag':'file','id':'id:x','rev':'1','content_hash':'a'*64,'path_lower':'/joe mccann/current/2026/september/sep 07/x.pdf'}
    state.ingest_page('2026/September/Sep 07',{'cursor':'c','entries':[e]},'2026-09-07')
    work=state.pending()[0];state.claim(work['key']);state.complete(work['key'],publications=[{'id':'research-x'}])
    def fail(_):raise OSError('unavailable')
    with pytest.raises(OSError):flush_outbox(state,SimpleNamespace(publish=fail))
    assert len(state.outbox())==1
    assert flush_outbox(state,SimpleNamespace(publish=lambda _:None))==1
    assert state.outbox()==[]
    state.close()


def test_discovery_commits_empty_cursor_and_preserves_date(tmp_path):
    from datetime import datetime,timezone
    root=tmp_path/'private';root.mkdir(mode=0o700);state=State(root/'state.sqlite')
    client=SimpleNamespace(list_page=lambda scope,cursor=None:{'cursor':'new:'+scope,'entries':[],'has_more':False})
    assert discover(client,state,datetime(2026,9,7,12,tzinfo=timezone.utc))==0
    assert len(state.scopes())==2 and all(state.cursor(s).startswith('new:') for s in state.scopes())
    state.close()


def test_persisted_outbox_flushes_during_discovery_outage(tmp_path,monkeypatch):
    from research import worker
    events=[]
    monkeypatch.setattr(worker,'flush_outbox',lambda *args:events.append('published') or 1)
    def broken(*args):
        events.append('discovery');raise OSError('Dropbox down')
    monkeypatch.setattr(worker,'discover',broken)
    with pytest.raises(OSError):worker.cycle(tmp_path,None,None,None,None,publish=True)
    assert events==['published','discovery']


def test_selector_reviewer_success_keeps_original_crop_and_provenance(tmp_path):
    item=candidate();responses=[{'candidates':[item]},crop_gates(),gates() | {'numeric_checks':[{'proposal_quote':'September4','page':1,'source_quote':'September4','supported':True}], 'date_evidence':{'page':1,'date_text':'4 September 2026','source_quote':'Report date: 4 September 2026','role':'report','role_verified':True}}];calls=[]
    reviewer=SimpleNamespace(model='test',ask=lambda prompt,images:responses.pop(0))
    def extract(pdf,out):
        (out/'page.md').write_text('Report date: 4 September 2026. September4 source')
        (out/'manifest.json').write_text('{"schema_version":1}')
        return {'page_count':1,'source_sha256':'a'*64,'pages':[{'page_number':1,'markdown_file':'page.md'}]}
    def render(pdf,out,pages,dpi,crop=None):
        calls.append((dpi,crop));out.mkdir(parents=True,exist_ok=True);(out/'image.png').write_bytes(b'image')
        return [{'page_number':p,'image_file':'image.png','width':800,'height':1100} for p in pages]
    pub=SimpleNamespace(store_asset=lambda p:'/api/newsfeed/research/files/'+'a'*64+Path(p).suffix)
    pipe=Pipeline(tmp_path,reviewer,pub,render,extract)
    work={'key':'key','folder_date':'2026-09-07','metadata':{'name':'report.pdf','id':'id:one','rev':'r1','content_hash':'b'*64}}
    posts=pipe.process(work,tmp_path/'report.pdf',[])
    assert len(posts)==1 and calls==[(96,None),(216,[.1,.2,.9,.8])]
    assert posts[0]['source']['documentDate']=='2026-09-04'
    assert posts[0]['source']['revision']=='r1'
    assert posts[0]['source']['evidenceUrl']=='/api/newsfeed/research/files/'+'a'*64+'.json'
    assert posts[0]['images']==[posts[0]['source']['figures'][0]['url']]


def test_native_render_is_subprocess_bounded(tmp_path,monkeypatch):
    calls=[]
    monkeypatch.setattr('research.pipeline.subprocess.run',lambda command,**kwargs:calls.append((command,kwargs)) or SimpleNamespace(returncode=0,stdout=b'[]'))
    pipe=Pipeline(tmp_path,None,None,extractor=lambda *a:None)
    assert pipe.render_isolated(tmp_path/'source.pdf',tmp_path/'out',[2],216,[0,0,1,1])==[]
    assert '--render-only' in calls[0][0] and calls[0][1]['timeout']==180


def test_model_real_byte_limit_closes_stream():
    closed=[]
    response=SimpleNamespace(status_code=200,iter_content=lambda _:iter([b'x'*1_500_000,b'x'*600_000]),close=lambda:closed.append(True))
    with pytest.raises(ModelError,match='limit'):Reviewer('secret',session=SimpleNamespace(post=lambda *a,**k:response)).ask('test')
    assert closed==[True]
