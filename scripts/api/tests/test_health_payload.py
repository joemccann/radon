"""The public /health surface must not leak account IDs, IB auth/connection
state, or internal topology.

`/health` is auth-exempt and reachable from the public internet via Caddy's
`handle_path /api/ib/*` (→ /api/ib/health). Untrusted (proxied/public) callers
therefore get liveness only; trusted local/tailnet callers — the admin panel
via server-side radonFetch, the watchdogs curling 127.0.0.1:8321/health — keep
the full payload. The untrusted branch must also short-circuit BEFORE
check_ib_gateway() so an internet GET can't drive the pool-reconnect / heal
side effects on that call path.
"""

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

_scripts_dir = os.path.join(os.path.dirname(__file__), os.pardir, os.pardir)
sys.path.insert(0, os.path.abspath(_scripts_dir))

from scripts.api import server


def _request(host, extra_headers=None):
    return SimpleNamespace(
        client=SimpleNamespace(host=host) if host is not None else None,
        headers=extra_headers or {},
        url=SimpleNamespace(path="/health"),
    )


# Fields that must never reach an untrusted caller.
_SENSITIVE_KEYS = {"ib_gateway", "ib_pool", "auth_state", "managed_accounts", "restart_backoff"}


class TestHealthPayloadScoping:
    @pytest.mark.asyncio
    async def test_public_proxied_caller_gets_liveness_only(self, monkeypatch):
        # Loopback peer + forwarding header == arrived through Caddy. Must get
        # liveness only, and must NOT invoke check_ib_gateway (no side effects).
        called = {"gw": False}

        async def _should_not_run(*args, **kwargs):
            called["gw"] = True
            return {"auth_state": "authenticated", "managed_accounts": ["U1234567"]}

        monkeypatch.setattr(server, "check_ib_gateway", _should_not_run)

        req = _request("127.0.0.1", {"X-Forwarded-For": "8.8.8.8"})
        result = await server.health(req)

        assert result == {"status": "ok"}
        assert not (_SENSITIVE_KEYS & set(result)), result
        assert called["gw"] is False  # side-effect-free for untrusted callers

    @pytest.mark.asyncio
    async def test_public_direct_caller_gets_liveness_only(self, monkeypatch):
        async def _should_not_run(*args, **kwargs):
            raise AssertionError("check_ib_gateway must not run for public callers")

        monkeypatch.setattr(server, "check_ib_gateway", _should_not_run)

        req = _request("8.8.8.8")  # real remote IP (uvicorn proxy-headers rewrite)
        result = await server.health(req)

        assert result == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_trusted_local_caller_gets_full_payload(self, monkeypatch):
        async def _gw(*args, **kwargs):
            return {"auth_state": "authenticated", "managed_accounts": ["U1234567"], "port": 4001}

        monkeypatch.setattr(server, "check_ib_gateway", _gw)
        monkeypatch.setattr(server, "ib_pool", SimpleNamespace(status=lambda: {"sync": {"connected": True}}))

        req = _request("127.0.0.1")  # loopback, no forwarding headers
        result = await server.health(req)

        assert result["status"] == "ok"
        assert result["ib_gateway"]["auth_state"] == "authenticated"
        assert result["ib_gateway"]["managed_accounts"] == ["U1234567"]
        assert result["ib_pool"] == {"sync": {"connected": True}}

    @pytest.mark.asyncio
    async def test_trusted_tailnet_caller_gets_full_payload(self, monkeypatch):
        async def _gw(*args, **kwargs):
            return {"auth_state": "awaiting_2fa"}

        monkeypatch.setattr(server, "check_ib_gateway", _gw)
        monkeypatch.setattr(server, "ib_pool", SimpleNamespace(status=lambda: {}))

        req = _request("100.112.32.16")  # cloud-thin laptop over Tailscale
        result = await server.health(req)

        assert result["ib_gateway"]["auth_state"] == "awaiting_2fa"


class TestHealthProbeBounding:
    """/health must NEVER hang. The IB gateway probe (check_ib_gateway) can block
    for tens of seconds while the pool reconnects after a 2FA approval; when it
    does, uvicorn workers pile up and every health-dependent UI surface shows a
    timeout / RELAY OFFLINE state even though IB is healthy. The probe is bounded
    by asyncio.wait_for and falls back to a degraded payload on timeout/error,
    while keeping the payload shape backward-compatible.
    """

    @pytest.mark.asyncio
    async def test_hanging_probe_returns_degraded_promptly(self, monkeypatch):
        async def _hangs(*args, **kwargs):
            await asyncio.sleep(60)  # simulate a pool mid-reconnect wedge

        monkeypatch.setattr(server, "check_ib_gateway", _hangs)
        monkeypatch.setattr(server, "ib_pool", SimpleNamespace(status=lambda: {"sync": {"connected": False}}))
        monkeypatch.setattr(server, "HEALTH_GATEWAY_PROBE_TIMEOUT_SECS", 0.05)

        req = _request("127.0.0.1")
        result = await asyncio.wait_for(server.health(req), timeout=2.0)

        # HTTP 200 + structured payload, same top-level keys as the happy path.
        assert result["status"] == "ok"
        assert set(result) >= {"status", "test_mode", "ib_gateway", "ib_pool", "uw"}
        # Degraded gateway: flagged, and the web-parsed nested keys still present.
        assert result["ib_gateway"]["probe_timed_out"] is True
        assert result["ib_gateway"]["auth_state"] == "unknown"
        assert result["ib_gateway"]["port_listening"] is False
        assert "container_state" in result["ib_gateway"]
        # Pool status (synchronous, fast) is still surfaced unchanged.
        assert result["ib_pool"] == {"sync": {"connected": False}}

    @pytest.mark.asyncio
    async def test_raising_probe_returns_degraded(self, monkeypatch):
        async def _boom(*args, **kwargs):
            raise RuntimeError("pool inspection blew up")

        monkeypatch.setattr(server, "check_ib_gateway", _boom)
        monkeypatch.setattr(server, "ib_pool", SimpleNamespace(status=lambda: {}))

        req = _request("127.0.0.1")
        result = await server.health(req)

        assert result["status"] == "ok"
        assert result["ib_gateway"]["probe_timed_out"] is True
        assert result["ib_gateway"]["auth_state"] == "unknown"

    @pytest.mark.asyncio
    async def test_healthy_probe_returns_normal_payload(self, monkeypatch):
        async def _gw(*args, **kwargs):
            return {
                "auth_state": "authenticated",
                "port_listening": True,
                "container_state": "running",
                "managed_accounts": ["U1234567"],
            }

        monkeypatch.setattr(server, "check_ib_gateway", _gw)
        monkeypatch.setattr(server, "ib_pool", SimpleNamespace(status=lambda: {"sync": {"connected": True}}))

        req = _request("127.0.0.1")
        result = await server.health(req)

        assert result["ib_gateway"]["auth_state"] == "authenticated"
        assert result["ib_gateway"]["port_listening"] is True
        assert "probe_timed_out" not in result["ib_gateway"]
        assert result["ib_pool"] == {"sync": {"connected": True}}


class TestHealthLite:
    """/health/lite is the side-effect-free, account-free coarse IB-state
    contract for high-frequency pollers (the standalone health daemon). It must
    call check_ib_gateway with pool=None so it never triggers reconnect_all/heal
    (that recovery heartbeat stays on /health), and it must never return account
    IDs, ports, restart backoff, or pool/topology detail.
    """

    @pytest.mark.asyncio
    async def test_lite_is_side_effect_free(self, monkeypatch):
        captured = {}

        async def _gw(pool_status=None, pool=None):
            captured["pool"] = pool
            captured["pool_status"] = pool_status
            return {"auth_state": "authenticated", "service_state": "healthy", "upstream_dead": False,
                    "port_listening": True, "managed_accounts": ["U1234567"]}

        monkeypatch.setattr(server, "check_ib_gateway", _gw)
        monkeypatch.setattr(server, "ib_pool", SimpleNamespace(status=lambda: {"sync": {"connected": True}}))

        await server.health_lite()

        # pool MUST be None — no reconnect_all / heal on the lite poll path.
        assert captured["pool"] is None
        # pool_status is still passed so auth_state can be derived in-memory.
        assert captured["pool_status"] == {"sync": {"connected": True}}

    @pytest.mark.asyncio
    async def test_lite_payload_is_coarse_and_account_free(self, monkeypatch):
        async def _gw(pool_status=None, pool=None):
            return {"auth_state": "awaiting_2fa", "service_state": "healthy", "upstream_dead": False,
                    "port_listening": True, "managed_accounts": ["U1234567"], "host": "127.0.0.1",
                    "port": 4001, "restart_backoff": {"attempt": 2}, "container_state": "running"}

        monkeypatch.setattr(server, "check_ib_gateway", _gw)
        monkeypatch.setattr(server, "ib_pool", SimpleNamespace(status=lambda: {}))

        result = await server.health_lite()

        assert result == {
            "status": "ok",
            "auth_state": "awaiting_2fa",
            "service_state": "healthy",
            "upstream_dead": False,
            "port_listening": True,
        }
        for leaked in ("managed_accounts", "host", "port", "restart_backoff", "container_state", "ib_pool"):
            assert leaked not in result, leaked

    @pytest.mark.asyncio
    async def test_lite_tolerates_missing_fields(self, monkeypatch):
        async def _gw(pool_status=None, pool=None):
            return {}  # cloud cold-start can omit everything

        monkeypatch.setattr(server, "check_ib_gateway", _gw)
        monkeypatch.setattr(server, "ib_pool", None)

        result = await server.health_lite()

        assert result == {
            "status": "ok",
            "auth_state": "unknown",
            "service_state": "unknown",
            "upstream_dead": False,
            "port_listening": False,
        }

    def test_auth_exempt_paths_exact_pin(self):
        # FULL set-equality pin, not membership checks: the perimeter is the
        # set itself. Three incidents shipped with green CI because an exempt
        # surface grew without review (world-callable /api/*, /health leaking
        # IB account IDs via the Caddy /api/ib/* bypass). Any change to this
        # set — adding OR removing — must update this test deliberately.
        #
        #   /health             — liveness probe; payload itself is
        #                         trust-scoped (untrusted callers get
        #                         {"status":"ok"} only)
        #   /ws-ticket/validate — internal WS ticket validation
        #   /docs, /openapi.json — FastAPI docs surface
        #
        # /health/lite must NEVER appear here — it would be world-readable
        # via Caddy /api/ib/health/lite (loopback daemon is covered by the
        # trusted-local bypass; public callers must get 401). See
        # feedback_health_endpoint_public_leak_and_trust_chokepoint.
        from scripts.api.server import AUTH_EXEMPT_PATHS

        assert AUTH_EXEMPT_PATHS == {
            "/health",
            "/ws-ticket/validate",
            "/docs",
            "/openapi.json",
        }
        assert "/health/lite" not in AUTH_EXEMPT_PATHS


class TestIbRecoveryHeartbeat:
    """The server-side recovery heartbeat must drive check_ib_gateway WITH the
    pool (so awaiting_2fa->authenticated reconnect_all fires) once the browser
    consumers move to the read-only /edge-health surface."""

    @pytest.mark.asyncio
    async def test_tick_drives_check_with_pool(self, monkeypatch):
        captured = {}

        async def _gw(pool_status=None, pool=None):
            captured["pool"] = pool
            captured["status"] = pool_status
            return {}

        fake_pool = SimpleNamespace(status=lambda: {"sync": {"connected": True}})
        monkeypatch.setattr(server, "check_ib_gateway", _gw)
        monkeypatch.setattr(server, "ib_pool", fake_pool)

        await server._ib_recovery_heartbeat_tick()

        assert captured["pool"] is fake_pool  # WITH pool => recovery path runs
        assert captured["status"] == {"sync": {"connected": True}}

    @pytest.mark.asyncio
    async def test_tick_noop_without_pool(self, monkeypatch):
        called = {"n": 0}

        async def _gw(**kwargs):
            called["n"] += 1
            return {}

        monkeypatch.setattr(server, "check_ib_gateway", _gw)
        monkeypatch.setattr(server, "ib_pool", None)

        await server._ib_recovery_heartbeat_tick()
        assert called["n"] == 0

    @pytest.mark.asyncio
    async def test_tick_swallows_exceptions(self, monkeypatch):
        async def _boom(**kwargs):
            raise RuntimeError("gateway probe failed")

        monkeypatch.setattr(server, "check_ib_gateway", _boom)
        monkeypatch.setattr(server, "ib_pool", SimpleNamespace(status=lambda: {}))

        await server._ib_recovery_heartbeat_tick()  # must not raise
