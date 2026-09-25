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


def test_ambiguous_commit_replays_whole_prepared_batch_idempotently(connection, monkeypatch):
    from knowledge.schema import KnowledgeDoc
    from knowledge.store import upsert_documents

    _, server = connection
    server.db.executescript('''
        CREATE TABLE knowledge(id INTEGER PRIMARY KEY, source TEXT, scope TEXT,
          doc_key TEXT, chunk_ix INTEGER, title TEXT, summary TEXT, content TEXT,
          metadata TEXT, embedding BLOB, content_hash TEXT, created_at TEXT,
          last_activity_at TEXT, UNIQUE(source, doc_key, chunk_ix));
        CREATE VIRTUAL TABLE knowledge_fts USING fts5(title, summary, content);
    ''')
    original_open = server.open
    lose_receipt = True

    def ambiguous_once(request, timeout):
        nonlocal lose_receipt
        body = json.loads(request.data)
        response = original_open(request, timeout)
        if lose_receipt and body['requests'][0].get('stmt', {}).get('sql') == 'COMMIT':
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
    return KnowledgeDoc(source='docs', scope='ops', doc_key=key, content='durable', **kwargs)


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
