"""In-memory (60s) and disk UW GET cache with endpoint-class TTLs.

Memory entries expire after TTL_SECONDS (60s). Disk files live under
data/uw_http_cache/ and use longer TTLs for slow-moving endpoints.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# Default in-memory TTL in seconds
TTL_SECONDS = 60

TTL_OHLC_IV_GEX_CONTRACTS = 15 * 60
TTL_STOCK_INFO = 60 * 60
TTL_FLOW_ALERTS = 2 * 60
TTL_DEFAULT = 60

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "uw_http_cache"

# Global cache: {cache_key: (timestamp, data)}
_cache: Dict[str, Tuple[float, Any]] = {}


def get_cached(key: str) -> Optional[Any]:
    """Get a cached value if it exists and hasn't expired."""
    entry = _cache.get(key)
    if entry is None:
        return None
    timestamp, data = entry
    if time.time() - timestamp > TTL_SECONDS:
        del _cache[key]
        return None
    return data


def set_cached(key: str, data: Any) -> None:
    """Store a value in the cache."""
    _cache[key] = (time.time(), data)


def make_key(endpoint: str, params: Optional[Dict] = None) -> str:
    """Create a cache key from endpoint and params."""
    if params:
        sorted_params = sorted(params.items())
        param_str = "&".join(f"{k}={v}" for k, v in sorted_params)
        return f"{endpoint}?{param_str}"
    return endpoint


def clear_cache() -> None:
    """Clear all cached entries."""
    _cache.clear()


def endpoint_ttl(endpoint: str) -> int:
    """Return the on-disk TTL for a UW GET path."""
    path = endpoint.lstrip("/").lower()
    if any(
        token in path
        for token in ("ohlc", "iv-rank", "greek-exposure", "option-contracts")
    ):
        return TTL_OHLC_IV_GEX_CONTRACTS
    parts = path.split("/")
    if len(parts) == 3 and parts[0] == "stock" and parts[2] == "info":
        return TTL_STOCK_INFO
    if "flow" in path or "alert" in path:
        return TTL_FLOW_ALERTS
    return TTL_DEFAULT


def _disk_filename(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest() + ".json"


def _disk_path(key: str) -> Path:
    return CACHE_DIR / _disk_filename(key)


# R-069: without eviction every distinct endpoint+params tuple leaves a file
# behind forever and the VPS root fs fills silently. Expired/corrupt entries
# are unlinked on read; every write sweeps anything older than the longest
# endpoint-class TTL and caps the file count oldest-first.
MAX_DISK_FILES = 512
_MAX_TTL_SECONDS = max(
    TTL_OHLC_IV_GEX_CONTRACTS, TTL_STOCK_INFO, TTL_FLOW_ALERTS, TTL_DEFAULT
)


def _unlink_quiet(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def get_disk_cached(key: str, now: Optional[float] = None) -> Optional[Any]:
    """Return a non-expired disk payload for key, else None.

    Expired or unparseable entries are unlinked — they can never be served
    again, so keeping them only grows the directory.
    """
    path = _disk_path(key)
    if not path.exists():
        return None
    try:
        with open(path) as handle:
            payload = json.load(handle)
    except OSError:
        return None
    except ValueError:
        _unlink_quiet(path)
        return None
    if not isinstance(payload, dict):
        _unlink_quiet(path)
        return None
    cached_at = payload.get("cached_at")
    ttl = payload.get("ttl_seconds")
    if isinstance(cached_at, bool) or not isinstance(cached_at, (int, float)):
        _unlink_quiet(path)
        return None
    if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or ttl <= 0:
        _unlink_quiet(path)
        return None
    clock = time.time() if now is None else now
    if clock - float(cached_at) >= float(ttl):
        _unlink_quiet(path)
        return None
    return payload.get("data")


def prune_disk_cache(
    now: Optional[float] = None, max_files: Optional[int] = None
) -> int:
    """Evict dead weight from CACHE_DIR. Returns files removed.

    Two sweeps by mtime (no JSON reads — cheap enough to run per write, the
    ``prune_cache`` precedent in utils/price_cache.py): anything older than
    the longest endpoint-class TTL is definitionally expired regardless of
    class (this also collects orphaned ``.tmp`` files from crashed writes),
    then the surviving ``.json`` count is capped oldest-first.
    """
    clock = time.time() if now is None else now
    cap = MAX_DISK_FILES if max_files is None else max_files
    try:
        entries = list(CACHE_DIR.iterdir())
    except OSError:
        return 0

    removed = 0
    survivors: list[tuple[Path, float]] = []
    for path in entries:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if clock - mtime > _MAX_TTL_SECONDS:
            _unlink_quiet(path)
            removed += 1
        elif path.suffix == ".json":
            survivors.append((path, mtime))

    if len(survivors) > cap:
        survivors.sort(key=lambda entry: entry[1])
        for path, _ in survivors[: len(survivors) - cap]:
            _unlink_quiet(path)
            removed += 1
    return removed


def set_disk_cached(
    key: str,
    endpoint: str,
    data: Any,
    now: Optional[float] = None,
) -> None:
    """Persist a successful UW GET body. No-op on I/O or encode failure."""
    clock = time.time() if now is None else now
    payload = {
        "key": key,
        "endpoint": endpoint,
        "cached_at": clock,
        "ttl_seconds": endpoint_ttl(endpoint),
        "data": data,
    }
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    path = _disk_path(key)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w") as handle:
            json.dump(payload, handle)
        os.replace(tmp, path)
    except (OSError, TypeError, ValueError):
        try:
            tmp.unlink()
        except OSError:
            pass
        return
    prune_disk_cache(now=clock)