"""PERF-031: BPI Yahoo spark/chart GETs must reuse one TCP socket per thread.

`_fetch_spark_chunk` and `_fetch_yahoo_daily` used urllib.request.urlopen,
which opens a fresh TCP (and TLS in production) handshake on every request.
The nightly oneshot walks ~2,600 NDX/SPX/RUT members in 20-symbol spark
chunks (~130 sequential GETs on the full-universe path) plus chart fallback,
and `ma_ratio_scan` / `rsi_oversold_scan` share the same helper. Sequential
GETs on one thread must share a keep-alive socket the way divyield
(PERF-028) already does.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import bpi_scan as bpi


def _serve(connections, *, body=b'{}', status=200):
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
    closer = getattr(bpi, "close_http_session", None)
    if closer:
        closer()
    server.shutdown()
    thread.join(timeout=2)


def test_five_sequential_yahoo_gets_use_one_tcp_connection():
    connections = {"n": 0}
    server, thread = _serve(connections)
    host, port = server.server_address
    url = f"http://{host}:{port}/spark"
    try:
        for _ in range(5):
            assert bpi._yahoo_get_json(url) == {}
        assert connections["n"] == 1, (
            f"TCP connections {connections['n']} for 5 sequential BPI GETs "
            "(want 1 keep-alive session, not one urlopen per spark chunk)"
        )
    finally:
        _stop(server, thread)
