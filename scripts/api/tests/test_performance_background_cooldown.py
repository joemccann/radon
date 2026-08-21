"""`POST /performance/background` must not be able to hammer IBKR Flex.

Incident 2026-08-17. The endpoint guarded only against a *concurrent* build
(`already_running`). It had no cooldown, so every page load that found a stale
snapshot fired a fresh rebuild, and every rebuild attempts a Flex fetch. The
snapshot table shows the result: a run roughly every 15 minutes all day.

IBKR answered that with escalating errors, ending at code 1025 — "Too many
failed attempts. Please review your configuration" — which is a lockout earned
by repeated failures, not a per-second rate limit. That lockout took out
`/performance` AND `cash-flow-sync`, both of which share the one Flex token.

Since `radon-perf-twr.timer` now owns the schedule (Tue..Sat 07:30 ET, the
morning after Flex finalizes a session), an on-demand rebuild is a fallback,
not the primary path. It needs a floor.

No Flex, no subprocess: the builder is stubbed on every path.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = API_DIR.parent.parent
for p in (str(REPO_ROOT), str(API_DIR.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

import scripts.api.server as server  # noqa: E402


@pytest.fixture(autouse=True)
def reset_build_state(monkeypatch):
    """Every test starts with no in-flight build and no cooldown history."""
    monkeypatch.setattr(server, "_running_build", None, raising=False)
    monkeypatch.setattr(server, "_last_background_build_at", None, raising=False)
    yield


@pytest.fixture
def never_actually_builds(monkeypatch):
    """Count rebuild invocations without running the builder."""
    calls = {"n": 0}

    async def _fake_rebuild():
        calls["n"] += 1
        return {"status": "ok"}

    monkeypatch.setattr(server, "_do_performance_rebuild", _fake_rebuild)
    return calls


class TestBackgroundRebuildCooldown:
    @pytest.mark.asyncio
    async def test_first_call_is_accepted(self, never_actually_builds):
        result = await server.performance_background()
        assert result["status"] == "accepted"

    @pytest.mark.asyncio
    async def test_a_second_call_inside_the_window_is_refused(
        self, never_actually_builds, monkeypatch
    ):
        """The whole incident in one assertion: two page loads a minute apart
        must not become two Flex fetches."""
        first = await server.performance_background()
        assert first["status"] == "accepted"

        # Let the first build finish so `already_running` cannot be what
        # refuses the second call -- the cooldown must stand on its own.
        if server._running_build is not None:
            await server._running_build

        second = await server.performance_background()

        assert second["status"] == "cooldown"
        assert never_actually_builds["n"] == 1, "second call must not reach Flex"

    @pytest.mark.asyncio
    async def test_the_window_expires(self, never_actually_builds, monkeypatch):
        await server.performance_background()
        if server._running_build is not None:
            await server._running_build

        # Rewind the clock past the cooldown.
        monkeypatch.setattr(
            server,
            "_last_background_build_at",
            server._last_background_build_at - server.PERFORMANCE_BACKGROUND_COOLDOWN_S - 1,
        )
        again = await server.performance_background()
        assert again["status"] == "accepted"

        # performance_background only SCHEDULES the rebuild; let it run before
        # counting invocations.
        if server._running_build is not None:
            await server._running_build
        assert never_actually_builds["n"] == 2

    @pytest.mark.asyncio
    async def test_a_1025_lockout_refuses_the_flex_fetch(
        self, never_actually_builds, monkeypatch
    ):
        """Same token as cash-flow-sync. A page-driven rebuild during 1025
        is what earned the lockout on 2026-08-17 and keeps it alive."""
        monkeypatch.setattr("utils.flex_embargo.is_blocked", lambda **k: True)
        result = await server.performance_background()
        assert result["status"] == "lockout"
        assert never_actually_builds["n"] == 0

    @pytest.mark.asyncio
    async def test_cooldown_is_at_least_fifteen_minutes(self):
        """Shorter than the observed ~15-minute SWR cadence would not have
        prevented the lockout."""
        assert server.PERFORMANCE_BACKGROUND_COOLDOWN_S >= 15 * 60

    @pytest.mark.asyncio
    async def test_concurrent_guard_still_reports_already_running(
        self, never_actually_builds, monkeypatch
    ):
        """The pre-existing in-flight guard must survive the new one."""
        import asyncio

        started = asyncio.Event()
        release = asyncio.Event()

        async def _slow_rebuild():
            started.set()
            await release.wait()
            return {"status": "ok"}

        monkeypatch.setattr(server, "_do_performance_rebuild", _slow_rebuild)

        first = await server.performance_background()
        assert first["status"] == "accepted"
        await started.wait()

        second = await server.performance_background()
        assert second["status"] == "already_running"

        release.set()
        await server._running_build
