"""IBKR login throttle is not an API hang (2026-09-26 incident).

IBKR answered every Gateway login with "Too many failed login attempts. Please
wait N seconds", BEFORE the 2FA step, so no IBKR Mobile push was ever sent.
IBC then sat at that dialog with the API port open and no handshake, which the
independent probe reads as ``wedged``. The watchdog treated it as a JVM API
hang and restarted the container three times (21:57, 22:07, 22:20Z). Each
restart was one more login attempt, keeping IBKR's failed-login counter armed,
and the operator's forced restarts (21:46, 22:30, 22:40Z) added three more.

The watchdog must recognise the throttle, never feed it to the api-hang ladder,
tell the operator plainly, and make exactly one fresh login attempt only after
a quiet cooldown with no attempts, backing off if IBKR throttles again.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import ib_watchdog
from ib_watchdog import (  # type: ignore[import-not-found]
    GATEWAY_ALIVE,
    GATEWAY_WEDGED,
    LOGIN_THROTTLE_COOLDOWN_BASE_SECS,
    GatewayState,
    WatchdogState,
    load_state,
    parse_login_events,
    run_cycle,
    save_state,
)

# Verbatim from the ib-gateway container, 2026-09-26.
ATTEMPT = "2026-09-26 22:40:32:142 IBC: Login attempt: 1"
THROTTLE = (
    "2026-09-26 22:40:32:372 IBC: Too many failed login attempts. "
    "Please wait 22 seconds before attempting to re-login again."
)
THROTTLED_LOG = "\n".join(
    [
        "2026-09-26 22:40:32:140 IBC: Setting user name",
        "2026-09-26 22:40:32:141 IBC: Setting password",
        ATTEMPT,
        "2026-09-26 22:40:32:179 IBC: Click button: Log In",
        "2026-09-26 22:40:32:347 IBC: detected dialog entitled: ** no title **; event=Opened",
        THROTTLE,
        "2026-09-26 22:40:32:373 IBC: detected frame entitled: IBKR Gateway; event=Lost focus",
    ]
)
NEXT_ATTEMPT = "2026-09-26 23:10:01:100 IBC: Login attempt: 1"
NEXT_THROTTLE = (
    "2026-09-26 23:10:01:300 IBC: Too many failed login attempts. "
    "Please wait 41 seconds before attempting to re-login again."
)
LOGIN_PROCEEDING_LOG = "\n".join(
    [
        NEXT_ATTEMPT,
        "2026-09-26 23:10:01:400 IBC: Click button: Log In",
        "2026-09-26 23:10:02:000 IBC: detected dialog entitled: Second Factor Authentication; event=Opened",
    ]
)

T0 = 1_790_000_000.0


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IB_2FA_LOCK_PATH", str(tmp_path / "ib-2fa-push-lock.json"))
    monkeypatch.setattr("ib_watchdog.data_plane_window_active", lambda *a, **k: True)
    monkeypatch.setattr("ib_watchdog.quiet_window_active", lambda *a, **k: False)
    monkeypatch.setattr("ib_watchdog._capture_hang_forensics", lambda: None)


@pytest.fixture
def state_path(tmp_path: Path) -> Path:
    return tmp_path / "watchdog-state.json"


def _health(auth_state: str = "unreachable") -> dict:
    return {
        "ib_gateway": {
            "service_state": "unhealthy",
            "port_listening": True,
            "upstream_dead": True,
            "auth_state": auth_state,
        }
    }


def _cycle(state_path, *, log, now, verdict=GATEWAY_WEDGED, auth="unreachable"):
    with (
        patch("ib_watchdog.fetch_health", return_value=GatewayState.from_health_payload(_health(auth))),
        patch("ib_watchdog.probe_gateway_direct", return_value=verdict),
        patch("ib_watchdog.read_gateway_login_log", return_value=log),
        patch("ib_watchdog.trigger_restart", return_value=True) as restart,
        patch("ib_watchdog.record_service_health") as health,
    ):
        state = run_cycle(state_path=state_path, dry_run=True, clock=lambda: now)
    return state, restart, health


def test_parse_recognises_a_throttled_latest_login():
    events = parse_login_events(THROTTLED_LOG)
    assert events.throttled
    assert events.throttle_line == THROTTLE


def test_parse_a_login_that_got_past_the_throttle_is_not_throttled():
    assert not parse_login_events(THROTTLED_LOG + "\n" + LOGIN_PROCEEDING_LOG).throttled
    assert not parse_login_events("").throttled
    assert not parse_login_events(None).throttled


def test_a_throttled_login_never_feeds_the_api_hang_ladder(state_path):
    now = T0
    for _ in range(10):
        state, restart, _ = _cycle(state_path, log=THROTTLED_LOG, now=now)
        restart.assert_not_called()
        now += 60
    assert state.degraded_count == 0
    assert state.last_outcome.startswith("login_throttled")


def test_the_operator_is_told_plainly_not_to_restart(state_path):
    _, _, health = _cycle(state_path, log=THROTTLED_LOG, now=T0)
    status, = health.call_args.args[:1]
    message = health.call_args.kwargs.get("error_message") or ""
    assert status == "error"
    assert "too many failed login attempts" in message.lower()
    assert "do not restart" in message.lower()


def test_the_episode_outlives_the_five_minute_log_window(state_path):
    _cycle(state_path, log=THROTTLED_LOG, now=T0)
    # The shim returns `docker logs --since 5m`: the throttle line ages out
    # while IBC keeps sitting on its dialog.
    state, restart, _ = _cycle(state_path, log="", now=T0 + 600)
    restart.assert_not_called()
    assert state.last_outcome.startswith("login_throttled")


def test_one_fresh_login_after_a_quiet_cooldown_then_backoff(state_path):
    _cycle(state_path, log=THROTTLED_LOG, now=T0)
    state, restart, _ = _cycle(state_path, log="", now=T0 + LOGIN_THROTTLE_COOLDOWN_BASE_SECS + 1)
    assert restart.call_count == 1
    assert state.login_throttle_retries == 1
    # The next cooldown is longer, and nothing fires inside it.
    now = T0 + LOGIN_THROTTLE_COOLDOWN_BASE_SECS + 61
    for _ in range(25):
        state, restart, _ = _cycle(state_path, log="", now=now)
        restart.assert_not_called()
        now += 60


def test_a_new_throttled_attempt_restarts_the_cooldown(state_path):
    _cycle(state_path, log=THROTTLED_LOG, now=T0)
    # The operator forced another restart 14 minutes in; IBKR throttled it too.
    retried_at = T0 + 840
    _cycle(state_path, log=NEXT_ATTEMPT + "\n" + NEXT_THROTTLE, now=retried_at)
    state, restart, _ = _cycle(state_path, log="", now=T0 + LOGIN_THROTTLE_COOLDOWN_BASE_SECS + 1)
    restart.assert_not_called()
    state, restart, _ = _cycle(
        state_path, log="", now=retried_at + LOGIN_THROTTLE_COOLDOWN_BASE_SECS + 1
    )
    assert restart.call_count == 1


def test_off_hours_never_retries_the_login(state_path, monkeypatch):
    monkeypatch.setattr("ib_watchdog.data_plane_window_active", lambda *a, **k: False)
    _cycle(state_path, log=THROTTLED_LOG, now=T0)
    state, restart, _ = _cycle(state_path, log="", now=T0 + 4 * LOGIN_THROTTLE_COOLDOWN_BASE_SECS)
    restart.assert_not_called()
    assert "off_hours" in state.last_outcome


def test_a_held_push_lock_defers_the_retry(state_path):
    _cycle(state_path, log=THROTTLED_LOG, now=T0)
    held = type("Lock", (), {"holder": "radon-cloud.ib-gateway-control", "expires_at": T0 + 99999})()
    with patch("ib_watchdog._check_2fa_push_lock_bounded", return_value=held):
        state, restart, _ = _cycle(state_path, log="", now=T0 + LOGIN_THROTTLE_COOLDOWN_BASE_SECS + 1)
    restart.assert_not_called()
    assert "push_lock" in state.last_outcome


def test_the_api_hang_cap_does_not_block_the_throttle_retry(state_path):
    save_state(state_path, WatchdogState(api_hang_restart_count=3))
    _cycle(state_path, log=THROTTLED_LOG, now=T0)
    _, restart, _ = _cycle(state_path, log="", now=T0 + LOGIN_THROTTLE_COOLDOWN_BASE_SECS + 1)
    assert restart.call_count == 1


def test_recovery_ends_the_episode(state_path):
    _cycle(state_path, log=THROTTLED_LOG, now=T0)
    state, _, _ = _cycle(state_path, log="", now=T0 + 60, verdict=GATEWAY_ALIVE, auth="authenticated")
    assert state.login_throttle_since == 0.0
    assert state.login_throttle_retries == 0
    assert load_state(state_path).login_throttle_line == ""


def test_a_login_that_got_past_the_throttle_ends_the_episode(state_path):
    _cycle(state_path, log=THROTTLED_LOG, now=T0)
    state, _, _ = _cycle(state_path, log=LOGIN_PROCEEDING_LOG, now=T0 + 60)
    assert state.login_throttle_since == 0.0


def test_an_authenticated_wedge_is_still_an_api_hang(state_path):
    """The real JVM API hang (session authenticated, acceptor wedged) keeps
    its ladder, and the throttle check is not even consulted."""
    save_state(state_path, WatchdogState(degraded_count=2))
    with patch("ib_watchdog.read_gateway_login_log") as reader:
        with (
            patch("ib_watchdog.fetch_health", return_value=GatewayState.from_health_payload(_health("authenticated"))),
            patch("ib_watchdog.probe_gateway_direct", return_value=GATEWAY_WEDGED),
            patch("ib_watchdog.trigger_restart", return_value=True) as restart,
            patch("ib_watchdog.record_service_health"),
        ):
            run_cycle(state_path=state_path, dry_run=True, clock=lambda: T0)
    reader.assert_not_called()
    assert restart.call_count == 1


def test_an_unreadable_log_keeps_the_existing_ladder(state_path):
    save_state(state_path, WatchdogState(degraded_count=2))
    state, restart, _ = _cycle(state_path, log=None, now=T0)
    assert restart.call_count == 1
    assert state.last_outcome.startswith("restarted")


def test_the_state_round_trips(state_path):
    _cycle(state_path, log=THROTTLED_LOG, now=T0)
    loaded = load_state(state_path)
    assert loaded.login_throttle_since == T0
    assert loaded.login_throttle_line == THROTTLE
    assert ib_watchdog.WatchdogState.from_dict(loaded.to_dict()) == loaded
