"""REL-234 (R-635/R-636): the compose body root executes is gated everywhere.

R-635: the content gate on /etc/radon/ib-gateway-compose.yml was four greps
whose host-bind arm matched only the literal `- /:`, hand-copied into
deploy-root-helper.sh and bootstrap-control-plane.sh. One shared
compose_body_is_valid() implementation now lives byte-for-byte identical in
both (plus setup-vps.sh), pinned here, with an explicit deny-list: absolute
host binds (quoted or not), docker.sock, long-form binds, pid namespaces,
host networking, cap_add, devices, user root, and any security_opt beyond
no-new-privileges:true.

R-636: setup-vps.sh install_docker_gw staged the radon-writable checkout's
docker-compose.yml straight to the root-owned target with no validation and
no provenance. It now installs the git blob at HEAD, refuses a working-tree
body that differs from that blob, and runs the shared validator first.

Shim half of the contract: radon-docker-gw refuses a symlinked, missing, or
group/other-writable COMPOSE_ENV_FILE with exit 78.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

CLOUD = Path(__file__).resolve().parents[1]
HELPER = CLOUD / "scripts" / "deploy-root-helper.sh"
BOOTSTRAP = CLOUD / "scripts" / "bootstrap-control-plane.sh"
SETUP = CLOUD / "scripts" / "setup-vps.sh"
SHIM = CLOUD / "scripts" / "radon-docker-gw.sh"
GOOD_BODY = (CLOUD / "docker-compose.yml").read_text(encoding="utf-8")

FUNC_RE = re.compile(r"^compose_body_is_valid\(\) \{\n.*?^\}$", re.DOTALL | re.MULTILINE)

BASE = (
    "services:\n"
    "  ib-gateway:\n"
    "    image: ghcr.io/example/ib-gateway@sha256:0000\n"
    "    container_name: ib-gateway\n"
)

POISONS = {
    "quoted-root-bind": BASE + '    volumes:\n      - "/:/host"\n',
    "etc-bind": BASE + "    volumes:\n      - /etc:/etc\n",
    "docker-sock": BASE
    + "    volumes:\n      - /var/run/docker.sock:/var/run/docker.sock\n",
    "pid-host": BASE + "    pid: host\n",
    "long-form-bind": BASE
    + "    volumes:\n      - type: bind\n        source: /var/lib\n        target: /x\n",
    "cap-add": BASE + "    cap_add:\n      - SYS_ADMIN\n",
    "devices": BASE + "    devices:\n      - /dev/mem:/dev/mem\n",
    "network-host": BASE + "    network_mode: host\n",
    "user-root": BASE + "    user: root\n",
    "security-opt": BASE + "    security_opt:\n      - seccomp:unconfined\n",
    "privileged": BASE + "    privileged: true\n",
    "commented-container-name": BASE.replace(
        "    container_name: ib-gateway\n", "    # container_name: ib-gateway\n"
    ),
}

SCRIPTS = {"helper": HELPER, "bootstrap": BOOTSTRAP, "setup-vps": SETUP}


def _function_text(script: Path) -> str:
    match = FUNC_RE.search(script.read_text(encoding="utf-8"))
    assert match, f"compose_body_is_valid() not found in {script.name}"
    return match.group(0)


def _validate(script: Path, body: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    candidate = tmp_path / "candidate.yml"
    candidate.write_text(body, encoding="utf-8")
    snippet = (
        "set -uo pipefail\n"
        + _function_text(script)
        + f'\ncompose_body_is_valid "{candidate}" test-dest\n'
    )
    return subprocess.run(
        ["bash", "-c", snippet], capture_output=True, text=True, check=False
    )


def test_the_three_copies_are_byte_identical() -> None:
    """Two hand-copied gates diverged once (R-635); pin them against each other."""
    helper = _function_text(HELPER)
    assert helper == _function_text(BOOTSTRAP)
    assert helper == _function_text(SETUP)


def test_bootstrap_compose_arm_calls_the_shared_function() -> None:
    text = BOOTSTRAP.read_text(encoding="utf-8")
    # Strip comments so a comment naming the call cannot satisfy this.
    code = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    arm = code[code.index("compose)") :]
    arm = arm[: arm.index(";;")]
    assert "compose_body_is_valid" in arm


@pytest.mark.parametrize("which", sorted(SCRIPTS))
@pytest.mark.parametrize("poison", sorted(POISONS))
def test_poisoned_bodies_are_refused_by_every_copy(
    which: str, poison: str, tmp_path: Path
) -> None:
    result = _validate(SCRIPTS[which], POISONS[poison], tmp_path)
    assert result.returncode != 0, f"{which} accepted {poison}: {result.stderr}"
    assert "compose validation failed" in result.stderr


@pytest.mark.parametrize("which", sorted(SCRIPTS))
def test_the_shipped_body_passes_every_copy(which: str, tmp_path: Path) -> None:
    result = _validate(SCRIPTS[which], GOOD_BODY, tmp_path)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("which", sorted(SCRIPTS))
def test_a_comment_naming_a_root_bind_does_not_trip_the_gate(
    which: str, tmp_path: Path
) -> None:
    body = BASE + "    # - /:/host would be an escalation; there is no bind.\n"
    result = _validate(SCRIPTS[which], body, tmp_path)
    assert result.returncode == 0, result.stderr


# -- R-636: setup-vps install_docker_gw provenance --------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"},
    ).stdout.strip()


@pytest.fixture()
def provisioned_repo(tmp_path: Path) -> dict[str, Path]:
    repo = tmp_path / "repo"
    cloud = repo / "cloud"
    (cloud / "scripts").mkdir(parents=True)
    (cloud / "docker-compose.yml").write_text(GOOD_BODY, encoding="utf-8")
    (cloud / "scripts" / "radon-docker-gw.sh").write_text(
        SHIM.read_text(encoding="utf-8"), encoding="utf-8"
    )
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "seed")
    (tmp_path / "sbin").mkdir()  # /usr/local/sbin exists on the host
    return {
        "repo": repo,
        "cloud": cloud,
        "gw_target": tmp_path / "sbin" / "radon-docker-gw",
        "compose_target": tmp_path / "etc" / "ib-gateway-compose.yml",
        "stage": tmp_path / "stage",
    }


def _run_install_docker_gw(env_paths: dict[str, Path]) -> subprocess.CompletedProcess[str]:
    shell = f"set -euo pipefail\nsource {SETUP}\ninstall_docker_gw\n"
    return subprocess.run(
        ["bash", "-c", shell],
        env={
            **os.environ,
            "RADON_SETUP_SOURCE_ONLY": "1",
            "RADON_CLOUD_DIR": str(env_paths["cloud"]),
            "RADON_SETUP_STAGE_DIR": str(env_paths["stage"]),
            "RADON_HELPER_SKIP_CHOWN": "1",
            "RADON_DOCKER_GW_TARGET": str(env_paths["gw_target"]),
            "RADON_COMPOSE_TARGET": str(env_paths["compose_target"]),
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
        },
        capture_output=True,
        text=True,
        check=False,
    )


def test_install_docker_gw_installs_the_committed_body(provisioned_repo) -> None:
    result = _run_install_docker_gw(provisioned_repo)
    assert result.returncode == 0, result.stderr + result.stdout
    installed = provisioned_repo["compose_target"].read_text(encoding="utf-8")
    assert installed == GOOD_BODY


def test_install_docker_gw_refuses_a_tampered_checkout_body(provisioned_repo) -> None:
    """The radon-writable working tree is not an input to a root install."""
    compose = provisioned_repo["cloud"] / "docker-compose.yml"
    compose.write_text(GOOD_BODY + "# tampered\n", encoding="utf-8")
    result = _run_install_docker_gw(provisioned_repo)
    assert result.returncode != 0
    assert not provisioned_repo["compose_target"].exists()


def test_install_docker_gw_validates_even_a_committed_body(provisioned_repo) -> None:
    compose = provisioned_repo["cloud"] / "docker-compose.yml"
    compose.write_text(POISONS["cap-add"], encoding="utf-8")
    _git(provisioned_repo["repo"], "add", "-A")
    _git(provisioned_repo["repo"], "commit", "-qm", "poison")
    result = _run_install_docker_gw(provisioned_repo)
    assert result.returncode != 0
    assert not provisioned_repo["compose_target"].exists()


def test_install_docker_gw_refuses_an_uncommitted_body(provisioned_repo, tmp_path) -> None:
    """A checkout that never committed the compose body has no provenance."""
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "docker-compose.yml").write_text(GOOD_BODY, encoding="utf-8")
    (bare / "scripts").mkdir()
    (bare / "scripts" / "radon-docker-gw.sh").write_text(
        SHIM.read_text(encoding="utf-8"), encoding="utf-8"
    )
    paths = dict(provisioned_repo)
    paths["cloud"] = bare
    result = _run_install_docker_gw(paths)
    assert result.returncode != 0
    assert not paths["compose_target"].exists()


# -- Shim env-file trust (contract c) ----------------------------------------


def _shim_box(tmp_path: Path) -> dict[str, object]:
    log = tmp_path / "docker.log"
    docker = tmp_path / "docker"
    docker.write_text(f"#!/bin/bash\nprintf '%s\\n' \"$*\" >> {log}\nexit 0\n")
    docker.chmod(0o755)
    compose_file = tmp_path / "ib-gateway-compose.yml"
    compose_file.write_text(GOOD_BODY, encoding="utf-8")
    env_file = tmp_path / "env"
    env_file.write_text("X=1\n", encoding="utf-8")
    env_file.chmod(0o600)
    env = {
        **os.environ,
        "RADON_DOCKER_GW_TEST_MODE": "1",
        "RADON_TEST_DOCKER": str(docker),
        "RADON_TEST_COMPOSE_FILE": str(compose_file),
        "RADON_TEST_COMPOSE_ENV_FILE": str(env_file),
    }
    return {"env": env, "env_file": env_file, "tmp": tmp_path}


def _shim_run(box: dict[str, object], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SHIM), *args],
        env=box["env"],  # type: ignore[arg-type]
        capture_output=True,
        text=True,
        check=False,
    )


def test_shim_accepts_a_private_regular_env_file(tmp_path: Path) -> None:
    box = _shim_box(tmp_path)
    assert _shim_run(box, "compose-up").returncode == 0


def test_shim_refuses_a_symlinked_env_file(tmp_path: Path) -> None:
    box = _shim_box(tmp_path)
    planted = tmp_path / "planted.env"
    planted.write_text("X=1\n", encoding="utf-8")
    box["env_file"].unlink()  # type: ignore[union-attr]
    box["env_file"].symlink_to(planted)  # type: ignore[union-attr]
    result = _shim_run(box, "compose-up")
    assert result.returncode == 78
    assert "env" in result.stderr


def test_shim_refuses_a_missing_env_file(tmp_path: Path) -> None:
    box = _shim_box(tmp_path)
    box["env_file"].unlink()  # type: ignore[union-attr]
    assert _shim_run(box, "compose-up").returncode == 78


@pytest.mark.parametrize("mode", [0o620, 0o602, 0o666])
def test_shim_refuses_a_group_or_other_writable_env_file(
    tmp_path: Path, mode: int
) -> None:
    box = _shim_box(tmp_path)
    box["env_file"].chmod(mode)  # type: ignore[union-attr]
    result = _shim_run(box, "compose-up")
    assert result.returncode == 78
    assert "writable" in result.stderr
