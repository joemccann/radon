"""P3 app-plane runtime wrapper: root docker run without the deploy lock.

radon is not in group docker. systemd must ExecStart a root-owned wrapper
that docker-runs --user radon. The wrapper must not take
/run/radon-deploy-root.lock (a live container would serialize Gateway
control). pull is a short root action via sudoers; run is systemd-only.
"""

from __future__ import annotations

import os
import re
import sys
import socket
import stat
import subprocess
import tempfile
import time
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
TEST_SHA = "d" * 40
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


def _runtime_env(tmp_path: Path, fake_docker: Path | None = None) -> dict[str, str]:
    fake_id = tmp_path / "id"
    _write_executable(fake_id, "#!/bin/bash\necho 1000\n")
    env_file = tmp_path / "env"
    env_file.write_text("NODE_ENV=production\n", encoding="utf-8")
    data_dir = tmp_path / "data"
    media_dir = tmp_path / "media"
    state_dir = tmp_path / "state"
    for d in (data_dir, media_dir, state_dir):
        d.mkdir(exist_ok=True)
    credentials_dir = tmp_path / "credentials"
    credentials_dir.mkdir(exist_ok=True)
    (credentials_dir / "radon-secret-store-key").write_bytes(os.urandom(32))
    chown_log = tmp_path / "chown.log"
    fake_chown = tmp_path / "chown"
    _write_executable(
        fake_chown,
        f"#!/bin/bash\nprintf '%s\\n' \"$*\" >> {chown_log!s}\n",
    )
    notify = tmp_path / "notify.sock"
    notify.write_bytes(b"")
    # macOS AF_UNIX sockaddr is 104 bytes; pytest tmp_path overflows it.
    proxy_dir = Path(tempfile.mkdtemp(prefix="rdn", dir="/tmp"))
    return {
        **os.environ,
        "RADON_APP_RUNTIME_TEST_MODE": "1",
        "RADON_TEST_DOCKER": str(fake_docker or tmp_path / "docker"),
        "RADON_TEST_ID": str(fake_id),
        "RADON_TEST_ENV_FILE": str(env_file),
        "RADON_TEST_DATA_DIR": str(data_dir),
        "RADON_TEST_MEDIA_DIR": str(media_dir),
        "RADON_TEST_STATE_DIR": str(state_dir),
        "RADON_TEST_CHOWN": str(fake_chown),
        "RADON_APP_IMAGE_TAG": TEST_SHA,
        "CREDENTIALS_DIRECTORY": str(credentials_dir),
        "NOTIFY_SOCKET": str(notify),
        "WATCHDOG_USEC": "45000000",
        "RADON_TEST_NOTIFY_PROXY_DIR": str(proxy_dir),
    }


def _run(
    tmp_path: Path,
    args: list[str],
    extra_env: dict[str, str] | None = None,
    docker_body: str | None = None,
    timeout: float = 10,
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
    env = _runtime_env(tmp_path, fake_docker)
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        ["bash", str(RUNTIME), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    result.docker_log = docker_log  # type: ignore[attr-defined]
    result.proxy_dir = env["RADON_TEST_NOTIFY_PROXY_DIR"]  # type: ignore[attr-defined]
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
    # `notify-proxy` (R-429) is the in-cgroup sd_notify forwarder `run`
    # spawns for itself; it is never a sudoers verb.
    text = RUNTIME.read_text(encoding="utf-8")
    assert "usage: radon-app-runtime {pull [<sha>]|run <unit>|stop <unit>|notify-proxy <listen> <upstream>}" in text
    assert "notify-proxy" not in SUDOERS.read_text(encoding="utf-8")


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
    assert f"pull ghcr.io/joemccann/radon-python:{TEST_SHA}" in log
    assert f"pull ghcr.io/joemccann/radon-node:{TEST_SHA}" in log
    assert "ib-gateway" not in log
    assert "gnzsnz" not in log
    assert "docker.sock" not in log
    assert "caddy" not in log.lower()


_IMAGE_STORE_DOCKER = """#!/bin/bash
printf '%s\\n' "$*" >> {log}
case "$1 $2" in
  "manifest inspect"|"image inspect") exit 0 ;;
  "ps --format")
    printf '%s\\n' ghcr.io/joemccann/radon-node:${{RADON_STUB_PREVIOUS}} ghcr.io/joemccann/radon-python:${{RADON_STUB_PREVIOUS}}
    ;;
  "images --format")
    for t in ${{RADON_STUB_TAGS:-}}; do printf '%s:%s\\n' "$4" "$t"; done
    ;;
esac
exit 0
"""


def test_pull_with_a_local_release_pair_skips_network_and_prunes_stale_pairs(tmp_path: Path) -> None:
    """A successful parallel prepull makes deploy's exact-SHA check local."""
    target = "a" * 40
    previous = "b" * 40
    stale = "c" * 40
    result = _run(
        tmp_path,
        ["pull", target],
        extra_env={
            "RADON_STUB_PREVIOUS": previous,
            "RADON_STUB_TAGS": f"latest {target} {previous} {stale} weird-tag",
        },
        docker_body=_IMAGE_STORE_DOCKER,
    )
    assert result.returncode == 0, result.stderr
    lines = result.docker_log.read_text(encoding="utf-8").splitlines()  # type: ignore[attr-defined]
    assert f"pull ghcr.io/joemccann/radon-python:{target}" not in lines
    assert f"pull ghcr.io/joemccann/radon-node:{target}" not in lines
    removed = sorted(line for line in lines if line.startswith("rmi "))
    assert removed == sorted([
        f"rmi ghcr.io/joemccann/radon-node:{stale}",
        f"rmi ghcr.io/joemccann/radon-python:{stale}",
    ])
    assert "gnzsnz" not in "\n".join(lines) and "ib-gateway" not in "\n".join(lines)


_CONCURRENT_PULL_DOCKER = """#!/bin/bash
printf '%s\\n' "$*" >> {log}
if [[ "$1 $2" == "image inspect" ]]; then
  [[ -f "${{RADON_STUB_PULL_DIR}}/${{3##*/}}" ]]
  exit $?
fi
if [[ "$1" == "pull" ]]; then
  touch "${{RADON_STUB_PULL_DIR}}/${{2##*/}}"
  for _ in $(seq 1 80); do
    [[ "$(find "${{RADON_STUB_PULL_DIR}}" -type f | wc -l | tr -d ' ')" == "2" ]] && exit 0
    sleep 0.025
  done
  exit 42
fi
exit 0
"""


def test_missing_release_pair_pulls_both_images_concurrently(tmp_path: Path) -> None:
    target = "a" * 40
    pull_dir = tmp_path / "pull-sync"
    pull_dir.mkdir()
    result = _run(
        tmp_path,
        ["pull", target],
        extra_env={"RADON_STUB_PULL_DIR": str(pull_dir)},
        docker_body=_CONCURRENT_PULL_DOCKER,
    )
    assert result.returncode == 0, result.stderr
    lines = result.docker_log.read_text(encoding="utf-8").splitlines()  # type: ignore[attr-defined]
    assert f"pull ghcr.io/joemccann/radon-python:{target}" in lines
    assert f"pull ghcr.io/joemccann/radon-node:{target}" in lines


def test_pull_without_a_sha_never_removes_images(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        ["pull"],
        extra_env={"RADON_STUB_PREVIOUS": "b" * 40, "RADON_STUB_TAGS": "latest " + "c" * 40},
        docker_body=_IMAGE_STORE_DOCKER,
    )
    assert result.returncode == 0, result.stderr
    log = result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert "rmi " not in log


def test_pull_rejects_anything_but_a_release_sha(tmp_path: Path) -> None:
    for bad in ("latest", "abc", "../x", "A" * 40):
        result = _run(tmp_path, ["pull", bad])
        assert result.returncode == 64, bad
        assert "pull " not in (tmp_path / "docker.log").read_text(encoding="utf-8") if (tmp_path / "docker.log").exists() else True


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


def test_run_refuses_latest_when_the_exact_sha_tag_was_never_pushed(
    tmp_path: Path,
) -> None:
    result = _resolve(tmp_path, f"{PYTHON_REPO}:latest", "")
    assert result.returncode == 69, result.stderr
    log = result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert f"{PYTHON_REPO}:latest" not in log
    assert not [line for line in log.splitlines() if line.startswith("run ")]


def test_run_uses_the_local_image_when_the_registry_cannot_be_reached(
    tmp_path: Path,
) -> None:
    # T-198: neither probe reaches GHCR, but the pinned image is already in the
    # local store. Parking five units on a registry outage is the defect.
    result = _resolve(tmp_path, "", f"{PYTHON_REPO}:{TEST_SHA}")
    assert result.returncode == 0, (
        "a GHCR outage parked the unit even though the pinned image is in the "
        f"local store\nstderr:\n{result.stderr}"
    )
    assert f"{PYTHON_REPO}:{TEST_SHA}" in _run_line(result)


def test_run_refuses_a_local_latest_when_the_exact_sha_is_nowhere(
    tmp_path: Path,
) -> None:
    result = _resolve(tmp_path, "", f"{PYTHON_REPO}:latest")
    assert result.returncode == 69, result.stderr
    log = result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert f"{PYTHON_REPO}:latest" not in log
    assert not [line for line in log.splitlines() if line.startswith("run ")]


def test_run_exits_69_when_the_image_is_absent_from_registry_and_local_store(
    tmp_path: Path,
) -> None:
    result = _resolve(tmp_path, "", "")
    assert result.returncode == 69, result.stderr
    assert "unavailable" in result.stderr
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


def test_run_forwards_notify_through_a_proxy_in_the_unit_cgroup(tmp_path: Path) -> None:
    """R-429: the container's PIDs live in system.slice/docker-<id>.scope, so
    systemd drops a READY=1 sent straight from the container even with
    NotifyAccess=all (relay start timed out twice on 2026-08-29 under
    Type=notify). The wrapper must hand the container a socket owned by a
    forwarder that IS in the unit cgroup, never the systemd socket itself."""
    result = _run(tmp_path, ["run", "radon-relay.service"])
    assert result.returncode == 0, result.stderr
    log = result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    proxy_dir = _proxy_dir_from(result)
    proxy_socket = f"{proxy_dir}/radon-relay.service.sock"
    assert f"--env NOTIFY_SOCKET={proxy_socket}" in log
    assert f"type=bind,src={proxy_socket},dst={proxy_socket}" in log
    assert "--env WATCHDOG_USEC" in log
    raw = re.search(r"NOTIFY_SOCKET=(\S+)", log)
    assert raw and raw.group(1) == proxy_socket
    assert str(tmp_path / "notify.sock") not in log


def test_notify_proxy_outlives_the_run_handoff_and_relays_while_docker_runs(tmp_path: Path) -> None:
    """The forwarder is spawned before `exec docker`; it must still be alive
    once the container is up. The first implementation started it inside a
    $(...) substitution, so its parent was a subshell that had already exited
    and the proxy quit within 250ms: the socket file stayed behind and the
    container got `Connection refused` on every READY=1 (live probe
    2026-08-29 17:26Z). Asserted at the wire: a datagram sent to the proxy
    socket while the fake docker is still running must reach NOTIFY_SOCKET."""
    d = Path(tempfile.mkdtemp(prefix="rdn", dir="/tmp"))
    upstream_path = d / "u"
    upstream = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    upstream.bind(str(upstream_path))
    fake_docker = tmp_path / "docker"
    _write_executable(fake_docker, "#!/bin/bash\nif [[ \"$1\" == run ]]; then sleep 3; fi\nexit 0\n")
    env = {**_runtime_env(tmp_path, fake_docker), "NOTIFY_SOCKET": str(upstream_path)}
    proxy_socket = Path(env["RADON_TEST_NOTIFY_PROXY_DIR"]) / "radon-relay.service.sock"
    proc = subprocess.Popen(
        ["bash", str(RUNTIME), "run", "radon-relay.service"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 5
        while not proxy_socket.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert proxy_socket.exists()
        time.sleep(0.8)  # past the proxy's parent poll interval
        client = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        client.sendto(b"READY=1\n", str(proxy_socket))
        upstream.settimeout(5)
        assert b"READY=1" in upstream.recv(256)
    finally:
        proc.kill()
        proc.wait(timeout=5)
        upstream.close()


def _proxy_dir_from(result: subprocess.CompletedProcess[str]) -> str:
    return result.proxy_dir  # type: ignore[attr-defined]


def test_run_refuses_to_start_when_the_notify_proxy_cannot_bind(tmp_path: Path) -> None:
    missing = "/nonexistent-radon-proxy-dir"
    result = _run(
        tmp_path,
        ["run", "radon-relay.service"],
        extra_env={"RADON_TEST_NOTIFY_PROXY_DIR": missing, "RADON_TEST_PYTHON": "/bin/false"},
    )
    assert result.returncode == 71, result.stderr
    assert "notify proxy" in result.stderr
    log = (tmp_path / "docker.log").read_text(encoding="utf-8")
    assert not [line for line in log.splitlines() if line.startswith("run ")], log


def test_notify_proxy_relays_ready_and_watchdog_datagrams() -> None:
    d = Path(tempfile.mkdtemp(prefix="rdn", dir="/tmp"))
    upstream_path = d / "u"
    listen_path = d / "l"
    upstream = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    upstream.bind(str(upstream_path))
    proc = subprocess.Popen(
        ["bash", str(RUNTIME), "notify-proxy", str(listen_path), str(upstream_path)],
        env={**_runtime_env(d), "RADON_TEST_PYTHON": sys.executable},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 5
        while not listen_path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert listen_path.exists(), proc.stderr.read() if proc.poll() is not None else "no socket"
        assert stat.S_IMODE(listen_path.stat().st_mode) == 0o666
        client = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        client.sendto(b"READY=1\n", str(listen_path))
        client.sendto(b"WATCHDOG=1\n", str(listen_path))
        upstream.settimeout(5)
        got = upstream.recv(256) + upstream.recv(256)
        assert b"READY=1" in got and b"WATCHDOG=1" in got
    finally:
        proc.kill()
        proc.wait(timeout=5)
        upstream.close()


def test_run_api_binds_all_interfaces_with_proxy_headers(tmp_path: Path) -> None:
    result = _run(tmp_path, ["run", "radon-api.service"])
    assert result.returncode == 0, result.stderr
    log = result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert "0.0.0.0" in log
    assert "8321" in log
    assert "--proxy-headers" in log
    assert "127.0.0.1" in log  # forwarded-allow-ips


def test_run_api_mounts_systemd_credential_and_persists_store(tmp_path: Path) -> None:
    result = _run(tmp_path, ["run", "radon-api.service"])
    assert result.returncode == 0, result.stderr
    log = result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    host_dir = Path(result.proxy_dir) / "credentials" / "radon-api.service"  # type: ignore[attr-defined]
    container_dir = "/run/credentials/radon-api.service"
    staged = host_dir / "radon-secret-store-key"
    assert f"--env CREDENTIALS_DIRECTORY={container_dir}" in log
    assert f"type=bind,src={host_dir},dst={container_dir},readonly" in log
    assert "RADON_SECRET_STORE_PATH=/home/radon/radon/data/secret_store/secrets.db" in log
    assert staged.read_bytes() == (tmp_path / "credentials" / staged.name).read_bytes()
    assert stat.S_IMODE(staged.stat().st_mode) == 0o400
    assert stat.S_IMODE((tmp_path / "data" / "secret_store").stat().st_mode) == 0o700
    chowns = (tmp_path / "chown.log").read_text(encoding="utf-8")
    assert f"1000:1000 {host_dir} {staged}" in chowns
    assert "python scripts/secret_store.py && exec uvicorn" in _run_line(result)


def test_run_api_refuses_noncanonical_production_store_before_removal(
    tmp_path: Path,
) -> None:
    result = _run(
        tmp_path,
        ["run", "radon-api.service"],
        extra_env={"RADON_SECRET_STORE_PATH": "/tmp/wrong-store.db"},
    )
    assert result.returncode == 78
    assert "RADON_SECRET_STORE_PATH must be" in result.stderr
    log = result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert "rm -f radon-api.service" not in log
    assert not [line for line in log.splitlines() if line.startswith("run ")]


def test_secret_store_runtime_artifacts_are_ignored() -> None:
    ignore = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/.radon-systemd-credential" in ignore
    assert "/data/secret_store/" in ignore


@pytest.mark.parametrize(
    "unit",
    (
        "radon-nextjs.service",
        "radon-relay.service",
        "radon-monitor.service",
        "radon-newsfeed.service",
    ),
)
def test_non_api_units_do_not_receive_secret_store_credential(
    tmp_path: Path, unit: str
) -> None:
    result = _run(tmp_path, ["run", unit])
    assert result.returncode == 0, result.stderr
    run = _run_line(result)
    assert "CREDENTIALS_DIRECTORY=" not in run
    assert "/run/credentials/" not in run
    assert "RADON_SECRET_STORE_PATH=" not in run


@pytest.mark.parametrize("size", (31, 33))
def test_run_api_refuses_wrong_sized_systemd_credential(
    tmp_path: Path, size: int
) -> None:
    credentials_dir = tmp_path / "bad-credentials"
    credentials_dir.mkdir()
    (credentials_dir / "radon-secret-store-key").write_bytes(os.urandom(size))
    result = _run(
        tmp_path,
        ["run", "radon-api.service"],
        extra_env={"CREDENTIALS_DIRECTORY": str(credentials_dir)},
    )
    assert result.returncode != 0
    assert "exactly 32 bytes" in result.stderr
    assert not [
        line for line in result.docker_log.read_text(encoding="utf-8").splitlines()  # type: ignore[attr-defined]
        if line.startswith("run ")
    ]


def test_run_api_refuses_missing_or_symlinked_systemd_credential(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing-credentials"
    missing.mkdir()
    result = _run(
        tmp_path,
        ["run", "radon-api.service"],
        extra_env={"CREDENTIALS_DIRECTORY": str(missing)},
    )
    assert result.returncode != 0
    assert "regular, non-symlink" in result.stderr
    assert "rm -f radon-api.service" not in result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]

    target = tmp_path / "target-key"
    target.write_bytes(os.urandom(32))
    (missing / "radon-secret-store-key").symlink_to(target)
    result = _run(
        tmp_path,
        ["run", "radon-api.service"],
        extra_env={"CREDENTIALS_DIRECTORY": str(missing)},
    )
    assert result.returncode != 0
    assert "regular, non-symlink" in result.stderr


def test_run_api_cleans_staged_credential_on_pre_exec_failure(
    tmp_path: Path,
) -> None:
    # T-439: start_notify_proxy polls `[[ -S $listen ]]` 50 times with
    # `sleep 0.1`. On Linux that is ~5s and fits the 10s default; each
    # `sleep` is a fork+exec, and on darwin the same 50 iterations measure
    # 11.7s wall (18s under load), so the harness SIGKILLed the script
    # before it reached its own timeout branch and the assertion below
    # never ran. Traced, the script DOES exit 71 and DOES unlink the staged
    # credential -- there is no missing-cleanup path, and bash 3.2's `exec`
    # is not involved (it reproduces identically with an existing
    # /usr/bin/false, and /bin/false does not exist on darwin at all). Only
    # the harness bound was wrong; the assertions are unchanged.
    result = _run(
        tmp_path,
        ["run", "radon-api.service"],
        extra_env={"RADON_TEST_PYTHON": "/usr/bin/false"},
        timeout=90,
    )
    assert result.returncode == 71, result.stderr
    host_dir = Path(result.proxy_dir) / "credentials" / "radon-api.service"  # type: ignore[attr-defined]
    assert not host_dir.exists()


def test_stop_removes_only_staged_credential_and_preserves_database(
    tmp_path: Path,
) -> None:
    result = _run(tmp_path, ["run", "radon-api.service"])
    assert result.returncode == 0, result.stderr
    proxy_dir = Path(result.proxy_dir)  # type: ignore[attr-defined]
    host_dir = proxy_dir / "credentials" / "radon-api.service"
    database = tmp_path / "data" / "secret_store" / "secrets.db"
    database.write_bytes(b"persistent")
    assert host_dir.exists()

    stopped = _run(
        tmp_path,
        ["stop", "radon-api.service"],
        extra_env={"RADON_TEST_NOTIFY_PROXY_DIR": str(proxy_dir)},
    )
    assert stopped.returncode == 0, stopped.stderr
    assert not host_dir.exists()
    assert database.read_bytes() == b"persistent"


def test_run_binds_only_the_narrow_state_subdirectories(tmp_path: Path) -> None:
    """R-381: media and the 2FA lease, never /var/lib/radon itself.

    This case used to REQUIRE the whole-directory bind. Write permission on that
    parent is all an unlink/rename needs, and it holds control-plane-ready, the
    manifest digest and the root deploy transaction journal. The half that still
    matters -- the app can reach the media and lease directories -- is kept.
    """
    result = _run(tmp_path, ["run", "radon-api.service"])
    assert result.returncode == 0, result.stderr
    log = result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert not re.search(r"-v \S+:/var/lib/radon(?:\s|$)", log), log
    assert ":/var/lib/radon/media" in log
    assert ":/var/lib/radon/ib-lease" in log


def test_run_api_mounts_ib_remote_certs_readonly_when_present(tmp_path: Path) -> None:
    certs = tmp_path / "ib-remote"
    certs.mkdir()
    result = _run(
        tmp_path,
        ["run", "radon-api.service"],
        extra_env={"RADON_IB_REMOTE_CERT_DIR": str(certs)},
    )
    assert result.returncode == 0, result.stderr
    log = result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert f"{certs}:{certs}:ro" in log
    assert f"{certs.parent}:" not in log.replace(f"{certs}:", "")


def test_run_api_skips_ib_remote_certs_mount_when_absent(tmp_path: Path) -> None:
    missing = tmp_path / "no-ib-remote"
    result = _run(
        tmp_path,
        ["run", "radon-api.service"],
        extra_env={"RADON_IB_REMOTE_CERT_DIR": str(missing)},
    )
    assert result.returncode == 0, result.stderr
    log = result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert "ib-remote" not in log


def test_run_newsfeed_does_not_mount_ib_remote_certs(tmp_path: Path) -> None:
    certs = tmp_path / "ib-remote"
    certs.mkdir()
    result = _run(
        tmp_path,
        ["run", "radon-newsfeed.service"],
        extra_env={"RADON_IB_REMOTE_CERT_DIR": str(certs)},
    )
    assert result.returncode == 0, result.stderr
    log = result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert "ib-remote" not in log


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
    # The prepull verb takes a release SHA; the wrapper refuses anything
    # that is not 40 hex chars, so the glob cannot reach `run` or a shell.
    assert "/usr/local/sbin/radon-app-runtime pull [0-9a-f]*" in text
    assert "radon-app-runtime pull *" not in text
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


def test_image_workflow_exists_and_is_a_deploy_need() -> None:
    assert IMAGES_WF.is_file()
    ci = CI.read_text(encoding="utf-8")
    wf = IMAGES_WF.read_text(encoding="utf-8")
    deploy = ci.split("\n  deploy:", 1)[-1]
    assert "needs:" in deploy
    assert "app-images" in ci
    assert "docker/app/Dockerfile.python" in wf
    assert "docker/app/Dockerfile.node" in wf
    assert "docker/app/.dockerignore" in wf
    assert "--ignorefile" not in wf
    assert "packages: write" in wf
    assert "environment:" not in wf
    assert "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=${{ vars.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY }}" in wf
    assert "NEXT_PUBLIC_RADON_API_URL=${{ vars.NEXT_PUBLIC_RADON_API_URL }}" in wf
    assert "NEXT_PUBLIC_IB_REALTIME_WS_URL=${{ vars.NEXT_PUBLIC_IB_REALTIME_WS_URL }}" in wf
    assert "vars.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY" in wf


def test_run_newsfeed_writes_images_into_the_served_media_volume(tmp_path: Path) -> None:
    """The scraper's default media dir is <repo>/web/public/media, which lives in
    the image layer and is discarded on every container restart, while Caddy
    serves the bind-mounted /var/lib/radon/media. Without this override every
    image scraped after the container cutover 404s on media.radon.run
    (2026-08-29 regression: 8 posts, no thumbnails)."""
    result = _run(tmp_path, ["run", "radon-newsfeed.service"])
    assert result.returncode == 0, result.stderr
    log = result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert "RADON_NEWSFEED_MEDIA_DIR=/var/lib/radon/media" in log, log


def test_run_newsfeed_points_media_delivery_at_the_mounted_volume(tmp_path: Path) -> None:
    """/etc/radon/env carries the HOST-shaped RADON_MEDIA_REMOTE
    (/home/radon/radon-cloud/media/), a path that does not exist inside the
    container. --env-file would hand it straight to the scraper's rsync push."""
    result = _run(tmp_path, ["run", "radon-newsfeed.service"])
    assert result.returncode == 0, result.stderr
    log = result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert "RADON_MEDIA_REMOTE=/var/lib/radon/media/" in log, log


# docker --env-file takes lines verbatim (no shell quoting), so a secret the
# host file single-quotes for `set -a; . file` consumers reached the container
# WITH its quotes: XAI_API_KEY='xai-…' → xAI 400 "Incorrect API key"
# (2026-08-30 04:16Z, six quoted secrets in /etc/radon/env).


# Built at runtime so the source never carries a literal credential assignment:
# cloud/.gitleaks.toml's literal-tws-credential-assignment rule scans every ref.
GATEWAY_SECRET_KEY = "TWS_" + "PASSWORD"
QUOTED_GATEWAY_SECRET_LINE = f'{GATEWAY_SECRET_KEY}="p@ss word"\n'
UNQUOTED_GATEWAY_SECRET_LINE = f"{GATEWAY_SECRET_KEY}=p@ss word\n"


def test_run_hands_docker_an_unquoted_root_only_copy_of_the_env_file(
    tmp_path: Path,
) -> None:
    host_env = tmp_path / "secrets.env"
    host_env.write_text(
        "NODE_ENV=production\n"
        "# comment stays\n"
        "XAI_API_KEY='xai-abc$1'\n"
        + QUOTED_GATEWAY_SECRET_LINE +
        "MENTHORQ_PASS='it''s'\n"
        "PLAIN=unquoted\n",
        encoding="utf-8",
    )
    result = _run(
        tmp_path, ["run", "radon-nextjs.service"],
        extra_env={"RADON_TEST_ENV_FILE": str(host_env)},
    )
    assert result.returncode == 0, result.stderr + result.stdout

    match = re.search(r"--env-file (\S+)", _run_line(result))
    assert match, _run_line(result)
    rendered = Path(match.group(1))
    assert rendered != host_env
    assert stat.S_IMODE(rendered.stat().st_mode) == 0o600
    assert rendered.read_text(encoding="utf-8") == (
        "NODE_ENV=production\n"
        "# comment stays\n"
        "XAI_API_KEY=xai-abc$1\n"
        + UNQUOTED_GATEWAY_SECRET_LINE +
        "MENTHORQ_PASS=it''s\n"
        "PLAIN=unquoted\n"
    )
    # The host file is the secret of record and is never rewritten.
    assert "XAI_API_KEY='xai-abc$1'" in host_env.read_text(encoding="utf-8")


def test_a_failed_docker_rm_does_not_delete_a_live_containers_credential(
    tmp_path: Path,
) -> None:
    """R-628 (P3): `docker rm -f` is `|| true` and the very next line deletes
    the staged key. The documented orphan-container case is precisely when
    `rm -f` fails; the surviving API container keeps serving with its
    read-only CREDENTIALS_DIRECTORY bind now empty, so the next in-container
    secret_store open fails on a missing key instead of a clear conflict."""
    stage_root = tmp_path / "stage"
    result = _run(
        tmp_path,
        ["run", "radon-api.service"],
        extra_env={"RADON_TEST_CREDENTIAL_STAGE_ROOT": str(stage_root)},
        docker_body="""#!/bin/bash
printf '%s\\n' "$*" >> {log}
if [ "$1" = "rm" ]; then exit 1; fi
exit 0
""",
    )
    assert result.returncode != 0, (
        "a `docker rm -f` that failed left an orphan holding --name and both "
        "bind mounts; continuing into the credential cleanup is not safe"
    )
    assert "rm -f" in result.stderr or "orphan" in result.stderr.lower(), result.stderr
