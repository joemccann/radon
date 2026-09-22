from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from api import auth, server
from api.subprocess import ScriptResult


@pytest.fixture(autouse=True)
def force_live_mode(monkeypatch):
    monkeypatch.setattr(server, "test_mode", False)
    yield


def test_vol_skew_mr_cooldown_is_hourly():
    assert server.VOL_SKEW_MR_COOLDOWN_S == 3600


def test_vol_skew_mr_preset_scan_second_post_inside_hourly_window_returns_cache(monkeypatch):
    clock = {"now": 10_000.0}

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(server, "_vol_skew_mr_last_scan", -1e9)
    monkeypatch.setattr(server, "_vol_skew_mr_scan_lock", None)

    payload = {
        "scan_time": "2026-09-16T16:00:00Z",
        "source": "Unusual Whales + Radon vol/skew feeds",
        "universe": "fallback:ndx100",
        "requested_tickers": ["AAPL", "MSFT"],
        "tickers_scanned": 2,
        "candidates_found": 0,
        "actionable_count": 0,
        "results": [],
    }
    calls: list[tuple[str, list[str], int | None]] = []

    async def fake_run_script(script: str, args: list[str], timeout: int | None = None):
        calls.append((script, args, timeout))
        return ScriptResult(ok=True, data=payload)

    monkeypatch.setattr(server, "run_script", fake_run_script)
    monkeypatch.setattr(server, "_read_cache", lambda _path: payload)

    client = TestClient(server.app)
    first = client.post("/vol-skew-mr/scan?preset=ndx100")
    clock["now"] = 10_000.0 + 3599.0
    second = client.post("/vol-skew-mr/scan?preset=ndx100")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == payload
    assert len(calls) == 1


def test_vol_skew_mr_ticker_scan_bypasses_preset_cooldown(monkeypatch):
    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "_vol_skew_mr_last_scan", time.monotonic())
    monkeypatch.setattr(server, "_vol_skew_mr_scan_lock", None)

    payload = {
        "scan_time": "2026-09-16T16:00:00Z",
        "source": "Unusual Whales + Radon vol/skew feeds",
        "universe": "explicit",
        "requested_tickers": ["AAPL", "MSFT"],
        "tickers_scanned": 2,
        "candidates_found": 2,
        "actionable_count": 1,
        "results": [],
    }
    calls: list[tuple[str, list[str], int | None]] = []

    async def fake_run_script(script: str, args: list[str], timeout: int | None = None):
        calls.append((script, args, timeout))
        return ScriptResult(ok=True, data=payload)

    monkeypatch.setattr(server, "run_script", fake_run_script)
    monkeypatch.setattr(server, "_read_cache", lambda _path: payload)

    client = TestClient(server.app)
    response = client.post("/vol-skew-mr/scan?tickers=aapl,msft")

    assert response.status_code == 200
    assert response.json()["requested_tickers"] == ["AAPL", "MSFT"]
    assert calls == [("vol_skew_mr_scanner.py", ["--json", "--workers", "24", "AAPL", "MSFT"], 480)]


def test_vol_skew_mr_rejects_invalid_ticker(monkeypatch):
    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)

    async def fake_run_script(*_args, **_kwargs):
        raise AssertionError("invalid ticker should fail before subprocess")

    monkeypatch.setattr(server, "run_script", fake_run_script)

    client = TestClient(server.app)
    response = client.post("/vol-skew-mr/scan?tickers=MU1")

    assert response.status_code == 400
    assert "comma-separated" in response.text
