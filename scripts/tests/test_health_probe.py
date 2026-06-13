"""Tests for the Tier-3 OFF-BOX prober (scripts/health_probe).

Covers the pure logic: HTTP probe parsing (mocked urllib), ping+status
classification, row construction, the Turso HTTP pipeline body builder, and the
dead-man's-switch reader. No real network, no real Turso.
"""
import errno
import io
import json
import socket
import subprocess
import sys
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest

from health_probe import probe, reader, turso_http


# ── isolation contract: stdlib-only, no trading stack, no libsql ─────────────

class TestStdlibOnlyIsolation:
    """Tier-3 runs on a generic GH runner with no native libsql and a
    zero-shared-fate mandate. Importing it must pull in NONE of the trading
    stack and NO libsql client."""

    def test_import_pulls_in_no_trading_stack(self):
        import os
        forbidden_roots = {"ib_insync", "uvicorn", "fastapi", "starlette",
                           "libsql", "libsql_experimental", "ibapi", "eventkit"}
        code = (
            "import sys; import health_probe.probe; import health_probe.reader;\n"
            "bad = sorted(m for m in sys.modules\n"
            "  if m.split('.')[0] in %r\n"
            "  or m.startswith('scripts.api') or m.startswith('api.')\n"
            "  or m == 'scripts.db' or m.startswith('scripts.db'));\n"
            "print(','.join(bad)); sys.exit(1 if bad else 0)" % (forbidden_roots,)
        )
        import os as _os
        repo_root = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), _os.pardir, _os.pardir))
        env = {**_os.environ, "PYTHONPATH": _os.pathsep.join(["scripts", "."])}
        r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                           text=True, env=env, cwd=repo_root, timeout=30)
        assert r.returncode == 0, f"prober imported forbidden modules: {r.stdout.strip()} / {r.stderr.strip()}"


# ── probe_endpoint: mocked urllib ────────────────────────────────────────────

class _FakeResp:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def read(self, _n=None):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestProbeEndpoint:
    def test_2xx_json_is_reachable_with_payload(self, monkeypatch):
        monkeypatch.setattr(probe.urllib.request, "urlopen",
                            lambda *a, **k: _FakeResp(b'{"ok": true, "state": "up"}', 200))
        result = probe.probe_endpoint("https://app.radon.run/edge-health/status")
        assert result["reachable"] is True
        assert result["http_status"] == 200
        assert result["payload"] == {"ok": True, "state": "up"}
        assert result["latency_ms"] >= 0

    def test_empty_body_yields_empty_payload(self, monkeypatch):
        monkeypatch.setattr(probe.urllib.request, "urlopen",
                            lambda *a, **k: _FakeResp(b"", 200))
        result = probe.probe_endpoint("https://app.radon.run/edge-health/ping")
        assert result["reachable"] is True
        assert result["payload"] == {}

    def test_non_json_body_yields_empty_payload(self, monkeypatch):
        monkeypatch.setattr(probe.urllib.request, "urlopen",
                            lambda *a, **k: _FakeResp(b"OK", 200))
        result = probe.probe_endpoint("https://app.radon.run/edge-health/ping")
        assert result["reachable"] is True
        assert result["payload"] == {}

    def test_http_error_is_reachable_with_status(self, monkeypatch):
        def _raise(*a, **k):
            raise urllib.error.HTTPError("u", 503, "down", {}, io.BytesIO(b""))
        monkeypatch.setattr(probe.urllib.request, "urlopen", _raise)
        result = probe.probe_endpoint("https://app.radon.run/edge-health/status")
        assert result["reachable"] is True
        assert result["http_status"] == 503

    def test_timeout_is_unreachable(self, monkeypatch):
        def _raise(*a, **k):
            raise socket.timeout()
        monkeypatch.setattr(probe.urllib.request, "urlopen", _raise)
        result = probe.probe_endpoint("https://app.radon.run/edge-health/ping")
        assert result == {"reachable": False, "detail": "timeout"}

    def test_connection_refused_is_unreachable_refused(self, monkeypatch):
        def _raise(*a, **k):
            raise urllib.error.URLError(ConnectionRefusedError())
        monkeypatch.setattr(probe.urllib.request, "urlopen", _raise)
        result = probe.probe_endpoint("https://app.radon.run/edge-health/ping")
        assert result["reachable"] is False
        assert result["detail"] == "refused"

    def test_dns_failure_is_unreachable(self, monkeypatch):
        def _raise(*a, **k):
            raise urllib.error.URLError("Name or service not known")
        monkeypatch.setattr(probe.urllib.request, "urlopen", _raise)
        result = probe.probe_endpoint("https://app.radon.run/edge-health/ping")
        assert result["reachable"] is False
        assert result["detail"] == "unreachable"


# ── classify_probes ──────────────────────────────────────────────────────────

def _ok_probe(status=200, payload=None):
    return {"reachable": True, "http_status": status, "latency_ms": 12, "payload": payload or {}}


class TestClassifyProbes:
    def test_both_healthy_is_ok(self):
        result = probe.classify_probes(_ok_probe(), _ok_probe(payload={"ok": True, "state": "up"}))
        assert result == {"ok": 1, "detail": "edge_ok"}

    def test_ping_unreachable_fails(self):
        result = probe.classify_probes({"reachable": False, "detail": "timeout"}, _ok_probe())
        assert result["ok"] == 0
        assert result["detail"].startswith("ping_unreachable")

    def test_ping_5xx_fails(self):
        result = probe.classify_probes(_ok_probe(status=502), _ok_probe())
        assert result == {"ok": 0, "detail": "ping_http_502"}

    def test_status_unreachable_fails_even_if_ping_ok(self):
        result = probe.classify_probes(_ok_probe(), {"reachable": False, "detail": "refused"})
        assert result["ok"] == 0
        assert result["detail"].startswith("status_unreachable")

    def test_status_503_fails(self):
        result = probe.classify_probes(_ok_probe(), _ok_probe(status=503))
        assert result == {"ok": 0, "detail": "status_http_503"}

    def test_aggregate_ok_false_fails(self):
        result = probe.classify_probes(_ok_probe(), _ok_probe(payload={"ok": False}))
        assert result == {"ok": 0, "detail": "aggregate_unhealthy"}

    def test_aggregate_state_down_fails(self):
        result = probe.classify_probes(_ok_probe(), _ok_probe(payload={"state": "down"}))
        assert result == {"ok": 0, "detail": "aggregate_unhealthy"}

    def test_aggregate_state_unknown_is_not_fatal(self):
        # 'unknown' is not proof of death — the edge answered 200.
        result = probe.classify_probes(_ok_probe(), _ok_probe(payload={"state": "unknown"}))
        assert result["ok"] == 1

    def test_opaque_200_payload_is_healthy(self):
        result = probe.classify_probes(_ok_probe(), _ok_probe(payload={}))
        assert result["ok"] == 1


# ── build_probe_row ──────────────────────────────────────────────────────────

class TestBuildProbeRow:
    def test_healthy_row_uses_status_code_and_max_latency(self):
        ping = {"reachable": True, "http_status": 200, "latency_ms": 30, "payload": {}}
        status = {"reachable": True, "http_status": 200, "latency_ms": 90, "payload": {"ok": True}}
        row = probe.build_probe_row("src", ping, status, "2026-05-29T12:00:00Z")
        assert row == {
            "source": "src",
            "ok": 1,
            "http_status": 200,
            "latency_ms": 90,  # worst-case of the two
            "detail": "edge_ok",
            "checked_at": "2026-05-29T12:00:00Z",
        }

    def test_transport_failure_yields_null_status_and_latency(self):
        ping = {"reachable": False, "detail": "timeout"}
        status = {"reachable": False, "detail": "timeout"}
        row = probe.build_probe_row("src", ping, status, "2026-05-29T12:00:00Z")
        assert row["ok"] == 0
        assert row["http_status"] is None
        assert row["latency_ms"] is None
        assert row["detail"].startswith("ping_unreachable")

    def test_status_unreachable_keeps_null_http_status(self):
        ping = {"reachable": True, "http_status": 200, "latency_ms": 10, "payload": {}}
        status = {"reachable": False, "detail": "refused"}
        row = probe.build_probe_row("src", ping, status, "2026-05-29T12:00:00Z")
        assert row["http_status"] is None
        assert row["latency_ms"] == 10  # only the reachable ping contributes


# ── turso_http: URL rewrite + pipeline body + error surfacing ────────────────

class TestHttpBaseUrl:
    def test_libsql_scheme_becomes_https(self):
        assert turso_http.http_base_url("libsql://radon.turso.io") == "https://radon.turso.io"

    def test_https_passthrough(self):
        assert turso_http.http_base_url("https://radon.turso.io") == "https://radon.turso.io"

    def test_ws_dev_becomes_http(self):
        assert turso_http.http_base_url("ws://127.0.0.1:8080") == "http://127.0.0.1:8080"


class TestBuildUpsertPipeline:
    def test_upsert_has_conflict_clause_and_named_args(self):
        row = {"source": "s", "ok": 1, "http_status": 200,
               "latency_ms": 42, "detail": "edge_ok", "checked_at": "2026-05-29T12:00:00Z"}
        body = turso_http.build_upsert_pipeline(row)
        stmt = body["requests"][0]["stmt"]
        assert "ON CONFLICT(source) DO UPDATE" in stmt["sql"]
        names = {a["name"]: a["value"] for a in stmt["named_args"]}
        assert names["source"] == {"type": "text", "value": "s"}
        assert names["ok"] == {"type": "integer", "value": "1"}
        assert names["http_status"] == {"type": "integer", "value": "200"}
        assert names["checked_at"] == {"type": "text", "value": "2026-05-29T12:00:00Z"}
        assert body["requests"][-1] == {"type": "close"}

    def test_none_encodes_as_null(self):
        row = {"source": "s", "ok": 0, "http_status": None,
               "latency_ms": None, "detail": "timeout", "checked_at": "2026-05-29T12:00:00Z"}
        names = {a["name"]: a["value"] for a in turso_http.build_upsert_pipeline(row)["requests"][0]["stmt"]["named_args"]}
        assert names["http_status"] == {"type": "null"}
        assert names["latency_ms"] == {"type": "null"}


class TestUpsertErrorSurfacing:
    def test_missing_env_raises(self, monkeypatch):
        monkeypatch.delenv("TURSO_DB_URL", raising=False)
        monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
        with pytest.raises(turso_http.TursoHttpError):
            turso_http.upsert_external_probe({"source": "s", "ok": 1, "checked_at": "x"})

    def test_pipeline_error_result_raises(self, monkeypatch):
        monkeypatch.setenv("TURSO_DB_URL", "libsql://radon.turso.io")
        monkeypatch.setenv("TURSO_AUTH_TOKEN", "tok")
        err_body = json.dumps({"results": [{"type": "error", "error": {"message": "no such table"}}]}).encode()
        monkeypatch.setattr(turso_http.urllib.request, "urlopen",
                            lambda *a, **k: _FakeResp(err_body, 200))
        with pytest.raises(turso_http.TursoHttpError) as exc:
            turso_http.upsert_external_probe({
                "source": "s", "ok": 1, "http_status": 200,
                "latency_ms": 1, "detail": "edge_ok", "checked_at": "x"})
        assert "no such table" in str(exc.value)

    def test_http_error_raises(self, monkeypatch):
        monkeypatch.setenv("TURSO_DB_URL", "libsql://radon.turso.io")
        monkeypatch.setenv("TURSO_AUTH_TOKEN", "tok")

        def _raise(*a, **k):
            raise urllib.error.HTTPError("u", 401, "unauthorized", {}, io.BytesIO(b"bad token"))
        monkeypatch.setattr(turso_http.urllib.request, "urlopen", _raise)
        with pytest.raises(turso_http.TursoHttpError) as exc:
            turso_http.upsert_external_probe({
                "source": "s", "ok": 1, "http_status": 200,
                "latency_ms": 1, "detail": "edge_ok", "checked_at": "x"})
        assert "401" in str(exc.value)

    def test_success_does_not_raise(self, monkeypatch):
        monkeypatch.setenv("TURSO_DB_URL", "libsql://radon.turso.io")
        monkeypatch.setenv("TURSO_AUTH_TOKEN", "tok")
        ok_body = json.dumps({"results": [{"type": "ok"}, {"type": "ok"}]}).encode()
        monkeypatch.setattr(turso_http.urllib.request, "urlopen",
                            lambda *a, **k: _FakeResp(ok_body, 200))
        turso_http.upsert_external_probe({
            "source": "s", "ok": 1, "http_status": 200,
            "latency_ms": 1, "detail": "edge_ok", "checked_at": "x"})


# ── user-path probe: unauthenticated /dashboard must hit the Clerk wall ──────
#
# Live evidence (2026-06-12, curl against production):
#   * Accept: text/html  -> 307 with
#     Location: https://app.radon.run/sign-in?redirect_url=...
#     and x-clerk-auth-status: signed-out
#   * no Accept header   -> 404 protect-rewrite, still carrying
#     x-clerk-auth-status: signed-out
# Healthy = the Clerk middleware answered: a 3xx to a sign-in location, or any
# response carrying the x-clerk-auth-status header. A 5xx (Edge-runtime crash
# class), a timeout, or a 200 WITHOUT Clerk headers (perimeter bypass) fails.

def _user_path_raw(status=307, location="https://app.radon.run/sign-in?redirect_url=x",
                   clerk_auth_status="signed-out", latency_ms=80):
    return {"reachable": True, "http_status": status, "location": location,
            "clerk_auth_status": clerk_auth_status, "latency_ms": latency_ms}


class TestClassifyUserPath:
    def test_307_to_on_domain_sign_in_is_ok(self):
        result = probe.classify_user_path(_user_path_raw())
        assert result == {"ok": 1, "detail": "clerk_redirect"}

    def test_3xx_to_clerk_domain_is_ok(self):
        raw = _user_path_raw(status=302, location="https://clerk.radon.run/sign-in", clerk_auth_status=None)
        assert probe.classify_user_path(raw)["ok"] == 1

    def test_404_protect_rewrite_with_clerk_header_is_ok(self):
        raw = _user_path_raw(status=404, location=None)
        result = probe.classify_user_path(raw)
        assert result["ok"] == 1
        assert result["detail"] == "clerk_protect_404"

    def test_200_sign_in_served_inline_with_clerk_header_is_ok(self):
        raw = _user_path_raw(status=200, location=None)
        assert probe.classify_user_path(raw)["ok"] == 1

    def test_200_without_clerk_header_is_a_perimeter_failure(self):
        raw = _user_path_raw(status=200, location=None, clerk_auth_status=None)
        result = probe.classify_user_path(raw)
        assert result == {"ok": 0, "detail": "user_path_http_200_no_clerk"}

    def test_3xx_to_unexpected_location_fails(self):
        raw = _user_path_raw(status=307, location="https://evil.example/", clerk_auth_status=None)
        assert probe.classify_user_path(raw)["ok"] == 0

    def test_500_fails(self):
        raw = _user_path_raw(status=500, location=None, clerk_auth_status=None)
        result = probe.classify_user_path(raw)
        assert result == {"ok": 0, "detail": "user_path_http_500"}

    def test_timeout_fails(self):
        result = probe.classify_user_path({"reachable": False, "detail": "timeout"})
        assert result["ok"] == 0
        assert result["detail"] == "user_path_unreachable:timeout"


class _FakeOpener:
    def __init__(self, response=None, raises=None):
        self._response = response
        self._raises = raises

    def open(self, *_a, **_k):
        if self._raises:
            raise self._raises
        return self._response


class TestProbeUserPath:
    def test_redirect_is_captured_not_followed(self, monkeypatch):
        headers = {"Location": "https://app.radon.run/sign-in?redirect_url=x",
                   "x-clerk-auth-status": "signed-out"}
        exc = urllib.error.HTTPError("u", 307, "redirect", headers, io.BytesIO(b""))
        monkeypatch.setattr(probe.urllib.request, "build_opener",
                            lambda *a: _FakeOpener(raises=exc))
        raw = probe.probe_user_path("https://app.radon.run/dashboard")
        assert raw["reachable"] is True
        assert raw["http_status"] == 307
        assert "/sign-in" in raw["location"]
        assert raw["clerk_auth_status"] == "signed-out"

    def test_timeout_is_unreachable(self, monkeypatch):
        monkeypatch.setattr(probe.urllib.request, "build_opener",
                            lambda *a: _FakeOpener(raises=socket.timeout()))
        raw = probe.probe_user_path("https://app.radon.run/dashboard")
        assert raw == {"reachable": False, "detail": "timeout"}

    def test_plain_200_captures_clerk_header(self, monkeypatch):
        resp = _FakeResp(b"<html>", 200)
        resp.headers = {"x-clerk-auth-status": "signed-out"}
        monkeypatch.setattr(probe.urllib.request, "build_opener",
                            lambda *a: _FakeOpener(response=resp))
        raw = probe.probe_user_path("https://app.radon.run/dashboard")
        assert raw["http_status"] == 200
        assert raw["clerk_auth_status"] == "signed-out"


# ── freshness probe: /api/probe/freshness contract ──────────────────────────
#
# Recorded contract fixture (the web half builds the endpoint against this
# exact shape — see DUR-16). The endpoint goes live only when the web half
# deploys; until then production answers 404/401 which MUST classify as
# endpoint_pending (freshness_ok NULL), not a failure.

FRESHNESS_CONTRACT_FIXTURE = {
    "generated_at": "2026-06-12T19:00:00Z",
    "market_state": "open",
    "checks": {
        "relay_tick": {"applicable": True, "age_secs": 4.2, "fresh": True},
        "vcg_scan": {"applicable": True, "age_secs": 640.0, "fresh": True},
        "gex_scan": {"applicable": True, "age_secs": 810.0, "fresh": True},
        "journal": {"applicable": True, "age_secs": 120.0, "fresh": True},
    },
    "all_fresh": True,
}

FRESHNESS_QUIET_MARKET_FIXTURE = {
    "generated_at": "2026-06-13T02:00:00Z",
    "market_state": "closed",
    "checks": {
        "relay_tick": {"applicable": False, "age_secs": None, "fresh": None},
        "vcg_scan": {"applicable": False, "age_secs": None, "fresh": None},
        "gex_scan": {"applicable": False, "age_secs": None, "fresh": None},
        "journal": {"applicable": False, "age_secs": None, "fresh": None},
    },
    "all_fresh": None,
}


def _freshness_raw(status=200, payload=None):
    return {"reachable": True, "http_status": status, "latency_ms": 60,
            "payload": FRESHNESS_CONTRACT_FIXTURE if payload is None else payload}


class TestClassifyFreshness:
    def test_200_all_fresh_true_is_healthy(self):
        result = probe.classify_freshness(_freshness_raw())
        assert result["freshness_ok"] == 1
        assert result["tick_fresh"] == 1
        assert result["scan_fresh"] == 1
        assert result["market_state"] == "open"
        assert result["detail"] == "fresh"

    def test_200_all_fresh_null_quiet_market_is_healthy(self):
        result = probe.classify_freshness(_freshness_raw(payload=FRESHNESS_QUIET_MARKET_FIXTURE))
        assert result["freshness_ok"] == 1
        assert result["tick_fresh"] is None
        assert result["scan_fresh"] is None
        assert result["market_state"] == "closed"

    def test_200_all_fresh_false_is_unhealthy_with_per_check_flags(self):
        payload = {
            "generated_at": "x", "market_state": "open", "all_fresh": False,
            "checks": {
                "relay_tick": {"applicable": True, "age_secs": 900.0, "fresh": False},
                "vcg_scan": {"applicable": True, "age_secs": 100.0, "fresh": True},
                "gex_scan": {"applicable": True, "age_secs": 99999.0, "fresh": False},
                "journal": {"applicable": True, "age_secs": 10.0, "fresh": True},
            },
        }
        result = probe.classify_freshness(_freshness_raw(payload=payload))
        assert result["freshness_ok"] == 0
        assert result["tick_fresh"] == 0
        assert result["scan_fresh"] == 0  # any stale scan poisons the pair
        assert result["detail"] == "stale"

    def test_scan_fresh_is_null_safe_across_the_pair(self):
        payload = {
            "market_state": "open", "all_fresh": True,
            "checks": {
                "relay_tick": {"applicable": True, "fresh": True},
                "vcg_scan": {"applicable": True, "fresh": True},
                "gex_scan": {"applicable": False, "fresh": None},
            },
        }
        assert probe.classify_freshness(_freshness_raw(payload=payload))["scan_fresh"] == 1

    def test_404_is_endpoint_pending_not_failure(self):
        result = probe.classify_freshness(_freshness_raw(status=404, payload={}))
        assert result["freshness_ok"] is None
        assert result["detail"] == "endpoint_pending"
        assert result["market_state"] is None

    def test_401_is_endpoint_pending_not_failure(self):
        result = probe.classify_freshness(_freshness_raw(status=401, payload={}))
        assert result["freshness_ok"] is None
        assert result["detail"] == "endpoint_pending"

    def test_500_is_unhealthy_but_market_state_unknown(self):
        result = probe.classify_freshness(_freshness_raw(status=500, payload={}))
        assert result["freshness_ok"] == 0
        assert result["detail"] == "freshness_http_500"
        assert result["market_state"] is None

    def test_timeout_is_unhealthy_but_market_state_unknown(self):
        result = probe.classify_freshness({"reachable": False, "detail": "timeout"})
        assert result["freshness_ok"] == 0
        assert result["detail"] == "freshness_unreachable:timeout"
        assert result["market_state"] is None

    def test_missing_token_is_pending_like_not_failure(self):
        result = probe.classify_freshness({"reachable": False, "detail": "no_token", "skipped": True})
        assert result["freshness_ok"] is None
        assert result["detail"] == "freshness_no_token"


class TestProbeFreshness:
    def test_sends_bearer_and_parses_payload(self, monkeypatch):
        captured = {}

        def _fake_urlopen(request, timeout=None):
            captured["auth"] = request.headers.get("Authorization")
            return _FakeResp(json.dumps(FRESHNESS_CONTRACT_FIXTURE).encode(), 200)
        monkeypatch.setattr(probe.urllib.request, "urlopen", _fake_urlopen)
        raw = probe.probe_freshness("https://app.radon.run/api/probe/freshness", "tok123")
        assert captured["auth"] == "Bearer tok123"
        assert raw["http_status"] == 200
        assert raw["payload"]["all_fresh"] is True

    def test_no_token_skips_the_request(self, monkeypatch):
        def _boom(*a, **k):
            raise AssertionError("must not hit the network without a token")
        monkeypatch.setattr(probe.urllib.request, "urlopen", _boom)
        raw = probe.probe_freshness("https://app.radon.run/api/probe/freshness", "")
        assert raw == {"reachable": False, "detail": "no_token", "skipped": True}


# ── exit-code policy: arms GitHub's workflow-failure email (DUR-04 residual) ─

class TestExitCodePolicy:
    def test_all_healthy_exits_zero(self):
        assert probe.exit_code_for(1, 1, 1, "open") == 0

    def test_edge_down_exits_nonzero(self):
        assert probe.exit_code_for(0, 1, None, None) == probe.EXIT_UNHEALTHY

    def test_user_path_down_exits_nonzero(self):
        assert probe.exit_code_for(1, 0, 1, "open") == probe.EXIT_UNHEALTHY

    def test_stale_freshness_during_rth_exits_nonzero(self):
        assert probe.exit_code_for(1, 1, 0, "open") == probe.EXIT_UNHEALTHY

    def test_stale_freshness_off_hours_exits_zero(self):
        assert probe.exit_code_for(1, 1, 0, "closed") == 0
        assert probe.exit_code_for(1, 1, 0, "extended") == 0

    def test_stale_freshness_with_unknown_market_state_exits_zero(self):
        assert probe.exit_code_for(1, 1, 0, None) == 0

    def test_endpoint_pending_exits_zero(self):
        assert probe.exit_code_for(1, 1, None, "open") == 0


# ── history row construction ─────────────────────────────────────────────────

class TestBuildRunsRow:
    def _edge_row(self, ok=1, latency=90, detail="edge_ok"):
        return {"source": "src", "ok": ok, "http_status": 200,
                "latency_ms": latency, "detail": detail, "checked_at": "2026-06-12T19:00:00Z"}

    def test_assembles_all_columns(self):
        user = {"ok": 1, "detail": "clerk_redirect"}
        fresh = {"freshness_ok": 1, "tick_fresh": 1, "scan_fresh": 1,
                 "market_state": "open", "detail": "fresh"}
        row = probe.build_runs_row(self._edge_row(), user, fresh,
                                   run_at="2026-06-12T19:00:00Z", latency_ms=120.0)
        assert row["run_at"] == "2026-06-12T19:00:00Z"
        assert row["edge_ok"] == 1
        assert row["user_path_ok"] == 1
        assert row["freshness_ok"] == 1
        assert row["tick_fresh"] == 1
        assert row["scan_fresh"] == 1
        assert row["latency_ms"] == 120.0
        detail = json.loads(row["detail"])
        assert detail == {"edge": "edge_ok", "user_path": "clerk_redirect",
                          "freshness": "fresh", "market_state": "open"}

    def test_pending_freshness_keeps_nulls(self):
        user = {"ok": 1, "detail": "clerk_redirect"}
        fresh = {"freshness_ok": None, "tick_fresh": None, "scan_fresh": None,
                 "market_state": None, "detail": "endpoint_pending"}
        row = probe.build_runs_row(self._edge_row(), user, fresh,
                                   run_at="x", latency_ms=None)
        assert row["freshness_ok"] is None
        assert row["tick_fresh"] is None
        assert row["scan_fresh"] is None
        assert row["latency_ms"] is None
        assert json.loads(row["detail"])["freshness"] == "endpoint_pending"


# ── turso_http: history insert + 30d prune pipeline ──────────────────────────

class TestBuildInsertRunPipeline:
    _ROW = {"run_at": "2026-06-12T19:00:00Z", "edge_ok": 1, "user_path_ok": 1,
            "freshness_ok": None, "tick_fresh": None, "scan_fresh": None,
            "detail": "{}", "latency_ms": 120.0}

    def test_insert_carries_every_column_as_named_arg(self):
        body = turso_http.build_insert_run_pipeline(self._ROW)
        stmt = body["requests"][0]["stmt"]
        assert "INSERT INTO external_probe_runs" in stmt["sql"]
        names = {a["name"]: a["value"] for a in stmt["named_args"]}
        assert names["run_at"] == {"type": "text", "value": "2026-06-12T19:00:00Z"}
        assert names["edge_ok"] == {"type": "integer", "value": "1"}
        assert names["freshness_ok"] == {"type": "null"}
        assert names["latency_ms"] == {"type": "float", "value": 120.0}

    def test_pipeline_prunes_rows_older_than_30_days(self):
        body = turso_http.build_insert_run_pipeline(self._ROW)
        prune = body["requests"][1]["stmt"]["sql"]
        assert "DELETE FROM external_probe_runs" in prune
        assert "-30 days" in prune
        assert body["requests"][-1] == {"type": "close"}


# ── run_probe orchestration (mocked transport + DB) ──────────────────────────

def _patch_happy_network(monkeypatch):
    monkeypatch.setattr(probe, "probe_endpoint",
                        lambda url, **k: _ok_probe(payload={"ok": True}))
    monkeypatch.setattr(probe, "probe_user_path", lambda url, **k: _user_path_raw())
    monkeypatch.setattr(probe, "probe_freshness", lambda url, token, **k: _freshness_raw())


class TestRunProbe:
    def test_writes_classified_row_and_history_row(self, monkeypatch):
        _patch_happy_network(monkeypatch)
        written, history = {}, {}
        monkeypatch.setattr(probe, "upsert_external_probe", lambda row: written.update(row))
        monkeypatch.setattr(probe, "insert_external_probe_run", lambda row: history.update(row))
        outcome = probe.run_probe(source="test/edge")
        assert outcome["edge_row"]["source"] == "test/edge"
        assert outcome["edge_row"]["ok"] == 1
        assert written == outcome["edge_row"]  # the exact classified row reached the writer
        assert history == outcome["runs_row"]
        assert history["edge_ok"] == 1
        assert history["user_path_ok"] == 1
        assert history["freshness_ok"] == 1
        assert outcome["exit_code"] == 0

    def test_user_path_internal_error_is_isolated_and_fails_loud(self, monkeypatch):
        _patch_happy_network(monkeypatch)

        def _bug(url, **k):
            raise RuntimeError("probe bug")
        monkeypatch.setattr(probe, "probe_user_path", _bug)
        written, history = {}, {}
        monkeypatch.setattr(probe, "upsert_external_probe", lambda row: written.update(row))
        monkeypatch.setattr(probe, "insert_external_probe_run", lambda row: history.update(row))
        outcome = probe.run_probe(source="test/edge")
        assert written["ok"] == 1  # edge write still landed
        assert history["user_path_ok"] == 0
        assert "internal" in json.loads(history["detail"])["user_path"]
        assert outcome["exit_code"] == probe.EXIT_UNHEALTHY

    def test_unhealthy_edge_exits_nonzero(self, monkeypatch):
        _patch_happy_network(monkeypatch)
        monkeypatch.setattr(probe, "probe_endpoint",
                            lambda url, **k: {"reachable": False, "detail": "timeout"})
        monkeypatch.setattr(probe, "upsert_external_probe", lambda row: None)
        monkeypatch.setattr(probe, "insert_external_probe_run", lambda row: None)
        assert probe.main() == probe.EXIT_UNHEALTHY

    def test_pending_freshness_keeps_exit_zero(self, monkeypatch):
        _patch_happy_network(monkeypatch)
        monkeypatch.setattr(probe, "probe_freshness",
                            lambda url, token, **k: _freshness_raw(status=404, payload={}))
        monkeypatch.setattr(probe, "upsert_external_probe", lambda row: None)
        history = {}
        monkeypatch.setattr(probe, "insert_external_probe_run", lambda row: history.update(row))
        assert probe.main() == 0
        assert history["freshness_ok"] is None

    def test_main_returns_1_when_write_fails(self, monkeypatch):
        _patch_happy_network(monkeypatch)

        def _boom(_row):
            raise turso_http.TursoHttpError("down")
        monkeypatch.setattr(probe, "upsert_external_probe", _boom)
        assert probe.main() == 1

    def test_main_returns_1_when_history_write_fails(self, monkeypatch):
        _patch_happy_network(monkeypatch)
        monkeypatch.setattr(probe, "upsert_external_probe", lambda row: None)

        def _boom(_row):
            raise turso_http.TursoHttpError("down")
        monkeypatch.setattr(probe, "insert_external_probe_run", _boom)
        assert probe.main() == 1


# ── dead-man's-switch reader ─────────────────────────────────────────────────

_NOW = datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc)


def _row(ok=1, ago_seconds=60, detail="edge_ok"):
    checked = (_NOW - timedelta(seconds=ago_seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {"source": "github-actions/edge", "ok": ok, "http_status": 200,
            "latency_ms": 50, "detail": detail, "checked_at": checked}


class TestDeadMansSwitch:
    def test_fresh_ok_row_is_healthy(self):
        result = reader.classify_external_probe(_row(ok=1, ago_seconds=60), now=_NOW)
        assert result["verdict"] == reader.VERDICT_HEALTHY

    def test_fresh_failed_row_is_down(self):
        result = reader.classify_external_probe(_row(ok=0, ago_seconds=60, detail="status_http_503"), now=_NOW)
        assert result["verdict"] == reader.VERDICT_DOWN
        assert result["reason"] == "status_http_503"

    def test_stale_ok_row_is_stale_not_healthy(self):
        # The critical case: a frozen ok=1 must NOT read as green.
        result = reader.classify_external_probe(_row(ok=1, ago_seconds=30 * 60), now=_NOW)
        assert result["verdict"] == reader.VERDICT_STALE
        assert result["reason"] == "prober_silent"

    def test_missing_row_is_stale(self):
        assert reader.classify_external_probe(None, now=_NOW)["verdict"] == reader.VERDICT_STALE
        assert reader.classify_external_probe({}, now=_NOW)["verdict"] == reader.VERDICT_STALE

    def test_boundary_just_inside_window_is_trusted(self):
        result = reader.classify_external_probe(_row(ok=1, ago_seconds=reader.STALE_AFTER_SECONDS - 1), now=_NOW)
        assert result["verdict"] == reader.VERDICT_HEALTHY

    def test_boundary_just_past_window_is_stale(self):
        result = reader.classify_external_probe(_row(ok=1, ago_seconds=reader.STALE_AFTER_SECONDS + 1), now=_NOW)
        assert result["verdict"] == reader.VERDICT_STALE

    def test_age_seconds_clamps_future_timestamps_to_zero(self):
        future = (_NOW + timedelta(seconds=120)).isoformat().replace("+00:00", "Z")
        assert reader.age_seconds(future, now=_NOW) == 0.0
