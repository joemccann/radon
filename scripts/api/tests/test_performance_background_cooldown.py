"""Page-driven performance rebuilds must not SendRequest.

Incident 2026-08-17: POST /performance/background had no floor, so every
stale page load fired a Flex fetch. IBKR answered with 1025 and took out
cash-flow-sync on the same token.

P2: both POST /performance and POST /performance/background are 404.
Timer + `perf_twr_builder.py --from-file` own rebuilds. A 404 is stronger
than cooldown/lockout — the page cannot earn another 1025.

No Flex, no subprocess: the builder is stubbed on every path.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse

API_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = API_DIR.parent.parent
for p in (str(REPO_ROOT), str(API_DIR.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

import scripts.api.server as server  # noqa: E402


@pytest.fixture(autouse=True)
def reset_build_state(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_running_build", None, raising=False)
    monkeypatch.setattr(
        server, "PERFORMANCE_REBUILD_SIDECAR", tmp_path / "cooldown.json"
    )
    yield


def _status_and_body(result):
    if isinstance(result, JSONResponse):
        import json as _json

        return result.status_code, _json.loads(bytes(result.body))
    if isinstance(result, dict):
        return 200, result
    return result.status_code, result


@pytest.fixture
def never_actually_builds(monkeypatch):
    calls = {"n": 0}

    async def _fake_rebuild():
        calls["n"] += 1
        return {"status": "ok"}

    monkeypatch.setattr(server, "_do_performance_rebuild", _fake_rebuild)
    return calls


class TestBackgroundRebuildIsGone:
    @pytest.mark.asyncio
    async def test_first_call_is_404(self, never_actually_builds):
        status, body = _status_and_body(await server.performance_background())
        assert status == 404
        assert never_actually_builds["n"] == 0
        assert "file" in str(body).lower() or body.get("status") == "file_ingest_only"

    @pytest.mark.asyncio
    async def test_a_second_call_does_not_reach_flex(self, never_actually_builds):
        await server.performance_background()
        status, _body = _status_and_body(await server.performance_background())
        assert status == 404
        assert never_actually_builds["n"] == 0

    @pytest.mark.asyncio
    async def test_a_1025_lockout_is_irrelevant_because_the_route_is_gone(
        self, never_actually_builds, monkeypatch
    ):
        monkeypatch.setattr(
            "utils.flex_embargo.active_until", lambda **k: "2026-08-30T12:00:00Z"
        )
        status, _body = _status_and_body(await server.performance_background())
        assert status == 404
        assert never_actually_builds["n"] == 0

    @pytest.mark.asyncio
    async def test_cooldown_constant_is_still_a_floor_if_the_route_reopens(self):
        assert server.PERFORMANCE_BACKGROUND_COOLDOWN_S >= 15 * 60

    @pytest.mark.asyncio
    async def test_an_inflight_build_is_not_joined(self, never_actually_builds, monkeypatch):
        import asyncio

        started = asyncio.Event()
        release = asyncio.Event()

        async def _slow_rebuild():
            started.set()
            await release.wait()
            return {"status": "ok"}

        monkeypatch.setattr(server, "_do_performance_rebuild", _slow_rebuild)
        status, _body = _status_and_body(await server.performance_background())
        assert status == 404
        assert never_actually_builds["n"] == 0
        assert not started.is_set()
        release.set()


class TestSynchronousRebuildIsGoneToo:
    @pytest.mark.asyncio
    async def test_a_lockout_never_reaches_flex(self, never_actually_builds, monkeypatch):
        monkeypatch.setattr(
            "utils.flex_embargo.active_until", lambda **k: "2026-08-30T12:00:00Z"
        )
        with pytest.raises(HTTPException) as exc:
            await server.performance_sync()
        assert exc.value.status_code == 404
        assert never_actually_builds["n"] == 0

    @pytest.mark.asyncio
    async def test_the_synchronous_path_is_404_too(self, never_actually_builds):
        with pytest.raises(HTTPException) as exc:
            await server.performance_sync()
        assert exc.value.status_code == 404
        assert never_actually_builds["n"] == 0

    @pytest.mark.asyncio
    async def test_a_process_restart_cannot_reopen_the_fetch(self, never_actually_builds):
        status, _body = _status_and_body(await server.performance_background())
        assert status == 404
        server._running_build = None
        status, _body = _status_and_body(await server.performance_background())
        assert status == 404
        assert never_actually_builds["n"] == 0
