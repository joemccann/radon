"""Pool IB calls run on the loop their client connected on.

ib_insync binds the client's socket to the event loop current in the
connecting thread. A data call from an ``ib_executor`` worker (radon-ib_N)
found no loop at all, so every ``/historical/bars`` 500'd with
"There is no current event loop in thread 'radon-ib_0'", and a fresh loop
times out because nothing reads the socket. Verified on the live Gateway
2026-11-19: the connect loop answers, a fresh loop times out.
"""
from __future__ import annotations

import asyncio
import threading

from scripts.api import ib_pool as pool_module
from scripts.api.routes import historical
from scripts.clients.ib_client import IBClient


def _in_thread(fn):
    box = {}

    def run():
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - surfaced to the test
            box["error"] = exc

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(5)
    if "error" in box:
        raise box["error"]
    return box["value"]


def test_ib_client_remembers_the_loop_it_connected_on(monkeypatch):
    monkeypatch.setattr(IBClient, "_reset_session_state", lambda self: None)
    client = IBClient()
    monkeypatch.setattr(client.ib, "connect", lambda *a, **k: None)

    def connect():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        client.connect(host="h", port=1, client_id=97)
        return loop

    assert client.connect_loop is None
    connected_on = _in_thread(connect)
    assert client.connect_loop is connected_on


def test_pool_connect_installs_a_fresh_loop_even_on_a_reused_thread(monkeypatch):
    pool_client = pool_module.IBClient
    monkeypatch.setattr(pool_client, "connect", lambda self, **k: self._remember_connect_loop())

    def connect_on_thread_with_existing_loop():
        stale = asyncio.new_event_loop()
        asyncio.set_event_loop(stale)
        client = pool_module._connect_in_thread("h", 1, 5)
        return stale, client.connect_loop

    stale, bound = _in_thread(connect_on_thread_with_existing_loop)
    assert bound is not None
    assert bound is not stale


def test_bounded_pool_call_runs_the_operation_on_the_client_connect_loop():
    connect_loop = asyncio.new_event_loop()

    class Client:
        pass

    client = Client()
    client.connect_loop = connect_loop

    class Pool:
        async def retire(self, role, retiring):
            raise AssertionError("must not time out")

    def current_loop():
        return threading.current_thread().name, asyncio.get_event_loop()

    async def scenario():
        return await historical._bounded_pool_call(Pool(), "data", client, current_loop, timeout=5)

    thread_name, loop = asyncio.run(scenario())
    assert thread_name.startswith("radon-ib")
    assert loop is connect_loop
    connect_loop.close()
