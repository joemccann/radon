#!/usr/bin/env python3
"""Pace every call made with the shared NVIDIA (build.nvidia.com) key.

The free tier is limited per key, about 40 requests a minute (NVIDIA sends no
rate-limit headers; the figure is the documented free-tier default). One key
serves production (the model ladder, knowledge embeddings, research parsing)
and the nightly fx loops on the Mac mini, so each host paces itself below it.

- ``RateLimiter`` spaces requests to ``RADON_NVIDIA_RPM`` (default 20) across
  every process on the host that shares its state file (flock + timestamps),
  honours Retry-After after a 429, and TRIPS the key after persistent 429s or
  any 401/403: while tripped no request is sent, and callers move on to their
  next rung instead of hammering. A trip also writes ``<state>.tripped`` (an
  epoch) so shell code can check it without Python.
- ``make_proxy`` / ``serve`` is a loopback-only forwarding proxy for fx, a
  closed binary that cannot be paced from inside: the runner points fx's
  ``nvidia-paced`` provider at it. It never holds the key; it forwards the
  caller's bearer to the fixed upstream.

Stdlib only: the runner installs this file root-owned and runs it with -I.
CLI: ``ensure-proxy`` (start a detached proxy unless one answers) and ``serve``.
"""
from __future__ import annotations

import argparse
import contextlib
import email.utils
import http.client
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

try:
    import fcntl
except ImportError:  # pragma: no cover - POSIX only in practice
    fcntl = None  # type: ignore[assignment]

DEFAULT_RPM = 20.0
DEFAULT_COOLDOWN_SECS = 30.0
MAX_COOLDOWN_SECS = 300.0
DEFAULT_TRIP_AFTER = 5
DEFAULT_TRIP_SECS = 900.0
DEFAULT_AUTH_BLOCK_SECS = 900.0
DEFAULT_PORT = 18431
UPSTREAM = "https://integrate.api.nvidia.com"
HEALTH_PATH = "/__radon_nvidia_proxy"

_SECRET_PATTERN = re.compile(
    r"(sk-[a-zA-Z0-9_-]{8,}|xai-[a-zA-Z0-9_-]{8,}|nvapi-[a-zA-Z0-9_-]{8,}|"
    r"csk-[a-zA-Z0-9_-]{8,}|Bearer\s+[a-zA-Z0-9._-]{8,})",
    re.IGNORECASE,
)
_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
    "trailer", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
    "accept-encoding",
}


def log(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] nvidia-rate {msg}",
          file=sys.stderr, flush=True)


def redact_snippet(text: Any, max_len: int = 200) -> str:
    """Short, credential-free one-line excerpt of an error body."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    msg = " ".join(_SECRET_PATTERN.sub("[redacted]", str(text or "")).split())
    return msg if len(msg) <= max_len else msg[: max_len - 3] + "..."


def _duration(value: str) -> float | None:
    value = value.strip()
    try:
        return float(value)
    except ValueError:
        pass
    parts = re.findall(r"(\d+(?:\.\d+)?)(ms|h|m|s)", value)
    if not parts or "".join(n + u for n, u in parts) != value:
        return None
    scale = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}
    return sum(float(n) * scale[u] for n, u in parts)


def retry_after_seconds(headers: Mapping[str, str] | None, now: float | None = None) -> float | None:
    """Seconds to wait from Retry-After (delta or HTTP-date) or x-ratelimit-reset*."""
    lowered = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    raw = lowered.get("retry-after")
    if raw is not None:
        secs = _duration(raw)
        if secs is not None:
            return max(0.0, secs)
        try:
            when = email.utils.parsedate_to_datetime(raw).timestamp()
        except (TypeError, ValueError, IndexError):
            return None
        return max(0.0, when - (time.time() if now is None else now))
    for key in ("x-ratelimit-reset-requests", "x-ratelimit-reset"):
        if key in lowered:
            secs = _duration(lowered[key])
            if secs is not None:
                return max(0.0, secs)
    return None


def _env_number(env: Mapping[str, str], name: str, default: float) -> float:
    try:
        value = float((env.get(name) or "").strip())
    except ValueError:
        return default
    return value if value > 0 else default


class RateLimiter:
    """Host-wide pacing plus 429 / 403 back-off for one NVIDIA key."""

    def __init__(
        self,
        path: Path | str | None,
        *,
        rpm: float = DEFAULT_RPM,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        trip_after: int = DEFAULT_TRIP_AFTER,
        trip_secs: float = DEFAULT_TRIP_SECS,
        auth_block_secs: float = DEFAULT_AUTH_BLOCK_SECS,
    ):
        self.path = Path(path) if path else None
        self.rpm = float(rpm) if rpm and rpm > 0 else DEFAULT_RPM
        self.clock = clock
        self.sleep = sleep
        self.trip_after = max(1, int(trip_after))
        self.trip_secs = trip_secs
        self.auth_block_secs = auth_block_secs
        self._mutex = threading.Lock()
        self._memory: dict[str, float] = {}
        if self.path is not None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            except OSError:
                self.path = None

    @classmethod
    def from_env(cls, env: Mapping[str, str], default_path: Path | str | None, **kw: Any) -> "RateLimiter":
        path = (env.get("RADON_NVIDIA_RATE_STATE") or "").strip() or default_path
        return cls(
            path,
            rpm=_env_number(env, "RADON_NVIDIA_RPM", DEFAULT_RPM),
            trip_after=int(_env_number(env, "RADON_NVIDIA_TRIP_AFTER_429S", DEFAULT_TRIP_AFTER)),
            trip_secs=_env_number(env, "RADON_NVIDIA_TRIP_SECS", DEFAULT_TRIP_SECS),
            auth_block_secs=_env_number(env, "RADON_NVIDIA_AUTH_BLOCK_SECS", DEFAULT_AUTH_BLOCK_SECS),
            **kw,
        )

    @contextlib.contextmanager
    def _state(self):
        with self._mutex:
            if self.path is None or fcntl is None:
                yield self._memory
                return
            try:
                lock = open(str(self.path) + ".lock", "a")
            except OSError:
                yield self._memory
                return
            with lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    state = json.loads(self.path.read_text())
                    if not isinstance(state, dict):
                        state = {}
                except (OSError, ValueError):
                    state = {}
                before = dict(state)
                yield state
                if state != before:
                    tmp = self.path.with_name(self.path.name + ".new")
                    try:
                        tmp.write_text(json.dumps(state))
                        os.chmod(tmp, 0o600)
                        os.replace(tmp, self.path)
                    except OSError:
                        self._memory.update(state)

    def _trip(self, state: dict, until: float) -> None:
        state["tripped_until"] = max(float(state.get("tripped_until", 0)), until)
        if self.path is not None:
            with contextlib.suppress(OSError):
                self.path.with_name(self.path.name + ".tripped").write_text(
                    f"{int(state['tripped_until'])}\n")

    def acquire(self, max_wait: float) -> bool:
        """Reserve the next send slot, waiting at most ``max_wait`` seconds."""
        interval = 60.0 / self.rpm
        deadline = self.clock() + max(0.0, max_wait)
        while True:
            with self._state() as state:
                now = self.clock()
                tripped = float(state.get("tripped_until", 0))
                ready = max(float(state.get("next_at", 0)), float(state.get("cooldown_until", 0)), tripped)
                if ready <= now:
                    state["next_at"] = now + interval
                    return True
            if tripped > now or ready > deadline:
                return False
            self.sleep(max(0.01, min(ready - now, 5.0)))

    def note_success(self) -> None:
        with self._state() as state:
            if state.get("consecutive_429"):
                state["consecutive_429"] = 0

    def note_429(self, retry_after: float | None) -> None:
        wait = DEFAULT_COOLDOWN_SECS if retry_after is None else retry_after
        wait = min(max(wait, 1.0), MAX_COOLDOWN_SECS)
        with self._state() as state:
            now = self.clock()
            state["cooldown_until"] = max(float(state.get("cooldown_until", 0)), now + wait)
            state["consecutive_429"] = int(state.get("consecutive_429", 0)) + 1
            state["last_429_at"] = now
            if state["consecutive_429"] >= self.trip_after:
                self._trip(state, now + self.trip_secs)

    def note_auth_failure(self, status: int) -> None:
        with self._state() as state:
            now = self.clock()
            state["last_auth_failure"] = {"at": now, "status": int(status)}
            self._trip(state, now + self.auth_block_secs)

    def tripped_until(self) -> float:
        with self._state() as state:
            until = float(state.get("tripped_until", 0))
        return until if until > self.clock() else 0


_SHARED: dict[str, RateLimiter] = {}
_SHARED_LOCK = threading.Lock()


def shared_limiter(env: Mapping[str, str] | None = None) -> RateLimiter:
    """Process-wide limiter for production callers (one per state path)."""
    env = os.environ if env is None else env
    home = (env.get("HOME") or "").strip() or str(Path.home())
    path = (env.get("RADON_NVIDIA_RATE_STATE") or "").strip() or str(Path(home) / ".radon" / "nvidia-rate.json")
    with _SHARED_LOCK:
        if path not in _SHARED:
            _SHARED[path] = RateLimiter.from_env(env, path)
        return _SHARED[path]


# --- proxy for fx ----------------------------------------------------------------

class _Proxy(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: Any

    def log_message(self, *args: Any) -> None:  # the proxy logs its own lines
        pass

    def do_GET(self) -> None:
        if self.path == HEALTH_PATH:
            self._reply(200, b"ok", "text/plain")
            return
        self._forward(b"")

    def do_POST(self) -> None:
        n = int(self.headers.get("content-length") or 0)
        self._forward(self.rfile.read(n) if n else b"")

    def _reply(self, status: int, body: bytes, ctype: str = "application/json",
               extra: Mapping[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("content-type", ctype)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _local_429(self) -> None:
        srv = self.server
        wait = max(1, int(max(srv.limiter.tripped_until() - time.time(), DEFAULT_COOLDOWN_SECS)))
        body = json.dumps({"error": {"message": "radon nvidia proxy: rate budget exhausted, not sent",
                                     "type": "rate_limited"}}).encode()
        log(f"local 429: nothing sent upstream, retry-after={wait}s")
        self._reply(429, body, extra={"retry-after": str(wait)})

    def _forward(self, body: bytes) -> None:
        srv = self.server
        srv.touch(+1)
        try:
            if not self.path.startswith("/v1/"):
                self._reply(404, b'{"error":"not found"}')
                return
            with srv.slots:
                self._send_upstream(body)
        finally:
            srv.touch(-1)

    def _send_upstream(self, body: bytes) -> None:
        srv = self.server
        headers = {k: v for k, v in self.headers.items() if k.lower() not in _HOP}
        headers["content-length"] = str(len(body))
        headers["accept-encoding"] = "identity"
        attempt = 0
        while True:
            if not srv.limiter.acquire(srv.max_wait):
                self._local_429()
                return
            conn = srv.connect()
            try:
                conn.request(self.command, self.path, body=body or None, headers=headers)
                resp = conn.getresponse()
            except OSError as exc:
                conn.close()
                log(f"upstream unreachable: {type(exc).__name__}")
                self._reply(502, b'{"error":"nvidia upstream unreachable"}')
                return
            status = resp.status
            if status == 200 or status < 400:
                srv.limiter.note_success()
                self._stream(resp)
                conn.close()
                return
            data = resp.read(65536)
            conn.close()
            snippet = redact_snippet(data)
            if status == 429:
                retry_after = retry_after_seconds(dict(resp.getheaders()))
                srv.limiter.note_429(retry_after)
                attempt += 1
                log(f"status=429 retry_after={retry_after} attempt={attempt} body={snippet!r}")
                if attempt <= srv.retries and not srv.limiter.tripped_until():
                    continue
                self._reply(429, data, resp.getheader("content-type") or "application/json",
                            {"retry-after": str(int(retry_after or DEFAULT_COOLDOWN_SECS))})
                return
            if status in (401, 403):
                srv.limiter.note_auth_failure(status)
                log(f"NVIDIA AUTHORIZATION FAILED status={status}: key refused or temporarily "
                    f"blocked; not retried, nvidia paused for {int(srv.limiter.auth_block_secs)}s "
                    f"body={snippet!r}")
            else:
                log(f"status={status} body={snippet!r}")
            self._reply(status, data, resp.getheader("content-type") or "application/json")
            return

    def _stream(self, resp: http.client.HTTPResponse) -> None:
        self.send_response(resp.status)
        length = resp.getheader("content-length")
        for k, v in resp.getheaders():
            if k.lower() not in _HOP:
                self.send_header(k, v)
        if length is not None:
            self.send_header("content-length", length)
        else:
            self.send_header("transfer-encoding", "chunked")
        self.end_headers()
        while True:
            chunk = resp.read1(65536)
            if not chunk:
                break
            self.wfile.write(chunk if length is not None else b"%x\r\n%s\r\n" % (len(chunk), chunk))
            self.wfile.flush()
        if length is None:
            self.wfile.write(b"0\r\n\r\n")


def make_proxy(
    port: int,
    *,
    limiter: RateLimiter,
    upstream: str = UPSTREAM,
    retries: int = 2,
    max_wait: float = 120.0,
    concurrency: int = 2,
    idle_secs: float = 900.0,
    timeout: float = 600.0,
) -> ThreadingHTTPServer:
    """Loopback proxy to ``upstream``; ``idle_secs`` > 0 shuts it down when unused."""
    srv = _Proxy(("127.0.0.1", port), _Handler)
    parts = urlsplit(upstream)
    conn_cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    srv.limiter = limiter
    srv.retries = max(0, int(retries))
    srv.max_wait = max_wait
    srv.slots = threading.BoundedSemaphore(max(1, int(concurrency)))
    srv.connect = lambda: conn_cls(parts.hostname, parts.port, timeout=timeout)
    state = {"inflight": 0, "last": time.monotonic()}
    guard = threading.Lock()

    def touch(delta: int) -> None:
        with guard:
            state["inflight"] += delta
            state["last"] = time.monotonic()

    srv.touch = touch
    if idle_secs > 0:
        def reaper() -> None:
            while True:
                time.sleep(min(idle_secs, 5.0))
                with guard:
                    idle = state["inflight"] == 0 and time.monotonic() - state["last"] >= idle_secs
                if idle:
                    log(f"idle {idle_secs:.0f}s, exiting")
                    srv.shutdown()
                    return
        threading.Thread(target=reaper, daemon=True).start()
    return srv


def _healthy(port: int) -> bool:
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        conn.request("GET", HEALTH_PATH)
        ok = conn.getresponse().read() == b"ok"
        conn.close()
        return ok
    except OSError:
        return False


def _spawn_daemon(argv: list[str], log_path: Path) -> None:
    """Double-fork a detached ``serve`` (own session, cwd /, output to log_path)."""
    pid = os.fork()
    if pid:
        os.waitpid(pid, 0)
        return
    try:
        os.setsid()
        if os.fork():
            os._exit(0)
        os.chdir("/")
        os.umask(0o077)
        fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        null = os.open(os.devnull, os.O_RDONLY)
        os.dup2(null, 0)
        os.dup2(fd, 1)
        os.dup2(fd, 2)
        os.execv(sys.executable, [sys.executable, "-I", os.path.abspath(__file__), *argv])
    finally:
        os._exit(127)


def ensure_proxy(
    port: int,
    *,
    state: Path | str,
    log: Path | str,
    rpm: float,
    spawn: Callable[[], None] | None = None,
    wait_secs: float = 5.0,
) -> int:
    """0 once a proxy answers on ``port`` (starting one if needed), else 1."""
    if _healthy(port):
        return 0
    if spawn is None:
        argv = ["serve", "--port", str(port), "--state", str(state), "--rpm", str(rpm)]
        spawn = lambda: _spawn_daemon(argv, Path(log))  # noqa: E731
    spawn()
    deadline = time.monotonic() + wait_secs
    while time.monotonic() < deadline:
        if _healthy(port):
            return 0
        time.sleep(0.1)
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("serve", "ensure-proxy"):
        p = sub.add_parser(name)
        p.add_argument("--port", type=int, default=DEFAULT_PORT)
        p.add_argument("--state", required=True)
        p.add_argument("--rpm", type=float, default=DEFAULT_RPM)
        p.add_argument("--log", default=os.devnull)
    args = ap.parse_args(argv)
    env = dict(os.environ, RADON_NVIDIA_RPM=str(args.rpm))
    if args.cmd == "ensure-proxy":
        return ensure_proxy(args.port, state=args.state, log=args.log, rpm=args.rpm)
    limiter = RateLimiter.from_env(env, args.state)
    try:
        srv = make_proxy(args.port, limiter=limiter)
    except OSError as exc:  # another proxy won the port
        log(f"not serving on {args.port}: {exc.strerror or exc}")
        return 0
    log(f"serving 127.0.0.1:{args.port} rpm={limiter.rpm:g}")
    srv.serve_forever()
    srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
