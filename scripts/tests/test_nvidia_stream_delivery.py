"""T-526: the proxy must deliver progress before the provider finishes.

A completed-body equality check also passes a proxy that buffers all output.
This local provider cannot finish until the client has received its first event.
"""
from __future__ import annotations

import http.client
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import nvidia_rate_limit as nrl


@pytest.mark.parametrize("chunked", [True, False], ids=["chunked", "content-length"])
def test_client_receives_first_event_before_provider_can_finish(chunked):
    first = b'data: {"delta":"first"}\n\n'
    last = b'data: [DONE]\n\n'
    release = threading.Event()
    upstream_finished = threading.Event()

    class Provider(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("content-length", "0")))
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            if chunked:
                self.send_header("transfer-encoding", "chunked")
            else:
                self.send_header("content-length", str(len(first) + len(last)))
            self.end_headers()

            def emit(data):
                self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data) if chunked else data)
                self.wfile.flush()

            emit(first)
            # Safety bound only; ordering is controlled by client acknowledgement.
            if release.wait(15):
                emit(last)
                if chunked:
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                upstream_finished.set()
            self.close_connection = True

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    proxy = nrl.make_proxy(
        0, limiter=nrl.RateLimiter(None),
        upstream=f"http://127.0.0.1:{upstream.server_address[1]}", idle_secs=0,
    )
    threads = [threading.Thread(target=server.serve_forever) for server in (upstream, proxy)]
    for thread in threads:
        thread.start()
    client = http.client.HTTPConnection("127.0.0.1", proxy.server_address[1], timeout=5)
    try:
        client.request("POST", "/v1/chat/completions", body=b"{}")
        response = client.getresponse()
        assert response.status == 200
        assert response.getheader("content-type") == "text/event-stream"
        assert response.read(len(first)) == first
        assert not upstream_finished.is_set()
        release.set()
        assert response.read() == last
        assert upstream_finished.wait(5)
    finally:
        release.set()
        client.close()
        for server in (proxy, upstream):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(5)
            assert not thread.is_alive()
