"""scripts/nvidia_rate_limit.py: one NVIDIA key, one pace, no hammering.

The build.nvidia.com key is shared by production and the nightly fx loops and
is limited per key (about 40 requests a minute). 2026-09-27: one 17-minute
documentation run logged 232 HTTP 429s because nothing paced fx's calls.
Every test uses a fake clock or a local fake upstream; nothing reaches NVIDIA.
"""
from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import nvidia_rate_limit as nrl


class FakeClock:
    def __init__(self, start: float = 1_000_000.0):
        self.now = start
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, secs: float) -> None:
        self.slept.append(secs)
        self.now += secs


def _limiter(tmp_path, clock, **kw):
    kw.setdefault("rpm", 30)
    return nrl.RateLimiter(tmp_path / "rate.json", clock=clock, sleep=clock.sleep, **kw)


# --- pacing -------------------------------------------------------------------

def test_requests_are_spaced_to_the_configured_rate(tmp_path):
    clock = FakeClock()
    lim = _limiter(tmp_path, clock, rpm=30)
    start = clock.now
    for _ in range(4):
        assert lim.acquire(max_wait=60) is True
    # 30/min = one every 2s: the 4th request goes out 6s after the first.
    assert clock.now - start == pytest.approx(6.0)


def test_the_pace_is_shared_through_the_state_file(tmp_path):
    clock = FakeClock()
    a = _limiter(tmp_path, clock, rpm=30)
    b = _limiter(tmp_path, clock, rpm=30)
    assert a.acquire(max_wait=60)
    start = clock.now
    assert b.acquire(max_wait=60)
    assert clock.now - start == pytest.approx(2.0), "a second process waits its turn"


def test_acquire_gives_up_instead_of_waiting_past_max_wait(tmp_path):
    clock = FakeClock()
    lim = _limiter(tmp_path, clock, rpm=6)  # one every 10s
    assert lim.acquire(max_wait=0)
    assert lim.acquire(max_wait=5) is False
    assert clock.now - 1_000_000.0 <= 5


def test_rpm_comes_from_the_environment_with_a_safe_default(tmp_path):
    assert nrl.RateLimiter.from_env({}, tmp_path / "s.json").rpm == nrl.DEFAULT_RPM
    assert nrl.DEFAULT_RPM < 40, "the default stays below NVIDIA's per-key limit"
    assert nrl.RateLimiter.from_env({"RADON_NVIDIA_RPM": "12"}, tmp_path / "s.json").rpm == 12
    assert nrl.RateLimiter.from_env({"RADON_NVIDIA_RPM": "junk"}, tmp_path / "s.json").rpm == nrl.DEFAULT_RPM
    assert nrl.RateLimiter.from_env({"RADON_NVIDIA_RPM": "0"}, tmp_path / "s.json").rpm == nrl.DEFAULT_RPM


def test_an_unwritable_state_path_falls_back_to_in_process_pacing(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    clock = FakeClock()
    lim = nrl.RateLimiter(blocker / "sub" / "rate.json", rpm=30, clock=clock, sleep=clock.sleep)
    assert lim.acquire(max_wait=60)
    assert lim.acquire(max_wait=60)
    assert clock.now - 1_000_000.0 == pytest.approx(2.0)


# --- 429 / 403 ----------------------------------------------------------------

def test_a_429_honours_retry_after_for_every_caller(tmp_path):
    clock = FakeClock()
    lim = _limiter(tmp_path, clock)
    other = _limiter(tmp_path, clock)
    lim.note_429(retry_after=45)
    start = clock.now
    assert other.acquire(max_wait=60)
    assert clock.now - start == pytest.approx(45.0)


def test_a_429_without_retry_after_uses_a_bounded_default(tmp_path):
    clock = FakeClock()
    lim = _limiter(tmp_path, clock)
    lim.note_429(retry_after=None)
    start = clock.now
    assert lim.acquire(max_wait=600)
    assert clock.now - start == pytest.approx(nrl.DEFAULT_COOLDOWN_SECS)
    lim.note_429(retry_after=99_999)
    start = clock.now
    assert lim.acquire(max_wait=99_999)
    assert clock.now - start == pytest.approx(nrl.MAX_COOLDOWN_SECS)


def test_persistent_429s_trip_the_key_and_write_the_trip_file(tmp_path):
    clock = FakeClock()
    lim = _limiter(tmp_path, clock, trip_after=3, trip_secs=900)
    lim.note_429(retry_after=1)
    lim.note_success()
    lim.note_429(retry_after=1)
    lim.note_429(retry_after=1)
    assert lim.tripped_until() == 0, "a success resets the streak"
    lim.note_429(retry_after=1)
    assert lim.tripped_until() == pytest.approx(clock.now + 900)
    assert int((tmp_path / "rate.json.tripped").read_text()) == int(clock.now + 900)
    assert lim.acquire(max_wait=60) is False, "no request goes out while tripped"


def test_a_403_trips_at_once_and_is_never_retried(tmp_path):
    clock = FakeClock()
    lim = _limiter(tmp_path, clock, auth_block_secs=600)
    lim.note_auth_failure(403)
    assert lim.tripped_until() == pytest.approx(clock.now + 600)
    assert (tmp_path / "rate.json.tripped").exists()
    assert lim.acquire(max_wait=60) is False


@pytest.mark.parametrize(
    "headers, expected",
    [
        ({"Retry-After": "7"}, 7.0),
        ({"retry-after": "2.5"}, 2.5),
        ({"Retry-After": "Wed, 30 Sep 2026 12:00:30 GMT"}, 30.0),
        ({"x-ratelimit-reset-requests": "1m30s"}, 90.0),
        ({"x-ratelimit-reset-requests": "500ms"}, 0.5),
        ({"x-ratelimit-reset": "12"}, 12.0),
        ({"Retry-After": "soon"}, None),
        ({}, None),
    ],
)
def test_retry_after_parsing(headers, expected):
    now = 1_790_769_600.0  # 2026-09-30T12:00:00Z
    got = nrl.retry_after_seconds(headers, now=now)
    assert got == (pytest.approx(expected) if expected is not None else None)


def test_error_snippets_are_short_and_secret_free():
    body = 'denied for Bearer nvapi-ABCDEFGHIJKLMNOP and csk-ABCDEFGHIJKLMNOPQRST ' + "x" * 500
    out = nrl.redact_snippet(body)
    assert "nvapi-ABCD" not in out and "csk-ABCD" not in out
    assert len(out) <= 200


# --- proxy --------------------------------------------------------------------

class Upstream:
    """Local fake of integrate.api.nvidia.com: scripted replies, recorded requests."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("content-length") or 0)
                outer.requests.append({"path": self.path, "headers": dict(self.headers),
                                       "body": self.rfile.read(n)})
                status, headers, body = outer.replies.pop(0) if outer.replies else (200, {}, b"{}")
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                if isinstance(body, list):  # streamed SSE chunks
                    self.send_header("transfer-encoding", "chunked")
                    self.end_headers()
                    for chunk in body:
                        self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                        self.wfile.flush()
                    self.wfile.write(b"0\r\n\r\n")
                    return
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def proxy(tmp_path):
    made = []

    def make(replies, **kw):
        up = Upstream(replies)
        kw.setdefault("rpm", 600)
        lim = nrl.RateLimiter(tmp_path / "rate.json", rpm=kw.pop("rpm"),
                              trip_after=kw.pop("trip_after", 5))
        srv = nrl.make_proxy(0, limiter=lim, upstream=up.url,
                             retries=kw.pop("retries", 2), max_wait=kw.pop("max_wait", 5),
                             idle_secs=0, **kw)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        made.append((up, srv))
        return up, srv, lim

    yield make
    for up, srv in made:
        srv.shutdown()
        srv.server_close()
        up.close()


def _post(srv, body=b'{"model":"m"}', path="/v1/chat/completions"):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
    conn.request("POST", path, body=body, headers={"authorization": "Bearer nvapi-TESTTESTTEST",
                                                     "content-type": "application/json"})
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp, data


def test_proxy_forwards_the_request_and_the_bearer_untouched(proxy):
    up, srv, _ = proxy([(200, {"content-type": "application/json"}, b'{"ok":1}')])
    resp, data = _post(srv)
    assert resp.status == 200 and json.loads(data) == {"ok": 1}
    assert up.requests[0]["path"] == "/v1/chat/completions"
    assert up.requests[0]["headers"]["authorization"] == "Bearer nvapi-TESTTESTTEST"
    assert up.requests[0]["body"] == b'{"model":"m"}'


def test_proxy_streams_sse_through(proxy):
    chunks = [b"data: {\"a\":1}\n\n", b"data: [DONE]\n\n"]
    up, srv, _ = proxy([(200, {"content-type": "text/event-stream"}, chunks)])
    resp, data = _post(srv)
    assert resp.status == 200
    assert data == b"".join(chunks)


def test_proxy_retries_a_429_after_retry_after_then_succeeds(proxy, capfd):
    up, srv, lim = proxy([(429, {"retry-after": "1"}, b'{"error":"Too Many Requests"}'),
                          (200, {}, b'{"ok":2}')])
    t0 = time.monotonic()
    resp, data = _post(srv)
    assert resp.status == 200 and json.loads(data) == {"ok": 2}
    assert time.monotonic() - t0 >= 0.9, "Retry-After was honoured before the retry"
    assert len(up.requests) == 2
    assert "status=429" in capfd.readouterr().err


def test_proxy_hands_back_a_429_after_bounded_retries(proxy):
    replies = [(429, {"retry-after": "0"}, b"slow down")] * 5
    up, srv, _ = proxy(replies, retries=1)
    resp, _ = _post(srv)
    assert resp.status == 429
    assert resp.getheader("retry-after") is not None
    assert len(up.requests) == 2, "one try plus one retry, never more"


def test_proxy_does_not_contact_nvidia_while_tripped(proxy):
    up, srv, lim = proxy([])
    lim.note_auth_failure(403)
    resp, data = _post(srv)
    assert resp.status == 429
    assert b"radon" in data
    assert up.requests == []


def test_proxy_passes_a_403_through_once_logs_it_loudly_and_trips(proxy, capfd):
    up, srv, lim = proxy([(403, {}, b'{"detail":"Authorization failed for nvapi-SECRETSECRETSECRET"}'),
                          (200, {}, b"{}")])
    resp, _ = _post(srv)
    assert resp.status == 403
    assert len(up.requests) == 1, "a 403 is never retried"
    assert lim.tripped_until() > time.time()
    err = capfd.readouterr().err
    assert "NVIDIA AUTHORIZATION FAILED" in err and "status=403" in err
    assert "Authorization failed" in err, "the body snippet is logged"
    assert "SECRETSECRET" not in err


def test_proxy_refuses_paths_outside_v1(proxy):
    up, srv, _ = proxy([])
    resp, _ = _post(srv, path="/admin")
    assert resp.status == 404
    assert up.requests == []


def test_proxy_health_endpoint(proxy):
    _, srv, _ = proxy([])
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    conn.request("GET", nrl.HEALTH_PATH)
    resp = conn.getresponse()
    assert resp.status == 200 and resp.read() == b"ok"
    conn.close()


def test_proxy_binds_loopback_only(proxy):
    _, srv, _ = proxy([])
    assert srv.server_address[0] == "127.0.0.1"


def test_proxy_exits_when_idle(tmp_path):
    lim = nrl.RateLimiter(tmp_path / "r.json", rpm=60)
    srv = nrl.make_proxy(0, limiter=lim, upstream="http://127.0.0.1:9", idle_secs=0.3)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    t.join(timeout=5)
    srv.server_close()
    assert not t.is_alive(), "an idle proxy shuts itself down"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_ensure_proxy_reuses_a_running_proxy(proxy, tmp_path):
    _, srv, _ = proxy([])
    port = srv.server_address[1]
    assert nrl.ensure_proxy(port, state=tmp_path / "r.json", log=tmp_path / "p.log",
                            rpm=20, spawn=lambda: pytest.fail("must not spawn")) == 0


def test_ensure_proxy_reports_failure_when_nothing_comes_up(tmp_path):
    port = _free_port()
    assert nrl.ensure_proxy(port, state=tmp_path / "r.json", log=tmp_path / "p.log",
                            rpm=20, spawn=lambda: None, wait_secs=0.3) == 1
