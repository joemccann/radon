from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from api import auth, server
from api.subprocess import ScriptResult


@pytest.fixture(autouse=True)
def force_live_mode(monkeypatch):
    """Pin test_mode False — RADON_API_TEST_MODE leaks process-wide from
    test_demo_trial_expiry_route's import-time env set, and these tests
    exercise the real subprocess path (the demo guard would hijack it)."""
    monkeypatch.setattr(server, "test_mode", False)
    yield


def test_theta_harvester_ticker_scan_bypasses_preset_cooldown(monkeypatch):
    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "_theta_last_scan", time.monotonic())
    monkeypatch.setattr(server, "_theta_scan_lock", None)

    payload = {
        "scan_time": "2026-06-24T16:00:00Z",
        "source": "Unusual Whales",
        "universe": "explicit",
        "requested_tickers": ["MU"],
        "tickers_scanned": 1,
        "candidates_found": 0,
        "theta_harvest_count": 0,
        "results": [],
    }
    calls: list[tuple[str, list[str], int | None]] = []

    async def fake_run_script(script: str, args: list[str], timeout: int | None = None):
        calls.append((script, args, timeout))
        return ScriptResult(ok=True, data=payload)

    monkeypatch.setattr(server, "run_script", fake_run_script)
    monkeypatch.setattr(server, "_read_cache", lambda _path: payload)

    client = TestClient(server.app)
    response = client.post("/theta-harvester/scan?ticker=mu")

    assert response.status_code == 200
    assert response.json()["requested_tickers"] == ["MU"]
    assert calls == [(
        "theta_harvester_scanner.py",
        ["--json", "--workers", "24", "MU", "--min-dte", "7", "--max-dte", "45", "--min-credit", "0.0"],
        420,
    )]


def test_theta_harvester_preset_scan_ignores_explicit_ticker_cache(monkeypatch):
    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "_theta_last_scan", time.monotonic())
    monkeypatch.setattr(server, "_theta_scan_lock", None)

    explicit_payload = {
        "scan_time": "2026-06-24T16:00:00Z",
        "source": "Unusual Whales",
        "universe": "explicit",
        "requested_tickers": ["MU"],
        "tickers_scanned": 1,
        "candidates_found": 0,
        "theta_harvest_count": 0,
        "results": [],
    }
    preset_payload = {
        **explicit_payload,
        "universe": "fallback:ndx100",
        "requested_tickers": ["AAPL", "MSFT"],
        "tickers_scanned": 2,
    }
    cache_reads = {"n": 0}
    calls: list[tuple[str, list[str], int | None]] = []

    def fake_read_cache(_path):
        cache_reads["n"] += 1
        return explicit_payload if cache_reads["n"] < 3 else preset_payload

    async def fake_run_script(script: str, args: list[str], timeout: int | None = None):
        calls.append((script, args, timeout))
        return ScriptResult(ok=True, data=preset_payload)

    monkeypatch.setattr(server, "run_script", fake_run_script)
    monkeypatch.setattr(server, "_read_cache", fake_read_cache)

    client = TestClient(server.app)
    response = client.post("/theta-harvester/scan?preset=ndx100")

    assert response.status_code == 200
    assert response.json()["universe"] == "fallback:ndx100"
    assert calls == [(
        "theta_harvester_scanner.py",
        ["--json", "--workers", "24", "--preset", "ndx100", "--min-dte", "7", "--max-dte", "45", "--min-credit", "0.0"],
        420,
    )]


def test_theta_harvester_forwards_dte_and_credit_params(monkeypatch):
    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "_theta_last_scan", 0.0)
    monkeypatch.setattr(server, "_theta_scan_lock", None)

    payload = {
        "universe": "fallback:ndx100",
        "params": {"min_dte": 14, "max_dte": 30, "min_credit": 1.5},
        "results": [],
    }
    calls: list[list[str]] = []

    async def fake_run_script(script: str, args: list[str], timeout: int | None = None):
        calls.append(args)
        return ScriptResult(ok=True, data=payload)

    monkeypatch.setattr(server, "run_script", fake_run_script)
    monkeypatch.setattr(server, "_read_cache", lambda _path: payload)

    client = TestClient(server.app)
    response = client.post("/theta-harvester/scan?preset=ndx100&min_dte=14&max_dte=30&min_credit=1.5")

    assert response.status_code == 200
    assert calls[0][-6:] == ["--min-dte", "14", "--max-dte", "30", "--min-credit", "1.5"]


def test_theta_harvester_cooldown_does_not_serve_stale_params(monkeypatch):
    # Within the cooldown window, a scan with DIFFERENT params must re-run rather
    # than return the cached snapshot from the previous parameters.
    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "_theta_last_scan", time.monotonic())  # inside cooldown
    monkeypatch.setattr(server, "_theta_scan_lock", None)

    cached_old = {
        "universe": "fallback:ndx100",
        "params": {"min_dte": 7, "max_dte": 45, "min_credit": 0.0},
        "results": [],
    }
    fresh = {
        "universe": "fallback:ndx100",
        "params": {"min_dte": 21, "max_dte": 60, "min_credit": 2.0},
        "results": [{"ticker": "MU"}],
    }
    ran = {"n": 0}

    async def fake_run_script(script: str, args: list[str], timeout: int | None = None):
        ran["n"] += 1
        return ScriptResult(ok=True, data=fresh)

    monkeypatch.setattr(server, "run_script", fake_run_script)
    # cooldown check reads cache (old params); post-scan read returns fresh
    monkeypatch.setattr(server, "_read_cache", lambda _path: cached_old if ran["n"] == 0 else fresh)

    client = TestClient(server.app)
    response = client.post("/theta-harvester/scan?preset=ndx100&min_dte=21&max_dte=60&min_credit=2.0")

    assert response.status_code == 200
    assert ran["n"] == 1  # re-scanned despite cooldown, because params differ
    assert response.json()["params"] == {"min_dte": 21, "max_dte": 60, "min_credit": 2.0}


def test_theta_harvester_rejects_invalid_ticker(monkeypatch):
    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)

    async def fake_run_script(*_args, **_kwargs):
        raise AssertionError("invalid ticker should fail before subprocess")

    monkeypatch.setattr(server, "run_script", fake_run_script)

    client = TestClient(server.app)
    response = client.post("/theta-harvester/scan?ticker=MU1")

    assert response.status_code == 400
    assert "ticker must be 1-6 letters" in response.text
