"""Route tests for the RV-ratio surface (rv-ratio plan step 6).

GET serves the subprocess's disk snapshot (missing -> 200 missing:true,
never 4xx); POST runs rv_ratio_scan.py behind a per-symbol 600s cooldown
+ single-flight lock and returns the fresh payload synchronously. The
subprocess is always mocked here.
"""
from __future__ import annotations

import asyncio
import json
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

    server._rv_ratio_last_scan.clear()
    server._rv_ratio_scan_locks.clear()
    yield
    server._rv_ratio_last_scan.clear()
    server._rv_ratio_scan_locks.clear()


@pytest.fixture
def client():
    from scripts.api.server import app

    return TestClient(app)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    from scripts.api import server

    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    return tmp_path


def _payload(symbol: str = "SMH") -> dict:
    return {
        "schema_version": 1,
        "symbol": symbol,
        "benchmark": "SPY",
        "window_sessions": 252,
        "scan_time": "2026-07-17T22:31:04Z",
        "sources": {"asset": ["yahoo"], "benchmark": ["yahoo"]},
        "dates": ["2026-07-17"],
        "ratio": [1.1841],
        "asset_rv": [17.42],
        "benchmark_rv": [14.71],
        "stats": {
            "high": 1.62, "low": 0.94, "avg": 1.21, "last": 1.48, "stddev": 0.11,
            "last_percentile": 0.92, "band_upper": 1.32, "band_lower": 1.10,
            "divergence": "elevated",
        },
        "coverage": {
            "first_date": "2026-07-17", "last_date": "2026-07-17",
            "sessions": 1, "complete": False,
        },
        "units": "x SPY realized vol",
        "missing": False,
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

    recorder = _RunScriptRecorder(ScriptResult(ok=True, data=_payload()))
    monkeypatch.setattr(server, "run_script", recorder)
    return recorder


# ── GET /options/rv-ratio/{symbol} ───────────────────────────────────

class TestGet:
    def test_missing_snapshot_is_200_missing_true(self, client, data_dir):
        response = client.get("/options/rv-ratio/SMH")
        assert response.status_code == 200
        assert response.json() == {"symbol": "SMH", "missing": True}

    def test_serves_disk_snapshot(self, client, data_dir):
        snapshot_dir = data_dir / "rv_ratio"
        snapshot_dir.mkdir(parents=True)
        (snapshot_dir / "SMH.json").write_text(json.dumps(_payload()))

        response = client.get("/options/rv-ratio/SMH")

        assert response.status_code == 200
        assert response.json() == _payload()

    @pytest.mark.parametrize("symbol", ["smh", "SMH%24", "TOO-LONG-SYMBOL"])
    def test_invalid_symbol_is_400(self, client, data_dir, symbol):
        response = client.get(f"/options/rv-ratio/{symbol}")
        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid symbol"


# ── POST /options/rv-ratio/{symbol}/scan ─────────────────────────────

class TestPostScan:
    def test_happy_path_returns_fresh_payload(self, client, run_script_ok):
        response = client.post("/options/rv-ratio/SMH/scan")

        assert response.status_code == 200
        assert response.json() == _payload()
        assert run_script_ok.calls == [("rv_ratio_scan.py", ["SMH"], 240)]

    def test_second_post_within_cooldown_returns_cooldown(self, client, run_script_ok):
        first = client.post("/options/rv-ratio/SMH/scan")
        assert first.status_code == 200

        second = client.post("/options/rv-ratio/SMH/scan")

        assert second.status_code == 200
        body = second.json()
        assert body["status"] == "cooldown"
        assert 0 < body["retry_in"] <= 600
        assert len(run_script_ok.calls) == 1

    def test_cooldown_is_per_symbol(self, client, run_script_ok):
        client.post("/options/rv-ratio/SMH/scan")
        response = client.post("/options/rv-ratio/XLE/scan")

        assert response.status_code == 200
        assert response.json() == _payload()
        assert len(run_script_ok.calls) == 2

    def test_script_failure_is_503_and_does_not_advance_cooldown(self, client, monkeypatch):
        from scripts.api import server

        recorder = _RunScriptRecorder(
            ScriptResult(ok=False, error="benchmark SPY has no close history", exit_code=1)
        )
        monkeypatch.setattr(server, "run_script", recorder)

        first = client.post("/options/rv-ratio/SMH/scan")
        assert first.status_code == 503
        assert first.json()["detail"] == "benchmark SPY has no close history"

        second = client.post("/options/rv-ratio/SMH/scan")
        assert second.status_code == 503
        assert len(recorder.calls) == 2  # failure never starts the cooldown

    @pytest.mark.parametrize("symbol", ["smh", "SMH%24", "TOO-LONG-SYMBOL"])
    def test_invalid_symbol_is_400(self, client, symbol):
        response = client.post(f"/options/rv-ratio/{symbol}/scan")
        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid symbol"

    def test_single_flight_concurrent_posts_run_one_subprocess(self, monkeypatch):
        from scripts.api import server

        recorder = _RunScriptRecorder(ScriptResult(ok=True, data=_payload()), delay_s=0.02)
        monkeypatch.setattr(server, "run_script", recorder)

        async def _two_concurrent():
            return await asyncio.gather(
                server.rv_ratio_scan("SMH"),
                server.rv_ratio_scan("SMH"),
            )

        responses = asyncio.run(_two_concurrent())

        assert len(recorder.calls) == 1
        payloads = [r for r in responses if r.get("missing") is False]
        cooldowns = [r for r in responses if r.get("status") == "cooldown"]
        assert len(payloads) == 1
        assert len(cooldowns) == 1

    def test_concurrent_scans_for_different_symbols_never_overlap(self, monkeypatch):
        """Every scan subprocess also refreshes the shared SPY benchmark rows;
        a concurrent scan during a SPY lineage reset (DELETE + re-insert)
        would read an empty benchmark. Different symbols must serialize."""
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
            return ScriptResult(ok=True, data=_payload(args[0]))

        monkeypatch.setattr(server, "run_script", _tracking_run_script)

        async def _two_concurrent():
            return await asyncio.gather(
                server.rv_ratio_scan("SMH"),
                server.rv_ratio_scan("XLE"),
            )

        responses = asyncio.run(_two_concurrent())

        assert len(calls) == 2  # both scans still run…
        assert max_active == 1  # …but never concurrently (shared SPY writes)
        assert all(r.get("missing") is False for r in responses)

    def test_test_mode_never_spawns_subprocess(self, client, monkeypatch):
        from scripts.api import server

        monkeypatch.setattr(server, "test_mode", True)

        async def _boom(*args, **kwargs):
            raise AssertionError("run_script must not be called in TEST_MODE")

        monkeypatch.setattr(server, "run_script", _boom)

        response = client.post("/options/rv-ratio/SMH/scan")

        assert response.status_code == 200
        body = response.json()
        assert body["symbol"] == "SMH"
        assert body["missing"] is True
