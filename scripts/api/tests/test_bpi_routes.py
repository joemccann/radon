"""Route tests for POST /bpi/scan (BPI plan §5).

Runs bpi_scan.py behind a per-index 600s cooldown + a single shared lock
(every run writes the shared price_history_daily member rows). The
subprocess is always mocked here.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from scripts.api.subprocess import ScriptResult  # noqa: E402


@pytest.fixture(autouse=True)
def localhost_bypass(monkeypatch):
    from scripts.api import auth, server

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)


@pytest.fixture(autouse=True)
def reset_scan_state():
    from scripts.api import server

    server._bpi_last_scan.clear()
    server._bpi_scan_locks.clear()
    yield
    server._bpi_last_scan.clear()
    server._bpi_scan_locks.clear()


@pytest.fixture
def client():
    from scripts.api.server import app

    return TestClient(app)


def _envelope() -> dict:
    return {
        "generated_at": "2026-07-25T21:30:00Z",
        "indices": {
            "NDX": {
                "schema_version": 1,
                "index_symbol": "NDX",
                "bpi": 30.39,
                "missing": False,
            }
        },
    }


class _RunScriptRecorder:
    def __init__(self, result: ScriptResult, delay_s: float = 0.0):
        self.result = result
        self.delay_s = delay_s
        self.calls: list[tuple] = []

    async def __call__(self, name, args, timeout=None, **kwargs):
        self.calls.append((name, args, timeout))
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        return self.result


@pytest.fixture
def run_script_ok(monkeypatch):
    from scripts.api import server

    recorder = _RunScriptRecorder(ScriptResult(ok=True, data=_envelope()))
    monkeypatch.setattr(server, "run_script", recorder)
    return recorder


class TestPostScan:
    def test_happy_path_returns_fresh_envelope(self, client, run_script_ok):
        from scripts.api import server

        response = client.post("/bpi/scan?index=NDX")

        assert response.status_code == 200
        assert response.json() == _envelope()
        assert run_script_ok.calls == [
            ("bpi_scan.py", ["--index", "NDX"], server.BPI_SCAN_TIMEOUT_S)
        ]

    def test_default_index_is_all(self, client, run_script_ok):
        response = client.post("/bpi/scan")

        assert response.status_code == 200
        assert run_script_ok.calls[0][1] == ["--index", "all"]

    def test_second_post_within_cooldown_returns_cooldown(self, client, run_script_ok):
        assert client.post("/bpi/scan?index=NDX").status_code == 200

        second = client.post("/bpi/scan?index=NDX")

        assert second.status_code == 200
        body = second.json()
        assert body["status"] == "cooldown"
        assert 0 < body["retry_in"] <= 600
        assert len(run_script_ok.calls) == 1

    def test_cooldown_is_per_index(self, client, run_script_ok):
        client.post("/bpi/scan?index=NDX")
        response = client.post("/bpi/scan?index=SPX")

        assert response.status_code == 200
        assert len(run_script_ok.calls) == 2

    def test_script_failure_is_503_and_does_not_advance_cooldown(self, client, monkeypatch):
        from scripts.api import server

        recorder = _RunScriptRecorder(
            ScriptResult(ok=False, error="constituents unavailable", exit_code=1)
        )
        monkeypatch.setattr(server, "run_script", recorder)

        first = client.post("/bpi/scan?index=NDX")
        assert first.status_code == 503
        assert first.json()["detail"] == "constituents unavailable"

        second = client.post("/bpi/scan?index=NDX")
        assert second.status_code == 503
        assert len(recorder.calls) == 2  # failure never starts the cooldown

    @pytest.mark.parametrize("index", ["DAX", "ndx100", "NDX;RM"])
    def test_invalid_index_is_400(self, client, index):
        response = client.post(f"/bpi/scan?index={index}")
        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid index"

    def test_all_scans_serialize_on_one_lock(self, monkeypatch):
        """Every run writes shared price_history_daily member rows (NDX is
        a subset of SPX), so two BPI subprocesses must never overlap."""
        from scripts.api import server

        active = 0
        max_active = 0
        calls: list[tuple] = []

        async def _tracking_run_script(name, args, timeout=None, **kwargs):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.02)
            active -= 1
            calls.append((name, args))
            return ScriptResult(ok=True, data=_envelope())

        monkeypatch.setattr(server, "run_script", _tracking_run_script)

        async def _two_concurrent():
            return await asyncio.gather(
                server.bpi_scan("NDX"),
                server.bpi_scan("SPX"),
            )

        asyncio.run(_two_concurrent())

        assert len(calls) == 2
        assert max_active == 1
