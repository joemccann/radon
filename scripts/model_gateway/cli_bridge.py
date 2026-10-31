"""Private per-agent native HTTP adapter; provider authentication stays in the gateway."""
from __future__ import annotations

import hmac
import http.server
import json
import secrets
import select
import socket
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .client import request

MAX_REQUEST_BYTES = 16 * 1024 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
NATIVE_PATHS = {'/v1/messages': 'messages', '/v1/chat/completions': 'chat_completions', '/v1/responses': 'responses'}

@dataclass(frozen=True)
class BridgeHandle:
    url: str
    token: str = field(repr=False)


@contextmanager
def bridge(*, gateway=request, timeout_seconds=120):
    token = secrets.token_urlsafe(32)
    stopping = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.0'
        def log_message(self, *_args):
            pass

        def reply(self, status, body, content_type='application/json'):
            raw = body.encode() if isinstance(body, str) else json.dumps(body, separators=(',', ':')).encode()
            if len(raw) > MAX_RESPONSE_BYTES:
                status, raw, content_type = 502, b'{"error":{"message":"Model response exceeds bridge limit"}}', 'application/json'
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def authorize(self):
            if self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}':
                self.reply(403, {'error': {'message': 'Invalid local model bridge host'}})
                return False
            value = self.headers.get('Authorization', '')
            if not hmac.compare_digest(value, 'Bearer ' + token):
                self.reply(401, {'error': {'message': 'Local model bridge authentication required'}})
                return False
            return True

        def cancelled(self):
            if stopping.is_set():
                return True
            try:
                ready, _, _ = select.select([self.connection], [], [], 0)
                return bool(ready) and not self.connection.recv(1, socket.MSG_PEEK)
            except OSError:
                return True

        def do_GET(self):
            if not self.authorize():
                return
            if self.path == '/':
                self.reply(200, {'status': 'ok'})
                return
            if self.path != '/v1/models':
                self.reply(404, {'error': {'message': 'Unknown local model operation'}})
                return
            try:
                catalog = gateway('models', {}, timeout_seconds=timeout_seconds, cancelled=self.cancelled)
                models = catalog.get('models', [])
                data = [{'id': row['id'], 'model': row['id'], 'object': 'model', 'owned_by': 'radon',
                         'name': row.get('label', row['id']), 'api_backend': 'chat_completions'} for row in models]
                self.reply(200, {'object': 'list', 'data': data})
            except Exception:
                self.reply(503, {'error': {'message': 'Model gateway is unavailable'}})

        def do_POST(self):
            if not self.authorize():
                return
            route = urlsplit(self.path)
            protocol = NATIVE_PATHS.get(route.path)
            if not protocol or route.query not in {'', 'beta=true'}:
                self.reply(404, {'error': {'message': 'Unknown local model operation'}})
                return
            try:
                if self.headers.get('Transfer-Encoding'):
                    raise ValueError('Chunked requests are not supported')
                length = int(self.headers.get('Content-Length', '-1'))
                if not 0 <= length <= MAX_REQUEST_BYTES:
                    self.reply(413, {'error': {'message': 'Model request exceeds bridge limit'}})
                    return
                self.connection.settimeout(5)
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError('Truncated request')
                body = json.loads(raw)
                if not isinstance(body, dict) or not isinstance(body.get('stream', False), bool):
                    raise ValueError('Invalid native request')
            except (ValueError, OSError, UnicodeDecodeError):
                self.reply(400, {'error': {'message': 'Invalid local model request'}})
                return
            try:
                result = gateway('native', {'protocol': protocol, 'request': body, 'stream': bool(body.get('stream'))}, timeout_seconds=timeout_seconds, cancelled=self.cancelled)
                status, response, content_type = result['status'], result['body'], result['content_type']
                if not isinstance(status, int) or not 200 <= status <= 599 or not isinstance(response, (str, dict)) or content_type not in {'application/json', 'text/event-stream'}:
                    raise ValueError('Invalid native result')
                self.reply(status, response, content_type)
            except Exception:
                self.reply(503, {'error': {'message': 'Model gateway request failed'}})

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    server.block_on_close = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield BridgeHandle(f'http://127.0.0.1:{server.server_port}', token)
    finally:
        stopping.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
