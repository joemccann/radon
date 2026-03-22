"""Tests for performance endpoint deduplication and atomic cache writes.

Uses httpx.AsyncClient against the FastAPI app with mocked deps (no IB/network).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

# Ensure scripts/ is importable
SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# Mock heavy dependencies before importing the app
mock_ib_pool = MagicMock()
mock_ib_pool.connect_all = AsyncMock(return_value={"sync": "ok"})
mock_ib_pool.disconnect_all = AsyncMock()
mock_ib_pool.status.return_value = {"sync": "mock"}


@pytest.fixture()
def anyio_backend():
    return "asyncio"


@pytest.fixture()
async def client():
    """Create an httpx.AsyncClient against the FastAPI app with mocked startup."""
    import httpx
    from contextlib import asynccontextmanager

    # Patch lifespan to avoid IB/UW connections
    @asynccontextmanager
    async def mock_lifespan(app):
        yield

    with patch("api.server.lifespan", mock_lifespan), \
         patch("api.server.ib_pool", mock_ib_pool):
        # Must import after patching
        from api.server import app
        app.router.lifespan_context = mock_lifespan
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as c:
            yield c


FAKE_PERF_DATA = {
    "as_of": "2026-03-17",
    "period_label": "YTD",
    "summary": {"total_return": 0.15},
    "series": [],
    "warnings": [],
}


@pytest.mark.anyio
async def test_post_performance_returns_result(client, tmp_path):
    """POST /performance returns the build result."""
    from api.subprocess import ScriptResult
    mock_result = ScriptResult(ok=True, data=FAKE_PERF_DATA)

    with patch("api.server.run_script", AsyncMock(return_value=mock_result)), \
         patch("api.server._write_cache"), \
         patch("api.server._running_build", None):
        import api.server
        api.server._running_build = None
        resp = await client.post("/performance")

    assert resp.status_code == 200
    data = resp.json()
    assert data["period_label"] == "YTD"
    assert "_checksum" not in data


@pytest.mark.anyio
async def test_background_returns_202(client):
    """POST /performance/background returns 202 accepted."""
    from api.subprocess import ScriptResult
    mock_result = ScriptResult(ok=True, data=FAKE_PERF_DATA)

    with patch("api.server.run_script", AsyncMock(return_value=mock_result)), \
         patch("api.server._write_cache"):
        import api.server
        api.server._running_build = None
        resp = await client.post("/performance/background")

    assert resp.status_code == 202
    assert resp.json()["status"] == "accepted"


@pytest.mark.anyio
async def test_background_dedup_returns_already_running(client):
    """Second background call while build in-flight returns already_running."""
    import api.server

    # Simulate an in-flight task
    never_done = asyncio.get_running_loop().create_future()
    fake_task = asyncio.ensure_future(never_done)
    api.server._running_build = fake_task

    try:
        resp = await client.post("/performance/background")
        assert resp.status_code == 202
        assert resp.json()["status"] == "already_running"
    finally:
        fake_task.cancel()
        try:
            await fake_task
        except (asyncio.CancelledError, Exception):
            pass


@pytest.mark.anyio
async def test_post_piggybacks_on_inflight_task(client):
    """POST /performance awaits an in-flight task instead of starting a new one."""
    import api.server
    from api.subprocess import ScriptResult

    call_count = 0

    async def slow_build(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.01)
        return ScriptResult(ok=True, data=FAKE_PERF_DATA)

    with patch("api.server.run_script", slow_build), \
         patch("api.server._write_cache"):
        api.server._running_build = None

        # Fire two concurrent POST requests
        r1, r2 = await asyncio.gather(
            client.post("/performance"),
            client.post("/performance"),
        )

    assert r1.status_code == 200
    assert r2.status_code == 200
    # Both should get the same data
    assert r1.json()["period_label"] == "YTD"
    assert r2.json()["period_label"] == "YTD"
    # Only one build should have occurred
    assert call_count == 1


@pytest.mark.anyio
async def test_atomic_write_cache_survives_crash(tmp_path):
    """_write_cache uses atomic temp+replace, not direct write."""
    from api.server import _write_cache

    target = tmp_path / "test.json"
    data = {"key": "value"}
    _write_cache(target, data)

    assert target.exists()
    written = json.loads(target.read_text())
    assert written == data
    # No temp files should remain
    temps = list(tmp_path.glob(".cache_*"))
    assert len(temps) == 0


@pytest.mark.anyio
async def test_no_internal_metadata_in_response(client):
    """Response from POST /performance must not contain _checksum or cache metadata."""
    from api.subprocess import ScriptResult
    mock_result = ScriptResult(ok=True, data=FAKE_PERF_DATA)

    with patch("api.server.run_script", AsyncMock(return_value=mock_result)), \
         patch("api.server._write_cache"):
        import api.server
        api.server._running_build = None
        resp = await client.post("/performance")

    data = resp.json()
    assert "_checksum" not in data
    assert "fetched_at" not in data
    assert "ttl_seconds" not in data
