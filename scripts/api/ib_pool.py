"""IB connection pool with role-based persistent connections.

Maintains long-lived IBClient connections keyed by role (sync, orders, data).
Each role maps to a specific client_id as defined in IBClient.CLIENT_IDS.
asyncio.Lock per role ensures serialized access (IB socket is not concurrent-safe).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, Optional

logger = logging.getLogger("radon.ib_pool")

# Import path setup — scripts/api/ needs scripts/ on sys.path
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from clients.ib_client import IBClient, POOL_ROLES, DEFAULT_HOST, DEFAULT_GATEWAY_PORT


def _connect_in_thread(host: str, port: int, client_id: int, timeout: int = 5) -> IBClient:
    """Connect an IBClient in a thread with its own event loop.

    ib_insync needs an event loop in the connecting thread. When called
    from asyncio.to_thread(), the thread has no loop by default.
    """
    import asyncio as _aio
    try:
        _aio.get_event_loop()
    except RuntimeError:
        _aio.set_event_loop(_aio.new_event_loop())

    client = IBClient()
    client.connect(host=host, port=port, client_id=client_id, timeout=timeout)
    return client


class IBPool:
    """Role-based IB connection pool.

    Usage:
        pool = IBPool()
        await pool.connect_all()

        async with pool.acquire("sync") as client:
            positions = client.get_positions()

        await pool.disconnect_all()
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_GATEWAY_PORT,
    ):
        self._host = host
        self._port = port
        self._clients: Dict[str, IBClient] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._connected: Dict[str, bool] = {}

        for role in POOL_ROLES:
            self._locks[role] = asyncio.Lock()
            self._connected[role] = False

    async def connect_all(self) -> Dict[str, bool]:
        """Connect all pool roles. Returns status per role.

        Non-blocking: if IB Gateway is down, roles start disconnected.
        IB-dependent endpoints will return 503; UW-only endpoints still work.
        """
        status = {}
        for i, (role, client_id) in enumerate(POOL_ROLES.items()):
            # IB Gateway rate-limits rapid successive connections — stagger by 1s
            if i > 0:
                await asyncio.sleep(1)

            connected = False
            for attempt in range(3):
                try:
                    client = await asyncio.to_thread(
                        _connect_in_thread,
                        self._host, self._port, client_id, 10,
                    )
                    self._clients[role] = client
                    self._connected[role] = True
                    status[role] = True
                    connected = True
                    logger.info("IB pool: %s connected (client_id=%d)", role, client_id)
                    break
                except Exception as e:
                    if attempt < 2:
                        logger.info("IB pool: %s attempt %d failed, retrying in 2s: %s", role, attempt + 1, e)
                        await asyncio.sleep(2)
                    else:
                        self._connected[role] = False
                        status[role] = False
                        logger.warning("IB pool: %s failed to connect after 3 attempts: %s", role, e)

        return status

    async def disconnect_all(self) -> None:
        """Disconnect all pool connections."""
        for role, client in self._clients.items():
            try:
                await asyncio.to_thread(client.disconnect)
                logger.info("IB pool: %s disconnected", role)
            except Exception as e:
                logger.warning("IB pool: %s disconnect error: %s", role, e)
            self._connected[role] = False
        self._clients.clear()

    def get(self, role: str) -> Optional[IBClient]:
        """Get the client for a role (may be None if not connected)."""
        if role not in POOL_ROLES:
            raise ValueError(f"Unknown pool role: {role}. Valid: {list(POOL_ROLES.keys())}")
        return self._clients.get(role)

    def is_connected(self, role: str) -> bool:
        """Check if a role's connection is active."""
        client = self._clients.get(role)
        if client is None:
            return False
        try:
            return client.ib.isConnected()
        except Exception:
            return False

    def acquire(self, role: str) -> _PoolContext:
        """Acquire exclusive access to a role's connection.

        Usage:
            async with pool.acquire("sync") as client:
                data = client.get_positions()
        """
        return _PoolContext(self, role)

    async def _reconnect(self, role: str) -> bool:
        """Attempt to reconnect a disconnected role."""
        client_id = POOL_ROLES[role]
        try:
            client = await asyncio.to_thread(
                _connect_in_thread,
                self._host, self._port, client_id, 5,
            )
            self._clients[role] = client
            self._connected[role] = True
            logger.info("IB pool: %s reconnected (client_id=%d)", role, client_id)
            return True
        except Exception as e:
            self._connected[role] = False
            logger.warning("IB pool: %s reconnect failed: %s", role, e)
            return False

    def status(self) -> dict:
        """Return pool status for health endpoint."""
        return {
            role: {
                "connected": self.is_connected(role),
                "client_id": POOL_ROLES[role],
            }
            for role in POOL_ROLES
        }


class _PoolContext:
    """Async context manager for exclusive role access."""

    def __init__(self, pool: IBPool, role: str):
        self._pool = pool
        self._role = role

    async def __aenter__(self) -> IBClient:
        await self._pool._locks[self._role].acquire()

        # Auto-reconnect if connection dropped
        if not self._pool.is_connected(self._role):
            reconnected = await self._pool._reconnect(self._role)
            if not reconnected:
                self._pool._locks[self._role].release()
                raise ConnectionError(f"IB pool: {self._role} is not connected")

        client = self._pool.get(self._role)
        if client is None:
            self._pool._locks[self._role].release()
            raise ConnectionError(f"IB pool: {self._role} has no client")

        return client

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self._pool._locks[self._role].release()
        return False
