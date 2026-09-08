"""Durable extraction must remain independent from expensive evidence review."""
from datetime import datetime, timezone
import multiprocessing
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
from research.ingestion import Backoff, parse_one
from research.state import State
from research.worker import discover


def entry(name='a', rev='1', day='08'):
    return {'.tag':'file', 'id':name, 'rev':rev, 'content_hash':'a'*64,
            'path_lower':f'/joe mccann/current/2026/september/sep {day}/{name}.pdf'}


@pytest.fixture
def state(tmp_path):
    tmp_path.chmod(0o700)
    instance = State(tmp_path / 'state.sqlite')
    yield instance
    instance.close()


def ingest(state, entries, day='08'):
    state.ingest_page(f'2026/september/sep {day}', {'cursor':'c', 'entries':entries}, f'2026-09-{day}')


def test_current_day_poll_rolls_over_without_waiting_for_review(state):
    calls = []
    client = SimpleNamespace(list_page=lambda scope, cursor: calls.append(scope) or {'cursor':'c', 'entries':[]})
    discover(client, state, datetime(2026,9,9,3,59,tzinfo=timezone.utc), current_only=True)
    discover(client, state, datetime(2026,9,9,4,0,tzinfo=timezone.utc), current_only=True)
    assert calls == ['2026/september/sep 08', '2026/september/sep 09']


def test_parse_new_arrival_while_previous_review_is_claimed(state, tmp_path):
    ingest(state, [entry('old')])
    old = state.pending()[0]
    state.claim(old['key'])
    ingest(state, [entry('new')])
    backoff = Backoff(multiprocessing.get_context('spawn'))
    downloaded = []
    client = SimpleNamespace(download=lambda metadata, out: downloaded.append(metadata['id']) or SpawnClient().download(metadata,out))
    assert parse_one(tmp_path, state, client, spawn_extract, backoff)
    assert downloaded == ['new']
    assert len(state.ready()) == 1


def test_parse_revision_idempotence_and_recovery(state, tmp_path):
    ingest(state, [entry()])
    work = state.unparsed()[0]
    assert state.claim_parse(work['key'])
    assert not state.claim_parse(work['key'])
    state.recover()
    assert state.claim_parse(work['key'])
    state.parsed(work['key'], str(tmp_path/'pdf'))
    ingest(state, [entry()])
    state.recover()
    assert not state.unparsed()
    assert len(state.ready()) == 1
    ingest(state, [entry(rev='2')])
    assert not state.ready()
    assert state.unparsed()[0]['rev'] == '2'


def test_failed_parse_does_not_starve_fresh_document(state, tmp_path):
    ingest(state, [entry('bad')])
    backoff = Backoff(multiprocessing.get_context('spawn'))
    client = SimpleNamespace(download=lambda metadata, out: tmp_path/'pdf')
    def fail(*args):
        raise ValueError('private text')
    assert parse_one(tmp_path, state, client, fail, backoff)
    ingest(state, [entry('new')])
    assert state.unparsed()[0]['file_id'] == 'new'
    assert state.db.execute('SELECT error FROM ingestion').fetchone()[0] == 'ValueError'


def test_today_and_fresh_parse_outrank_old_retry(state):
    ingest(state, [entry('old', day='07')], day='07')
    ingest(state, [entry('new')])
    assert state.unparsed()[0]['file_id'] == 'new'


def test_provider_backoff_blocks_download(state, tmp_path):
    ingest(state, [entry()])
    backoff = Backoff(multiprocessing.get_context('spawn'))
    backoff.record(SimpleNamespace(status=429, retry_after=60))
    assert not parse_one(tmp_path, state, None, None, backoff)
    assert state.unparsed()[0]['parse_attempts'] == 0


def test_deleted_or_superseded_parse_cannot_become_ready(state):
    ingest(state, [entry()])
    work = state.unparsed()[0]
    state.claim_parse(work['key'])
    ingest(state, [entry(rev='2')])
    state.parsed(work['key'], '/private/pdf')
    assert not state.ready()


def test_poll_respects_fixed_cadence_and_error_backoff(state, tmp_path, monkeypatch):
    from research import ingestion, worker
    clock = SimpleNamespace(now=0)
    health, calls = [], []
    monkeypatch.setattr(ingestion.time, 'monotonic', lambda: clock.now)
    monkeypatch.setattr(worker, 'heartbeat', lambda root, status, error=None, **kw: health.append(status))
    def listing(scope, cursor):
        calls.append(clock.now)
        if len(calls) == 1:
            raise worker.DropboxError('rate limited', status=429, retry_after=125)
        clock.now += 15  # Listing duration is not added to the poll interval.
        return {'cursor':'c', 'entries':[]}
    wait = SimpleNamespace(wait=lambda seconds: setattr(clock, 'now', clock.now+seconds))
    ingestion.poll(tmp_path, state, 60, lambda: len(calls)>=3, wait, threading.Event(),
                   Backoff(multiprocessing.get_context('spawn')), lambda: True,
                   lambda: SimpleNamespace(list_page=listing), lambda: clock.now)
    assert calls == [0,125,185]
    assert health == ['error','ok','ok']


def test_corrupt_cached_pdf_never_reaches_reviewer(state, tmp_path):
    from research.ingestion import review_one
    ingest(state, [entry()])
    work = state.unparsed()[0]
    state.claim_parse(work['key'])
    pdf = tmp_path/'bad.pdf'; pdf.write_bytes(b'changed')
    state.parsed(work['key'], str(pdf))
    assert review_one(tmp_path, state, None, None, False)
    row = state.db.execute('SELECT status,error FROM work').fetchone()
    assert tuple(row) == ('pending','ValueError')
    assert not state.outbox()


def test_parse_retry_is_bounded_and_restart_keeps_ready_cache(state, tmp_path):
    ingest(state, [entry()])
    key = state.unparsed()[0]['key']
    for _ in range(6):
        assert state.claim_parse(key)
        state.parse_retry(key, ValueError(), delay=0)
    assert not state.unparsed()
    assert state.db.execute('SELECT status FROM ingestion').fetchone()[0] == 'held'
    state.recover()
    assert not state.unparsed()


class SpawnClient:
    def download(self, metadata, directory):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = directory / (metadata['id']+'.pdf')
        target.write_bytes(b'pdf')
        return target


def spawn_extract(pdf, output):
    import json
    import hashlib
    (output/'evidence.json').write_text(json.dumps({'page_count':1, 'pages':[], 'source_sha256':hashlib.sha256(pdf.read_bytes()).hexdigest()}))
    return {'page_count':1, 'pages':[]}


class BlockingPipeline:
    def __init__(self, entered, release):
        self.entered, self.release = entered, release

    def process(self, *args, **kwargs):
        self.entered.set()
        self.release.wait(20)
        return []


def spawned_review(root, entered, release):
    from research.ingestion import review_one
    local = State(Path(root)/'state.sqlite')
    try:
        review_one(root, local, BlockingPipeline(entered, release),
                   SimpleNamespace(recent_posts=lambda **kw: []), False)
    finally:
        local.close()


def test_spawned_parser_receives_two_polls_during_blocked_review(state, tmp_path, monkeypatch):
    """Actual separate processes, independent DB connections and ready notification."""
    import time
    from research import ingestion, worker
    from research.dropbox import content_hash
    from research.state import date_scopes
    context = multiprocessing.get_context('spawn')
    stop, wake, parsed, entered, release = [context.Event() for _ in range(5)]
    backoff = Backoff(context)
    scope, folder_date = date_scopes()[-1]
    def item(name):
        value = entry(name)
        value['path_lower'] = f'/joe mccann/current/{scope}/{name}.pdf'
        value['content_hash'] = content_hash(b'pdf')
        return value
    state.ingest_page(scope, {'cursor':'start','entries':[item('old')]}, folder_date)
    assert parse_one(tmp_path, state, SpawnClient(), spawn_extract, backoff)
    review = context.Process(target=spawned_review, args=(str(tmp_path),entered,release))
    parser = context.Process(target=ingestion._consumer,
        args=(str(tmp_path),stop,wake,parsed,backoff,False,False,SpawnClient,None,spawn_extract))
    review.start(); parser.start()
    calls, clock = [], SimpleNamespace(now=0)
    monkeypatch.setattr(worker, 'heartbeat', lambda *args, **kwargs: True)
    def listing(scope, cursor):
        calls.append(clock.now)
        return {'cursor':str(len(calls)), 'entries':[item('new'+str(len(calls)))]}
    def wait(seconds):
        deadline = time.monotonic()+10
        while len(state.ready()) < len(calls) and time.monotonic()<deadline:
            parsed.wait(.05); parsed.clear()
        assert len(state.ready()) == len(calls)
        clock.now += 60
    try:
        assert entered.wait(10)
        ingestion.poll(tmp_path,state,60,lambda:len(calls)==2,SimpleNamespace(wait=wait),wake,
                       backoff,lambda:parser.is_alive() and review.is_alive(),
                       lambda:SimpleNamespace(list_page=listing),lambda:clock.now)
        assert calls == [0,60]
        assert len(state.ready()) == 2
        assert not release.is_set()
    finally:
        stop.set();wake.set();release.set()
        parser.join(10);review.join(10)
        for child in (parser,review):
            if child.is_alive(): child.kill();child.join()
    assert parser.exitcode == review.exitcode == 0


@pytest.mark.parametrize('tamper', ['markdown','evidence','pdf'])
def test_cached_evidence_rejects_changed_artifacts(state,tmp_path,tamper):
    import hashlib, json
    from research.ingestion import seal_evidence, cached_extract
    output = tmp_path/'evidence'; output.mkdir(mode=0o700)
    pdf = tmp_path/'source.pdf'; pdf.write_bytes(b'original')
    (output/'page-0001.md').write_text('original source')
    evidence = {'page_count':1,'source_sha256':hashlib.sha256(pdf.read_bytes()).hexdigest(),
                'pages':[{'page_number':1,'markdown_file':'page-0001.md'}]}
    (output/'evidence.json').write_text(json.dumps(evidence))
    seal_evidence(pdf,output)
    assert cached_extract(pdf,output) == evidence
    if tamper == 'markdown':
        (output/'page-0001.md').write_text('unsupported claim')
    elif tamper == 'evidence':
        evidence['page_count'] = 2
        (output/'evidence.json').write_text(json.dumps(evidence))
    else:
        pdf.write_bytes(b'changed source')
    with pytest.raises(ValueError): cached_extract(pdf,output)


def test_shutdown_kills_native_descendants_of_dead_group_leader(monkeypatch):
    from research import ingestion
    calls = []
    dead = SimpleNamespace(pid=123456, join=lambda timeout:None, is_alive=lambda:False)
    monkeypatch.setattr(ingestion.os,'killpg',lambda pid,sig:calls.append((pid,sig)))
    ingestion.stop_consumers([dead])
    assert calls == [(dead.pid,ingestion.signal.SIGTERM),(dead.pid,ingestion.signal.SIGKILL)]


@pytest.mark.parametrize('attempts',[0,5])
def test_revision_replaced_during_review_discards_result(state,tmp_path,attempts):
    from research.ingestion import review_one
    from research.dropbox import content_hash
    value=entry();value['content_hash']=content_hash(b'pdf')
    ingest(state,[value])
    backoff=Backoff(multiprocessing.get_context('spawn'))
    assert parse_one(tmp_path,state,SpawnClient(),spawn_extract,backoff)
    state.db.execute('UPDATE work SET attempts=?',(attempts,));state.db.commit()
    def review(*args,**kwargs):
        ingest(state,[entry(rev='2')])
        return [{'id':'stale'}]
    assert review_one(tmp_path,state,SimpleNamespace(process=review),SimpleNamespace(recent_posts=lambda **kw:[]),False)
    assert not state.outbox()
    assert state.db.execute("SELECT status FROM work WHERE rev='1'").fetchone()[0]=='superseded'


def test_outbox_snapshot_cancelled_before_dispatch_is_not_published(state, monkeypatch):
    from research.worker import flush_outbox
    ingest(state,[entry()]);work=state.pending()[0];state.claim(work['key'])
    state.complete(work['key'],publications=[{'id':'cancelled'}])
    original=state.outbox
    def snapshot(**kwargs):
        rows=original(**kwargs)
        ingest(state,[entry(rev='2')])
        return rows
    monkeypatch.setattr(state,'outbox',snapshot)
    assert flush_outbox(state,None)==0


def test_consumer_parse_loop_wakes_review_and_closes_own_connection(state,tmp_path,monkeypatch):
    from research import ingestion
    ingest(state,[entry()])
    stop,wake,parsed=threading.Event(),threading.Event(),threading.Event()
    monkeypatch.setattr(ingestion.os,'setsid',lambda:None)
    def extract(pdf,output):
        spawn_extract(pdf,output)
        stop.set()
    ingestion._consumer(tmp_path,stop,wake,parsed,Backoff(multiprocessing.get_context('spawn')),
                        False,False,SpawnClient,None,extract)
    assert parsed.is_set() and len(state.ready())==1


def test_consumer_initialization_failure_is_safe_and_interruptible(state,tmp_path,monkeypatch):
    import json
    from research import ingestion
    monkeypatch.setattr(ingestion.os,'setsid',lambda:None)
    stop=threading.Event()
    monkeypatch.setattr(stop,'wait',lambda seconds:stop.set())
    def broken():raise ValueError('secret')
    ingestion._consumer(tmp_path,stop,threading.Event(),threading.Event(),
                        Backoff(multiprocessing.get_context('spawn')),False,False,broken)
    detail=json.loads((tmp_path/'health-extraction.json').read_text())
    assert detail['state']=='error' and detail['error']=='ValueError'
    assert 'secret' not in (tmp_path/'health-extraction.json').read_text()


def test_consumer_review_loop_uses_ready_cache_and_stops(state,tmp_path,monkeypatch):
    from research import ingestion, publish
    from research.dropbox import content_hash
    value=entry();value['content_hash']=content_hash(b'pdf');ingest(state,[value])
    backoff=Backoff(multiprocessing.get_context('spawn'))
    assert parse_one(tmp_path,state,SpawnClient(),spawn_extract,backoff)
    stop=threading.Event()
    monkeypatch.setattr(ingestion.os,'setsid',lambda:None)
    monkeypatch.setattr(publish,'recent_posts',lambda **kwargs:[])
    def review(work,pdf,recent,progress):
        ingestion.cached_extract(pdf,tmp_path/'evidence'/work['key'])
        progress('rendered')
        stop.set()
        return []
    ingestion._consumer(tmp_path,stop,threading.Event(),threading.Event(),backoff,
                        True,False,pipeline_factory=lambda:SimpleNamespace(process=review))
    assert state.db.execute('SELECT status FROM work').fetchone()[0]=='complete'


@pytest.mark.parametrize('failure',[False,True])
def test_telemetry_preserves_discovery_and_stage_error_without_blocking_poll(tmp_path,monkeypatch,failure):
    import json
    from research import ingestion
    import api.db_http
    stop=threading.Event();calls=[]
    (tmp_path/'health.json').write_text(json.dumps({'state':'ok','updated_at':'fixed-poll-time','last_error':None}))
    ingestion.stage_health(tmp_path,'review','error',ValueError())
    def record(sql,args,**kwargs):
        calls.append((args,kwargs))
        if failure:raise TimeoutError()
    monkeypatch.setattr(api.db_http,'hrana_execute',record)
    monkeypatch.setattr(stop,'wait',lambda seconds:stop.set())
    ingestion.report_health(tmp_path,stop)
    assert calls[0][0][1:3]==('error','fixed-poll-time')
    assert calls[0][1]=={'timeout':2}
    assert json.loads(calls[0][0][3])['stages'][0]['error']=='ValueError'
    assert json.loads((tmp_path/'health.json').read_text())['state']=='ok'


def test_daemon_owns_bounded_consumers_and_cleanup(state,tmp_path,monkeypatch):
    from research import ingestion
    real_context=multiprocessing.get_context('spawn')
    children=[];groups=[]
    class Child:
        def __init__(self,**kwargs):
            self.pid=123450+len(children);self.started=False;children.append(self)
        def start(self):self.started=True
        def is_alive(self):return False
        def join(self,timeout):pass
    context=SimpleNamespace(Event=real_context.Event,Value=real_context.Value,Process=Child)
    monkeypatch.setattr(ingestion.multiprocessing,'get_context',lambda mode:context)
    monkeypatch.setattr(ingestion.os,'killpg',lambda pid,sig:groups.append((pid,sig)))
    ingestion.run_daemon(tmp_path,state,60,False,lambda:True)
    assert len(children)==2 and all(child.started for child in children)
    assert len(groups)==4


def test_poll_detects_dead_consumer_and_fails_service(state,tmp_path,monkeypatch):
    from research import ingestion,worker
    health=[]
    monkeypatch.setattr(worker,'heartbeat',lambda *args,**kwargs:health.append(args[1]))
    with pytest.raises(RuntimeError,match='consumer exited'):
        ingestion.poll(tmp_path,state,60,lambda:False,threading.Event(),threading.Event(),
                       Backoff(multiprocessing.get_context('spawn')),lambda:False)
    assert health==['error']


def test_successful_poll_does_not_clear_review_error_and_recovery_clears_only_stage(tmp_path):
    from research.ingestion import aggregate_health,stage_health
    base={'state':'ok','updated_at':'poll-time','last_error':None}
    stage_health(tmp_path,'review','error',ValueError())
    failed=aggregate_health(tmp_path,base)
    assert failed['state']=='error' and failed['updated_at']=='poll-time'
    stage_health(tmp_path,'review','ok')
    recovered=aggregate_health(tmp_path,failed)
    assert recovered['state']=='ok' and recovered['last_error'] is None


def test_successful_review_never_clears_discovery_error(tmp_path):
    from research.ingestion import aggregate_health,stage_health
    base={'state':'error','updated_at':'poll-time','last_error':{'message':'DropboxError'}}
    stage_health(tmp_path,'extraction','error',ValueError())
    failed=aggregate_health(tmp_path,base)
    stage_health(tmp_path,'extraction','ok');stage_health(tmp_path,'review','ok')
    restored=aggregate_health(tmp_path,failed)
    assert restored['state']=='error' and restored['last_error']==base['last_error']


@pytest.mark.parametrize('kind',['traversal','symlink','source'])
def test_untrusted_extraction_cannot_seal(state,tmp_path,kind):
    import json,hashlib
    from research.ingestion import seal_evidence
    pdf=tmp_path/'pdf';pdf.write_bytes(b'pdf')
    source=hashlib.sha256(b'pdf').hexdigest()
    name='../outside.md' if kind=='traversal' else 'page.md'
    if kind=='symlink':(tmp_path/name).symlink_to(pdf)
    (tmp_path/'evidence.json').write_text(json.dumps({'source_sha256':'wrong' if kind=='source' else source,
        'pages':[{'markdown_file':name}]}))
    with pytest.raises(ValueError):seal_evidence(pdf,tmp_path)
    assert not (tmp_path/'ingestion-manifest.json').exists()


def test_review_error_exhausts_lease_without_republishing(state,tmp_path):
    from research.ingestion import review_one
    from research.dropbox import content_hash
    value=entry();value['content_hash']=content_hash(b'pdf');ingest(state,[value])
    assert parse_one(tmp_path,state,SpawnClient(),spawn_extract,Backoff(multiprocessing.get_context('spawn')))
    state.db.execute('UPDATE work SET attempts=5');state.db.commit()
    def fail(*args,**kwargs):raise ValueError('secret')
    publisher=SimpleNamespace(recent_posts=lambda **kwargs:[],publish=lambda *args:pytest.fail('no publication'))
    assert review_one(tmp_path,state,SimpleNamespace(process=fail),publisher,True)
    assert state.db.execute('SELECT status FROM work').fetchone()[0]=='complete'
    assert not review_one(tmp_path,state,None,publisher,True)


@pytest.mark.parametrize('throttled',[False,True])
def test_idle_or_throttled_consumer_stops_promptly_without_download(state,tmp_path,monkeypatch,throttled):
    from research import ingestion
    stop,wake=threading.Event(),threading.Event()
    monkeypatch.setattr(ingestion.os,'setsid',lambda:None)
    monkeypatch.setattr(stop,'wait',lambda delay:stop.set())
    monkeypatch.setattr(wake,'wait',lambda delay:stop.set())
    backoff=Backoff(multiprocessing.get_context('spawn'))
    if throttled:backoff.record(SimpleNamespace(status=429,retry_after=60))
    ingestion._consumer(tmp_path,stop,wake,threading.Event(),backoff,False,False,SpawnClient)
    assert stop.is_set() and not (tmp_path/'downloads').exists()


def test_shared_backoff_guards_subsequent_pagination(state):
    from research.ingestion import GuardedClient
    from research.dropbox import DropboxError
    backoff=Backoff(multiprocessing.get_context('spawn'))
    backoff.record(SimpleNamespace(status=429,retry_after=60))
    with pytest.raises(DropboxError) as error:GuardedClient(None,backoff).list_page('scope')
    assert error.value.status==429 and error.value.retry_after>0


def test_shutdown_before_child_setsid_falls_back_to_process_signal(monkeypatch):
    from research import ingestion
    calls=[]
    child=SimpleNamespace(pid=123456,join=lambda timeout:None,is_alive=lambda:True,
        terminate=lambda:calls.append('term'),kill=lambda:calls.append('kill'))
    def missing(*args):raise ProcessLookupError()
    monkeypatch.setattr(ingestion.os,'killpg',missing)
    ingestion.stop_consumers([child])
    assert calls==['term','kill']
