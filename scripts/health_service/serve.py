"""Standalone Radon health daemon HTTP server.

Runs as radon-health.service with NO dependency edge to radon-ib-gateway (or any
radon-* unit), so the documented cascade-stop
(feedback_systemd_cascade_stop_no_autorecover.md) can never take it down. It
probes every service from the OUTSIDE and never imports the trading stack.

Routes:
  GET /healthz  -> zero-I/O static 200 (the never-502 liveness pin)
  GET /status   -> isolated live probes + cached systemctl unit states; ALWAYS
                   200, degraded sources are body fields. Detail is trust-split:
                   proxied (public-edge) callers get the aggregate verdict only
                   unless they carry the shared bearer token.
"""
from __future__ import annotations

import hmac
import json
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from . import probes, turso_http
except ImportError:  # pragma: no cover - loose-module fallback
    import probes  # type: ignore
    import turso_http  # type: ignore


# --- config (env-overridable; defaults match the Hetzner VPS) ---
BIND = os.environ.get("RADON_HEALTH_BIND", "127.0.0.1")
PORT = int(os.environ.get("RADON_HEALTH_PORT", "8330"))
FASTAPI_LITE_URL = os.environ.get("RADON_HEALTH_FASTAPI_URL", "http://127.0.0.1:8321/health/lite")
RELAY = (os.environ.get("RADON_HEALTH_RELAY_HOST", "127.0.0.1"), int(os.environ.get("RADON_HEALTH_RELAY_PORT", "8765")))
NEXTJS = (os.environ.get("RADON_HEALTH_NEXTJS_HOST", "127.0.0.1"), int(os.environ.get("RADON_HEALTH_NEXTJS_PORT", "3000")))
IB_GATEWAY = (os.environ.get("RADON_HEALTH_IB_HOST", "127.0.0.1"), int(os.environ.get("RADON_HEALTH_IB_PORT", "4001")))
UNITS = os.environ.get(
    "RADON_HEALTH_UNITS",
    "radon-api.service radon-relay.service radon-monitor.service "
    "radon-nextjs.service radon-ib-gateway.service radon-newsfeed.service",
).split()
UNIT_REFRESH_SECS = float(os.environ.get("RADON_HEALTH_UNIT_REFRESH", "5"))
SERVICE_HEALTH_TTL = float(os.environ.get("RADON_HEALTH_SH_TTL", "5"))
SERVICE_HEALTH_TIMEOUT = float(os.environ.get("RADON_HEALTH_SH_TIMEOUT", "2.5"))

# --- /status detail gate ---
# Caddy reverse_proxies app.radon.run/edge-health/status here with no auth of its
# own, so the full body (IB auth_state incl. awaiting_2fa, the radon-* unit
# inventory, service_health last_error text carrying tickers / IB order ids) was
# readable by any anonymous internet client — the same data the FastAPI perimeter
# denies to untrusted callers, and a live "a 2FA push is pending right now"
# signal for a phishing operator.
STATUS_TOKEN = os.environ.get("RADON_HEALTH_STATUS_TOKEN", "").strip()
PUBLIC_STATUS_FIELDS = ("schema_version", "ok", "overall_state", "generated_at",
                        "health_service")
# A proxy can only ADD these to a request; a client cannot strip what Caddy
# stamps. Treating their presence as untrusted therefore fails safe, and the
# daemon stays protected even if the Caddy marker has not been rolled out yet.
PROXY_MARKER_HEADERS = ("X-Radon-Public-Edge", "X-Forwarded-For", "X-Forwarded-Host",
                        "X-Forwarded-Proto", "X-Real-Ip", "Forwarded")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_probes() -> dict:
    """Probe every service concurrently with bounded timeouts. Each probe is
    isolated — one failure becomes a labelled state, never an exception that
    fails the whole response."""
    tasks = {
        "radon-api": lambda: probes.probe_http_json(FASTAPI_LITE_URL, timeout=2.0),
        "radon-relay": lambda: probes.probe_tcp(*RELAY),
        "radon-nextjs": lambda: probes.probe_tcp(*NEXTJS),
        "ib-gateway": lambda: probes.probe_tcp(*IB_GATEWAY),
    }
    # REL-194 (R-554): a hung-but-alive radon-mcp was invisible. Dependency
    # probe (never collapses the edge to down); enabled only where the MCP
    # runs, via the unit's RADON_MCP_PROBE_URL.
    mcp_url = os.environ.get("RADON_MCP_PROBE_URL", "")
    if mcp_url:
        tasks["radon-mcp"] = lambda: probes.probe_http_alive(mcp_url, timeout=2.0)
    results: dict = {}
    with ThreadPoolExecutor(max_workers=len(tasks)) as ex:
        futures = {name: ex.submit(fn) for name, fn in tasks.items()}
        for name, fut in futures.items():
            try:
                results[name] = fut.result(timeout=6)
            except Exception:
                results[name] = {"state": "unknown", "detail": "probe_error"}
    return results


class ProbeCache:
    """Refresh live probes in one background sweep, never per HTTP request."""

    def __init__(self, fetch_fn=run_probes, interval: float = 5.0):
        self._fetch_fn = fetch_fn
        self._interval = interval
        self._lock = threading.Lock()
        self._value: dict = {}
        # `refresh_once` swallows every exception and keeps the last value, so
        # without a timestamp a dead `health-probe-cache` thread served an
        # hours-old probe dict as current — and `aggregate_state` folded it in
        # unconditionally, unlike unit evidence, which it already age-gates.
        # R-401.
        self._updated = None
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, name="health-probe-cache", daemon=True
        )

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            self.refresh_once()
            self._stop.wait(self._interval)

    def refresh_once(self):
        try:
            value = self._fetch_fn()
            if not isinstance(value, dict):
                return
            with self._lock:
                self._value = value
                self._updated = time.time()
        except Exception:
            pass

    def snapshot(self):
        """``(value, age_secs)`` — mirrors UnitStateCache. R-401."""
        with self._lock:
            age = None if self._updated is None else round(time.time() - self._updated, 1)
            return dict(self._value), age


class UnitStateCache:
    """Polls `systemctl show` on a background thread, NEVER on the request hot
    path — forking under an OOM/disk-full incident is exactly when you can't
    afford it. On failure it keeps the last value; staleness is exposed as age.
    """

    def __init__(self, units, interval: float = UNIT_REFRESH_SECS, timeout: float = 3.0):
        self._units = list(units)
        self._interval = interval
        self._timeout = timeout
        self._lock = threading.Lock()
        self._value: dict = {}
        self._updated = None
        # unit -> monotonic-ish wall clock when it was first seen not-`up`.
        # `aggregate_state` needs a DWELL, not a snapshot: without one a unit
        # that died two seconds ago and one failed for a week were the same
        # input, and the dependency suppression made the second edge-green
        # forever. R-382.
        self._non_up_since: dict = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="unit-state-cache", daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            self.refresh_once()
            self._stop.wait(self._interval)

    def refresh_once(self):
        if not self._units:
            return
        try:
            out = subprocess.run(
                ["systemctl", "show", *self._units,
                 "-p", "Id", "-p", "ActiveState", "-p", "SubState", "-p", "Result"],
                capture_output=True, text=True, timeout=self._timeout,
            )
            parsed = probes.parse_unit_states(out.stdout)
            if out.returncode != 0 or set(parsed) != set(self._units):
                return
            now = time.time()
            for uid, props in parsed.items():
                if props.get("state") == "up":
                    self._non_up_since.pop(uid, None)
                    props["non_up_secs"] = None
                else:
                    since = self._non_up_since.setdefault(uid, now)
                    props["non_up_secs"] = round(now - since, 1)
            for uid in list(self._non_up_since):
                if uid not in parsed:
                    self._non_up_since.pop(uid, None)
            with self._lock:
                self._value = parsed
                self._updated = now
        except Exception:
            pass  # keep last value; age reflects staleness

    def snapshot(self):
        with self._lock:
            age = None if self._updated is None else round(time.time() - self._updated, 1)
            return dict(self._value), age


def healthz_response():
    """Zero-I/O liveness pin. Structurally cannot 502 while the daemon serves."""
    return 200, {"ok": True}


def status_response(run_probes_fn, unit_cache, now_fn=_now_iso, service_health_cache=None,
                    external_probe_cache=None):
    """Always returns 200. A probe sweep that raises degrades health_service to
    'degraded' rather than failing the response. The Turso service_health and
    external_probe sections degrade to 'unknown'/None on any failure and never
    affect the response code."""
    health = "ok"
    probes_age = None
    try:
        probe_results = run_probes_fn()
        # ProbeCache.snapshot returns (value, age); a bare `run_probes` does not.
        if isinstance(probe_results, tuple):
            probe_results, probes_age = probe_results
    except Exception:
        probe_results, health = {}, "degraded"
    try:
        units, age = unit_cache.snapshot()
    except Exception:
        units, age = {}, None
    try:
        sh = service_health_cache.snapshot() if service_health_cache is not None else None
    except Exception:
        sh = {"state": "unknown", "detail": "cache_error", "rows": []}
    try:
        ep = external_probe_cache.snapshot() if external_probe_cache is not None else None
    except Exception:
        ep = None
    return 200, probes.build_status(probe_results, units, now_fn(),
                                    health_service=health, units_age_secs=age,
                                    service_health=sh, external_probe=ep,
                                    probes_age_secs=probes_age)


def public_status_payload(payload: dict) -> dict:
    """The unauthenticated view: exactly the aggregate the off-box prober
    validates, plus the two cheap context fields. No unit names, no
    service_health error text, no nested broker state."""
    return {key: payload[key] for key in PUBLIC_STATUS_FIELDS if key in payload}


def is_proxied_request(headers) -> bool:
    return any(headers.get(name) for name in PROXY_MARKER_HEADERS)


def bearer_token_matches(authorization, expected: str) -> bool:
    if not expected:
        return False
    scheme, _, token = (authorization or "").strip().partition(" ")
    if scheme.lower() != "bearer":
        return False
    return hmac.compare_digest(token.strip(), expected)


def status_detail_authorized(headers, token: str) -> bool:
    """On-box callers (watchdogs, deploy gates, CI curl 127.0.0.1:8330 with no
    proxy headers) keep the full body; everything arriving through the public
    edge needs the shared token."""
    if bearer_token_matches(headers.get("Authorization"), token):
        return True
    return not is_proxied_request(headers)


class _Handler(BaseHTTPRequestHandler):
    def _write(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        raw = self.path.split("?", 1)[0]
        path = raw.rstrip("/") or "/"
        if path == "/healthz":
            self._write(*healthz_response())
        elif path == "/status":
            try:
                status, body = status_response(
                    self.server.probe_cache.snapshot,
                    self.server.unit_cache,
                    service_health_cache=getattr(self.server, "service_health_cache", None),
                    external_probe_cache=getattr(self.server, "external_probe_cache", None),
                )
                # Redaction, never a 401: an unauthenticated caller must still get
                # a fast, valid 200 or the never-502 edge floor trips.
                if not status_detail_authorized(self.headers, STATUS_TOKEN):
                    body = public_status_payload(body)
                self._write(status, body)
            except Exception:
                self._write(200, {"health_service": "degraded", "error": "status_render_failed"})
        else:
            self._write(404, {"error": "not_found"})

    def log_message(self, *args):  # quiet — journald only gets real errors
        pass


def build_server(bind: str = BIND, port: int = PORT, units=UNITS):
    cache = UnitStateCache(units)
    probe_cache = ProbeCache()
    sh_cache = turso_http.ServiceHealthCache(
        ttl=SERVICE_HEALTH_TTL, timeout=SERVICE_HEALTH_TIMEOUT,
    )
    ep_cache = turso_http.ServiceHealthCache(
        ttl=SERVICE_HEALTH_TTL, timeout=SERVICE_HEALTH_TIMEOUT,
        fetch_fn=turso_http.fetch_external_probe, default=None,
    )
    server = ThreadingHTTPServer((bind, port), _Handler)
    server.unit_cache = cache  # type: ignore[attr-defined]
    server.probe_cache = probe_cache  # type: ignore[attr-defined]
    server.service_health_cache = sh_cache  # type: ignore[attr-defined]
    server.external_probe_cache = ep_cache  # type: ignore[attr-defined]
    return server, cache


def main():
    server, cache = build_server()
    probe_cache = server.probe_cache
    cache.refresh_once()  # warm the unit cache before accepting traffic
    probe_cache.refresh_once()
    cache.start()
    probe_cache.start()
    try:
        server.serve_forever()
    finally:
        probe_cache.stop()
        cache.stop()
        server.server_close()


if __name__ == "__main__":
    main()
