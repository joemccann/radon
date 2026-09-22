from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from api import auth, server
from api.subprocess import ScriptResult


@pytest.fixture(autouse=True)
def live_local(monkeypatch):
    monkeypatch.setattr(server, "test_mode", False)
    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "_bounce_setup_scan_lock", None)


def _fake(calls, payload):
    async def fake_run_script(script, args, timeout=None):
        calls.append((script, args, timeout))
        return ScriptResult(ok=True, data=payload)
    return fake_run_script


PAYLOAD = {"scan_time": "2026-09-18T21:10:00+00:00", "universe": "largecaps", "bounce_count": 0, "results": []}


def test_preset_scan_runs_once_inside_cooldown(monkeypatch):
    clock = {"now": 10_000.0}
    monkeypatch.setattr(time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(server, "_bounce_setup_last_scan", -1e9)
    calls: list = []
    monkeypatch.setattr(server, "run_script", _fake(calls, PAYLOAD))
    monkeypatch.setattr(server, "_read_cache", lambda _path: PAYLOAD)

    client = TestClient(server.app)
    first = client.post("/bounce-setup/scan?preset=largecaps&limit=50")
    clock["now"] += 3599.0
    second = client.post("/bounce-setup/scan?preset=largecaps")

    assert first.status_code == 200
    assert second.json() == PAYLOAD
    assert calls == [("bounce_setup_scanner.py", ["--json", "--preset", "largecaps", "--limit", "50"], 600)]


def test_ticker_scan_bypasses_cooldown_and_returns_subprocess_payload(monkeypatch):
    monkeypatch.setattr(server, "_bounce_setup_last_scan", time.monotonic())
    explicit = {**PAYLOAD, "universe": "explicit"}
    calls: list = []
    monkeypatch.setattr(server, "run_script", _fake(calls, explicit))
    monkeypatch.setattr(server, "_read_cache", lambda _path: PAYLOAD)

    response = TestClient(server.app).post("/bounce-setup/scan?tickers=bac,jpm")

    assert response.json() == explicit
    assert calls == [("bounce_setup_scanner.py", ["--json", "BAC", "JPM"], 600)]


def test_rejects_invalid_ticker_before_subprocess(monkeypatch):
    async def boom(*_a, **_k):
        raise AssertionError("must not spawn")

    monkeypatch.setattr(server, "run_script", boom)
    assert TestClient(server.app).post("/bounce-setup/scan?tickers=MU1").status_code == 400


def test_subprocess_failure_is_502(monkeypatch):
    monkeypatch.setattr(server, "_bounce_setup_last_scan", -1e9)

    async def failing(*_a, **_k):
        return ScriptResult(ok=False, error="boom")

    monkeypatch.setattr(server, "run_script", failing)
    assert TestClient(server.app).post("/bounce-setup/scan").status_code == 502
