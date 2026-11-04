"""UW quota is one machine with a typed refusal reason.

The audit (implicit-state-machines-20261103 §3.4) found three UW quota
machines that disagreed: the governor refused with text that never matched
``uw_embargo.is_daily_quota``, so a writer armed a 5-minute burst sidecar while
the governor held every request until the 20:00 ET reset. These pins hold the
reason typed from the refusal through the client to the writer's sidecar.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
import requests
from clients.uw_client import UWClient, UWRateLimitError

from utils import uw_budget, uw_embargo
from utils import uw_governor as gov

TOKEN = "synthetic-uw-token"


def _response(code=200, payload=None, headers=None):
    result = requests.Response()
    result.status_code = code
    result._content = json.dumps(payload or {"data": []}).encode()
    result.headers.update(headers or {})
    return result


def _forbid_wire(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("provider HTTP must not run after refusal")

    monkeypatch.setattr(requests.Session, "get", forbidden)


class TestReasonIsTyped:
    def test_message_inference_keeps_legacy_constructors_working(self):
        assert UWRateLimitError("You have hit your daily request limit").reason == "daily_quota"
        assert UWRateLimitError("429 Too Many Requests").reason == "burst"

    def test_explicit_reason_wins_over_message_text(self):
        exc = UWRateLimitError("UW provider quota is exhausted", reason="daily_quota")
        assert uw_embargo.is_daily_quota(exc) is True

    def test_unknown_reason_is_refused(self):
        with pytest.raises(ValueError):
            UWRateLimitError("x", reason="sometimes")


class TestGovernorRefusalReachesTheWriter:
    def test_daily_embargo_refusal_is_daily_quota_end_to_end(self, monkeypatch):
        now = datetime(2026, 10, 21, 16, tzinfo=timezone.utc)
        monkeypatch.setattr(gov.time, "time", lambda: now.timestamp())
        gov.observe(TOKEN, 429, {}, daily_exhausted=True)
        with pytest.raises(gov.AdmissionError) as refused:
            gov.reserve(TOKEN, timeout=0)
        assert refused.value.reason == "daily_quota"

        _forbid_wire(monkeypatch)
        with UWClient(token=TOKEN) as client:
            with pytest.raises(UWRateLimitError) as raised:
                client.get("market/economic-calendar", wait_timeout=0, use_cache=False)
        assert raised.value.reason == "daily_quota"
        assert uw_embargo.is_daily_quota(raised.value) is True
        # The writer's sidecar now waits for the same reset the governor holds.
        assert uw_embargo.retry_at(raised.value, now) == "2026-10-22T00:00:00Z"

    def test_exhausted_daily_budget_is_daily_quota(self, monkeypatch):
        monkeypatch.setenv("RADON_UW_DAILY_LIMIT", "2")
        gov.reserve(TOKEN, priority="interactive", timeout=0)
        gov.reserve(TOKEN, priority="interactive", timeout=0)
        with pytest.raises(gov.AdmissionError) as refused:
            gov.reserve(TOKEN, priority="interactive", timeout=0)
        assert refused.value.reason == "daily_quota"

    def test_provider_burst_embargo_is_burst(self, monkeypatch):
        now = datetime(2026, 10, 21, 16, tzinfo=timezone.utc).timestamp()
        monkeypatch.setattr(gov.time, "time", lambda: now)
        gov.observe(TOKEN, 429, {"Retry-After": "120"})
        with pytest.raises(gov.AdmissionError) as refused:
            gov.reserve(TOKEN, timeout=0)
        assert refused.value.reason == "burst"

    def test_local_minute_window_is_busy(self, monkeypatch):
        monkeypatch.setenv("RADON_UW_REQUESTS_PER_MINUTE", "1")
        gov.reserve(TOKEN, priority="interactive", timeout=0)
        with pytest.raises(gov.AdmissionError) as refused:
            gov.reserve(TOKEN, priority="interactive", timeout=0)
        assert refused.value.reason == "busy"
        now = datetime(2026, 10, 21, 16, tzinfo=timezone.utc)
        exc = UWRateLimitError(str(refused.value), reason=refused.value.reason)
        assert uw_embargo.retry_at(exc, now) == "2026-10-21T16:05:00Z"


class TestAuthStatusIsConsulted:
    def test_rejected_credential_is_not_retried_until_the_probe_window(self, monkeypatch):
        clock = {"now": datetime(2026, 10, 21, 16, tzinfo=timezone.utc).timestamp()}
        monkeypatch.setattr(gov.time, "time", lambda: clock["now"])
        gov.observe(TOKEN, 401, {})
        with pytest.raises(gov.AdmissionError) as refused:
            gov.reserve(TOKEN, priority="interactive", timeout=0)
        assert refused.value.reason == "auth"
        # A rotated key is a different credential and is admitted at once.
        gov.reserve("rotated-synthetic-token", priority="interactive", timeout=0)
        # One bounded probe after the window, so a transient 401 cannot lock out.
        clock["now"] += gov.AUTH_PROBE_SECONDS + 1
        gov.reserve(TOKEN, priority="interactive", timeout=0)

    def test_client_surfaces_auth_refusal_without_the_wire(self, monkeypatch):
        gov.observe(TOKEN, 403, {})
        _forbid_wire(monkeypatch)
        with UWClient(token=TOKEN) as client:
            with pytest.raises(UWRateLimitError) as raised:
                client.get("market/economic-calendar", wait_timeout=0, use_cache=False)
        assert raised.value.reason == "auth"

    def test_success_clears_the_auth_block(self, monkeypatch):
        gov.observe(TOKEN, 401, {})
        gov.observe(TOKEN, 200, {})
        gov.reserve(TOKEN, priority="interactive", timeout=0)


class TestOneResetFunction:
    @pytest.mark.parametrize(
        "moment",
        [
            datetime(2026, 8, 14, 23, 59, 59, tzinfo=timezone.utc),  # 19:59:59 EDT
            datetime(2026, 8, 15, 0, 0, 0, tzinfo=timezone.utc),  # 20:00:00 EDT
            datetime(2026, 1, 15, 1, 0, 0, tzinfo=timezone.utc),  # 20:00:00 EST
            datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc),  # DST fall-back day
            datetime(2026, 3, 8, 12, 0, 0, tzinfo=timezone.utc),  # DST spring-forward day
        ],
    )
    def test_budget_governor_and_embargo_agree(self, moment):
        reset = uw_budget.next_reset(moment)
        assert reset > moment
        assert reset.astimezone(uw_budget.ET).hour == 20
        assert gov._next_reset(moment.timestamp()) == reset.timestamp()
        assert gov._day(moment.timestamp()) == uw_budget.quota_date(moment)
        daily = UWRateLimitError("daily", reason="daily_quota")
        expected = reset.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        assert uw_embargo.retry_at(daily, moment) == expected


class TestUniverseScanReadsTheAdmissionCounter:
    def test_governor_reservations_brake_universe_scans(self, monkeypatch):
        monkeypatch.setenv("RADON_UW_DAILY_LIMIT", "10")
        for _ in range(5):
            gov.reserve(TOKEN, priority="interactive", timeout=0)
        # The budget file never saw these (no wire), but admission counted them.
        assert uw_budget.used() == 0
        assert uw_budget.should_block_universe_scan() is True
        assert uw_budget.remaining() == 5

    def test_budget_file_hits_count_toward_admission(self, monkeypatch):
        # Next.js mirrors its hits into the budget file after the governor
        # seeded once; admission must still see them.
        monkeypatch.setenv("RADON_UW_DAILY_LIMIT", "5")
        gov.snapshot()  # seeds the governor from an empty budget
        uw_budget.record_hits(5)
        with pytest.raises(gov.AdmissionError) as refused:
            gov.reserve(TOKEN, priority="interactive", timeout=0)
        assert refused.value.reason == "daily_quota"


class TestEmbargoArm:
    def _embargo(self, tmp_path):
        return uw_embargo.UwEmbargo("probe", lambda: tmp_path / "probe_uw_embargo.json")

    def test_arm_is_extend_only(self, tmp_path):
        e = self._embargo(tmp_path)
        e.arm("2026-08-15T00:00:00Z")
        e.arm("2026-08-14T18:05:00Z")
        now = datetime(2026, 8, 14, 18, 0, tzinfo=timezone.utc)
        assert e.active_until(now) == "2026-08-15T00:00:00Z"

    def test_arm_extends_a_shorter_deadline(self, tmp_path):
        e = self._embargo(tmp_path)
        e.arm("2026-08-14T18:05:00Z")
        e.arm("2026-08-15T00:00:00Z")
        now = datetime(2026, 8, 14, 18, 0, tzinfo=timezone.utc)
        assert e.active_until(now) == "2026-08-15T00:00:00Z"

    def test_arm_never_leaves_a_torn_sidecar(self, tmp_path, monkeypatch):
        e = self._embargo(tmp_path)
        e.arm("2026-08-15T00:00:00Z")
        from pathlib import Path

        original = Path.write_text

        def torn(self, data, *args, **kwargs):
            original(self, data[: len(data) // 2], *args, **kwargs)
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", torn)
        with pytest.raises(OSError):
            e.arm("2026-08-16T00:00:00Z")
        monkeypatch.setattr(Path, "write_text", original)
        now = datetime(2026, 8, 14, 18, 0, tzinfo=timezone.utc)
        assert e.active_until(now) == "2026-08-15T00:00:00Z"
        assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []
