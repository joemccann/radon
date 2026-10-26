"""PERF-028: divyield Yahoo GETs must reuse one TCP socket per thread.

`_http_get` used urllib.request.urlopen, which opens a fresh TCP (and TLS
in production) handshake on every chart. The daily oneshot walks ~503
S&P 500 constituents with 6 workers, so that is hundreds of handshakes
to query1.finance.yahoo.com each cycle. Sequential GETs on one thread
must share a keep-alive socket the way Dropbox (PERF-019) and Hrana
(PERF-008) already do.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import fetch_divyield as fd


def _serve(connections, *, body=b'{"ok":true}', status=200):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        timeout = 1

        def setup(self):
            super().setup()
            connections["n"] += 1

        def do_GET(self):
            self.send_response(status)
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


def _stop(server, thread):
    closer = getattr(fd, "close_http_session", None)
    if closer:
        closer()
    server.shutdown()
    thread.join(timeout=2)


def test_five_sequential_chart_gets_use_one_tcp_connection():
    connections = {"n": 0}
    server, thread = _serve(connections)
    host, port = server.server_address
    url = f"http://{host}:{port}/chart"
    try:
        for _ in range(5):
            assert fd._http_get(url, fd.BROWSER_UA) == '{"ok":true}'
        assert connections["n"] == 1, (
            f"TCP connections {connections['n']} for 5 sequential divyield GETs "
            "(want 1 keep-alive session, not one urlopen per ticker)"
        )
    finally:
        _stop(server, thread)
