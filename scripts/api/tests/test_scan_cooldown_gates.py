"""The ad hoc scan cooldowns are ScanGate instances (audit §3.2, §2 #8).

/leap, /theta-harvester, /strength-confirmation and the hourly flow tabs
(/scan, /discover, /flow-analysis) each kept a bare monotonic float that
started at 0.0. ``monotonic() - 0.0 < cooldown`` is true on a host whose
clock has run for less than the cooldown, so a scan that never ran was
treated as cooling down (served a stale cache, or refused 429). ScanGate's
"never succeeded" is -inf.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from scripts.api import auth, server
from scripts.api.subprocess import ScriptResult

_ROUTES = [
    ("_LEAP_GATE", "/leap/scan?preset=mag7", "LEAP_COOLDOWN_S"),
    ("_THETA_GATE", "/theta-harvester/scan", "THETA_COOLDOWN_S"),
    ("_STRENGTH_GATE", "/strength-confirmation/scan", "STRENGTH_COOLDOWN_S"),
]


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "test_mode", False)
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    monkeypatch.setattr(server, "_FLOW_TAB_GATES", {})
    # A host that booted 100 s ago.
    monkeypatch.setattr(time, "monotonic", lambda: 100.0)
    calls = []

    async def run_script(script, args, timeout=None):
        calls.append(script)
        return ScriptResult(ok=True, data={"scan_time": "t", "results": [], "universe": "preset:x"})

    monkeypatch.setattr(server, "run_script", run_script)
    monkeypatch.setattr(server, "_read_cache", lambda _path: {"stale": True})
    return TestClient(server.app), calls


@pytest.mark.parametrize("attr, path, cooldown", _ROUTES)
def test_never_scanned_preset_runs_on_a_freshly_booted_host(client, monkeypatch, attr, path, cooldown):
    http, calls = client
    gate = server._preset_scan_gate(attr, getattr(server, cooldown))
    monkeypatch.setattr(server, attr, gate)

    resp = http.post(path)

    assert resp.status_code == 200, resp.text
    assert len(calls) == 1
    assert gate.in_cooldown()


def test_never_scanned_flow_tab_runs_on_a_freshly_booted_host(client):
    http, calls = client
    resp = http.post("/scan")
    assert resp.status_code == 200, resp.text
    assert calls == ["scanner.py"]
    assert server._flow_tab_gate("scanner").in_cooldown()


def test_flow_tab_gates_are_scan_gates():
    assert isinstance(server._flow_tab_gate("discover"), server.ScanGate)
    assert server._flow_tab_gate("discover").cooldown_s == server.FLOW_TAB_COOLDOWN_S
