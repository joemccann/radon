"""Disk cache for price history fetches (stocks + options).

Stores per-contract JSON files with TTL-based expiration.
Filenames use SHA-256 of the cache key to avoid path issues.
Written via atomic_save() for crash safety.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from utils.atomic_io import atomic_save, verified_load

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "price_history_cache"
STOCKS_DIR = CACHE_DIR / "stocks"
OPTIONS_DIR = CACHE_DIR / "options"

# Auto-create cache directories on import
os.makedirs(STOCKS_DIR, exist_ok=True)
os.makedirs(OPTIONS_DIR, exist_ok=True)

# TTLs in seconds
TTL_MARKET_HOURS = 15 * 60       # 15 min
TTL_AFTER_CLOSE = 24 * 60 * 60   # 24 hours


def cache_key_stock(symbol: str, start: str, end: str) -> str:
    return f"{symbol}|{start}|{end}|v1"


def cache_key_option(option_id: str, start: str, end: str) -> str:
    return f"{option_id}|{start}|{end}|v1"


def _filename(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest() + ".json"


def is_market_hours() -> bool:
    """Check if within 9:30-16:00 ET Mon-Fri."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]
    now = datetime.now(ZoneInfo("America/New_York"))
    if now.weekday() >= 5:  # Sat/Sun
        return False
    t = now.hour * 60 + now.minute
    return 570 <= t < 960  # 9:30 (570) to 16:00 (960)


def _resolve_ttl(source: str) -> int:
    """Return TTL based on market hours."""
    return TTL_MARKET_HOURS if is_market_hours() else TTL_AFTER_CLOSE


def _cache_path(subdir: Path, key: str) -> Path:
    return subdir / _filename(key)


def _quarantine_invalid(path: Path) -> None:
    """Move an invalid cache entry aside so every read remains a cheap miss."""
    try:
        path.replace(path.with_name(f"{path.name}.invalid-{time.time_ns()}"))
    except OSError:
        try:
            path.unlink()
        except OSError:
            pass


def read_cache(cache_dir: Path, key: str) -> Optional[Dict[str, float]]:
    """Read cached price history. Returns data dict or None on miss/expired/corrupt."""
    subdir = STOCKS_DIR if cache_dir == STOCKS_DIR else OPTIONS_DIR
    path = _cache_path(subdir, key)
    if not path.exists():
        return None
    try:
        data = verified_load(str(path))
        if not isinstance(data, dict):
            raise TypeError("cache envelope must be an object")
        fetched_at = data.get("fetched_at")
        ttl = data.get("ttl_seconds")
        prices = data.get("data")
        if not isinstance(fetched_at, str) or not fetched_at:
            raise TypeError("cache fetched_at must be a timestamp string")
        if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl <= 0:
            raise TypeError("cache ttl_seconds must be a positive integer")
        if not isinstance(prices, dict) or not all(
            isinstance(day, str)
            and day
            and not isinstance(price, bool)
            and isinstance(price, (int, float))
            and math.isfinite(float(price))
            for day, price in prices.items()
        ):
            raise TypeError("cache data must be a finite numeric price mapping")
    except (TypeError, ValueError, json.JSONDecodeError, FileNotFoundError):
        _quarantine_invalid(path)
        return None

    try:
        fetched_ts = datetime.fromisoformat(fetched_at).timestamp()
        if time.time() - fetched_ts > ttl:
            return None  # Expired
    except (ValueError, TypeError):
        _quarantine_invalid(path)
        return None

    return prices


def write_cache(cache_dir: Path, key: str, data: Dict[str, float], source: str, ttl: int) -> None:
    """Write price history to cache atomically."""
    subdir = STOCKS_DIR if cache_dir == STOCKS_DIR else OPTIONS_DIR
    path = _cache_path(subdir, key)
    payload = {
        "key": key,
        "source": source,
        "fetched_at": datetime.now().isoformat(),
        "ttl_seconds": ttl,
        "data": data,
    }
    atomic_save(str(path), payload)
    # R-033: the 500-file cap used to be enforced only by the shelved
    # performance rebuild — every other writer grew the cache unboundedly.
    # Cheap: prune_cache is a no-op count check below the cap.
    prune_cache(subdir)


def prune_cache(cache_dir: Path, max_files: int = 500) -> int:
    """Delete oldest files by mtime when count exceeds max_files.

    Thread-safe: call ONCE after all parallel writes complete.
    Returns count of files deleted.
    """
    all_files = []
    for subdir in (STOCKS_DIR, OPTIONS_DIR):
        for f in subdir.iterdir():
            if f.suffix == ".json":
                try:
                    all_files.append((f, f.stat().st_mtime))
                except OSError:
                    continue

    if len(all_files) <= max_files:
        return 0

    # Sort oldest first
    all_files.sort(key=lambda x: x[1])
    to_delete = len(all_files) - max_files
    deleted = 0
    for f, _ in all_files[:to_delete]:
        try:
            f.unlink()
            deleted += 1
        except OSError:
            continue
    return deleted
