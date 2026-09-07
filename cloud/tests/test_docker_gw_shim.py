"""P1 contract: `radon-docker-gw`, the shim that replaces radon's group docker.

Group `docker` is root-equivalent. radon held it only to drive one container,
so the shim has to be narrower than that membership in every direction: a fixed
verb set, a pinned container, no caller-supplied paths or flags, and a compose
body root owns rather than one the caller can rewrite.
"""

from __future__ import annotations

import os
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
    docker = tmp_path / "docker"
    _write_executable(
        docker,
        f'#!/bin/bash\nprintf \'%s\\n\' "$*" >> {log}\n'
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
    env = {
        **os.environ,
        "RADON_DOCKER_GW_TEST_MODE": "1",
        "RADON_TEST_DOCKER": str(docker),
        "RADON_TEST_COMPOSE_FILE": str(compose_file),
        "RADON_TEST_COMPOSE_ENV_FILE": str(env_file),
    }

    class Box:
        def __init__(self) -> None:
            self.env = env
            self.log = log
            self.compose_file = compose_file
            self.tmp = tmp_path

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
        "IB_GATEWAY_PORT=4002\n",
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
