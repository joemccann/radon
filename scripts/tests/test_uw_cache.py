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
