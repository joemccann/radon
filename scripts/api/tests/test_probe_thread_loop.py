"""REL-056 / R-142 (P0) — `_probe_authenticated` must not lose to thread affinity.

`_do_probe` is dispatched with a bare `asyncio.to_thread`. `ib.connect()`
reaches `ib_insync.util.getLoop()`, which raises `RuntimeError` in any
non-main thread with no loop installed, and the bare `except Exception`
reports that as `authenticated=False`.

`ib_pool._connect_in_thread` already knows this and installs a loop first.
`_do_probe` did not inherit it, so three load-bearing consumers mistake a
plumbing error for a 2FA wait: `restart_ib_gateway` records a SUCCESSFUL
restart as `awaiting_2fa` and keeps the 2FA push lock held (REL-017 then
refuses orders), `recover_stuck_pool` never calls `reconnect_roles`, and
`server.py`'s 3-strike self-heal ladder never counts a failure.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import ib_gateway


class _LoopSensitiveIB:
    """Stands in for ib_insync.IB: `connect` needs a loop in this thread,
    exactly like `ib_insync.util.getLoop()`."""

    instances: list["_LoopSensitiveIB"] = []

    def __init__(self):
        self.connected = False
        _LoopSensitiveIB.instances.append(self)

    def connect(self, *_args, **_kwargs):
        asyncio.get_event_loop()  # RuntimeError with no loop in this thread
        self.connected = True

    def managedAccounts(self):
        return ["DU1234567"] if self.connected else []

    def disconnect(self):
        self.connected = False


@pytest.fixture(autouse=True)
def _reset():
    _LoopSensitiveIB.instances.clear()
    yield
    _LoopSensitiveIB.instances.clear()


def _install(monkeypatch):
    import ib_insync

    monkeypatch.setattr(ib_insync, "IB", _LoopSensitiveIB)


@pytest.mark.asyncio
async def test_probe_installs_an_event_loop_in_the_worker_thread(monkeypatch):
    _install(monkeypatch)

    authenticated, accounts = await ib_gateway._probe_authenticated(timeout=1.0)

    assert authenticated is True, (
        "a live, authenticated gateway was reported as awaiting_2fa because "
        "the worker thread had no event loop"
    )
    assert accounts == ["DU1234567"]


@pytest.mark.asyncio
async def test_probe_still_disconnects_and_reports_a_real_auth_failure(monkeypatch):
    class _NoAccounts(_LoopSensitiveIB):
        def managedAccounts(self):
            return []

    import ib_insync

    monkeypatch.setattr(ib_insync, "IB", _NoAccounts)

    authenticated, accounts = await ib_gateway._probe_authenticated(timeout=1.0)

    assert authenticated is False
    assert accounts == []
    assert _LoopSensitiveIB.instances[-1].connected is False, "probe leaked a connection"


@pytest.mark.asyncio
async def test_probe_does_not_disturb_the_caller_loop(monkeypatch):
    """The loop it installs belongs to the worker thread, not this one."""
    _install(monkeypatch)
    before = asyncio.get_running_loop()

    await ib_gateway._probe_authenticated(timeout=1.0)

    assert asyncio.get_running_loop() is before
