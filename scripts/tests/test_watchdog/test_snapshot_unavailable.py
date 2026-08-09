#!/usr/bin/env python3
"""REL-010 — watchdog must not go mute when Turso is down
(RELIABILITY_AUDIT.md R-021).

A failed service_health snapshot used to return BucketReport(ran=False),
printed "skipped (off-window)", and exited 0 — skipping the units alarm,
the external-probe read, and any page. The on-box alerting pipeline went
silent exactly when the DB was down.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from scripts.watchdog import check as check_mod
from scripts.watchdog import __main__ as main_mod


NOW = datetime(2026, 8, 7, 15, 0, tzinfo=timezone.utc)


class TestSkipReasonPlumbing:
    def test_snapshot_fetch_failure_reports_reason(self, monkeypatch):
        monkeypatch.setattr(
            check_mod, "_fetch_bucket_snapshot",
            MagicMock(side_effect=check_mod.SnapshotUnavailable("hrana 502")),
        )
        report = check_mod.check_bucket(bucket="continuous", now=NOW)
        assert report.ran is False
        assert "snapshot_unavailable" in (report.skip_reason or "")

    def test_off_window_keeps_distinct_reason(self):
        # Sunday 03:00 UTC — intraday window closed.
        off = datetime(2026, 8, 9, 3, 0, tzinfo=timezone.utc)
        report = check_mod.check_bucket(bucket="intraday", now=off)
        assert report.ran is False
        assert "snapshot_unavailable" not in (report.skip_reason or "")


class TestSnapshotUnavailableHandling:
    @pytest.fixture
    def state_file(self, tmp_path, monkeypatch):
        path = tmp_path / "snapshot_failures.json"
        monkeypatch.setattr(main_mod, "_SNAPSHOT_FAILURE_STATE", path)
        return path

    def test_units_and_probe_still_run_on_continuous(self, state_file, monkeypatch):
        unit_outcome = SimpleNamespace(
            service="radon-monitor.unit", status="failed", fired=True,
            severity="P1", message="unit failed",
        )
        units_check = MagicMock(return_value=[unit_outcome])
        probe_check = MagicMock(return_value=SimpleNamespace(
            service="external-probe", status="healthy", fired=False,
            severity="P2", message="",
        ))
        page = MagicMock(return_value=None)
        monkeypatch.setattr("scripts.watchdog.units.check_units", units_check)
        monkeypatch.setattr(
            "scripts.watchdog.external_probe.check_external_probe", probe_check
        )
        monkeypatch.setattr("scripts.watchdog.notify.send_direct_page", page)

        rc = main_mod._handle_snapshot_unavailable(
            bucket="continuous", now=NOW, reason="snapshot_unavailable: hrana 502"
        )
        assert rc == 0
        units_check.assert_called_once()
        probe_check.assert_called_once()
        # The failed unit was paged directly (DB-free path).
        assert any(
            "radon-monitor.unit" in str(c) for c in page.call_args_list
        )

    def test_three_consecutive_failures_page_p1(self, state_file, monkeypatch):
        page = MagicMock(return_value=None)
        monkeypatch.setattr("scripts.watchdog.notify.send_direct_page", page)
        monkeypatch.setattr("scripts.watchdog.units.check_units", MagicMock(return_value=[]))
        monkeypatch.setattr(
            "scripts.watchdog.external_probe.check_external_probe",
            MagicMock(return_value=SimpleNamespace(
                service="external-probe", status="healthy", fired=False,
                severity="P2", message="")),
        )

        for _ in range(2):
            main_mod._handle_snapshot_unavailable(
                bucket="daily", now=NOW, reason="snapshot_unavailable: hrana 502"
            )
        blind_pages = [c for c in page.call_args_list if "blind" in str(c).lower()]
        assert blind_pages == []  # below threshold — no page yet

        main_mod._handle_snapshot_unavailable(
            bucket="daily", now=NOW, reason="snapshot_unavailable: hrana 502"
        )
        blind_pages = [c for c in page.call_args_list if "blind" in str(c).lower()]
        assert len(blind_pages) == 1

        # Fourth failure inside the page cooldown — no second page.
        main_mod._handle_snapshot_unavailable(
            bucket="daily", now=NOW, reason="snapshot_unavailable: hrana 502"
        )
        blind_pages = [c for c in page.call_args_list if "blind" in str(c).lower()]
        assert len(blind_pages) == 1

    def test_successful_cycle_resets_counter(self, state_file):
        state_file.write_text(json.dumps({"consecutive": 2, "last_paged_at": None}))
        main_mod._reset_snapshot_failure_counter()
        assert json.loads(state_file.read_text())["consecutive"] == 0
