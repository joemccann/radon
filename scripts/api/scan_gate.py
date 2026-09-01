"""Admission policy for the intraday scan routes (/regime, /breadth, /vcg, /gex).

A completed scan is authoritative for the cooldown, so callers inside it are
served from cache without a subprocess. A failed scan (typically "Subprocess
capacity exhausted" on the 2-vCPU host) arms a backoff during which the route
refuses with 429 + Retry-After rather than re-spawning — the 2026-08-24 storm
was every 5 s client poll re-firing a scan the moment the previous one 502'd.
"""
from __future__ import annotations

import asyncio
import math
import time
from typing import Callable, Optional

SCAN_COOLDOWN_S = 120
SCAN_FAILURE_BACKOFF_S = 60

_NEVER = float("-inf")


class ScanGate:
    def __init__(
        self,
        name: str,
        *,
        cooldown_s: float = SCAN_COOLDOWN_S,
        failure_backoff_s: float = SCAN_FAILURE_BACKOFF_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self.cooldown_s = cooldown_s
        self.failure_backoff_s = failure_backoff_s
        self._clock = clock
        self._last_success = _NEVER
        self._last_failure = _NEVER
        self._lock: Optional[asyncio.Lock] = None

    @property
    def lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def in_cooldown(self) -> bool:
        return self._clock() - self._last_success < self.cooldown_s

    def in_backoff(self) -> bool:
        return self._clock() - self._last_failure < self.failure_backoff_s

    def retry_after(self) -> float:
        """Seconds until the gate admits a new scan; 0 when it admits now."""
        now = self._clock()
        remaining = max(
            self._last_success + self.cooldown_s - now,
            self._last_failure + self.failure_backoff_s - now,
        )
        return max(0.0, remaining)

    def retry_after_header(self) -> dict[str, str]:
        return {"Retry-After": str(max(1, math.ceil(self.retry_after())))}

    def mark_success(self) -> None:
        self._last_success = self._clock()
        self._last_failure = _NEVER

    def mark_failure(self) -> None:
        self._last_failure = self._clock()

    def reset(self) -> None:
        """Disarm the gate: no cooldown, no backoff."""
        self._last_success = self._last_failure = _NEVER

    def __repr__(self) -> str:
        return f"ScanGate({self.name!r}, retry_after={self.retry_after():.1f}s)"
