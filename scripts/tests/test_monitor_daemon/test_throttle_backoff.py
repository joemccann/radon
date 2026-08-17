#!/usr/bin/env python3
"""Tests for the throttle-aware exponential backoff helper.

The Flex Web Service uses a sliding-window rate limit — every request
during throttle pushes the window out. This module is the pure-data
state machine the cash_flow_sync handler uses to decide when it's
allowed to call Flex again.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from monitor_daemon.handlers._throttle_backoff import (  # noqa: E402
    FlexThrottleError,
    THROTTLE_EMBARGO_SECS,
    blocked_until,
    initial_state,
    is_blocked,
    record_soft_failure,
    record_success,
    record_throttle,
)


NOW = datetime(2026, 5, 11, 21, 0, 0, tzinfo=timezone.utc)  # Mon 5pm ET


class TestInitialState:
    def test_empty_state_has_no_blocking(self):
        state = initial_state()
        assert state["throttle_count"] == 0
        assert state["blocked_until"] is None
        assert is_blocked(state, now_utc=NOW) is False


class TestEscalation:
    """The ladder models IBKR's ACTUAL 1018 limit: one request per second,
    10 per minute, per token. A one-minute window.

    It was 24h -> 48h -> 72h -> 168h, three orders of magnitude harsher than the
    constraint it claimed to model; a full walk cost 312 hours. Re-pinned here
    rather than deleted so the old durations cannot come back.
    """

    def test_first_throttle_embargoes_ninety_seconds(self):
        state = record_throttle(initial_state(), now_utc=NOW)
        assert state["throttle_count"] == 1
        # Just past IBKR's documented 60s window.
        assert blocked_until(state) == NOW + timedelta(seconds=90)

    def test_second_throttle_embargoes_five_minutes(self):
        s1 = record_throttle(initial_state(), now_utc=NOW)
        s2 = record_throttle(s1, now_utc=NOW + timedelta(minutes=2))
        assert s2["throttle_count"] == 2
        assert blocked_until(s2) == NOW + timedelta(minutes=2) + timedelta(minutes=5)

    def test_third_throttle_embargoes_fifteen_minutes(self):
        s = initial_state()
        for _ in range(3):
            s = record_throttle(s, now_utc=NOW)
        assert s["throttle_count"] == 3
        assert blocked_until(s) == NOW + timedelta(minutes=15)

    def test_fourth_throttle_caps_at_one_hour(self):
        s = initial_state()
        for _ in range(4):
            s = record_throttle(s, now_utc=NOW)
        assert s["throttle_count"] == 4
        assert blocked_until(s) == NOW + timedelta(hours=1)

    def test_seventh_throttle_still_capped_at_one_hour(self):
        s = initial_state()
        for _ in range(7):
            s = record_throttle(s, now_utc=NOW)
        assert s["throttle_count"] == 7
        # Cap is honored regardless of counter value.
        assert blocked_until(s) == NOW + timedelta(hours=1)
        # Anti-pin: the superseded week-long cap.
        assert blocked_until(s) != NOW + timedelta(hours=168)


class TestBlockedQuery:
    def test_is_blocked_before_window_expires(self):
        s = record_throttle(initial_state(), now_utc=NOW)
        assert is_blocked(s, now_utc=NOW + timedelta(seconds=60)) is True

    def test_is_not_blocked_after_window_expires(self):
        s = record_throttle(initial_state(), now_utc=NOW)
        assert is_blocked(s, now_utc=NOW + timedelta(seconds=120)) is False

    def test_is_blocked_handles_naive_iso_string(self):
        # Daemon state.json may end up with naive timestamps.
        s = {
            "throttle_count": 1,
            "blocked_until": (NOW + timedelta(hours=24))
            .replace(tzinfo=None)
            .isoformat(),
        }
        # Treat naive as UTC — so we should still see "blocked" 1h in.
        assert is_blocked(s, now_utc=NOW + timedelta(hours=1)) is True


class TestSuccessResets:
    def test_success_clears_counter_and_block(self):
        s = record_throttle(initial_state(), now_utc=NOW)
        s = record_throttle(s, now_utc=NOW)
        s = record_success(s)
        assert s == initial_state()
        assert is_blocked(s, now_utc=NOW + timedelta(hours=1)) is False


class TestSoftFailure:
    def test_soft_failure_does_not_escalate(self):
        s = record_throttle(initial_state(), now_utc=NOW)
        before = s["throttle_count"]
        s2 = record_soft_failure(s, now_utc=NOW + timedelta(hours=25))
        assert s2["throttle_count"] == before

    def test_soft_failure_sets_short_embargo(self):
        """A soft failure (e.g. "statement not ready") sets a 5-min
        embargo so the handler retries within the same trading day on a
        measured cadence rather than every 30s. Before 2026-05-15 this
        function returned ``blocked_until=None`` and the daily handler's
        latched ``last_run`` skipped the entire day on a single transient
        timeout — 7 days of cash flow data went missing in the 2026-05-14
        incident.
        """
        s = record_soft_failure(initial_state(), now_utc=NOW)
        # Blocked immediately and for the next ~5 minutes.
        assert is_blocked(s, now_utc=NOW) is True
        assert is_blocked(s, now_utc=NOW + timedelta(minutes=4)) is True
        # Cleared after the cooldown — handler can retry.
        assert is_blocked(s, now_utc=NOW + timedelta(minutes=6)) is False

    def test_soft_failure_supports_custom_cooldown(self):
        s = record_soft_failure(initial_state(), now_utc=NOW, cooldown_seconds=30)
        assert is_blocked(s, now_utc=NOW + timedelta(seconds=20)) is True
        assert is_blocked(s, now_utc=NOW + timedelta(seconds=40)) is False


class TestThrottleErrorType:
    def test_throttle_error_is_runtime_error_subclass(self):
        err = FlexThrottleError("1018", "Too many requests")
        assert isinstance(err, RuntimeError)
        assert err.code == "1018"
        assert err.message == "Too many requests"
        assert "1018" in str(err)
