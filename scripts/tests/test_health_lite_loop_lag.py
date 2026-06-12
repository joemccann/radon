"""/health/lite must report event-loop lag (DUR-12).

The June wedges were diagnosed with ad-hoc py-spy because nothing measured
the uvicorn event loop. `loop_lag_ms` is one timed call_soon roundtrip — a
healthy loop turns it around in microseconds; a loop starved by blocking
work shows milliseconds-to-seconds. The host-metrics sampler reads it every
minute via the trusted-local bypass.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from api import server as srv


@pytest.mark.asyncio
async def test_health_lite_payload_carries_loop_lag_ms(monkeypatch):
    monkeypatch.setattr(srv, "check_ib_gateway", AsyncMock(return_value={}))
    payload = await srv.health_lite()
    assert isinstance(payload["loop_lag_ms"], float)
    # An idle test loop turns the roundtrip around far inside a second.
    assert 0.0 <= payload["loop_lag_ms"] < 1000.0


@pytest.mark.asyncio
async def test_health_lite_keeps_its_existing_coarse_fields(monkeypatch):
    monkeypatch.setattr(
        srv,
        "check_ib_gateway",
        AsyncMock(return_value={"auth_state": "authenticated"}),
    )
    payload = await srv.health_lite()
    assert payload["status"] == "ok"
    assert payload["auth_state"] == "authenticated"
    for key in ("service_state", "upstream_dead", "port_listening"):
        assert key in payload


@pytest.mark.asyncio
async def test_measure_event_loop_lag_is_cheap_and_nonnegative():
    lag_ms = await srv._measure_event_loop_lag_ms()
    assert isinstance(lag_ms, float)
    assert 0.0 <= lag_ms < 1000.0
