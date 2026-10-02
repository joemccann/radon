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
        "RADON_IB_HOLD_CLI": str(APP_ROOT / "scripts" / "utils" / "ib_operator_hold.py"),
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
    # The watchdog keeps running: it stands down on the hold and resumes
    # recovery when the admin panel (which cannot start a timer) clears it.
    assert "systemctl stop radon-ib-watchdog.timer" not in box.calls()


def test_release_records_an_optional_expiry_that_never_lifts_the_hold(box, tmp_path):
    result = box.run("release", "--expires-at", "2000-01-01T00:00:00+00:00")
    assert result.returncode == 0, result.stdout + result.stderr
    assert box.held()
    assert '"expires_at": "2000-01-01T00:00:00+00:00"' in (tmp_path / "hold.json").read_text()


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


# -- The root command must never run code the radon account can write --------


def test_root_hold_never_executes_from_the_app_checkout():
    """Root ran the hold CLI out of the radon-owned checkout, and CPython put
    that directory first on sys.path: radon code execution became root at the
    operator's next `radon ib`. Root runs the root-owned installed copy,
    isolated."""
    text = SCRIPT.read_text(encoding="utf-8")
    code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    assert "RADON_APP_DIR" not in code
    assert "/home/radon" not in code
    assert "/usr/local/lib/radon/ib_operator_hold.py" in code
    assert '"$HOLD_PYTHON" -I "$HOLD_CLI"' in code


def _cli_copy(tmp_path: Path, mode: int = 0o644) -> Path:
    cli = tmp_path / "cli" / "ib_operator_hold.py"
    cli.parent.mkdir()
    cli.write_bytes((APP_ROOT / "scripts" / "utils" / "ib_operator_hold.py").read_bytes())
    cli.chmod(mode)
    return cli


def test_release_runs_an_installed_cli_copy(box, tmp_path):
    result = box.run("release", RADON_IB_HOLD_CLI=str(_cli_copy(tmp_path)))
    assert result.returncode == 0, result.stdout + result.stderr
    assert box.held()


@pytest.mark.parametrize("variant", ["symlink", "group-writable", "other-writable", "missing", "directory"])
def test_an_untrusted_hold_cli_is_refused_before_it_runs(box, tmp_path, variant):
    cli = _cli_copy(tmp_path)
    if variant == "symlink":
        link = tmp_path / "cli" / "link.py"
        link.symlink_to(cli)
        cli = link
    elif variant == "group-writable":
        cli.chmod(0o624)
    elif variant == "other-writable":
        cli.chmod(0o646)
    elif variant == "missing":
        cli = tmp_path / "cli" / "absent.py"
    elif variant == "directory":
        cli = tmp_path / "cli"
    result = box.run("status", RADON_IB_HOLD_CLI=str(cli))
    assert "refusing hold CLI" in result.stderr
    assert not (tmp_path / "hold.json").exists()
    release = box.run("resume", RADON_IB_HOLD_CLI=str(cli))
    assert release.returncode != 0
    assert "gateway-control start" not in box.calls()


def test_setup_stages_the_hold_cli_root_owned_beside_the_command():
    text = SETUP.read_text(encoding="utf-8")
    body = text[text.index("install_ib_hold() {"):]
    body = body[: body.index("\n}\n")]
    assert '"${RADON_DIR}/scripts/utils/ib_operator_hold.py"' in body
    assert "/usr/local/lib/radon/ib_operator_hold.py" in body
    assert body.count("stage_from_checkout") == 2


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"},
    )


@pytest.fixture
def provisioned(tmp_path: Path) -> dict[str, Path]:
    repo = tmp_path / "repo"
    (repo / "cloud" / "scripts").mkdir(parents=True)
    (repo / "scripts" / "utils").mkdir(parents=True)
    (repo / "cloud" / "scripts" / "ib-operator-hold.sh").write_bytes(SCRIPT.read_bytes())
    cli = repo / "scripts" / "utils" / "ib_operator_hold.py"
    cli.write_bytes((APP_ROOT / "scripts" / "utils" / "ib_operator_hold.py").read_bytes())
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "seed")
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    _git(repo, "push", "-q", str(remote), "HEAD:refs/heads/main")
    return {"repo": repo, "cli": cli, "remote": remote, "root": tmp_path}


def _install_ib_hold(p: dict[str, Path]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", f"set -euo pipefail\nsource {SETUP}\ninstall_ib_hold\n"],
        env={
            **os.environ,
            "RADON_SETUP_SOURCE_ONLY": "1",
            "RADON_APP_DIR": str(p["repo"]),
            "RADON_SETUP_STAGE_DIR": str(p["root"] / "stage"),
            "RADON_HELPER_SKIP_CHOWN": "1",
            "RADON_IB_HOLD_TARGET": str(p["root"] / "radon-ib-hold"),
            "RADON_IB_HOLD_CLI_TARGET": str(p["root"] / "lib" / "ib_operator_hold.py"),
            "RADON_PROVENANCE_REMOTE_URL": str(p["remote"]),
            "RADON_PROVISION_ROOT": str(p["root"] / "provision"),
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
        },
        capture_output=True, text=True, check=False, timeout=120,
    )


def test_install_ib_hold_stages_the_committed_cli(provisioned):
    result = _install_ib_hold(provisioned)
    assert result.returncode == 0, result.stdout + result.stderr
    installed = provisioned["root"] / "lib" / "ib_operator_hold.py"
    assert installed.read_bytes() == provisioned["cli"].read_bytes()
    assert installed.stat().st_mode & 0o777 == 0o644


def test_install_ib_hold_refuses_a_tampered_checkout_cli(provisioned):
    provisioned["cli"].write_text("import os\n", encoding="utf-8")
    result = _install_ib_hold(provisioned)
    assert result.returncode != 0
    assert not (provisioned["root"] / "lib" / "ib_operator_hold.py").exists()
