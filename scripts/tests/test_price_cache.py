"""Tests for scripts/utils/price_cache.py — disk cache for price histories."""

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# price_cache auto-creates dirs on import; patch os.makedirs to avoid side effects
with patch("os.makedirs"):
    from utils.price_cache import (
        STOCKS_DIR,
        OPTIONS_DIR,
        cache_key_stock,
        cache_key_option,
        _filename,
        is_market_hours,
        read_cache,
        write_cache,
        prune_cache,
    )


@pytest.fixture()
def cache_dirs(tmp_path):
    """Create temporary stock/option cache directories."""
    stocks = tmp_path / "stocks"
    options = tmp_path / "options"
    stocks.mkdir()
    options.mkdir()
    return stocks, options


class TestCacheKeys:
    def test_stock_key_format(self):
        key = cache_key_stock("SPY", "2026-01-01", "2026-03-17")
        assert key == "SPY|2026-01-01|2026-03-17|v1"

    def test_option_key_format(self):
        key = cache_key_option("AAPL260321C00230000", "2026-01-01", "2026-03-17")
        assert key == "AAPL260321C00230000|2026-01-01|2026-03-17|v1"

    def test_filename_is_sha256(self):
        key = "SPY|2026-01-01|2026-03-17|v1"
        name = _filename(key)
        assert len(name) == 64 + 5  # sha256 hex + ".json"
        assert name.endswith(".json")

    def test_different_keys_different_filenames(self):
        f1 = _filename(cache_key_stock("SPY", "2026-01-01", "2026-03-17"))
        f2 = _filename(cache_key_stock("QQQ", "2026-01-01", "2026-03-17"))
        assert f1 != f2

    def test_schema_version_in_key(self):
        key = cache_key_stock("SPY", "2026-01-01", "2026-03-17")
        assert "|v1" in key


class TestReadWriteCache:
    def test_write_then_read(self, cache_dirs):
        stocks_dir, _ = cache_dirs
        key = cache_key_stock("SPY", "2026-01-01", "2026-03-17")
        data = {"2026-01-02": 230.5, "2026-01-03": 232.1}

        with patch("utils.price_cache.STOCKS_DIR", stocks_dir), \
             patch("utils.price_cache.OPTIONS_DIR", cache_dirs[1]):
            write_cache(stocks_dir, key, data, source="ib", ttl=86400)
            result = read_cache(stocks_dir, key)

        assert result == data

    def test_read_miss(self, cache_dirs):
        stocks_dir, _ = cache_dirs
        key = cache_key_stock("MISSING", "2026-01-01", "2026-03-17")

        with patch("utils.price_cache.STOCKS_DIR", stocks_dir), \
             patch("utils.price_cache.OPTIONS_DIR", cache_dirs[1]):
            result = read_cache(stocks_dir, key)

        assert result is None

    def test_expired_ttl_returns_none(self, cache_dirs):
        stocks_dir, _ = cache_dirs
        key = cache_key_stock("SPY", "2026-01-01", "2026-03-17")
        data = {"2026-01-02": 230.5}

        with patch("utils.price_cache.STOCKS_DIR", stocks_dir), \
             patch("utils.price_cache.OPTIONS_DIR", cache_dirs[1]):
            write_cache(stocks_dir, key, data, source="ib", ttl=1)

            # Patch time to be 2 seconds ahead
            with patch("time.time", return_value=time.time() + 5):
                result = read_cache(stocks_dir, key)

        assert result is None

    def test_corrupted_json_returns_none(self, cache_dirs):
        stocks_dir, _ = cache_dirs
        key = cache_key_stock("CORRUPT", "2026-01-01", "2026-03-17")
        from utils.price_cache import _cache_path
        with patch("utils.price_cache.STOCKS_DIR", stocks_dir):
            path = _cache_path(stocks_dir, key)
            path.write_text("{invalid json")
            result = read_cache(stocks_dir, key)

        assert result is None

    @pytest.mark.parametrize(
        "payload",
        [
            [],
            {"fetched_at": "2026-01-01T00:00:00", "ttl_seconds": 60, "data": []},
            {"fetched_at": "2026-01-01T00:00:00", "ttl_seconds": "forever", "data": {}},
            {"fetched_at": "2026-01-01T00:00:00", "ttl_seconds": 60, "data": {"d": "bad"}},
        ],
    )
    def test_valid_json_with_invalid_shape_is_quarantined_cache_miss(
        self, cache_dirs, payload
    ):
        stocks_dir, _ = cache_dirs
        key = cache_key_stock("MALFORMED", "2026-01-01", "2026-03-17")
        from utils.price_cache import _cache_path

        with patch("utils.price_cache.STOCKS_DIR", stocks_dir):
            path = _cache_path(stocks_dir, key)
            path.write_text(json.dumps(payload))
            assert read_cache(stocks_dir, key) is None
            assert not path.exists()
            assert list(stocks_dir.glob(f"{path.name}.invalid-*"))

    def test_option_write_read(self, cache_dirs):
        _, options_dir = cache_dirs
        key = cache_key_option("AAPL260321C00230000", "2026-01-01", "2026-03-17")
        data = {"2026-02-15": 5.50, "2026-02-16": 5.75}

        with patch("utils.price_cache.STOCKS_DIR", cache_dirs[0]), \
             patch("utils.price_cache.OPTIONS_DIR", options_dir):
            write_cache(options_dir, key, data, source="uw", ttl=86400)
            result = read_cache(options_dir, key)

        assert result == data

    def test_no_checksum_in_returned_data(self, cache_dirs):
        stocks_dir, _ = cache_dirs
        key = cache_key_stock("SPY", "2026-01-01", "2026-03-17")
        data = {"2026-01-02": 230.5}

        with patch("utils.price_cache.STOCKS_DIR", stocks_dir), \
             patch("utils.price_cache.OPTIONS_DIR", cache_dirs[1]):
            write_cache(stocks_dir, key, data, source="ib", ttl=86400)
            result = read_cache(stocks_dir, key)

        assert "_checksum" not in (result or {})


class TestMarketHours:
    def test_weekday_during_hours(self):
        """Mock a Tuesday at 10:00 ET — should be market hours."""
        from datetime import datetime as dt
        from zoneinfo import ZoneInfo
        mock_now = dt(2026, 3, 17, 10, 0, 0, tzinfo=ZoneInfo("America/New_York"))
        with patch("utils.price_cache.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.fromisoformat = dt.fromisoformat
            assert is_market_hours() is True

    def test_weekday_after_close(self):
        """Mock a Tuesday at 17:00 ET — market closed."""
        from datetime import datetime as dt
        from zoneinfo import ZoneInfo
        mock_now = dt(2026, 3, 17, 17, 0, 0, tzinfo=ZoneInfo("America/New_York"))
        with patch("utils.price_cache.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.fromisoformat = dt.fromisoformat
            assert is_market_hours() is False

    def test_weekend(self):
        """Mock a Saturday — market closed."""
        from datetime import datetime as dt
        from zoneinfo import ZoneInfo
        # 2026-03-14 is a Saturday
        mock_now = dt(2026, 3, 14, 12, 0, 0, tzinfo=ZoneInfo("America/New_York"))
        with patch("utils.price_cache.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.fromisoformat = dt.fromisoformat
            assert is_market_hours() is False


class TestPruneCache:
    def test_prune_when_under_limit(self, cache_dirs):
        stocks_dir, options_dir = cache_dirs
        # Write 3 files
        for i in range(3):
            (stocks_dir / f"file{i}.json").write_text("{}")

        with patch("utils.price_cache.STOCKS_DIR", stocks_dir), \
             patch("utils.price_cache.OPTIONS_DIR", options_dir):
            deleted = prune_cache(stocks_dir, max_files=10)
        assert deleted == 0

    def test_prune_deletes_oldest(self, cache_dirs):
        stocks_dir, options_dir = cache_dirs
        # Write 5 files with staggered mtimes
        for i in range(5):
            f = stocks_dir / f"file{i}.json"
            f.write_text("{}")

        with patch("utils.price_cache.STOCKS_DIR", stocks_dir), \
             patch("utils.price_cache.OPTIONS_DIR", options_dir):
            deleted = prune_cache(stocks_dir, max_files=3)

        assert deleted == 2
        remaining = list(stocks_dir.glob("*.json"))
        assert len(remaining) == 3

    def test_prune_handles_missing_files(self, cache_dirs):
        """Prune should not crash on race-condition file deletions."""
        stocks_dir, options_dir = cache_dirs
        for i in range(3):
            (stocks_dir / f"file{i}.json").write_text("{}")

        with patch("utils.price_cache.STOCKS_DIR", stocks_dir), \
             patch("utils.price_cache.OPTIONS_DIR", options_dir):
            deleted = prune_cache(stocks_dir, max_files=5)

        assert deleted == 0


class TestQuarantinePrune:
    """R-072: _quarantine_invalid renames to <sha>.json.invalid-<ns>, but
    prune_cache only enumerated suffix == ".json" — quarantined files
    accumulated forever."""

    def test_prune_removes_quarantined_files_past_retention(self, cache_dirs):
        import os

        from utils.price_cache import QUARANTINE_RETENTION_SECONDS

        stocks_dir, options_dir = cache_dirs
        stale = stocks_dir / (("a" * 64) + ".json.invalid-1234")
        stale.write_text("{corrupt")
        stale_mtime = time.time() - QUARANTINE_RETENTION_SECONDS - 60
        os.utime(stale, (stale_mtime, stale_mtime))
        fresh = options_dir / (("b" * 64) + ".json.invalid-5678")
        fresh.write_text("{corrupt")

        with patch("utils.price_cache.STOCKS_DIR", stocks_dir), \
             patch("utils.price_cache.OPTIONS_DIR", options_dir):
            deleted = prune_cache(stocks_dir, max_files=500)

        assert deleted == 1
        assert not stale.exists()
        # A freshly-quarantined file survives its forensic window.
        assert fresh.exists()

    def test_quarantine_sweep_runs_even_under_the_count_cap(self, cache_dirs):
        """The old early-return on len <= max_files must not skip the sweep."""
        import os

        from utils.price_cache import QUARANTINE_RETENTION_SECONDS

        stocks_dir, options_dir = cache_dirs
        (stocks_dir / "live.json").write_text("{}")
        stale = stocks_dir / (("c" * 64) + ".json.invalid-42")
        stale.write_text("{corrupt")
        stale_mtime = time.time() - QUARANTINE_RETENTION_SECONDS - 60
        os.utime(stale, (stale_mtime, stale_mtime))

        with patch("utils.price_cache.STOCKS_DIR", stocks_dir), \
             patch("utils.price_cache.OPTIONS_DIR", options_dir):
            deleted = prune_cache(stocks_dir, max_files=500)

        assert deleted == 1
        assert not stale.exists()
        assert (stocks_dir / "live.json").exists()

    def test_quarantined_files_do_not_count_toward_the_json_cap(self, cache_dirs):
        stocks_dir, options_dir = cache_dirs
        for i in range(3):
            (stocks_dir / f"file{i}.json").write_text("{}")
        (stocks_dir / (("d" * 64) + ".json.invalid-9")).write_text("{corrupt")

        with patch("utils.price_cache.STOCKS_DIR", stocks_dir), \
             patch("utils.price_cache.OPTIONS_DIR", options_dir):
            deleted = prune_cache(stocks_dir, max_files=3)

        # The fresh quarantined file is neither deleted nor allowed to push a
        # live .json entry over the cap.
        assert deleted == 0
        assert len(list(stocks_dir.glob("*.json"))) == 3
