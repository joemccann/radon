"""R-060: pool retire must actually dispose the quarantined client.

`_bounded_pool_call` bounds an IB call at 15s, but the retired client's only
`disconnect` used to sit BEHIND `await asyncio.shield(task)` — behind the very
call that hung. The zombie kept holding the role's fixed client_id (data=5),
the next `acquire("data")` reconnected with the SAME id, IB rejected it as a
duplicate, and every data-role consumer 503'd until the wedged call returned.

These tests inject that exact fault with a fake gateway that enforces IB's
one-session-per-client_id rule. No real IB connection anywhere.
"""

from __future__ import annotations

import asyncio
import threading

import pytest
from fastapi import HTTPException

from scripts.api import ib_pool as pool_module
from api.routes.historical import _bounded_pool_call


class FakeGateway:
    """Tracks live client_ids and enforces IB's duplicate-id rejection."""

    def __init__(self) -> None:
        self.in_use: set[int] = set()

    def connect(self, client_id: int) -> "FakeClient":
        if client_id in self.in_use:
            raise ConnectionError(f"client id {client_id} already in use")
        self.in_use.add(client_id)
        return FakeClient(self, client_id)


class FakeClient:
    def __init__(self, gateway: FakeGateway, client_id: int) -> None:
        self._gateway = gateway
        self._client_id = client_id
        self.disconnect_calls = 0
        self.ib = _FakeIB(self)

    def disconnect(self) -> None:
        self.disconnect_calls += 1
        self._gateway.in_use.discard(self._client_id)


class _FakeIB:
    def __init__(self, client: FakeClient) -> None:
        self._client = client

    def isConnected(self) -> bool:
        return self._client._client_id in self._client._gateway.in_use

    def managedAccounts(self):
        return ["U1"] if self.isConnected() else []


async def _wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = asyncio.get_event_loop().time() + timeout
    while not predicate():
        if asyncio.get_event_loop().time() >= deadline:
            return False
        await asyncio.sleep(0.01)
    return True


@pytest.mark.asyncio
async def test_hung_call_frees_client_id_and_next_acquire_gets_fresh_session(monkeypatch):
    """A wedged /historical IB call must not wedge the data role.

    After the 15s bound fires and the client is retired, the zombie's
    disconnect must run out-of-band (NOT behind the hung awaitable) so the
    fixed client_id frees and the next acquire("data") reconnects cleanly.
    """
    monkeypatch.setattr(pool_module, "POOL_ROLES", {"data": 5})
    gateway = FakeGateway()
    monkeypatch.setattr(
        pool_module,
        "_connect_in_thread",
        lambda host, port, client_id, timeout=5: gateway.connect(client_id),
    )

    pool = pool_module.IBPool()
    assert await pool.connect_all() == {"data": True}
    zombie = pool.get("data")

    release = threading.Event()

    def wedged():
        release.wait(timeout=5)

    try:
        with pytest.raises(HTTPException) as exc:
            async with pool.acquire("data") as client:
                assert client is zombie
                await _bounded_pool_call(pool, "data", client, wedged, timeout=0.05)
        assert exc.value.status_code == 504

        freed = await _wait_for(lambda: 5 not in gateway.in_use)
        assert freed, (
            "retired data client still holds client_id 5 — disposal is stuck "
            "behind the hung awaitable"
        )
        assert zombie.disconnect_calls >= 1

        async with pool.acquire("data") as fresh:
            assert fresh is not zombie, "acquire must hand out a fresh session"
            assert fresh.ib.isConnected()
    finally:
        release.set()
        await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_retire_disposes_while_worker_still_wedged(monkeypatch):
    """retire() itself must schedule the disconnect — no worker cooperation."""
    monkeypatch.setattr(pool_module, "POOL_ROLES", {"data": 5})
    gateway = FakeGateway()
    pool = pool_module.IBPool()
    client = gateway.connect(5)
    pool._clients["data"] = client
    pool._connected["data"] = True

    assert await pool.retire("data", client) is True

    disposed = await _wait_for(lambda: client.disconnect_calls == 1)
    assert disposed, "retire() never disconnected the quarantined client"
    assert 5 not in gateway.in_use
    assert pool.get("data") is None


@pytest.mark.asyncio
async def test_reconnect_roles_leaves_live_roles_untouched(monkeypatch):
    """Role-scoped reconnect must not tear down live money-role sessions."""
    monkeypatch.setattr(
        pool_module, "POOL_ROLES", {"sync": 3, "orders": 4, "data": 5}
    )
    gateway = FakeGateway()
    monkeypatch.setattr(
        pool_module,
        "_connect_in_thread",
        lambda host, port, client_id, timeout=5: gateway.connect(client_id),
    )

    pool = pool_module.IBPool()
    sync_client = gateway.connect(3)
    orders_client = gateway.connect(4)
    pool._clients.update({"sync": sync_client, "orders": orders_client})
    pool._connected.update({"sync": True, "orders": True, "data": False})

    status = await pool.reconnect_roles(["data"])

    assert status == {"data": True}
    assert pool.get("sync") is sync_client
    assert pool.get("orders") is orders_client
    assert sync_client.disconnect_calls == 0
    assert orders_client.disconnect_calls == 0
    assert pool.get("data") is not None
    assert pool.get("data").ib.isConnected()
