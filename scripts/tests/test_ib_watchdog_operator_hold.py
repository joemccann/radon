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
