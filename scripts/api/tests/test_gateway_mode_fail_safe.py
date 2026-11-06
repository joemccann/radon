"""An unrecognized IB_GATEWAY_MODE must fail safe (audit §3.2).

Each mode predicate compared the raw env string, so a typo made all three
False. The runtime recovery predicate read "not cloud, not docker, not
launchd" as "auto-restart allowed", and restart/ensure fell through to the
launchd lifecycle. An unknown mode now never restarts or starts anything.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from scripts.api import server

gw = server.ib_gateway


@pytest.mark.parametrize("raw, expected", [
    ("cloud", "cloud"), ("docker", "docker"), ("launchd", "launchd"),
    ("dokcer", "unknown"), ("Cloud", "unknown"), ("", "unknown"),
])
def test_gateway_mode_parses_to_one_enum(monkeypatch, raw, expected):
    monkeypatch.setattr(gw, "GATEWAY_MODE", raw)
    assert gw.gateway_mode() is gw.GatewayMode(expected)


@pytest.mark.parametrize("raw", ["cloud", "docker", "launchd", "dokcer", ""])
def test_runtime_failure_never_auto_restarts(monkeypatch, raw):
    monkeypatch.setattr(gw, "GATEWAY_MODE", raw)
    assert server._should_auto_restart_ib_gateway_after_runtime_failure() is False


def test_unknown_mode_restart_refuses_without_touching_a_lifecycle(monkeypatch):
    monkeypatch.setattr(gw, "GATEWAY_MODE", "dokcer")
    monkeypatch.setattr(gw, "_app_role_refusal", lambda action: None)
    monkeypatch.setattr(gw, "_restart_launchd", AsyncMock(side_effect=AssertionError("launchd")))
    monkeypatch.setattr(gw, "_restart_docker", AsyncMock(side_effect=AssertionError("docker")))
    monkeypatch.setattr(gw, "_acquire_2fa_push_lock_bounded",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("lease")))
    result = asyncio.run(gw.restart_ib_gateway())
    assert result["restarted"] is False
    assert result["gateway_mode"] == "unknown"
    assert "IB_GATEWAY_MODE" in result["error"]


def test_unknown_mode_ensure_refuses_without_touching_a_lifecycle(monkeypatch):
    monkeypatch.setattr(gw, "GATEWAY_MODE", "dokcer")
    monkeypatch.setattr(gw, "_app_role_refusal", lambda action: None)
    monkeypatch.setattr(gw, "_ensure_launchd", AsyncMock(side_effect=AssertionError("launchd")))
    monkeypatch.setattr(gw, "_ensure_docker_container", AsyncMock(side_effect=AssertionError("docker")))
    result = asyncio.run(gw.ensure_ib_gateway())
    assert result["status"] == "refused"
    assert result["gateway_mode"] == "unknown"
