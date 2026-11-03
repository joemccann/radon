"""Gate 1 backoff ladder applicability (audit §3.2).

restart_ib_gateway returns before Gate 1 in cloud mode, so the per-process
ladder never runs in production. /health used to report it anyway
(attempt_count 0, next_attempt_in_secs 0), which read as "no retry pending"
to every consumer. In cloud mode the ladder is now reported not applicable;
the push lock (the part that IS live in cloud mode) is kept.
"""
from __future__ import annotations

import asyncio

from scripts.api import server

gw = server.ib_gateway

_LADDER_KEYS = ("attempt_count", "last_attempt_at", "next_attempt_after",
                "next_attempt_in_secs", "last_outcome")


def _cloud(monkeypatch, tmp_path):
    monkeypatch.setattr(gw, "GATEWAY_MODE", "cloud")
    monkeypatch.setenv("RADON_HOST_ROLE", "broker")
    monkeypatch.setenv("IB_2FA_LOCK_PATH", str(tmp_path / "lock.json"))
    monkeypatch.setattr(gw, "_port_listening", lambda *a, **k: False)


def test_cloud_health_reports_ladder_not_applicable(monkeypatch, tmp_path):
    _cloud(monkeypatch, tmp_path)
    # A stale in-memory ladder must not leak into the cloud report either.
    monkeypatch.setitem(gw._restart_state, "attempt_count", 3)
    result = asyncio.run(gw.check_ib_gateway())
    backoff = result["restart_backoff"]
    assert backoff["applicable"] is False
    assert backoff["gateway_mode"] == "cloud"
    for key in _LADDER_KEYS:
        assert key not in backoff, key
    assert "push_lock" in backoff


def test_local_health_reports_ladder_applicable(monkeypatch, tmp_path):
    monkeypatch.setattr(gw, "GATEWAY_MODE", "docker")
    monkeypatch.setenv("IB_2FA_LOCK_PATH", str(tmp_path / "lock.json"))

    async def fake_check_docker():
        return {"port_listening": False, "gateway_mode": "docker"}

    monkeypatch.setattr(gw, "_check_docker", fake_check_docker)
    result = asyncio.run(gw.check_ib_gateway())
    backoff = result["restart_backoff"]
    assert backoff["applicable"] is True
    assert backoff["gateway_mode"] == "docker"
    for key in _LADDER_KEYS:
        assert key in backoff, key


def test_reset_restart_backoff_clears_last_outcome(monkeypatch, tmp_path):
    monkeypatch.setenv("IB_2FA_LOCK_PATH", str(tmp_path / "lock.json"))
    monkeypatch.setitem(gw._restart_state, "attempt_count", 2)
    monkeypatch.setitem(gw._restart_state, "next_attempt_after", 9e12)
    monkeypatch.setitem(gw._restart_state, "last_outcome", "awaiting_2fa")
    result = gw.reset_restart_backoff()
    assert result["previous"]["last_outcome"] == "awaiting_2fa"
    assert gw._restart_state["last_outcome"] is None
    assert gw._restart_state["attempt_count"] == 0
