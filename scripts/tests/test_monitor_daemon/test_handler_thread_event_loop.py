#!/usr/bin/env python3
"""Regression: monitor-daemon handler THREADS must be able to connect to IB.

Incident (2026-08-10, flapping): every ~30s cycle, `radon-monitor` logged

    RuntimeError: There is no current event loop in thread
        'handler-position_reconcile_0'.
    clients.ib_client.IBConnectionError: Failed to connect to IB on
        127.0.0.1:4001 after 1 attempt(s): There is no current event loop
        in thread 'handler-position_reconcile_0'.

for `handler-position_reconcile_0`, `handler-fill_monitor_0` and
`handler-journal_sync_0` while the gateway itself was healthy
(`/health/lite` -> authenticated + port_listening). The connect never
reached the socket: it died acquiring the worker thread's asyncio event
loop, and the three `service_health` rows latched to `error` until the
incident watchdog raised a P2.

Topology this test deliberately encodes (a single-caller version of this
test is GREEN against the bug and is exactly how it shipped):

1. The connect runs from a NON-MAIN worker thread spawned the way
   ``MonitorDaemon._run_handler_bounded`` spawns it (ThreadPoolExecutor
   with ``thread_name_prefix=f"handler-{handler.name}"``, hence the
   ``handler-<name>_0`` names in the production log). In the MAIN thread
   ``asyncio.get_event_loop()`` still auto-creates a loop, so the bug is
   invisible there — see ``test_main_thread_connect_is_the_blind_spot``.
2. MORE THAN ONE handler thread, both sequentially through the real
   daemon cycle and concurrently, matching the real topology where
   position_reconcile / fill_monitor / journal_sync all connect.
3. REPEAT CYCLES: connect -> disconnect -> connect again on the next
   cycle, in a freshly spawned thread each time (the daemon builds a new
   executor per handler per cycle). A loop-lifecycle fix that works once
   and breaks on the second cycle must not pass.

Only the IB SOCKET is stubbed. The asyncio-loop acquisition is the real
``ib_insync`` code path, so the only failure mode this test can produce
is the production one.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from typing import Any, Callable, Dict, List

import pytest
from ib_insync import IB

from monitor_daemon.daemon import MonitorDaemon
from monitor_daemon.handlers.base import BaseHandler
from monitor_daemon.handlers import fill_monitor, journal_sync, position_reconcile


# The exact substring asyncio puts in the RuntimeError production raised.
LOOP_ERROR_SIGNATURE = "no current event loop in thread"


class SocketOnlyStubIB(IB):
    """The real ``ib_insync.IB`` with only the TCP handshake stubbed out.

    ``IB.connect`` -> ``IB._run`` -> ``ib_insync.util.run`` ->
    ``ib_insync.util.getLoop`` -> ``asyncio.get_event_loop_policy().get_event_loop()``
    is kept entirely real by overriding ONLY ``connectAsync``, the leaf that
    opens the socket. A successful connect is therefore always possible and
    the single thing that can still fail is the event-loop acquisition —
    exactly the production failure, with no live gateway required.
    """

    def __init__(self) -> None:
        super().__init__()
        self.stub_connect_count = 0

    async def connectAsync(self, *args: Any, **kwargs: Any) -> "SocketOnlyStubIB":
        await asyncio.sleep(0)
        self.stub_connect_count += 1
        return self

    def isConnected(self) -> bool:
        return self.stub_connect_count > 0

    def disconnect(self) -> None:
        self.stub_connect_count = 0


def _connect_like_fill_monitor() -> Any:
    """Mirror of scripts/monitor_daemon/handlers/fill_monitor.py:108-111."""
    from clients.ib_client import IBClient, DEFAULT_HOST

    client = IBClient()
    client.connect(
        host=DEFAULT_HOST,
        port=fill_monitor.DEFAULT_IB_PORT,
        client_id=fill_monitor.DEFAULT_CLIENT_ID,
    )
    return client


def _connect_like_journal_sync() -> Any:
    """Mirror of scripts/monitor_daemon/handlers/journal_sync.py:119-121."""
    from clients.ib_client import IBClient, DEFAULT_HOST

    client = IBClient()
    client.connect(
        host=DEFAULT_HOST,
        port=journal_sync.DEFAULT_IB_PORT,
        client_id=journal_sync.DEFAULT_CLIENT_ID,
    )
    return client


# The three handlers named in the incident, each paired with the production
# connect call it makes. position_reconcile's factory IS production code.
PRODUCTION_CONNECTS: Dict[str, Callable[[], Any]] = {
    "position_reconcile": position_reconcile._default_client_factory,
    "fill_monitor": _connect_like_fill_monitor,
    "journal_sync": _connect_like_journal_sync,
}


class IBConnectCycleHandler(BaseHandler):
    """Minimal handler whose cycle is exactly the incident's failing step.

    Connect, then disconnect — nothing downstream, so the only reason this
    can fail is the reason production failed.
    """

    interval_seconds = 0
    requires_market_hours = False
    service_name = None

    def __init__(self, name: str, connect: Callable[[], Any]) -> None:
        super().__init__()
        self.name = name
        self._connect = connect
        self.thread_names: List[str] = []
        self.loops_at_entry: List[Any] = []
        self.had_open_loop_at_entry: List[bool] = []

    def execute(self) -> Dict[str, Any]:
        self.thread_names.append(threading.current_thread().name)
        loop = _current_thread_loop()
        self.loops_at_entry.append(loop)
        self.had_open_loop_at_entry.append(loop is not None and not loop.is_closed())
        client = self._connect()
        client.disconnect()
        return {"connected": True}


class LoopLifecycleHandler(BaseHandler):
    """Handler that only counts its runs, for loop-lifecycle failure tests."""

    interval_seconds = 0
    requires_market_hours = False
    service_name = None

    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name
        self.run_count = 0

    def execute(self) -> Dict[str, Any]:
        self.run_count += 1
        return {"ran": True}


def _current_thread_loop() -> Any:
    """The loop bound to this thread right now, or None. Never creates one."""
    try:
        return asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        return None


def _build_handlers() -> List[IBConnectCycleHandler]:
    return [
        IBConnectCycleHandler(name, connect)
        for name, connect in PRODUCTION_CONNECTS.items()
    ]


def _loop_failures(results: List[Dict[str, Any]]) -> List[str]:
    """Cycle results whose error is the production event-loop RuntimeError."""
    return [
        str(result.get("error"))
        for result in results
        if LOOP_ERROR_SIGNATURE in str(result.get("error"))
    ]


@pytest.fixture(autouse=True)
def _stub_ib_socket(monkeypatch: pytest.MonkeyPatch):
    """Replace the IB socket, keep the real event-loop acquisition."""
    import clients.ib_client as ib_client_module

    monkeypatch.setattr(ib_client_module, "IB", SocketOnlyStubIB)


class TestHandlerThreadEventLoop:
    """The connect must survive the daemon's worker-thread topology."""

    def test_repeated_cycles_across_handler_threads_connect_to_ib(self):
        """Three handlers x two daemon cycles, each in a handler-<name>_0 thread.

        This is the production shape: ``run_once`` -> ``_run_handler_bounded``
        -> fresh ThreadPoolExecutor per handler per cycle.
        """
        daemon = MonitorDaemon(respect_market_hours=False)
        handlers = _build_handlers()
        for handler in handlers:
            daemon.register(handler)

        cycle_results: List[Dict[str, Any]] = []
        for _ in range(2):
            cycle_results.extend(daemon.run_once().values())

        assert len(cycle_results) == 6, "both cycles must have run all three handlers"

        # The threads must genuinely be the daemon's named workers, not main.
        spawned = [n for handler in handlers for n in handler.thread_names]
        assert spawned, "handlers never executed"
        assert all(name.startswith("handler-") for name in spawned), spawned
        assert "MainThread" not in spawned

        failures = _loop_failures(cycle_results)
        assert not failures, (
            "IB connect died acquiring the worker thread's asyncio event loop "
            "(gateway was never contacted). Production errors:\n  "
            + "\n  ".join(failures)
        )
        assert all(result["status"] == "ok" for result in cycle_results), cycle_results

    def test_concurrent_handler_threads_connect_to_ib(self):
        """position_reconcile / fill_monitor / journal_sync connecting at once.

        The daemon serializes handlers within one cycle, but a slow handler
        overruns into the next cycle's thread (REL-008 abandons timed-out
        threads), so concurrent connects against the shared client are real.
        """
        daemon = MonitorDaemon(respect_market_hours=False)
        handlers = _build_handlers()

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(handlers)) as driver:
            futures = [
                driver.submit(daemon._run_handler_bounded, handler)
                for handler in handlers
            ]
            results = [future.result(timeout=30) for future in futures]

        failures = _loop_failures(results)
        assert not failures, (
            "concurrent handler threads died acquiring their event loops:\n  "
            + "\n  ".join(failures)
        )
        assert all(result["status"] == "ok" for result in results), results

    def test_loop_creation_failure_degrades_only_that_handler(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """An unallocatable loop must degrade the handler, not kill the daemon.

        ``asyncio.new_event_loop`` opens file descriptors, so it fails under
        EMFILE. It runs INSIDE the worker thread, so anything it raises comes
        back out of ``future.result()`` — past the ``TimeoutError`` arm, out of
        ``run_once``, and ``run_loop`` catches only ``KeyboardInterrupt``. One
        handler must never take the whole cycle down (the REL-008 contract).
        """
        daemon = MonitorDaemon(respect_market_hours=False)
        handler = LoopLifecycleHandler("loop_create_failure")
        daemon.register(handler)

        def _out_of_file_descriptors() -> asyncio.AbstractEventLoop:
            raise OSError(24, "Too many open files")

        monkeypatch.setattr(asyncio, "new_event_loop", _out_of_file_descriptors)

        results = daemon.run_once()

        result = results[handler.name]
        assert result["status"] == "error", result
        assert "Too many open files" in str(result["error"])
        assert handler.run_count == 0, "handler cannot have run without a loop"

    def test_loop_close_failure_keeps_the_successful_result(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Teardown must never replace a result the handler already earned.

        ``loop.close()`` runs in the wrapper's ``finally``, so a raising close
        both crashes the cycle and discards a handler run whose ``last_run``
        and service_health row were already latched.
        """
        daemon = MonitorDaemon(respect_market_hours=False)
        handler = LoopLifecycleHandler("loop_close_failure")
        daemon.register(handler)

        new_event_loop = asyncio.new_event_loop
        created: List[asyncio.AbstractEventLoop] = []

        def _refuse_to_close() -> None:
            raise RuntimeError("close boom")

        def _loop_that_fails_to_close() -> asyncio.AbstractEventLoop:
            loop = new_event_loop()
            created.append(loop)
            loop.close = _refuse_to_close
            return loop

        monkeypatch.setattr(asyncio, "new_event_loop", _loop_that_fails_to_close)

        try:
            results = daemon.run_once()
        finally:
            for loop in created:
                type(loop).close(loop)

        result = results[handler.name]
        assert result["status"] == "ok", result
        assert handler.run_count == 1

    def test_the_daemon_owns_the_loop_not_ib_client(self):
        """The loop must already exist when the handler starts, before IBClient.

        Installing it inside ``IBClient.connect`` instead would keep every other
        test in this file green while leaving each non-connect ib_insync entry
        point loopless — qualify_contracts, place_order, disconnect. That is why
        the diagnosis put the fix at the daemon's thread-spawn chokepoint, and
        this is the only assertion that encodes it.
        """
        daemon = MonitorDaemon(respect_market_hours=False)
        handlers = _build_handlers()
        for handler in handlers:
            daemon.register(handler)

        daemon.run_once()

        observed = [seen for handler in handlers for seen in handler.had_open_loop_at_entry]
        assert observed, "handlers never executed"
        assert all(observed), (
            "a handler thread reached its body with no usable event loop — the "
            "install has moved downstream of the daemon chokepoint"
        )

    def test_every_handler_loop_is_closed_when_its_cycle_ends(self):
        """A loop per handler per cycle must not outlive the cycle.

        radon-monitor is long-lived and cycles every 30s across five IB
        handlers, so a dropped ``loop.close()`` leaks a kqueue plus a self-pipe
        socketpair per run — fd exhaustion within the hour, with every other
        test in this file still green.
        """
        daemon = MonitorDaemon(respect_market_hours=False)
        handlers = _build_handlers()
        for handler in handlers:
            daemon.register(handler)

        for _ in range(2):
            daemon.run_once()

        loops = [loop for handler in handlers for loop in handler.loops_at_entry]
        assert len(loops) == 6, "both cycles must have run all three handlers"
        assert all(loop is not None and loop.is_closed() for loop in loops), (
            "handler event loops outlived the cycle that created them"
        )

    def test_main_thread_connect_is_the_blind_spot(self):
        """Documents why a single-caller regression test shipped green.

        The identical production connect succeeds on the main thread because
        the default event-loop policy auto-creates a loop there. Any future
        test that exercises this path must stay off the main thread.
        """
        previous = None
        try:
            previous = asyncio.get_event_loop_policy().get_event_loop()
        except RuntimeError:
            previous = None
        asyncio.set_event_loop(asyncio.new_event_loop())
        try:
            client = PRODUCTION_CONNECTS["position_reconcile"]()
            client.disconnect()
        finally:
            asyncio.get_event_loop().close()
            # Never hand the main thread an explicit None: that sets the policy's
            # _set_called flag, after which get_event_loop() raises for the rest
            # of the session instead of auto-creating — session-wide pollution
            # for any later test that relies on implicit loop creation.
            asyncio.set_event_loop(previous if previous is not None else asyncio.new_event_loop())
