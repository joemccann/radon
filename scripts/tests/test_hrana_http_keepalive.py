"""PERF-009: job/watchdog Hrana transport must reuse one TCP socket per thread.

Sister of PERF-008 (api.db_http). Watchdog heartbeats and sequential
writer pipelines each opened a fresh urllib socket.

A reused socket whose peer closed before the status line must reconnect
once. flex-pull's trade-coverage read saw that as RemoteDisconnected and
the oneshot exited 1 on an already-applied delivery.
"""
from __future__ import annotations

import json
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


def _serve(posts, connections, *, drop_on):
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


def _stop(server, thread):
    closer = getattr(hrana_http, "_close_keepalive", None)
    if closer:
        closer()
    server.shutdown()
    thread.join(timeout=2)


def test_reused_socket_closed_before_status_retries_once(monkeypatch):
    """Idle keep-alive dies during the next statement's status line.

    The cash-flow heartbeat holds the socket across a long libsql mirror.
    The following trade-coverage SELECT is the reused POST. The peer
    accepts it and closes before a status line. One fresh socket must
    return the row. A second reconnect would hide a real outage.
    """
    posts = {"n": 0}
    connections = {"n": 0}
    server, thread = _serve(posts, connections, drop_on={2})
    _point_at(server, monkeypatch)
    try:
        assert hrana_http.hrana_query("SELECT 1") == [("1",)]
        assert hrana_http.hrana_query("SELECT 1") == [("1",)]
        assert connections["n"] == 2
        assert posts["n"] == 3
    finally:
        _stop(server, thread)


def test_fresh_socket_closed_before_status_does_not_open_another(monkeypatch):
    posts = {"n": 0}
    connections = {"n": 0}
    server, thread = _serve(posts, connections, drop_on={1})
    _point_at(server, monkeypatch)
    try:
        with pytest.raises(hrana_http.HranaHttpError, match="RemoteDisconnected|Connection reset|Connection aborted"):
            hrana_http.hrana_query("SELECT 1")
        assert connections["n"] == 1
        assert posts["n"] == 1
    finally:
        _stop(server, thread)


def test_reused_close_and_fresh_retry_close_raises_once(monkeypatch):
    posts = {"n": 0}
    connections = {"n": 0}
    server, thread = _serve(posts, connections, drop_on={2, 3})
    _point_at(server, monkeypatch)
    try:
        assert hrana_http.hrana_query("SELECT 1") == [("1",)]
        with pytest.raises(hrana_http.HranaHttpError):
            hrana_http.hrana_query("SELECT 1")
        assert connections["n"] == 2
        assert posts["n"] == 3
    finally:
        _stop(server, thread)


@pytest.mark.parametrize("sql", [
    "INSERT INTO counter(value) VALUES(1)",
    "UPDATE counter SET value=value+1",
    "DELETE FROM counter WHERE value=1",
    "WITH chosen AS (SELECT 1) UPDATE counter SET value=value+1",
])
def test_lost_write_response_never_replays_the_statement(monkeypatch, sql):
    """A peer can commit a POST before disconnecting without a status line."""
    posts = {"n": 0}
    connections = {"n": 0}
    server, thread = _serve(posts, connections, drop_on={2})
    _point_at(server, monkeypatch)
    try:
        assert hrana_http.hrana_query("SELECT 1") == [("1",)]
        with pytest.raises(hrana_http.HranaHttpError):
            hrana_http.hrana_execute(sql)
        assert posts["n"] == 2, "unknown write outcome must not send another POST"
        assert connections["n"] == 1
    finally:
        _stop(server, thread)
