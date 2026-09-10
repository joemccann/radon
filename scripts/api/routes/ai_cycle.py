"""Read-only AI infrastructure observations; collection belongs to the timer."""

from __future__ import annotations

import asyncio
import logging
import os
import time

from fastapi import APIRouter, HTTPException, Response

router = APIRouter()
logger = logging.getLogger("radon.ai_cycle")
_CACHE_SECONDS = 60
_READ_DEADLINE_SECONDS = 35
_cached: dict | None = None
_cached_at = 0.0
_inflight: asyncio.Task | None = None


def _read_snapshot() -> dict:
    # Lazy import avoids database work and provider imports during API startup.
    from ai_cycle.snapshot import load_api_snapshot
    from ai_cycle.store import ObservationStore

    store = ObservationStore(path=os.environ.get("RADON_AI_CYCLE_DB_PATH") or None)
    try:
        return load_api_snapshot(store)
    finally:
        store.close()


async def _load() -> dict:
    global _cached, _cached_at
    payload = await asyncio.wait_for(asyncio.to_thread(_read_snapshot), _READ_DEADLINE_SECONDS)
    _cached = payload
    _cached_at = time.monotonic()
    return payload


@router.get("/ai-cycle")
async def ai_cycle(response: Response):
    """Latest versioned AI measurements, source provenance and experimental state.

    No upstream collection or history scan occurs on this read. The compact
    API snapshot is written by the collector. Missing snapshots are registry
    status only; transport/storage failures remain 503.
    """
    global _inflight
    response.headers["Cache-Control"] = "private, no-store"
    if _cached is not None and time.monotonic() - _cached_at < _CACHE_SECONDS:
        return _cached
    if _inflight is None or _inflight.done():
        _inflight = asyncio.create_task(_load())
    try:
        return await asyncio.shield(_inflight)
    except Exception as exc:
        # The operator gets a stable error; credentials/transport URLs never
        # escape through exceptions from database clients.
        logger.warning("AI infrastructure read failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="AI infrastructure observations are temporarily unavailable",
            headers={"Cache-Control": "private, no-store"},
        ) from None
