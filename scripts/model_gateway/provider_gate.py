"""Per-provider admission for one gateway process.

Inference calls take a shared slot, up to ``capacity`` at once. Credential
renewal takes the gate exclusively: it waits for in-flight calls to finish and
holds new ones back while it waits, so a rotated refresh token is never
presented by a call already on the wire. Capacity 1 is the original
one-in-flight contract.
"""
from __future__ import annotations

import re
import threading
import time
from contextlib import contextmanager
from typing import Mapping

CONCURRENCY_ENV = "RADON_MODEL_PROVIDER_CONCURRENCY"
MAX_CONCURRENCY = 16


def provider_concurrency(env: Mapping[str, str]) -> int:
    raw = env.get(CONCURRENCY_ENV, "")
    if raw == "":
        return 1
    if not re.fullmatch(r"[1-9][0-9]?", raw) or int(raw) > MAX_CONCURRENCY:
        raise ValueError("invalid model provider concurrency")
    return int(raw)


class ProviderGate:
    def __init__(self, capacity: int = 1):
        if type(capacity) is not int or not 1 <= capacity <= MAX_CONCURRENCY:
            raise ValueError("invalid model provider concurrency")
        self.capacity = capacity
        self._cond = threading.Condition(threading.Lock())
        self._active = 0
        self._renewing = False
        self._renewals_waiting = 0

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        deadline = None if not blocking or timeout is None or timeout < 0 else time.monotonic() + timeout
        with self._cond:
            while self._renewing or self._renewals_waiting or self._active >= self.capacity:
                if not blocking:
                    return False
                if deadline is None:
                    self._cond.wait()
                    continue
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self._cond.wait(left)
            self._active += 1
            return True

    def release(self) -> None:
        with self._cond:
            if self._active <= 0:
                raise RuntimeError("release of an unacquired provider slot")
            self._active -= 1
            self._cond.notify_all()

    @contextmanager
    def exclusive(self):
        with self._cond:
            self._renewals_waiting += 1
            try:
                while self._renewing or self._active:
                    self._cond.wait()
            finally:
                self._renewals_waiting -= 1
            self._renewing = True
        try:
            yield self
        finally:
            with self._cond:
                self._renewing = False
                self._cond.notify_all()
