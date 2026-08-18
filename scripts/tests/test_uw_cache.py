"""Disk UW GET cache: endpoint-class TTLs, temp-dir isolation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from utils import uw_cache


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "uw_http_cache"
    monkeypatch.setattr(uw_cache, "CACHE_DIR", target)
    return target


def test_memory_ttl_stays_60s() -> None:
    assert uw_cache.TTL_SECONDS == 60


@pytest.mark.parametrize(
    ("endpoint", "ttl"),
    [
        ("stock/SPY/ohlc", 15 * 60),
        ("stock/AAPL/iv-rank", 15 * 60),
        ("stock/AAPL/greek-exposure", 15 * 60),
        ("stock/AAPL/greek-exposure/strike", 15 * 60),
        ("stock/AAPL/option-contracts", 15 * 60),
        ("stock/AAPL/info", 60 * 60),
        ("stock/AAPL/flow-alerts", 2 * 60),
        ("option-trades/flow-alerts", 2 * 60),
        ("stock/AAPL/flow-per-strike", 2 * 60),
        ("darkpool/AAPL", 60),
        ("etfs/SPY/info", 60),
    ],
)
def test_endpoint_class_ttls(endpoint: str, ttl: int) -> None:
    assert uw_cache.endpoint_ttl(endpoint) == ttl


def test_disk_round_trip_uses_temp_dir(cache_dir: Path) -> None:
    key = uw_cache.make_key("stock/SPY/ohlc", None)
    payload = {"data": [{"close": 190.0}]}
    uw_cache.set_disk_cached(key, "stock/SPY/ohlc", payload, now=1_000.0)

    files = list(cache_dir.glob("*.json"))
    assert len(files) == 1
    assert files[0].parent == cache_dir
    assert uw_cache.get_disk_cached(key, now=1_000.0) == payload


def test_disk_expired_is_miss(cache_dir: Path) -> None:
    key = uw_cache.make_key("stock/SPY/ohlc", None)
    uw_cache.set_disk_cached(key, "stock/SPY/ohlc", {"data": []}, now=1_000.0)
    assert uw_cache.get_disk_cached(key, now=1_000.0 + 15 * 60) is None


def test_stock_info_disk_ttl_outlives_15_min(cache_dir: Path) -> None:
    key = uw_cache.make_key("stock/AAPL/info", None)
    payload = {"data": {"ticker": "AAPL"}}
    uw_cache.set_disk_cached(key, "stock/AAPL/info", payload, now=1_000.0)
    assert uw_cache.get_disk_cached(key, now=1_000.0 + 15 * 60 + 1) == payload
    assert uw_cache.get_disk_cached(key, now=1_000.0 + 60 * 60) is None


def test_params_are_distinct_disk_keys(cache_dir: Path) -> None:
    a = uw_cache.make_key("stock/AAPL/option-contracts", {"expiry": "2026-03-20"})
    b = uw_cache.make_key("stock/AAPL/option-contracts", {"expiry": "2026-06-19"})
    uw_cache.set_disk_cached(a, "stock/AAPL/option-contracts", {"data": [1]}, now=1_000.0)
    uw_cache.set_disk_cached(b, "stock/AAPL/option-contracts", {"data": [2]}, now=1_000.0)
    assert uw_cache.get_disk_cached(a, now=1_000.0) == {"data": [1]}
    assert uw_cache.get_disk_cached(b, now=1_000.0) == {"data": [2]}


def test_corrupt_disk_entry_is_miss(cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = uw_cache.make_key("stock/AAPL/info", None)
    path = cache_dir / (uw_cache._disk_filename(key))
    path.write_text("{not-json")
    assert uw_cache.get_disk_cached(key, now=1_000.0) is None


def test_disk_file_stores_endpoint_ttl(cache_dir: Path) -> None:
    key = uw_cache.make_key("option-trades/flow-alerts", None)
    uw_cache.set_disk_cached(key, "option-trades/flow-alerts", {"data": []}, now=2_000.0)
    raw = json.loads(next(cache_dir.glob("*.json")).read_text())
    assert raw["ttl_seconds"] == 2 * 60
    assert raw["cached_at"] == 2_000.0
    assert raw["data"] == {"data": []}


# ── R-069: eviction — expired/over-cap entries must not accumulate forever ──


def test_expired_disk_entry_is_unlinked_on_read(cache_dir: Path) -> None:
    key = uw_cache.make_key("stock/SPY/ohlc", None)
    uw_cache.set_disk_cached(key, "stock/SPY/ohlc", {"data": []}, now=1_000.0)
    assert uw_cache.get_disk_cached(key, now=1_000.0 + 16 * 60) is None
    assert list(cache_dir.glob("*.json")) == []


def test_corrupt_disk_entry_is_unlinked_on_read(cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = uw_cache.make_key("stock/AAPL/info", None)
    path = cache_dir / uw_cache._disk_filename(key)
    path.write_text("{not-json")
    assert uw_cache.get_disk_cached(key, now=1_000.0) is None
    assert not path.exists()


def _write_entry_with_mtime(cache_dir: Path, name: str, mtime: float) -> Path:
    import os

    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / name
    path.write_text(json.dumps({"cached_at": mtime, "ttl_seconds": 60, "data": {}}))
    os.utime(path, (mtime, mtime))
    return path


def test_prune_removes_entries_older_than_the_longest_ttl(cache_dir: Path) -> None:
    now = 1_000_000.0
    stale = _write_entry_with_mtime(cache_dir, "a" * 64 + ".json", now - 2 * 60 * 60)
    fresh = _write_entry_with_mtime(cache_dir, "b" * 64 + ".json", now - 30)
    removed = uw_cache.prune_disk_cache(now=now)
    assert removed == 1
    assert not stale.exists()
    assert fresh.exists()


def test_prune_removes_orphaned_tmp_files(cache_dir: Path) -> None:
    now = 1_000_000.0
    tmp = _write_entry_with_mtime(cache_dir, "c" * 64 + ".json.tmp", now - 2 * 60 * 60)
    uw_cache.prune_disk_cache(now=now)
    assert not tmp.exists()


def test_prune_caps_file_count_oldest_first(cache_dir: Path) -> None:
    now = 1_000_000.0
    paths = [
        _write_entry_with_mtime(cache_dir, f"{i:064d}.json", now - 100 + i)
        for i in range(5)
    ]
    uw_cache.prune_disk_cache(now=now, max_files=3)
    assert [p.exists() for p in paths] == [False, False, True, True, True]


def test_cap_outlives_one_preset_scan_cluster() -> None:
    """The cap must exceed what the scanners write in a single window.

    garch, leap, theta-harvester and strength-confirmation all fire inside
    the same two minutes over overlapping ndx100 / indexes universes, at
    3-5 UW paths per ticker — roughly 2000 distinct keys. A cap below that
    evicts the first scanner's entries before the next scanner can reuse
    them, so every scanner re-fetches the same OHLC and contracts from UW.
    """
    assert uw_cache.MAX_DISK_FILES >= 2000


def test_set_disk_cached_enforces_the_cap(cache_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(uw_cache, "MAX_DISK_FILES", 2)
    for i, endpoint in enumerate(("stock/A/info", "stock/B/info", "stock/C/info")):
        key = uw_cache.make_key(endpoint, None)
        uw_cache.set_disk_cached(key, endpoint, {"data": [i]})
    assert len(list(cache_dir.glob("*.json"))) <= 2
