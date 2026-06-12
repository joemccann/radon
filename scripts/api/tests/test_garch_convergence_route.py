"""Tests for the FastAPI /garch-convergence/scan endpoint.

Same mocking strategy as test_ticker_ratings_and_pi.py: patch
`scripts.api.server.run_script` so we exercise the route without
spinning up a real subprocess. The route writes the cache file from
the subprocess's perspective, so we also mock `_read_cache` to return
the payload the test wants the route to forward.
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


@pytest.fixture(autouse=True)
def localhost_bypass(monkeypatch):
    from scripts.api import server, auth
    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    yield


@pytest.fixture(autouse=True)
def reset_cooldown(monkeypatch):
    """Force each test past the scan cooldown so the subprocess path runs.

    The route gates on ``monotonic() - _garch_last_scan < GARCH_COOLDOWN_S``.
    Seeding ``_garch_last_scan = 0.0`` only clears the cooldown when
    ``monotonic()`` is already larger than the cooldown — true on a long-uptime
    dev box but NOT on a freshly-booted CI runner where ``monotonic()`` is a few
    seconds, which left the cooldown active and short-circuited the scan (the
    subprocess mock was never called, so ``run_mock.call_args`` was None). Seed
    far in the past so the cooldown is always elapsed regardless of uptime.
    """
    from scripts.api import server
    monkeypatch.setattr(server, "_garch_last_scan", -1e9)
    monkeypatch.setattr(server, "_garch_scan_lock", None)
    yield


@pytest.fixture
def client():
    from scripts.api.server import app
    return TestClient(app)


def _fake_script_result(ok=True, data=None, error=None, exit_code=0):
    from api.subprocess import ScriptResult
    return ScriptResult(ok=ok, data=data, error=error, exit_code=exit_code)


def test_garch_scan_returns_cache_payload_after_subprocess(client):
    """Happy path: subprocess succeeds, route re-reads data/garch_convergence.json
    via _read_cache, returns the parsed payload to the caller."""
    fake = _fake_script_result(ok=True, data={})

    async def _stub(*args, **kwargs):
        return fake

    cache_payload = {
        "scan_time": "2026-05-22T14:00:00",
        "tickers": {"NVDA": {"price": 800.0}},
        "pairs": [
            {
                "pair": ["NVDA", "AMD"],
                "leader": "NVDA",
                "lagger": "AMD",
                "divergence": 0.42,
                "lagger_hv_iv_gap": 5.2,
                "lagger_iv_rank": 35.0,
                "signal": "LAGGER_BID",
                "gates_passed": True,
                "failing_gates": [],
                "expected_iv": 32.1,
                "expected_move": 4.8,
            }
        ],
    }

    with (
        patch("scripts.api.server.run_script", side_effect=_stub) as run_mock,
        patch("scripts.api.server._read_cache", return_value=cache_payload),
    ):
        resp = client.post("/garch-convergence/scan?preset=semis")

    assert resp.status_code == 200
    body = resp.json()
    assert body["pairs"][0]["pair"] == ["NVDA", "AMD"]
    assert body["pairs"][0]["divergence"] == 0.42

    # Subprocess was invoked with the right args.
    args, kwargs = run_mock.call_args
    assert args[0] == "garch_convergence.py"
    assert "--preset" in args[1]
    assert "semis" in args[1]
    assert "--json" in args[1]
    assert "--no-open" in args[1]

    # service_health[garch-scan] is recorded by the subprocess itself now
    # (db/scan_mirror.py) — the route no longer mirrors the cache to Turso.


def test_garch_scan_uses_default_preset_when_omitted(client):
    fake = _fake_script_result(ok=True, data={})

    async def _stub(*args, **kwargs):
        return fake

    with (
        patch("scripts.api.server.run_script", side_effect=_stub) as run_mock,
        patch("scripts.api.server._read_cache", return_value={"pairs": []}),
    ):
        resp = client.post("/garch-convergence/scan")

    assert resp.status_code == 200
    args, _ = run_mock.call_args
    # Default preset = mega-tech.
    assert "mega-tech" in args[1]


def test_garch_scan_surfaces_subprocess_failure_as_502(client):
    fake = _fake_script_result(ok=False, error="UW rate-limited", exit_code=1)

    async def _stub(*args, **kwargs):
        return fake

    with (
        patch("scripts.api.server.run_script", side_effect=_stub),
        patch("scripts.api.server._read_cache", return_value=None),
    ):
        resp = client.post("/garch-convergence/scan?preset=energy")

    assert resp.status_code == 502
    body = resp.json()
    assert "UW rate-limited" in body["detail"]


def test_garch_scan_returns_empty_envelope_when_cache_missing(client):
    """If the subprocess succeeded but _read_cache returns None (file write
    race, disk full, etc.), the route should still return a valid envelope
    so the dashboard doesn't crash."""
    fake = _fake_script_result(ok=True, data={})

    async def _stub(*args, **kwargs):
        return fake

    with (
        patch("scripts.api.server.run_script", side_effect=_stub),
        patch("scripts.api.server._read_cache", return_value=None),
    ):
        resp = client.post("/garch-convergence/scan")

    assert resp.status_code == 200
    body = resp.json()
    assert body["pairs"] == []
    assert body["tickers"] == {}


def test_garch_scan_returns_cached_payload_within_cooldown(client, monkeypatch):
    """Cooldown gate: second call within 600s skips run_script and returns
    the cache directly."""
    import time
    from scripts.api import server

    # Force the cooldown to be active.
    monkeypatch.setattr(server, "_garch_last_scan", time.monotonic())

    cache_payload = {"scan_time": "2026-05-22T14:00:00", "tickers": {}, "pairs": []}

    with (
        patch("scripts.api.server.run_script") as run_mock,
        patch("scripts.api.server._read_cache", return_value=cache_payload),
    ):
        resp = client.post("/garch-convergence/scan")

    assert resp.status_code == 200
    # run_script should NOT have been invoked.
    run_mock.assert_not_called()
