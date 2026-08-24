"""2026-08-24 scan storm: the intraday scan routes must not re-spawn on retry.

Three periodic callers plus a 5 s client poll superimposed on /vcg/scan,
/gex/scan, /regime/scan and /breadth/scan; a 502 (subprocess lane exhausted)
cleared every in-flight guard instantly, so each poll re-fired a subprocess
on a 2-vCPU box and starved the IB Gateway JVM (84 relay tick stalls vs 0
the prior Friday). One admission policy now fronts all four routes: a
completed scan is served from cache for the cooldown, and a failed scan is
refused with 429 + Retry-After for the backoff instead of being re-run.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from scripts.api.scan_gate import (  # noqa: E402
    SCAN_COOLDOWN_S,
    SCAN_FAILURE_BACKOFF_S,
    ScanGate,
)


class _Clock:
    def __init__(self) -> None:
        self.t = 1_000.0

    def __call__(self) -> float:
        return self.t


# ── policy ────────────────────────────────────────────────────────


def test_a_fresh_gate_admits_immediately() -> None:
    gate = ScanGate("vcg", clock=_Clock())
    assert gate.retry_after() == 0
    assert not gate.in_cooldown()
    assert not gate.in_backoff()


def test_a_completed_scan_holds_the_cooldown_until_it_lapses() -> None:
    clock = _Clock()
    gate = ScanGate("vcg", clock=clock)
    gate.mark_success()
    assert gate.in_cooldown()
    assert gate.retry_after() == pytest.approx(SCAN_COOLDOWN_S)
    clock.t += SCAN_COOLDOWN_S
    assert not gate.in_cooldown()
    assert gate.retry_after() == 0


def test_a_failed_scan_arms_the_backoff_and_a_success_clears_it() -> None:
    clock = _Clock()
    gate = ScanGate("regime", clock=clock)
    gate.mark_failure()
    assert gate.in_backoff()
    assert gate.retry_after() == pytest.approx(SCAN_FAILURE_BACKOFF_S)
    clock.t += SCAN_FAILURE_BACKOFF_S / 2
    gate.mark_success()
    assert not gate.in_backoff()


# ── routes ────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def localhost_bypass(monkeypatch):
    from scripts.api import auth, server

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "test_mode", False)
    yield


@pytest.fixture
def clock():
    return _Clock()


@pytest.fixture(autouse=True)
def fresh_gates(monkeypatch, clock):
    from scripts.api import server

    for name in ("regime", "breadth", "vcg", "gex"):
        monkeypatch.setitem(server.SCAN_GATES, name, ScanGate(name, clock=clock))
    yield


@pytest.fixture
def client():
    from scripts.api.server import app

    return TestClient(app)


def _result(ok: bool, data=None, error=None):
    from api.subprocess import ScriptResult

    return ScriptResult(ok=ok, data=data, error=error, exit_code=0 if ok else 1)


@pytest.mark.parametrize("route", ["/regime/scan", "/vcg/scan", "/gex/scan", "/breadth/scan"])
def test_a_failed_scan_is_refused_not_rerun_inside_the_backoff(client, route) -> None:
    calls = []

    async def _exhausted(*args, **kwargs):
        calls.append(args)
        return _result(False, error="Subprocess capacity exhausted")

    with patch("scripts.api.server.run_script", side_effect=_exhausted):
        first = client.post(route)
        second = client.post(route)

    assert first.status_code == 502
    assert second.status_code == 429
    assert int(second.headers["Retry-After"]) >= 1
    assert len(calls) == 1


def test_the_backoff_lapses_and_the_scan_runs_again(client, clock) -> None:
    calls = []

    async def _exhausted(*args, **kwargs):
        calls.append(args)
        return _result(False, error="Subprocess capacity exhausted")

    with patch("scripts.api.server.run_script", side_effect=_exhausted):
        client.post("/regime/scan")
        clock.t += SCAN_FAILURE_BACKOFF_S
        client.post("/regime/scan")

    assert len(calls) == 2


def test_regime_scan_serves_the_cache_inside_the_cooldown_without_spawning(client) -> None:
    payload = {"scan_time": "2026-08-24T14:00:00", "signals": []}
    calls = []

    async def _ok(*args, **kwargs):
        calls.append(args)
        return _result(True, data=payload)

    with patch("scripts.api.server.run_script", side_effect=_ok), patch(
        "scripts.api.server._write_cache"
    ), patch("scripts.api.server._read_cache", return_value=payload):
        first = client.post("/regime/scan")
        second = client.post("/regime/scan")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == payload
    assert len(calls) == 1
