"""REL-057 / R-143 (P0) — a wedged IB call must not starve the whole app.

`_bounded_pool_call` shields and reaps a timed-out worker, and `pool.retire`
fires a SECOND uncancellable `asyncio.to_thread(client.disconnect)`. Both ran
on the DEFAULT executor (`max_workers = min(32, cpu_count + 4)`, single
digits on a 2-vCPU box), which also serves Clerk JWKS verification, Turso
writes, the IB port probes and order cancel/place. After N wedged data-role
calls, every `await asyncio.to_thread(...)` in FastAPI queued forever, cancel
and replace included. No counter, no ceiling, no alarm; recovery needed a
`radon-api` restart.

Fixed by giving IB thread work its own bounded executor with a wedged-worker
ceiling that fails the acquire loudly, so the blast radius is IB data calls
and the default executor stays free for everything else.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import ib_executor
from api.routes import historical


@pytest.fixture(autouse=True)
def _fresh_executor():
    ib_executor.reset_for_test(max_workers=3)
    yield
    ib_executor.reset_for_test()


class _FakePool:
    def __init__(self):
        self.retired = []

    async def retire(self, role, client):
        self.retired.append((role, client))
        return True


def _wedge(release: threading.Event):
    def _blocked():
        release.wait(30)
        return "late"
    return _blocked


@pytest.mark.asyncio
async def test_wedged_ib_calls_do_not_consume_the_default_executor():
    release = threading.Event()
    pool = _FakePool()
    try:
        for _ in range(3):
            with pytest.raises(HTTPException):
                await historical._bounded_pool_call(
                    pool, "data", object(), _wedge(release), timeout=0.05
                )

        # The default executor must still be completely free: this is the
        # Clerk-verification / Turso-write path the finding names.
        got = await asyncio.wait_for(asyncio.to_thread(lambda: "clerk-ok"), timeout=2)
        assert got == "clerk-ok"
    finally:
        release.set()


@pytest.mark.asyncio
async def test_quarantined_workers_are_counted_and_capped():
    release = threading.Event()
    pool = _FakePool()
    try:
        for _ in range(2):
            with pytest.raises(HTTPException) as first:
                await historical._bounded_pool_call(
                    pool, "data", object(), _wedge(release), timeout=0.05
                )
            assert first.value.status_code == 504

        assert ib_executor.wedged_workers() == 2

        # Ceiling reached: the next acquire fails loudly and IMMEDIATELY,
        # instead of queueing behind the wedged workers forever.
        with pytest.raises(HTTPException) as saturated:
            await asyncio.wait_for(
                historical._bounded_pool_call(
                    pool, "data", object(), lambda: "never runs", timeout=5
                ),
                timeout=2,
            )
        assert saturated.value.status_code == 503
        assert "wedged" in str(saturated.value.detail).lower()
    finally:
        release.set()


@pytest.mark.asyncio
async def test_a_worker_that_finally_returns_frees_its_slot():
    release = threading.Event()
    pool = _FakePool()
    with pytest.raises(HTTPException):
        await historical._bounded_pool_call(
            pool, "data", object(), _wedge(release), timeout=0.05
        )
    assert ib_executor.wedged_workers() == 1

    release.set()
    for _ in range(100):
        if ib_executor.wedged_workers() == 0:
            break
        await asyncio.sleep(0.02)
    assert ib_executor.wedged_workers() == 0

    assert await historical._bounded_pool_call(
        pool, "data", object(), lambda: "ok", timeout=2
    ) == "ok"


@pytest.mark.asyncio
async def test_healthy_calls_still_run_on_the_ib_executor():
    pool = _FakePool()
    name = await historical._bounded_pool_call(
        pool, "data", object(), lambda: threading.current_thread().name, timeout=5
    )
    assert name.startswith(ib_executor.THREAD_NAME_PREFIX), name
    assert pool.retired == []
