"""REL-215 (R-587): a future-dated disk NAV key must not beat Turso forever.

Freshness selection is `max(date-string)`; the disk age budget rejects OLD
caches but a corrupted future date (2027-01-01) passed trivially, pinning the
published TWR to the stale disk series until manually cleared.
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import perf_twr_builder as twr  # noqa: E402


def _write_disk_cache(monkeypatch, tmp_path, nav: dict[str, float]):
    path = tmp_path / "nav_cache.json"
    path.write_text(json.dumps(nav))
    monkeypatch.setattr(twr, "_disk_nav_candidates", lambda: [path])
    return path


class TestFutureDatedKeysAreRejected:
    def test_a_future_key_is_dropped_from_the_disk_series(self, monkeypatch, tmp_path):
        today = date.today()
        nav = {
            (today - timedelta(days=2)).isoformat(): 100_000.0,
            (today - timedelta(days=1)).isoformat(): 101_000.0,
            "2099-01-01": 999_999.0,
        }
        _write_disk_cache(monkeypatch, tmp_path, nav)
        loaded = twr.load_nav_from_disk()
        assert loaded is not None
        assert "2099-01-01" not in loaded

    def test_a_future_only_cache_loses_to_turso(self, monkeypatch, tmp_path):
        """The AC: a disk cache whose newest key is 2099 must not outrank a
        fresher-in-reality Turso series."""
        today = date.today()
        stale_recent = (today - timedelta(days=40)).isoformat()
        nav = {stale_recent: 90_000.0, "2099-01-01": 999_999.0}
        _write_disk_cache(monkeypatch, tmp_path, nav)
        loaded = twr.load_nav_from_disk()
        # With the corrupt key dropped, the cache is a 40-day-old singleton and
        # fails the existing age/size budgets outright.
        assert loaded is None
