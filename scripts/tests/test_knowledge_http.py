"""The CLI must not enter the native libsql path that stalled a live catch-up."""
from knowledge import ingest


def test_cli_fresh_connection_never_opens_native_libsql(monkeypatch):
    import db.client
    monkeypatch.setenv("TURSO_DB_URL", "libsql://knowledge-test.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "fake-test-token")
    def native_is_forbidden():
        raise AssertionError("knowledge CLI entered unbounded native libsql")
    monkeypatch.setattr(db.client, "get_db", native_is_forbidden)
    first = ingest._fresh_db()
    second = ingest._fresh_db()
    assert first is not second

import io
import json
import sqlite3

import pytest

from knowledge import http_db


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class Server:
    """SQLite-backed fake Hrana stream: close rolls back uncommitted changes."""
    def __init__(self):
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        self.db.executescript("CREATE TABLE docs(id INTEGER PRIMARY KEY, content TEXT); CREATE VIRTUAL TABLE search USING fts5(content);")
        self.baton = None
        self.calls = []
        self.responses = []
        self.serial = 0

    def open(self, request, timeout):
        payload = json.loads(request.data)
        self.calls.append((request.full_url, payload, timeout))
        if self.responses:
            result = self.responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return Response(json.dumps(result).encode())
        assert payload['baton'] == self.baton
        results = []
        for item in payload['requests']:
            if item['type'] == 'close':
                self.db.rollback()
                self.baton = None
                results.append({'type': 'ok', 'response': {'type': 'close'}})
                continue
            self.serial += 1
            self.baton = f'baton-{self.serial}'
            stmt = item['stmt']
            try:
                cursor = self.db.execute(stmt['sql'], [http_db._cell(a) for a in stmt['args']])
                rows = [[{'type': 'null'} if v is None else {'type': 'integer', 'value': str(v)} if isinstance(v, int) else {'type': 'text', 'value': v} for v in row] for row in cursor.fetchall()]
                results.append({'type': 'ok', 'response': {'type': 'execute', 'result': {'rows': rows, 'last_insert_rowid': str(cursor.lastrowid)}}})
            except sqlite3.Error as exc:
                results.append({'type': 'error', 'error': {'code': exc.sqlite_errorname, 'message': str(exc)}})
        return Response(json.dumps({'baton': self.baton, 'base_url': None, 'results': results}).encode())


@pytest.fixture
def connection(monkeypatch):
    monkeypatch.setenv('TURSO_DB_URL', 'libsql://knowledge-test.turso.io')
    monkeypatch.setenv('TURSO_AUTH_TOKEN', 'fake-test-token')
    monkeypatch.setattr(http_db, '_refuse_pytest_pollution', lambda: None)
    server = Server()
    monkeypatch.setattr(http_db.urllib.request, 'build_opener', lambda *a: server)
    return http_db.Connection(), server


def test_transaction_keeps_rotating_stream_and_commits_document_with_fts(connection):
    db, server = connection
    db.execute('BEGIN IMMEDIATE')
    row_id = db.execute('INSERT INTO docs(content) VALUES (?)', ('complete',)).lastrowid
    assert isinstance(row_id, int)
    db.execute('INSERT INTO search(rowid, content) VALUES (?, ?)', (row_id, 'complete'))
    db.commit()
    assert db.execute('SELECT id, content FROM docs').fetchall() == [(1, 'complete')]
    assert db.execute("SELECT rowid FROM search WHERE search MATCH 'complete'").fetchone() == (1,)
    batons = [call[1]['baton'] for call in server.calls]
    assert batons[:4] == [None, 'baton-1', 'baton-2', 'baton-3']
    assert batons[4:] == [None, None]
    assert all(call[2] == http_db.REQUEST_TIMEOUT for call in server.calls)


def test_failed_fts_write_discards_rotated_error_stream_and_rolls_back_document(connection):
    db, server = connection
    db.execute('BEGIN IMMEDIATE')
    db.execute('INSERT INTO docs(content) VALUES (?)', ('uncommitted',))
    with pytest.raises(http_db.HranaHttpError, match='SQLITE_ERROR'):
        db.execute('INSERT INTO absent_fts VALUES (1)')
    assert server.calls[-1][1] == {'baton': 'baton-3', 'requests': [{'type': 'close'}]}
    assert server.db.execute('SELECT count(*) FROM docs').fetchone() == (0,)
    with pytest.raises(http_db.TransportError, match='fresh connection'):
        db.execute('SELECT 1')


def result(baton='next', base_url=None):
    return {'baton': baton, 'base_url': base_url, 'results': [{'type': 'ok', 'response': {'type': 'execute', 'result': {'rows': []}}}]}


def test_routing_follows_trusted_base_and_null_retains_it(connection):
    db, server = connection
    server.responses = [result(base_url='https://region.turso.io'), result(), result()]
    db.execute('BEGIN IMMEDIATE')
    db.execute('SELECT 1')
    db.execute('SELECT 2')
    assert [c[0] for c in server.calls] == ['https://knowledge-test.turso.io/v2/pipeline', 'https://region.turso.io/v2/pipeline', 'https://region.turso.io/v2/pipeline']


@pytest.mark.parametrize('baton', [None, '', 42])
def test_missing_transaction_stream_fails_closed(connection, baton):
    db, server = connection
    server.responses = [result(baton=baton)]
    with pytest.raises(http_db.HranaHttpError):
        db.execute('BEGIN IMMEDIATE')
    with pytest.raises(http_db.TransportError):
        db.execute('INSERT INTO docs VALUES (1, 2)')


@pytest.mark.parametrize('route', ['http://region.turso.io', 'https://evil.test', 'https://turso.io.evil.test', 'https://user:pass@region.turso.io', 'https://region.turso.io:444'])
def test_untrusted_route_never_receives_credentials(connection, route):
    db, server = connection
    server.responses = [result(base_url=route), {'baton': None, 'results': [{'type': 'ok', 'response': {'type': 'close'}}]}]
    with pytest.raises(http_db.HranaHttpError, match='unsafe'):
        db.execute('BEGIN IMMEDIATE')
    assert all(c[0] == 'https://knowledge-test.turso.io/v2/pipeline' for c in server.calls)


def test_timeout_poisons_transaction_and_preserves_failure_when_cleanup_fails(connection):
    db, server = connection
    db.execute('BEGIN IMMEDIATE')
    server.responses = [TimeoutError('ambiguous COMMIT'), TimeoutError('cleanup failed')]
    with pytest.raises(http_db.TransportError, match='ambiguous COMMIT'):
        db.commit()
    db.rollback()
    with pytest.raises(http_db.TransportError, match='fresh connection'):
        db.execute('SELECT 1')
    assert len(server.calls) == 3


def test_response_size_is_bounded_and_rejects_truncation(connection, monkeypatch):
    db, server = connection
    monkeypatch.setattr(http_db, 'MAX_RESPONSE_BYTES', 20)
    with pytest.raises(http_db.HranaHttpError, match='bounded size'):
        db.execute('SELECT 1')


def test_cursor_decodes_types_without_truthy_zero():
    cursor = http_db._Cursor({'last_insert_rowid': '9223372036854775806', 'rows': [[{'type': 'integer', 'value': '0'}, {'type': 'float', 'value': 1.5}, {'type': 'null'}, {'type': 'blob', 'base64': 'YWJj'}, {'type': 'text', 'value': 'x'}]]})
    assert cursor.lastrowid == 9223372036854775806
    assert cursor.fetchone() == (0, 1.5, None, b'abc', 'x')
    assert cursor.fetchone() is None
    assert cursor.fetchall() == []


@pytest.mark.parametrize('code, transient', [('SQLITE_ERROR', False), ('SQLITE_BUSY', True)])
def test_failed_begin_with_closed_stream_preserves_sql_error_classification(connection, code, transient):
    db, server = connection
    server.responses = [{'baton': None, 'results': [{'type': 'error', 'error': {'code': code, 'message': 'failed begin'}}]}]
    with pytest.raises(http_db.HranaHttpError, match=code) as caught:
        db.execute('BEGIN IMMEDIATE')
    assert ingest._is_transient_db_error(caught.value) is transient
    assert len(server.calls) == 1


def test_missing_insert_rowid_aborts_before_fts_or_commit(connection):
    db, server = connection
    db.execute('BEGIN IMMEDIATE')
    server.responses = [result('rotated'), {'baton': None, 'results': [{'type': 'ok', 'response': {'type': 'close'}}]}]
    with pytest.raises(http_db.TransportError, match='last_insert_rowid'):
        db.execute('INSERT INTO docs(content) VALUES (?)', ('x',))
    assert server.calls[-1][1]['requests'] == [{'type': 'close'}]
    with pytest.raises(http_db.TransportError):
        db.commit()


def test_response_deadline_rejects_slow_stream(connection, monkeypatch):
    db, server = connection
    ticks = iter([0, 5])
    monkeypatch.setattr(http_db.time, 'monotonic', lambda: next(ticks))
    with pytest.raises(http_db.TransportError, match='deadline'):
        db.execute('SELECT 1')


def test_ambiguous_commit_replays_whole_prepared_batch_idempotently(atomic_server, monkeypatch):
    from knowledge.schema import KnowledgeDoc
    from knowledge.store import upsert_documents

    server, _ = atomic_server
    original_open = server.open
    lose_receipt = True

    def ambiguous_once(request, timeout):
        nonlocal lose_receipt
        body = json.loads(request.data)
        response = original_open(request, timeout)
        if lose_receipt and body['requests'][0]['type'] == 'batch':
            lose_receipt = False
            response.close()
            raise TimeoutError('COMMIT applied but receipt lost')
        return response

    monkeypatch.setattr(server, 'open', ambiguous_once)
    monkeypatch.setattr(ingest, '_SOURCE_RETRY_BACKOFF_SECS', 0)
    handles = []

    def fresh():
        handle = http_db.Connection()
        handles.append(handle)
        return handle

    docs = [KnowledgeDoc(source='docs', scope='ops', doc_key='one', content='durable')]
    counts = ingest._persist_prepared(fresh, lambda db: upsert_documents(db, docs), source='docs')
    assert len(handles) == 2 and handles[0] is not handles[1]
    assert counts == {'inserted': 0, 'updated': 0, 'skipped': 1, 'pruned': 0}
    assert server.db.execute('SELECT id, content FROM knowledge').fetchall() == [(1, 'durable')]
    assert server.db.execute("SELECT rowid FROM knowledge_fts WHERE knowledge_fts MATCH 'durable'").fetchall() == [(1,)]


@pytest.mark.parametrize('body', [
    [], {}, {'baton': 'x'}, {'baton': 'x', 'results': []},
    {'baton': 'x', 'results': [None]},
    {'baton': 'x', 'results': [{'type': 'ok', 'response': {'type': 'wrong'}}]},
    {'baton': 'x', 'results': [{'type': 'ok', 'response': {'type': 'execute', 'result': {}}}]},
    {'baton': 'x', 'results': [{'type': 'error', 'error': 'invalid error shape'}]},
])
def test_malformed_transaction_receipts_fail_closed(connection, body):
    db, server = connection
    server.responses = [body]
    with pytest.raises(http_db.HranaHttpError):
        db.execute('BEGIN IMMEDIATE')
    with pytest.raises(http_db.TransportError):
        db.execute('INSERT INTO docs VALUES (1, 2)')


@pytest.mark.parametrize('body', [
    {'baton': 'still-open', 'results': [{'type': 'ok'}, {'type': 'ok'}]},
    {'baton': None, 'results': [result()['results'][0], {'type': 'error'}]},
])
def test_unconfirmed_close_is_not_success(connection, body):
    db, server = connection
    server.responses = [body]
    with pytest.raises(http_db.HranaHttpError):
        db.execute('SELECT 1')
    with pytest.raises(http_db.TransportError):
        db.execute('SELECT 2')


def test_explicit_rollback_and_close_preserve_committed_rows(connection):
    db, server = connection
    with pytest.raises(http_db.HranaHttpError, match='no transaction'):
        db.commit()
    db.execute('BEGIN IMMEDIATE')
    with pytest.raises(http_db.HranaHttpError, match='already open'):
        db.execute('BEGIN IMMEDIATE')
    db.execute('INSERT INTO docs(content) VALUES (?)', ('rolled back',))
    db.rollback()
    assert db.execute('SELECT count(*) FROM docs').fetchone() == (0,)
    db.close()
    with pytest.raises(http_db.TransportError):
        db.execute('SELECT 1')


def test_configuration_fails_before_network_without_credentials(monkeypatch):
    monkeypatch.setattr(http_db, 'read_env', lambda: ('', ''))
    with pytest.raises(http_db.HranaHttpError, match='not configured'):
        http_db.Connection()


def test_unknown_cell_type_is_rejected():
    with pytest.raises(http_db.HranaHttpError, match='value type'):
        http_db._Cursor({'rows': [[{'type': 'unknown'}]]})


def test_http_redirect_handler_refuses_credential_forwarding():
    assert http_db._NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.test') is None


class AtomicServer(Server):
    """Execute the complete server queue before delivering (or losing) a receipt."""
    def __init__(self, path):
        super().__init__()
        self.db.close()
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.create_function('vector32', 1, lambda value: value)
        self.db.executescript('''
            CREATE TABLE knowledge(id INTEGER PRIMARY KEY, source TEXT, scope TEXT,
              doc_key TEXT, chunk_ix INTEGER, title TEXT, summary TEXT, content TEXT,
              metadata TEXT, embedding BLOB, content_hash TEXT, created_at TEXT,
              last_activity_at TEXT, UNIQUE(source, doc_key, chunk_ix));
            CREATE VIRTUAL TABLE knowledge_fts USING fts5(title, summary, content);
        ''')
        self.executed_sql = []
        self.fail_sql = None
        self.close_count = 0

    def open(self, request, timeout):
        payload = json.loads(request.data)
        if payload['requests'][0]['type'] != 'batch':
            return super().open(request, timeout)
        self.calls.append((request.full_url, payload, timeout))
        assert payload['baton'] is None
        replies = []
        for item in payload['requests']:
            if item['type'] == 'close':
                self.db.rollback()
                self.baton = None
                self.close_count += 1
                replies.append({'type': 'ok', 'response': {'type': 'close'}})
                continue
            results, errors = [], []
            def allowed(condition):
                if condition is None:
                    return True
                if condition['type'] == 'ok':
                    return results[condition['step']] is not None
                if condition['type'] == 'not':
                    return not allowed(condition['cond'])
                raise AssertionError(condition)
            for step in item['batch']['steps']:
                if not allowed(step.get('condition')):
                    results.append(None)
                    errors.append(None)
                    continue
                stmt = step['stmt']
                self.executed_sql.append(stmt['sql'])
                if self.fail_sql and self.fail_sql in stmt['sql']:
                    results.append(None)
                    errors.append({'code': 'SQLITE_ERROR', 'message': 'injected middle SQL failure'})
                    continue
                try:
                    cursor = self.db.execute(stmt['sql'], [http_db._cell(a) for a in stmt.get('args', [])])
                    rows = [[http_db._encode_arg(value) for value in row] for row in cursor.fetchall()]
                    results.append({'rows': rows, 'last_insert_rowid': str(cursor.lastrowid) if cursor.lastrowid is not None else None, 'affected_row_count': max(0, cursor.rowcount)})
                    errors.append(None)
                except sqlite3.Error as exc:
                    results.append(None)
                    errors.append({'code': exc.sqlite_errorname, 'message': str(exc)})
            replies.append({'type': 'ok', 'response': {'type': 'batch', 'result': {'step_results': results, 'step_errors': errors}}})
        return Response(json.dumps({'baton': None, 'base_url': None, 'results': replies}).encode())


@pytest.fixture
def atomic_server(monkeypatch, tmp_path):
    monkeypatch.setenv('TURSO_DB_URL', 'libsql://knowledge-test.turso.io')
    monkeypatch.setenv('TURSO_AUTH_TOKEN', 'fake-test-token')
    monkeypatch.setattr(http_db, '_refuse_pytest_pollution', lambda: None)
    path = tmp_path / 'knowledge.sqlite'
    server = AtomicServer(path)
    monkeypatch.setattr(http_db.urllib.request, 'build_opener', lambda *a: server)
    return server, path


def _raw_doc(key='one', **kwargs):
    from knowledge.schema import KnowledgeDoc
    values = dict(source='docs', scope='ops', doc_key=key, content='durable')
    values.update(kwargs)
    return KnowledgeDoc(**values)


def test_lost_store_receipt_cannot_leave_a_reserved_writer(atomic_server, monkeypatch):
    from knowledge.store import upsert_documents
    server, path = atomic_server
    original_open = server.open
    def drop_receipt(request, timeout):
        response = original_open(request, timeout)
        response.close()
        raise TimeoutError('server executed request; receipt lost')
    monkeypatch.setattr(server, 'open', drop_receipt)
    with pytest.raises(http_db.TransportError):
        upsert_documents(http_db.Connection(), [_raw_doc()])
    assert not server.db.in_transaction, 'lost receipt retained the shared writer lock'
    competitor = sqlite3.connect(path, isolation_level=None, timeout=0)
    competitor.execute('BEGIN IMMEDIATE')
    competitor.rollback()
    competitor.close()
    assert server.close_count == 1


def test_store_replays_lost_atomic_commit_without_duplicate_document_or_fts(atomic_server, monkeypatch):
    from knowledge.store import upsert_documents
    server, _ = atomic_server
    original_open = server.open
    lost = False
    def drop_once(request, timeout):
        nonlocal lost
        response = original_open(request, timeout)
        if not lost:
            lost = True
            response.close()
            raise TimeoutError('receipt lost after complete server queue')
        return response
    monkeypatch.setattr(server, 'open', drop_once)
    monkeypatch.setattr(ingest, '_SOURCE_RETRY_BACKOFF_SECS', 0)
    counts = ingest._persist_prepared(http_db.Connection, lambda db: upsert_documents(db, [_raw_doc()]), source='docs')
    assert counts == {'inserted': 0, 'updated': 0, 'skipped': 1, 'pruned': 0}
    assert server.db.execute('SELECT id,doc_key FROM knowledge').fetchall() == [(1, 'one')]
    assert server.db.execute('SELECT rowid FROM knowledge_fts').fetchall() == [(1,)]
    assert server.close_count == 2


def test_middle_sql_failure_skips_commit_rolls_back_and_closes(atomic_server):
    from knowledge.store import upsert_documents
    server, _ = atomic_server
    server.fail_sql = 'INSERT INTO knowledge_fts'
    with pytest.raises(http_db.HranaHttpError, match='SQLITE_ERROR'):
        upsert_documents(http_db.Connection(), [_raw_doc()])
    assert server.db.execute('SELECT count(*) FROM knowledge').fetchone() == (0,)
    assert server.db.execute('SELECT count(*) FROM knowledge_fts').fetchone() == (0,)
    assert 'COMMIT' not in server.executed_sql
    assert server.executed_sql[-1] == 'ROLLBACK'
    assert server.close_count == 1


def test_source_prune_is_one_atomic_server_queue(atomic_server):
    from knowledge.store import delete_source_docs, upsert_documents
    server, _ = atomic_server
    upsert_documents(http_db.Connection(), [_raw_doc()])
    server.calls.clear()
    assert delete_source_docs(http_db.Connection(), 'docs', ['one']) == 1
    assert len(server.calls) == 1
    assert [item['type'] for item in server.calls[0][1]['requests']] == ['batch', 'close']
    assert server.db.execute('SELECT count(*) FROM knowledge').fetchone() == (0,)
    assert server.db.execute('SELECT count(*) FROM knowledge_fts').fetchone() == (0,)


def test_http_store_preserves_skip_backfill_and_changed_row_semantics(atomic_server):
    from knowledge.store import upsert_documents
    server, _ = atomic_server
    created, activity = '2020-01-01T00:00:00Z', '2020-01-02T00:00:00Z'
    def persist(**kwargs):
        return upsert_documents(http_db.Connection(), [_raw_doc(**kwargs)])
    assert persist(created_at=created, last_activity_at=activity)['inserted'] == 1
    before = server.db.total_changes
    assert persist(created_at='new', last_activity_at='new')['skipped'] == 1
    assert server.db.total_changes == before
    assert persist(embedding=[0.25] * 384, last_activity_at='new')['updated'] == 1
    # Only the canonical vector changed: no FTS delete/insert or activity bump.
    assert server.db.total_changes == before + 1
    assert server.db.execute('SELECT id,created_at,last_activity_at,embedding IS NOT NULL FROM knowledge').fetchone() == (1, created, activity, 1)
    before = server.db.total_changes
    assert persist()['skipped'] == 1
    assert server.db.total_changes == before
    assert persist(content='replacement', title='changed title', summary='changed summary', metadata={'new': True}, scope='research', last_activity_at='later')['updated'] == 1
    assert server.db.execute('SELECT id,created_at,last_activity_at,embedding,scope,metadata FROM knowledge').fetchone() == (1, created, 'later', None, 'research', '{"new": true}')
    assert server.db.execute('SELECT rowid,title,summary,content FROM knowledge_fts').fetchall() == [(1, 'changed title', 'changed summary', 'replacement')]


def test_http_store_atomic_chunk_prune_preserves_siblings_and_other_sources(atomic_server):
    from knowledge.store import delete_source_docs, upsert_documents
    server, _ = atomic_server
    doc = lambda ix: _raw_doc(chunk_ix=ix, content=f'part{ix}')
    upsert_documents(http_db.Connection(), [doc(0), doc(1), doc(2), _raw_doc('sibling'), _raw_doc(source='other')])
    result = upsert_documents(http_db.Connection(), [doc(0), doc(1)])
    assert result == {'inserted': 0, 'updated': 0, 'skipped': 2, 'pruned': 1}
    assert server.db.execute('SELECT source,doc_key,chunk_ix FROM knowledge ORDER BY id').fetchall() == [('docs','one',0), ('docs','one',1), ('docs','sibling',0), ('other','one',0)]
    assert delete_source_docs(http_db.Connection(), 'docs', ['one']) == 2
    assert server.db.execute('SELECT source,doc_key FROM knowledge ORDER BY id').fetchall() == [('docs','sibling'), ('other','one')]
    assert server.db.execute('SELECT rowid FROM knowledge_fts ORDER BY rowid').fetchall() == server.db.execute('SELECT id FROM knowledge ORDER BY id').fetchall()


def test_http_store_repairs_missing_mirror_without_mutating_canonical_row(atomic_server):
    from knowledge.store import upsert_documents
    server, _ = atomic_server
    upsert_documents(http_db.Connection(), [_raw_doc()])
    canonical = server.db.execute('SELECT * FROM knowledge').fetchall()
    server.db.execute('DELETE FROM knowledge_fts')
    assert upsert_documents(http_db.Connection(), [_raw_doc()])['skipped'] == 1
    assert server.db.execute('SELECT * FROM knowledge').fetchall() == canonical
    assert server.db.execute('SELECT rowid,content FROM knowledge_fts').fetchall() == [(1, 'durable')]


def test_large_embedded_authoritative_document_is_one_bounded_atomic_request(atomic_server):
    from knowledge.store import upsert_documents
    server, _ = atomic_server
    docs = [_raw_doc(chunk_ix=i, content='x' * 2000, embedding=[0.1234567890123456] * 384) for i in range(205)]
    result = upsert_documents(http_db.Connection(), docs)
    assert result['inserted'] == 205
    assert len(server.calls) == 1
    payload = server.calls[0][1]
    assert len(json.dumps(payload).encode()) <= http_db.MAX_REQUEST_BYTES
    assert len(payload['requests'][0]['batch']['steps']) <= http_db.MAX_TRANSACTION_STEPS
    assert server.db.execute('SELECT count(*) FROM knowledge_fts').fetchone() == (205,)


def test_failed_begin_preserves_first_error_and_never_runs_writes(atomic_server):
    from knowledge.store import upsert_documents
    server, _ = atomic_server
    server.fail_sql = 'BEGIN IMMEDIATE'
    with pytest.raises(http_db.HranaHttpError, match='injected middle SQL failure'):
        upsert_documents(http_db.Connection(), [_raw_doc()])
    assert server.executed_sql == ['BEGIN IMMEDIATE', 'ROLLBACK']
    assert server.close_count == 1


@pytest.mark.parametrize('corruption', ['missing_results', 'missing_errors', 'unconfirmed_commit', 'unexpected_rollback', 'bad_close', 'bad_step_error', 'wrong_response'])
def test_atomic_store_rejects_incomplete_success_receipts(atomic_server, monkeypatch, corruption):
    from knowledge.store import upsert_documents
    server, _ = atomic_server
    original_open = server.open
    def corrupt(request, timeout):
        response = original_open(request, timeout)
        body = json.loads(response.getvalue())
        response.close()
        result = body['results'][0]['response']['result']
        if corruption == 'missing_results': result.pop('step_results')
        elif corruption == 'missing_errors': result['step_errors'].pop()
        elif corruption == 'unconfirmed_commit': result['step_results'][-2] = None
        elif corruption == 'unexpected_rollback': result['step_results'][-1] = {'rows': []}
        elif corruption == 'bad_close': body['results'][-1] = {'type': 'error'}
        elif corruption == 'bad_step_error': result['step_errors'][0] = 'invalid'
        elif corruption == 'wrong_response': body['results'][0]['response']['type'] = 'execute'
        return Response(json.dumps(body).encode())
    monkeypatch.setattr(server, 'open', corrupt)
    with pytest.raises(http_db.TransportError):
        upsert_documents(http_db.Connection(), [_raw_doc()])
    assert not server.db.in_transaction


@pytest.mark.parametrize('bound', ['MAX_TRANSACTION_STEPS', 'MAX_REQUEST_BYTES'])
def test_oversized_atomic_document_fails_before_network_without_splitting(atomic_server, monkeypatch, bound):
    from knowledge.store import upsert_documents
    server, _ = atomic_server
    monkeypatch.setattr(http_db, bound, 3)
    with pytest.raises(http_db.HranaHttpError, match='bounded'):
        upsert_documents(http_db.Connection(), [_raw_doc()])
    assert server.calls == []


def test_atomic_outer_sql_error_preserves_retry_classification(atomic_server, monkeypatch):
    server, _ = atomic_server
    body = {'baton': None, 'results': [
        {'type': 'error', 'error': {'code': 'SQLITE_BUSY', 'message': 'busy'}},
        {'type': 'ok', 'response': {'type': 'close'}},
    ]}
    monkeypatch.setattr(server, 'open', lambda *a, **k: Response(json.dumps(body).encode()))
    with pytest.raises(http_db.HranaHttpError, match='SQLITE_BUSY') as caught:
        http_db.Connection().execute_transaction([('SELECT 1', ())])
    assert ingest._is_transient_db_error(caught.value)


def test_atomic_operations_require_fresh_handle_and_empty_store_never_locks(atomic_server):
    from knowledge.store import delete_source_docs, upsert_documents
    server, _ = atomic_server
    db = http_db.Connection()
    assert upsert_documents(db, []) == {'inserted': 0, 'updated': 0, 'skipped': 0, 'pruned': 0}
    assert delete_source_docs(db, 'docs', []) == 0
    assert server.calls == []
    db.close()
    with pytest.raises(http_db.TransportError, match='fresh stream'):
        db.execute_transaction([('SELECT 1', ())])
    assert server.calls == []


def test_atomic_receipt_has_separate_finite_budget_from_reads_and_cleanup(atomic_server, monkeypatch):
    server, _ = atomic_server
    clock = [0.0]
    original_open = server.open
    def delayed_receipt(request, timeout):
        response = original_open(request, timeout)
        if json.loads(request.data)['requests'][0]['type'] == 'batch':
            clock[0] += 5.0  # whole document legitimately exceeds a single-read budget
        return response
    monkeypatch.setattr(server, 'open', delayed_receipt)
    monkeypatch.setattr(http_db.time, 'monotonic', lambda: clock[0])
    db = http_db.Connection()
    db.execute_transaction([('SELECT 1', ())])
    assert db.last_transaction_step_count == 4
    db.execute('SELECT 1')
    db.execute('BEGIN IMMEDIATE')
    db.close()
    assert [call[2] for call in server.calls] == [30.0, 4.0, 4.0, 4.0]
    assert [r['type'] for r in server.calls[0][1]['requests']] == ['batch', 'close']


def test_atomic_receipt_still_expires_after_finite_allowance(atomic_server, monkeypatch):
    server, path = atomic_server
    clock = [0.0]
    original_open = server.open
    def expired_receipt(request, timeout):
        response = original_open(request, timeout)
        clock[0] += 31.0
        return response
    monkeypatch.setattr(server, 'open', expired_receipt)
    monkeypatch.setattr(http_db.time, 'monotonic', lambda: clock[0])
    with pytest.raises(http_db.TransportError, match='deadline'):
        http_db.Connection().execute_transaction([('SELECT 1', ())])
    assert server.calls[0][2] == 30.0
    assert not server.db.in_transaction
    competitor = sqlite3.connect(path, isolation_level=None, timeout=0)
    competitor.execute('BEGIN IMMEDIATE')
    competitor.rollback()
    competitor.close()
