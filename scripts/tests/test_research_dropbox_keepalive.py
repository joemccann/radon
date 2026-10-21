"""PERF-019: DropboxClient must reuse one TCP socket per origin.

urllib.request.urlopen opens a fresh TCP/TLS handshake on every RPC and
download. Discovery lists many date folders, then continues pages, on
api.dropboxapi.com; the extraction worker then downloads PDFs from
content.dropboxapi.com. Sequential POSTs on one client must share a
keep-alive socket the way Hrana does (PERF-008 / PERF-009).
"""
from __future__ import annotations

import http.client
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from research.dropbox import DropboxClient, DropboxError


def _client():
    return DropboxClient({
        "folder_path": "/Joe McCann/Current",
        "root_namespace_id": "ns",
        "account_id": "acct",
        "folder_id": "id",
        "app_key": "key",
        "refresh_token": "tok",
    })


def _serve(connections, *, body=b'{"ok":true}', status=200, extra_headers=None):
    extra_headers = extra_headers or {}

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
            for name, value in extra_headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _stop(server, thread, client):
    closer = getattr(client, "close", None)
    if closer:
        closer()
    server.shutdown()
    thread.join(timeout=2)


def test_five_sequential_posts_use_one_tcp_connection():
    connections = {"n": 0}
    server, thread = _serve(connections)
    client = _client()
    host, port = server.server_address
    url = f"http://{host}:{port}/2/files/list_folder"
    try:
        for _ in range(5):
            data, _headers = client._post(url, b"{}", {"Content-Type": "application/json"}, 65536)
            assert data == b'{"ok":true}'
        assert connections["n"] == 1, (
            f"TCP connections {connections['n']} for 5 sequential Dropbox POSTs"
        )
    finally:
        _stop(server, thread, client)


def test_http_error_status_is_preserved_on_the_pooled_socket():
    connections = {"n": 0}
    server, thread = _serve(connections, body=b'{"error":"conflict"}', status=409)
    client = _client()
    host, port = server.server_address
    url = f"http://{host}:{port}/2/files/list_folder"
    try:
        try:
            client._post(url, b"{}", {"Content-Type": "application/json"}, 65536)
            raised = False
        except DropboxError as exc:
            raised = True
            assert exc.status == 409
        assert raised
    finally:
        _stop(server, thread, client)
