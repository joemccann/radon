"""Tests for scripts/host_metrics_sampler.py (DUR-12).

Pure parsers are exercised from fixture text (no /proc dependency, so the
suite runs on macOS laptops). The Turso write path is mocked at the module
seam (`hrana_execute` / `write_service_health_http`) — a test must never
reach production Turso (feedback_test_pollution_to_production; the hrana
transport's own pytest guard is belt-and-braces behind these mocks).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

import host_metrics_sampler as sampler
from db.hrana_http import HranaHttpError

PROC_STAT_FIRST = "cpu  100 0 100 700 100 0 0 0 0 0\ncpu0 50 0 50 350 50 0 0 0 0 0\n"
PROC_STAT_SECOND = "cpu  300 0 300 1300 100 0 0 0 0 0\ncpu0 150 0 150 650 50 0 0 0 0 0\n"

PROC_MEMINFO = (
    "MemTotal:        7892000 kB\n"
    "MemFree:          500000 kB\n"
    "MemAvailable:    3946000 kB\n"
    "Buffers:          100000 kB\n"
    "SwapTotal:       1048576 kB\n"
    "SwapFree:         786432 kB\n"
)

PROC_LOADAVG = "0.42 0.36 0.30 1/123 4567\n"

SYSTEMCTL_SHOW_BLOB = (
    "Id=radon-api.service\n"
    "ActiveState=active\n"
    "NRestarts=2\n"
    "\n"
    "Id=radon-relay.service\n"
    "ActiveState=failed\n"
    "NRestarts=7\n"
)


class TestProcParsing:
    def test_parse_proc_stat_cpu_returns_idle_and_total(self):
        idle, total = sampler.parse_proc_stat_cpu(PROC_STAT_FIRST)
        # idle = idle(700) + iowait(100); total = sum of the 8 jiffy fields.
        assert idle == 800
        assert total == 100 + 0 + 100 + 700 + 100 + 0 + 0 + 0

    def test_cpu_pct_from_samples_uses_the_delta(self):
        first = sampler.parse_proc_stat_cpu(PROC_STAT_FIRST)
        second = sampler.parse_proc_stat_cpu(PROC_STAT_SECOND)
        # totals 1000 -> 2000 (delta 1000); idle 800 -> 1400 (delta 600)
        # busy = 400/1000 = 40%
        assert sampler.cpu_pct_from_samples(first, second) == pytest.approx(40.0)

    def test_cpu_pct_from_samples_zero_delta_is_none(self):
        snap = sampler.parse_proc_stat_cpu(PROC_STAT_FIRST)
        assert sampler.cpu_pct_from_samples(snap, snap) is None

    def test_parse_meminfo_extracts_kb_fields(self):
        info = sampler.parse_meminfo(PROC_MEMINFO)
        assert info["MemTotal"] == 7892000
        assert info["MemAvailable"] == 3946000
        assert info["SwapTotal"] == 1048576
        assert info["SwapFree"] == 786432

    def test_mem_metrics_in_mb(self):
        used_mb, avail_mb, swap_used_mb = sampler.mem_metrics(
            sampler.parse_meminfo(PROC_MEMINFO)
        )
        # Values are rounded to 0.1 MB — sub-MB precision is noise here.
        assert used_mb == pytest.approx((7892000 - 3946000) / 1024, abs=0.05)
        assert avail_mb == pytest.approx(3946000 / 1024, abs=0.05)
        assert swap_used_mb == pytest.approx((1048576 - 786432) / 1024, abs=0.05)

    def test_parse_loadavg_first_field(self):
        assert sampler.parse_loadavg(PROC_LOADAVG) == pytest.approx(0.42)


class TestUnitsParsing:
    def test_parse_units_blob_yields_one_record_per_unit(self):
        units = sampler.parse_units_blob(SYSTEMCTL_SHOW_BLOB)
        assert units == [
            {"unit": "radon-api.service", "active_state": "active", "n_restarts": 2},
            {"unit": "radon-relay.service", "active_state": "failed", "n_restarts": 7},
        ]

    def test_parse_units_blob_tolerates_empty_output(self):
        assert sampler.parse_units_blob("") == []


class TestLoopLag:
    def test_parse_loop_lag_reads_the_health_lite_field(self):
        assert sampler.parse_loop_lag({"status": "ok", "loop_lag_ms": 0.123}) == 0.123

    def test_parse_loop_lag_missing_or_garbage_is_none(self):
        assert sampler.parse_loop_lag({"status": "ok"}) is None
        assert sampler.parse_loop_lag({"loop_lag_ms": "fast"}) is None
        assert sampler.parse_loop_lag(None) is None


class TestBoundedWrites:
    def test_write_row_inserts_over_the_bounded_hrana_path(self, monkeypatch):
        execute = MagicMock()
        monkeypatch.setattr(sampler, "hrana_execute", execute)
        row = {
            "taken_at": "2026-06-12T07:00:17Z",
            "cpu_pct": 40.0,
            "mem_used_mb": 3853.5,
            "mem_avail_mb": 3853.5,
            "load1": 0.42,
            "swap_used_mb": 0.0,
            "loop_lag_ms": 0.2,
            "units_json": "[]",
        }
        assert sampler.write_row(row) is True
        execute.assert_called_once()
        sql, args = execute.call_args.args
        assert "INSERT INTO host_metrics" in sql
        assert args == (
            "2026-06-12T07:00:17Z", 40.0, 3853.5, 3853.5, 0.42, 0.0, 0.2, "[]",
        )

    def test_write_row_failure_falls_back_to_jsonl(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            sampler, "hrana_execute", MagicMock(side_effect=HranaHttpError("timeout"))
        )
        fallback = tmp_path / "host_metrics_fallback.jsonl"
        monkeypatch.setattr(sampler, "FALLBACK_PATH", fallback)
        row = {"taken_at": "2026-06-12T07:00:17Z", "cpu_pct": 1.0}
        assert sampler.write_row(row) is False
        lines = fallback.read_text().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["taken_at"] == "2026-06-12T07:00:17Z"

    def test_fallback_file_is_capped(self, monkeypatch, tmp_path):
        fallback = tmp_path / "host_metrics_fallback.jsonl"
        monkeypatch.setattr(sampler, "FALLBACK_PATH", fallback)
        monkeypatch.setattr(sampler, "FALLBACK_MAX_LINES", 5)
        for i in range(9):
            sampler.append_fallback({"taken_at": f"t{i}"})
        lines = fallback.read_text().splitlines()
        assert len(lines) == 5
        # Oldest rows are dropped; the newest survive.
        assert json.loads(lines[-1])["taken_at"] == "t8"
        assert json.loads(lines[0])["taken_at"] == "t4"


class TestPrune:
    def test_should_prune_only_at_the_top_of_the_hour(self):
        assert sampler.should_prune(datetime(2026, 6, 12, 7, 0, 17, tzinfo=timezone.utc))
        assert not sampler.should_prune(datetime(2026, 6, 12, 7, 1, 17, tzinfo=timezone.utc))
        assert not sampler.should_prune(datetime(2026, 6, 12, 7, 59, 17, tzinfo=timezone.utc))

    def test_prune_deletes_rows_older_than_fourteen_days(self, monkeypatch):
        execute = MagicMock()
        monkeypatch.setattr(sampler, "hrana_execute", execute)
        now = datetime(2026, 6, 15, 7, 0, 0, tzinfo=timezone.utc)
        sampler.prune_old_rows(now)
        sql, args = execute.call_args.args
        assert "DELETE FROM host_metrics" in sql
        assert args == ("2026-06-01T07:00:00Z",)
        assert sampler.RETENTION_DAYS == 14

    def test_prune_failure_does_not_raise(self, monkeypatch):
        monkeypatch.setattr(
            sampler, "hrana_execute", MagicMock(side_effect=HranaHttpError("down"))
        )
        sampler.prune_old_rows(datetime(2026, 6, 15, 7, 0, tzinfo=timezone.utc))


class TestHeartbeat:
    def test_heartbeat_uses_the_bounded_service_health_path(self, monkeypatch):
        write = MagicMock()
        monkeypatch.setattr(sampler, "write_service_health_http", write)
        sampler.heartbeat("ok", started_at="s", finished_at="f")
        write.assert_called_once_with(
            "host-metrics", "ok", started_at="s", finished_at="f", error=None
        )

    def test_heartbeat_is_best_effort(self, monkeypatch):
        monkeypatch.setattr(
            sampler,
            "write_service_health_http",
            MagicMock(side_effect=HranaHttpError("down")),
        )
        sampler.heartbeat("error", started_at="s", finished_at="f",
                          error={"error": "turso_write_failed"})
