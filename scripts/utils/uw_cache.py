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


def get_disk_cached(key: str, now: Optional[float] = None) -> Optional[Any]:
    """Return a non-expired disk payload for key, else None."""
    path = _disk_path(key)
    if not path.exists():
        return None
    try:
        with open(path) as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    cached_at = payload.get("cached_at")
    ttl = payload.get("ttl_seconds")
    if isinstance(cached_at, bool) or not isinstance(cached_at, (int, float)):
        return None
    if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or ttl <= 0:
        return None
    clock = time.time() if now is None else now
    if clock - float(cached_at) >= float(ttl):
        return None
    return payload.get("data")


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