"""Tests for the standalone health daemon (scripts/health_service).

Covers the pure probe/parse/assembly logic plus a real-socket smoke test of the
HTTP wiring. Deliberately uses real localhost sockets/servers rather than mocks
so the stdlib probe behaviour is genuinely exercised.
"""
import json
import os
import socket
import subprocess
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from health_service import probes, serve, turso_http

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))


# --- isolation contract: the daemon must share NO code with the trading stack ---

class TestStdlibOnlyIsolation:
    """The daemon's reason for existing is zero shared fate. Importing it must
    pull in NONE of the trading stack — if a future edit adds `from scripts.api
    ...` or an ib_insync/uvicorn import, this fails loudly."""

    def test_import_pulls_in_no_trading_stack(self):
        forbidden_roots = {"ib_insync", "uvicorn", "fastapi", "starlette",
                           "libsql", "libsql_experimental", "ibapi", "eventkit"}
        code = (
            "import sys; import health_service.serve;\n"
            "bad = sorted(m for m in sys.modules\n"
            "  if m.split('.')[0] in %r\n"
            "  or m.startswith('scripts.api') or m.startswith('api.')\n"
            "  or m == 'scripts.db' or m.startswith('scripts.db'));\n"
            "print(','.join(bad)); sys.exit(1 if bad else 0)" % (forbidden_roots,)
        )
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(["scripts", "."])}
        r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                           text=True, env=env, cwd=_REPO_ROOT, timeout=30)
        assert r.returncode == 0, f"daemon imported trading-stack modules: {r.stdout.strip()} / {r.stderr.strip()}"


# --- classify_conn_error ---

class TestClassifyConnError:
    def test_refused_is_down(self):
        assert probes.classify_conn_error(ConnectionRefusedError()) == "down"

    def test_errno_refused_is_down(self):
        import errno
        exc = OSError()
        exc.errno = errno.ECONNREFUSED
        assert probes.classify_conn_error(exc) == "down"

    def test_timeout_is_unknown(self):
        assert probes.classify_conn_error(socket.timeout()) == "unknown"
        assert probes.classify_conn_error(TimeoutError()) == "unknown"

    def test_other_oserror_is_unknown(self):
        assert probes.classify_conn_error(OSError("no route")) == "unknown"


# --- probe_tcp (real sockets) ---

class TestProbeTcp:
    def test_open_port_is_up(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            assert probes.probe_tcp("127.0.0.1", port, timeout=1.0)["state"] == "up"
        finally:
            srv.close()

    def test_closed_port_is_down(self):
        # Bind to claim a port, then close it so a connect is refused.
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        port = srv.getsockname()[1]
        srv.close()
        assert probes.probe_tcp("127.0.0.1", port, timeout=1.0)["state"] == "down"


# --- probe_http_json (real local server) ---

class _JsonHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/ok":
            body = json.dumps({"status": "ok", "auth_state": "authenticated"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b"down")

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def json_server():
    """Real local HTTP server shared across the module to avoid per-test
    teardown overhead from ThreadingHTTPServer.shutdown()'s 0.5s poll."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _JsonHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


class TestProbeHttpJson:
    def test_200_returns_up_with_payload(self, json_server):
        res = probes.probe_http_json(f"http://127.0.0.1:{json_server}/ok", timeout=2.0)
        assert res["state"] == "up"
        assert res["http_status"] == 200
        assert res["payload"]["auth_state"] == "authenticated"

    def test_5xx_returns_down(self, json_server):
        res = probes.probe_http_json(f"http://127.0.0.1:{json_server}/bad", timeout=2.0)
        assert res["state"] == "down"
        assert res["http_status"] == 503

    def test_refused_returns_down(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        port = srv.getsockname()[1]
        srv.close()
        res = probes.probe_http_json(f"http://127.0.0.1:{port}/x", timeout=1.0)
        assert res["state"] == "down"


# --- unit state parsing ---

class TestUnitCoarseState:
    def test_active_running_is_up(self):
        assert probes.unit_coarse_state("active", "running") == "up"

    def test_active_exited_is_up(self):
        # radon-ib-gateway.service is a docker-wrapper/oneshot: it settles at
        # active+exited, which must read as 'up', not 'unknown' (Feature A).
        assert probes.unit_coarse_state("active", "exited") == "up"

    def test_failed_is_down(self):
        assert probes.unit_coarse_state("failed", "failed") == "down"

    def test_inactive_is_down(self):
        assert probes.unit_coarse_state("inactive", "dead") == "down"

    def test_activating_is_starting(self):
        assert probes.unit_coarse_state("activating", "start") == "starting"

    def test_unknown_default(self):
        assert probes.unit_coarse_state("weird", "state") == "unknown"


class TestParseUnitStates:
    RAW = (
        "Id=radon-api.service\nActiveState=active\nSubState=running\nResult=success\n"
        "\n"
        "Id=radon-relay.service\nActiveState=failed\nSubState=failed\nResult=exit-code\n"
    )

    def test_parses_multiple_blocks(self):
        units = probes.parse_unit_states(self.RAW)
        assert set(units) == {"radon-api.service", "radon-relay.service"}
        assert units["radon-api.service"]["state"] == "up"
        assert units["radon-relay.service"]["state"] == "down"
        assert units["radon-relay.service"]["result"] == "exit-code"

    def test_empty_input(self):
        assert probes.parse_unit_states("") == {}

    def test_block_without_id_skipped(self):
        assert probes.parse_unit_states("ActiveState=active\nSubState=running") == {}


# --- status assembly + handlers ---

class TestHealthzResponse:
    def test_static_200(self):
        assert serve.healthz_response() == (200, {"ok": True})


class _FakeCache:
    def __init__(self, value, age):
        self._v, self._a = value, age

    def snapshot(self):
        return dict(self._v), self._a


class _FakeSHCache:
    def __init__(self, value):
        self._v = value

    def snapshot(self):
        return dict(self._v)


class TestStatusResponse:
    def test_always_200_with_probes_and_units(self):
        status, body = serve.status_response(
            run_probes_fn=lambda: {"radon-api": {"state": "up"}},
            unit_cache=_FakeCache({"radon-api.service": {"state": "up"}}, 1.2),
            now_fn=lambda: "2026-05-29T00:00:00+00:00",
        )
        assert status == 200
        assert body["health_service"] == "ok"
        assert body["probes"]["radon-api"]["state"] == "up"
        assert body["units"]["radon-api.service"]["state"] == "up"
        assert body["units_age_secs"] == 1.2
        # service_health section present even with no cache wired in
        assert body["service_health"]["state"] == "unknown"

    def test_probe_sweep_exception_degrades_but_still_200(self):
        def _boom():
            raise RuntimeError("probe sweep blew up")

        status, body = serve.status_response(
            run_probes_fn=_boom,
            unit_cache=_FakeCache({}, None),
            now_fn=lambda: "t",
        )
        assert status == 200
        assert body["health_service"] == "degraded"
        assert body["probes"] == {}

    def test_service_health_section_merged_when_ok(self):
        sh = {"state": "ok", "rows": [{"service": "cri-scan", "state": "ok",
                                       "updated_at": "2026-05-29T00:00:00+00:00",
                                       "age_secs": 12.0}],
              "row_count": 1}
        status, body = serve.status_response(
            run_probes_fn=lambda: {},
            unit_cache=_FakeCache({}, None),
            now_fn=lambda: "t",
            service_health_cache=_FakeSHCache(sh),
        )
        assert status == 200
        assert body["health_service"] == "ok"
        assert body["service_health"]["state"] == "ok"
        assert body["service_health"]["rows"][0]["service"] == "cri-scan"
        assert body["service_health"]["rows"][0]["age_secs"] == 12.0

    def test_service_health_cache_raising_degrades_section_only(self):
        class _BoomCache:
            def snapshot(self):
                raise RuntimeError("turso cache blew up")

        status, body = serve.status_response(
            run_probes_fn=lambda: {"radon-api": {"state": "up"}},
            unit_cache=_FakeCache({}, None),
            now_fn=lambda: "t",
            service_health_cache=_BoomCache(),
        )
        # response stays 200 + ok; only the service_health section degrades
        assert status == 200
        assert body["health_service"] == "ok"
        assert body["service_health"]["state"] == "unknown"


# --- Feature B: stdlib-only Turso service_health HTTP reader ---

class TestHttpUrlFromLibsql:
    def test_libsql_becomes_https(self):
        assert turso_http.http_url_from_libsql("libsql://radon-x.turso.io") == "https://radon-x.turso.io"

    def test_https_passthrough(self):
        assert turso_http.http_url_from_libsql("https://radon-x.turso.io") == "https://radon-x.turso.io"

    def test_empty_is_empty(self):
        assert turso_http.http_url_from_libsql("") == ""


class TestFetchServiceHealth:
    def test_no_creds_is_unknown(self, monkeypatch):
        monkeypatch.delenv("TURSO_DB_URL", raising=False)
        monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
        res = turso_http.fetch_service_health(timeout=0.1)
        assert res["state"] == "unknown"
        assert res["detail"] == "no_creds"
        assert res["rows"] == []

    def test_partial_creds_is_unknown(self, monkeypatch):
        monkeypatch.setenv("TURSO_DB_URL", "libsql://radon-x.turso.io")
        monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
        res = turso_http.fetch_service_health(timeout=0.1)
        assert res["state"] == "unknown"
        assert res["detail"] == "no_creds"

    def test_happy_path_parses_rows(self, monkeypatch):
        monkeypatch.setenv("TURSO_DB_URL", "libsql://radon-x.turso.io")
        monkeypatch.setenv("TURSO_AUTH_TOKEN", "tok")
        captured = {}

        def _fake_post(origin, token, sql, timeout):
            captured["origin"] = origin
            captured["token"] = token
            return {
                "results": [
                    {"type": "ok", "response": {"type": "execute", "result": {
                        "cols": [{"name": c} for c in turso_http.SERVICE_HEALTH_COLUMNS],
                        "rows": [[
                            {"type": "text", "value": "cri-scan"},
                            {"type": "text", "value": "error"},
                            {"type": "null"},
                            {"type": "null"},
                            {"type": "text", "value": "{\"msg\":\"boom\"}"},
                            {"type": "text", "value": "2026-05-29T00:00:00+00:00"},
                        ]],
                    }}},
                    {"type": "ok", "response": {"type": "close"}},
                ]
            }

        monkeypatch.setattr(turso_http, "_post_pipeline", _fake_post)
        res = turso_http.fetch_service_health(timeout=2.5)
        assert captured["origin"] == "https://radon-x.turso.io"
        assert captured["token"] == "tok"
        assert res["state"] == "ok"
        assert res["row_count"] == 1
        row = res["rows"][0]
        assert row["service"] == "cri-scan"
        assert row["state"] == "error"
        assert row["last_error"] == '{"msg":"boom"}'
        assert row["last_attempt_started_at"] is None
        # raw age exposed; staleness judgement left to the consumer
        assert isinstance(row["age_secs"], float)

    def test_timeout_degrades_to_unknown(self, monkeypatch):
        monkeypatch.setenv("TURSO_DB_URL", "libsql://radon-x.turso.io")
        monkeypatch.setenv("TURSO_AUTH_TOKEN", "tok")

        def _boom(*a, **k):
            raise TimeoutError("turso slow")

        monkeypatch.setattr(turso_http, "_post_pipeline", _boom)
        res = turso_http.fetch_service_health(timeout=2.5)
        assert res["state"] == "unknown"
        assert res["detail"] == "TimeoutError"
        assert res["rows"] == []

    def test_http_error_degrades_to_unknown(self, monkeypatch):
        monkeypatch.setenv("TURSO_DB_URL", "libsql://radon-x.turso.io")
        monkeypatch.setenv("TURSO_AUTH_TOKEN", "tok")

        def _boom(*a, **k):
            raise urllib.error.HTTPError("u", 401, "unauthorized", {}, None)

        monkeypatch.setattr(turso_http, "_post_pipeline", _boom)
        res = turso_http.fetch_service_health(timeout=2.5)
        assert res["state"] == "unknown"
        assert res["detail"] == "http_401"

    def test_malformed_response_degrades_to_unknown(self, monkeypatch):
        monkeypatch.setenv("TURSO_DB_URL", "libsql://radon-x.turso.io")
        monkeypatch.setenv("TURSO_AUTH_TOKEN", "tok")
        monkeypatch.setattr(turso_http, "_post_pipeline",
                            lambda *a, **k: {"results": [{"type": "error"}]})
        res = turso_http.fetch_service_health(timeout=2.5)
        assert res["state"] == "unknown"
        assert res["detail"] == "bad_response"


class TestServiceHealthCache:
    def test_caches_within_ttl(self):
        calls = {"n": 0}

        def _fetch(timeout):
            calls["n"] += 1
            return {"state": "ok", "rows": [], "row_count": 0}

        cache = turso_http.ServiceHealthCache(ttl=60.0, timeout=2.5, fetch_fn=_fetch)
        a = cache.snapshot()
        b = cache.snapshot()
        assert calls["n"] == 1  # second read served from cache
        assert a["state"] == "ok" and b["state"] == "ok"

    def test_fetch_fn_raising_degrades_to_unknown(self):
        def _boom(timeout):
            raise RuntimeError("unexpected")

        cache = turso_http.ServiceHealthCache(ttl=5.0, fetch_fn=_boom)
        snap = cache.snapshot()
        assert snap["state"] == "unknown"
        assert snap["rows"] == []


class TestUnitStateCacheRefresh:
    def test_refresh_never_raises_cross_platform(self):
        # Platform-agnostic: on macOS systemctl is absent (FileNotFoundError,
        # swallowed → {}); on Linux CI `systemctl show <unknown-unit>` returns
        # inactive/dead props. Either way refresh_once must NOT raise and
        # snapshot must return a dict — we don't assert emptiness.
        cache = serve.UnitStateCache(["radon-nonexistent-xyztest.service"])
        cache.refresh_once()
        value, age = cache.snapshot()
        assert isinstance(value, dict)

    def test_empty_units_is_noop(self):
        cache = serve.UnitStateCache([])
        cache.refresh_once()
        assert cache.snapshot() == ({}, None)


# --- HTTP wiring smoke test (real ephemeral server) ---

class TestServerSmoke:
    @pytest.fixture(scope="class")
    def running(self):
        """Health server shared across the class to avoid per-test
        shutdown overhead from ThreadingHTTPServer.shutdown()'s 0.5s poll."""
        server, cache = serve.build_server(bind="127.0.0.1", port=0, units=[])
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        port = server.server_address[1]
        try:
            yield port
        finally:
            server.shutdown()
            cache.stop()
            server.server_close()

    def _get(self, port, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=8) as r:
            return r.status, json.loads(r.read().decode())

    def test_healthz_is_200(self, running):
        status, body = self._get(running, "/healthz")
        assert status == 200 and body == {"ok": True}

    def test_status_is_200(self, running):
        status, body = self._get(running, "/status")
        assert status == 200
        assert "probes" in body and "units" in body
        assert body["health_service"] in ("ok", "degraded")

    def test_unknown_path_404(self, running):
        with pytest.raises(urllib.error.HTTPError) as exc:
            self._get(running, "/nope")
        assert exc.value.code == 404


import urllib.error  # noqa: E402  (used in the 404 assertion above)


# --- external_probe (Tier-3 off-box) surfaced inline in /status ---

class TestFetchExternalProbe:
    def test_no_creds_is_none(self, monkeypatch):
        monkeypatch.delenv("TURSO_DB_URL", raising=False)
        monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
        assert turso_http.fetch_external_probe(timeout=0.1) is None

    def test_happy_path_returns_freshest_row(self, monkeypatch):
        monkeypatch.setenv("TURSO_DB_URL", "libsql://radon-x.turso.io")
        monkeypatch.setenv("TURSO_AUTH_TOKEN", "tok")

        def _fake_post(origin, token, sql, timeout):
            assert "external_probe" in sql
            return {"results": [
                {"type": "ok", "response": {"type": "execute", "result": {
                    "cols": [{"name": c} for c in
                             ("source", "ok", "http_status", "latency_ms", "checked_at", "detail")],
                    "rows": [[
                        {"type": "text", "value": "github-actions/edge"},
                        {"type": "integer", "value": "1"},
                        {"type": "integer", "value": "200"},
                        {"type": "integer", "value": "142"},
                        {"type": "text", "value": "2026-05-30T13:05:22Z"},
                        {"type": "null"},
                    ]],
                }}},
                {"type": "ok", "response": {"type": "close"}},
            ]}

        monkeypatch.setattr(turso_http, "_post_pipeline", _fake_post)
        row = turso_http.fetch_external_probe(timeout=2.5)
        assert row["source"] == "github-actions/edge"
        assert row["ok"] == 1
        assert row["latency_ms"] == 142
        assert row["checked_at"] == "2026-05-30T13:05:22Z"
        assert "age_secs" not in row  # external_probe uses checked_at

    def test_no_rows_is_none(self, monkeypatch):
        monkeypatch.setenv("TURSO_DB_URL", "libsql://radon-x.turso.io")
        monkeypatch.setenv("TURSO_AUTH_TOKEN", "tok")
        monkeypatch.setattr(turso_http, "_post_pipeline", lambda *a, **k: {
            "results": [{"type": "ok", "response": {"type": "execute",
                        "result": {"cols": [], "rows": []}}},
                        {"type": "ok", "response": {"type": "close"}}]})
        assert turso_http.fetch_external_probe(timeout=2.5) is None

    def test_failure_is_none(self, monkeypatch):
        monkeypatch.setenv("TURSO_DB_URL", "libsql://radon-x.turso.io")
        monkeypatch.setenv("TURSO_AUTH_TOKEN", "tok")

        def _boom(*a, **k):
            raise TimeoutError("turso slow")

        monkeypatch.setattr(turso_http, "_post_pipeline", _boom)
        assert turso_http.fetch_external_probe(timeout=2.5) is None


class TestExternalProbeInStatus:
    def test_build_status_includes_external_probe(self):
        body = probes.build_status({}, {}, "t", external_probe={
            "source": "gh", "ok": 1, "latency_ms": 142, "checked_at": "x"})
        assert body["external_probe"]["latency_ms"] == 142

    def test_build_status_external_probe_defaults_none(self):
        assert probes.build_status({}, {}, "t")["external_probe"] is None

    def test_status_response_wires_external_probe(self):
        class _EpCache:
            def snapshot(self):
                return {"source": "gh", "ok": 1, "latency_ms": 142, "checked_at": "x"}

        status, body = serve.status_response(
            run_probes_fn=lambda: {},
            unit_cache=_FakeCache({}, None),
            now_fn=lambda: "t",
            external_probe_cache=_EpCache(),
        )
        assert status == 200
        assert body["external_probe"]["latency_ms"] == 142

    def test_status_response_external_probe_none_when_no_cache(self):
        status, body = serve.status_response(
            run_probes_fn=lambda: {}, unit_cache=_FakeCache({}, None), now_fn=lambda: "t")
        assert body["external_probe"] is None
