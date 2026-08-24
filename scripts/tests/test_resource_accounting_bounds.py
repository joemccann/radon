"""REL-068 tranche E — R-172, R-173, R-174, R-175.

Four resource accountants that stop accounting exactly when the resource
runs short: a UW quota tally that resets to zero on a truncated state file,
a disk cache that bounds file COUNT while caching hundreds of KB each, a
prune that runs inside the parallel path its own docstring forbids, and an
unbounded per-post image fan-out whose width comes from third-party markup.
"""
from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 23, 18, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# R-172 — a corrupt tally is not a zero tally
# --------------------------------------------------------------------------
class TestUwBudgetFailsClosed:
    def test_a_truncated_state_file_does_not_reset_the_day(self, tmp_path):
        from utils import uw_budget as mod

        state = tmp_path / "uw_budget.json"
        state.write_text('{"date": "2026-08-23", "count": 39')  # torn write
        assert mod.used(path=state, now=NOW) >= mod.UNIVERSE_BLOCK_AT, (
            "an unreadable tally silently reported 0 used, which re-opens the "
            "whole daily budget to a scanner that has already spent it"
        )

    def test_a_truncated_state_file_blocks_the_universe_scan(self, tmp_path):
        from utils import uw_budget as mod

        state = tmp_path / "uw_budget.json"
        state.write_text("{oops")
        assert mod.should_block_universe_scan(path=state, now=NOW) is True

    def test_an_absent_file_is_still_a_clean_zero(self, tmp_path):
        from utils import uw_budget as mod

        assert mod.used(path=tmp_path / "nope.json", now=NOW) == 0
        assert mod.should_block_universe_scan(path=tmp_path / "nope.json", now=NOW) is False

    def test_a_readable_yesterday_still_resets(self, tmp_path):
        from utils import uw_budget as mod

        state = tmp_path / "uw_budget.json"
        state.write_text(json.dumps({"date": "2026-08-01", "count": 500}))
        assert mod.used(path=state, now=NOW) == 0

    def test_the_snapshot_says_the_tally_is_unreadable(self, tmp_path):
        from utils import uw_budget as mod

        state = tmp_path / "uw_budget.json"
        state.write_text("{oops")
        snap = mod.usage_snapshot(path=state, now=NOW)
        assert snap.get("state_unreadable") is True


# --------------------------------------------------------------------------
# R-173 — the cache cap must bound BYTES, not just file count
# --------------------------------------------------------------------------
class TestUwCacheBoundsBytes:
    def test_a_byte_ceiling_exists(self):
        from utils import uw_cache as mod

        assert getattr(mod, "MAX_DISK_BYTES", 0) > 0

    def test_a_few_huge_files_are_evicted_below_the_count_cap(self, tmp_path, monkeypatch):
        from utils import uw_cache as mod

        monkeypatch.setattr(mod, "CACHE_DIR", tmp_path)
        monkeypatch.setattr(mod, "MAX_DISK_BYTES", 3_000)
        blob = "x" * 1_000
        for i in range(10):
            path = tmp_path / f"k{i}.json"
            path.write_text(blob)
        removed = mod.prune_disk_cache(now=None, max_files=10_000)
        assert removed > 0, "10 KB of payload survived a 3 KB ceiling"
        total = sum(p.stat().st_size for p in tmp_path.glob("*.json"))
        assert total <= 3_000

    def test_it_evicts_oldest_first(self, tmp_path, monkeypatch):
        import os

        from utils import uw_cache as mod

        monkeypatch.setattr(mod, "CACHE_DIR", tmp_path)
        monkeypatch.setattr(mod, "MAX_DISK_BYTES", 2_000)
        import time

        base = time.time() - 60  # inside the TTL sweep's window
        for i in range(4):
            path = tmp_path / f"k{i}.json"
            path.write_text("x" * 1_000)
            os.utime(path, (base + i, base + i))
        mod.prune_disk_cache(now=None, max_files=10_000)
        survivors = sorted(p.name for p in tmp_path.glob("*.json"))
        assert "k0.json" not in survivors
        assert "k3.json" in survivors

    def test_a_cache_under_both_ceilings_is_untouched(self, tmp_path, monkeypatch):
        from utils import uw_cache as mod

        monkeypatch.setattr(mod, "CACHE_DIR", tmp_path)
        monkeypatch.setattr(mod, "MAX_DISK_BYTES", 1_000_000)
        (tmp_path / "k.json").write_text("x" * 100)
        assert mod.prune_disk_cache(now=None, max_files=10_000) == 0


# --------------------------------------------------------------------------
# R-174 — prune_cache must not run concurrently from the parallel path
# --------------------------------------------------------------------------
class TestPriceCachePruneIsSerialized:
    def test_concurrent_writes_run_at_most_one_prune_at_a_time(self, tmp_path, monkeypatch):
        from utils import price_cache as mod

        monkeypatch.setattr(mod, "STOCKS_DIR", tmp_path / "stocks")
        monkeypatch.setattr(mod, "OPTIONS_DIR", tmp_path / "options")
        (tmp_path / "stocks").mkdir()
        (tmp_path / "options").mkdir()

        concurrent = {"now": 0, "max": 0}
        gate = threading.Lock()
        real_prune = mod.prune_cache

        def spy(*a, **k):
            with gate:
                concurrent["now"] += 1
                concurrent["max"] = max(concurrent["max"], concurrent["now"])
            try:
                return real_prune(*a, **k)
            finally:
                with gate:
                    concurrent["now"] -= 1

        monkeypatch.setattr(mod, "prune_cache", spy)
        monkeypatch.setattr(mod, "PRUNE_MIN_INTERVAL_SECONDS", 0)

        threads = [
            threading.Thread(
                target=mod.write_cache,
                args=(mod.STOCKS_DIR, f"K{i}", {"2026-08-21": 1.0}, "ib", 900),
            )
            for i in range(12)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert concurrent["max"] <= 1, (
            "prune_cache documents 'call ONCE after all parallel writes "
            "complete' and write_cache calls it from inside that path"
        )

    def test_the_prune_is_rate_limited_between_writes(self, tmp_path, monkeypatch):
        from utils import price_cache as mod

        monkeypatch.setattr(mod, "STOCKS_DIR", tmp_path / "stocks")
        monkeypatch.setattr(mod, "OPTIONS_DIR", tmp_path / "options")
        (tmp_path / "stocks").mkdir()
        (tmp_path / "options").mkdir()

        calls = {"n": 0}
        monkeypatch.setattr(mod, "prune_cache", lambda *a, **k: calls.__setitem__("n", calls["n"] + 1))
        assert mod.PRUNE_MIN_INTERVAL_SECONDS > 0
        for i in range(20):
            mod.write_cache(mod.STOCKS_DIR, f"K{i}", {"2026-08-21": 1.0}, "ib", 900)
        assert calls["n"] <= 2, f"{calls['n']} full sweeps for 20 writes"

    def test_the_cap_is_still_enforced_eventually(self, tmp_path, monkeypatch):
        from utils import price_cache as mod

        monkeypatch.setattr(mod, "STOCKS_DIR", tmp_path / "stocks")
        monkeypatch.setattr(mod, "OPTIONS_DIR", tmp_path / "options")
        (tmp_path / "stocks").mkdir()
        (tmp_path / "options").mkdir()
        for i in range(20):
            (tmp_path / "stocks" / f"old{i}.json").write_text("{}")
        assert mod.prune_cache(mod.STOCKS_DIR, max_files=5) >= 15


# --------------------------------------------------------------------------
# R-175 — attacker-influenced fan-out width needs a pool
# --------------------------------------------------------------------------
class TestImageDownloadConcurrency:
    def test_the_module_declares_a_concurrency_cap(self):
        src = (REPO / "scripts" / "newsfeed" / "media.js").read_text()
        assert "IMAGE_DOWNLOAD_CONCURRENCY" in src, (
            "download() fans out one concurrent request per <img> and "
            "post.rawImages comes verbatim from third-party article markup"
        )

    def test_the_fan_out_is_not_a_bare_promise_all_over_every_url(self):
        src = (REPO / "scripts" / "newsfeed" / "media.js").read_text()
        body = src.split("async function download(postId, urls)")[1].split("\n  }")[0]
        assert "urls.map(async" not in body, "still one in-flight request per image"

    def test_there_is_a_per_post_image_cap(self):
        src = (REPO / "scripts" / "newsfeed" / "media.js").read_text()
        assert "MAX_IMAGES_PER_POST" in src

    def test_the_url_cache_is_bounded(self):
        src = (REPO / "scripts" / "newsfeed" / "media.js").read_text()
        assert "MAX_URL_CACHE_ENTRIES" in src, (
            "the absoluteUrl -> publicPath map is never evicted in a "
            "long-lived 120 s-cycle process"
        )
