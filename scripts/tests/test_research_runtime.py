"""No-network operational failure and recovery tests for research automation."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from research import model, pipeline, seed, worker
from research.publish import stable_post_id
from research.state import State


def item():
    return {"title":"New yen demand", "content":"JPM reports new measured demand.", "publisher":"JPMorgan",
            "claim_key":"yen-demand", "document_date":"2026-09-04", "pages":[1], "tags":["JPY"],
            "text_only":False, "figures":[{"page":1,"crop":[0,0,1,1],"caption":"JPY demand, source date"}]}


def entry():
    return {".tag":"file", "id":"id:one", "rev":"rev1", "content_hash":"a"*64,
            "name":"report.pdf", "path_lower":"/joe mccann/current/2026/september/sep 07/report.pdf",
            "path_display":"/Joe McCann/Current/2026/September/Sep 07/report.pdf"}


@pytest.fixture
def queue(tmp_path):
    root=tmp_path/"private"; root.mkdir(mode=0o700)
    state=State(root/"state.sqlite")
    state.ingest_page("2026/September/Sep 07", {"cursor":"cursor", "entries":[entry()]}, "2026-09-07")
    yield root,state
    state.close()


def session_response(value=None, raw=None, status=200):
    closed=[]; calls=[]
    response=SimpleNamespace(status_code=status, iter_content=lambda _: [raw if raw is not None else json.dumps(value).encode()], close=lambda:closed.append(True))
    return SimpleNamespace(post=lambda *a,**kw:calls.append((a,kw)) or response),closed,calls


def test_model_valid_multimodal_request_is_tool_free_and_bounded(tmp_path):
    chart=tmp_path/"chart.png"; chart.write_bytes(b"original-image")
    session,closed,calls=session_response({"stop_reason":"end_turn","content":[{"type":"text","text":'```json\n{"candidates": []}\n```'}]})
    assert model.Reviewer("test",model="explicit",session=session).ask("evaluate",[("original page",chart)])=={"candidates":[]}
    request=calls[0][1]
    assert request["stream"] is True and request["timeout"]==(10,120)
    assert "tools" not in request["json"]
    assert request["json"]["model"]=="explicit"
    assert request["json"]["messages"][0]["content"][1]["source"]["media_type"]=="image/png"
    assert closed==[True]


@pytest.mark.parametrize("value,raw", [
    (None,b"malformed"), ([],None), (None,b"null"),
    ({"stop_reason":"end_turn","content":["bad block"]},None),
    ({"stop_reason":"end_turn","content":[{"type":"text","text":"[]"}]},None),
    ({"stop_reason":"end_turn","content":[{"type":"text","text":"not json"}]},None),
    ({"stop_reason":"end_turn","content":[{"type":"text"}]},None),
])
def test_model_malformed_response_fails_closed(value,raw):
    session,closed,_=session_response(value,raw)
    with pytest.raises(model.ModelError): model.Reviewer("test",session=session).ask("evaluate")
    assert closed==[True]


def test_model_requires_key_and_rejects_large_images_before_request(tmp_path):
    with pytest.raises(model.ModelError,match="No keyed model provider"): model.Reviewer(env={})
    path=tmp_path/"large.png"; path.write_bytes(b"x"*5_000_001)
    session=SimpleNamespace(post=lambda *a,**kw:pytest.fail("oversized image reached remote request"))
    with pytest.raises(model.ModelError,match="byte limit"): model.Reviewer("test",session=session).ask("evaluate",[("page",path)])


def test_model_network_error_is_classified_without_secrets():
    def fail(*a,**kw): raise requests.Timeout("private-token-and-source")
    with pytest.raises(model.ModelError) as error: model.Reviewer("test",session=SimpleNamespace(post=fail)).ask("evaluate")
    assert "private-token" not in str(error.value)


@pytest.mark.parametrize("value", [None,{}, {**item(),"figures":"not-list"}, {**item(),"figures":[{"page":2}]},
    {**item(),"figures":[{"page":1,"crop":[0,0,1,1],"caption":""}]}])
def test_candidate_missing_shapes_cannot_be_accepted(value):
    with pytest.raises(pipeline.EvidenceError): pipeline.validate_candidate(value,2,"2026-09-07")


def test_comparison_includes_actual_newest_not_id_order():
    posts=[{"id":str(i),"title":"unrelated","content":"old","timestamp":"2026-08-01"} for i in range(200)]
    posts[-1]["timestamp"]="2026-09-07"
    assert "199" in {p["id"] for p in pipeline.comparison_posts(item(),posts,limit=0)}


def test_native_subprocess_failures_are_held_errors(tmp_path,monkeypatch):
    monkeypatch.setattr(pipeline.subprocess,"run",lambda *a,**kw:SimpleNamespace(returncode=-11,stdout=b""))
    pipe=pipeline.Pipeline(tmp_path,None,None)
    with pytest.raises(pipeline.EvidenceError,match="rendering"): pipe.render_isolated(tmp_path/"file.pdf",tmp_path/"out",[1])
    with pytest.raises(pipeline.EvidenceError,match="extraction"): pipe.extract(tmp_path/"file.pdf",tmp_path/"out")


def fixture_pipeline(tmp_path,responses,count=1):
    def extract(pdf,out):
        records=[]
        for page in range(1,count+1):
            name=f"{page}.md"; (out/name).write_text("Report date: 4 September 2026. Source page")
            records.append({"page_number":page,"markdown_file":name})
        return {"page_count":count,"source_sha256":"a"*64,"pages":records}
    def render(pdf,out,pages,**kwargs):
        out.mkdir(parents=True,exist_ok=True)
        for p in pages:(out/f"{p}.png").write_bytes(b"image")
        return [{"page_number":p,"image_file":f"{p}.png","width":800,"height":1100} for p in pages]
    def ask(prompt, images):
        if prompt.startswith(pipeline.CROP_INSPECTION):
            return {"figures":[{"index":i,"complete":True,"chart_titles":["Yen demand"],"axis_labels":["JPY"],
                "legend_labels":[],"source_labels":["JPMorgan"],"missing_or_clipped":[],"unrelated_prose":False,"reason":"Visible"}
                for i in range(len(images))]}
        return responses.pop(0)
    reviewer=SimpleNamespace(model="test",ask=ask)
    publisher=SimpleNamespace(store_asset=lambda p:str(p))
    return pipeline.Pipeline(tmp_path,reviewer,publisher,render,extract), {"key":"one","folder_date":"2026-09-07","metadata":entry()}


def test_pipeline_empty_selection_records_audited_rejection(tmp_path):
    pipe,work=fixture_pipeline(tmp_path,[{"candidates":[],"reason":"Already covered"}])
    assert pipe.process(work,tmp_path/"source.pdf",[])==[]
    saved=json.loads((tmp_path/"evidence/one/review.json").read_text())
    assert saved["audit"][0]["selection"]["reason"]=="Already covered"
    assert saved["posts"]==[]


@pytest.mark.parametrize("response", [{}, {"candidates":None}])
def test_pipeline_requires_selection_array(tmp_path,response):
    pipe,work=fixture_pipeline(tmp_path,[response])
    with pytest.raises(pipeline.EvidenceError,match="missing candidates"):pipe.process(work,tmp_path/"source.pdf",[])


def test_pipeline_rejects_unseen_crop_page(tmp_path):
    proposal=item();proposal["pages"]=[9];proposal["figures"][0]["page"]=9
    pipe,work=fixture_pipeline(tmp_path,[{"candidates":[proposal]}],count=9)
    with pytest.raises(pipeline.EvidenceError,match="visually supplied"):pipe.process(work,tmp_path/"source.pdf",[])


def test_pipeline_deduplicates_same_claim_before_second_review(tmp_path):
    gates=dict.fromkeys(("supported","material_new_evidence","dates_verified","charts_complete","not_market_ear","no_unresolved_conflicts"),True)|{"reason":"Verified", "date_evidence":{"page":1,"date_text":"4 September 2026","source_quote":"Report date: 4 September 2026","role":"report","role_verified":True}}
    pipe,work=fixture_pipeline(tmp_path,[{"candidates":[item(),copy.deepcopy(item())]},gates])
    assert len(pipe.process(work,tmp_path/"source.pdf",[]))==1


def test_pipeline_page_budget_prevents_render(tmp_path):
    pipe=pipeline.Pipeline(tmp_path,None,None,renderer=lambda *a:pytest.fail("overbudget render"),extractor=lambda *a:{"page_count":101})
    with pytest.raises(pipeline.EvidenceError,match="page budget"):pipe.process({"key":"x"},tmp_path/"source.pdf",[])


def test_document_deadline_bounds_a_100_page_selection_and_emits_progress(tmp_path):
    """REL-252: never start a reviewer call that cannot finish in the lease."""
    class Clock:
        now = 0
        def __call__(self): return self.now
    clock = Clock()
    calls, stages = [], []
    def ask(*_args):
        calls.append(True)
        clock.now += pipeline.REVIEWER_CALL_TIMEOUT_SECS
        return {"candidates": []}
    def extract(_pdf, out):
        pages = []
        for page in range(1, 101):
            name = f"{page}.md"
            (out / name).write_text("source")
            pages.append({"page_number": page, "markdown_file": name})
        return {"page_count": 100, "source_sha256": "a" * 64, "pages": pages}
    def render(_pdf, out, pages, **_kwargs):
        out.mkdir(parents=True, exist_ok=True)
        return [{"page_number": page, "image_file": f"{page}.png", "width": 1, "height": 1} for page in pages]
    pipe = pipeline.Pipeline(tmp_path, SimpleNamespace(ask=ask), None, render, extract,
                             clock=clock, document_budget_secs=2 * pipeline.REVIEWER_CALL_TIMEOUT_SECS)
    work = {"key": "deadline", "folder_date": "2026-09-07", "metadata": {"name": "large.pdf"}}
    with pytest.raises(pipeline.DocumentDeadlineExceeded, match="deadline"):
        pipe.process(work, tmp_path / "large.pdf", [], progress=stages.append)
    assert len(calls) == 2
    assert stages[:2] == ["extracted", "rendered-pages"]
    assert stages[-1] == "selected-pages-9-16"


def test_cycle_success_then_restart_does_not_reprocess(queue,monkeypatch):
    root,state=queue; monkeypatch.setattr(worker,"discover",lambda *a:0)
    calls=[]; published=[]
    pipe=SimpleNamespace(process=lambda work,pdf,recent,**kwargs:calls.append(work["key"]) or [{"id":"research-one","title":"new"}])
    client=SimpleNamespace(download=lambda *a:root/"source.pdf")
    publisher=SimpleNamespace(recent_posts=lambda **k:[],publish=lambda post:published.append(post["id"]))
    result=worker.cycle(root,client,state,pipe,publisher,publish=True)
    assert result=={"discovered":0,"processed":1,"published":1,"errors":[]}
    state.recover()
    assert worker.cycle(root,client,state,pipe,publisher,publish=True)["processed"]==0
    assert len(calls)==1 and published==["research-one"]


def test_cycle_failure_backoff_then_exhaustion_is_held(queue,monkeypatch):
    root,state=queue; monkeypatch.setattr(worker,"discover",lambda *a:0)
    def fail(*a):raise worker.DropboxError("private-message",status=429,retry_after=500)
    client=SimpleNamespace(download=fail)
    publisher=SimpleNamespace(recent_posts=lambda **k:[])
    result=worker.cycle(root,client,state,None,publisher)
    row=state.db.execute("SELECT * FROM work").fetchone()
    assert result["errors"]==["DropboxError"] and row["status"]=="pending"
    assert row["available_at"]>worker.time.time()+490 and "private-message" not in row["error"]
    with state.db: state.db.execute("UPDATE work SET available_at=0,attempts=5")
    worker.cycle(root,client,state,None,publisher)
    row=state.db.execute("SELECT * FROM work").fetchone()
    assert row["status"]=="complete" and json.loads(row["result"])["status"]=="held"
    assert state.outbox()==[]


def test_heartbeat_keeps_private_local_error_when_db_unavailable(tmp_path,monkeypatch):
    from api import db_http
    def fail(*a):raise OSError("offline")
    monkeypatch.setattr(db_http,"hrana_execute",fail)
    assert worker.heartbeat(tmp_path,"error",ValueError("private source")) is False
    payload=json.loads((tmp_path/"health.json").read_text())
    assert payload["last_error"]=={"message":"ValueError"}
    monkeypatch.setattr(db_http,"hrana_execute",lambda *a:[])
    assert worker.heartbeat(tmp_path,"ok") is True


def test_discovery_reset_relist_and_missing_date_folder(queue,monkeypatch):
    _,state=queue; scope="2026/september/sep 07"
    monkeypatch.setattr(worker,"date_scopes",lambda now:[(scope,"2026-09-07")])
    calls=[]
    def listing(path,cursor=None):
        calls.append(cursor)
        if cursor:raise worker.DropboxError("reset",status=409)
        return {"cursor":"fresh","entries":[entry()],"has_more":False}
    assert worker.discover(SimpleNamespace(list_page=listing),state)==0
    assert calls==["cursor",None] and state.cursor(scope)=="fresh"
    def absent(*a,**k):raise worker.DropboxError("absent",status=409)
    assert worker.discover(SimpleNamespace(list_page=absent),state)==0
    assert state.cursor(scope) is None


def test_discovery_non409_and_pagination_limit_fail(queue,monkeypatch):
    _,state=queue; monkeypatch.setattr(worker,"date_scopes",lambda now:[])
    def throttle(*a,**k):raise worker.DropboxError("throttle",status=429)
    with pytest.raises(worker.DropboxError):worker.discover(SimpleNamespace(list_page=throttle),state)
    client=SimpleNamespace(list_page=lambda *a,**k:{"cursor":"again","entries":[],"has_more":True})
    with pytest.raises(worker.DiscoveryError) as caught:worker.discover(client,state)
    assert caught.value.failures == [{"stage":"discovery","type":"RuntimeError", "scope_id": hashlib.sha256(b"2026/september/sep 07").hexdigest()[:16]}]


@pytest.fixture
def approved_batch(tmp_path):
    directory=tmp_path/"batch";directory.mkdir()
    pdf=tmp_path/"source.pdf";pdf.write_bytes(b"%PDF-approved")
    digest=hashlib.sha256(pdf.read_bytes()).hexdigest()
    png=directory/"chart.png";png.write_bytes(b"original approved chart")
    document=entry()|{"local_path":str(pdf),"folder_date":"2026-09-07"}
    candidate={"review_id":"R1","title":"New evidence","draft":"Measured new evidence", "tags":["MACRO"],"document_index":1,
               "source_date":"2026-09-04","source_pages":[1],"source":{"local_path":str(pdf),"source_sha256":digest,"folder_date":"2026-09-07"}}
    batch={"selection_calibration":{"accepted_ids":["R1"]},"candidates":[candidate]}
    chart={"review_id":"R1","image_path":str(png),"source_sha256":digest,"image_sha256":hashlib.sha256(png.read_bytes()).hexdigest(),"page_number":1,"caption":"Original chart"}
    (directory/"review-batch.json").write_text(json.dumps(batch))
    (directory/"chart-manifest.json").write_text(json.dumps({"charts":[chart|{"review_id":"R2"},chart]}))
    (tmp_path/"batch-manifest.json").write_text(json.dumps({"documents":[document]}))
    return directory,pdf,png


def fake_publisher():
    return SimpleNamespace(stable_post_id=stable_post_id,store_asset=lambda path:"/private/"+Path(path).name)


def test_seed_is_idempotent_and_preserves_approved_metadata(queue,approved_batch):
    _,state=queue;directory,_,_=approved_batch
    assert seed.seed_reviewed(directory,state,fake_publisher())==1
    row=state.outbox()[0]["payload"]
    assert row["source"]["publisher"]=="Morgan Stanley" and row["source"]["documentDate"]=="2026-09-04"
    assert row["source"]["figures"][0]["caption"]=="Original chart"
    assert seed.seed_reviewed(directory,state,fake_publisher())==0
    assert len(state.outbox())==1


@pytest.mark.parametrize("change", ["approval","pdf","figure-source","figure-path","figure-bytes"])
def test_seed_rejects_unapproved_or_tampered_source(queue,approved_batch,tmp_path,change):
    _,state=queue;directory,pdf,png=approved_batch
    if change=="approval":
        path=directory/"review-batch.json";data=json.loads(path.read_text());data["selection_calibration"]["accepted_ids"]=[];path.write_text(json.dumps(data))
    elif change=="pdf":pdf.write_bytes(b"%PDF-altered")
    elif change=="figure-bytes":png.write_bytes(b"altered chart")
    else:
        path=directory/"chart-manifest.json";data=json.loads(path.read_text())
        if change=="figure-source":data["charts"][-1]["source_sha256"]="b"*64
        else:
            outside=tmp_path/"outside.png";outside.write_bytes(png.read_bytes());data["charts"][-1]["image_path"]=str(outside)
        path.write_text(json.dumps(data))
    with pytest.raises(ValueError):seed.seed_reviewed(directory,state,fake_publisher())
    assert state.outbox()==[]


@pytest.fixture
def cli(tmp_path,monkeypatch):
    import sys
    root=tmp_path/"worker-root"
    monkeypatch.setenv("RADON_RESEARCH_DIR",str(root))
    monkeypatch.delenv("RADON_RESEARCH_PUBLISH",raising=False)
    monkeypatch.setattr(sys,"argv",["research-worker","--root",str(root)])
    monkeypatch.setattr(worker.os,"umask",lambda value:0o077)
    events=[]; handlers={}
    state=SimpleNamespace(recover=lambda:events.append("recover"),close=lambda:events.append("close"))
    monkeypatch.setattr(worker,"State",lambda path:state)
    monkeypatch.setattr(model,"Reviewer",lambda:SimpleNamespace(model="test"))
    monkeypatch.setattr(pipeline,"Pipeline",lambda *args:object())
    client=SimpleNamespace(connect=lambda:events.append("connect") or object())
    monkeypatch.setattr(worker.DropboxClient,"from_env",lambda:client)
    monkeypatch.setattr(worker.signal,"signal",lambda signum,handler:handlers.__setitem__(signum,handler))
    monkeypatch.setattr(worker,"heartbeat",lambda root,status,error=None:events.append(status))
    monkeypatch.setattr(worker,"cycle",lambda *a,**k:{"processed":0,"errors":[]})
    return root,state,events,handlers


def test_cli_once_recovers_and_closes_state(cli,capsys):
    _,_,events,_=cli
    worker.main()
    assert events==["recover","connect","running","ok","close"]
    assert json.loads(capsys.readouterr().out)["processed"]==0


def test_cli_once_error_is_nonzero_and_redacted(cli,monkeypatch,capsys):
    def fail(*a,**k):raise RuntimeError("private-token")
    monkeypatch.setattr(worker,"cycle",fail)
    with pytest.raises(SystemExit) as error:worker.main()
    assert error.value.code==1
    assert json.loads(capsys.readouterr().out)=={"error":"RuntimeError"}


@pytest.mark.parametrize("failure",[False,True])
def test_cli_daemon_honors_stop_signal_and_closes_state(cli,monkeypatch,failure):
    import sys
    from research import ingestion
    root,_,events,handlers=cli
    monkeypatch.setattr(sys,"argv",["research-worker","--root",str(root),"--daemon","--interval","30"])
    def daemon(root, state, interval, publish, stopping):
        assert interval == 30 and not stopping()
        handlers[worker.signal.SIGTERM](None,None)
        assert stopping()
        if failure:
            raise RuntimeError('consumer failed')
    monkeypatch.setattr(ingestion, 'run_daemon', daemon)
    if failure:
        with pytest.raises(RuntimeError): worker.main()
    else:
        worker.main()
    assert events == ['recover','close']


def test_cli_seed_only_never_connects_dropbox_or_model(cli,monkeypatch,capsys):
    import sys
    root,_,events,_=cli
    monkeypatch.setattr(sys,"argv",["research-worker","--root",str(root),"--seed-reviewed",str(root/"batch"),"--publish"])
    monkeypatch.setattr(seed,"seed_reviewed",lambda *a:6)
    monkeypatch.setattr(worker,"flush_outbox",lambda *a:6)
    worker.main()
    assert "connect" not in events
    assert json.loads(capsys.readouterr().out)=={"seeded":6,"published":6}


def test_cli_rejects_fast_poll_and_concurrent_worker(cli,monkeypatch):
    import sys
    root,_,_,_=cli
    monkeypatch.setattr(sys,"argv",["worker","--interval","10"])
    with pytest.raises(SystemExit) as error:worker.main()
    assert error.value.code==2
    monkeypatch.setattr(sys,"argv",["worker","--root",str(root)])
    def locked(*a):raise BlockingIOError()
    monkeypatch.setattr(worker.fcntl,"flock",locked)
    with pytest.raises(SystemExit,match="already running"):worker.main()


def test_invalid_caption_holds_only_that_candidate_not_valid_siblings(tmp_path):
    invalid=item();invalid["figures"][0]["caption"]="x"*308
    valid=item()
    gates=dict.fromkeys(("supported","material_new_evidence","dates_verified","charts_complete","not_market_ear","no_unresolved_conflicts"),True)|{"reason":"Verified", "date_evidence":{"page":1,"date_text":"4 September 2026","source_quote":"Report date: 4 September 2026","role":"report","role_verified":True}}
    pipe,work=fixture_pipeline(tmp_path,[{"candidates":[invalid,valid]},gates])
    assert len(pipe.process(work,tmp_path/"source.pdf",[]))==1
    audit=json.loads((tmp_path/"evidence/one/review.json").read_text())["audit"]
    assert any(row.get("held")=="invalid candidate" for row in audit)


def test_pipeline_does_not_accept_numeric_waiver_from_generic_verifier(tmp_path):
    proposal=item();proposal["content"]="Real yields rose more than 70bps."
    gates=dict.fromkeys(("supported","material_new_evidence","dates_verified","charts_complete","not_market_ear","no_unresolved_conflicts"),True)|{"reason":"70bps can be treated as directional", "numeric_checks":[{"proposal_quote":"70bps","page":1,"source_quote":"Source page","supported":True}]}
    pipe,work=fixture_pipeline(tmp_path,[{"candidates":[proposal]},gates])
    assert pipe.process(work,tmp_path/"source.pdf",[])==[]
    audit=json.loads((tmp_path/"evidence/one/review.json").read_text())["audit"]
    assert next(row for row in audit if "numeric_evidence_passed" in row)["numeric_evidence_passed"] is False


def test_seed_attributes_approved_goldman_report_to_original_bank(queue,approved_batch):
    _,state=queue;directory,_,_=approved_batch
    path=directory/'review-batch.json';batch=json.loads(path.read_text())
    batch['candidates'][0]['document_index']=18
    path.write_text(json.dumps(batch))
    assert seed.seed_reviewed(directory,state,fake_publisher())==1
    assert state.outbox()[0]['payload']['source']['publisher']=='Goldman Sachs'


def test_intermediary_candidate_is_held_without_blocking_valid_sibling(tmp_path):
    invalid=item();invalid['publisher']='Goldman Sachs via ZERO HEDGE'
    checks=dict.fromkeys(('supported','material_new_evidence','dates_verified','charts_complete','not_market_ear','no_unresolved_conflicts'),True)
    checks.update(reason='Verified',date_evidence={'page':1,'date_text':'4 September 2026','source_quote':'Report date: 4 September 2026','role':'report','role_verified':True})
    pipe,work=fixture_pipeline(tmp_path,[{'candidates':[invalid,item()]},checks])
    posts=pipe.process(work,tmp_path/'source.pdf',[])
    assert len(posts)==1 and posts[0]['source']['publisher']==item()['publisher']
    audit=json.loads((tmp_path/'evidence/one/review.json').read_text())['audit']
    assert any(a.get('held')=='invalid candidate' and 'original provider' in a['validation_error'] for a in audit)


def test_discovery_bad_scope_retains_cursor_and_continues(queue, monkeypatch):
    _, state = queue
    bad = '2026/september/sep 07'
    good = '2026/september/sep 08'
    monkeypatch.setattr(worker, 'date_scopes', lambda now: [(bad, '2026-09-07'), (good, '2026-09-08')])
    calls = []
    def listing(scope, cursor=None):
        calls.append((scope, cursor))
        if scope == bad:
            raise ValueError('private filename and token')
        return {'cursor': 'next', 'entries': [entry() | {'id': 'id:two', 'path_lower': '/joe mccann/current/' + good + '/new.pdf'}]}
    with pytest.raises(worker.DiscoveryError) as caught:
        worker.discover(SimpleNamespace(list_page=listing), state)
    assert calls == [(bad, 'cursor'), (good, None)]
    assert state.cursor(bad) == 'cursor' and state.cursor(good) == 'next'
    assert caught.value.discovered == 1
    assert caught.value.failures == [{'stage': 'discovery', 'type': 'ValueError', 'scope_id': hashlib.sha256(bad.encode()).hexdigest()[:16]}]
    assert 'private' not in str(caught.value)
    assert len(state.pending()) == 2


def test_cycle_discovery_failure_drains_validated_queue_and_outbox(queue, monkeypatch):
    root, state = queue
    monkeypatch.setattr(worker, 'date_scopes', lambda now: [])
    def listing(*a, **kw):
        raise ValueError('private filename')
    published = []
    publisher = SimpleNamespace(recent_posts=lambda **kw: [], publish=lambda post: published.append(post['id']))
    pipe = SimpleNamespace(process=lambda *a, **kw: [{'id': 'research-new'}])
    result = worker.cycle(root, SimpleNamespace(list_page=listing, download=lambda *a: root/'source.pdf'), state, pipe, publisher, publish=True)
    assert result['processed'] == 1 and result['published'] == 1
    assert result['errors'] == ['ValueError']
    assert result['discovery_errors'] == [{'stage': 'discovery', 'type': 'ValueError', 'scope_id': hashlib.sha256(b'2026/september/sep 07').hexdigest()[:16]}]
    assert published == ['research-new'] and state.outbox() == []
    assert state.cursor('2026/september/sep 07') == 'cursor'
    assert 'private' not in json.dumps(result)


def test_discovery_rate_limit_stops_remote_calls_and_preserves_queue(queue, monkeypatch):
    root, state = queue
    monkeypatch.setattr(worker, 'date_scopes', lambda now: [('2026/september/sep 07', '2026-09-07'), ('2026/september/sep 08', '2026-09-08')])
    calls = []
    def listing(*a, **kw):
        calls.append(True)
        raise worker.DropboxError('private token', status=429, retry_after=500)
    publisher = SimpleNamespace(recent_posts=lambda **kw: [])
    client = SimpleNamespace(list_page=listing, download=lambda *a: pytest.fail('download during backoff'))
    result = worker.cycle(root, client, state, None, publisher)
    assert calls == [True] and result['retry_after'] == 500
    assert result['processed'] == 0 and len(state.pending()) == 1
    assert result['discovery_errors'] == [{'stage': 'discovery', 'type': 'DropboxError', 'status': 429, 'scope_id': hashlib.sha256(b'2026/september/sep 07').hexdigest()[:16]}]


def test_discovery_invalid_page_rolls_back_then_retries_every_revision(queue, monkeypatch):
    _, state = queue
    scope = '2026/september/sep 07'
    monkeypatch.setattr(worker, 'date_scopes', lambda now: [])
    second = entry() | {'id': 'id:two', 'path_lower': '/joe mccann/current/' + scope + '/two.pdf'}
    third = entry() | {'id': 'id:three', 'path_lower': '/joe mccann/current/' + scope + '/three.pdf'}
    invalid = third | {'path_lower': '/outside/three.pdf'}
    cursors = []
    def listing(_scope, cursor=None):
        cursors.append(cursor)
        return {'cursor': 'new', 'entries': [second, invalid]}
    client = SimpleNamespace(list_page=listing)
    with pytest.raises(worker.DiscoveryError):
        worker.discover(client, state)
    assert state.cursor(scope) == 'cursor' and len(state.pending()) == 1
    invalid.update(third)
    assert worker.discover(client, state) == 2
    assert cursors == ['cursor', 'cursor']
    assert state.cursor(scope) == 'new' and len(state.pending()) == 3
    assert worker.discover(client, state) == 0


def test_discovery_heartbeat_retains_safe_diagnostics_locally_and_remotely(tmp_path, monkeypatch):
    from api import db_http
    saved = []
    monkeypatch.setattr(db_http, 'hrana_execute', lambda sql, params: saved.append(params))
    failures = [{'stage': 'discovery', 'type': 'DropboxError', 'status': 403}]
    error = worker.DiscoveryError(0, failures)
    assert worker.heartbeat(tmp_path, 'error', error)
    local = json.loads((tmp_path/'health.json').read_text())
    assert local['state'] == 'error'
    assert local['last_error']['discovery_errors'] == failures
    assert json.loads(saved[0][3]) == local['last_error']


def test_cli_daemon_interval_is_capped_at_one_minute(cli, monkeypatch):
    import sys
    from research import ingestion
    root, _, events, _ = cli
    monkeypatch.setattr(sys, 'argv', ['worker', '--root', str(root), '--daemon', '--interval', '120'])
    intervals = []
    monkeypatch.setattr(ingestion, 'run_daemon', lambda root, state, interval, *args: intervals.append(interval))
    worker.main()
    assert intervals == [60] and events == ['recover', 'close']


def test_cli_once_partial_discovery_failure_closes_and_exits_nonzero(cli, monkeypatch, capsys):
    _, _, events, _ = cli
    result = {'discovered': 0, 'processed': 1, 'published': 1, 'errors': ['ValueError'],
              'discovery_errors': [{'stage': 'discovery', 'type': 'ValueError', 'scope_id': 'safe-scope'}]}
    monkeypatch.setattr(worker, 'cycle', lambda *a, **kw: result)
    with pytest.raises(SystemExit) as caught:
        worker.main()
    assert caught.value.code == 1
    assert events[-2:] == ['error', 'close']
    assert json.loads(capsys.readouterr().out) == result
