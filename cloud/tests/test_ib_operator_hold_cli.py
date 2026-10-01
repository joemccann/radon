"""`radon ib release|resume|status`: let the operator use the shared IBKR login.

2026-09-25: the Gateway and the operator share one IBKR username, and IBC
reclaimed the session three times, kicking the operator off
interactivebrokers.com. Release must set the hold BEFORE stopping anything (so
no start path can win a race), must not depend on the deploy lock or the
lifecycle mutex to get the Gateway down, and must report success only when the
container is really gone.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

CLOUD = Path(__file__).resolve().parents[1]
APP_ROOT = CLOUD.parent
SCRIPT = CLOUD / "scripts" / "ib-operator-hold.sh"
OPERATOR = CLOUD / "scripts" / "operator-radon.sh"
SETUP = CLOUD / "scripts" / "setup-vps.sh"


def _stub(path: Path, body: str) -> Path:
    path.write_text("#!/bin/bash\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture
def box(tmp_path: Path):
    calls = tmp_path / "calls.log"
    state = tmp_path / "container"
    state.write_text("running\n")
    log = f'printf "%s %s\\n" "$(basename "$0")" "$*" >> {calls}\n'
    control = _stub(tmp_path / "gateway-control", log + f"""
case "$1" in
  stop) [[ "${{STUB_CONTROL_STOP_RC:-0}}" == 0 ]] && echo stopped > {state}; exit "${{STUB_CONTROL_STOP_RC:-0}}" ;;
  start) [[ "${{STUB_CONTROL_START_RC:-0}}" == 0 ]] && echo running > {state}; exit "${{STUB_CONTROL_START_RC:-0}}" ;;
  status) cat {state}; exit 0 ;;
esac
""")
    docker_gw = _stub(tmp_path / "docker-gw", log + f"""
case "$1" in
  compose-down) [[ "${{STUB_DOWN_WORKS:-1}}" == 1 ]] && echo stopped > {state}; exit 0 ;;
  kill) echo stopped > {state}; exit 0 ;;
  inspect-running) [[ "$(cat {state})" == running ]] && echo true || echo false; exit 0 ;;
esac
""")
    systemctl = _stub(tmp_path / "systemctl", log + "exit 0\n")
    env = {
        **os.environ,
        "RADON_IB_HOLD_TEST_MODE": "1",
        "RADON_APP_DIR": str(APP_ROOT),
        "RADON_IB_HOLD_PYTHON": sys.executable,
        "RADON_IB_GATEWAY_CONTROL": str(control),
        "RADON_DOCKER_GW": str(docker_gw),
        "RADON_SYSTEMCTL": str(systemctl),
        "RADON_IB_RELEASE_POLL_SECS": "0",
        "RADON_IB_OPERATOR_HOLD_PATH": str(tmp_path / "hold.json"),
        "RADON_IB_OPERATOR_HOLD_AUDIT": str(tmp_path / "hold.jsonl"),
        "RADON_IB_OPERATOR_HOLD_OWNER_UID": str(os.getuid()),
    }

    class Box:
        def run(self, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["bash", str(SCRIPT), *args], env={**env, **extra},
                text=True, capture_output=True, check=False, timeout=60,
            )

        def calls(self) -> list[str]:
            return calls.read_text().splitlines() if calls.exists() else []

        def held(self) -> bool:
            return (tmp_path / "hold.json").read_text().startswith('{"held": true')

        def container(self) -> str:
            return state.read_text().strip()

    return Box()


def test_release_holds_first_then_stops_the_gateway(box):
    result = box.run("release", "--reason", "web login")
    assert result.returncode == 0, result.stdout + result.stderr
    assert box.held()
    assert box.container() == "stopped"
    assert "RELEASED" in result.stdout
    calls = box.calls()
    assert "systemctl stop radon-ib-watchdog.timer" in calls
    assert calls.index("systemctl stop radon-ib-watchdog.timer") < calls.index("gateway-control stop")


def test_release_falls_back_to_compose_down_when_the_helper_is_blocked(box):
    result = box.run("release", STUB_CONTROL_STOP_RC="74")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "docker-gw compose-down" in box.calls()
    assert box.container() == "stopped"


def test_release_kills_a_container_that_survives_compose_down(box):
    result = box.run("release", STUB_CONTROL_STOP_RC="74", STUB_DOWN_WORKS="0")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "docker-gw kill" in box.calls()
    assert box.container() == "stopped"


def test_release_reports_failure_while_the_container_still_runs(box, tmp_path):
    _stub(tmp_path / "docker-gw", 'case "$1" in inspect-running) echo true ;; esac\nexit 0\n')
    result = box.run("release")
    assert result.returncode != 0
    assert "STILL RUNNING" in result.stdout + result.stderr
    assert box.held(), "a failed stop must leave the hold in place"


def test_resume_clears_the_hold_and_starts_once(box):
    box.run("release")
    result = box.run("resume")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not box.held()
    assert box.container() == "running"
    assert box.calls().count("gateway-control start") == 1
    assert "systemctl start radon-ib-watchdog.timer" in box.calls()
    assert "Approve" in result.stdout


def test_status_reports_hold_and_gateway(box):
    box.run("release")
    result = box.run("status")
    assert result.returncode == 0
    assert '"held": true' in result.stdout
    assert "gateway: stopped" in result.stdout


@pytest.mark.parametrize("argv", [(), ("delete",), ("release", "--force"), ("resume", "extra")])
def test_unknown_arguments_are_refused(box, argv):
    result = box.run(*argv)
    assert result.returncode == 64
    assert box.calls() == []


def test_non_root_is_refused_outside_test_mode(box):
    if os.geteuid() == 0:
        pytest.skip("runs as root")
    result = box.run("release", RADON_IB_HOLD_TEST_MODE="0")
    assert result.returncode == 77
    assert box.calls() == []


def test_radon_cli_dispatches_ib_before_taking_the_deploy_lock():
    text = OPERATOR.read_text(encoding="utf-8")
    dispatch = text.index('"ib"')
    assert dispatch < text.index("acquire_deploy_lock() {")
    assert "/usr/local/sbin/radon-ib-hold" in text


def test_setup_installs_the_hold_command_root_owned():
    text = SETUP.read_text(encoding="utf-8")
    assert "install_ib_hold() {" in text
    assert "/usr/local/sbin/radon-ib-hold" in text
