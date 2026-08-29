"""P3 app-plane runtime wrapper: root docker run without the deploy lock.

radon is not in group docker. systemd must ExecStart a root-owned wrapper
that docker-runs --user radon. The wrapper must not take
/run/radon-deploy-root.lock (a live container would serialize Gateway
control). pull is a short root action via sudoers; run is systemd-only.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

CLOUD = Path(__file__).resolve().parents[1]
REPO = CLOUD.parent
RUNTIME = CLOUD / "scripts" / "radon-app-runtime.sh"
SUDOERS = CLOUD / "config" / "sudoers.d" / "radon-deploy"
HELPER = CLOUD / "scripts" / "deploy-root-helper.sh"
BOOTSTRAP = CLOUD / "scripts" / "bootstrap-control-plane.sh"
SETUP = CLOUD / "scripts" / "setup-vps.sh"
CI = REPO / ".github" / "workflows" / "ci.yml"
IMAGES_WF = REPO / ".github" / "workflows" / "app-images.yml"

APP_UNITS = (
    "radon-api.service",
    "radon-nextjs.service",
    "radon-relay.service",
    "radon-monitor.service",
    "radon-newsfeed.service",
)
FORBIDDEN_UNITS = (
    "radon-ib-gateway.service",
    "radon-health.service",
    "caddy.service",
)


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


# Stub docker that answers `manifest inspect` (registry) and `image inspect`
# (local store) independently, so resolve_image can be exercised against a
# GHCR outage without touching a real registry. Every other verb succeeds.
_SELECTIVE_DOCKER = """#!/bin/bash
printf '%s\\n' "$*" >> {log}
if [[ "$1 $2" == "manifest inspect" ]]; then
  for ok in ${{RADON_STUB_REGISTRY_TAGS:-}}; do
    [[ "$3" == "$ok" ]] && exit 0
  done
  exit 1
fi
if [[ "$1 $2" == "image inspect" ]]; then
  for ok in ${{RADON_STUB_LOCAL_TAGS:-}}; do
    [[ "$3" == "$ok" ]] && exit 0
  done
  exit 1
fi
exit 0
"""


def _run(
    tmp_path: Path,
    args: list[str],
    extra_env: dict[str, str] | None = None,
    docker_body: str | None = None,
) -> subprocess.CompletedProcess[str]:
    docker_log = tmp_path / "docker.log"
    fake_docker = tmp_path / "docker"
    # A tag named in RADON_TEST_MISSING_TAGS is absent from BOTH the registry
    # and the local store, which is how the pinned-SHA preflight and its
    # fallback to :latest are exercised. Failing only `manifest inspect` would
    # let T-198's local-store fallback resolve the missing tag and the
    # :latest fallback would never be reached; a test wanting the local store
    # to answer passes its own `docker_body`.
    _write_executable(
        fake_docker,
        (docker_body or """#!/bin/bash
printf '%s\\n' "$*" >> {log}
if [ "$2" = "inspect" ] && [ "$1" = "manifest" -o "$1" = "image" ]; then
  for missing in ${{RADON_TEST_MISSING_TAGS:-}}; do
    [ "$3" = "$missing" ] && exit 1
  done
fi
exit 0
""").format(log=repr(docker_log.as_posix())),
    )
    fake_id = tmp_path / "id"
    _write_executable(fake_id, "#!/bin/bash\necho 1000\n")
    env_file = tmp_path / "env"
    env_file.write_text("NODE_ENV=production\n", encoding="utf-8")
    data_dir = tmp_path / "data"
    media_dir = tmp_path / "media"
    state_dir = tmp_path / "state"
    data_dir.mkdir()
    media_dir.mkdir()
    state_dir.mkdir()
    notify = tmp_path / "notify.sock"
    notify.write_bytes(b"")
    env = {
        **os.environ,
        "RADON_APP_RUNTIME_TEST_MODE": "1",
        "RADON_TEST_DOCKER": str(fake_docker),
        "RADON_TEST_ID": str(fake_id),
        "RADON_TEST_ENV_FILE": str(env_file),
        "RADON_TEST_DATA_DIR": str(data_dir),
        "RADON_TEST_MEDIA_DIR": str(media_dir),
        "RADON_TEST_STATE_DIR": str(state_dir),
        "RADON_APP_IMAGE_TAG": "testsha",
        "NOTIFY_SOCKET": str(notify),
        "WATCHDOG_USEC": "45000000",
    }
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        ["bash", str(RUNTIME), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    result.docker_log = docker_log  # type: ignore[attr-defined]
    return result


def test_runtime_script_exists_and_is_executable() -> None:
    assert RUNTIME.is_file()
    assert os.access(RUNTIME, os.X_OK)
    mode = stat.S_IMODE(RUNTIME.stat().st_mode)
    assert mode & 0o111


def test_usage_is_pull_or_run_or_stop_unit() -> None:
    # `stop` is new (R-232): the container lives in
    # system.slice/docker-<id>.scope rather than the unit's cgroup, so
    # KillMode=control-group reaps only the `docker run` client and each
    # drop-in calls this verb from ExecStopPost to reap the container itself.
    text = RUNTIME.read_text(encoding="utf-8")
    assert "usage: radon-app-runtime {pull|run <unit>|stop <unit>}" in text


def test_non_root_is_refused_outside_test_mode() -> None:
    result = subprocess.run(
        ["bash", str(RUNTIME), "pull"],
        env={**os.environ, "RADON_APP_RUNTIME_TEST_MODE": "0", "EUID": "1000"},
        capture_output=True,
        text=True,
        timeout=10,
    )
    # Real EUID is the test runner; script checks EUID != 0 in non-test mode.
    if os.geteuid() != 0:
        assert result.returncode == 77
        assert "root" in result.stderr.lower()


def test_pull_pulls_only_app_images(tmp_path: Path) -> None:
    result = _run(tmp_path, ["pull"])
    assert result.returncode == 0, result.stderr
    log = result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert "pull ghcr.io/joemccann/radon-python:testsha" in log
    assert "pull ghcr.io/joemccann/radon-node:testsha" in log
    assert "ib-gateway" not in log
    assert "gnzsnz" not in log
    assert "docker.sock" not in log
    assert "caddy" not in log.lower()


def test_run_does_not_docker_pull(tmp_path: Path) -> None:
    result = _run(tmp_path, ["run", "radon-api.service"])
    assert result.returncode == 0, result.stderr
    log = result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert "pull " not in log
    assert "run " in log


# --- T-198: resolve_image must survive a GHCR outage --------------------------
# `image_available` probes the REGISTRY. Under `set -euo pipefail` a GHCR 429,
# outage or expired root credential fails both the SHA and the `latest` probe,
# resolve_image returns 69 and `image="$(python_image)"` exits — while the
# correct image is already in the local store. Restart=always +
# StartLimitBurst=5 then parks all five app units start-limit-hit. These three
# tests execute the real resolution ladder against a stub docker; the shipped
# coverage was two string greps in test_app_plane_cutover_safety.py.

PYTHON_REPO = "ghcr.io/joemccann/radon-python"


def _resolve(
    tmp_path: Path, registry_tags: str, local_tags: str
) -> subprocess.CompletedProcess[str]:
    return _run(
        tmp_path,
        ["run", "radon-api.service"],
        extra_env={
            "RADON_STUB_REGISTRY_TAGS": registry_tags,
            "RADON_STUB_LOCAL_TAGS": local_tags,
        },
        docker_body=_SELECTIVE_DOCKER,
    )


def _run_line(result: subprocess.CompletedProcess[str]) -> str:
    log = result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    lines = [line for line in log.splitlines() if line.startswith("run ")]
    assert lines, f"no docker run in log:\n{log}\nstderr:\n{result.stderr}"
    return lines[-1]


def test_run_falls_back_to_latest_when_the_sha_tag_was_never_pushed(
    tmp_path: Path,
) -> None:
    # R-234: app-images.yml cancels in-progress builds, so SHA1's tags can be
    # missing while ci.yml still deploys SHA1.
    result = _resolve(tmp_path, f"{PYTHON_REPO}:latest", "")
    assert result.returncode == 0, result.stderr
    assert f"{PYTHON_REPO}:latest" in _run_line(result)


def test_run_uses_the_local_image_when_the_registry_cannot_be_reached(
    tmp_path: Path,
) -> None:
    # T-198: neither probe reaches GHCR, but the pinned image is already in the
    # local store. Parking five units on a registry outage is the defect.
    result = _resolve(tmp_path, "", f"{PYTHON_REPO}:testsha")
    assert result.returncode == 0, (
        "a GHCR outage parked the unit even though the pinned image is in the "
        f"local store\nstderr:\n{result.stderr}"
    )
    assert f"{PYTHON_REPO}:testsha" in _run_line(result)


def test_run_falls_back_to_a_local_latest_when_the_sha_is_nowhere(
    tmp_path: Path,
) -> None:
    result = _resolve(tmp_path, "", f"{PYTHON_REPO}:latest")
    assert result.returncode == 0, result.stderr
    assert f"{PYTHON_REPO}:latest" in _run_line(result)


def test_run_exits_69_when_the_image_is_absent_from_registry_and_local_store(
    tmp_path: Path,
) -> None:
    result = _resolve(tmp_path, "", "")
    assert result.returncode == 69, result.stderr
    assert "neither" in result.stderr
    log_path = result.docker_log  # type: ignore[attr-defined]
    log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    assert not [line for line in log.splitlines() if line.startswith("run ")]


@pytest.mark.parametrize("unit", APP_UNITS)
def test_run_allowlisted_unit_uses_host_net_and_radon_user(
    tmp_path: Path, unit: str
) -> None:
    result = _run(tmp_path, ["run", unit])
    assert result.returncode == 0, result.stderr + result.stdout
    log = result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert "--network host" in log
    assert "--user 1000:1000" in log
    assert "--env-file" in log
    assert "--init" in log
    assert "--cap-drop ALL" in log
    assert "no-new-privileges" in log
    assert "--cgroupns host" in log or "--cgroupns=host" in log
    # Docker's systemd driver accepts a slice only, not a unit path.
    assert "--cgroup-parent=system.slice --env-file" in log
    assert f"system.slice/{unit}" not in log
    assert "docker.sock" not in log
    assert "--privileged" not in log
    assert "ib-gateway" not in log


def test_run_forwards_notify_socket(tmp_path: Path) -> None:
    result = _run(tmp_path, ["run", "radon-relay.service"])
    assert result.returncode == 0, result.stderr
    log = result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert "NOTIFY_SOCKET" in log
    assert "WATCHDOG_USEC" in log
    assert "type=bind" in log or "bind," in log


def test_run_api_binds_all_interfaces_with_proxy_headers(tmp_path: Path) -> None:
    result = _run(tmp_path, ["run", "radon-api.service"])
    assert result.returncode == 0, result.stderr
    log = result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert "0.0.0.0" in log
    assert "8321" in log
    assert "--proxy-headers" in log
    assert "127.0.0.1" in log  # forwarded-allow-ips


def test_run_binds_var_lib_radon_state(tmp_path: Path) -> None:
    result = _run(tmp_path, ["run", "radon-api.service"])
    assert result.returncode == 0, result.stderr
    log = result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert re.search(r"-v \S+:/var/lib/radon(?:\s|$)", log), log
    assert ":/var/lib/radon/media" in log


def test_run_newsfeed_mounts_host_playwright_browsers(tmp_path: Path) -> None:
    """Page 3e952746: container newsfeed crash-looped because Playwright
    looked up chromium_headless_shell-1217 under the image ENV
    /ms-playwright, which `bun x playwright install` never populated with
    that revision. Host deploy already caches 1217 at radon's ms-playwright
    dir. Bind that cache onto /ms-playwright so the current image can
    launch without waiting for a new GHCR tag (R-234).
    """
    result = _run(tmp_path, ["run", "radon-newsfeed.service"])
    assert result.returncode == 0, result.stderr
    log = result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    state_dir = tmp_path / "state"
    assert f"{state_dir / 'ms-playwright'}:/ms-playwright" in log, log
    assert "PLAYWRIGHT_BROWSERS_PATH=/ms-playwright" in log
    assert "--ipc host" in log
    assert "PLAYWRIGHT_CHROMIUM_SANDBOX=0" in log
    assert f"{tmp_path / 'data' / 'newsfeed-scripts'}:/home/radon/radon/scripts/newsfeed" in log, log


@pytest.mark.parametrize("unit", ("radon-api.service", "radon-monitor.service"))
def test_run_python_units_set_scripts_pythonpath(tmp_path: Path, unit: str) -> None:
    result = _run(tmp_path, ["run", unit])
    assert result.returncode == 0, result.stderr
    log = result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert "PYTHONPATH=/home/radon/radon/scripts" in log


@pytest.mark.parametrize("unit", FORBIDDEN_UNITS)
def test_run_refuses_gateway_health_caddy(tmp_path: Path, unit: str) -> None:
    result = _run(tmp_path, ["run", unit])
    assert result.returncode == 64
    log_path = result.docker_log  # type: ignore[attr-defined]
    log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    assert log == ""


def test_run_refuses_unknown_unit(tmp_path: Path) -> None:
    result = _run(tmp_path, ["run", "radon-drift-audit.service"])
    assert result.returncode == 64


def test_runtime_source_never_mentions_docker_sock() -> None:
    text = RUNTIME.read_text(encoding="utf-8")
    assert "docker.sock" not in text
    assert "flock" not in text
    assert "/run/radon-deploy-root.lock" not in text


def test_helper_does_not_own_run_or_pull() -> None:
    helper = HELPER.read_text(encoding="utf-8")
    assert "run-app" not in helper
    assert "pull-app" not in helper
    usage = [line for line in helper.splitlines() if line.startswith("  echo \"usage:")]
    assert usage
    assert "run-app" not in usage[0]


def test_sudoers_grants_exact_pull_not_run() -> None:
    text = SUDOERS.read_text(encoding="utf-8")
    assert "/usr/local/sbin/radon-app-runtime pull" in text
    assert "radon-app-runtime run" not in text
    assert "radon-app-runtime *" not in text
    assert "docker.sock" not in text


def test_bootstrap_installs_runtime_wrapper_not_dropins() -> None:
    text = BOOTSTRAP.read_text(encoding="utf-8")
    assert "scripts/radon-app-runtime.sh" in text
    assert "/usr/local/sbin/radon-app-runtime" in text
    assert "runtime-container.conf.example" not in text


def test_setup_vps_installs_runtime_wrapper() -> None:
    text = SETUP.read_text(encoding="utf-8")
    assert "install_app_runtime" in text
    assert "radon-app-runtime.sh" in text
    assert "runtime-container.conf" not in _function_body_setup(text)


def _function_body_setup(script: str) -> str:
    import re

    match = re.search(
        r"^install_fleet_dropin\(\)\s*\{\s*\n(.+?)\n\}\s*$",
        script,
        re.MULTILINE | re.DOTALL,
    )
    assert match
    return match.group(1)


def test_image_workflow_exists_and_is_not_a_deploy_need() -> None:
    assert IMAGES_WF.is_file()
    ci = CI.read_text(encoding="utf-8")
    wf = IMAGES_WF.read_text(encoding="utf-8")
    deploy = ci.split("\n  deploy:", 1)[-1]
    assert "needs:" in deploy
    assert "app-images" not in ci
    assert "docker/app/Dockerfile.python" in wf
    assert "docker/app/Dockerfile.node" in wf
    assert "docker/app/.dockerignore" in wf
    assert "--ignorefile" not in wf
    assert "packages: write" in wf
    assert "environment:" not in wf
    assert "--build-arg NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY" in wf
    assert "--build-arg NEXT_PUBLIC_RADON_API_URL" in wf
    assert "--build-arg NEXT_PUBLIC_IB_REALTIME_WS_URL" in wf
    assert "vars.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY" in wf
