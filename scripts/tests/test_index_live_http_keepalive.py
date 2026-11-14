"""PERF-055: Cboe delayed quotes and IB pool bars must reuse one TCP socket.

`live_from_cboe` and `pool_daily_bars` used `requests.get` / `requests.post`,
which open a fresh TCP (and TLS, on the Cboe CDN) handshake per symbol.
`radon-vixts` (3 legs) and `radon-panic-index` (4 legs) sample every 5
minutes in ET trading hours; `radon-vix-model-spread` adds 3 more.
Sequential GETs or POSTs on one thread must share a keep-alive socket the
way divyield (PERF-028) and BPI (PERF-031) already do.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from utils import index_live

_QUOTE = json.dumps(
    {"data": {"current_price": 17.6, "last_trade_time": "2026-11-11T15:44:32"}}
).encode()
_BARS = json.dumps({"bars": [{"date": "2026-10-19", "close": 20.0}]}).encode()
_SYMBOLS = ("VIX", "VIX3M", "VVIX", "SKEW", "SPX")


def _serve(connections, *, get_body=_QUOTE, post_body=_BARS, status=200):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        timeout = 1

        def setup(self):
            super().setup()
            connections["n"] += 1

        def _reply(self, body):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._reply(get_body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            self._reply(post_body)

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _stop(server, thread):
    closer = getattr(index_live, "close_http_session", None)
    if closer:
        closer()
    server.shutdown()
    thread.join(timeout=2)


def test_five_sequential_cboe_gets_use_one_tcp_connection(monkeypatch):
    connections = {"n": 0}
    server, thread = _serve(connections)
    host, port = server.server_address
    monkeypatch.setattr(
        index_live,
        "LIVE_QUOTE_URL",
        f"http://{host}:{port}/quotes/_{{symbol}}.json",
    )
    try:
        live = index_live.live_from_cboe(_SYMBOLS)
        assert live is not None
        assert live["values"]["VIX"] == 17.6
        assert connections["n"] == 1, (
            f"TCP connections {connections['n']} for 5 sequential Cboe GETs "
            "(want 1 keep-alive session, not one requests.get per symbol)"
        )
    finally:
        _stop(server, thread)


def test_five_sequential_ib_posts_use_one_tcp_connection(monkeypatch):
    connections = {"n": 0}
    server, thread = _serve(connections)
    host, port = server.server_address
    monkeypatch.setattr(
        index_live,
        "POOL_BARS_URL",
        f"http://{host}:{port}/historical/bars",
    )
    now = datetime(2026, 10, 19, 19, 0, tzinfo=timezone.utc)
    try:
        live = index_live.live_from_ib(_SYMBOLS, now)
        assert live is not None
        assert live["values"]["VIX"] == 20.0
        assert connections["n"] == 1, (
            f"TCP connections {connections['n']} for 5 sequential IB POSTs "
            "(want 1 keep-alive session, not one requests.post per symbol)"
        )
    finally:
        _stop(server, thread)
