"""The watchdog must never log the Gateway back in during an operator hold.

2026-09-25: the Gateway and the operator share one IBKR username. While
`radon ib release` holds the Gateway out, every sensor reads "Gateway dead",
which is exactly the state the watchdog exists to repair. Repairing it would
log in and kick the operator off interactivebrokers.com again.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ib_watchdog import GatewayState, run_cycle  # noqa: E402
from utils import ib_operator_hold  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IB_2FA_LOCK_PATH", str(tmp_path / "ib-2fa-push-lock.json"))
    monkeypatch.setenv("RADON_IB_OPERATOR_HOLD_PATH", str(tmp_path / "hold.json"))
    monkeypatch.setenv("RADON_IB_OPERATOR_HOLD_AUDIT", str(tmp_path / "hold.jsonl"))
    monkeypatch.setenv("RADON_IB_OPERATOR_HOLD_OWNER_UID", str(os.getuid()))
    monkeypatch.setattr("ib_watchdog.data_plane_window_active", lambda *a, **k: True)


def _cycle(state_path: Path, *, health_reachable: bool):
    dead = {"ib_gateway": {"service_state": "unhealthy", "port_listening": False,
                           "upstream_dead": True, "auth_state": "unreachable"}}

    def fake_fetch(url, timeout):
        return GatewayState.from_health_payload(dead) if health_reachable else None

    with (
        patch("ib_watchdog.fetch_health", side_effect=fake_fetch) as fetch,
        patch("ib_watchdog.trigger_restart", return_value=True) as restart,
        patch("ib_watchdog.record_service_health") as health,
        patch("ib_watchdog.probe_gateway_direct", return_value="dead"),
        patch("ib_watchdog.attribute_api_down", return_value="attribution_unavailable"),
    ):
        state = run_cycle(state_path=state_path)
    return state, restart, health, fetch


@pytest.mark.parametrize("health_reachable", [True, False])
def test_a_held_gateway_is_never_restarted(tmp_path, health_reachable):
    ib_operator_hold.set_hold("operator web login", "test")
    state_path = tmp_path / "state.json"
    for _ in range(10):
        state, restart, _health, _fetch = _cycle(state_path, health_reachable=health_reachable)
        assert restart.call_count == 0
    assert state.last_outcome == "operator_hold"


def test_a_held_cycle_reports_healthy_with_the_hold_reason(tmp_path):
    ib_operator_hold.set_hold("operator web login", "test")
    _state, _restart, health, fetch = _cycle(tmp_path / "state.json", health_reachable=False)
    assert fetch.call_count == 0, "a held cycle must not even probe"
    label, message = health.call_args.args
    assert label == "ok"
    assert "operator hold" in message


def test_clearing_the_hold_resumes_normal_cycles(tmp_path):
    ib_operator_hold.set_hold("operator web login", "test")
    ib_operator_hold.clear_hold("test")
    state, _restart, _health, fetch = _cycle(tmp_path / "state.json", health_reachable=True)
    assert fetch.call_count == 1
    assert state.last_outcome != "operator_hold"


# --- HELD is paged once, clearing is never silent (2026-10-01) ---------------


def _cycle_paging(state_path: Path, pages: list, *, deliver: bool = True):
    def fake_page(title, message):
        pages.append((title, message))
        return deliver

    with patch("ib_watchdog._page_operator", side_effect=fake_page):
        return _cycle(state_path, health_reachable=False)


def test_a_hold_is_paged_once_as_held_with_who_and_why(tmp_path):
    ib_operator_hold.set_hold("flatten from IBKR Mobile", "ssh:joe@phone")
    pages: list = []
    for _ in range(5):
        _cycle_paging(tmp_path / "state.json", pages)
    assert len(pages) == 1
    title, message = pages[0]
    assert "HELD" in title
    assert "not down" in message
    assert "ssh:joe@phone" in message and "flatten from IBKR Mobile" in message
    assert "restart" not in message.lower()


def test_a_failed_held_page_is_retried_next_cycle(tmp_path):
    ib_operator_hold.set_hold("flatten", "ssh:joe")
    pages: list = []
    _cycle_paging(tmp_path / "state.json", pages, deliver=False)
    _cycle_paging(tmp_path / "state.json", pages, deliver=True)
    _cycle_paging(tmp_path / "state.json", pages, deliver=True)
    assert len(pages) == 2


def test_clearing_the_hold_pages_once_that_recovery_is_back(tmp_path):
    ib_operator_hold.set_hold("flatten", "ssh:joe")
    pages: list = []
    _cycle_paging(tmp_path / "state.json", pages)
    ib_operator_hold.clear_hold("ssh:joe")
    _cycle_paging(tmp_path / "state.json", pages)
    _cycle_paging(tmp_path / "state.json", pages)
    assert [title for title, _ in pages] == [
        "radon: IB Gateway HELD",
        "radon: IB Gateway hold cleared",
    ]


def test_an_expired_hold_stays_held_and_says_so(tmp_path):
    ib_operator_hold.set_hold("flatten", "ssh:joe", expires_at="2000-01-01T00:00:00+00:00")
    state, restart, health, _fetch = _cycle(tmp_path / "state.json", health_reachable=False)
    assert restart.call_count == 0
    assert state.last_outcome == "operator_hold"
    assert "still held" in health.call_args.args[1]


# --- Auto-hold when IBC reports the session was taken -------------------------

SCENARIO_6 = (
    "2026-10-01 14:02:11:101 IBC: Other session may be primary, so end this session "
    "and let the other one proceed (scenario 6)"
)
SCENARIO_4 = (
    "2026-10-01 14:02:11:101 IBC: Other session must be primary or primary override, "
    "so end this session and let the other one proceed (scenario 4)"
)
LOGIN = "2026-10-01 13:00:00:000 IBC: Login attempt: 1"


def _cycle_with_log(state_path: Path, log: str, *, health_reachable: bool):
    with (
        patch("ib_watchdog.read_gateway_login_log", return_value=log),
        patch("ib_watchdog._page_operator", return_value=True),
    ):
        return _cycle(state_path, health_reachable=health_reachable)


@pytest.mark.parametrize("health_reachable", [True, False])
@pytest.mark.parametrize("yield_line", [SCENARIO_6, SCENARIO_4])
def test_a_taken_session_sets_the_hold_instead_of_restarting(tmp_path, health_reachable, yield_line):
    state_path = tmp_path / "state.json"
    for _ in range(6):
        state, restart, _health, _fetch = _cycle_with_log(
            state_path, f"{LOGIN}\n{yield_line}\n", health_reachable=health_reachable
        )
        assert restart.call_count == 0
    hold = ib_operator_hold.hold_state()
    assert hold["held"] is True
    assert hold["actor"] == "auto:ib-watchdog"
    assert "taken by another login" in hold["reason"]
    assert state.last_outcome == "operator_hold"


def test_a_login_after_the_yield_is_not_a_taken_session(tmp_path):
    log = f"{LOGIN}\n{SCENARIO_6}\n2026-10-01 14:10:00:000 IBC: Login attempt: 1\n"
    _cycle_with_log(tmp_path / "state.json", log, health_reachable=True)
    assert ib_operator_hold.is_held() is False


def test_primary_scenario_5_is_not_a_yield(tmp_path):
    log = (
        f"{LOGIN}\n2026-10-01 14:02:11:101 IBC: Continue this session and let the "
        "other session exit (scenario 5)\n"
    )
    _cycle_with_log(tmp_path / "state.json", log, health_reachable=True)
    assert ib_operator_hold.is_held() is False


def test_an_unwritable_auto_hold_still_does_not_restart(tmp_path):
    with patch("ib_watchdog.ib_operator_hold.set_hold", side_effect=OSError("read-only")):
        state, restart, health, _fetch = _cycle_with_log(
            tmp_path / "state.json", f"{LOGIN}\n{SCENARIO_6}\n", health_reachable=True
        )
    assert restart.call_count == 0
    assert state.last_outcome == "session_taken:hold_unwritten"
    assert health.call_args.args[0] == "error"
