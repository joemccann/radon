"""Restart ladder applicability (audit §3.2).

FastAPI never cycles the Gateway, so no per-process restart ladder exists.
/health used to report one anyway (attempt_count 0, next_attempt_in_secs 0),
which read as "no retry pending" to every consumer. It is reported not
applicable; the push lock (the part that IS live) is kept.
"""
from __future__ import annotations

import asyncio

from scripts.api import server

gw = server.ib_gateway

_LADDER_KEYS = ("attempt_count", "last_attempt_at", "next_attempt_after",
                "next_attempt_in_secs", "last_outcome")


def _cloud(monkeypatch, tmp_path):
    monkeypatch.setenv("RADON_HOST_ROLE", "broker")
    monkeypatch.setenv("IB_2FA_LOCK_PATH", str(tmp_path / "lock.json"))
    monkeypatch.setattr(gw, "_port_listening", lambda *a, **k: False)


def test_cloud_health_reports_ladder_not_applicable(monkeypatch, tmp_path):
    _cloud(monkeypatch, tmp_path)
    result = asyncio.run(gw.check_ib_gateway())
    backoff = result["restart_backoff"]
    assert backoff["applicable"] is False
    assert backoff["gateway_mode"] == "cloud"
    for key in _LADDER_KEYS:
        assert key not in backoff, key
    assert "push_lock" in backoff


def test_reset_restart_backoff_reports_the_lease_it_released(monkeypatch, tmp_path):
    monkeypatch.setenv("IB_2FA_LOCK_PATH", str(tmp_path / "lock.json"))
    gw.ib_2fa_lock.acquire_2fa_push_lock("operator", ttl_secs=600)
    result = gw.reset_restart_backoff()
    assert result["reset"] is True
    assert result["lock_released"]["holder"] == "operator"
    assert gw.ib_2fa_lock.check_2fa_push_lock() is None
