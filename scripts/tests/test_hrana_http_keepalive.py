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
        assert connections["n"] == 1
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
        assert connections['n'] == 1
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
