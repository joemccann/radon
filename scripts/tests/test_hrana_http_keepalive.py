"""PERF-009: job/watchdog Hrana transport must reuse one TCP socket per thread.

Sister of PERF-008 (api.db_http). Watchdog heartbeats and sequential
writer pipelines each opened a fresh urllib socket.
"""
from __future__ import annotations

import json
import http.client
import socket
import sys
import threading

import pytest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from db import hrana_http  # noqa: E402
from api import db_http  # noqa: E402


@pytest.fixture(params=[hrana_http, db_http], ids=['shared', 'api'])
def transport(request):
    module = request.param
    query = module.hrana_query if module is hrana_http else module.hrana_execute
    error = module.HranaHttpError if module is hrana_http else module.DbHttpError
    rows = [('1',)] if module is hrana_http else [(1,)]
    return module, query, error, rows


def test_five_sequential_pipelines_use_one_tcp_connection(monkeypatch):
    connections = {"n": 0}
    empty_pipeline = json.dumps({
        "results": [
            {
                "type": "ok",
                "response": {
                    "type": "execute",
                    "result": {"cols": [], "rows": []},
                },
            },
            {"type": "ok", "response": {"type": "close"}},
        ]
    }).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        timeout = 1

        def setup(self):
            super().setup()
            connections["n"] += 1

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(empty_pipeline)))
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.wfile.write(empty_pipeline)

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    monkeypatch.setenv("TURSO_DB_URL", f"http://{host}:{port}")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "token")
    monkeypatch.setenv("RADON_DB_TEST_WRITE_OK", "1")
    try:
        for _ in range(5):
            assert hrana_http.hrana_query("SELECT 1") == []
        assert connections["n"] == 1, (
            f"TCP connections {connections['n']} for 5 sequential pipelines"
        )
    finally:
        closer = getattr(hrana_http, "_close_keepalive", None)
        if closer:
            closer()
        server.shutdown()
        thread.join(timeout=2)


def test_oversized_body_rejects_and_leaves_the_next_pipeline_usable(monkeypatch):
    """Same cap contract as the API transport. A prefix of an oversized 200
    must not stick the thread-local socket, or the next watchdog write fails
    before it is sent.
    """
    cap = hrana_http._MAX_RESPONSE_BYTES
    over = b"X" * (cap + 1)
    ok = json.dumps({
        "results": [
            {
                "type": "ok",
                "response": {
                    "type": "execute",
                    "result": {
                        "cols": [{"name": "value"}],
                        "rows": [[{"type": "text", "value": "kept"}]],
                    },
                },
            },
            {"type": "ok", "response": {"type": "close"}},
        ]
    }).encode("utf-8")
    posts = {"n": 0}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            posts["n"] += 1
            payload = over if posts["n"] == 1 else ok
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    monkeypatch.setenv("TURSO_DB_URL", f"http://{host}:{port}")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "token")
    monkeypatch.setenv("RADON_DB_TEST_WRITE_OK", "1")
    hrana_http._close_keepalive()
    try:
        with pytest.raises(hrana_http.HranaHttpError, match="exceeds limit"):
            hrana_http.hrana_query("SELECT 1")
        assert hrana_http.hrana_query("SELECT 1") == [("kept",)]
        assert posts["n"] == 2
    finally:
        hrana_http._close_keepalive()
        server.shutdown()
        thread.join(timeout=2)

def test_oversized_response_does_not_abandon_the_next_pipeline(monkeypatch):
    """A body over the cap must fail that call only.

    The keep-alive socket used to keep the unread tail. The next pipeline
    was written onto that socket and then discarded, so a committed write
    came back as a transport error.
    """
    posts: list[bytes] = []
    small = json.dumps({
        "results": [
            {
                "type": "ok",
                "response": {
                    "type": "execute",
                    "result": {"cols": [], "rows": []},
                },
            },
            {"type": "ok", "response": {"type": "close"}},
        ]
    }).encode("utf-8")
    huge = b'{"overflow":"' + (b"x" * (hrana_http._MAX_RESPONSE_BYTES + 64))

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            posts.append(body)
            payload = huge if len(posts) == 1 else small
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                return

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    monkeypatch.setenv("TURSO_DB_URL", f"http://{host}:{port}")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "token")
    monkeypatch.setenv("RADON_DB_TEST_WRITE_OK", "1")
    try:
        try:
            hrana_http.hrana_query("SELECT big")
            raised = False
        except hrana_http.HranaHttpError:
            raised = True
        assert raised, "an over-cap body must fail closed"
        assert len(posts) == 1, f"the rejected read issued {len(posts)} pipelines"
        assert hrana_http.hrana_query("SELECT 1") == []
        assert len(posts) == 2, (
            f"the recovered read saw {len(posts)} pipelines; "
            "an abandoned follow-up must not be sent"
        )
    finally:
        hrana_http._close_keepalive()
        server.shutdown()
        thread.join(timeout=2)

def _pipeline_body(value: str) -> bytes:
    return json.dumps({
        "results": [
            {
                "type": "ok",
                "response": {
                    "type": "execute",
                    "result": {
                        "cols": [{"name": "one"}],
                        "rows": [[{"type": "integer", "value": value}]],
                    },
                },
            },
            {"type": "ok", "response": {"type": "close"}},
        ]
    }).encode("utf-8")


def _serve(posts, connections, *, drop_on, received=None):
    """HTTP/1.1 keep-alive. `drop_on` post counts close before any status."""
    body = _pipeline_body("1")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        timeout = 1

        def setup(self):
            super().setup()
            connections["n"] += 1

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            posts["n"] += 1
            if received is not None:
                received.set()
            if posts["n"] in drop_on:
                self.close_connection = True
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.connection.close()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _point_at(server, monkeypatch):
    host, port = server.server_address
    monkeypatch.setenv("TURSO_DB_URL", f"http://{host}:{port}")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "token")
    monkeypatch.setenv("RADON_DB_TEST_WRITE_OK", "1")


def _stop(server, thread, module=hrana_http):
    closer = getattr(module, "_close_keepalive", None)
    if closer:
        closer()
    server.shutdown()
    thread.join(timeout=2)


def test_reused_socket_closed_before_status_retries_once(monkeypatch, transport):
    """Idle keep-alive dies during the next statement's status line.

    The cash-flow heartbeat holds the socket across a long libsql mirror.
    The following trade-coverage SELECT is the reused POST. The peer
    accepts it and closes before a status line. One fresh socket must
    return the row. A second reconnect would hide a real outage.
    """
    posts = {"n": 0}
    module, query, error, rows = transport
    connections = {"n": 0}
    server, thread = _serve(posts, connections, drop_on={2})
    _point_at(server, monkeypatch)
    try:
        assert query("SELECT 1") == rows
        assert query("SELECT 1") == rows
        assert connections["n"] == 2
        assert posts["n"] == 3
    finally:
        _stop(server, thread, module)


def test_fresh_socket_closed_before_status_does_not_open_another(monkeypatch, transport):
    module, query, error, rows = transport
    posts = {"n": 0}
    connections = {"n": 0}
    server, thread = _serve(posts, connections, drop_on={1})
    _point_at(server, monkeypatch)
    try:
        with pytest.raises(error, match="RemoteDisconnected|Connection reset|Connection aborted"):
            query("SELECT 1")
        assert connections["n"] == 1
        assert posts["n"] == 1
        assert getattr(module._local, 'conn', None) is None
    finally:
        _stop(server, thread, module)


def test_reused_close_and_fresh_retry_close_raises_once(monkeypatch, transport):
    module, query, error, rows = transport
    posts = {"n": 0}
    connections = {"n": 0}
    server, thread = _serve(posts, connections, drop_on={2, 3})
    _point_at(server, monkeypatch)
    try:
        assert query("SELECT 1") == rows
        with pytest.raises(error):
            query("SELECT 1")
        assert connections["n"] == 2
        assert posts["n"] == 3
        assert getattr(module._local, 'conn', None) is None
    finally:
        _stop(server, thread, module)


@pytest.mark.parametrize("sql", [
    "INSERT INTO counter(value) VALUES(1)",
    "UPDATE counter SET value=value+1",
    "DELETE FROM counter WHERE value=1",
    "WITH chosen AS (SELECT 1) UPDATE counter SET value=value+1",
    "SELECT 1; UPDATE counter SET value=value+1",
    "PRAGMA user_version=5",
])
def test_lost_write_response_never_replays_the_statement(monkeypatch, sql, transport):
    """A peer can commit a POST before disconnecting without a status line."""
    posts = {"n": 0}
    module, query, error, rows = transport
    connections = {"n": 0}
    server, thread = _serve(posts, connections, drop_on={2})
    _point_at(server, monkeypatch)
    try:
        assert query("SELECT 1") == rows
        with pytest.raises(error):
            module.hrana_execute(sql)
        assert posts["n"] == 2, "unknown write outcome must not send another POST"
        assert connections["n"] == (2 if module is db_http else 1)
    finally:
        _stop(server, thread, module)


@pytest.mark.parametrize('sql', [
    'INSERT INTO counter(value) VALUES(1)',
    'UPDATE counter SET value=value+1',
])
def test_request_error_after_peer_received_write_never_replays(monkeypatch, sql, transport):
    """A send failure can occur after the server received the mutation."""
    module, query, error, rows = transport
    posts, connections, received = {'n': 0}, {'n': 0}, threading.Event()
    server, thread = _serve(posts, connections, drop_on={2}, received=received)
    _point_at(server, monkeypatch)
    original_request = http.client.HTTPConnection.request
    requests = {'n': 0}
    def request_then_fail(connection, *args, **kwargs):
        requests['n'] += 1
        original_request(connection, *args, **kwargs)
        if requests['n'] == 2:
            assert received.wait(2), 'the peer must receive the write before its send error'
            raise OSError('response outcome unknown after send')
    monkeypatch.setattr(http.client.HTTPConnection, 'request', request_then_fail)
    try:
        assert query('SELECT 1') == rows
        received.clear()
        with pytest.raises(error):
            module.hrana_execute(sql)
        assert posts['n'] == 2 and requests['n'] == 2, 'an uncertain write must not be sent twice'
        assert connections['n'] == (2 if module is db_http else 1)
        assert getattr(module._local, 'conn', None) is None
    finally:
        _stop(server, thread, module)


def test_failed_fresh_read_retry_retires_the_socket(monkeypatch, transport):
    module, query, error, rows = transport
    posts, connections, received = {'n': 0}, {'n': 0}, threading.Event()
    server, thread = _serve(posts, connections, drop_on={2, 3}, received=received)
    _point_at(server, monkeypatch)
    original_request = http.client.HTTPConnection.request
    requests = {'n': 0}
    def request_then_fail(connection, *args, **kwargs):
        requests['n'] += 1
        received.clear()
        original_request(connection, *args, **kwargs)
        if requests['n'] > 1:
            assert received.wait(2)
            raise OSError('send outcome unknown')
    monkeypatch.setattr(http.client.HTTPConnection, 'request', request_then_fail)
    try:
        assert query('SELECT 1') == rows
        with pytest.raises(error):
            query('SELECT 1')
        assert posts['n'] == 3 and requests['n'] == 3
        assert connections['n'] == 2
        assert getattr(module._local, 'conn', None) is None
    finally:
        _stop(server, thread, module)


@pytest.mark.parametrize("payload, safe", [
    ({"requests": [{"type": "execute", "stmt": {"sql": " select 1; "}}, {"type": "close"}]}, True),
    ({"requests": [{"type": "execute", "stmt": {"sql": "SELECT ?", "args": [{"type": "text", "value": ";UPDATE"}]}}]}, True),
    ({"requests": [{"type": "close"}]}, False),
    ({"requests": [{"type": "batch"}]}, False),
    ({"requests": [{"type": "execute", "stmt": {"sql": "SELECT 1"}}, {"type": "execute", "stmt": {"sql": "DELETE FROM t"}}]}, False),
    ({"requests": [{"type": "execute", "stmt": {"sql": None}}]}, False),
    ({"requests": [None]}, False),
    ({"requests": "SELECT 1"}, False),
    ({"requests": []}, False),
    ({}, False),
])
def test_retry_requires_a_proven_read_only_pipeline(payload, safe, transport):
    import urllib.request
    request = urllib.request.Request("https://example.invalid/v2/pipeline", data=json.dumps(payload).encode())
    assert transport[0]._retry_safe_read(request) is safe


def test_retry_refuses_an_unparsed_pipeline(transport):
    import urllib.request
    request = urllib.request.Request("https://example.invalid/v2/pipeline", data=b"not json")
    assert transport[0]._retry_safe_read(request) is False


@pytest.fixture
def idle_pool(monkeypatch, transport):
    """No sleeps or sockets: time advances only when a response completes."""
    from types import SimpleNamespace
    import urllib.request

    module = transport[0]
    module._close_keepalive()
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: clock.now), raising=False)
    connections, responses = [], []

    class Response:
        status = 200

        def read(self, _limit):
            return _pipeline_body('1')

        def isclosed(self):
            return True

    class Connection:
        def __init__(self, host, port, timeout, **kwargs):
            self.host, self.port, self.timeout = host, port, timeout
            self.requests, self.closed = [], False
            connections.append(self)

        def request(self, method, path, body, headers):
            assert not self.closed, 'a retired connection received another request'
            self.requests.append((method, path, json.loads(body), self.timeout))

        def getresponse(self):
            value = responses.pop(0) if responses else Response()
            if isinstance(value, Exception):
                raise value
            return value

        def close(self):
            self.closed = True

    monkeypatch.setattr(module.http.client, 'HTTPConnection', Connection)
    monkeypatch.setattr(module.http.client, 'HTTPSConnection', Connection)

    def request(sql='SELECT 1', timeout=4.0, origin='https://synthetic.invalid'):
        body = json.dumps({'requests': [{'type': 'execute', 'stmt': {'sql': sql}}, {'type': 'close'}]}).encode()
        req = urllib.request.Request(origin + '/v2/pipeline', data=body, method='POST')
        return module._pooled_open(req, timeout).read()

    yield module, clock, connections, responses, request, Response
    module._close_keepalive()


@pytest.mark.parametrize('sql', ['SELECT 1', 'UPDATE synthetic_counter SET value=value+1'])
@pytest.mark.parametrize('kind', ['zero', 'just_under', 'exact', 'past'])
def test_pool_expires_idle_connections_before_sending_reads_or_writes(idle_pool, sql, kind):
    module, clock, connections, responses, request, _ = idle_pool
    idle_s = module._KEEPALIVE_IDLE_S
    idle = {
        'zero': 0.0,
        'just_under': idle_s - 0.001,
        'exact': idle_s,
        'past': idle_s + 40.0,
    }[kind]
    expired = kind in ('exact', 'past') or (module is db_http and not sql.startswith('SELECT '))
    request()
    clock.now += idle
    request(sql, timeout=2.0)
    assert len(connections) == (2 if expired else 1)
    assert len(connections[0].requests) == (1 if expired else 2)
    assert connections[0].closed is expired
    assert connections[-1].requests[-1][2]['requests'][0]['stmt']['sql'] == sql
    assert connections[-1].requests[-1][3] == 2.0
    assert sum(len(conn.requests) for conn in connections) == 2, 'expiry is retirement, never a write retry'


def test_pool_idle_age_starts_after_full_successful_response(idle_pool):
    module, clock, connections, responses, request, Response = idle_pool

    class SlowResponse(Response):
        def read(self, limit):
            clock.now += 20.0
            return super().read(limit)

    responses.append(SlowResponse())
    request()
    assert clock.now == 120.0
    idle_s = module._KEEPALIVE_IDLE_S
    under = 120.0 + idle_s - 0.001
    clock.now = under
    request('SELECT 1')
    assert len(connections) == 1, 'request duration is not idle age'
    clock.now = under + idle_s
    request('SELECT 1')
    assert len(connections) == 2, 'every full success refreshes the idle clock'


@pytest.mark.parametrize('failure', ['headers', 'body', 'oversized', 'http'])
def test_pool_failure_retires_connection_and_clears_idle_clock(idle_pool, failure, monkeypatch):
    module, clock, connections, responses, request, Response = idle_pool
    request()
    assert module._local.last_success == 100.0
    clock.now = 101.0

    class FailedResponse(Response):
        status = 503 if failure == 'http' else 200

        def read(self, limit):
            if failure == 'body':
                raise OSError('synthetic interrupted body')
            return b'x' * (limit + 1) if failure == 'oversized' else super().read(limit)

    responses.append(http.client.RemoteDisconnected('synthetic lost write response')
                     if failure == 'headers' else FailedResponse())
    with pytest.raises((OSError, http.client.HTTPException)):
        request('UPDATE synthetic_counter SET value=value+1')
    failed_connections = 2 if module is db_http else 1
    assert len(connections) == failed_connections
    assert sum(len(conn.requests) for conn in connections) == 2, 'no failed write replay'
    assert all(conn.closed for conn in connections)
    assert getattr(module._local, 'conn', None) is None
    assert getattr(module._local, 'last_success', None) is None
    clock.now = 102.0
    request('UPDATE synthetic_counter SET value=value+1')
    assert len(connections) == failed_connections + 1 and len(connections[-1].requests) == 1
    assert module._local.last_success == 102.0


def test_pool_origin_change_retires_prior_connection_and_starts_fresh_idle_clock(idle_pool):
    module, clock, connections, responses, request, _ = idle_pool
    request()
    clock.now = 101.0
    request(origin='https://other-synthetic.invalid')
    assert len(connections) == 2
    assert connections[0].closed, 'changing origins cannot retain an unreachable old pooled socket'
    assert connections[1].host == 'other-synthetic.invalid'
    assert module._local.last_success == 101.0


@pytest.fixture
def publication_peer(monkeypatch):
    """Real HTTP/1.1 peer: stale reuse drops only its second request.

    SQLite executes the real Hrana conditional transaction against posts and
    provenance tables. A fresh connection remains usable after the copy gap.
    """
    import sqlite3
    from types import SimpleNamespace

    connection = sqlite3.connect(':memory:', isolation_level=None, check_same_thread=False)
    migrations = _SCRIPTS_DIR / 'db/migrations'
    for name in ('0001_init.sql', '0071_research_post_sources.sql'):
        connection.executescript((migrations / name).read_text())
    state = SimpleNamespace(mode='stale_reuse', connections=0, requests=0, commits=0, batches=[])
    clock = {'t': 1000.0}
    monkeypatch.setattr(db_http.time, 'monotonic', lambda: clock['t'])

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def setup(self):
            super().setup()
            self.requests = 0
            state.connections += 1

        def drop(self):
            self.close_connection = True
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            self.requests += 1
            state.requests += 1
            if state.mode == 'stale_reuse' and self.requests > 1:
                self.drop()
                return
            operation = body['requests'][0]
            if operation['type'] == 'batch':
                state.batches.append(operation)
                results, errors = [], []
                for step in operation['batch']['steps']:
                    cond = step.get('condition')
                    if cond:
                        valid = (results[cond['step']] is not None) if cond['type'] == 'ok' else (
                            results[cond['cond']['step']] is None)
                        if not valid:
                            results.append(None)
                            errors.append(None)
                            continue
                    stmt = step['stmt']
                    args = [int(a['value']) if a['type'] == 'integer' else a.get('value') for a in stmt['args']]
                    try:
                        connection.execute(stmt['sql'], args)
                        if stmt['sql'] == 'COMMIT':
                            state.commits += 1
                        results.append({'affected_row_count': 1})
                        errors.append(None)
                    except sqlite3.Error as error:
                        results.append(None)
                        errors.append({'message': str(error)})
                response = {'results': [{'type': 'ok', 'response': {'type': 'batch', 'result': {
                    'step_results': results, 'step_errors': errors}}}]}
                if state.mode == 'lost_commit':
                    self.drop()
                    return
            else:
                stmt = operation['stmt']
                connection.execute(stmt['sql'])
                response = json.loads(_pipeline_body('1'))
            encoded = json.dumps(response).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(encoded)))
            self.send_header('Connection', 'keep-alive')
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _point_at(server, monkeypatch)
    db_http._close_keepalive()
    hrana_http._close_keepalive()
    try:
        yield state, clock, connection
    finally:
        db_http._close_keepalive()
        hrana_http._close_keepalive()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
        connection.close()


def _certified_marketdesk_post(monkeypatch):
    from research import publish
    from research_editor_fixtures import certify
    monkeypatch.setattr(publish.book, 'current_tickers', lambda: {'NVDA'})
    article = 'fixtureAbc12'
    url = 'https://marketdesk.ai/library/browse/' + article
    return certify({'id': publish.stable_post_id('marketdesk:' + article, 'article'),
        'title': 'Dispersion shifted', 'content': 'New sector evidence.',
        'timestamp': '2026-10-31T06:00:00Z', 'images': [], 'tags': ['NVDA'],
        'source': {'kind': 'marketdesk', 'publisher': 'MS', 'articleId': article,
            'articleUrl': url, 'url': url, 'evidenceKind': 'article', 'contentHash': 'a' * 64,
            'documentDate': '2026-10-31', 'folderDate': '2026-10-31', 'pages': [], 'figures': []}})


@pytest.mark.parametrize('copy_gap', [0, 18])
def test_publication_fresh_connection_commits_post_and_source_once(monkeypatch, publication_peer, copy_gap):
    from research import publish
    state, clock, connection = publication_peer
    post = _certified_marketdesk_post(monkeypatch)
    assert db_http.hrana_execute('SELECT 1') == [(1,)]
    clock['t'] += copy_gap  # Reproduce production's 18s gap below the 30s read idle budget.
    assert publish.publish(post) == post['id']
    assert state.connections == 2 and state.requests == 2
    assert state.commits == 1 and len(state.batches) == 1
    assert connection.execute('SELECT id FROM posts').fetchall() == [(post['id'],)]
    source = connection.execute('SELECT post_id,provenance_json FROM research_post_sources').fetchall()
    assert len(source) == 1 and source[0][0] == post['id']
    assert json.loads(source[0][1])['articleId'] == post['source']['articleId']
    assert not connection.in_transaction


def test_publication_lost_commit_response_is_not_resent(monkeypatch, publication_peer):
    from research import publish
    state, clock, connection = publication_peer
    state.mode = 'lost_commit'
    post = _certified_marketdesk_post(monkeypatch)
    assert db_http.hrana_execute('SELECT 1') == [(1,)]
    clock['t'] += 18
    with pytest.raises(db_http.DbHttpError, match='RemoteDisconnected') as error:
        publish.publish(post)
    assert isinstance(error.value.__cause__, http.client.RemoteDisconnected)
    assert state.connections == 2 and state.requests == 2, 'fresh ambiguous write must never reconnect/resend'
    assert state.commits == 1 and len(state.batches) == 1
    assert connection.execute('SELECT count(*) FROM posts').fetchone() == (1,)
    assert connection.execute('SELECT count(*) FROM research_post_sources').fetchone() == (1,)
    assert not connection.in_transaction


@pytest.mark.parametrize('gap', [5, 18])
def test_read_pool_preserves_health_cadence_and_copy_gap(monkeypatch, publication_peer, gap):
    state, clock, _ = publication_peer
    state.mode = 'normal'
    for _ in range(3):
        assert db_http.hrana_execute('SELECT 1') == [(1,)]
        clock['t'] += gap
    assert db_http._KEEPALIVE_IDLE_S == 30.0
    assert state.connections == 1 and state.requests == 3


def test_sibling_write_pool_keeps_existing_five_second_reuse(publication_peer):
    state, clock, connection = publication_peer
    state.mode = 'normal'
    hrana_http.hrana_execute('CREATE TABLE counter(value INTEGER)')
    clock['t'] += 1
    hrana_http.hrana_execute('INSERT INTO counter VALUES(1)')
    clock['t'] += 3
    hrana_http.hrana_execute('UPDATE counter SET value=value+1')
    assert hrana_http._KEEPALIVE_IDLE_S == 5.0
    assert state.connections == 1 and state.requests == 3
    assert connection.execute('SELECT value FROM counter').fetchall() == [(2,)]
