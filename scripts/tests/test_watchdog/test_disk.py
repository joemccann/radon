"""R-069: root-filesystem usage alarm — nothing watched the disk, so
data/uw_http_cache/ (or any other unbounded writer) could fill the VPS
root fs silently until every writer on the box started failing at once.
"""
from __future__ import annotations

import collections
import inspect
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from watchdog import disk

_Usage = collections.namedtuple("usage", "total used free")
NOW = datetime(2026, 8, 16, 12, 0, 0, tzinfo=timezone.utc)


def _with_usage(monkeypatch: pytest.MonkeyPatch, *, total: int, used: int) -> None:
    monkeypatch.setattr(
        disk.shutil,
        "disk_usage",
        MagicMock(return_value=_Usage(total, used, total - used)),
    )


class TestCheckDisk:
    def test_below_warn_threshold_is_healthy(self, monkeypatch):
        _with_usage(monkeypatch, total=100, used=50)
        outcome = disk.check_disk(now=NOW)
        assert outcome.service == disk.SERVICE
        assert outcome.status == "healthy"
        assert outcome.fired is False
        assert outcome.severity is None

    def test_warn_threshold_fires_p2(self, monkeypatch):
        _with_usage(monkeypatch, total=100, used=90)
        outcome = disk.check_disk(now=NOW)
        assert outcome.fired is True
        assert outcome.severity == "P2"
        assert "90" in outcome.message

    def test_critical_threshold_fires_p1(self, monkeypatch):
        _with_usage(monkeypatch, total=100, used=96)
        outcome = disk.check_disk(now=NOW)
        assert outcome.fired is True
        assert outcome.severity == "P1"

    def test_unreadable_usage_never_fires(self, monkeypatch):
        monkeypatch.setattr(
            disk.shutil, "disk_usage", MagicMock(side_effect=OSError("statvfs"))
        )
        outcome = disk.check_disk(now=NOW)
        assert outcome.fired is False
        assert outcome.status == "healthy"

    def test_zero_total_never_fires(self, monkeypatch):
        _with_usage(monkeypatch, total=0, used=0)
        outcome = disk.check_disk(now=NOW)
        assert outcome.fired is False

    def test_thresholds_are_ordered(self):
        assert 0 < disk.DISK_P2_PCT < disk.DISK_P1_PCT <= 100


class TestWiring:
    """The check is DB-free, so it must ride the continuous bucket on BOTH
    paths: the normal cycle and the snapshot-unavailable (DB down) cycle —
    a full disk is one plausible cause of the DB-adjacent outage itself."""

    def test_continuous_bucket_runs_the_disk_check(self):
        from watchdog import __main__ as cli

        source = inspect.getsource(cli._cmd_bucket)
        assert "check_disk" in source

    def test_snapshot_unavailable_path_runs_the_disk_check(self):
        from watchdog import __main__ as cli

        source = inspect.getsource(cli._handle_snapshot_unavailable)
        assert "check_disk" in source
