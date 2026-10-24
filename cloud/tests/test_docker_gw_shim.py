"""P1 contract: `radon-docker-gw`, the shim that replaces radon's group docker.

Group `docker` is root-equivalent. radon held it only to drive one container,
so the shim has to be narrower than that membership in every direction: a fixed
verb set, a pinned container, no caller-supplied paths or flags, and a compose
body root owns rather than one the caller can rewrite.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

CLOUD = Path(__file__).resolve().parents[1]
SHIM = CLOUD / "scripts" / "radon-docker-gw.sh"
GW_CONTROL = CLOUD / "scripts" / "ib-gateway-control.sh"
SETUP = CLOUD / "scripts" / "setup-vps.sh"
OPS_SUDOERS = CLOUD / "config" / "sudoers.d" / "radon-ops"

VERBS = (
    "compose-up",
    "compose-down",
    "config-check",
    "inspect-running",
    "pgrep-jvm",
    "pgrep-java",
    "logs",
    "stats",
    "ps",
)


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture()
def box(tmp_path: Path):
    log = tmp_path / "docker.log"
    env_log = tmp_path / "docker-env.log"
    docker = tmp_path / "docker"
    _write_executable(
        docker,
        f'#!/bin/bash\nprintf \'%s\\n\' "$*" >> {log}\n'
        f'printf \'%s\\n\' "${{RADON_COMPOSE_ENV_FILE:-unset}}" >> {env_log}\n'
        'if [[ "${RADON_STUB_DOCKER_RC:-0}" != "0" ]]; then\n'
        '  echo "Error: No such object: ib-gateway" >&2\n'
        '  exit "${RADON_STUB_DOCKER_RC}"\n'
        "fi\n"
        'if [[ "$1" == "inspect" ]]; then echo true; fi\n'
        "exit 0\n",
    )
    compose_file = tmp_path / "ib-gateway-compose.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    env_file = tmp_path / "env"
    env_file.write_text("X=1\n", encoding="utf-8")
    gateway_env = tmp_path / "ib-gateway" / "gateway.env"
    gateway_env.parent.mkdir(mode=0o700)
    gateway_env.write_text("TWS_PASSWORD='x'\n", encoding="utf-8")
    gateway_env.chmod(0o600)
    env = {
        **os.environ,
        "RADON_DOCKER_GW_TEST_MODE": "1",
        "RADON_TEST_DOCKER": str(docker),
        "RADON_TEST_COMPOSE_FILE": str(compose_file),
        "RADON_TEST_COMPOSE_ENV_FILE": str(env_file),
        "RADON_TEST_GATEWAY_ENV_FILE": str(gateway_env),
        "RADON_TEST_OPERATOR_HOLD_PATH": str(tmp_path / "ib-operator-hold.json"),
    }

    class Box:
        def __init__(self) -> None:
            self.env = env
            self.log = log
            self.env_log = env_log
            self.env_file = env_file
            self.gateway_env = gateway_env
            self.compose_file = compose_file
            self.tmp = tmp_path
            self.hold = tmp_path / "ib-operator-hold.json"

        def run(self, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["bash", str(SHIM), *args],
                env={**env, **extra},
                text=True,
                capture_output=True,
                check=False,
            )

    return Box()


@pytest.mark.parametrize("verb", VERBS)
def test_every_verb_pins_the_gateway_container(box, verb: str) -> None:
    result = box.run(verb)
    assert result.returncode == 0, result.stderr
    logged = box.log.read_text(encoding="utf-8")
    assert "ib-gateway" in logged or "--project-name cloud" in logged


def test_compose_pins_file_project_and_env(box) -> None:
    assert box.run("compose-up").returncode == 0
    line = box.log.read_text(encoding="utf-8").strip()
    assert line.startswith("compose ")
    assert f"-f {box.compose_file}" in line
    assert "--project-name cloud" in line
    assert line.endswith("up -d")


def test_config_check_renders_the_pinned_body_and_env(box) -> None:
    """deploy.sh's preflight render, without a caller-supplied env path."""
    assert box.run("config-check").returncode == 0
    line = box.log.read_text(encoding="utf-8").strip()
    assert f"-f {box.compose_file}" in line
    assert line.endswith("config --quiet")

    assert box.run("config-check", "/tmp/somewhere.env").returncode == 64


def test_compose_down_is_the_only_other_lifecycle_verb(box) -> None:
    assert box.run("compose-down").returncode == 0
    assert box.log.read_text(encoding="utf-8").strip().endswith("down")


def test_inspect_running_passes_through_stderr_and_exit_code(box) -> None:
    """gateway_state() tells `missing` from `unknown` on these exact bytes."""
    result = box.run("inspect-running", RADON_STUB_DOCKER_RC="1")
    assert result.returncode == 1
    assert "No such object" in result.stderr


def test_thread_dump_requires_a_numeric_pid(box) -> None:
    assert box.run("thread-dump", "1770100").returncode == 0
    assert "kill -3 1770100" in box.log.read_text(encoding="utf-8")

    for bad in ("; rm -rf /", "$(id)", "-1", "", "12a"):
        result = box.run("thread-dump", bad)
        assert result.returncode == 64, bad


@pytest.mark.parametrize(
    "argv",
    [
        ("run", "--rm", "-v", "/:/host", "alpine"),
        ("compose-up", "--build"),
        ("logs", "--follow"),
        ("inspect-running", "other-container"),
        ("exec",),
        (),
    ],
)
def test_arbitrary_argv_is_refused(box, argv) -> None:
    result = box.run(*argv)
    assert result.returncode == 64, result.stdout
    assert not box.log.exists() or box.log.read_text(encoding="utf-8") == ""


def test_a_symlinked_compose_body_is_refused(box) -> None:
    """The whole point: root must not act on a file the caller can swap."""
    planted = box.tmp / "planted.yml"
    planted.write_text("services: {}\n", encoding="utf-8")
    box.compose_file.unlink()
    box.compose_file.symlink_to(planted)

    result = box.run("compose-up")

    assert result.returncode == 78, result.stdout
    assert "non-symlink" in result.stderr
    assert not box.log.exists() or box.log.read_text(encoding="utf-8") == ""


def test_a_missing_compose_body_is_refused(box) -> None:
    box.compose_file.unlink()
    result = box.run("compose-down")
    assert result.returncode == 78
    assert not box.log.exists() or box.log.read_text(encoding="utf-8") == ""


# --- Gateway env split: the container gets only gateway.env ----------------
# /etc/radon/env carries every app secret on the broker. compose-up/down hand
# the container /etc/radon/ib-gateway/gateway.env (env_file via
# RADON_COMPOSE_ENV_FILE) and keep /etc/radon/env only for interpolation.


def test_compose_up_uses_gateway_env_file_for_container_and_root_env_for_interpolation(box) -> None:
    assert box.run("compose-up").returncode == 0
    line = box.log.read_text(encoding="utf-8").strip()
    assert f"--env-file {box.env_file}" in line
    assert box.env_log.read_text(encoding="utf-8").strip() == str(box.gateway_env)


def test_compose_down_uses_gateway_env_file(box) -> None:
    assert box.run("compose-down").returncode == 0
    assert box.env_log.read_text(encoding="utf-8").strip() == str(box.gateway_env)
    assert f"--env-file {box.env_file}" in box.log.read_text(encoding="utf-8")


def test_compose_up_refuses_missing_gateway_env_after_cutover(box) -> None:
    """Once /etc/radon/env no longer carries TWS_PASSWORD (prune-root-env ran),
    a missing gateway.env is refused: the fallback would start a Gateway
    with no password, which is a failed IBKR login."""
    box.gateway_env.unlink()
    result = box.run("compose-up")
    assert result.returncode == 78, result.stderr
    assert "gateway.env" in result.stderr
    assert not box.log.exists() or box.log.read_text(encoding="utf-8") == ""


# The unattended release controller syncs the broker control plane within
# minutes of a main deploy, before anyone runs `migrate`. Until then the
# Gateway must keep its pre-split behaviour (env_file = /etc/radon/env), or
# every watchdog, admin and boot start path exits 78 and a crashed Gateway
# stays down.


@pytest.mark.parametrize("verb", ["compose-up", "compose-down"])
def test_missing_gateway_env_before_cutover_falls_back_to_root_env(box, verb: str) -> None:
    box.gateway_env.unlink()
    box.env_file.write_text("X=1\nTWS_PASSWORD='pw-sentinel-7Q'\n", encoding="utf-8")
    result = box.run(verb)
    assert result.returncode == 0, result.stderr
    assert box.env_log.read_text(encoding="utf-8").strip() == str(box.env_file)
    assert "radon-ib-gateway-credential migrate" in result.stderr
    assert "WARNING" in result.stderr
    assert "pw-sentinel-7Q" not in result.stderr


def test_fallback_never_follows_a_dangling_gateway_env_symlink(box) -> None:
    box.gateway_env.unlink()
    box.gateway_env.symlink_to(box.tmp / "absent.env")
    box.env_file.write_text("TWS_PASSWORD='old'\n", encoding="utf-8")
    assert box.run("compose-up").returncode == 78
    assert not box.log.exists() or box.log.read_text(encoding="utf-8") == ""


def test_compose_up_refuses_symlink_gateway_env(box) -> None:
    planted = box.tmp / "planted.env"
    planted.write_text("TWS_PASSWORD='x'\n", encoding="utf-8")
    planted.chmod(0o600)
    box.gateway_env.unlink()
    box.gateway_env.symlink_to(planted)
    assert box.run("compose-up").returncode == 78
    assert not box.log.exists() or box.log.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o660, 0o644])
def test_compose_up_refuses_group_readable_gateway_env(box, mode: int) -> None:
    box.gateway_env.chmod(mode)
    result = box.run("compose-up")
    assert result.returncode == 78, result.stderr
    assert not box.log.exists() or box.log.read_text(encoding="utf-8") == ""


def test_config_check_needs_no_gateway_env(box) -> None:
    """App-host deploy preflight renders with /etc/radon/env alone."""
    box.gateway_env.unlink()
    assert box.run("config-check").returncode == 0
    assert box.env_log.read_text(encoding="utf-8").strip() == str(box.env_file)


def test_production_gateway_env_path_is_pinned() -> None:
    text = SHIM.read_text(encoding="utf-8")
    assert "GATEWAY_ENV_FILE=/etc/radon/ib-gateway/gateway.env" in text


# --- operator hold (2026-09-25: the Gateway kept reclaiming the operator's
# IBKR session). The shim is the lowest layer every start funnels through, so
# it refuses compose-up while the hold is set, whatever the caller. ---------


@pytest.mark.parametrize(
    "flag",
    [
        '{"held": true, "reason": "operator web login"}\n',
        "garbage",
        "",
        '{ "held": false }',
    ],
)
def test_compose_up_is_refused_while_the_operator_hold_is_set(box, flag) -> None:
    box.hold.write_text(flag, encoding="utf-8")
    result = box.run("compose-up")
    assert result.returncode == 73, result.stderr
    assert "operator hold" in result.stderr
    assert not box.log.exists() or box.log.read_text(encoding="utf-8") == ""


def test_a_symlinked_hold_flag_counts_as_held(box) -> None:
    planted = box.tmp / "planted-hold.json"
    planted.write_text('{"held": false}\n', encoding="utf-8")
    box.hold.symlink_to(planted)
    assert box.run("compose-up").returncode == 73


def test_a_cleared_hold_lets_compose_up_through(box) -> None:
    box.hold.write_text('{"held": false, "actor": "ssh:test"}\n', encoding="utf-8")
    assert box.run("compose-up").returncode == 0
    assert box.log.read_text(encoding="utf-8").strip().endswith("up -d")


def test_release_verbs_stay_open_while_held(box) -> None:
    box.hold.write_text('{"held": true}\n', encoding="utf-8")
    assert box.run("compose-down").returncode == 0
    assert box.run("kill").returncode == 0
    assert box.log.read_text(encoding="utf-8").splitlines()[-1] == "kill ib-gateway"


def test_kill_is_root_only_and_not_granted_to_radon() -> None:
    assert "radon-docker-gw kill" not in OPS_SUDOERS.read_text(encoding="utf-8")


# --- wiring ---------------------------------------------------------------


def test_gateway_control_no_longer_calls_docker_directly() -> None:
    """Never assert structure over a comment that quotes the code it explains."""
    text = GW_CONTROL.read_text(encoding="utf-8")
    code = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    assert "/usr/local/sbin/radon-docker-gw" in code
    assert "/usr/bin/docker" not in code
    assert "docker-compose.yml" not in code


def test_setup_never_adds_radon_to_group_docker() -> None:
    text = SETUP.read_text(encoding="utf-8")
    assert "usermod -aG docker radon" not in text
    assert "gpasswd -d radon docker" in text


def test_sudoers_lists_each_verb_without_a_wildcard() -> None:
    text = OPS_SUDOERS.read_text(encoding="utf-8")
    for verb in VERBS:
        assert f"/usr/local/sbin/radon-docker-gw {verb}" in text
    assert "/usr/local/sbin/radon-docker-gw *" not in text
    assert "radon ALL=(root) NOPASSWD: /usr/bin/docker compose" not in text


def test_sudoers_grants_no_host_lifecycle() -> None:
    """radon must not hold passwordless reboot, shutdown, poweroff, or halt."""
    grants = "\n".join(
        line
        for line in OPS_SUDOERS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    for command in ("reboot", "shutdown", "poweroff", "halt"):
        assert command not in grants, (
            f"radon-ops must not grant {command} to radon"
        )
    visudo = shutil.which("visudo")
    if visudo is None:
        return
    checked = subprocess.run(
        [visudo, "-cf", str(OPS_SUDOERS)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert checked.returncode == 0, checked.stderr or checked.stdout


# --- deploy preflight executes, not greps (T-443) ---------------------------
#
# The former tests here byte-offset-compared deploy.sh's source (`shim_at <
# direct_at < fail_at`, `compose_check_ok=1` counts). These run preflight_env()
# instead: a fake `sudo -n` that refuses the shim verb reproduces the exact
# deadlock scenario (deploy.sh arrives from the new release BEFORE
# refresh-control-plane installs the sudoers verb), and the assertions are on
# what actually executed -- the direct render's full argv, the countable
# fallback line, and the shim-covered short circuit.

DEPLOY = CLOUD / "scripts" / "deploy.sh"
FALLBACK_MARKER = "direct render covered"


def _run_preflight_env(tmp_path: Path, *, sudo_grants: bool, docker_exit: int = 0):
    env_file = tmp_path / "env"
    env_file.write_text(
        "GATEWAY_MODE=cloud\n"
        "IB_GATEWAY_MODE=cloud\n"
        "RADON_MODE=hetzner\n"
        "NODE_ENV=production\n"
        "RADON_HOST_ROLE=combined\n"
        "IB_GATEWAY_HOST=127.0.0.1\n"
        "TRADING_MODE=paper\n"
        "IB_GATEWAY_PORT=4002\n"
        # A PRODUCTION_INVARIANT since the 2026-09-06 security merge
        # (cloud/scripts/check-env.py). Without it the preflight fails at the
        # env check and never reaches the compose render these tests assert on.
        "RADON_REQUIRE_OPERATOR_ALLOWLIST=1\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    required = tmp_path / "required-env.txt"
    required.write_text("", encoding="utf-8")

    bindir = tmp_path / "bin"
    bindir.mkdir()
    sudo_log = tmp_path / "sudo.log"
    shim_log = tmp_path / "shim.log"
    docker_log = tmp_path / "docker.log"
    shim = tmp_path / "radon-docker-gw"
    _write_executable(
        shim, f"#!/bin/bash\nprintf '%s\\n' \"$*\" >> '{shim_log}'\nexit 0\n"
    )
    if sudo_grants:
        # sudo -n <shim> config-check -> strip flags, run the shim directly.
        sudo_tail = 'while [[ "$1" == -* ]]; do shift; done\nexec "$@"\n'
    else:
        # The sudoers verb is not installed yet: non-interactive sudo refuses.
        sudo_tail = 'echo "sudo: a password is required" >&2\nexit 1\n'
    _write_executable(
        bindir / "sudo",
        "#!/bin/bash\n" + f"printf '%s\\n' \"$*\" >> '{sudo_log}'\n" + sudo_tail,
    )
    _write_executable(
        bindir / "docker",
        f"#!/bin/bash\nprintf '%s\\n' \"$*\" >> '{docker_log}'\nexit {docker_exit}\n",
    )

    command = f"""
set -uo pipefail
export PATH='{bindir}':"$PATH"
export RADON_DEPLOY_ENV_FILE='{env_file}'
export RADON_REQUIRED_ENV_FILE='{required}'
export RADON_ENV_CHECKER_PYTHON='{sys.executable}'
export RADON_DOCKER_GW='{shim}'
export RADON_CLOUD_DIR='{CLOUD}'
source '{DEPLOY}'
preflight_env
"""
    result = subprocess.run(
        ["bash", "-c", command], capture_output=True, text=True, timeout=60
    )
    return result, {
        "env_file": env_file,
        "sudo_log": sudo_log,
        "shim_log": shim_log,
        "docker_log": docker_log,
    }


def test_preflight_direct_render_executes_when_sudo_refuses_the_shim_verb(
    tmp_path: Path,
) -> None:
    result, logs = _run_preflight_env(tmp_path, sudo_grants=False)
    combined = result.stdout + result.stderr

    assert result.returncode == 0, combined
    sudo_line = logs["sudo_log"].read_text(encoding="utf-8").strip()
    assert sudo_line.startswith("-n ")
    assert sudo_line.endswith(" config-check")
    assert not logs["shim_log"].exists()
    docker_line = logs["docker_log"].read_text(encoding="utf-8").strip()
    assert docker_line == f"compose --env-file {logs['env_file']} config --quiet"
    assert combined.count(FALLBACK_MARKER) == 1, combined
    assert "shim refused (exit 1)" in combined


def test_preflight_fails_only_when_neither_path_renders(tmp_path: Path) -> None:
    result, logs = _run_preflight_env(tmp_path, sudo_grants=False, docker_exit=1)
    combined = result.stdout + result.stderr

    assert result.returncode != 0
    assert "docker compose config validation failed" in combined
    assert FALLBACK_MARKER not in combined
    assert "config --quiet" in logs["docker_log"].read_text(encoding="utf-8")


def test_preflight_skips_the_direct_render_when_the_shim_covers(
    tmp_path: Path,
) -> None:
    result, logs = _run_preflight_env(tmp_path, sudo_grants=True)
    combined = result.stdout + result.stderr

    assert result.returncode == 0, combined
    assert logs["shim_log"].read_text(encoding="utf-8").splitlines() == ["config-check"]
    assert not logs["docker_log"].exists()
    assert FALLBACK_MARKER not in combined
