"""PERF-009: job/watchdog Hrana transport must reuse one TCP socket per thread.

Sister of PERF-008 (api.db_http). Watchdog heartbeats and sequential
writer pipelines each opened a fresh urllib socket.
"""
from __future__ import annotations

import json
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
