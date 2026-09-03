"""Tests for the FastAPI /leap/scan endpoint (preset + scan-by-ticker).

Same mocking strategy as test_theta_harvester_route.py: patch
``server.run_script`` and ``server._read_cache`` so the route runs
without a real subprocess. The subprocess owns the cache write + Turso
mirror + service_health row; the route just re-reads data/leap.json.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from api import auth, server
from api.subprocess import ScriptResult


@pytest.fixture(autouse=True)
def localhost_bypass(monkeypatch):
    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    # Pin test_mode False — RADON_API_TEST_MODE leaks process-wide from
    # test_demo_trial_expiry_route's import-time env set, and these tests
    # exercise the real subprocess path (the demo guard would hijack it).
    monkeypatch.setattr(server, "test_mode", False)
    yield


def _explicit_payload():
    return {
        "scan_time": "2026-07-05T14:00:00",
        "min_gap": 10.0,
        "universe": "explicit",
        "requested_tickers": ["NVDA", "AMD"],
        "results": [],
    }


def _preset_payload():
    return {
        "scan_time": "2026-07-05T14:00:00",
        "min_gap": 10.0,
        "universe": "preset:mag7",
        "requested_tickers": ["AAPL", "MSFT"],
        "results": [],
    }


def test_leap_ticker_scan_bypasses_preset_cooldown(monkeypatch):
    seeded = time.monotonic()
    monkeypatch.setattr(server, "_leap_last_scan", seeded)
    monkeypatch.setattr(server, "_leap_scan_lock", None)

    payload = _explicit_payload()
    calls: list[tuple[str, list[str], int | None]] = []

    async def fake_run_script(script, args, timeout=None):
        calls.append((script, args, timeout))
        return ScriptResult(ok=True, data={})

    monkeypatch.setattr(server, "run_script", fake_run_script)
    monkeypatch.setattr(server, "_read_cache", lambda _path: payload)

    client = TestClient(server.app)
    response = client.post("/leap/scan?tickers=nvda,amd")

    assert response.status_code == 200
    assert response.json()["requested_tickers"] == ["NVDA", "AMD"]
    assert calls == [
        (
            "leap_scanner_uw.py",
            ["--tickers", "NVDA,AMD", "--min-gap", "10.0", "--json", "--workers", "16"],
            300,
        )
    ]
    # A ticker scan must NOT advance the preset cooldown.
    assert server._leap_last_scan == seeded


def test_leap_preset_scan_inside_cooldown_never_serves_explicit_cache(monkeypatch):
    """An explicit-ticker cache never satisfies a preset request, and inside
    the window the route refuses (429) rather than re-spawning."""
    monkeypatch.setattr(server, "_leap_last_scan", time.monotonic())
    monkeypatch.setattr(server, "_leap_scan_lock", None)

    async def fake_run_script(*_args, **_kwargs):
        raise AssertionError("cooldown must not re-scan on a cache miss")

    monkeypatch.setattr(server, "run_script", fake_run_script)
    monkeypatch.setattr(server, "_read_cache", lambda _path: _explicit_payload())

    client = TestClient(server.app)
    response = client.post("/leap/scan?preset=mag7")

    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) >= 1


def test_leap_cooldown_is_keyed_on_route_not_preset(monkeypatch):
    monkeypatch.setattr(server, "_leap_last_scan", time.monotonic())
    monkeypatch.setattr(server, "_leap_scan_lock", None)

    calls: list[tuple[str, list[str], int | None]] = []

    async def fake_run_script(script, args, timeout=None):
        calls.append((script, args, timeout))
        return ScriptResult(ok=True, data={})

    monkeypatch.setattr(server, "run_script", fake_run_script)
    monkeypatch.setattr(server, "_read_cache", lambda _path: _preset_payload())

    client = TestClient(server.app)
    assert client.post("/leap/scan?preset=mag7").status_code == 200
    blocked = client.post("/leap/scan?preset=semis")
    assert blocked.status_code == 429
    assert 1 <= int(blocked.headers["Retry-After"]) <= server.LEAP_COOLDOWN_S
    assert calls == []

    monkeypatch.setattr(server, "_leap_last_scan", -1e9)
    assert client.post("/leap/scan?preset=semis").status_code == 200
    assert calls == [
        (
            "leap_scanner_uw.py",
            ["--preset", "semis", "--min-gap", "10.0", "--json", "--workers", "16"],
            3600,
        )
    ]


def test_leap_cooldown_serves_matching_preset_cache(monkeypatch):
    monkeypatch.setattr(server, "_leap_last_scan", time.monotonic())
    monkeypatch.setattr(server, "_leap_scan_lock", None)

    async def fake_run_script(*_args, **_kwargs):
        raise AssertionError("cooldown hit with matching cache must not re-scan")

    monkeypatch.setattr(server, "run_script", fake_run_script)
    monkeypatch.setattr(server, "_read_cache", lambda _path: _preset_payload())

    client = TestClient(server.app)
    response = client.post("/leap/scan?preset=mag7")

    assert response.status_code == 200
    assert response.json()["universe"] == "preset:mag7"


def test_leap_rejects_invalid_tickers(monkeypatch):
    async def fake_run_script(*_args, **_kwargs):
        raise AssertionError("invalid tickers should fail before subprocess")

    monkeypatch.setattr(server, "run_script", fake_run_script)

    client = TestClient(server.app)
    response = client.post("/leap/scan?tickers=MU1,AMD")

    assert response.status_code == 400
    assert "1-6 letter" in response.text


def test_leap_rejects_too_many_tickers(monkeypatch):
    async def fake_run_script(*_args, **_kwargs):
        raise AssertionError("oversized ticker list should fail before subprocess")

    monkeypatch.setattr(server, "run_script", fake_run_script)

    tickers = ",".join(f"{chr(ord('A') + i)}{chr(ord('A') + i)}" for i in range(26))
    client = TestClient(server.app)
    response = client.post(f"/leap/scan?tickers={tickers}")

    assert response.status_code == 400
    assert "max 25" in response.text


def test_leap_omitted_preset_forwards_largecaps(monkeypatch):
    monkeypatch.setattr(server, "_leap_last_scan", -1e9)
    monkeypatch.setattr(server, "_leap_scan_lock", None)

    payload = {
        "scan_time": "2026-08-13T14:00:00",
        "min_gap": 10.0,
        "universe": "preset:largecaps",
        "requested_tickers": ["AAPL", "NVDA"],
        "results": [],
    }
    calls: list[tuple[str, list[str], int | None]] = []

    async def fake_run_script(script, args, timeout=None):
        calls.append((script, args, timeout))
        return ScriptResult(ok=True, data={})

    monkeypatch.setattr(server, "run_script", fake_run_script)
    monkeypatch.setattr(server, "_read_cache", lambda _path: payload)

    client = TestClient(server.app)
    response = client.post("/leap/scan")

    assert response.status_code == 200
    assert response.json()["universe"] == "preset:largecaps"
    assert calls == [
        (
            "leap_scanner_uw.py",
            ["--preset", "largecaps", "--min-gap", "10.0", "--json", "--workers", "16"],
            3600,
        )
    ]
