"""REL-087 / R-232 residual: the app container must live in its unit's cgroup.

Docker's `--cgroup-parent=system.slice` put every container PID in
`system.slice/docker-<id>.scope`, outside the unit, so systemd's stop/kill
sweep reached only the `docker run` client and the container survived as an
orphan (explicit `rm -f` reaping was the stopgap). Operator policy: Podman,
systemd native. `podman run --cgroups=split` keeps conmon and the container
under the unit's own cgroup (Delegate=yes), so `KillMode` reaches them.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_app_runtime import APP_UNITS, _run, _write_executable  # noqa: E402

CLOUD = Path(__file__).resolve().parents[1]
RUNTIME = CLOUD / "scripts" / "radon-app-runtime.sh"
DROP_INS = sorted((CLOUD / "services").glob("radon-*.service.d/runtime-container.conf"))


def _run_line(result) -> str:
    log = result.docker_log.read_text(encoding="utf-8")
    return next(line for line in log.splitlines() if line.startswith("run "))


def _podman(tmp_path: Path, args: list[str], **extra: str):
    return _run(tmp_path, args, extra_env={"RADON_TEST_ENGINE": "podman", **extra})


@pytest.mark.parametrize("unit", APP_UNITS)
def test_podman_run_places_the_container_in_the_unit_cgroup(tmp_path: Path, unit: str) -> None:
    result = _podman(tmp_path, ["run", unit])
    assert result.returncode == 0, result.stderr
    line = _run_line(result)
    assert "--cgroups=split" in line
    assert "--cgroup-parent" not in line
    assert "system.slice" not in line
    # The wrapper's in-cgroup notify proxy stays the one sd_notify path.
    assert "--sdnotify=ignore" in line
    assert "--env NOTIFY_SOCKET=" in line
    assert "--env WATCHDOG_USEC" in line
    assert "--user 1000:1000" in line
    assert "--rm" in line and f"--name {unit}" in line
    assert "--cap-drop ALL" in line and "no-new-privileges" in line
    assert f":{'d' * 40} " in line  # exact pinned release image


def test_podman_run_still_stages_the_secret_store_credential(tmp_path: Path) -> None:
    result = _podman(tmp_path, ["run", "radon-api.service"])
    assert result.returncode == 0, result.stderr
    line = _run_line(result)
    assert "--group-add 1001" in line
    assert "CREDENTIALS_DIRECTORY=/run/credentials/radon-api.service" in line
    assert "dst=/run/credentials/radon-api.service,readonly" in line


def test_docker_fallback_keeps_the_old_contract(tmp_path: Path) -> None:
    result = _run(tmp_path, ["run", "radon-relay.service"], extra_env={"RADON_TEST_ENGINE": "docker"})
    assert result.returncode == 0, result.stderr
    line = _run_line(result)
    assert "--cgroup-parent=system.slice" in line
    assert "--cgroups=split" not in line


def _legacy_docker(tmp_path: Path, surviving: bool) -> tuple[Path, Path]:
    log = tmp_path / "legacy.log"
    stub = tmp_path / "legacy-docker"
    _write_executable(
        stub,
        f"#!/bin/bash\nprintf '%s\\n' \"$*\" >> {log}\n"
        + ('[[ "$1" == rm ]] && exit 1\n' if surviving else '[[ "$1" == inspect ]] && exit 1\n')
        + "exit 0\n",
    )
    return stub, log


@pytest.mark.parametrize("verb", ["run", "stop"])
def test_podman_reaps_a_legacy_docker_container_of_the_same_unit(tmp_path: Path, verb: str) -> None:
    """Staged cutover: the first podman start must not share data/ with a
    docker-era container still running under the same name."""
    stub, log = _legacy_docker(tmp_path, surviving=False)
    result = _podman(tmp_path, [verb, "radon-relay.service"], RADON_TEST_LEGACY_DOCKER=str(stub))
    assert result.returncode == 0, result.stderr
    assert "rm -f radon-relay.service" in log.read_text()


def test_a_surviving_legacy_docker_container_blocks_the_start(tmp_path: Path) -> None:
    stub, _ = _legacy_docker(tmp_path, surviving=True)
    result = _podman(tmp_path, ["run", "radon-api.service"], RADON_TEST_LEGACY_DOCKER=str(stub))
    assert result.returncode == 75
    log = result.docker_log.read_text() if result.docker_log.exists() else ""
    assert not [line for line in log.splitlines() if line.startswith("run ")]


def test_podman_stop_removes_the_container_through_the_engine(tmp_path: Path) -> None:
    result = _podman(tmp_path, ["stop", "radon-api.service"])
    assert result.returncode == 0, result.stderr
    assert "rm -f radon-api.service" in result.docker_log.read_text()


def test_podman_pull_does_not_use_docker_only_buildx(tmp_path: Path) -> None:
    result = _podman(tmp_path, ["pull", "e" * 40])
    assert result.returncode == 0, result.stderr
    log = result.docker_log.read_text()
    assert "buildx" not in log
    assert f"pull ghcr.io/joemccann/radon-python:{'e' * 40}" in log
    assert f"pull ghcr.io/joemccann/radon-node:{'e' * 40}" in log


def test_production_prefers_podman_with_an_explicit_docker_override() -> None:
    text = RUNTIME.read_text(encoding="utf-8")
    assert "/usr/bin/podman" in text
    assert "RADON_CONTAINER_ENGINE" in text


@pytest.mark.parametrize("drop_in", DROP_INS, ids=lambda p: p.parent.name)
def test_drop_in_delegates_the_cgroup_and_signals_the_whole_tree(drop_in: Path) -> None:
    text = drop_in.read_text(encoding="utf-8")
    assert "\nDelegate=yes\n" in text
    assert "\nKillMode=mixed\n" in text
    assert "ExecStopPost=/usr/local/sbin/radon-app-runtime stop %n" in text
