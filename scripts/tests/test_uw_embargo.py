"""Shared UW daily-cap circuit breaker.

The same sidecar logic was copy-pasted into fetch_skew.py and then again into
fetch_oi_changes.py (2026-08-14). These pins describe the ONE implementation
both now delegate to; the per-writer tests still cover their own wiring.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from clients.uw_client import UWRateLimitError
from utils.uw_embargo import UwEmbargo, is_daily_quota, retry_at

DAILY = UWRateLimitError("You have hit your daily request limit of 40000 requests.")
BURST = UWRateLimitError("429 Too Many Requests")


def _embargo(tmp_path: Path, name: str = "probe") -> UwEmbargo:
    return UwEmbargo(name, lambda: tmp_path / f"{name}_uw_embargo.json")


class TestQuotaClassification:
    def test_daily_cap_is_recognised_case_insensitively(self):
        assert is_daily_quota(DAILY) is True
        assert is_daily_quota(UWRateLimitError("DAILY REQUEST LIMIT reached")) is True

    def test_a_burst_429_is_not_the_daily_cap(self):
        assert is_daily_quota(BURST) is False

    def test_an_unrelated_exception_is_never_the_daily_cap(self):
        assert is_daily_quota(ValueError("daily request limit")) is False


class TestRetryAt:
    def test_daily_cap_waits_for_the_next_2000_et_reset(self):
        # 14:00 ET — the reset is later the same day.
        now = datetime(2026, 8, 14, 18, 0, tzinfo=timezone.utc)
        assert retry_at(DAILY, now) == "2026-08-15T00:00:00Z"

    def test_after_the_reset_hour_it_rolls_to_tomorrow(self):
        # 21:00 ET, already past the 20:00 ET reset.
        now = datetime(2026, 8, 15, 1, 0, tzinfo=timezone.utc)
        assert retry_at(DAILY, now) == "2026-08-16T00:00:00Z"

    def test_a_burst_429_gets_five_minutes_not_a_whole_day(self):
        now = datetime(2026, 8, 14, 18, 0, tzinfo=timezone.utc)
        assert retry_at(BURST, now) == "2026-08-14T18:05:00Z"

    def test_reset_tracks_et_not_a_fixed_utc_offset(self):
        """EST vs EDT: the same 20:00 ET wall clock is a different UTC hour."""
        winter = retry_at(DAILY, datetime(2026, 1, 14, 18, 0, tzinfo=timezone.utc))
        summer = retry_at(DAILY, datetime(2026, 8, 14, 18, 0, tzinfo=timezone.utc))
        assert winter == "2026-01-15T01:00:00Z"
        assert summer == "2026-08-15T00:00:00Z"


class TestSidecar:
    def test_arm_then_active_until_reports_the_deadline(self, tmp_path):
        e = _embargo(tmp_path)
        e.arm("2026-08-15T00:00:00Z")
        now = datetime(2026, 8, 14, 22, 0, tzinfo=timezone.utc)
        assert e.active_until(now) == "2026-08-15T00:00:00Z"

    def test_no_sidecar_means_no_embargo(self, tmp_path):
        assert _embargo(tmp_path).active_until(datetime.now(timezone.utc)) is None

    def test_active_until_consumes_a_lapsed_embargo(self, tmp_path):
        e = _embargo(tmp_path)
        e.arm("2026-08-15T00:00:00Z")
        past_deadline = datetime(2026, 8, 15, 0, 0, tzinfo=timezone.utc)
        assert e.active_until(past_deadline) is None
        assert not e.path().exists(), "a lapsed embargo must clear its sidecar"

    def test_deadline_is_read_only(self, tmp_path):
        """Derived indicators peek at a parent's embargo; only the owner clears."""
        e = _embargo(tmp_path)
        e.arm("2026-08-15T00:00:00Z")
        assert e.deadline(datetime(2026, 8, 15, 0, 0, tzinfo=timezone.utc)) is None
        assert e.path().exists(), "deadline() must not consume the sidecar"

    def test_deadline_returns_an_aware_datetime_while_live(self, tmp_path):
        e = _embargo(tmp_path)
        e.arm("2026-08-15T00:00:00Z")
        got = e.deadline(datetime(2026, 8, 14, 22, 0, tzinfo=timezone.utc))
        assert got == datetime(2026, 8, 15, 0, 0, tzinfo=timezone.utc)

    @pytest.mark.parametrize("payload", ["", "{}", "not json", '{"next_attempt_at": null}'])
    def test_unreadable_sidecar_is_no_embargo_never_a_crash(self, tmp_path, payload):
        e = _embargo(tmp_path)
        e.path().parent.mkdir(parents=True, exist_ok=True)
        e.path().write_text(payload)
        assert e.active_until(datetime.now(timezone.utc)) is None

    def test_naive_timestamp_is_read_as_utc(self, tmp_path):
        e = _embargo(tmp_path)
        e.path().parent.mkdir(parents=True, exist_ok=True)
        e.path().write_text(json.dumps({"next_attempt_at": "2026-08-15T00:00:00"}))
        assert e.deadline(datetime(2026, 8, 14, 22, 0, tzinfo=timezone.utc)) is not None

    def test_clear_is_idempotent_on_a_missing_sidecar(self, tmp_path):
        _embargo(tmp_path).clear()  # must not raise

    def test_path_is_resolved_per_call_not_captured(self, tmp_path):
        """Writers derive the sidecar from a module constant tests relocate."""
        target = {"dir": tmp_path / "a"}
        e = UwEmbargo("probe", lambda: target["dir"] / "probe_uw_embargo.json")
        e.arm("2026-08-15T00:00:00Z")
        target["dir"] = tmp_path / "b"
        now = datetime(2026, 8, 14, 22, 0, tzinfo=timezone.utc)
        assert e.active_until(now) is None, "must follow the relocated path"


class TestErrorHeartbeat:
    def test_records_the_writers_own_service_with_next_attempt(self, tmp_path, monkeypatch):
        calls = []

        class FakeWriter:
            def ensure_no_replica_for_writers(self):
                return None

            def record_service_health(self, service, state, finished_at=None, error=None):
                calls.append((service, state, error))

        import utils.uw_embargo as mod

        monkeypatch.setattr(mod, "_load_writer", lambda: FakeWriter())
        e = _embargo(tmp_path, "oi-changes")
        e.record_error(
            DAILY,
            now=datetime(2026, 8, 14, 22, 0, tzinfo=timezone.utc),
            next_attempt_at="2026-08-15T00:00:00Z",
        )
        assert calls and calls[0][0] == "oi-changes"
        assert calls[0][1] == "error"
        assert calls[0][2]["next_attempt_at"] == "2026-08-15T00:00:00Z"
        assert "daily request limit" in calls[0][2]["message"]

    def test_a_dead_writer_never_breaks_the_cycle(self, tmp_path, monkeypatch):
        import utils.uw_embargo as mod

        monkeypatch.setattr(
            mod, "_load_writer", lambda: (_ for _ in ()).throw(RuntimeError("turso down"))
        )
        _embargo(tmp_path).record_error(
            DAILY,
            now=datetime.now(timezone.utc),
            next_attempt_at="2026-08-15T00:00:00Z",
        )
