"""REL-015: acquire-time liveness probe for the orders/sync pool roles.

A half-open socket or wedged Java listener keeps ``ib.isConnected()`` True
while the next request blocks forever (ib_insync has no request timeouts).
Acquire for the money roles (orders, sync) must run a bounded
application-level probe and reconnect on failure; the data role stays on
the cheap TCP check.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from scripts.api import ib_pool as pool_module


class _FakeIB:
    def __init__(self, accounts=("U1",), probe_hang_secs: float = 0.0) -> None:
        self.connected = True
        self._accounts = list(accounts)
        self._probe_hang_secs = probe_hang_secs
        self.probe_calls = 0

    def isConnected(self) -> bool:
        return self.connected

    def managedAccounts(self):
        self.probe_calls += 1
        if self._probe_hang_secs:
            time.sleep(self._probe_hang_secs)
        return list(self._accounts)


class _FakeClient:
    def __init__(self, accounts=("U1",), probe_hang_secs: float = 0.0) -> None:
        self.ib = _FakeIB(accounts=accounts, probe_hang_secs=probe_hang_secs)
        self.disconnect_calls = 0

    def disconnect(self) -> None:
        self.disconnect_calls += 1
        self.ib.connected = False


def _pool_with(monkeypatch, role: str, client: _FakeClient) -> pool_module.IBPool:
    monkeypatch.setattr(pool_module, "POOL_ROLES", {role: 3})
    pool = pool_module.IBPool()
    pool._clients[role] = client
    pool._connected[role] = True
    return pool


def _fresh_client_factory(monkeypatch):
    """Route _connect_in_thread to healthy replacement clients; count calls."""
    created: list[_FakeClient] = []

    def connect(*args, **kwargs):
        replacement = _FakeClient()
        created.append(replacement)
        return replacement

    monkeypatch.setattr(pool_module, "_connect_in_thread", connect)
    return created


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["orders", "sync"])
async def test_probe_empty_accounts_reconnects_money_role(monkeypatch, role):
    wedged = _FakeClient(accounts=())
    pool = _pool_with(monkeypatch, role, wedged)
    created = _fresh_client_factory(monkeypatch)

    async with pool.acquire(role) as client:
        assert client is not wedged
        assert created == [client]


@pytest.mark.asyncio
async def test_probe_timeout_reconnects_instead_of_handing_out(monkeypatch):
    monkeypatch.setattr(pool_module, "LIVENESS_PROBE_TIMEOUT_SECS", 0.05)
    wedged = _FakeClient(probe_hang_secs=0.5)
    pool = _pool_with(monkeypatch, "orders", wedged)
    created = _fresh_client_factory(monkeypatch)

    async with pool.acquire("orders") as client:
        assert client is not wedged
        assert created == [client]


@pytest.mark.asyncio
async def test_healthy_probe_hands_out_existing_client(monkeypatch):
    healthy = _FakeClient(accounts=("U1",))
    pool = _pool_with(monkeypatch, "orders", healthy)
    created = _fresh_client_factory(monkeypatch)

    async with pool.acquire("orders") as client:
        assert client is healthy
        assert created == []
    assert healthy.ib.probe_calls == 1


@pytest.mark.asyncio
async def test_data_role_skips_probe(monkeypatch):
    wedged = _FakeClient(accounts=())
    pool = _pool_with(monkeypatch, "data", wedged)
    created = _fresh_client_factory(monkeypatch)

    async with pool.acquire("data") as client:
        assert client is wedged
        assert created == []
    assert wedged.ib.probe_calls == 0


@pytest.mark.asyncio
async def test_probe_dead_and_reconnect_failed_raises_pool_error(monkeypatch):
    wedged = _FakeClient(accounts=())
    pool = _pool_with(monkeypatch, "orders", wedged)

    def connect(*args, **kwargs):
        raise ConnectionRefusedError("gateway down")

    monkeypatch.setattr(pool_module, "_connect_in_thread", connect)

    with pytest.raises(ConnectionError, match="not connected"):
        async with pool.acquire("orders"):
            pass

    assert not pool._locks["orders"].locked()
