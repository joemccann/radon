"""Watchdog state load never resets the restart-storm caps and never crashes.

Audit 2026-11-03 §1 #20: corrupt JSON reset every counter, including the caps
that bound watchdog-initiated 2FA pushes (stuck-2FA rolling hour, api-hang
consecutive restarts), so a torn write re-armed the 2026-07-05 storm. Valid
JSON with one bad field type raised out of ``from_dict`` and crashed every
oneshot run. Load is now per field: a bad cap field reads as "cap reached",
any other bad field takes its default, and cross-field invariants hold.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ib_watchdog as wd  # noqa: E402

NOW = 1_800_000_000.0


@pytest.fixture
def state_path(tmp_path: Path) -> Path:
    return tmp_path / "ib-watchdog-state.json"


def _caps_reached(state: wd.WatchdogState) -> bool:
    history = wd._prune_restart_history(state.stuck_2fa_restart_history, NOW)
    return (
        wd._stuck_2fa_restart_blocked(history, NOW) is not None
        and wd._api_hang_restart_blocked(state, NOW) is not None
    )


def test_missing_file_is_a_clean_slate(state_path):
    state = wd.load_state(state_path, now=NOW)
    assert state == wd.WatchdogState()
    assert not _caps_reached(state)


@pytest.mark.parametrize("raw", ["{garbage", "", "[]", "null", '"text"', "42"])
def test_unparseable_or_non_object_state_saturates_the_caps(state_path, raw):
    state_path.write_text(raw)
    state = wd.load_state(state_path, now=NOW)
    assert _caps_reached(state)
    assert state.api_hang_restart_count == wd.API_HANG_MAX_CONSECUTIVE_RESTARTS
    assert state.degraded_count == 0
    assert state.last_outcome == "state_unreadable"


def test_cap_window_is_bounded(state_path):
    state_path.write_text("{garbage")
    state = wd.load_state(state_path, now=NOW)
    later = NOW + wd.STUCK_2FA_RESTART_WINDOW_SECS + 1
    history = wd._prune_restart_history(state.stuck_2fa_restart_history, later)
    assert wd._stuck_2fa_restart_blocked(history, later) is None


def test_bad_field_types_do_not_crash_and_keep_good_fields(state_path):
    state_path.write_text(json.dumps({
        "degraded_count": "two",
        "last_outcome": 5,
        "awaiting_2fa_alerted": "no",
        "stuck_2fa_restart_history": ["a", None],
        "api_hang_restart_count": None,
        "last_restart_at": {"x": 1},
        "operator_hold_announced": "hold-1",
        "quiet_degraded_since": 12.5,
    }))
    state = wd.load_state(state_path, now=NOW)
    assert state.degraded_count == 0
    assert state.last_outcome == "init"
    assert state.awaiting_2fa_alerted is False
    assert state.operator_hold_announced == "hold-1"
    assert state.quiet_degraded_since == 12.5
    assert _caps_reached(state)


def test_good_caps_are_kept_as_written(state_path):
    state_path.write_text(json.dumps({
        "api_hang_restart_count": 1,
        "last_restart_at": NOW - 10_000,
        "stuck_2fa_restart_history": [NOW - 100],
        "degraded_count": "bad",
    }))
    state = wd.load_state(state_path, now=NOW)
    assert state.api_hang_restart_count == 1
    assert state.stuck_2fa_restart_history == [NOW - 100]
    assert state.last_restart_at == NOW - 10_000


def test_bad_throttle_timestamp_restarts_the_quiet_period(state_path):
    state_path.write_text(json.dumps({
        "login_throttle_since": "yesterday",
        "login_throttle_retries": 1,
        "login_throttle_line": "Too many failed login attempts",
    }))
    state = wd.load_state(state_path, now=NOW)
    assert state.login_throttle_since == NOW
    assert state.login_throttle_retries == 1


class TestInvariants:
    def test_recovery_confirmation_needs_a_pending_reconnect(self, state_path):
        state_path.write_text(json.dumps({
            "authenticated_recovery_count": 1, "pending_pool_reconnect": False,
        }))
        assert wd.load_state(state_path, now=NOW).authenticated_recovery_count == 0

    def test_throttle_fields_need_an_episode(self, state_path):
        state_path.write_text(json.dumps({
            "login_throttle_since": 0,
            "login_throttle_retries": 2,
            "login_throttle_alerted": True,
            "login_throttle_line": "x",
        }))
        state = wd.load_state(state_path, now=NOW)
        assert state.login_throttle_retries == 0
        assert state.login_throttle_alerted is False
        assert state.login_throttle_line == ""

    def test_negative_counters_clamp(self, state_path):
        state_path.write_text(json.dumps({
            "degraded_count": -4, "stuck_2fa_count": -1, "api_hang_restart_count": -2,
        }))
        state = wd.load_state(state_path, now=NOW)
        assert state.degraded_count == 0
        assert state.stuck_2fa_count == 0
        # A negative cap counter is nonsense, not a reset: saturate.
        assert state.api_hang_restart_count == wd.API_HANG_MAX_CONSECUTIVE_RESTARTS


def test_round_trip_is_identity(state_path):
    original = wd.WatchdogState(
        degraded_count=2,
        stuck_2fa_restart_history=[NOW - 5],
        api_hang_restart_count=1,
        pending_pool_reconnect=True,
        authenticated_recovery_count=1,
        login_throttle_since=NOW - 30,
        login_throttle_retries=1,
        lease_acquired_at=NOW - 60,
    )
    wd.save_state(state_path, original)
    assert wd.load_state(state_path, now=NOW) == original
