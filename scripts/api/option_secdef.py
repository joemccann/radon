"""One IB option secdef read per underlying.

Expirations and every strike list on a chain page are slices of the same
reqSecDefOptParams result. A short TTL covers that page, including the
staggered expiry prefetch, without pinning a subprocess slot per expiry.
"""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable

OPTION_SECDEF_TTL_S = 60.0

Loader = Callable[[str], Awaitable[dict]]


class OptionSecdefError(Exception):
    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


class OptionSecdefCache:
    """Join in-flight readers and reuse a fresh snapshot. Failures are not stored."""

    def __init__(self, ttl_s: float = OPTION_SECDEF_TTL_S, clock=time.monotonic):
        self.ttl_s = ttl_s
        self._clock = clock
        self._stored: dict[str, tuple[float, dict]] = {}
        self._inflight: dict[str, asyncio.Task] = {}

    def clear(self) -> None:
        self._stored.clear()

    async def get(self, symbol: str, loader: Loader) -> dict:
        key = symbol.upper()
        fresh = self._fresh(key)
        if fresh is not None:
            return fresh
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(self._load(key, loader))
            self._inflight[key] = task
        return await asyncio.shield(task)

    def _fresh(self, key: str) -> dict | None:
        row = self._stored.get(key)
        if row is None:
            return None
        stored_at, payload = row
        if self._clock() - stored_at >= self.ttl_s:
            return None
        return payload

    async def _load(self, key: str, loader: Loader) -> dict:
        try:
            payload = await loader(key)
        except Exception:
            raise
        else:
            self._stored[key] = (self._clock(), payload)
            return payload
        finally:
            self._inflight.pop(key, None)


def expirations_from_snapshot(snapshot: dict) -> dict:
    return {
        "symbol": snapshot.get("symbol"),
        "expirations": list(snapshot.get("expirations") or []),
    }


def chain_from_snapshot(snapshot: dict, expiry: str) -> dict:
    normalized = str(expiry).replace("-", "")
    book = snapshot.get("by_expiry") or {}
    row = book.get(normalized)
    if not isinstance(row, dict):
        raise OptionSecdefError(f"No chain found for expiry {expiry}")
    return {
        "symbol": snapshot.get("symbol"),
        "expiry": expiry,
        "exchange": row.get("exchange") or "",
        "strikes": list(row.get("strikes") or []),
        "multiplier": row.get("multiplier") or "",
    }
