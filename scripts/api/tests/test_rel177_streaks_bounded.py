"""REL-177 (R-488..R-493): the STREAKS route is bounded, honest about failure
and cache, and cannot fan out through the assistant."""
from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture(autouse=True)
def localhost_bypass(monkeypatch):
    from scripts.api import auth, server

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    yield


@pytest.fixture
def client():
    from scripts.api.server import app

    return TestClient(app)


def _closes(n: int) -> dict[str, float]:
    out: dict[str, float] = {}
    for i in range(n):
        day = 1 + i
        month = 7 + (day - 1) // 28
        out[f"2026-{month:02d}-{((day - 1) % 28) + 1:02d}"] = 100.0 + i
    return out


async def _no_ib(request, symbol):
    return {}


class TestAuthGate:
    def test_awaiting_2fa_skips_the_pool_entirely(self, client, monkeypatch):
        """R-488: while IB sat in awaiting_2fa every lookup burned ~35s of
        pool machinery before UW was even tried."""
        from api import ib_gateway
        from api.routes import streaks as streaks_mod

        monkeypatch.setattr(
            ib_gateway, "last_observed_auth_state", lambda: "awaiting_2fa",
            raising=False,
        )
        pool = MagicMock()
        pool.acquire.side_effect = AssertionError("pool must not be touched")
        started = time.monotonic()
        with patch.object(streaks_mod, "_read_cached_envelope", return_value=None), \
             patch.object(streaks_mod, "_fetch_uw_closes", return_value=_closes(30)), \
             patch.object(streaks_mod, "_write_cached_closes"):
            client.app.state.ib_pool = pool
            try:
                resp = client.get("/streaks/SPY")
            finally:
                client.app.state.ib_pool = None
        assert time.monotonic() - started < 5
        assert resp.json()["source"] == "uw"


class TestFailureIsNotMissing:
    def test_all_vendors_failing_carries_an_errors_list(self, client):
        """R-489: IB down + UW embargo + Yahoo 5xx was byte-identical to an
        unlisted symbol."""
        from api.routes import streaks as streaks_mod

        def _uw_fail(symbol):
            raise RuntimeError("UW 429 embargo")

        def _rh_fail(symbol):
            raise RuntimeError("rh unconfigured hard")

        def _yahoo_fail(symbol):
            raise RuntimeError("yahoo 502")

        with patch.object(streaks_mod, "_read_cached_envelope", return_value=None), \
             patch.object(streaks_mod, "_fetch_ib_closes", new=_no_ib), \
             patch.object(streaks_mod, "_fetch_uw_closes", _uw_fail), \
             patch.object(streaks_mod, "_fetch_rh_closes", _rh_fail), \
             patch.object(streaks_mod, "_fetch_yahoo_closes", _yahoo_fail), \
             patch.object(streaks_mod, "_write_cached_closes") as write_cache:
            resp = client.get("/streaks/XYZ")
        body = resp.json()
        assert body["missing"] is True
        errors = body.get("errors")
        assert errors, "vendor failures were served indistinguishable from an unlisted symbol"
        assert any("UW" in str(e) or "uw" in str(e) for e in errors)
        write_cache.assert_not_called()


class TestCacheHonesty:
    def _envelope(self, source: str, hours_old: float, n: int = 25):
        return {
            "data": _closes(n),
            "source": source,
            "fetched_at": (datetime.now() - timedelta(hours=hours_old)).isoformat(),
        }

    def test_a_cached_uw_series_serves_with_provenance(self, client):
        """R-490: cache hits were rebuilt as source='cache' with a fresh
        clock, discarding the stored source and fetched_at."""
        from api.routes import streaks as streaks_mod

        uw = MagicMock()
        with patch.object(
            streaks_mod, "_read_cached_envelope",
            return_value=self._envelope("uw", 1.0),
        ), patch.object(streaks_mod, "_fetch_uw_closes", uw):
            resp = client.get("/streaks/SPY")
        body = resp.json()
        assert body["source"] == "uw"
        assert body["cached"] is True
        assert body["fetched_at"]
        uw.assert_not_called()

    def test_a_yahoo_cached_series_retries_the_higher_rungs(self, client):
        """Rule 7: a Yahoo win must not stick for 24h once IB recovers."""
        from api.routes import streaks as streaks_mod

        async def _healthy_ib(request, symbol):
            return _closes(30)

        with patch.object(
            streaks_mod, "_read_cached_envelope",
            return_value=self._envelope("yahoo", 20.0),
        ), patch.object(streaks_mod, "_fetch_ib_closes", new=_healthy_ib), \
             patch.object(streaks_mod, "_write_cached_closes"):
            resp = client.get("/streaks/SPY")
        assert resp.json()["source"] == "ib"


class TestInflightCoalescing:
    def test_concurrent_same_ticker_requests_share_one_ladder_run(self):
        """R-488: no per-ticker in-flight guard existed on the FastAPI side."""
        from api.routes import streaks as streaks_mod

        calls = {"n": 0}

        async def _slow_ib(request, symbol):
            calls["n"] += 1
            await asyncio.sleep(0.2)
            return _closes(30)

        async def _drive():
            with patch.object(streaks_mod, "_read_cached_envelope", return_value=None), \
                 patch.object(streaks_mod, "_fetch_ib_closes", new=_slow_ib), \
                 patch.object(streaks_mod, "_write_cached_closes"):
                request = MagicMock()
                results = await asyncio.gather(
                    streaks_mod.daily_streaks("COAL", request),
                    streaks_mod.daily_streaks("COAL", request),
                )
            return results

        results = asyncio.run(_drive())
        assert calls["n"] == 1, f"two concurrent requests ran {calls['n']} ladders"
        assert all(r["source"] == "ib" for r in results)


class TestSpawnPin:
    def test_the_fastapi_pin_is_read_spawn(self):
        from scripts.api.assistant_catalog import CATALOG

        assert CATALOG[("GET", "/streaks/{ticker}")] == "read.spawn"

    def test_the_next_route_pin_matches(self):
        src = (SCRIPTS_DIR.parent / "web" / "app" / "api" / "streaks" / "route.ts").read_text()
        assert 'radonCapability = "read.spawn"' in src
