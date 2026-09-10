"""REL-176 (R-487): the CRI scan lock distinguishes contention from a broken
lock path, and a lock-loser's cached answer carries its age."""
from __future__ import annotations

import fcntl
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cri_scan  # noqa: E402


@pytest.fixture()
def held_lock(tmp_path, monkeypatch):
    lock_path = tmp_path / "cri.lock"
    monkeypatch.setenv("RADON_CRI_SCAN_LOCK", str(lock_path))
    holder = open(lock_path, "a+")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    yield lock_path
    holder.close()


class TestBrokenLockPathIsNotContention:
    def test_unwritable_lock_dir_raises_a_named_error(self, tmp_path, monkeypatch):
        ro_dir = tmp_path / "ro"
        ro_dir.mkdir()
        os.chmod(ro_dir, 0o500)
        monkeypatch.setenv("RADON_CRI_SCAN_LOCK", str(ro_dir / "cri.lock"))
        try:
            with pytest.raises(cri_scan.ScanLockError) as exc:
                cri_scan._acquire_scan_lock()
            assert "cri.lock" in str(exc.value)
        finally:
            os.chmod(ro_dir, 0o700)

    def test_contention_still_returns_none(self, held_lock):
        assert cri_scan._acquire_scan_lock() is None


class TestLoserCacheCarriesAge:
    def _cached(self, tmp_path, age_days: float) -> dict:
        scan_time = (
            datetime.now(timezone.utc) - timedelta(days=age_days)
        ).isoformat()
        return {"scan_time": scan_time, "cri": 42.0}

    def test_a_fresh_cache_is_marked_served_from_cache(self, tmp_path):
        payload = cri_scan.loser_cache_payload(self._cached(tmp_path, 0.01))
        assert payload is not None
        assert payload["served_from_cache"] is True
        assert payload["scan_time"]

    def test_a_three_day_old_cache_is_refused(self, tmp_path):
        assert cri_scan.loser_cache_payload(self._cached(tmp_path, 3.5)) is None

    def test_a_cache_with_no_scan_time_is_refused(self, tmp_path):
        assert cri_scan.loser_cache_payload({"cri": 42.0}) is None
