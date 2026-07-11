"""IBPool lifecycle operations serialize and respect active role leases."""

from __future__ import annotations

import asyncio
import threading

import pytest

from scripts.api import ib_pool as pool_module


class _FakeIB:
    def __init__(self) -> None:
        self.connected = True

    def isConnected(self) -> bool:
        return self.connected

    def managedAccounts(self):
        return ["U1"] if self.connected else []


class _FakeClient:
    def __init__(self) -> None:
        self.ib = _FakeIB()
        self.disconnect_calls = 0

    def disconnect(self) -> None:
        self.disconnect_calls += 1
        self.ib.connected = False


@pytest.mark.asyncio
async def test_concurrent_connect_all_creates_one_client_per_role(monkeypatch):
    monkeypatch.setattr(pool_module, "POOL_ROLES", {"sync": 3})
    calls = 0

    def connect(*args, **kwargs):
        nonlocal calls
        calls += 1
        return _FakeClient()

    monkeypatch.setattr(pool_module, "_connect_in_thread", connect)
    pool = pool_module.IBPool()

    first, second = await asyncio.gather(pool.connect_all(), pool.connect_all())

    assert first == {"sync": True}
    assert second == {"sync": True}
    assert calls == 1


@pytest.mark.asyncio
async def test_disconnect_waits_for_active_role_lease(monkeypatch):
    monkeypatch.setattr(pool_module, "POOL_ROLES", {"sync": 3})
    pool = pool_module.IBPool()
    client = _FakeClient()
    pool._clients["sync"] = client
    pool._connected["sync"] = True
    release = asyncio.Event()
    acquired = asyncio.Event()

    async def borrower():
        async with pool.acquire("sync"):
            acquired.set()
            await release.wait()

    borrowed = asyncio.create_task(borrower())
    await acquired.wait()
    disconnecting = asyncio.create_task(pool.disconnect_all())
    await asyncio.sleep(0.05)

    assert client.disconnect_calls == 0
    release.set()
    await asyncio.gather(borrowed, disconnecting)
    assert client.disconnect_calls == 1


@pytest.mark.asyncio
async def test_disconnect_blocks_reconnect_of_an_already_drained_role(monkeypatch):
    monkeypatch.setattr(pool_module, "POOL_ROLES", {"sync": 3, "orders": 4})
    pool = pool_module.IBPool()
    sync_client = _FakeClient()
    orders_client = _FakeClient()
    pool._clients.update({"sync": sync_client, "orders": orders_client})
    pool._connected.update({"sync": True, "orders": True})

    release_orders = asyncio.Event()
    orders_acquired = asyncio.Event()

    async def hold_orders():
        async with pool.acquire("orders"):
            orders_acquired.set()
            await release_orders.wait()

    borrower = asyncio.create_task(hold_orders())
    await orders_acquired.wait()
    disconnecting = asyncio.create_task(pool.disconnect_all())
    while sync_client.disconnect_calls == 0:
        await asyncio.sleep(0)

    with pytest.raises(ConnectionError, match="lifecycle transition"):
        async with pool.acquire("sync"):
            pass

    release_orders.set()
    await asyncio.gather(borrower, disconnecting)
    assert pool.get("sync") is None


@pytest.mark.asyncio
async def test_cancelled_connect_disposes_late_client(monkeypatch):
    monkeypatch.setattr(pool_module, "POOL_ROLES", {"sync": 3})
    started = threading.Event()
    release = threading.Event()
    client = _FakeClient()

    def connect(*args, **kwargs):
        started.set()
        assert release.wait(timeout=2)
        return client

    monkeypatch.setattr(pool_module, "_connect_in_thread", connect)
    pool = pool_module.IBPool()
    connecting = asyncio.create_task(pool.connect_all())
    assert await asyncio.to_thread(started.wait, 2)

    connecting.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await connecting

    assert client.disconnect_calls == 1
    assert pool.get("sync") is None
