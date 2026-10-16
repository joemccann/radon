"""A response above the hard ceiling must not wedge the thread's keep-alive socket.

Both bounded Turso transports (``scripts/db/hrana_http.py`` and
``scripts/api/db_http.py``) consume a response with ``resp.read(ceiling)``.
A body above the ceiling leaves ``http.client`` holding an unfinished
response on a socket that stays parked in the thread-local pool, so the next
request from that thread raises ``http.client.CannotSendRequest`` /
``ResponseNotReady`` -- neither of which is an ``OSError``, so the request
guard's one-shot reconnect never runs and that worker thread never writes
again for the life of the process.

Pins that an over-ceiling response fails as a bounded single-request error
and the NEXT request on the same thread still succeeds.

Loopback fixture only, synthetic bodies, no production Turso.
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from api import db_http  # noqa: E402
from db import hrana_http  # noqa: E402

_EMPTY_PIPELINE = json.dumps({
    "results": [
        {
            "type": "ok",
            "response": {"type": "execute", "result": {"cols": [], "rows": []}},
        },
        {"type": "ok", "response": {"type": "close"}},
    ]
}).encode("utf-8")


def _serve(bodies):
    """Loopback keep-alive server handing out ``bodies`` in order."""
    served = {"n": 0}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        timeout = 2

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            body = bodies[min(served["n"], len(bodies) - 1)]
            served["n"] += 1
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


@pytest.mark.parametrize(
    "module, error, query",
    [
        (hrana_http, "HranaHttpError", "hrana_query"),
        (db_http, "DbHttpError", "hrana_execute"),
    ],
    ids=["db.hrana_http", "api.db_http"],
)
def test_oversized_response_does_not_wedge_the_pooled_socket(
    monkeypatch, module, error, query
):
    oversized = b"x" * 1024
    server, thread = _serve([oversized, _EMPTY_PIPELINE])
    host, port = server.server_address
    monkeypatch.setenv("TURSO_DB_URL", f"http://{host}:{port}")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "token")
    monkeypatch.setenv("RADON_DB_TEST_WRITE_OK", "1")
    # Shrink the ceiling rather than serve megabytes; the truncation path is
    # identical at any ceiling. 512 still clears the 145-byte valid pipeline
    # the second request is served.
    monkeypatch.setattr(module, "_MAX_RESPONSE_BYTES", 512)
    run = getattr(module, query)
    try:
        with pytest.raises(getattr(module, error)):
            run("SELECT 1")
        # The wedge: this second call must not raise CannotSendRequest.
        assert run("SELECT 1") == []
    finally:
        module._close_keepalive()
        server.shutdown()
        thread.join(timeout=2)


@pytest.mark.parametrize(
    "module, query",
    [(hrana_http, "hrana_query"), (db_http, "hrana_execute")],
    ids=["db.hrana_http", "api.db_http"],
)
def test_a_pooled_socket_left_mid_response_reconnects_once(monkeypatch, module, query):
    """The one-shot reconnect must also fire for a non-OSError wire state.

    Independent of the ceiling fix: if a pooled socket is ever left with a
    request sent and no response read, ``http.client`` raises
    ``CannotSendRequest``, which is not an ``OSError``.
    """
    server, thread = _serve([_EMPTY_PIPELINE])
    host, port = server.server_address
    monkeypatch.setenv("TURSO_DB_URL", f"http://{host}:{port}")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "token")
    monkeypatch.setenv("RADON_DB_TEST_WRITE_OK", "1")
    run = getattr(module, query)
    try:
        assert run("SELECT 1") == []
        # Poison the pooled socket: request sent, response never consumed.
        module._local.conn.request("POST", "/", b"{}", {"Content-Length": "2"})
        assert run("SELECT 1") == []
    finally:
        module._close_keepalive()
        server.shutdown()
        thread.join(timeout=2)
