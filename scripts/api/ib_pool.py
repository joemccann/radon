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
    try:
        client.connect(host=host, port=port, client_id=client_id, timeout=timeout)
        return client
    except BaseException:
        try:
            client.disconnect()
        except Exception:
            logger.debug("IB pool: failed connect cleanup failed", exc_info=True)
        raise


async def _connect_owned(
    host: str,
    port: int,
    client_id: int,
    timeout: int,
) -> IBClient:
    """Connect in a worker thread without abandoning a late client on cancel."""
    pending = asyncio.create_task(
        asyncio.to_thread(_connect_in_thread, host, port, client_id, timeout)
    )
    try:
        return await asyncio.shield(pending)
    except asyncio.CancelledError:
        # Cancelling ``to_thread`` cannot stop its worker. Wait for the bounded
        # connect call and dispose any client it returns before propagating the
        # cancellation, otherwise its client ID remains live but untracked.
        try:
            client = await asyncio.shield(pending)
        except Exception:
            pass
        else:
            try:
                await asyncio.to_thread(client.disconnect)
            except Exception:
                logger.warning(
                    "IB pool: cancelled connect returned a client that could not be disconnected",
                    exc_info=True,
                )
        raise


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
        self._lifecycle_lock = asyncio.Lock()
        self._lifecycle_transition = False

        for role in POOL_ROLES:
            self._locks[role] = asyncio.Lock()
            self._connected[role] = False

    async def connect_all(self) -> Dict[str, bool]:
        """Connect all pool roles. Returns status per role.

        Non-blocking: if IB Gateway is down, roles start disconnected.
        IB-dependent endpoints will return 503; UW-only endpoints still work.
        """
        async with self._lifecycle_lock:
            self._lifecycle_transition = True
            try:
                return await self._connect_all_unlocked()
            finally:
                self._lifecycle_transition = False

    async def _connect_all_unlocked(self) -> Dict[str, bool]:
        """Connect roles while the caller owns ``_lifecycle_lock``."""
        status = {}
        for i, (role, client_id) in enumerate(POOL_ROLES.items()):
            # IB Gateway rate-limits rapid successive connections — stagger by 1s
            if i > 0:
                await asyncio.sleep(1)

            async with self._locks[role]:
                if self.is_connected(role):
                    status[role] = True
                    continue

                stale = self._clients.pop(role, None)
                if stale is not None:
                    try:
                        await asyncio.to_thread(stale.disconnect)
                    except Exception:
                        logger.debug("IB pool: %s stale disconnect failed", role, exc_info=True)

                for attempt in range(3):
                    try:
                        client = await _connect_owned(
                            self._host, self._port, client_id, 10,
                        )
                        self._clients[role] = client
                        self._connected[role] = True
                        status[role] = True
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
        async with self._lifecycle_lock:
            self._lifecycle_transition = True
            try:
                await self._disconnect_all_unlocked()
            finally:
                self._lifecycle_transition = False

    async def _disconnect_all_unlocked(self) -> None:
        """Disconnect roles after active borrowers release their role locks."""
        for role in list(self._clients):
            async with self._locks[role]:
                client = self._clients.pop(role, None)
                if client is None:
                    continue
                try:
                    await asyncio.to_thread(client.disconnect)
                    logger.info("IB pool: %s disconnected", role)
                except Exception as e:
                    logger.warning("IB pool: %s disconnect error: %s", role, e)
                self._connected[role] = False

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
            stale = self._clients.pop(role, None)
            if stale is not None:
                try:
                    await asyncio.to_thread(stale.disconnect)
                except Exception:
                    logger.debug("IB pool: %s stale disconnect failed", role, exc_info=True)
            client = await _connect_owned(
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

    async def reconnect_all(self) -> Dict[str, bool]:
        """Drop and re-establish every pool role. Idempotent.

        Used by the auth-state transition handler when IBKR 2FA resolves while
        pool clients are still stuck in a disconnected state — a documented
        failure mode where the gateway returns `managedAccounts()` to a fresh
        probe but the pool's long-lived sockets don't auto-recover.

        Per-role disconnect errors are swallowed (the connection is dead
        anyway). Each role gets a fresh connection attempt via `connect_all()`.
        """
        logger.info("IB pool: reconnect_all — dropping all pool clients")
        async with self._lifecycle_lock:
            self._lifecycle_transition = True
            try:
                await self._disconnect_all_unlocked()
                return await self._connect_all_unlocked()
            finally:
                self._lifecycle_transition = False

    def status(self) -> dict:
        """Return pool status for health endpoint.

        Each role reports `connected` (TCP-level) plus `managed_accounts` (the
        accounts the IB client can see). A connected client with empty
        managed_accounts means the API socket is up but Gateway hasn't
        completed login — typically the IBKR mobile 2FA push is unanswered.
        """
        return {
            role: {
                "connected": self.is_connected(role),
                "client_id": POOL_ROLES[role],
                "managed_accounts": self._managed_accounts(role),
            }
            for role in POOL_ROLES
        }

    def _managed_accounts(self, role: str) -> list[str]:
        """Return managedAccounts() for a role, or [] if unavailable.

        Never raises — empty list signals either disconnected or pre-auth.
        """
        client = self._clients.get(role)
        if client is None:
            return []
        try:
            return list(client.ib.managedAccounts() or [])
        except Exception:
            return []


class _PoolContext:
    """Async context manager for exclusive role access."""

    def __init__(self, pool: IBPool, role: str):
        self._pool = pool
        self._role = role

    async def __aenter__(self) -> IBClient:
        await self._pool._locks[self._role].acquire()

        if self._pool._lifecycle_transition:
            self._pool._locks[self._role].release()
            raise ConnectionError("IB pool lifecycle transition in progress")

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
