"""One option secdef read per symbol, shared by every expiry on the page."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from api.option_secdef import OptionSecdefCache, OptionSecdefError  # noqa: E402


VIX_SNAPSHOT = {
    "symbol": "VIX",
    "expirations": ["20261020", "20261117"],
    "by_expiry": {
        "20261020": {"exchange": "CBOE", "multiplier": "100", "strikes": [15.0, 20.0]},
        "20261117": {"exchange": "CBOE", "multiplier": "100", "strikes": [16.0, 21.0]},
    },
}


def test_waiters_share_one_load_and_a_later_read_hits_the_cache():
    calls = 0
    started = asyncio.Event()
    release = asyncio.Event()

    async def loader(symbol: str) -> dict:
        nonlocal calls
        calls += 1
        assert symbol == "VIX"
        started.set()
        await release.wait()
        return VIX_SNAPSHOT

    async def run():
        cache = OptionSecdefCache(ttl_s=60, clock=lambda: 0.0)
        first = asyncio.create_task(cache.get("vix", loader))
        second = asyncio.create_task(cache.get("VIX", loader))
        await started.wait()
        for _ in range(5):
            await asyncio.sleep(0)
        assert calls == 1
        release.set()
        assert await first == VIX_SNAPSHOT
        assert await second == VIX_SNAPSHOT
        assert await cache.get("VIX", loader) == VIX_SNAPSHOT
        assert calls == 1

    asyncio.run(run())


def test_a_failed_load_is_not_cached():
    calls = 0

    async def loader(symbol: str) -> dict:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OptionSecdefError("subprocess capacity exhausted")
        return {"symbol": symbol, "expirations": [], "by_expiry": {}}

    async def run():
        cache = OptionSecdefCache(ttl_s=60, clock=lambda: 0.0)

        async def once():
            return await cache.get("VIX", loader)

        failed = await asyncio.gather(once(), once(), return_exceptions=True)
        assert all(isinstance(item, OptionSecdefError) for item in failed)
        assert calls == 1
        loaded = await once()
        assert loaded["symbol"] == "VIX"
        assert calls == 2

    asyncio.run(run())


def test_snapshot_expires_with_the_ttl():
    calls = 0
    now = {"t": 0.0}

    async def loader(symbol: str) -> dict:
        nonlocal calls
        calls += 1
        return {"symbol": symbol, "expirations": [], "by_expiry": {}}

    async def run():
        cache = OptionSecdefCache(ttl_s=60, clock=lambda: now["t"])
        await cache.get("SPX", loader)
        now["t"] = 59.0
        await cache.get("SPX", loader)
        assert calls == 1
        now["t"] = 60.0
        await cache.get("SPX", loader)
        assert calls == 2

    asyncio.run(run())


def test_distinct_symbols_do_not_share_a_load():
    calls = []

    async def loader(symbol: str) -> dict:
        calls.append(symbol)
        return {"symbol": symbol, "expirations": [], "by_expiry": {}}

    async def run():
        cache = OptionSecdefCache(ttl_s=60, clock=lambda: 0.0)
        await cache.get("VIX", loader)
        await cache.get("SPX", loader)

    asyncio.run(run())
    assert calls == ["VIX", "SPX"]


@pytest.fixture(autouse=True)
def localhost_bypass(monkeypatch):
    from scripts.api import auth, server

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    server._option_secdef_cache.clear()
    yield
    server._option_secdef_cache.clear()


@pytest.fixture
def client():
    from scripts.api.server import app

    return TestClient(app)


def _fake_script_result(*, ok=True, data=None, error=None, exit_code=0):
    from api.subprocess import ScriptResult

    return ScriptResult(ok=ok, data=data, error=error, exit_code=exit_code)


def test_expirations_and_every_expiry_share_one_snapshot(client):
    calls = []

    async def stub(script, args, timeout=None):
        calls.append((script, list(args), timeout))
        return _fake_script_result(ok=True, data=VIX_SNAPSHOT)

    with patch("scripts.api.server._run_ib_script_with_recovery", side_effect=stub):
        expirations = client.get("/options/expirations?symbol=vix")
        october = client.get("/options/chain?symbol=VIX&expiry=2026-10-20")
        november = client.get("/options/chain?symbol=VIX&expiry=20261117")
        missing = client.get("/options/chain?symbol=VIX&expiry=19990101")

    assert expirations.status_code == 200
    assert expirations.json() == {"symbol": "VIX", "expirations": ["20261020", "20261117"]}
    assert october.status_code == 200
    assert october.json() == {
        "symbol": "VIX",
        "expiry": "2026-10-20",
        "exchange": "CBOE",
        "strikes": [15.0, 20.0],
        "multiplier": "100",
    }
    assert november.status_code == 200
    assert november.json()["strikes"] == [16.0, 21.0]
    assert missing.status_code == 502
    assert missing.json()["detail"] == "No chain found for expiry 19990101"
    assert calls == [("ib_option_chain.py", ["--symbol", "VIX", "--snapshot"], 45.0)]


def test_secdef_timeout_is_not_cached(client):
    calls = []

    async def stub(script, args, timeout=None):
        calls.append(timeout)
        return _fake_script_result(ok=False, error="Script timed out after 45.0s")

    with patch("scripts.api.server._run_ib_script_with_recovery", side_effect=stub):
        first = client.get("/options/expirations?symbol=IWM")
        second = client.get("/options/chain?symbol=IWM&expiry=20260717")

    assert first.status_code == 504
    assert first.json()["detail"] == "Script timed out after 45.0s"
    assert second.status_code == 504
    assert calls == [45.0, 45.0]
