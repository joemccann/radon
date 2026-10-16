"""Unit tests for ``scripts/api/db_http.py`` — the bounded hrana access layer
that replaces sync libsql in the FastAPI process (DUR-09).

Pins the contract the routes depend on:
  - rows come back as positional tuples (drop-in for cursor.fetchall())
  - args encode per the hrana wire format (integers as strings, None → null)
  - every failure mode raises DbHttpError (callers own the fallback)
  - the socket timeout actually reaches urlopen
"""
from __future__ import annotations

import io
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from api import db_http  # noqa: E402


def _pipeline_body(cols: list[str], rows: list[list[dict]]) -> dict:
    return {
        "results": [
            {
                "type": "ok",
                "response": {
                    "type": "execute",
                    "result": {"cols": [{"name": c} for c in cols], "rows": rows},
                },
            },
            {"type": "ok", "response": {"type": "close"}},
        ]
    }


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv("TURSO_DB_URL", "libsql://example.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "token")


class TestArgEncoding:
    def test_none_encodes_as_null(self):
        assert db_http._encode_arg(None) == {"type": "null"}

    def test_int_encodes_as_string_valued_integer(self):
        # hrana requires integer values as strings on the wire.
        assert db_http._encode_arg(180) == {"type": "integer", "value": "180"}

    def test_float_encodes_as_float(self):
        assert db_http._encode_arg(1.5) == {"type": "float", "value": 1.5}

    def test_str_encodes_as_text(self):
        assert db_http._encode_arg("2026-03-14") == {"type": "text", "value": "2026-03-14"}


class TestHranaExecute:
    def test_decodes_rows_as_tuples(self, creds):
        body = _pipeline_body(
            ["date", "index_value"],
            [
                [{"type": "text", "value": "2026-06-10"}, {"type": "float", "value": 1.05}],
                [{"type": "text", "value": "2026-06-11"}, {"type": "integer", "value": "2"}],
                [{"type": "text", "value": "2026-06-12"}, {"type": "null"}],
            ],
        )
        with patch.object(db_http, "_post_pipeline", return_value=body):
            rows = db_http.hrana_execute("SELECT date, index_value FROM t")
        assert rows == [("2026-06-10", 1.05), ("2026-06-11", 2), ("2026-06-12", None)]

    def test_missing_creds_raise(self, monkeypatch):
        monkeypatch.delenv("TURSO_DB_URL", raising=False)
        monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
        with pytest.raises(db_http.DbHttpError, match="not configured"):
            db_http.hrana_execute("SELECT 1")

    def test_statement_error_raises(self, creds):
        body = {
            "results": [
                {"type": "error", "error": {"message": "no such table: nope"}},
            ]
        }
        with patch.object(db_http, "_post_pipeline", return_value=body):
            with pytest.raises(db_http.DbHttpError, match="no such table"):
                db_http.hrana_execute("SELECT * FROM nope")

    def test_transport_error_wrapped_in_db_http_error(self, creds):
        with patch.object(db_http, "_post_pipeline", side_effect=TimeoutError("timed out")):
            with pytest.raises(db_http.DbHttpError, match="TimeoutError"):
                db_http.hrana_execute("SELECT 1")

    def test_timeout_and_args_reach_the_wire(self, creds):
        seen: dict = {}

        def fake_urlopen(req, timeout=None):
            seen["timeout"] = timeout
            seen["url"] = req.full_url
            seen["payload"] = json.loads(req.data.decode("utf-8"))
            seen["auth"] = req.get_header("Authorization")
            return _FakeResponse(json.dumps(_pipeline_body(["n"], [])).encode("utf-8"))

        with patch.object(db_http.urllib.request, "urlopen", fake_urlopen):
            db_http.hrana_execute("SELECT ? AS n", (42,), timeout=2.5)

        assert seen["timeout"] == 2.5
        assert seen["url"] == "https://example.turso.io/v2/pipeline"
        assert seen["auth"] == "Bearer token"
        stmt = seen["payload"]["requests"][0]["stmt"]
        assert stmt["sql"] == "SELECT ? AS n"
        assert stmt["args"] == [{"type": "integer", "value": "42"}]

    def test_default_timeout_is_bounded(self):
        assert 0 < db_http.HRANA_TIMEOUT_S <= 10


class TestHranaExecuteMany:
    def test_one_post_carries_every_statement_then_close(self, creds):
        posts: list = []

        def fake_urlopen(req, timeout=None):
            posts.append(json.loads(req.data.decode("utf-8")))
            ok = _pipeline_body(["n"], [[{"type": "integer", "value": "1"}]])["results"][0]
            err = {"type": "error", "error": {"message": "no such table: nope"}}
            close = {"type": "ok", "response": {"type": "close"}}
            return _FakeResponse(json.dumps({"results": [ok, err, close]}).encode("utf-8"))

        with patch.object(db_http.urllib.request, "urlopen", fake_urlopen):
            first, second = db_http.hrana_execute_many(
                [("SELECT 1 AS n", ()), ("SELECT * FROM nope WHERE x = ?", ("a",))]
            )

        assert len(posts) == 1
        assert [r["type"] for r in posts[0]["requests"]] == ["execute", "execute", "close"]
        assert posts[0]["requests"][1]["stmt"]["args"] == [{"type": "text", "value": "a"}]
        assert first == [(1,)]
        assert isinstance(second, db_http.DbHttpError)
        assert "no such table" in str(second)

    def test_transport_error_raises(self, creds):
        with patch.object(db_http, "_post_statements", side_effect=TimeoutError("timed out")):
            with pytest.raises(db_http.DbHttpError, match="TimeoutError"):
                db_http.hrana_execute_many([("SELECT 1", ())])

    def test_missing_creds_raise(self, monkeypatch):
        monkeypatch.delenv("TURSO_DB_URL", raising=False)
        with pytest.raises(db_http.DbHttpError, match="not configured"):
            db_http.hrana_execute_many([("SELECT 1", ())])

    def test_statement_count_is_bounded(self, creds):
        with pytest.raises(ValueError):
            db_http.hrana_execute_many([])


class TestHranaKeepAlive:
    """PERF-008: five sequential pipelines on one thread must share one TCP
    connection. urllib.request.urlopen opens a new socket per POST, so each
    /health/lite Turso probe (every 5s) and every API Hrana call paid a fresh
    handshake. Probe TLS p50 to Turso is ~12ms on top of the query.
    """

    def test_five_sequential_pipelines_use_one_tcp_connection(self, monkeypatch):
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
        try:
            for _ in range(5):
                assert db_http.hrana_execute("SELECT 1") == []
            assert connections["n"] == 1, (
                f"TCP connections {connections['n']} for 5 sequential pipelines"
            )
        finally:
            db_http._close_keepalive()
            server.shutdown()
            thread.join(timeout=2)

    def test_oversized_body_rejects_and_leaves_the_next_pipeline_usable(self, monkeypatch):
        """A body past the cap must not be parsed as a prefix, and the unread
        tail must not fail the next pipeline on this thread. The keep-alive
        reader used to stop at the cap, return the prefix, and leave the
        socket in Request-sent so the following call never left the process.
        """
        cap = db_http._MAX_RESPONSE_BYTES
        over = b"X" * (cap + 1)
        ok = json.dumps(_pipeline_body(
            ["value"],
            [[{"type": "text", "value": "kept"}]],
        )).encode("utf-8")
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
        db_http._close_keepalive()
        try:
            with pytest.raises(db_http.DbHttpError, match="exceeds limit"):
                db_http.hrana_execute("SELECT 1")
            assert db_http.hrana_execute("SELECT 1") == [("kept",)]
            assert posts["n"] == 2
        finally:
            db_http._close_keepalive()
            server.shutdown()
            thread.join(timeout=2)

def test_oversized_response_does_not_abandon_the_next_transaction(monkeypatch):
    """The transaction cap must see the real body, and the next commit must not be abandoned."""
    posts: list[int] = []
    ok_tx = json.dumps({
        "results": [{
            "type": "ok",
            "response": {
                "type": "batch",
                "result": {
                    "step_results": [{}, {}, {"type": "ok"}],
                    "step_errors": [None, None, None],
                },
            },
        }]
    }).encode("utf-8")
    huge = b'{"overflow":"' + (b"x" * (db_http._MAX_RESPONSE_BYTES + 64))

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            posts.append(length)
            payload = huge if len(posts) == 1 else ok_tx
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
    try:
        with pytest.raises(db_http.DbHttpError):
            db_http.hrana_transaction([("SELECT big", ())])
        assert len(posts) == 1, f"the rejected transaction issued {len(posts)} pipelines"
        db_http.hrana_transaction([("SELECT 1", ())])
        assert len(posts) == 2, (
            f"the recovered transaction saw {len(posts)} pipelines; "
            "an abandoned follow-up must not be sent"
        )
    finally:
        db_http._close_keepalive()
        server.shutdown()
        thread.join(timeout=2)
