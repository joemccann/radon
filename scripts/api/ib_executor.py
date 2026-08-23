"""Dedicated bounded thread pool for IB data-role work (R-143).

``asyncio.to_thread`` runs on the loop's DEFAULT executor, which on a 2-vCPU
Hetzner box is ``min(32, cpu_count + 4)`` threads and also serves Clerk JWKS
verification, Turso writes, the IB port probes and order cancel/place. An IB
call that wedges cannot be cancelled — ``_bounded_pool_call`` shields and
reaps it, and ``pool.retire`` fires a second uncancellable disconnect — so a
handful of wedged data-role calls used to queue every ``to_thread`` in
FastAPI forever, cancel and replace included, until someone restarted
``radon-api``.

IB data work runs here instead. Two properties the default executor lacked:

* **Isolation** — a wedged IB worker consumes an IB thread, never the thread
  a Clerk verification or a Turso write is waiting for.
* **A ceiling** — wedged workers are counted, and once too few usable threads
  remain the acquire fails loudly and immediately instead of queueing behind
  corpses.

Order management deliberately stays on the default executor: it must not
queue behind wedged data calls either.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

logger = logging.getLogger("radon.ib_executor")

THREAD_NAME_PREFIX = "radon-ib"
DEFAULT_MAX_WORKERS = 8
# Refuse while fewer than this many threads are still usable. At 1 the last
# usable thread would be handed to the next caller and the pool would only
# report saturation once it was already fully wedged.
MIN_USABLE_WORKERS = 2


class IBExecutorSaturated(RuntimeError):
    """Too many IB workers are wedged to admit another call."""


_lock = threading.Lock()
_executor: Optional[ThreadPoolExecutor] = None
_max_workers: int = 0
_wedged: int = 0


def _configured_workers() -> int:
    raw = os.environ.get("RADON_IB_THREAD_WORKERS", "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_MAX_WORKERS
    return value if value >= 2 else DEFAULT_MAX_WORKERS


def _ensure_executor() -> ThreadPoolExecutor:
    global _executor, _max_workers
    with _lock:
        if _executor is None:
            _max_workers = _configured_workers()
            _executor = ThreadPoolExecutor(
                max_workers=_max_workers, thread_name_prefix=THREAD_NAME_PREFIX
            )
        return _executor


def max_workers() -> int:
    _ensure_executor()
    return _max_workers


def wedged_workers() -> int:
    """Threads occupied by calls that timed out and never came back."""
    return _wedged


def mark_wedged() -> None:
    global _wedged
    with _lock:
        _wedged += 1
    logger.error(
        "IB executor: worker wedged (%d/%d threads unusable)", _wedged, _max_workers
    )


def clear_wedged() -> None:
    global _wedged
    with _lock:
        if _wedged > 0:
            _wedged -= 1
            freed = _wedged
        else:
            return
    logger.warning(
        "IB executor: wedged worker returned (%d/%d still unusable)",
        freed, _max_workers,
    )


def has_capacity() -> bool:
    return (max_workers() - wedged_workers()) > MIN_USABLE_WORKERS - 1


def submit(fn: Callable[..., Any], *args: Any, force: bool = False,
           **kwargs: Any) -> asyncio.Future:
    """Schedule ``fn`` on the IB executor. Raises when the pool is wedged.

    ``force`` admits cleanup work (a retired client's disconnect) past the
    ceiling: refusing it would leave the client id held. It still runs HERE,
    which is the point — a disconnect that wedges consumes an IB thread and
    trips the ceiling for new data calls, never a default-executor thread.
    """
    executor = _ensure_executor()
    if not force and not has_capacity():
        raise IBExecutorSaturated(
            f"{wedged_workers()} of {max_workers()} IB threads are wedged"
        )
    loop = asyncio.get_running_loop()
    return loop.run_in_executor(executor, functools.partial(fn, *args, **kwargs))


def reset_for_test(max_workers: Optional[int] = None) -> None:
    """Drop the executor and the wedge counter between tests.

    Never shuts down the old pool: a wedged worker cannot be joined, and
    ``shutdown(wait=True)`` would hang the suite.
    """
    global _executor, _max_workers, _wedged
    with _lock:
        _executor = None
        _wedged = 0
    if max_workers is not None:
        os.environ["RADON_IB_THREAD_WORKERS"] = str(max_workers)
    else:
        os.environ.pop("RADON_IB_THREAD_WORKERS", None)
    _ensure_executor()
