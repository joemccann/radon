"""IB_GATEWAY_MODE can no longer select a local Gateway lifecycle (audit §3.2).

The docker and launchd lifecycles were retired: production only ever ran
``cloud`` (cloud/scripts/check-env.py refuses anything else at deploy time),
but FastAPI still read the variable and defaulted to ``docker``, so a drifted
or missing value reached ``docker compose`` and could fire a 2FA push. The
module now has one behavior whatever the environment says: probe the remote
Gateway, never spawn a process, never take the push lease.
"""
from __future__ import annotations

import asyncio
import importlib

import pytest

from scripts.api import ib_gateway as gw


@pytest.fixture(params=["docker", "launchd", "dokcer", "", None])
def drifted_mode(request, monkeypatch, tmp_path):
    """Re-import the module under each drifted env, then restore it."""
    if request.param is None:
        monkeypatch.delenv("IB_GATEWAY_MODE", raising=False)
    else:
        monkeypatch.setenv("IB_GATEWAY_MODE", request.param)
    monkeypatch.setenv("RADON_HOST_ROLE", "combined")
    monkeypatch.setenv("IB_2FA_LOCK_PATH", str(tmp_path / "lock.json"))
    module = importlib.reload(gw)
    monkeypatch.setattr(module, "_port_listening", lambda *a, **k: False)
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("spawned a Gateway process")),
    )
    monkeypatch.setattr(
        module.ib_2fa_lock, "acquire_2fa_push_lock",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("took the 2FA push lease")),
    )
    yield module
    monkeypatch.undo()
    importlib.reload(gw)


def test_ensure_only_reports_reachability(drifted_mode):
    result = asyncio.run(drifted_mode.ensure_ib_gateway())
    assert result["gateway_mode"] == "cloud"
    assert result["status"] == "unreachable"


def test_restart_refuses(drifted_mode):
    result = asyncio.run(drifted_mode.restart_ib_gateway())
    assert result["gateway_mode"] == "cloud"
    assert result["restarted"] is False


def test_health_reports_cloud(drifted_mode):
    result = asyncio.run(drifted_mode.check_ib_gateway())
    assert result["gateway_mode"] == "cloud"
    assert result["auth_state"] == "unreachable"
