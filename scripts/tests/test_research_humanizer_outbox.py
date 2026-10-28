"""Unscored publication recovery preserves durable queue decisions."""
import json
import sqlite3

import pytest

from research.state import State


@pytest.fixture
def state(tmp_path):
    root = tmp_path / 'private'
    root.mkdir(mode=0o700)
    value = State(root / 'state.sqlite')
    with value.db:
        value.db.execute("""INSERT INTO work(key,file_id,rev,path,scope,folder_date,metadata,status,attempts,available_at,error,result,note)
            VALUES('work','id:report','rev','/report.pdf','scope','2026-10-19','{}','complete',4,9999999999,NULL,?,?)""",
            (json.dumps({'status': 'reviewed', 'items': 2}), 'operator correction'))
        value.db.execute("INSERT INTO cursors VALUES('scope','keep-cursor','2026-10-19')")
        value.db.execute("INSERT INTO ingestion(work_key,status,attempts,pdf) VALUES('work','ready',3,'/report.pdf')")
        for publication_id, status in [('first', 'pending'), ('second', 'pending'), ('prior', 'published')]:
            value.db.execute('INSERT INTO outbox VALUES(?,?,?,?)', (publication_id, 'work', '{}', status))
    yield value
    value.close()


def test_requeue_unscored_is_atomic_idempotent_and_preserves_history(state):
    before = dict(state.db.execute("SELECT * FROM work WHERE key='work'").fetchone())
    assert state.requeue_unscored('first', 'work') is True
    after = dict(state.db.execute("SELECT * FROM work WHERE key='work'").fetchone())
    assert after.pop('status') == 'pending'
    assert after.pop('error') == 'humanizer_recheck_required'
    assert after.pop('available_at') < 9999999999
    for field in ('status', 'error', 'available_at'):
        before.pop(field)
    assert after == before
    assert dict(state.db.execute('SELECT id,status FROM outbox')) == {'first': 'cancelled', 'second': 'cancelled', 'prior': 'published'}
    assert state.cursor('scope') == 'keep-cursor'
    assert tuple(state.db.execute('SELECT status,attempts,pdf FROM ingestion').fetchone()) == ('ready', 3, '/report.pdf')
    assert state.requeue_unscored('first', 'work') is False


@pytest.mark.parametrize('status', ['pending', 'processing', 'published', 'deleted', 'superseded'])
def test_requeue_unscored_does_not_reset_other_work_states(state, status):
    with state.db:
        state.db.execute('UPDATE work SET status=?', (status,))
    assert state.requeue_unscored('first', 'work') is False
    assert state.db.execute('SELECT status FROM work').fetchone()[0] == status
    assert len(state.outbox()) == 2


@pytest.mark.parametrize('publication_id,key', [('missing', 'work'), ('first', 'other'), ('prior', 'work')])
def test_requeue_unscored_requires_exact_pending_publication(state, publication_id, key):
    assert state.requeue_unscored(publication_id, key) is False
    assert state.db.execute('SELECT status FROM work').fetchone()[0] == 'complete'
    assert len(state.outbox()) == 2


def test_requeue_unscored_preserves_held_outcome(state):
    with state.db:
        state.db.execute('UPDATE work SET result=?', (json.dumps({'status': 'held'}),))
    assert state.requeue_unscored('first', 'work') is False
    assert len(state.outbox()) == 2


def test_requeue_unscored_rolls_back_cancellation_when_work_update_fails(state):
    state.db.execute("CREATE TRIGGER reject_requeue BEFORE UPDATE ON work BEGIN SELECT RAISE(ABORT,'test failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match='test failure'):
        state.requeue_unscored('first', 'work')
    assert len(state.outbox()) == 2
    assert state.db.execute('SELECT status FROM work').fetchone()[0] == 'complete'


def test_flush_outbox_requeues_unscored_batch_and_continues_other_work(state):
    from types import SimpleNamespace
    from research.summary_editor import HumanizerCertificateError
    from research.worker import flush_outbox

    with state.db:
        state.db.execute("""INSERT INTO work(key,file_id,rev,path,scope,metadata,status,result)
            VALUES('next','id:next','rev','/next.pdf','scope','{}','complete',?)""",
            (json.dumps({'status': 'reviewed', 'items': 1}),))
        state.db.execute("INSERT INTO outbox VALUES('valid','next',?,'pending')", (json.dumps({'id': 'valid'}),))
    attempted = []

    def publish(payload):
        attempted.append(payload)
        if payload.get('id') != 'valid':
            raise HumanizerCertificateError('PDF summary is missing its Humanizer certificate')

    assert flush_outbox(state, SimpleNamespace(publish=publish)) == 1
    assert attempted == [{}, {'id': 'valid'}], 'cancelled sibling must not be dispatched from snapshot'
    assert state.db.execute("SELECT status FROM work WHERE key='work'").fetchone()[0] == 'pending'
    assert state.db.execute("SELECT status FROM work WHERE key='next'").fetchone()[0] == 'published'
    assert state.db.execute("SELECT status FROM outbox WHERE id='prior'").fetchone()[0] == 'published'
    assert state.db.execute("SELECT attempts,note FROM work WHERE key='work'").fetchone()[:] == (4, 'operator correction')


def test_reviewed_certificate_revives_cancelled_stable_id_for_publication(state):
    from types import SimpleNamespace
    from research import humanizer
    from research.worker import flush_outbox

    assert state.requeue_unscored('first', 'work') is True
    assert state.claim('work') is True
    value = {'id': 'first', 'title': 'Foreign equity buying rose',
             'content': 'Foreign investors bought equities.', 'source': {}}
    reviewed = humanizer.review_post(value, ask=lambda _prompt: {'findings': [], 'qualitative': []},
                                    verify=lambda _post: True, record=lambda _audit: None)
    assert reviewed is not None
    state.complete('work', {'status': 'reviewed', 'items': 1}, publications=[reviewed])
    assert state.outbox_current('first', 'work') is True
    published = []

    def publish(payload):
        humanizer.validate_certificate(payload)
        published.append(payload['id'])

    assert flush_outbox(state, SimpleNamespace(publish=publish)) == 1
    assert published == ['first']
    assert state.db.execute("SELECT status FROM work WHERE key='work'").fetchone()[0] == 'published'
    assert dict(state.db.execute('SELECT id,status FROM outbox')) == {
        'first': 'published', 'second': 'cancelled', 'prior': 'published'}


@pytest.mark.parametrize('outcome,reason', [
    ('dropped','EQUITY_RESEARCH_NOT_IN_BOOK'), ('held','EQUITY_RESEARCH_BOOK_UNAVAILABLE'),
])
def test_block_publication_is_atomic_idempotent_and_preserves_history(state,outcome,reason):
    before=dict(state.db.execute("SELECT * FROM work WHERE key='work'").fetchone())
    payloads=list(state.db.execute('SELECT id,payload FROM outbox'))
    assert state.block_publication('first','work',outcome,reason)=={
        'work_key':'work','outcome':outcome,'reason_code':reason,'published':1}
    after=dict(state.db.execute("SELECT * FROM work WHERE key='work'").fetchone())
    assert after['status']=='complete' and after['error']==reason
    result=json.loads(after['result'])
    assert result['status']==outcome and result['prior_result']==json.loads(before['result'])
    assert result['publication_id']=='first'
    assert {k:v for k,v in after.items() if k not in ('error','result')}=={k:v for k,v in before.items() if k not in ('error','result')}
    assert list(state.db.execute('SELECT id,payload FROM outbox'))==payloads
    assert dict(state.db.execute('SELECT id,status FROM outbox'))=={'first':'cancelled','second':'cancelled','prior':'published'}
    assert state.block_publication('first','work',outcome,reason) is False
    assert state.pending()==[]


def test_block_publication_rollback_preserves_pending_payloads(state):
    state.db.execute("CREATE TRIGGER reject_block BEFORE UPDATE ON work BEGIN SELECT RAISE(ABORT,'test failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        state.block_publication('first','work','held','EQUITY_RESEARCH_BOOK_UNAVAILABLE')
    assert len(state.outbox())==2
    assert json.loads(state.db.execute('SELECT result FROM work').fetchone()[0])['status']=='reviewed'


@pytest.mark.parametrize('publication_id,key', [('missing','work'),('first','other'),('prior','work')])
def test_block_publication_requires_exact_pending_identity(state,publication_id,key):
    assert state.block_publication(publication_id,key,'held','EQUITY_RESEARCH_BOOK_UNAVAILABLE') is False
    assert len(state.outbox())==2


def test_flush_outbox_blocks_batch_and_continues_unrelated_work(state):
    from types import SimpleNamespace
    from research.publish import PublicationAdmissionError
    from research.worker import flush_outbox
    with state.db:
        state.db.execute("INSERT INTO work(key,file_id,rev,path,scope,metadata,status,result) VALUES('next','id:next','rev','/next.pdf','scope','{}','complete','{}')")
        state.db.execute("INSERT INTO outbox VALUES('valid','next',?, 'pending')",(json.dumps({'id':'valid'}),))
    attempted=[]
    def publish(payload):
        attempted.append(payload)
        if payload.get('id')!='valid':
            raise PublicationAdmissionError('held','EQUITY_RESEARCH_BOOK_UNAVAILABLE',('HOOD',))
    assert flush_outbox(state,SimpleNamespace(publish=publish))==1
    assert attempted==[{}, {'id':'valid'}]
    assert dict(state.db.execute('SELECT id,status FROM outbox'))=={'first':'cancelled','second':'cancelled','prior':'published','valid':'published'}


def test_operator_re_review_revives_identical_policy_cancelled_payload(state):
    value={'id':'first'}
    with state.db:
        state.db.execute("UPDATE outbox SET payload=? WHERE id='first'",(json.dumps(value,sort_keys=True),))
    assert state.block_publication('first','work','dropped','EQUITY_RESEARCH_NOT_IN_BOOK')
    assert state.requeue_with_note('work','Issuer was added to the watchlist; re-review')
    assert state.claim('work')
    state.complete('work',{'status':'reviewed','items':1},publications=[value])
    assert state.outbox_current('first','work')


@pytest.mark.parametrize('status',['pending','processing','published','superseded','deleted'])
def test_admission_block_cannot_change_stale_or_noncomplete_work(state,status):
    with state.db:
        state.db.execute('UPDATE work SET status=?',(status,))
    assert state.block_publication('first','work','held','EQUITY_RESEARCH_BOOK_UNAVAILABLE') is False
    assert state.db.execute('SELECT status FROM work').fetchone()[0]==status
    assert len(state.outbox())==2


def test_outcome_mirror_failure_cannot_reopen_cancelled_publications(state):
    from types import SimpleNamespace
    from research.publish import PublicationAdmissionError
    from research.worker import flush_outbox
    calls=[]
    def blocked(payload):
        raise PublicationAdmissionError('held','EQUITY_RESEARCH_BOOK_UNAVAILABLE',('HOOD',))
    def mirror(*args):
        calls.append(args)
        raise ConnectionError('private transport detail must not be recorded')
    publisher=SimpleNamespace(publish=blocked,record_publication_block=mirror)
    assert flush_outbox(state,publisher)==0
    assert flush_outbox(state,publisher)==0
    assert len(calls)==1
    result=json.loads(state.db.execute('SELECT result FROM work').fetchone()[0])
    assert result['publication_mirror_error']=='ConnectionError'
    assert 'private transport' not in json.dumps(result)
    assert state.outbox()==[]


@pytest.mark.parametrize('outcome,reason',[('published','EQUITY_RESEARCH_NOT_IN_BOOK'),('held','untrusted detail')])
def test_publication_block_rejects_invalid_decisions_without_mutation(state,outcome,reason):
    with pytest.raises(ValueError):
        state.block_publication('first','work',outcome,reason)
    assert len(state.outbox())==2


def test_publication_block_preserves_even_malformed_prior_audit(state):
    with state.db:
        state.db.execute("UPDATE work SET result='legacy non-json audit'")
    state.block_publication('first','work','held','EQUITY_RESEARCH_BOOK_UNAVAILABLE')
    assert json.loads(state.db.execute('SELECT result FROM work').fetchone()[0])['prior_result']=='legacy non-json audit'


def test_missing_mirror_work_does_not_create_state(state):
    state.publication_mirror_failed('missing',ConnectionError('not recorded'))
    assert state.db.execute('SELECT count(*) FROM work').fetchone()[0]==1


@pytest.mark.parametrize('prior_status,new_file,new_rev,allowed', [
    ('published','id:report','rev2',True), ('superseded','id:report','rev2',True),
    ('complete','id:report','rev2',False), ('published','id:other','rev2',False),
])
def test_stable_publication_id_replacement_requires_new_revision_of_same_finished_source(state,prior_status,new_file,new_rev,allowed):
    with state.db:
        state.db.execute('UPDATE work SET status=? WHERE key=?',(prior_status,'work'))
        state.db.execute("INSERT INTO work(key,file_id,rev,path,scope,metadata,status) VALUES('next',?,?,'/next.pdf','scope','{}','processing')",(new_file,new_rev))
    payload={'id':'first','content':'Revised source copy'}
    if allowed:
        state.complete('next',{'status':'reviewed'},publications=[payload])
        assert state.outbox_current('first','next')
        assert json.loads(state.db.execute("SELECT payload FROM outbox WHERE id='first'").fetchone()[0])==payload
    else:
        with pytest.raises(ValueError,match='collision'):
            state.complete('next',{'status':'reviewed'},publications=[payload])
        assert state.db.execute("SELECT work_key,payload FROM outbox WHERE id='first'").fetchone()[:]==('work','{}')
        assert state.db.execute("SELECT status FROM work WHERE key='next'").fetchone()[0]=='processing'
