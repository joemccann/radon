"""PERF-047: DropboxWriter must reuse one TCP socket per origin.

urllib.request.urlopen opens a fresh TCP/TLS handshake on every token
refresh, identity RPC and files/upload. connect() is two POSTs to
api.dropboxapi.com; a MarketDesk cycle then keeps posting RPCs to that
origin (get_metadata on conflict) while uploads go to
content.dropboxapi.com. Sequential POSTs on one writer must share a
keep-alive socket the way the read client already does (PERF-019).
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from research.dropbox_writer import DropboxError
from research.dropbox_writer import DropboxWriter


def _writer():
    return DropboxWriter(
        app_key="key",
        refresh_token="tok",
        account_id="acct",
        namespace_id="ns",
    )


def _serve(connections, *, body=b'{"ok":true}', status=200):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        timeout = 1

        def setup(self):
            super().setup()
            connections["n"] += 1

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
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


def _stop(server, thread, writer):
    closer = getattr(writer, "close", None)
    if closer:
        closer()
    server.shutdown()
    thread.join(timeout=2)


def test_five_sequential_posts_use_one_tcp_connection():
    connections = {"n": 0}
    server, thread = _serve(connections)
    writer = _writer()
    host, port = server.server_address
    url = f"http://{host}:{port}/2/users/get_current_account"
    try:
        for _ in range(5):
            data = writer._post(url, b"{}", {"Content-Type": "application/json"}, limit=65536)
            assert data == b'{"ok":true}'
        assert connections["n"] == 1, (
            f"TCP connections {connections['n']} for 5 sequential DropboxWriter POSTs "
            "(want 1 keep-alive session, not one urlopen per RPC/upload)"
        )
    finally:
        _stop(server, thread, writer)


def test_http_error_status_is_preserved_on_the_pooled_socket():
    connections = {"n": 0}
    server, thread = _serve(connections, body=b'{"error_summary":"path/conflict/file"}', status=409)
    writer = _writer()
    host, port = server.server_address
    url = f"http://{host}:{port}/2/files/upload"
    try:
        try:
            writer._post(url, b"%PDF", {"Content-Type": "application/octet-stream"}, limit=65536)
            raised = False
        except DropboxError as exc:
            raised = True
            assert exc.status == 409
        assert raised
    finally:
        _stop(server, thread, writer)
