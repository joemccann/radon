"""VIX model contract; source samples attributed in docs/indicators/vix-model-spread.md."""
from pathlib import Path
import math
import statistics
import pytest
from fetch_vix_model_spread import parse_index_csv, compute_series, build_output

FIXTURES = Path(__file__).parent / "fixtures"

def test_real_source_schemas_and_calibration():
    sources = [parse_index_csv((FIXTURES / f"vix_model_spread_{s.lower()}_sample.csv").read_text(), s)
               for s in ("VIX", "VIXEQ", "COR1M")]
    rows = compute_series(*sources)
    assert rows[-1]["date"] == "2026-10-16"
    assert rows[-1]["model_vix"] == pytest.approx(13.235944124617633)
    assert rows[-1]["spread"] == pytest.approx(6.754055875382365)
    assert rows[-1]["mean_20"] is None

def test_windows_are_complete_population_stats_and_never_forward_looking():
    from datetime import date,timedelta
    dates = [(date(2025,1,1)+timedelta(days=i)).isoformat() for i in range(260)]
    vix=[{"date":d,"value":20+(i%17)/10} for i,d in enumerate(dates)]
    equity=[{"date":d,"value":30} for d in dates]
    cor=[{"date":d,"value":25} for d in dates]
    rows=compute_series(vix,equity,cor)
    for w in (20,252):
        assert rows[w-2][f"mean_{w}"] is None
        values=[r["spread"] for r in rows[-w:]]
        mean=statistics.mean(values);sd=statistics.pstdev(values)
        assert rows[-1][f"mean_{w}"] == pytest.approx(mean)
        assert rows[-1][f"upper_{w}"] == pytest.approx(mean+2*sd)
        assert rows[-1][f"z_{w}"] == pytest.approx((values[-1]-mean)/sd)
    assert compute_series(vix[:100],equity[:100],cor[:100])==rows[:100]

def test_same_date_join_zero_correlation_and_invalid_values():
    v=[{"date":"2026-10-14","value":20},{"date":"2026-10-15","value":21}]
    eq=[{"date":"2026-10-15","value":40}]
    c=[{"date":"2026-10-14","value":25},{"date":"2026-10-15","value":0}]
    assert len(compute_series(v,eq,c))==1
    assert compute_series(v,eq,c)[0]["model_vix"]==0
    for bad in (-1,101,math.nan,math.inf):
        assert compute_series(v,eq,[{"date":"2026-10-15","value":bad}])==[]

def test_output_uses_actual_common_date():
    rows=compute_series(*[[{"date":"2026-10-16","value":v}] for v in (19.99,41.71,10.07)])
    out=build_output(rows,"2026-10-18T08:00:00+00:00",{})
    assert out["data_date"]=="2026-10-16"
    assert out["current"]==rows[-1]
    assert out["count"]==1

@pytest.mark.parametrize('symbol,column', [('VIX','CLOSE'),('VIXEQ','VIXEQ'),('COR1M','CLOSE')])
def test_parser_deduplicates_and_rejects_invalid_dates_and_values(symbol,column):
    text=f'DATE,{column}\n10/16/2026,20\n10/15/2026,21\n10/16/2026,22\n02/30/2026,10\n10/17/2026,nan\n10/18/2026,inf\n10/19/2026,-1\n'
    assert parse_index_csv(text,symbol)==[{'date':'2026-10-15','value':21.0},{'date':'2026-10-16','value':22.0}]
    with pytest.raises(ValueError):parse_index_csv('DATE,WRONG\n10/16/2026,1',symbol)

def test_zero_sd_is_not_a_signal_and_invalid_iso_dates_are_excluded():
    from datetime import date,timedelta
    dates=[(date(2025,1,1)+timedelta(days=i)).isoformat() for i in range(252)]
    rows=compute_series(*[[dict(date=d,value=v) for d in dates] for v in (20,40,25)])
    for w in (20,252):
        assert rows[-1][f'std_{w}']==0
        assert rows[-1][f'z_{w}'] is None
    assert compute_series([{'date':'2026-1-1','value':20}],[],[])==[]

@pytest.fixture
def orchestration(monkeypatch,tmp_path):
    import fetch_vix_model_spread as mod
    from unittest.mock import Mock
    fake=Mock()
    monkeypatch.setattr(mod,'writer',fake)
    monkeypatch.setattr(mod,'VIX_MODEL_SPREAD_JSON',tmp_path/'model.json')
    monkeypatch.setattr(mod,'_probe_ib',lambda:{'status':'unavailable'})
    monkeypatch.setattr(mod,'_fetch_live',lambda probe,now:None)
    monkeypatch.setattr(mod,'hrana_query',lambda *a,**k:[])
    return mod,fake

class SourceClient:
    def __init__(self,texts): self.texts=texts;self.calls=[];self.closed=False
    def fetch_history(self,symbol,if_modified_since=None):
        self.calls.append((symbol,if_modified_since))
        return self.texts[symbol],f'{symbol}-stamp'
    def close(self):self.closed=True

def _texts():
    return {s:(FIXTURES/f'vix_model_spread_{s.lower()}_sample.csv').read_text() for s in ('VIX','VIXEQ','COR1M')}

def _now(day=18):
    from datetime import datetime,timezone
    return datetime(2026,10,day,8,tzinfo=timezone.utc)

def test_run_writes_snapshot_health_and_atomic_fallback(orchestration):
    mod,writer=orchestration
    payload=mod.run(SourceClient(_texts()),previous={},now=_now())
    assert payload['source_dates']==dict.fromkeys(('VIX','VIXEQ','COR1M'),'2026-10-16')
    assert payload['source']=='cboe'
    writer.upsert_vix_model_spread_rows.assert_called_once_with(payload['series'],recorded_at=payload['scan_time'])
    writer.upsert_scan_snapshot.assert_any_call('vix-model-spread',payload['scan_time'],payload)
    assert [c.args[0] for c in writer.upsert_scan_snapshot.call_args_list]==['vix-model-spread','vix-model-spread:live']
    assert mod._read_previous()['current']==payload['current']

def test_all_304_refreshes_heartbeat_not_history_and_rechecks_session(orchestration):
    mod,writer=orchestration
    previous=mod.run(SourceClient(_texts()),previous={},now=_now())
    writer.reset_mock()
    client=SourceClient(dict.fromkeys(('VIX','VIXEQ','COR1M')))
    payload=mod.run(client,previous=previous,now=_now(20))
    assert payload['scan_time']!=previous['scan_time']
    assert payload['status']=='stale_source'
    assert payload['expected_session']=='2026-10-19'
    assert payload['data_date']=='2026-10-16'
    writer.upsert_vix_model_spread_rows.assert_not_called()
    writer.record_service_health.assert_called_once()
    assert len(client.calls)==3

def test_partial_304_retains_other_raw_series_and_upserts_correction(orchestration):
    mod,writer=orchestration
    previous=mod.run(SourceClient(_texts()),previous={},now=_now())
    writer.reset_mock()
    texts=dict.fromkeys(('VIX','VIXEQ','COR1M'))
    texts['VIXEQ']=_texts()['VIXEQ'].replace('41.71','42.71')
    payload=mod.run(SourceClient(texts),previous=previous,now=_now())
    assert payload['current']['vixeq']==42.71
    assert payload['current']['spread']!=previous['current']['spread']
    writer.upsert_vix_model_spread_rows.assert_called_once()
    assert payload['source_series']['VIX']==previous['source_series']['VIX']

def test_304_without_raw_cache_refetches_unconditionally(orchestration):
    mod,writer=orchestration
    previous=mod.run(SourceClient(_texts()),previous={},now=_now())
    previous.pop('source_series')
    class Client(SourceClient):
        def fetch_history(self,symbol,if_modified_since=None):
            self.calls.append((symbol,if_modified_since))
            return (None if if_modified_since else _texts()[symbol]),f'{symbol}-stamp'
    client=Client({})
    mod.run(client,previous=previous,now=_now())
    assert len(client.calls)==6

@pytest.mark.parametrize('bad', ['empty','regressed','no_overlap','transport'])
def test_failed_source_preserves_last_good_and_records_error(orchestration,bad):
    mod,writer=orchestration
    previous=mod.run(SourceClient(_texts()),previous={},now=_now())
    cached=mod.VIX_MODEL_SPREAD_JSON.read_bytes()
    writer.reset_mock()
    texts=_texts()
    if bad=='empty':texts['VIXEQ']='DATE,VIXEQ\n'
    if bad=='regressed':texts['VIXEQ']='DATE,VIXEQ\n10/13/2026,40\n'
    if bad=='no_overlap':texts['VIXEQ']='DATE,VIXEQ\n10/19/2026,40\n'
    client=SourceClient(texts)
    if bad=='transport':
        def fail(*a,**k):raise RuntimeError('upstream unavailable')
        client.fetch_history=fail
    with pytest.raises((ValueError,RuntimeError)):
        mod.run(client,previous=previous,now=_now())
    assert mod.VIX_MODEL_SPREAD_JSON.read_bytes()==cached
    writer.upsert_scan_snapshot.assert_not_called()
    assert writer.record_service_health.call_args.args==('vix-model-spread','error')

def test_db_failure_writes_atomic_fallback_but_is_not_success(orchestration):
    mod,writer=orchestration
    writer.upsert_scan_snapshot.side_effect=RuntimeError('database unavailable')
    with pytest.raises(RuntimeError):mod.run(SourceClient(_texts()),previous={},now=_now())
    assert mod._read_previous()['count']==3
    assert writer.record_service_health.call_args.args==('vix-model-spread','error')

def test_owned_client_closes_on_failure_and_initialization_failure_is_heartbeated(orchestration,monkeypatch):
    mod,writer=orchestration
    client=SourceClient(dict.fromkeys(('VIX','VIXEQ','COR1M'),'DATE,CLOSE\n'))
    monkeypatch.setattr(mod,'CboeClient',lambda:client)
    with pytest.raises(ValueError):mod.run(previous={},now=_now())
    assert client.closed
    def fail():raise RuntimeError('client init failed')
    monkeypatch.setattr(mod,'CboeClient',fail)
    with pytest.raises(RuntimeError):mod.run(previous={},now=_now())
    assert writer.record_service_health.call_args.args==('vix-model-spread','error')

def test_migration_and_writer_are_idempotent_and_correct_same_session(monkeypatch):
    import sqlite3
    from db import writer
    conn=sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY,applied_at TEXT)')
    sql=(Path(__file__).parents[1]/'db/migrations/0098_vix_model_spread.sql').read_text()
    conn.executescript(sql);conn.executescript(sql)
    assert conn.execute('SELECT count(*) FROM schema_migrations WHERE version=98').fetchone()[0]==1
    monkeypatch.setattr(writer,'get_db',lambda:conn)
    rows=compute_series(*[[dict(date='2026-10-16',value=v)] for v in (20,40,25)])
    writer.upsert_vix_model_spread_rows(rows,recorded_at='first')
    rows[0]['spread']=6
    writer.upsert_vix_model_spread_rows(rows,recorded_at='second')
    assert conn.execute('SELECT count(*),spread,mean_20,recorded_at FROM vix_model_spread_history').fetchone()==(1,6,None,'second')
    writer.upsert_vix_model_spread_rows([])
    conn.close()

def test_ib_probe_skips_only_explicit_non_authenticated_and_uses_pool(monkeypatch):
    import fetch_vix_model_spread as mod
    import requests
    from unittest.mock import Mock
    request=Mock();request.return_value.json.return_value={'bars':[{'date':'2026-10-16','close':41.71}]}
    monkeypatch.setattr(requests,'post',request)
    monkeypatch.setattr(mod,'ib_auth_state',lambda:'awaiting_2fa')
    assert mod._probe_ib()['status']=='skipped'
    request.assert_not_called()
    for state in ('authenticated',None):
        monkeypatch.setattr(mod,'ib_auth_state',lambda:state)
        assert mod._probe_ib()['status']=='available'
        assert request.call_args.kwargs['timeout']==15
        assert request.call_args.kwargs['json']['contract']['symbol']=='VIXEQ'
    request.side_effect=requests.Timeout('timed out')
    assert mod._probe_ib()=={'status':'unavailable'}

def test_cli_json_stdout_and_error_exit(orchestration,monkeypatch,capsys):
    import json
    mod,writer=orchestration
    monkeypatch.setattr('sys.argv',['fetch_vix_model_spread.py','--json'])
    monkeypatch.setattr(mod,'run',lambda:{'count':1})
    assert mod.main()==0
    assert json.loads(capsys.readouterr().out)=={'count':1}
    def fail():raise ValueError('no aligned data')
    monkeypatch.setattr(mod,'run',fail)
    assert mod.main()==1
    out=capsys.readouterr()
    assert not out.out
    assert 'no aligned data' in out.err

def test_nonfinite_derived_arithmetic_refuses_snapshot():
    from datetime import date,timedelta
    dates=[(date(2025,1,1)+timedelta(days=i)).isoformat() for i in range(20)]
    vix=[dict(date=d,value=1e308 if i%2 else 1) for i,d in enumerate(dates)]
    eq=[dict(date=d,value=1e308 if i%2==0 else 1) for i,d in enumerate(dates)]
    cor=[dict(date=d,value=100) for d in dates]
    with pytest.raises(ValueError,match='Nonfinite'):
        compute_series(vix,eq,cor)

def test_watchdog_catalog_has_daily_and_source_appropriate_windows():
    from watchdog.services import SCHEDULED_SERVICES, BUCKETS
    assert SCHEDULED_SERVICES['vix-model-spread']=={'open':26*3600,'closed':48*3600,'requires_ib':False}
    assert 'vix-model-spread' in BUCKETS['daily']

@pytest.mark.parametrize('bad',[{}, {'series':[]}, {'series':[{}],'current':None,'count':1}])
def test_persist_refuses_empty_or_inconsistent_before_io(orchestration,bad):
    mod,writer=orchestration
    with pytest.raises(ValueError,match='Refusing'):
        mod.persist_result(bad)
    writer.upsert_vix_model_spread_rows.assert_not_called()
    writer.upsert_scan_snapshot.assert_not_called()
    assert not mod.VIX_MODEL_SPREAD_JSON.exists()

def test_previous_snapshot_is_turso_first_and_304_uses_hash(orchestration,monkeypatch):
    import json
    mod,writer=orchestration
    previous=mod.run(SourceClient(_texts()),previous={},now=_now())
    def query(sql,args):
        if len(args)==1:
            return [(previous['scan_time'],json.dumps(previous['source_last_modified']),json.dumps(previous['source_dates']),previous['series_hash'])]
        symbol=args[0].split('.')[-1]
        return [(json.dumps(previous['source_series'][symbol]),)]
    monkeypatch.setattr(mod,'hrana_query',query)
    mod.VIX_MODEL_SPREAD_JSON.unlink()
    retained=mod._read_previous()
    assert retained['source_dates']==previous['source_dates']
    assert 'series' not in retained
    writer.reset_mock()
    result=mod.run(SourceClient(dict.fromkeys(('VIX','VIXEQ','COR1M'))),now=_now(20))
    assert result['status']=='stale_source'
    writer.upsert_vix_model_spread_rows.assert_not_called()

def test_previous_turso_failure_uses_verified_disk(orchestration,monkeypatch):
    mod,writer=orchestration
    previous=mod.run(SourceClient(_texts()),previous={},now=_now())
    def fail(*a,**k):raise RuntimeError('db unavailable')
    monkeypatch.setattr(mod,'hrana_query',fail)
    assert mod._read_previous()['series_hash']==previous['series_hash']

def test_future_daily_source_is_not_a_fresh_close(orchestration):
    mod,writer=orchestration
    texts=_texts()
    texts['VIXEQ']='DATE,VIXEQ\n10/19/2026,40\n'
    with pytest.raises(ValueError,match='last completed session'):
        mod.run(SourceClient(texts),previous={},now=_now())
    assert not mod.VIX_MODEL_SPREAD_JSON.exists()
    writer.upsert_scan_snapshot.assert_not_called()

def test_writer_batches_history_instead_of_one_roundtrip_per_day(monkeypatch):
    from db import writer
    from unittest.mock import Mock
    from datetime import date,timedelta
    fake=Mock()
    monkeypatch.setattr(writer,'get_db',lambda:fake)
    dates=[(date(2025,1,1)+timedelta(days=i)).isoformat() for i in range(700)]
    rows=compute_series(*[[dict(date=d,value=v) for d in dates] for v in (20,40,25)])
    writer.upsert_vix_model_spread_rows(rows,recorded_at='2026-10-18T02:50:00Z')
    budget=math.ceil(len(rows)/writer._PRICE_HISTORY_INSERT_CHUNK_ROWS)
    assert fake.execute.call_count==budget
    fake.commit.assert_called_once()
    assert sum(len(call.args[1]) for call in fake.execute.call_args_list)==700*17

@pytest.mark.parametrize('bars',[None,{},'not-bars',[{}],[{'date':'2026-10-16','close':math.nan}]])
def test_ib_probe_never_marks_malformed_bars_available(monkeypatch,bars):
    import fetch_vix_model_spread as mod
    import requests
    from unittest.mock import Mock
    monkeypatch.setattr(mod,'ib_auth_state',lambda:'authenticated')
    response=Mock()
    response.json.return_value={'bars':bars}
    monkeypatch.setattr(requests,'post',Mock(return_value=response))
    assert mod._probe_ib()['status']!='available'

def test_error_heartbeat_failure_remains_a_nonzero_run(orchestration,capsys):
    mod,writer=orchestration
    writer.record_service_health.side_effect=RuntimeError('health unavailable')
    texts=_texts();texts['VIXEQ']='DATE,VIXEQ\n'
    with pytest.raises(ValueError):mod.run(SourceClient(texts),previous={},now=_now())
    assert 'heartbeat failed' in capsys.readouterr().err
    assert not mod.VIX_MODEL_SPREAD_JSON.exists()

def test_cli_human_summary_stays_on_stderr(orchestration,monkeypatch,capsys):
    mod,writer=orchestration
    monkeypatch.setattr('sys.argv',['fetch_vix_model_spread.py'])
    monkeypatch.setattr(mod,'run',lambda:{'data_date':'2026-10-16','current':{'spread':6.75},'status':'ok','expected_session':'2026-10-16'})
    assert mod.main()==0
    out=capsys.readouterr()
    assert out.out==''
    assert 'Data Freshness: ok' in out.err

@pytest.mark.parametrize('source_index', [0, 1, 2])
@pytest.mark.parametrize('boolean_value', [True, False])
def test_boolean_observation_cannot_be_coerced_into_a_model_input(source_index, boolean_value):
    sources = [[{'date': '2026-10-16', 'value': value}] for value in (20, 40, 25)]
    sources[source_index][0]['value'] = boolean_value
    assert compute_series(*sources) == []


def test_parser_refuses_an_unrecognized_index_instead_of_guessing_its_schema():
    with pytest.raises(ValueError, match='Unsupported index'):
        parse_index_csv('DATE,CLOSE\n10/16/2026,20\n', 'SPX')


@pytest.mark.parametrize('corrupt_bytes', [b'{truncated', b'[]', b'{"_checksum":"invalid","count":1}'])
def test_corrupt_disk_cache_is_not_used_as_conditional_request_state(orchestration, corrupt_bytes):
    mod, writer = orchestration
    mod.VIX_MODEL_SPREAD_JSON.write_bytes(corrupt_bytes)
    assert mod._read_previous() is None
    client = SourceClient(_texts())
    recovered = mod.run(client, now=_now())
    assert recovered['count'] == 3
    assert all(stamp is None for _, stamp in client.calls)
    assert mod._read_disk_previous()['current'] == recovered['current']


def test_incomplete_database_source_cache_keeps_verified_disk_state(orchestration, monkeypatch):
    import json
    mod, writer = orchestration
    previous = mod.run(SourceClient(_texts()), previous={}, now=_now())
    cached = mod.VIX_MODEL_SPREAD_JSON.read_bytes()

    def query(sql, args):
        if len(args) == 1:
            return [(previous['scan_time'], json.dumps(previous['source_last_modified']),
                     json.dumps(previous['source_dates']), previous['series_hash'])]
        return [('[]',)]

    monkeypatch.setattr(mod, 'hrana_query', query)
    assert mod._read_previous() == previous
    assert mod.VIX_MODEL_SPREAD_JSON.read_bytes() == cached


def test_double_304_without_retained_inputs_refuses_to_replace_last_good(orchestration):
    mod, writer = orchestration
    previous = mod.run(SourceClient(_texts()), previous={}, now=_now())
    cached = mod.VIX_MODEL_SPREAD_JSON.read_bytes()
    previous.pop('source_series')
    writer.reset_mock()
    client = SourceClient(dict.fromkeys(('VIX', 'VIXEQ', 'COR1M')))
    with pytest.raises(ValueError, match='304 without retained source rows'):
        mod.run(client, previous=previous, now=_now())
    assert client.calls == [('VIX', 'VIX-stamp'), ('VIX', None)]
    assert mod.VIX_MODEL_SPREAD_JSON.read_bytes() == cached
    writer.upsert_vix_model_spread_rows.assert_not_called()
    writer.upsert_scan_snapshot.assert_not_called()
    assert writer.record_service_health.call_args.args == ('vix-model-spread', 'error')


def test_valid_but_disjoint_completed_sessions_never_become_an_aligned_snapshot(orchestration):
    mod, writer = orchestration
    mod.run(SourceClient(_texts()), previous={}, now=_now())
    cached = mod.VIX_MODEL_SPREAD_JSON.read_bytes()
    writer.reset_mock()
    client = SourceClient({
        'VIX': 'DATE,CLOSE\n10/15/2026,20\n',
        'VIXEQ': 'DATE,VIXEQ\n10/14/2026,40\n',
        'COR1M': 'DATE,CLOSE\n10/15/2026,25\n',
    })
    with pytest.raises(ValueError, match='No aligned VIX model observations'):
        mod.run(client, previous={}, now=_now())
    assert mod.VIX_MODEL_SPREAD_JSON.read_bytes() == cached
    writer.upsert_scan_snapshot.assert_not_called()
    assert writer.record_service_health.call_args.args == ('vix-model-spread', 'error')


def test_broker_availability_probe_sends_only_the_hermetic_mdw_header(monkeypatch, capsys):
    import fetch_vix_model_spread as mod
    import requests
    from unittest.mock import Mock
    monkeypatch.setenv('MDW_API_KEY', 'vix-model-unit-test-only')
    monkeypatch.setattr(mod, 'ib_auth_state', lambda: 'authenticated')
    response = Mock()
    response.json.return_value = {'bars': [{'date': '2026-10-16', 'close': 41.71}]}
    post = Mock(return_value=response)
    monkeypatch.setattr(requests, 'post', post)
    assert mod._probe_ib() == {'status': 'available', 'count': 1}
    assert post.call_args.kwargs['headers']['X-API-Key'] == 'vix-model-unit-test-only'
    captured = capsys.readouterr()
    assert 'vix-model-unit-test-only' not in captured.out + captured.err


def test_cli_human_output_identifies_the_stale_session_and_signed_spread(orchestration, monkeypatch, capsys):
    mod, writer = orchestration
    monkeypatch.setattr('sys.argv', ['fetch_vix_model_spread.py'])
    monkeypatch.setattr(mod, 'run', lambda: {
        'data_date': '2026-10-16', 'current': {'spread': -1.25},
        'status': 'stale_source', 'expected_session': '2026-10-19',
    })
    assert mod.main() == 0
    captured = capsys.readouterr()
    assert captured.out == ''
    assert '2026-10-16 spread -1.25' in captured.err
    assert 'Data Freshness: stale_source (expected 2026-10-19)' in captured.err


@pytest.fixture(scope="module")
def full_history_payload():
    """Production-sized shape, generated observations rather than source redistribution."""
    from datetime import date, timedelta
    dates = [(date(1990, 1, 1) + timedelta(days=i)).isoformat() for i in range(12000)]
    source = {
        'VIX': [dict(date=day, value=20 + i % 17 / 100) for i, day in enumerate(dates)],
        'VIXEQ': [dict(date=day, value=41 + i % 13 / 100) for i, day in enumerate(dates[-3101:])],
        'COR1M': [dict(date=day, value=10 + i % 11 / 100) for i, day in enumerate(dates[-3101:])],
    }
    rows = compute_series(*(source[s] for s in ('VIX', 'VIXEQ', 'COR1M')))
    payload = build_output(rows, '2026-10-18T08:00:00Z', {})
    payload.update(source_series=source, series_hash='full-history-test')
    return payload


def test_full_history_snapshot_uses_bounded_requests_without_losing_any_observation(
        orchestration, monkeypatch, full_history_payload):
    import json
    import sqlite3
    mod, writer = orchestration
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE scan_snapshots(service TEXT,scan_time TEXT,payload TEXT,PRIMARY KEY(service,scan_time))')
    payload = full_history_payload
    assert len(json.dumps(payload).encode()) > 2_000_000
    requests = []
    writer.SCAN_SNAPSHOT_KEEP = 30

    def execute(sql, args=(), **kwargs):
        # Mirrors Hrana's escaped JSON argument encoding, including SQL overhead.
        wire = json.dumps({'requests': [{'type': 'execute', 'stmt': {
            'sql': sql, 'args': [{'type': 'text', 'value': str(a)} for a in args]}}, {'type': 'close'}]}).encode()
        assert len(wire) < 256 * 1024
        requests.append(wire)
        return conn.execute(sql, args)

    def generic(service, stamp, snapshot):
        execute('INSERT OR REPLACE INTO scan_snapshots VALUES(?,?,?)', (service, stamp, json.dumps(snapshot)))

    writer.upsert_scan_snapshot.side_effect = generic
    monkeypatch.setattr(mod, 'hrana_execute', execute, raising=False)
    monkeypatch.setattr(mod, 'hrana_query', lambda sql, args=(), **kwargs: [tuple(str(v) for v in row) for row in execute(sql, args, **kwargs).fetchall()])
    mod.persist_result(payload)
    stored = conn.execute('SELECT payload FROM scan_snapshots WHERE service=?', (mod.SERVICE,)).fetchone()
    assert json.loads(stored[0]) == payload
    assert len(requests) > 2
    assert conn.execute('SELECT count(*) FROM scan_snapshots').fetchone() == (1,)
    assert mod._read_disk_previous() == payload
    writer.record_service_health.assert_called_once()
    conn.close()


@pytest.fixture
def staged_snapshot_store(orchestration, monkeypatch):
    import sqlite3
    mod, writer = orchestration
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE scan_snapshots(service TEXT,scan_time TEXT,payload TEXT,PRIMARY KEY(service,scan_time))')
    writer.SCAN_SNAPSHOT_KEEP = 30
    monkeypatch.setattr(mod, '_SNAPSHOT_CHUNK_CHARS', 64)

    def execute(sql, args=(), **kwargs):
        assert 0 < kwargs['timeout'] <= 4
        return conn.execute(sql, args)

    def query(sql, args=(), **kwargs):
        from db.hrana_http import _rows_as_tuples
        rows = execute(sql, args, **kwargs).fetchall()
        return _rows_as_tuples({'rows': [[{'type': 'integer', 'value': str(v)} for v in row] for row in rows]})

    monkeypatch.setattr(mod, 'hrana_execute', execute)
    monkeypatch.setattr(mod, 'hrana_query', query)
    yield mod, writer, conn, execute
    conn.close()


def test_staging_prunes_only_day_old_orphans_preserves_concurrent_runs_and_keeps_30_snapshots(
        staged_snapshot_store):
    from datetime import datetime, timedelta, timezone
    mod, writer, conn, _ = staged_snapshot_store
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=2)).isoformat() + ':orphan'
    active = now.isoformat() + ':concurrent'
    conn.executemany('INSERT INTO scan_snapshots VALUES(?,?,?)', [
        (mod._SNAPSHOT_STAGING_SERVICE, old, '{"chunks":[]}'),
        (mod._SNAPSHOT_STAGING_SERVICE, active, '{"chunks":[]}'),
        ('other:staging', old, '{"chunks":[]}'),
        *[(mod.SERVICE, f'2026-09-{i:02}T08:00:00Z', '{}') for i in range(1, 32)],
    ])
    payload = mod.run(SourceClient(_texts()), previous={}, now=_now())
    assert conn.execute('SELECT count(*) FROM scan_snapshots WHERE service=?', (mod.SERVICE,)).fetchone() == (30,)
    assert conn.execute('SELECT scan_time FROM scan_snapshots WHERE service=?', (mod._SNAPSHOT_STAGING_SERVICE,)).fetchall() == [(active,)]
    assert conn.execute('SELECT count(*) FROM scan_snapshots WHERE service=?', ('other:staging',)).fetchone() == (1,)
    assert mod._read_previous()['source_series'] == payload['source_series']


@pytest.mark.parametrize('failing_step', ['insert', 'chunk', 'promote', 'prune', 'cleanup'])
def test_staging_failure_never_publishes_partial_snapshot_and_cleans_its_own_row(
        staged_snapshot_store, monkeypatch, failing_step):
    mod, writer, conn, execute = staged_snapshot_store
    conn.execute('INSERT INTO scan_snapshots VALUES(?,?,?)', (mod.SERVICE, 'old', '{"last_good":true}'))

    def failing(sql, args=(), **kwargs):
        if ((failing_step == 'insert' and sql.startswith('INSERT INTO'))
                or (failing_step == 'chunk' and sql.startswith('UPDATE'))
                or (failing_step == 'prune' and 'NOT IN' in sql)
                or (failing_step == 'cleanup' and sql.startswith('DELETE') and 'scan_time = ?' in sql)):
            raise RuntimeError(f'{failing_step} failed')
        return execute(sql, args, **kwargs)

    monkeypatch.setattr(mod, 'hrana_execute', failing)
    if failing_step == 'promote':
        def query(*args, **kwargs):
            raise RuntimeError('promote failed')
        monkeypatch.setattr(mod, 'hrana_query', query)
    with pytest.raises(RuntimeError, match=failing_step):
        mod.run(SourceClient(_texts()), previous={}, now=_now())
    rows = conn.execute('SELECT scan_time,payload FROM scan_snapshots WHERE service=? ORDER BY scan_time', (mod.SERVICE,)).fetchall()
    assert ('old', '{"last_good":true}') in rows
    assert all(stamp == 'old' or __import__('json').loads(raw)['count'] == 3 for stamp, raw in rows)
    stages = conn.execute('SELECT count(*) FROM scan_snapshots WHERE service=?', (mod._SNAPSHOT_STAGING_SERVICE,)).fetchone()[0]
    assert stages == int(failing_step == 'cleanup')
    assert writer.record_service_health.call_args.args == (mod.SERVICE, 'error')
    assert mod._read_disk_previous()['count'] == 3


def test_missing_chunk_prevents_atomic_promotion(staged_snapshot_store, monkeypatch):
    mod, writer, conn, execute = staged_snapshot_store
    conn.execute('INSERT INTO scan_snapshots VALUES(?,?,?)', (mod.SERVICE, 'old', '{"last_good":true}'))
    monkeypatch.setattr(mod, 'hrana_execute', lambda sql, args=(), **kwargs:
                        None if sql.startswith('UPDATE') else execute(sql, args, **kwargs))
    with pytest.raises(ValueError, match='complete document'):
        mod.run(SourceClient(_texts()), previous={}, now=_now())
    assert conn.execute('SELECT service,scan_time,payload FROM scan_snapshots').fetchall() == [(mod.SERVICE, 'old', '{"last_good":true}')]


def test_persistence_budget_expiry_preserves_last_good_and_cleanup_error_does_not_hide_it(
        staged_snapshot_store, monkeypatch, capsys):
    mod, writer, conn, execute = staged_snapshot_store
    conn.execute('INSERT INTO scan_snapshots VALUES(?,?,?)', (mod.SERVICE, 'old', '{"last_good":true}'))
    ticks = iter([0, 181])
    monkeypatch.setattr(mod.time, 'monotonic', lambda: next(ticks))

    def fail_cleanup(*args, **kwargs):
        raise RuntimeError('cleanup failed')

    monkeypatch.setattr(mod, 'hrana_execute', fail_cleanup)
    with pytest.raises(TimeoutError, match='budget exhausted'):
        mod.run(SourceClient(_texts()), previous={}, now=_now())
    assert 'staging cleanup failed: RuntimeError' in capsys.readouterr().err
    assert conn.execute('SELECT service,scan_time,payload FROM scan_snapshots').fetchall() == [(mod.SERVICE, 'old', '{"last_good":true}')]


# ── intraday provisional row (5-minute market-hours timer) ─────────────
from unittest.mock import Mock
from fetch_vix_model_spread import _fetch_live as REAL_FETCH_LIVE


def _session_now(hour=19, minute=0):
    from datetime import datetime, timezone
    return datetime(2026, 10, 19, hour, minute, tzinfo=timezone.utc)  # Mon 15:00 ET at 19:00


def _live(day='2026-10-19', values=(18.0, 40.0, 12.0), source='cboe_delayed'):
    return {'date': day, 'values': dict(zip(('VIX', 'VIXEQ', 'COR1M'), values)),
            'source': source, 'quote_time': f'{day}T14:45:00'}


def _live_snapshot(writer):
    calls = [c for c in writer.upsert_scan_snapshot.call_args_list if c.args[0] == 'vix-model-spread:live']
    assert len(calls) == 1
    return calls[0].args[2]


def _unchanged():
    return SourceClient(dict.fromkeys(('VIX', 'VIXEQ', 'COR1M')))


def test_live_quote_appends_provisional_row_in_side_snapshot_only(orchestration, monkeypatch):
    mod, writer = orchestration
    previous = mod.run(SourceClient(_texts()), previous={}, now=_session_now(13))
    writer.reset_mock()
    monkeypatch.setattr(mod, '_fetch_live', lambda probe, now: _live())
    payload = mod.run(_unchanged(), previous=previous, now=_session_now())
    live = _live_snapshot(writer)
    assert live['row']['date'] == '2026-10-19'
    assert live['row']['model_vix'] == pytest.approx(math.sqrt(0.12) * 40.0)
    assert live['row']['spread'] == pytest.approx(18.0 - math.sqrt(0.12) * 40.0)
    assert live['source'] == 'cboe_delayed' and live['quote_time'] == '2026-10-19T14:45:00'
    # Official history and its multi-MB snapshot are untouched by a live tick.
    assert payload['data_date'] == '2026-10-16'
    assert all(r['date'] != '2026-10-19' for r in payload['series'])
    assert all(r['date'] != '2026-10-19' for rows in payload['source_series'].values() for r in rows)
    writer.upsert_vix_model_spread_rows.assert_not_called()
    assert [c.args[0] for c in writer.upsert_scan_snapshot.call_args_list] == ['vix-model-spread:live']
    writer.record_service_health.assert_called_once()


def test_live_tick_rewrites_full_snapshot_once_it_is_twelve_hours_old(orchestration, monkeypatch):
    mod, writer = orchestration
    previous = mod.run(SourceClient(_texts()), previous={}, now=_session_now(5))
    writer.reset_mock()
    monkeypatch.setattr(mod, '_fetch_live', lambda probe, now: _live())
    mod.run(_unchanged(), previous=previous, now=_session_now(18))
    services = sorted(c.args[0] for c in writer.upsert_scan_snapshot.call_args_list)
    assert services == ['vix-model-spread', 'vix-model-spread:live']


@pytest.mark.parametrize('live', [None, _live(day='2026-10-16'), _live(day='2026-10-20')])
def test_live_quote_not_after_official_or_in_the_future_is_dropped(orchestration, monkeypatch, live):
    mod, writer = orchestration
    monkeypatch.setattr(mod, '_fetch_live', lambda probe, now: live)
    payload = mod.run(SourceClient(_texts()), previous={}, now=_session_now())
    assert _live_snapshot(writer)['row'] is None
    assert payload['data_date'] == '2026-10-16'


def test_live_fetch_failure_never_fails_the_official_run(orchestration, monkeypatch):
    mod, writer = orchestration
    monkeypatch.setattr(mod, '_fetch_live', REAL_FETCH_LIVE)
    monkeypatch.setattr(mod, '_live_ib', Mock(side_effect=RuntimeError('pool down')))
    monkeypatch.setattr(mod, '_live_cboe', Mock(side_effect=RuntimeError('cdn down')))
    payload = mod.run(SourceClient(_texts()), previous={}, now=_session_now())
    assert _live_snapshot(writer)['row'] is None and payload['data_date'] == '2026-10-16'


def test_fetch_live_tries_ib_first_only_when_vixeq_is_covered(monkeypatch):
    import fetch_vix_model_spread as mod
    ib = Mock(return_value=_live(source='ib'))
    cboe = Mock(return_value=_live())
    monkeypatch.setattr(mod, '_live_ib', ib)
    monkeypatch.setattr(mod, '_live_cboe', cboe)
    assert mod._fetch_live({'status': 'available'}, _session_now())['source'] == 'ib'
    cboe.assert_not_called()
    ib.reset_mock()
    assert mod._fetch_live({'status': 'unavailable'}, _session_now())['source'] == 'cboe_delayed'
    ib.assert_not_called()
    ib.return_value = None
    assert mod._fetch_live({'status': 'available'}, _session_now())['source'] == 'cboe_delayed'


def _quote(price, when):
    response = Mock()
    response.json = Mock(return_value={'data': {'current_price': price, 'last_trade_time': when}})
    return response


def test_cboe_delayed_quotes_need_all_three_on_one_session(monkeypatch):
    import fetch_vix_model_spread as mod
    import requests
    quotes = {'VIX': _quote(17.51, '2026-11-11T15:39:31'), 'VIXEQ': _quote(41.87, '2026-11-11T15:39:32'),
              'COR1M': _quote(9.92, '2026-11-11T15:39:31')}
    get = Mock(side_effect=lambda url, **k: quotes[url.rsplit('_', 1)[1].removesuffix('.json')])
    monkeypatch.setattr(requests, 'get', get)
    assert mod._live_cboe() == {'date': '2026-11-11', 'values': {'VIX': 17.51, 'VIXEQ': 41.87, 'COR1M': 9.92},
                                'source': 'cboe_delayed', 'quote_time': '2026-11-11T15:39:32'}
    assert get.call_args_list[0].args[0] == 'https://cdn-api.cboe.com/api/global/delayed_quotes/quotes/_VIX.json'
    assert get.call_args_list[0].kwargs['headers'] == {'User-Agent': 'radon/2.0'}
    quotes['COR1M'] = _quote(9.92, '2026-11-10T16:14:00')
    assert mod._live_cboe() is None
    quotes['COR1M'] = _quote(float('nan'), '2026-11-11T15:39:31')
    assert mod._live_cboe() is None


def test_ib_live_bars_need_todays_bar_for_all_three(monkeypatch):
    import fetch_vix_model_spread as mod
    import requests
    closes = {'VIX': 17.5, 'VIXEQ': 41.8, 'COR1M': 9.9}

    def post(url, **kwargs):
        response = Mock()
        response.json = Mock(return_value={'bars': [{'date': '2026-10-16', 'close': 1.0},
                                                    {'date': '2026-10-19', 'close': closes[kwargs['json']['contract']['symbol']]}]})
        return response

    monkeypatch.setattr(requests, 'post', post)
    live = mod._live_ib(_session_now())
    assert live['source'] == 'ib' and live['date'] == '2026-10-19' and live['values'] == closes
    assert mod._live_ib(_session_now(3)) is None  # 23:00 ET Sunday: no bar dated today
