"""P1 contract: `radon-docker-gw`, the shim that replaces radon's group docker.

Group `docker` is root-equivalent. radon held it only to drive one container,
so the shim has to be narrower than that membership in every direction: a fixed
verb set, a pinned container, no caller-supplied paths or flags, and a compose
body root owns rather than one the caller can rewrite.
"""

from __future__ import annotations

import os
import subprocess
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
