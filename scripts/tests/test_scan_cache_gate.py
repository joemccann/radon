"""Tests for the off-hours scan cache gate (utils.scan_cache_gate)."""
import json
from datetime import datetime, timezone

import pytest

import utils.scan_cache_gate as gate
from utils.market_calendar import last_completed_session_date, most_recent_session_date


# ── most_recent_session_date ────────────────────────────────────────

class TestMostRecentSessionDate:
    def test_saturday_resolves_to_friday(self):
        sat = datetime(2026, 5, 30, 15, 0, tzinfo=timezone.utc)  # Sat
        assert most_recent_session_date(sat) == "2026-05-29"

    def test_sunday_resolves_to_friday(self):
        sun = datetime(2026, 5, 31, 16, 0, tzinfo=timezone.utc)  # Sun
        assert most_recent_session_date(sun) == "2026-05-29"

    def test_weekday_after_open_is_today(self):
        # Fri 2026-05-29 14:00 UTC == 10:00 EDT (after open)
        wd = datetime(2026, 5, 29, 14, 0, tzinfo=timezone.utc)
        assert most_recent_session_date(wd) == "2026-05-29"

    def test_weekday_pre_open_is_prior_session(self):
        # Fri 2026-05-29 12:00 UTC == 08:00 EDT (before open) → Thursday
        wd = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)
        assert most_recent_session_date(wd) == "2026-05-28"


# ── last_completed_session_date ─────────────────────────────────────

class TestLastCompletedSessionDate:
    def test_weekday_intraday_is_prior_session(self):
        # Fri 2026-05-29 18:00 UTC == 14:00 EDT (open, pre-close) → Thursday:
        # today's daily close does not exist yet.
        wd = datetime(2026, 5, 29, 18, 0, tzinfo=timezone.utc)
        assert last_completed_session_date(wd) == "2026-05-28"

    def test_weekday_after_close_is_today(self):
        # Fri 2026-05-29 20:30 UTC == 16:30 EDT (after the close)
        wd = datetime(2026, 5, 29, 20, 30, tzinfo=timezone.utc)
        assert last_completed_session_date(wd) == "2026-05-29"

    def test_saturday_resolves_to_friday(self):
        sat = datetime(2026, 5, 30, 15, 0, tzinfo=timezone.utc)  # Sat
        assert last_completed_session_date(sat) == "2026-05-29"

    def test_weekday_pre_open_is_prior_session(self):
        wd = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)  # 08:00 EDT
        assert last_completed_session_date(wd) == "2026-05-28"


# ── _session_date_from_payload ──────────────────────────────────────

class TestSessionDateFromPayload:
    def test_cri_date_field(self):
        assert gate._session_date_from_payload({"date": "2026-05-30"}) == "2026-05-30"

    def test_scan_time_utc_to_et(self):
        # 2026-05-31T01:00Z == 2026-05-30 21:00 ET
        assert gate._session_date_from_payload({"scan_time": "2026-05-31T01:00:00Z"}) == "2026-05-30"

    def test_scan_time_naive_assumed_utc(self):
        assert gate._session_date_from_payload({"scan_time": "2026-05-30T12:50:25.484079"}) == "2026-05-30"

    def test_missing_returns_none(self):
        assert gate._session_date_from_payload({}) is None


# ── cached_scan_if_fresh ────────────────────────────────────────────

class TestCachedScanIfFresh:
    SATURDAY = datetime(2026, 5, 30, 15, 0, tzinfo=timezone.utc)  # closed; session = Fri 5/29
    WEEKDAY_OPEN = datetime(2026, 5, 29, 17, 0, tzinfo=timezone.utc)  # 13:00 EDT, market open

    def _write(self, tmp_path, payload):
        p = tmp_path / "scan.json"
        p.write_text(json.dumps(payload))
        return p

    def test_serves_current_cache_when_closed(self, tmp_path):
        p = self._write(tmp_path, {"date": "2026-05-29", "value": 1})
        out = gate.cached_scan_if_fresh(p, now=self.SATURDAY)
        assert out is not None and out["value"] == 1

    def test_skips_when_market_open(self, tmp_path):
        p = self._write(tmp_path, {"date": "2026-05-29"})
        assert gate.cached_scan_if_fresh(p, now=self.WEEKDAY_OPEN) is None

    def test_rescans_when_cache_behind_expected_session(self, tmp_path):
        # Closed Saturday, expected = Fri 5/29, but cache is Thursday → re-scan.
        p = self._write(tmp_path, {"date": "2026-05-28"})
        assert gate.cached_scan_if_fresh(p, now=self.SATURDAY) is None

    def test_serves_when_cache_newer_than_expected(self, tmp_path):
        # Defensive >= : a cache dated the expected session or newer is served.
        p = self._write(tmp_path, {"date": "2026-05-29"})
        assert gate.cached_scan_if_fresh(p, now=self.SATURDAY) is not None

    def test_force_bypasses_gate(self, tmp_path):
        p = self._write(tmp_path, {"date": "2026-05-29"})
        assert gate.cached_scan_if_fresh(p, force=True, now=self.SATURDAY) is None

    def test_missing_cache_returns_none(self, tmp_path):
        assert gate.cached_scan_if_fresh(tmp_path / "nope.json", now=self.SATURDAY) is None

    def test_unparseable_cache_returns_none(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not json")
        assert gate.cached_scan_if_fresh(p, now=self.SATURDAY) is None
