"""POST /portfolio/sync must heartbeat a miss that never reaches service_cycle.

2026-10-14 16:10Z page 1e269d9f: radon-portfolio-sync.service got HTTP 502
for 15 minutes while /health stayed authenticated and orders-sync stayed
fresh. service_cycle("portfolio-sync") lives inside ib_sync.save_portfolio,
so a 502 left state=ok and froze updated_at. The open 10-minute window
paged silence. Heartbeat error (never ok) when the gateway is up. Stay
silent when the gateway is down or awaiting 2FA so grouping can still
page stale.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from scripts.api import server
from scripts.api.subprocess import ScriptResult


def _fail(error: str) -> server._IBSyncOutcome:
    return server._IBSyncOutcome(
        result=ScriptResult(ok=False, error=error),
        error=error,
    )


def _capture(monkeypatch):
    captured: list[tuple] = []

    def fake_hrana(sql, args=(), *rest, **kwargs):
        captured.append((sql, tuple(args)))
        return []

    monkeypatch.setattr(server.db_http, "hrana_execute", fake_hrana)
    return captured


@pytest.mark.asyncio
async def test_portfolio_sync_miss_heartbeats_error_when_gateway_up(monkeypatch):
    """A connect-pattern 502 while the gateway is authenticated must not
    leave portfolio-sync silent. The 2026-10-14 streak armed the 15s IB
    cooldown (instant middle retry) and never wrote service_health."""
    captured = _capture(monkeypatch)
    # Split so the source line is not a contiguous account id. The heartbeat
    # must still scrub that shape before it lands on service_health.
    account = "U" + "1234567"

    async def fake_sync():
        return _fail(f"TimeoutError: connect timed out account {account}")

    async def fake_gw(pool_status=None, pool=None):
        return {
            "port_listening": True,
            "upstream_dead": False,
            "auth_state": "authenticated",
        }

    monkeypatch.setattr(server, "_coordinated_portfolio_sync", fake_sync)
    monkeypatch.setattr(server, "check_ib_gateway", fake_gw)

    with pytest.raises(HTTPException) as exc:
        await server.portfolio_sync()

    assert exc.value.status_code == 502
    assert captured, "a miss before service_cycle must heartbeat portfolio-sync"
    sql, args = captured[0]
    assert "INSERT INTO service_health" in sql
    assert args[0] == "portfolio-sync"
    assert args[1] == "error"
    assert args[1] != "ok"
    err = args[4] or ""
    assert "sync-failed" in err
    assert "TimeoutError" in err
    assert account not in err
    assert "[redacted-account]" in err


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "gw",
    [
        {"port_listening": False, "upstream_dead": False, "auth_state": "unreachable"},
        {"port_listening": True, "upstream_dead": True, "auth_state": "unreachable"},
        {"port_listening": True, "upstream_dead": False, "auth_state": "awaiting_2fa"},
        {"port_listening": True, "upstream_dead": False, "auth_state": "unreachable"},
    ],
)
async def test_portfolio_sync_miss_stays_silent_when_grouping_should_page(monkeypatch, gw):
    """Gateway down or 2FA must not write a fresh error row. A fresh row
    would clear the stale bucket and pull portfolio-sync out of the
    ib-gateway-grouped page."""
    captured = _capture(monkeypatch)

    async def fake_sync():
        return _fail("IB Gateway is not accepting connections")

    async def fake_gw(pool_status=None, pool=None):
        return gw

    monkeypatch.setattr(server, "_coordinated_portfolio_sync", fake_sync)
    monkeypatch.setattr(server, "check_ib_gateway", fake_gw)

    with pytest.raises(HTTPException) as exc:
        await server.portfolio_sync()

    assert exc.value.status_code == 502
    assert captured == []


@pytest.mark.asyncio
async def test_portfolio_sync_probe_failure_still_heartbeats(monkeypatch):
    """A raising gateway probe used to return with no row. That is the
    same silence class. Heartbeat error."""
    captured = _capture(monkeypatch)

    async def fake_sync():
        return _fail("Script ib_sync.py failed (code 1)")

    async def fake_gw(pool_status=None, pool=None):
        raise RuntimeError("probe down")

    monkeypatch.setattr(server, "_coordinated_portfolio_sync", fake_sync)
    monkeypatch.setattr(server, "check_ib_gateway", fake_gw)

    with pytest.raises(HTTPException):
        await server.portfolio_sync()

    assert captured, "a probe failure must not leave portfolio-sync silent"
    assert captured[0][1][0] == "portfolio-sync"
    assert captured[0][1][1] == "error"


@pytest.mark.asyncio
async def test_portfolio_sync_success_does_not_stamp_error(monkeypatch):
    captured = _capture(monkeypatch)

    async def fake_sync():
        return server._IBSyncOutcome(
            result=ScriptResult(ok=True, data={"bankroll": 1, "positions": []}),
            payload={"bankroll": 1, "positions": []},
        )

    async def fake_gw(pool_status=None, pool=None):
        raise AssertionError("success must not probe the gateway for a miss heartbeat")

    monkeypatch.setattr(server, "_coordinated_portfolio_sync", fake_sync)
    monkeypatch.setattr(server, "check_ib_gateway", fake_gw)

    payload = await server.portfolio_sync()
    assert payload["bankroll"] == 1
    assert captured == []
