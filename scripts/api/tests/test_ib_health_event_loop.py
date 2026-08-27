"""`/health` must not run the 2FA lease read on the FastAPI event loop.

`check_ib_gateway` is an `async def`. It called `restart_backoff_state()`
inline, which reaches `ib_2fa_lock.check_2fa_push_lock` ->
`_is_orphaned` -> ORPHAN_CONFIRM_PROBES blocking `socket.create_connection`
calls with `time.sleep` between them. On exactly the incident state — a lease
held past its grace with the Gateway port down — every `/health` poll stalled
the whole loop for ~1.85s, and `/health` is polled by ib_watchdog, by deploy's
`wait_for_gateway_ready` and by the UI.

These tests never open a socket, never reach a gateway, and never sleep on the
wall clock in the passing path: `restart_backoff_state` is replaced by a probe
that parks on a `threading.Event` until a concurrent asyncio task releases it.
A task that cannot run means the loop was blocked. T-201.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

import pytest


SCRIPTS_DIR = Path(__file__).resolve().parents[2]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from scripts.api import ib_gateway  # noqa: E402


# How long the parked probe waits for the loop to make progress before
# declaring it starved. Only ever reached when the loop IS blocked.
_STARVATION_TIMEOUT_SECS = 2.0
_HEARTBEAT_POLL_SECS = 0.005


class EventLoopBlocked(AssertionError):
    """Raised from the worker side when no concurrent task could run."""


def _parked_backoff_state(started: threading.Event, released: threading.Event):
    """Stand-in for the real ~1.85s blocking lease read."""

    def _probe():
        started.set()
        if not released.wait(timeout=_STARVATION_TIMEOUT_SECS):
            raise EventLoopBlocked(
                "check_ib_gateway ran restart_backoff_state on the event loop: "
                "a concurrent asyncio task made no progress for "
                f"{_STARVATION_TIMEOUT_SECS}s while the lease read was in flight"
            )
        return {"attempt_count": 0, "push_lock": None, "probed": True}

    return _probe


async def _drive_with_a_concurrent_task(started, released, **check_kwargs):
    """Run `check_ib_gateway` alongside a task that only needs the loop."""
    ticks = {"n": 0}

    async def heartbeat():
        # Bounded so a wedged run fails on the probe's timeout, not by hanging.
        for _ in range(int(_STARVATION_TIMEOUT_SECS / _HEARTBEAT_POLL_SECS) + 100):
            if started.is_set():
                ticks["n"] += 1
                released.set()
                return
            await asyncio.sleep(_HEARTBEAT_POLL_SECS)

    beat = asyncio.create_task(heartbeat())
    try:
        result = await ib_gateway.check_ib_gateway(**check_kwargs)
    finally:
        released.set()
        await beat
    return result, ticks


@pytest.fixture
def probe_pair():
    return threading.Event(), threading.Event()


def test_local_health_check_leaves_the_loop_free(monkeypatch, probe_pair):
    started, released = probe_pair

    async def fake_check_docker():
        return {"port_listening": True, "gateway_mode": "docker"}

    monkeypatch.setattr(ib_gateway, "is_cloud_mode", lambda: False)
    monkeypatch.setattr(ib_gateway, "is_docker_mode", lambda: True)
    monkeypatch.setattr(ib_gateway, "_check_docker", fake_check_docker)
    monkeypatch.setattr(
        ib_gateway, "restart_backoff_state", _parked_backoff_state(started, released)
    )

    result, ticks = asyncio.run(
        _drive_with_a_concurrent_task(started, released, pool_status=None)
    )

    assert ticks["n"] == 1, "the concurrent task never got the loop"
    assert result["restart_backoff"]["probed"] is True


def test_cloud_health_check_leaves_the_loop_free(monkeypatch, probe_pair):
    started, released = probe_pair

    async def fake_check_cloud():
        return {"port_listening": True, "gateway_mode": "cloud"}

    monkeypatch.setattr(ib_gateway, "is_cloud_mode", lambda: True)
    monkeypatch.setattr(ib_gateway, "_check_cloud", fake_check_cloud)
    monkeypatch.setattr(
        ib_gateway, "restart_backoff_state", _parked_backoff_state(started, released)
    )

    result, ticks = asyncio.run(
        _drive_with_a_concurrent_task(started, released, pool_status=None)
    )

    assert ticks["n"] == 1, "the concurrent task never got the loop"
    assert result["restart_backoff"]["probed"] is True
