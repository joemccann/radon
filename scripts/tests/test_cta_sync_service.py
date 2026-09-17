"""Tests for CTA sync scheduling, catch-up, and fetch-timeout retry."""

from __future__ import annotations

import contextlib
import json
import subprocess
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from utils.cta_sync import CTA_SYNC_ET_SLOTS, latest_closed_trading_day
from utils.cta_sync_health import retry_backoffs_for_error
from utils.launchd_schedule import convert_et_calendar_entries, expand_intraday_slots

import cta_sync_service as cta_sync_svc

VALID_CTA_PAYLOAD = {
    "date": "2026-09-17",
    "tables": {
        "main": [{"underlying": "SPX"}],
        "index": [],
        "commodity": [],
        "currency": [],
    },
}

_FETCH_CMD = [
    "/home/radon/radon/.venv/bin/python3.13",
    "/home/radon/radon/scripts/fetch_menthorq_cta.py",
    "--json",
    "--date",
    "2026-09-17",
]


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _isolate_cta_sync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    statuses: list[dict] = []
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr(cta_sync_svc, "LOCK_DIR", tmp_path / "lock")
    monkeypatch.setattr(cta_sync_svc, "ARTIFACT_DIR", tmp_path / "artifacts")
    monkeypatch.setattr(cta_sync_svc, "cache_path", lambda date_str: cache_dir / f"cta_{date_str}.json")
    monkeypatch.setattr(cta_sync_svc, "load_cta_sync_status", lambda: None)
    monkeypatch.setattr(cta_sync_svc, "latest_available_cta_date", lambda: "2026-09-16")
    monkeypatch.setattr(cta_sync_svc, "write_cta_sync_status", lambda payload: statuses.append(dict(payload)))
    monkeypatch.setattr(cta_sync_svc, "write_cta_sync_history", lambda payload: None)
    monkeypatch.setattr(cta_sync_svc, "_dual_write_cta_to_db", lambda *a, **k: None)

    @contextlib.contextmanager
    def _lock(**_kwargs):
        yield

    monkeypatch.setattr(cta_sync_svc, "cta_sync_lock", _lock)
    return statuses


class TestLatestClosedTradingDay:
    def test_after_close_uses_same_trading_day(self):
        now = datetime(2026, 3, 12, 16, 5)
        assert latest_closed_trading_day(now) == "2026-03-12"

    def test_before_close_uses_previous_trading_day(self):
        now = datetime(2026, 3, 12, 15, 59)
        assert latest_closed_trading_day(now) == "2026-03-11"

    def test_weekend_uses_prior_friday(self):
        now = datetime(2026, 3, 14, 12, 0)
        assert latest_closed_trading_day(now) == "2026-03-13"


class TestLaunchdCalendarEntries:
    def test_data_refresh_slots_convert_from_et_to_pacific(self):
        slots = expand_intraday_slots((9, 30), (10, 0), 15)
        entries = convert_et_calendar_entries(
            [(1, hour, minute) for hour, minute in slots],
            local_tz="America/Los_Angeles",
            reference_date=date(2026, 3, 9),
        )

        assert entries == [
            {"Weekday": 1, "Hour": 6, "Minute": 30},
            {"Weekday": 1, "Hour": 6, "Minute": 45},
            {"Weekday": 1, "Hour": 7, "Minute": 0},
        ]

    def test_cta_slots_only_include_two_post_close_runs(self):
        entries = convert_et_calendar_entries(
            [(1, hour, minute) for hour, minute in CTA_SYNC_ET_SLOTS],
            local_tz="America/Los_Angeles",
            reference_date=date(2026, 3, 9),
        )

        assert entries == [
            {"Weekday": 1, "Hour": 13, "Minute": 15},
            {"Weekday": 1, "Hour": 14, "Minute": 0},
        ]


class TestFetchTimeoutEnvelope:
    """2026-09-17 20:18Z: hung Playwright raised TimeoutExpired uncaught.

    Page 1e8d20a132ac2b56c844757d031309ef. Unit TimeoutStartSec=1800 covers
    a cold 8-12 min fetch; subprocess.run(timeout=300) killed it first and
    skipped the retry loop, leaving cta-sync-latest.json state=syncing.
    """

    def test_service_timeout_covers_retry_envelope(self):
        fetch_timeout = cta_sync_svc.FETCH_TIMEOUT_S
        backoffs = retry_backoffs_for_error("timeout")
        envelope = fetch_timeout * len(backoffs) + sum(backoffs[1:])
        unit_timeout = 1800
        assert fetch_timeout >= 720
        assert envelope + 60 <= unit_timeout

    def test_subprocess_run_uses_named_fetch_timeout(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        statuses = _isolate_cta_sync(tmp_path, monkeypatch)
        timeouts: list[int] = []

        def fake_run(*_args, **kwargs):
            timeouts.append(kwargs.get("timeout"))
            return _completed(0, stdout=json.dumps(VALID_CTA_PAYLOAD))

        monkeypatch.setattr(cta_sync_svc.subprocess, "run", fake_run)
        exit_code = cta_sync_svc.run_cta_sync(
            target_date="2026-09-17",
            source="launchd",
            sleep_fn=lambda _s: None,
        )
        assert exit_code == 0
        assert timeouts == [cta_sync_svc.FETCH_TIMEOUT_S]
        assert statuses[-1]["state"] == "healthy"

    def test_timeout_expired_retries_then_succeeds(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        statuses = _isolate_cta_sync(tmp_path, monkeypatch)
        sleeps: list[int] = []
        calls = {"n": 0}

        def fake_run(*_args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise subprocess.TimeoutExpired(cmd=_FETCH_CMD, timeout=kwargs.get("timeout", 300))
            return _completed(0, stdout=json.dumps(VALID_CTA_PAYLOAD))

        monkeypatch.setattr(cta_sync_svc.subprocess, "run", fake_run)
        exit_code = cta_sync_svc.run_cta_sync(
            target_date="2026-09-17",
            source="launchd",
            sleep_fn=sleeps.append,
        )
        assert exit_code == 0
        assert calls["n"] == 2
        assert sleeps == [120]
        assert statuses[-1]["state"] == "healthy"
        assert statuses[-1]["attempt_count"] == 2

    def test_timeout_expired_with_unrelated_stderr_still_retries(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        statuses = _isolate_cta_sync(tmp_path, monkeypatch)
        calls = {"n": 0}

        def fake_run(*_args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise subprocess.TimeoutExpired(
                    cmd=_FETCH_CMD,
                    timeout=kwargs.get("timeout", 300),
                    stderr="chrome still launching",
                )
            return _completed(0, stdout=json.dumps(VALID_CTA_PAYLOAD))

        monkeypatch.setattr(cta_sync_svc.subprocess, "run", fake_run)
        exit_code = cta_sync_svc.run_cta_sync(
            target_date="2026-09-17",
            source="launchd",
            sleep_fn=lambda _s: None,
        )
        assert exit_code == 0
        assert calls["n"] == 2
        assert statuses[-1]["state"] == "healthy"

    def test_persistent_timeout_expired_writes_degraded_not_traceback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        statuses = _isolate_cta_sync(tmp_path, monkeypatch)
        calls = {"n": 0}

        def fake_run(*_args, **kwargs):
            calls["n"] += 1
            raise subprocess.TimeoutExpired(cmd=_FETCH_CMD, timeout=kwargs.get("timeout", 300))

        monkeypatch.setattr(cta_sync_svc.subprocess, "run", fake_run)
        exit_code = cta_sync_svc.run_cta_sync(
            target_date="2026-09-17",
            source="launchd",
            sleep_fn=lambda _s: None,
        )
        assert exit_code == 1
        assert calls["n"] == 2
        final = statuses[-1]
        assert final["state"] == "degraded"
        assert final["last_error"]["type"] == "timeout"
        assert final["last_attempt_finished_at"]
        assert "syncing" != final["state"]
