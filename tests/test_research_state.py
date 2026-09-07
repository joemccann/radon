from datetime import datetime, timezone
import sqlite3
import pytest
from scripts.research.state import State, date_scopes

SCOPE='2026/September/Sep 07'
ROOT='/joe mccann/current/' + SCOPE.lower()

def item(rev='r1', path=ROOT+'/a.pdf', id='a'):
    return {'.tag':'file','path_lower':path,'path_display':path,'id':id,'rev':rev,'content_hash':'hash','name':'a.pdf','size':3}

def page(*entries, cursor='c1'):
    return {'entries':list(entries),'cursor':cursor,'has_more':False}

@pytest.fixture
def state(tmp_path):
    s=State(tmp_path/'state.sqlite');yield s;s.close()

def test_dates_midnight_and_year_rollover():
    assert date_scopes(datetime(2026,1,1,4,59,tzinfo=timezone.utc)) == [('2025/december/dec 30','2025-12-30'),('2025/december/dec 31','2025-12-31')]
    assert date_scopes(datetime(2026,1,1,5,tzinfo=timezone.utc))[-1] == ('2026/january/jan 01','2026-01-01')
    with pytest.raises(ValueError):date_scopes(datetime(2026,1,1))

def test_ingest_exclusion_replay_and_atomic_rollback(state):
    assert state.ingest_page(SCOPE,page(item(),item(id='t',path=ROOT+'/The Market Ear/x.pdf'),item(id='x',path=ROOT+'/x.txt')),'2026-09-07')==1
    assert state.ingest_page(SCOPE,page(item()))==0
    with pytest.raises(ValueError):state.ingest_page(SCOPE,page(item('r2'),item(id='bad',path='/other/a.pdf'),cursor='bad'))
    assert state.cursor(SCOPE)=='c1'
    assert len(state.pending())==1
    assert state.pending()[0]['metadata']['rev']=='r1'
    assert state.pending()[0]['folder_date']=='2026-09-07'

def test_restart_claim_retry_and_outbox(tmp_path):
    path=tmp_path/'s.sqlite';s=State(path)
    s.ingest_page(SCOPE,page(item()));key=s.pending()[0]['key']
    assert s.claim(key) and not s.claim(key)
    s.close();s=State(path)
    assert s.recover()==1
    assert s.claim(key)
    s.retry(key,RuntimeError('SECRET'),delay=0)
    assert s.pending()[0]['error']=='RuntimeError'
    assert s.claim(key)
    s.complete(key,{'verdict':'publish'},[{'id':'stable','title':'claim'}])
    s.close();s=State(path)
    assert s.outbox()[0]['id']=='stable'
    s.published('stable');s.published('stable')
    assert not s.outbox() and not s.pending()
    s.close()

def test_revision_supersedes_only_unpublished_and_replay_cannot_revert(state):
    state.ingest_page(SCOPE,page(item()));key=state.pending()[0]['key'];state.claim(key)
    state.complete(key,publications=[{'id':'old'}])
    state.ingest_page(SCOPE,page(item('r2'),cursor='c2'))
    assert not state.outbox()
    assert [r['rev'] for r in state.pending()]==['r2']
    state.ingest_page(SCOPE,page(item(),cursor='c3'))
    assert [r['rev'] for r in state.pending()]==['r2']
    state.reset_cursor(SCOPE);assert state.cursor(SCOPE) is None
    state.ingest_page(SCOPE,page(item('r2')))
    assert len(state.pending())==1

def test_folder_deletion_cancels_outbox(state):
    state.ingest_page(SCOPE,page(item(path=ROOT+'/bank/a.pdf')))
    key=state.pending()[0]['key'];state.claim(key);state.complete(key,publications=[{'id':'draft'}])
    state.ingest_page(SCOPE,page({'.tag':'deleted','path_lower':ROOT+'/bank'}))
    assert not state.outbox()

def test_completion_rollback_on_duplicate_publication_id(state):
    state.ingest_page(SCOPE,page(item(),item(id='b',path=ROOT+'/b.pdf')))
    a,b=state.pending();state.claim(a['key']);state.complete(a['key'],publications=[{'id':'same'}])
    state.claim(b['key'])
    with pytest.raises(ValueError):state.complete(b['key'],publications=[{'id':'other'},{'id':'same'}])
    assert [x['id'] for x in state.outbox()]==['same']
    assert state.recover()==1

def test_invalid_scope_entry_and_unclaimed_completion(state,tmp_path):
    for scope in ['../other','/absolute','a//b','id:bad']:
        with pytest.raises(ValueError):state.ingest_page(scope,page(item()))
    with pytest.raises(ValueError):state.ingest_page(SCOPE,{'cursor':''})
    bad=item();bad.pop('rev')
    with pytest.raises(ValueError):state.ingest_page(SCOPE,page(bad))
    with pytest.raises(ValueError):state.complete('unknown')
    assert not state.scopes()
    state.ingest_page(SCOPE,page(item()))
    assert state.scopes()==[SCOPE.lower()]
    public=tmp_path/'public';public.mkdir(mode=0o755)
    with pytest.raises(ValueError):State(public/'state.sqlite')

@pytest.mark.parametrize('published',[False,True])
def test_same_claim_updated_revision_replaces_outbox_preserves_history(state,published):
    state.ingest_page(SCOPE,page(item()),'2026-09-07')
    first=state.pending()[0]['key'];state.claim(first);state.complete(first,publications=[{'id':'claim','title':'original'}])
    if published:state.published('claim')
    state.reset_cursor(SCOPE)
    assert state.scopes()==[SCOPE.lower()] and state.cursor(SCOPE) is None
    state.ingest_page(SCOPE,page(item('r2')))
    second=state.pending()[0]
    assert second['folder_date']=='2026-09-07'
    state.claim(second['key']);state.complete(second['key'],publications=[{'id':'claim','title':'revised'}])
    assert state.outbox()==[{'id':'claim','work_key':second['key'],'payload':{'id':'claim','title':'revised'}}]
    assert state.db.execute('SELECT status FROM work WHERE key=?',(first,)).fetchone()[0]==('published' if published else 'superseded')
    state.published('claim')
    assert not state.outbox()


def test_market_ear_filter_does_not_drop_market_earnings(state):
    assert state.ingest_page(SCOPE,page(item(path=ROOT+'/market earnings.pdf'))) == 1

def test_seeded_lowercase_and_display_scope_share_one_cursor(state):
    state.ingest_page(SCOPE.lower(),page(item()),'2026-09-07')
    assert state.cursor(SCOPE)=='c1'
    state.ingest_page(SCOPE,page(item('r2'),cursor='c2'))
    assert state.scopes()==[SCOPE.lower()]
    assert state.pending()[0]['folder_date']=='2026-09-07'
    assert date_scopes(datetime(2026,9,7,12,tzinfo=timezone.utc))[-1][0]==SCOPE.lower()


def test_migrate_case_variant_cursors_relists_without_losing_dates(tmp_path):
    path=tmp_path/'case.sqlite';s=State(path)
    s.db.execute('INSERT INTO cursors VALUES(?,?,?)',(SCOPE,'title-cursor','2026-09-07'))
    s.db.execute('INSERT INTO cursors VALUES(?,?,?)',(SCOPE.lower(),'lower-cursor',None))
    s.db.commit();s.close();s=State(path)
    assert s.scopes()==[SCOPE.lower()]
    assert s.cursor(SCOPE) is None
    s.ingest_page(SCOPE,page(item()))
    assert s.pending()[0]['folder_date']=='2026-09-07'
    s.close()
