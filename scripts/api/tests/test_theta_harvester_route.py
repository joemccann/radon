from __future__ import annotations

import time

from fastapi.testclient import TestClient

from api import auth, server
from api.subprocess import ScriptResult


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
    assert calls == [("theta_harvester_scanner.py", ["--json", "MU"], 420)]


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
    assert calls == [("theta_harvester_scanner.py", ["--json", "--preset", "ndx100"], 420)]


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
