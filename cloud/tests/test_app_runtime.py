"""P3 app-plane runtime wrapper: root docker run without the deploy lock.

radon is not in group docker. systemd must ExecStart a root-owned wrapper
that docker-runs --user radon. The wrapper must not take
/run/radon-deploy-root.lock (a live container would serialize Gateway
control). pull is a short root action via sudoers; run is systemd-only.
"""

from __future__ import annotations

import functools
import json
import os
import re
import sys
import socket
import shlex
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
    "radon-research.service",
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


# --- T-452: wall-clock budgets scale with measured host contention ------------
# Fixed budgets in this file were sized on an idle darwin host; 2026-09-04
# recorded a 9.5x contention factor under sibling loops (~170s worst case for
# the run that carried a fixed 90s bound). The runtime's slow path is
# start_notify_proxy's 50-iteration `sleep 0.1` poll, where every iteration is
# a fork+exec. Calibrate that cost on this host at first use and scale each
# budget by it: the floors keep the idle-host bounds unchanged and the ceiling
# keeps a genuine hang red.

PROXY_POLL_ITERATIONS = 50  # start_notify_proxy's `seq 1 50`
PROXY_POLL_INTERVAL = 0.1  # start_notify_proxy's `sleep 0.1`
PROXY_PARENT_POLL_INTERVAL = 0.25  # cmd_notify_proxy's inbound.settimeout
BUDGET_CEILING = 540.0


@functools.cache
def _forked_sleep_seconds() -> float:
    samples = 5
    start = time.monotonic()
    subprocess.run(
        ["bash", "-c", f"for _ in $(seq 1 {samples}); do sleep {PROXY_POLL_INTERVAL}; done"],
        check=True,
        timeout=120,
    )
    return (time.monotonic() - start) / samples


def _contention_budget(nominal: float, floor: float) -> float:
    factor = max(1.0, _forked_sleep_seconds() / PROXY_POLL_INTERVAL)
    return min(max(nominal * factor * 2, floor), BUDGET_CEILING)


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
    # `id -nG radon` must answer with real group NAMES so the runtime can
    # refuse to start when the radon account has been added to the
    # credential-delivery group. Every other form still answers 1000.
    _write_executable(
        fake_id,
        "#!/bin/bash\n"
        'if [[ "$1" == "-nG" ]]; then\n'
        '  echo "${RADON_TEST_RADON_GROUPS:-radon docker}"\n'
        "  exit 0\n"
        "fi\n"
        "echo 1000\n",
    )
    fake_getent = tmp_path / "getent"
    _write_executable(
        fake_getent,
        "#!/bin/bash\n"
        'if [[ "${RADON_TEST_SECRET_GROUP_MISSING:-0}" == "1" ]]; then exit 2; fi\n'
        'echo "$2:x:${RADON_TEST_SECRET_GROUP_GID:-1001}:"\n',
    )
    env_file = tmp_path / "env"
    env_file.write_text("NODE_ENV=production\n", encoding="utf-8")
    data_dir = tmp_path / "data"
    media_dir = tmp_path / "media"
    state_dir = tmp_path / "state"
    for d in (data_dir, media_dir, state_dir):
        d.mkdir(exist_ok=True)
    # Newsfeed overlay bind sources must exist as real, owned directories.
    (state_dir / "ms-playwright").mkdir(exist_ok=True)
    (data_dir / "newsfeed-scripts").mkdir(exist_ok=True)
    (state_dir / "deploy.lock").touch()
    (state_dir / "last-green").write_text("b" * 40 + "\n")
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
        "RADON_TEST_GETENT": str(fake_getent),
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
    stdin=None,
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
        stdin=stdin,
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
    assert "usage: radon-app-runtime {pull [<sha>]|prune|prune-locked [<sha>]|check-model-space|install-model-clients|recover-model-clients|commit-model-clients|run <unit>|halt <unit> <grace-seconds>|stop <unit>|notify-proxy <listen> <upstream>}" in text
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
  "manifest inspect") exit 0 ;;
  "buildx imagetools") printf '{{"digest":"sha256:%064d"}}\\n' 0 ;;
  "image inspect")
    if [[ "$4" == "--format" ]]; then
      ref="$3"
      printf '["%s@sha256:%064d"]\\n' "${{ref%:*}}" 0
    fi
    ;;
  "ps --format")
    printf '%s\\n' ghcr.io/joemccann/radon-node:${{RADON_STUB_PREVIOUS}} ghcr.io/joemccann/radon-python:${{RADON_STUB_PREVIOUS}}
    ;;
  "images --format")
    for t in ${{RADON_STUB_TAGS:-}}; do printf '%s:%s\\n' "$4" "$t"; done
    ;;
esac
exit 0
"""


def test_pull_with_matching_digests_skips_layer_pulls_and_prunes_stale_pairs(tmp_path: Path) -> None:
    """Matching registry digests preserve cached layers and remove only stale releases."""
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
        f"rmi ghcr.io/joemccann/radon-model-gateway:{stale}",
    ])
    assert "gnzsnz" not in "\n".join(lines) and "ib-gateway" not in "\n".join(lines)


# Post-deploy prune (no argument). The stub reports running images from
# RADON_STUB_RUNNING, local tags from RADON_STUB_TAGS, and fails `rmi` for any
# ref listed in RADON_STUB_RMI_FAIL.
_PRUNE_DOCKER = """#!/bin/bash
printf '%s\\n' "$*" >> {log}
case "$1 $2" in
  "ps --format")
    for image in ${{RADON_STUB_RUNNING:-}}; do printf '%s\\n' "$image"; done
    ;;
  "images --format")
    for t in ${{RADON_STUB_TAGS:-}}; do printf '%s:%s\\n' "$4" "$t"; done
    ;;
esac
if [[ "$1" == "rmi" ]]; then
  for bad in ${{RADON_STUB_RMI_FAIL:-}}; do [[ "$2" == "$bad" ]] && exit 2; done
fi
exit 0
"""

_NODE = "ghcr.io/joemccann/radon-node"
_PY = "ghcr.io/joemccann/radon-python"
_MODEL = "ghcr.io/joemccann/radon-model-gateway"
_CUR, _PREV, _STALE1, _STALE2 = "a" * 40, "b" * 40, "c" * 40, "e" * 40


def _prune(tmp_path: Path, *, green: str | None, tags: str, running: str | None = None,
           journal: str | None = None, extra: dict[str, str] | None = None):
    # Isolated marker and journal paths: _runtime_env rewrites state/last-green.
    marker = tmp_path / "prune-green"
    journal_file = tmp_path / "prune-transition.json"
    marker.unlink(missing_ok=True)
    journal_file.unlink(missing_ok=True)
    if green is not None:
        marker.write_text(green)
    if journal is not None:
        journal_file.write_text(journal)
    log = tmp_path / "docker.log"
    log.unlink(missing_ok=True)
    result = _run(
        tmp_path,
        ["prune"],
        extra_env={
            "RADON_TEST_GREEN_MARKER": str(marker),
            "RADON_TEST_TRANSITION_JOURNAL": str(journal_file),
            "RADON_STUB_TAGS": tags,
            "RADON_STUB_RUNNING": running if running is not None else f"{_NODE}:{_CUR} {_PY}:{_CUR}",
            **(extra or {}),
        },
        docker_body=_PRUNE_DOCKER,
    )
    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return result, sorted(line for line in lines if line.startswith("rmi "))


def _rmi(*shas: str) -> list[str]:
    return sorted(f"rmi {repo}:{sha}" for sha in shas for repo in (_NODE, _PY, _MODEL))


def test_prune_keeps_running_and_previous_green_and_removes_every_stale_tag(tmp_path: Path) -> None:
    result, removed = _prune(
        tmp_path,
        green=f"{_CUR}\n{_PREV}\n",
        tags=f"latest {_CUR} {_PREV} {_STALE1} {_STALE2} weird-tag",
    )
    assert result.returncode == 0, result.stderr
    # Two stale tags (possibly on one image) each get their own rmi, so the
    # image is gone once its last stale tag is removed.
    assert removed == _rmi(_STALE1, _STALE2)


def test_prune_never_removes_an_image_a_running_container_uses(tmp_path: Path) -> None:
    result, removed = _prune(
        tmp_path,
        green=f"{_CUR}\n{_PREV}\n",
        tags=f"{_CUR} {_PREV} {_STALE1}",
        running=f"{_NODE}:{_STALE1} {_PY}:{_CUR}",
    )
    assert result.returncode == 0, result.stderr
    assert removed == sorted([f"rmi {_PY}:{_STALE1}", f"rmi {_MODEL}:{_STALE1}"])


def test_prune_removes_nothing_while_the_deploy_lock_is_held(tmp_path: Path) -> None:
    import fcntl
    _runtime_env(tmp_path)
    lock = tmp_path / "state" / "deploy.lock"
    with open(lock, "r+") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result, removed = _prune(tmp_path, green=f"{_CUR}\n{_PREV}\n", tags=f"{_CUR} {_PREV} {_STALE1}")
    assert result.returncode == 0, result.stderr
    assert removed == []
    assert "BlockingIOError" in result.stderr


def test_prune_skips_without_a_recorded_previous_release(tmp_path: Path) -> None:
    for green in (f"{_CUR}\n", f"{_CUR}\nnot-a-sha\n", None):
        result, removed = _prune(tmp_path, green=green, tags=f"{_CUR} {_PREV} {_STALE1}")
        assert result.returncode == 0, result.stderr
        assert removed == [], green
        assert "image prune skipped" in result.stderr


def test_prune_with_a_pending_transition_protects_both_journal_shas(tmp_path: Path) -> None:
    journal = json.dumps({"version": 1, "requested_sha": _STALE1, "previous_sha": _PREV})
    result, removed = _prune(
        tmp_path,
        green=f"{_CUR}\n",
        tags=f"{_CUR} {_PREV} {_STALE1} {_STALE2}",
        running=f"{_NODE}:{_PREV} {_PY}:{_PREV}",
        journal=journal,
    )
    assert result.returncode == 0, result.stderr
    # The journal names the rollback pair; the green marker is not consulted.
    assert removed == _rmi(_CUR, _STALE2)


def test_prune_with_an_invalid_journal_removes_nothing(tmp_path: Path) -> None:
    result, removed = _prune(
        tmp_path, green=f"{_CUR}\n{_PREV}\n", tags=f"{_CUR} {_STALE1}", journal="{}",
    )
    assert result.returncode == 0, result.stderr
    assert removed == []


def test_prune_skips_when_no_app_container_is_running(tmp_path: Path) -> None:
    result, removed = _prune(tmp_path, green=f"{_CUR}\n{_PREV}\n", tags=f"{_CUR} {_STALE1}", running="")
    assert result.returncode == 0, result.stderr
    assert removed == []


def test_prune_continues_past_a_refused_rmi(tmp_path: Path) -> None:
    result, removed = _prune(
        tmp_path,
        green=f"{_CUR}\n{_PREV}\n",
        tags=f"{_CUR} {_PREV} {_STALE1} {_STALE2}",
        extra={"RADON_STUB_RMI_FAIL": f"{_NODE}:{_STALE1}"},
    )
    assert result.returncode == 0, result.stderr
    assert removed == _rmi(_STALE1, _STALE2)
    assert f"rmi {_NODE}:{_STALE1} failed" in result.stderr


def test_prune_takes_no_argument(tmp_path: Path) -> None:
    result = _run(tmp_path, ["prune", _STALE1])
    assert result.returncode != 0
    assert "usage:" in result.stderr


# --- Lock-held prune (prune-locked) -------------------------------------------
# A refused deploy left its pulled set on disk forever: the only prune ran after
# a green deploy, and an in-deploy prune took a SECOND open of the deploy lock
# and conflicted with the lock its own caller held (#1012). `prune-locked`
# takes the caller's held deploy-lock descriptor as stdin and proves it owns it.


def _prune_locked(tmp_path: Path, *, green: str | None, tags: str, args: list[str],
                  stdin_mode: str = "held", running: str | None = None,
                  journal: str | None = None):
    import fcntl
    _runtime_env(tmp_path)
    lock = tmp_path / "state" / "deploy.lock"
    marker = tmp_path / "prune-green"
    journal_file = tmp_path / "prune-transition.json"
    marker.unlink(missing_ok=True)
    journal_file.unlink(missing_ok=True)
    if green is not None:
        marker.write_text(green)
    if journal is not None:
        journal_file.write_text(journal)
    log = tmp_path / "docker.log"
    log.unlink(missing_ok=True)
    holder = open(lock, "r")
    other = None
    try:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if stdin_mode == "held":
            stdin = holder
        elif stdin_mode == "foreign":
            # Somebody else holds the lock; the caller only opened the file.
            other = open(lock, "r")
            stdin = other
        elif stdin_mode == "not-the-lock":
            other = open(tmp_path / "env", "r")
            stdin = other
        else:
            raise AssertionError(stdin_mode)
        result = _run(
            tmp_path,
            ["prune-locked", *args],
            extra_env={
                "RADON_TEST_GREEN_MARKER": str(marker),
                "RADON_TEST_TRANSITION_JOURNAL": str(journal_file),
                "RADON_STUB_TAGS": tags,
                "RADON_STUB_RUNNING": running if running is not None else f"{_NODE}:{_CUR} {_PY}:{_CUR}",
            },
            docker_body=_PRUNE_DOCKER,
            stdin=stdin,
        )
        # The caller still owns its lock after the pruner exits.
        probe = open(lock, "r")
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            probe.close()
    finally:
        if other is not None:
            other.close()
        holder.close()
    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return result, sorted(line for line in lines if line.startswith("rmi "))


_TARGET = "f" * 40


def test_prune_locked_under_the_callers_deploy_lock_keeps_running_previous_and_target(tmp_path: Path) -> None:
    result, removed = _prune_locked(
        tmp_path,
        green=f"{_CUR}\n{_PREV}\n",
        tags=f"latest {_CUR} {_PREV} {_TARGET} {_STALE1} {_STALE2} weird-tag",
        args=[_TARGET],
    )
    assert result.returncode == 0, result.stderr
    assert "image prune skipped" not in result.stderr
    assert removed == _rmi(_STALE1, _STALE2)


def test_prune_locked_removes_a_refused_deploys_set(tmp_path: Path) -> None:
    # Production 2026-10-31: live 38154af, refused 707c642 still local. The next
    # deploy's prune must drop the refused set before it pulls or admits.
    result, removed = _prune_locked(
        tmp_path,
        green=f"{_CUR}\n{_PREV}\n",
        tags=f"{_CUR} {_STALE1}",
        args=[_TARGET],
    )
    assert result.returncode == 0, result.stderr
    assert removed == _rmi(_STALE1)


def test_prune_locked_with_a_target_does_not_need_a_previous_release(tmp_path: Path) -> None:
    result, removed = _prune_locked(
        tmp_path, green=f"{_CUR}\n", tags=f"{_CUR} {_TARGET} {_STALE1}", args=[_TARGET],
    )
    assert result.returncode == 0, result.stderr
    assert removed == _rmi(_STALE1)


def test_prune_locked_without_a_target_keeps_both_green_lines(tmp_path: Path) -> None:
    result, removed = _prune_locked(
        tmp_path, green=f"{_CUR}\n{_PREV}\n", tags=f"{_CUR} {_PREV} {_STALE1}", args=[],
    )
    assert result.returncode == 0, result.stderr
    assert removed == _rmi(_STALE1)


def test_prune_locked_without_a_target_skips_without_a_previous_release(tmp_path: Path) -> None:
    result, removed = _prune_locked(
        tmp_path, green=f"{_CUR}\n", tags=f"{_CUR} {_STALE1}", args=[],
    )
    assert result.returncode == 0, result.stderr
    assert removed == []
    assert "image prune skipped" in result.stderr


def test_prune_locked_never_removes_an_image_a_running_container_uses(tmp_path: Path) -> None:
    result, removed = _prune_locked(
        tmp_path,
        green=f"{_CUR}\n{_PREV}\n",
        tags=f"{_CUR} {_STALE1}",
        args=[_TARGET],
        running=f"{_NODE}:{_STALE1} {_PY}:{_CUR}",
    )
    assert result.returncode == 0, result.stderr
    assert removed == sorted([f"rmi {_PY}:{_STALE1}", f"rmi {_MODEL}:{_STALE1}"])


def test_prune_locked_with_a_pending_transition_protects_both_journal_shas(tmp_path: Path) -> None:
    journal = json.dumps({"version": 1, "requested_sha": _STALE1, "previous_sha": _PREV})
    result, removed = _prune_locked(
        tmp_path,
        green=f"{_CUR}\n",
        tags=f"{_CUR} {_PREV} {_STALE1} {_STALE2} {_TARGET}",
        args=[_TARGET],
        running=f"{_NODE}:{_PREV} {_PY}:{_PREV}",
        journal=journal,
    )
    assert result.returncode == 0, result.stderr
    assert removed == _rmi(_CUR, _STALE2)


def test_prune_locked_refuses_a_lock_held_by_someone_else(tmp_path: Path) -> None:
    result, removed = _prune_locked(
        tmp_path, green=f"{_CUR}\n{_PREV}\n", tags=f"{_CUR} {_STALE1}", args=[_TARGET],
        stdin_mode="foreign",
    )
    assert result.returncode == 0, result.stderr
    assert removed == []
    assert "image prune skipped" in result.stderr


def test_prune_locked_refuses_stdin_that_is_not_the_deploy_lock(tmp_path: Path) -> None:
    result, removed = _prune_locked(
        tmp_path, green=f"{_CUR}\n{_PREV}\n", tags=f"{_CUR} {_STALE1}", args=[_TARGET],
        stdin_mode="not-the-lock",
    )
    assert result.returncode == 0, result.stderr
    assert removed == []
    assert "image prune skipped: stdin is not the held deploy lock" in result.stderr


def test_prune_locked_skips_while_another_pull_holds_the_pull_lock(tmp_path: Path) -> None:
    import fcntl
    held = _hold(_pull_lock(tmp_path), fcntl.LOCK_SH)
    try:
        result, removed = _prune_locked(
            tmp_path, green=f"{_CUR}\n{_PREV}\n", tags=f"{_CUR} {_STALE1}", args=[_TARGET],
        )
    finally:
        held.close()
    assert result.returncode == 0, result.stderr
    assert removed == []
    assert "image prune skipped: app image pull in progress" in result.stderr


@pytest.mark.parametrize("bad", ["abc", "F" * 40, "g" * 40, "a" * 41])
def test_prune_locked_takes_only_a_40_hex_sha(tmp_path: Path, bad: str) -> None:
    result = _run(tmp_path, ["prune-locked", bad])
    assert result.returncode == 64
    log = result.docker_log  # type: ignore[attr-defined]
    assert not log.exists() or "rmi " not in log.read_text(encoding="utf-8")


def test_prune_locked_takes_at_most_one_argument(tmp_path: Path) -> None:
    result = _run(tmp_path, ["prune-locked", _TARGET, _TARGET])
    assert result.returncode == 64
    assert "usage:" in result.stderr


# `pull <sha>` prunes BEFORE fetching layers: the CI prepull pulled 3.4G into a
# full disk first and only then pruned (2026-10-31 Caddy ENOSPC).
_PULL_AFTER_PRUNE_DOCKER = """#!/bin/bash
printf '%s\\n' "$*" >> {log}
case "$1 $2" in
  "manifest inspect") exit 0 ;;
  "image inspect") [[ -f "${{RADON_STUB_PULL_DIR}}/${{3##*/}}" ]]; exit $? ;;
  "ps --format")
    for image in ${{RADON_STUB_RUNNING:-}}; do printf '%s\\n' "$image"; done
    ;;
  "images --format")
    for t in ${{RADON_STUB_TAGS:-}}; do printf '%s:%s\\n' "$4" "$t"; done
    ;;
esac
if [[ "$1" == "pull" ]]; then
  touch "${{RADON_STUB_PULL_DIR}}/${{2##*/}}"
fi
exit 0
"""


def test_pull_prunes_stale_sets_before_fetching_any_layer(tmp_path: Path) -> None:
    pull_dir = tmp_path / "pulled"
    pull_dir.mkdir()
    marker = tmp_path / "pull-green"
    marker.write_text(f"{_CUR}\n{_PREV}\n")
    result = _run(
        tmp_path,
        ["pull", _TARGET],
        extra_env={
            "RADON_STUB_PULL_DIR": str(pull_dir),
            "RADON_TEST_GREEN_MARKER": str(marker),
            "RADON_TEST_TRANSITION_JOURNAL": str(tmp_path / "none.json"),
            "RADON_STUB_TAGS": f"{_CUR} {_STALE1}",
            "RADON_STUB_RUNNING": f"{_NODE}:{_CUR} {_PY}:{_CUR}",
        },
        docker_body=_PULL_AFTER_PRUNE_DOCKER,
    )
    assert result.returncode == 0, result.stderr
    lines = result.docker_log.read_text(encoding="utf-8").splitlines()  # type: ignore[attr-defined]
    rmis = [i for i, line in enumerate(lines) if line.startswith("rmi ")]
    pulls = [i for i, line in enumerate(lines) if line.startswith("pull ")]
    assert sorted(lines[i] for i in rmis) == _rmi(_STALE1)
    assert pulls and max(rmis) < min(pulls), lines


# --- Pull lock (PR #1020 follow-up) -------------------------------------------
# Prepull runs `pull <sha>` without the deploy lock. A post-green prune from the
# previous run's deploy could delete the tag prepull had just pulled. `pull`
# now holds a shared pull lock from before the first layer fetch until the pair
# is verified; every prune takes that lock exclusively without blocking and
# skips while another pull holds it.


def _pull_lock(tmp_path: Path) -> Path:
    _runtime_env(tmp_path)
    return tmp_path / "state" / "image-pull.lock"


def _hold(path: Path, mode: int):
    import fcntl
    handle = open(path, "a+")
    fcntl.flock(handle, mode | fcntl.LOCK_NB)
    return handle


def test_prune_skips_while_another_pull_holds_the_pull_lock(tmp_path: Path) -> None:
    import fcntl
    held = _hold(_pull_lock(tmp_path), fcntl.LOCK_SH)
    try:
        result, removed = _prune(tmp_path, green=f"{_CUR}\n{_PREV}\n", tags=f"{_CUR} {_PREV} {_STALE1}")
    finally:
        held.close()
    assert result.returncode == 0, result.stderr
    assert removed == []
    assert "image prune skipped: app image pull in progress" in result.stderr


def test_prune_proceeds_once_the_pull_lock_is_free(tmp_path: Path) -> None:
    import fcntl
    # A lock file left by a finished pull is not a held lock.
    _hold(_pull_lock(tmp_path), fcntl.LOCK_SH).close()
    result, removed = _prune(tmp_path, green=f"{_CUR}\n{_PREV}\n", tags=f"{_CUR} {_PREV} {_STALE1}")
    assert result.returncode == 0, result.stderr
    assert removed == _rmi(_STALE1)


def test_pull_prune_skips_while_a_concurrent_pull_holds_the_pull_lock(tmp_path: Path) -> None:
    import fcntl
    target, previous, stale = "a" * 40, "b" * 40, "c" * 40
    held = _hold(_pull_lock(tmp_path), fcntl.LOCK_SH)
    try:
        result = _run(
            tmp_path,
            ["pull", target],
            extra_env={"RADON_STUB_PREVIOUS": previous, "RADON_STUB_TAGS": f"{target} {previous} {stale}"},
            docker_body=_IMAGE_STORE_DOCKER,
        )
    finally:
        held.close()
    assert result.returncode == 0, result.stderr
    assert "rmi " not in result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert "image prune skipped: app image pull in progress" in result.stderr


# Every docker call made while pulling and verifying records whether an
# exclusive (prune-style) lock on the pull lock could be taken at that moment.
_PULL_LOCK_PROBE_DOCKER = """#!/bin/bash
printf '%s\\n' "$*" >> {log}
probe() {{
  "$RADON_STUB_PY" - "$RADON_STUB_LOCK" <<'PY' 9>&-
import fcntl, sys
with open(sys.argv[1], "a+") as handle:
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print("free")
    except BlockingIOError:
        print("held")
PY
}}
case "$1 $2" in
  "manifest inspect") exit 0 ;;
  "image inspect") printf '%s %s\\n' "$3" "$(probe)" >> "$RADON_STUB_PROBES"; exit 0 ;;
esac
if [[ "$1" == "pull" ]]; then
  printf 'pull %s %s\\n' "$2" "$(probe)" >> "$RADON_STUB_PROBES"
fi
exit 0
"""


def test_pull_holds_the_pull_lock_while_pulling_and_verifying(tmp_path: Path) -> None:
    import fcntl
    lock = _pull_lock(tmp_path)
    probes = tmp_path / "probes"
    result = _run(
        tmp_path,
        ["pull", "a" * 40],
        extra_env={"RADON_STUB_PY": sys.executable, "RADON_STUB_LOCK": str(lock),
                   "RADON_STUB_PROBES": str(probes)},
        docker_body=_PULL_LOCK_PROBE_DOCKER,
    )
    assert result.returncode == 0, result.stderr
    lines = probes.read_text(encoding="utf-8").splitlines()
    assert any(line.startswith("pull ") for line in lines), lines
    assert all(line.endswith(" held") for line in lines), lines
    # Released once the verb exits: the next prune is not blocked by it.
    _hold(lock, fcntl.LOCK_EX).close()


def test_pull_waits_a_bounded_time_for_a_running_prune(tmp_path: Path) -> None:
    import fcntl
    held = _hold(_pull_lock(tmp_path), fcntl.LOCK_EX)
    try:
        started = time.monotonic()
        result = _run(tmp_path, ["pull", "a" * 40], extra_env={"RADON_TEST_PULL_LOCK_WAIT_SECS": "1"})
        elapsed = time.monotonic() - started
    finally:
        held.close()
    assert result.returncode == 75, result.stderr
    assert "pull lock" in result.stderr
    assert elapsed < 8
    log = result.docker_log  # type: ignore[attr-defined]
    assert not log.exists() or "pull " not in log.read_text(encoding="utf-8")


def test_pull_under_a_held_deploy_lock_completes_without_deadlock(tmp_path: Path) -> None:
    # deploy.sh holds ~/.radon-deploy.lock across its own `pull <sha>`. The
    # pull lock must not wait on it, and the prune inside pull must skip.
    import fcntl
    _runtime_env(tmp_path)
    target, previous, stale = "a" * 40, "b" * 40, "c" * 40
    with open(tmp_path / "state" / "deploy.lock", "r+") as deploy_lock:
        fcntl.flock(deploy_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = _run(
            tmp_path,
            ["pull", target],
            extra_env={"RADON_STUB_PREVIOUS": previous, "RADON_STUB_TAGS": f"{target} {previous} {stale}"},
            docker_body=_IMAGE_STORE_DOCKER,
            timeout=10,
        )
    assert result.returncode == 0, result.stderr
    assert "rmi " not in result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert "BlockingIOError" in result.stderr


def test_prune_under_a_held_deploy_lock_releases_the_pull_lock(tmp_path: Path) -> None:
    # A prune that loses the deploy lock must not leave the pull lock held.
    import fcntl
    lock = _pull_lock(tmp_path)
    with open(tmp_path / "state" / "deploy.lock", "r+") as deploy_lock:
        fcntl.flock(deploy_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result, removed = _prune(tmp_path, green=f"{_CUR}\n{_PREV}\n", tags=f"{_CUR} {_STALE1}")
    assert result.returncode == 0, result.stderr
    assert removed == []
    _hold(lock, fcntl.LOCK_EX).close()


def test_pull_refuses_a_symlinked_pull_lock(tmp_path: Path) -> None:
    lock = _pull_lock(tmp_path)
    victim = tmp_path / "victim"
    victim.write_text("keep\n")
    lock.symlink_to(victim)
    result = _run(tmp_path, ["pull", "a" * 40])
    assert result.returncode != 0
    assert victim.read_text() == "keep\n"
    log = result.docker_log  # type: ignore[attr-defined]
    assert not log.exists() or "pull " not in log.read_text(encoding="utf-8")


def test_production_pull_lock_lives_in_the_root_only_runtime_dir() -> None:
    text = RUNTIME.read_text(encoding="utf-8")
    assert "PULL_LOCK_FILE=/run/radon-app-runtime/image-pull.lock" in text


_CONCURRENT_PULL_DOCKER = """#!/bin/bash
printf '%s\\n' "$*" >> {log}
if [[ "$1 $2" == "image inspect" ]]; then
  [[ -f "${{RADON_STUB_PULL_DIR}}/${{3##*/}}" ]]
  exit $?
fi
if [[ "$1" == "pull" ]]; then
  touch "${{RADON_STUB_PULL_DIR}}/${{2##*/}}"
  for _ in $(seq 1 80); do
    [[ "$(find "${{RADON_STUB_PULL_DIR}}" -type f | wc -l | tr -d ' ')" == "3" ]] && exit 0
    sleep 0.025
  done
  exit 42
fi
exit 0
"""


def test_missing_release_set_pulls_all_images_concurrently(tmp_path: Path) -> None:
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
    # The newsfeed renders third-party web content in a sandbox-disabled
    # Chromium; it gets an isolated bridge network, never the host stack.
    if unit == "radon-newsfeed.service":
        assert "--network bridge" in log
        assert "--network host" not in log
    else:
        assert "--network host" in log
    assert "--user 1000:1000" in log
    assert "--env-file" in log
    assert "--init" in log
    assert "--cap-drop ALL" in log
    assert "no-new-privileges" in log
    assert "--cgroupns host" in log or "--cgroupns=host" in log
    # Docker's systemd driver accepts a slice only, not a unit path.
    assert "--cgroup-parent=system.slice" in log
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
    docker_started = tmp_path / "docker-started"
    docker_release = tmp_path / "docker-release"
    # T-452: the fake docker used to `sleep 3`, which every test-side wait
    # raced under contention. It now stays up until the test releases it, so
    # "while docker runs" holds by construction (loop bounded so a leaked
    # stub cannot outlive the run).
    _write_executable(
        fake_docker,
        "#!/bin/bash\n"
        'if [[ "$1" == run ]]; then\n'
        f'  touch "{docker_started}"\n'
        f'  for _ in $(seq 1 1200); do [[ -e "{docker_release}" ]] && exit 0; sleep 0.1; done\n'
        "fi\n"
        "exit 0\n",
    )
    env = {**_runtime_env(tmp_path, fake_docker), "NOTIFY_SOCKET": str(upstream_path)}
    proxy_socket = Path(env["RADON_TEST_NOTIFY_PROXY_DIR"]) / "radon-relay.service.sock"
    proc = subprocess.Popen(
        ["bash", str(RUNTIME), "run", "radon-relay.service"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + _contention_budget(1.0, floor=5.0)
        while not proxy_socket.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert proxy_socket.exists()
        handoff_deadline = time.monotonic() + _contention_budget(1.0, floor=5.0)
        while not docker_started.exists() and time.monotonic() < handoff_deadline:
            time.sleep(0.02)
        assert docker_started.exists(), "run never handed off to docker"
        # A proxy orphaned by the handoff exits within one parent poll; give
        # it a full interval past the exec before asserting it is alive. The
        # release-gated docker means this wait no longer races the container
        # exiting.
        time.sleep(PROXY_PARENT_POLL_INTERVAL * 2)
        client = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        client.sendto(b"READY=1\n", str(proxy_socket))
        upstream.settimeout(_contention_budget(1.0, floor=5.0))
        assert b"READY=1" in upstream.recv(256)
    finally:
        docker_release.touch()
        proc.kill()
        proc.wait(timeout=5)
        upstream.close()


def _proxy_dir_from(result: subprocess.CompletedProcess[str]) -> str:
    return result.proxy_dir  # type: ignore[attr-defined]


def test_run_refuses_to_start_when_the_notify_proxy_cannot_bind(tmp_path: Path) -> None:
    # R-438 now refuses failed environment rendering before proxy startup.
    # Keep that prerequisite writable so this test reaches its bind fault.
    missing = str(tmp_path / "notify")
    # Directory provisioning also uses Python; fail only the notify proxy.
    failing_python = tmp_path / "failing-python"
    lease_dir = shlex.quote(str(tmp_path / 'state' / 'ib-lease'))
    model_socket_dir = shlex.quote(str(tmp_path / 'state' / 'model-gateway'))
    model_control_dir = shlex.quote(str(tmp_path / 'state' / 'model-control'))
    data_dir = shlex.quote(str(tmp_path / 'data'))
    media_dir = shlex.quote(str(tmp_path / 'media'))
    _write_executable(failing_python,
        f'#!/bin/bash\nif [[ "${{3:-}}" == {lease_dir} || "${{3:-}}" == {model_socket_dir} || "${{3:-}}" == {model_control_dir} || "${{3:-}}" == {data_dir} || "${{3:-}}" == {media_dir} ]]; then exec {shlex.quote(sys.executable)} "$@"; fi\nexit 1\n')
    # The injected proxy never binds. Exercise all admission polls without
    # paying their sleep delay on the shared runner; keep the timeout intact.
    _write_executable(tmp_path / "sleep", "#!/bin/bash\nexit 0\n")
    result = _run(
        tmp_path,
        ["run", "radon-relay.service"],
        extra_env={"RADON_TEST_NOTIFY_PROXY_DIR": missing, "RADON_TEST_PYTHON": str(failing_python),
                   "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]},
    )
    assert result.returncode == 71, result.stderr
    assert "notify proxy" in result.stderr
    log = (tmp_path / "docker.log").read_text(encoding="utf-8")
    assert not [line for line in log.splitlines() if line.startswith("run ")], log


def test_notify_proxy_socket_is_chowned_to_the_container_uid(tmp_path: Path) -> None:
    """The 0600 socket is only usable by the container once start_notify_proxy
    hands ownership to the radon uid the container runs as."""
    d = Path(tempfile.mkdtemp(prefix="rdn", dir="/tmp"))
    upstream = d / "u"
    upstream.write_bytes(b"")
    result = _run(
        tmp_path,
        ["run", "radon-relay.service"],
        extra_env={"NOTIFY_SOCKET": str(upstream)},
    )
    assert result.returncode == 0, result.stderr
    listen = Path(_proxy_dir_from(result)) / "radon-relay.service.sock"
    chowns = (tmp_path / "chown.log").read_text(encoding="utf-8")
    assert f"-h 1000:1000 {listen}" in chowns


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
        # NotifyAccess=all trusts every datagram the proxy relays, so the
        # socket must never be world-writable: only its owner (chowned to the
        # container uid by start_notify_proxy) may write READY/WATCHDOG.
        assert stat.S_IMODE(listen_path.stat().st_mode) == 0o600
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
    # R-619: the master key is delivered through the radon-secrets group, which
    # the host radon account is not a member of. Owner is root and the owner
    # bits are empty, so uid radon -- the uid the container itself runs as --
    # cannot read the plaintext key outside the container.
    assert stat.S_IMODE(host_dir.stat().st_mode) == 0o050
    host_dir.chmod(0o700)  # the test user owns these; root ownership is stubbed
    assert stat.S_IMODE(staged.stat().st_mode) == 0o040
    staged.chmod(0o400)
    assert staged.read_bytes() == (tmp_path / "credentials" / staged.name).read_bytes()
    assert stat.S_IMODE((tmp_path / "data" / "secret_store").stat().st_mode) == 0o700
    chowns = (tmp_path / "chown.log").read_text(encoding="utf-8")
    assert f"root:1001 {host_dir} {staged}" in chowns
    # secret_store ownership is applied fd-based in-process (symlink-safe),
    # so it must never appear in the stubbed chown log.
    assert str(tmp_path / "data" / "secret_store") not in chowns
    assert "--group-add 1001" in _run_line(result)
    assert "python scripts/secret_store.py && exec uvicorn" in _run_line(result)


def test_run_api_refuses_symlinked_secret_store_dir(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere"
    target.mkdir()
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    (data / "secret_store").symlink_to(target)
    result = _run(tmp_path, ["run", "radon-api.service"])
    assert result.returncode == 78, result.stderr
    assert "secret store" in result.stderr.lower()
    # The link target must not have been chowned or chmodded.
    chown_log = tmp_path / "chown.log"
    if chown_log.exists():
        assert str(target) not in chown_log.read_text(encoding="utf-8")
    assert stat.S_IMODE(target.stat().st_mode) != 0o700


def test_run_refuses_symlinked_lease_dir(tmp_path: Path) -> None:
    """The 2FA lease directory gets the same O_NOFOLLOW treatment as the
    secret store: a pre-planted link at ib-lease must be refused, never
    followed by the root-privileged ownership pass."""
    target = tmp_path / "lease-elsewhere"
    target.mkdir()
    (tmp_path / "state" / "ib-lease").parent.mkdir(exist_ok=True)
    (tmp_path / "state" / "ib-lease").symlink_to(target)
    result = _run(tmp_path, ["run", "radon-monitor.service"])
    assert result.returncode == 78, result.stderr
    chown_log = tmp_path / "chown.log"
    if chown_log.exists():
        assert str(target) not in chown_log.read_text(encoding="utf-8")
    assert stat.S_IMODE(target.stat().st_mode) != 0o700


def test_run_prepares_lease_dir_symlink_safe(tmp_path: Path) -> None:
    """Valid path: the lease directory is created 0700 with fd-based
    ownership, so it never appears in the external chown log."""
    result = _run(tmp_path, ["run", "radon-monitor.service"])
    assert result.returncode == 0, result.stderr
    lease = tmp_path / "state" / "ib-lease"
    assert lease.is_dir() and not lease.is_symlink()
    assert stat.S_IMODE(lease.stat().st_mode) == 0o700
    chown_log = tmp_path / "chown.log"
    if chown_log.exists():
        assert str(lease) not in chown_log.read_text(encoding="utf-8")


def _engine_ran(result: subprocess.CompletedProcess[str]) -> bool:
    log = result.docker_log  # type: ignore[attr-defined]
    return log.exists() and any(
        line.startswith("run ") for line in log.read_text(encoding="utf-8").splitlines()
    )


def _swap_for_link(path: Path, target: Path) -> None:
    target.mkdir(exist_ok=True)
    if path.is_dir() and not path.is_symlink():
        path.rmdir()
    path.symlink_to(target)


@pytest.mark.parametrize("name", ("data", "media"))
def test_run_refuses_a_symlinked_data_or_media_bind_source(tmp_path: Path, name: str) -> None:
    """Bind sources under radon-writable parents are checked without
    following links; a link is refused before any container is started."""
    _swap_for_link(tmp_path / name, tmp_path / f"{name}-elsewhere")
    result = _run(tmp_path, ["run", "radon-api.service"])
    assert result.returncode == 78, result.stderr
    assert "bind source" in result.stderr
    assert not _engine_ran(result)


def test_run_refuses_a_bind_source_under_a_symlinked_ancestor(tmp_path: Path) -> None:
    real = tmp_path / "real"
    (real / "data").mkdir(parents=True)
    (tmp_path / "linked").symlink_to(real)
    result = _run(
        tmp_path,
        ["run", "radon-monitor.service"],
        extra_env={"RADON_TEST_DATA_DIR": str(tmp_path / "linked" / "data")},
    )
    assert result.returncode == 78, result.stderr
    assert "bind source" in result.stderr
    assert not _engine_ran(result)


@pytest.mark.parametrize(
    "env_name", ("RADON_NEWSFEED_SCRIPTS_PATH", "RADON_NEWSFEED_BROWSERS_PATH")
)
def test_run_newsfeed_refuses_symlinked_overlay_bind_sources(
    tmp_path: Path, env_name: str
) -> None:
    link = tmp_path / "overlay-link"
    _swap_for_link(link, tmp_path / "overlay-elsewhere")
    result = _run(tmp_path, ["run", "radon-newsfeed.service"], extra_env={env_name: str(link)})
    assert result.returncode == 78, result.stderr
    assert "bind source" in result.stderr
    assert not _engine_ran(result)


@pytest.mark.parametrize("unit", ("radon-api.service", "radon-newsfeed.service"))
def test_run_accepts_real_owned_bind_sources(tmp_path: Path, unit: str) -> None:
    result = _run(tmp_path, ["run", unit])
    assert result.returncode == 0, result.stderr
    assert _engine_ran(result)


# radon owns the parent of every bind source, so it can rename the validated
# leaf and plant a link once the check has passed. This stub does exactly that
# on the orphan reap, which is only a convenient mid-start hook: on a real host
# any part of the start path between validation and the engine's mount serves.
# The directory swap keeps allocating until it lands on the freed inode when
# the filesystem will reissue one (ext4 does, immediately). A busy allocator
# otherwise hands the replacement a new inode and a device:inode check passes
# for the wrong reason.
_SWAPPING_DOCKER = """#!/bin/bash
printf '%s\\n' "$*" >> {log}
if [ "$1" = "rm" ] && [ -n "${{RADON_TEST_SWAP_PATH:-}}" ]; then
  if [ "${{RADON_TEST_SWAP_KIND}}" = "link" ]; then
    /bin/rm -rf -- "${{RADON_TEST_SWAP_PATH}}"
    /bin/ln -s "${{RADON_TEST_SWAP_TARGET}}" "${{RADON_TEST_SWAP_PATH}}"
  else
    python3 -c 'import os, shutil
p = os.environ["RADON_TEST_SWAP_PATH"]
st = os.stat(p)
want = (st.st_dev, st.st_ino)
shutil.rmtree(p)
n = 0
while n < 64:
    os.mkdir(p)
    now = os.stat(p)
    if (now.st_dev, now.st_ino) == want:
        break
    os.rename(p, p + ".hold." + str(n))
    n += 1
if not os.path.isdir(p):
    os.mkdir(p)
'
  fi
fi
exit 0
"""


@pytest.mark.parametrize(
    ("kind", "message"),
    (
        ("link", "is a symlink"),
        ("dir", "replaced after validation"),
    ),
)
def test_run_refuses_a_bind_source_replaced_after_validation(
    tmp_path: Path, kind: str, message: str
) -> None:
    """A validated bind source is still a mutable name: the engine resolves it
    again when it mounts. The recorded directory must be re-resolved before the
    engine runs, so a swap during the rest of the start path refuses the start
    instead of binding a tree the check never saw. Both shapes of swap are
    refused: a planted link, and a different directory of the same owner,
    including one the filesystem recreated at the same device and inode."""
    victim = tmp_path / "root-only"
    victim.mkdir()
    (victim / "key").write_text("secret\n", encoding="utf-8")
    result = _run(
        tmp_path,
        ["run", "radon-monitor.service"],
        docker_body=_SWAPPING_DOCKER,
        extra_env={
            "RADON_TEST_SWAP_PATH": str(tmp_path / "media"),
            "RADON_TEST_SWAP_TARGET": str(victim),
            "RADON_TEST_SWAP_KIND": kind,
        },
    )
    assert result.returncode == 78, result.stdout + result.stderr
    assert message in result.stderr
    assert not _engine_ran(result)
    assert victim.joinpath("key").read_text(encoding="utf-8") == "secret\n"


@pytest.mark.parametrize(
    "env_name", ("RADON_IB_REMOTE_CERT_DIR", "RADON_RH_TOKEN_DIR")
)
def test_run_api_refuses_symlinked_gateway_or_token_bind_sources(
    tmp_path: Path, env_name: str
) -> None:
    """The Gateway mTLS directory and the Robinhood token store sit under
    radon-writable parents like every other bind source, so they are validated
    without following links instead of admitted by a bare -d test."""
    link = tmp_path / "store-link"
    _swap_for_link(link, tmp_path / "store-elsewhere")
    result = _run(tmp_path, ["run", "radon-api.service"], extra_env={env_name: str(link)})
    assert result.returncode == 78, result.stderr
    assert "bind source" in result.stderr
    assert not _engine_ran(result)


@pytest.mark.parametrize(
    "env_name", ("RADON_IB_REMOTE_CERT_DIR", "RADON_RH_TOKEN_DIR")
)
def test_run_api_rechecks_gateway_and_token_bind_sources(
    tmp_path: Path, env_name: str
) -> None:
    """Both names must be recorded in BIND_SOURCES, so a swap after validation
    is caught by the re-resolution before the engine mounts anything."""
    store = tmp_path / "store"
    store.mkdir()
    victim = tmp_path / "root-only"
    victim.mkdir()
    (victim / "key").write_text("secret\n", encoding="utf-8")
    result = _run(
        tmp_path,
        ["run", "radon-api.service"],
        docker_body=_SWAPPING_DOCKER,
        extra_env={
            env_name: str(store),
            "RADON_TEST_SWAP_PATH": str(store),
            "RADON_TEST_SWAP_TARGET": str(victim),
            "RADON_TEST_SWAP_KIND": "link",
        },
    )
    assert result.returncode == 78, result.stdout + result.stderr
    assert "is a symlink" in result.stderr
    assert not _engine_ran(result)


def test_bind_source_recheck_is_the_last_step_before_the_engine() -> None:
    """The re-resolution is only worth its window if nothing follows it, so no
    later edit may insert work between the recheck and the engine invocation."""
    lines = [
        line.strip()
        for line in RUNTIME.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    engine_calls = [i for i, line in enumerate(lines) if line == 'exec "$DOCKER" "$@"']
    assert len(engine_calls) == 1, engine_calls
    assert lines[engine_calls[0] - 1] == 'recheck_bind_sources "$ids"'


def test_run_api_refuses_when_credential_group_is_absent(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        ["run", "radon-api.service"],
        extra_env={"RADON_TEST_SECRET_GROUP_MISSING": "1"},
    )
    assert result.returncode == 78, result.stderr
    assert "radon-secrets" in result.stderr
    assert not [
        line
        for line in result.docker_log.read_text(encoding="utf-8").splitlines()  # type: ignore[attr-defined]
        if line.startswith("run ")
    ]


def test_run_api_refuses_when_radon_can_open_the_credential_group(
    tmp_path: Path,
) -> None:
    """The delivery channel is only closed while radon is outside the group."""
    result = _run(
        tmp_path,
        ["run", "radon-api.service"],
        extra_env={"RADON_TEST_RADON_GROUPS": "radon docker radon-secrets"},
    )
    assert result.returncode == 78, result.stderr
    assert "radon must not be a member of radon-secrets" in result.stderr
    assert not [
        line
        for line in result.docker_log.read_text(encoding="utf-8").splitlines()  # type: ignore[attr-defined]
        if line.startswith("run ")
    ]


def test_setup_creates_the_credential_group_without_radon_in_it() -> None:
    setup = SETUP.read_text(encoding="utf-8")
    assert "groupadd --system radon-secrets" in setup
    assert "radon must not be a member of radon-secrets" in setup
    assert "usermod -aG radon-secrets radon" not in setup


def test_non_api_units_are_not_given_the_credential_group(tmp_path: Path) -> None:
    result = _run(tmp_path, ["run", "radon-nextjs.service"])
    assert result.returncode == 0, result.stderr
    assert "--group-add" not in _run_line(result)


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
    # T-452: the fixed 90s bound still redded at the 9.5x contention factor
    # recorded 2026-09-04 (~170s worst case), so the budget now scales with
    # the measured cost of the same fork+exec sleep the script performs; the
    # idle-host floor stays 90s and the ceiling keeps a genuine hang red.
    # The failing python is a stub because /usr/bin/false is a darwin
    # location, not guaranteed on non-usrmerge Linux.
    failing_python = tmp_path / "failing-python"
    # Provisioning also uses Python now. Fail only the notify proxy so this
    # regression continues to exercise its intended cleanup branch.
    private_anchor = shlex.quote(str(tmp_path / 'state' / 'private'))
    secret_store = shlex.quote(str(tmp_path / 'data' / 'secret_store'))
    lease_dir = shlex.quote(str(tmp_path / 'state' / 'ib-lease'))
    model_socket_dir = shlex.quote(str(tmp_path / 'state' / 'model-gateway'))
    model_control_dir = shlex.quote(str(tmp_path / 'state' / 'model-control'))
    control_dir = shlex.quote(str(tmp_path / 'state' / 'control'))
    data_dir = shlex.quote(str(tmp_path / 'data'))
    media_dir = shlex.quote(str(tmp_path / 'media'))
    _write_executable(failing_python,
        f'#!/bin/bash\nif [[ "${{3:-}}" == {private_anchor} || "${{3:-}}" == {secret_store} || "${{3:-}}" == {lease_dir} || "${{3:-}}" == {model_socket_dir} || "${{3:-}}" == {model_control_dir} || "${{3:-}}" == {control_dir} || "${{3:-}}" == {data_dir} || "${{3:-}}" == {media_dir} ]]; then exec {shlex.quote(sys.executable)} "$@"; fi\nexit 1\n')
    result = _run(
        tmp_path,
        ["run", "radon-api.service"],
        extra_env={"RADON_TEST_PYTHON": str(failing_python)},
        timeout=_contention_budget(PROXY_POLL_ITERATIONS * PROXY_POLL_INTERVAL, floor=90.0),
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


def test_run_api_binds_the_host_control_socket_directory(tmp_path: Path) -> None:
    """radon-control: the API container's only path to unit control."""
    control = tmp_path / "control"
    result = _run(
        tmp_path,
        ["run", "radon-api.service"],
        extra_env={"RADON_TEST_CONTROL_DIR": str(control)},
    )
    assert result.returncode == 0, result.stderr
    log = result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert f"-v {control}:/run/radon-control " in log
    assert "--env RADON_CONTROL_SOCKET=/run/radon-control/control.sock" in log
    # Pre-created owner-only so start order against radon-control is moot.
    assert control.is_dir()
    assert oct(control.stat().st_mode & 0o777) == "0o700"


def test_run_api_refuses_a_symlinked_control_socket_directory(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere"
    target.mkdir()
    control = tmp_path / "control"
    control.symlink_to(target)
    result = _run(
        tmp_path,
        ["run", "radon-api.service"],
        extra_env={"RADON_TEST_CONTROL_DIR": str(control)},
    )
    assert result.returncode == 78
    log_path = result.docker_log  # type: ignore[attr-defined]
    assert not log_path.exists() or " run " not in f" {log_path.read_text(encoding='utf-8')}"


@pytest.mark.parametrize("unit", [
    "radon-nextjs.service",
    "radon-relay.service",
    "radon-monitor.service",
    "radon-newsfeed.service",
])
def test_no_other_container_gets_the_control_socket(tmp_path: Path, unit: str) -> None:
    control = tmp_path / "control"
    result = _run(
        tmp_path,
        ["run", unit],
        extra_env={"RADON_TEST_CONTROL_DIR": str(control)},
    )
    assert result.returncode == 0, result.stderr
    log = result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert "radon-control" not in log
    assert "RADON_CONTROL_SOCKET" not in log


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
    # Read-only: host radon processes launch the same cached browsers, so the
    # third-party-content container must not be able to rewrite them.
    assert f"{state_dir / 'ms-playwright'}:/ms-playwright:ro" in log, log
    assert "PLAYWRIGHT_BROWSERS_PATH=/ms-playwright" in log
    assert f"{tmp_path / 'data' / 'newsfeed-scripts'}:/home/radon/radon/scripts/newsfeed:ro" in log, log


def _newsfeed_run_args(tmp_path: Path, extra_env: dict[str, str] | None = None) -> list[str]:
    result = _run(tmp_path, ["run", "radon-newsfeed.service"], extra_env=extra_env)
    assert result.returncode == 0, result.stderr
    log = result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    run_lines = [line for line in log.splitlines() if line.startswith("run ")]
    assert len(run_lines) == 1, log
    return run_lines[0].split()


def test_run_newsfeed_does_not_join_the_host_ipc_namespace(tmp_path: Path) -> None:
    """DS-2026-09-20-04: Chromium renders hostile third-party pages. Host IPC
    put its shared memory and SysV/POSIX IPC in the host namespace."""
    args = _newsfeed_run_args(tmp_path)
    assert "--ipc" not in args
    assert not any(arg.startswith("--ipc=") for arg in args)


def test_run_newsfeed_sizes_its_own_dev_shm(tmp_path: Path) -> None:
    """Without host IPC the container gets its own /dev/shm, which Docker and
    Podman size at 64 MiB. Chromium crashes renderers on that, so size it."""
    args = _newsfeed_run_args(tmp_path)
    assert args[args.index("--shm-size") + 1] == "512m"


def test_run_newsfeed_uses_the_vendored_chromium_seccomp_profile(tmp_path: Path) -> None:
    args = _newsfeed_run_args(tmp_path)
    profile = CLOUD / "config" / "seccomp" / "chromium.json"
    opts = [args[i + 1] for i, arg in enumerate(args) if arg == "--security-opt"]
    assert f"seccomp={profile}" in opts, opts
    assert "no-new-privileges" in opts
    assert "seccomp=unconfined" not in opts


def test_run_newsfeed_production_profile_path_is_root_owned_control_plane() -> None:
    text = RUNTIME.read_text(encoding="utf-8")
    assert "CHROMIUM_SECCOMP_PROFILE=/etc/radon/seccomp/chromium.json" in text


def test_run_newsfeed_refuses_to_start_without_the_seccomp_profile(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        ["run", "radon-newsfeed.service"],
        extra_env={"RADON_TEST_SECCOMP_PROFILE": str(tmp_path / "absent.json")},
    )
    assert result.returncode == 78, result.stderr
    assert "seccomp profile" in result.stderr
    log = result.docker_log.read_text(encoding="utf-8") if result.docker_log.exists() else ""  # type: ignore[attr-defined]
    assert not [line for line in log.splitlines() if line.startswith("run ")]


def _planted_profile(tmp_path: Path, name: str = "seccomp") -> Path:
    """A well-formed profile at <tmp>/<name>/chromium.json, like the installed
    control-plane copy at /etc/radon/seccomp/chromium.json."""
    directory = tmp_path / name
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o755)
    profile = directory / "chromium.json"
    profile.write_text('{"defaultAction": "SCMP_ACT_ERRNO"}\n', encoding="utf-8")
    profile.chmod(0o644)
    return profile


def _newsfeed_profile_result(tmp_path: Path, profile: Path):
    return _run(
        tmp_path,
        ["run", "radon-newsfeed.service"],
        extra_env={"RADON_TEST_SECCOMP_PROFILE": str(profile)},
    )


def test_run_newsfeed_refuses_a_seccomp_profile_under_a_symlinked_directory(
    tmp_path: Path,
) -> None:
    """DS-2026-09-24-01 residual: /etc/radon is radon-writable (1770), so the
    profile's own directory is the untrusted component. A leaf -f/-L test
    follows a planted directory link and loads a radon-authored filter."""
    real = _planted_profile(tmp_path, "radon-owned")
    link = tmp_path / "seccomp"
    link.symlink_to(real.parent)
    result = _newsfeed_profile_result(tmp_path, link / "chromium.json")
    assert result.returncode == 78, result.stderr
    assert "seccomp profile" in result.stderr
    log = result.docker_log.read_text(encoding="utf-8") if result.docker_log.exists() else ""  # type: ignore[attr-defined]
    assert not [line for line in log.splitlines() if line.startswith("run ")]


def test_run_newsfeed_refuses_a_group_writable_seccomp_profile(tmp_path: Path) -> None:
    profile = _planted_profile(tmp_path)
    profile.chmod(0o664)
    result = _newsfeed_profile_result(tmp_path, profile)
    assert result.returncode == 78, result.stderr
    assert "seccomp profile" in result.stderr


def test_run_newsfeed_refuses_a_group_writable_seccomp_directory(tmp_path: Path) -> None:
    profile = _planted_profile(tmp_path)
    profile.parent.chmod(0o775)
    result = _newsfeed_profile_result(tmp_path, profile)
    assert result.returncode == 78, result.stderr
    assert "seccomp profile" in result.stderr


def test_run_newsfeed_refuses_a_symlinked_seccomp_profile(tmp_path: Path) -> None:
    profile = _planted_profile(tmp_path)
    link = profile.parent / "linked.json"
    link.symlink_to(profile)
    result = _newsfeed_profile_result(tmp_path, link)
    assert result.returncode == 78, result.stderr
    assert "seccomp profile" in result.stderr


def test_run_newsfeed_starts_with_a_private_owner_only_writable_profile(
    tmp_path: Path,
) -> None:
    profile = _planted_profile(tmp_path)
    result = _newsfeed_profile_result(tmp_path, profile)
    assert result.returncode == 0, result.stderr
    log = result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    run_lines = [line for line in log.splitlines() if line.startswith("run ")]
    assert len(run_lines) == 1, log
    assert f"seccomp={profile}" in run_lines[0]


def test_run_newsfeed_no_longer_forces_the_chromium_sandbox_off(tmp_path: Path) -> None:
    """The profile lets Chromium build its namespace sandbox, so the runtime
    stops injecting PLAYWRIGHT_CHROMIUM_SANDBOX=0 (which meant --no-sandbox)."""
    args = _newsfeed_run_args(tmp_path)
    assert "PLAYWRIGHT_CHROMIUM_SANDBOX=0" not in args


@pytest.mark.parametrize(
    "unit", [u for u in APP_UNITS if u != "radon-newsfeed.service"]
)
def test_run_other_units_keep_the_engine_default_seccomp(tmp_path: Path, unit: str) -> None:
    result = _run(tmp_path, ["run", unit])
    assert result.returncode == 0, result.stderr
    log = result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert "seccomp=" not in log
    assert "--shm-size" not in log


def test_run_newsfeed_source_overlay_is_read_only(tmp_path: Path) -> None:
    """The newsfeed drives sandbox-disabled Chromium over third-party pages,
    so its source overlay bind must be read-only; runtime writes go to the
    data and media mounts, never into scripts/newsfeed."""
    result = _run(tmp_path, ["run", "radon-newsfeed.service"])
    assert result.returncode == 0, result.stderr
    log = result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    scripts_binds = [
        arg
        for arg in log.split()
        if ":/home/radon/radon/scripts/newsfeed" in arg
    ]
    assert scripts_binds, log
    assert all(bind.endswith(":ro") for bind in scripts_binds), scripts_binds


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
    # Cleanup alone serializes with deploy; running a service never takes its lock.
    run_body = text.split("cmd_run()", 1)[1].split("cmd_stop()", 1)[0]
    assert "flock" not in run_body
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
    # Post-deploy cleanup verb: listed in full, no argument, no wildcard.
    assert "/usr/local/sbin/radon-app-runtime prune," in text
    assert "radon-app-runtime prune *" not in text
    # The lock-held prune takes the release SHA only, like `pull`; root's
    # weekly sweep calls the no-SHA form directly, never through sudo.
    assert "/usr/local/sbin/radon-app-runtime prune-locked [0-9a-f]*" in text
    assert "radon-app-runtime prune-locked *" not in text
    assert "radon-app-runtime prune-locked," not in text
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
    # The GHCR write grant lives on the ci.yml caller; the reusable workflow
    # itself carries no permissions so the PR caller's read-only token rules.
    assert "packages: write" not in wf
    assert "packages: write" in ci
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
# WITH its quotes: a quoted provider key → the provider answered 400 "Incorrect API key"
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
        "CEREBRAS_API_KEY='csk-abc$1'\n"
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
        + UNQUOTED_GATEWAY_SECRET_LINE +
        "MENTHORQ_PASS=it''s\n"
        "PLAIN=unquoted\n"
    )
    # The host file is the secret of record and is never rewritten.
    assert "CEREBRAS_API_KEY='csk-abc$1'" in host_env.read_text(encoding="utf-8")


PREPAID_NAMES = (
    "ANTHROPIC_API_KEY", "CLAUDE_CODE_API_KEY", "CLAUDE_API_KEY",
    "XAI_API_KEY", "GROK_API_KEY", "OPENAI_API_KEY", "RADON_LADDER_ALLOW_PREPAID",
)


@pytest.mark.parametrize(
    "unit", ["radon-nextjs.service", "radon-api.service", "radon-newsfeed.service"]
)
def test_run_strips_prepaid_provider_keys_from_every_container_env(
    tmp_path: Path, unit: str
) -> None:
    """Subscriptions only (2026-10-26): a prepaid key left in /etc/radon/env
    never reaches a container, where an agent CLI would otherwise bill it."""
    host_env = tmp_path / "secrets.env"
    host_env.write_text(
        "NODE_ENV=production\n"
        + "".join(f"{name}='prepaid-{i}'\n" for i, name in enumerate(PREPAID_NAMES))
        + "CLAUDE_CODE_OAUTH_TOKEN=grant\n"
        "CEREBRAS_API_KEY=k2\n",
        encoding="utf-8",
    )
    result = _run(tmp_path, ["run", unit], extra_env={"RADON_TEST_ENV_FILE": str(host_env)})
    assert result.returncode == 0, result.stderr + result.stdout
    match = re.search(r"--env-file (\S+)", _run_line(result))
    assert match, _run_line(result)
    rendered = Path(match.group(1)).read_text(encoding="utf-8")
    assert "prepaid-" not in rendered
    keys = {line.split("=", 1)[0] for line in rendered.splitlines() if "=" in line}
    assert "NODE_ENV" in keys
    assert not {"CLAUDE_CODE_OAUTH_TOKEN", "CEREBRAS_API_KEY"} & keys
    assert "prepaid-" in host_env.read_text(encoding="utf-8")  # host file untouched


def test_run_newsfeed_env_file_carries_only_its_allowlisted_keys(
    tmp_path: Path,
) -> None:
    """The newsfeed unit drives a sandbox-disabled Chromium over third-party
    content; its rendered env file must carry only the keys its own code
    reads, never the full production secret set."""
    host_env = tmp_path / "secrets.env"
    host_env.write_text(
        "NODE_ENV=production\n"
        + QUOTED_GATEWAY_SECRET_LINE +
        "TURSO_DB_URL=libsql://example.turso.io\n"
        "TURSO_AUTH_TOKEN='tok'\n"
        "ANTHROPIC_API_KEY=k1\n"
        "CLAUDE_CODE_OAUTH_TOKEN=k-cc\n"
        "RADON_LADDER_ALLOW_PREPAID=0\n"
        "XAI_API_KEY=k-xai\n"
        "OPENAI_API_KEY=k-oai\n"
        "GEMINI_API_KEY=k-gem\n"
        "ANTIGRAVITY_MODEL=m-agy\n"
        "NVIDIA_API_KEY=k-nv\n"
        "CEREBRAS_API_KEY=k2\n"
        "RADON_PYTHON_BIN=/usr/bin/python3.13\n"
        "RADON_NEWSFEED_HEADLESS=1\n"
        "RADON_MEDIA_REMOTE=/home/radon/radon-cloud/media/\n"
        "CLERK_SECRET_KEY=sk\n"
        "MENTHORQ_PASS=mq\n",
        encoding="utf-8",
    )
    result = _run(
        tmp_path, ["run", "radon-newsfeed.service"],
        extra_env={"RADON_TEST_ENV_FILE": str(host_env)},
    )
    assert result.returncode == 0, result.stderr + result.stdout
    match = re.search(r"--env-file (\S+)", _run_line(result))
    assert match, _run_line(result)
    rendered = Path(match.group(1)).read_text(encoding="utf-8")
    keys = {
        line.split("=", 1)[0]
        for line in rendered.splitlines()
        if "=" in line and not line.startswith("#")
    }
    assert GATEWAY_SECRET_KEY not in keys
    assert "CLERK_SECRET_KEY" not in keys
    assert "MENTHORQ_PASS" not in keys
    assert "GEMINI_API_KEY" not in keys  # retired: Google is the Antigravity CLI only
    # Subscriptions only (2026-10-26): prepaid keys never reach a container.
    assert not {"ANTHROPIC_API_KEY", "RADON_LADDER_ALLOW_PREPAID", "XAI_API_KEY", "OPENAI_API_KEY"} & keys
    assert {
        "NODE_ENV",
        "TURSO_DB_URL",
        "TURSO_AUTH_TOKEN",
        "RADON_PYTHON_BIN",
        "RADON_NEWSFEED_HEADLESS",
        "RADON_MEDIA_REMOTE",
    } <= keys
    # Quote stripping still applies on the filtered path.
    assert "TURSO_AUTH_TOKEN=tok" in rendered.splitlines()


def test_run_newsfeed_env_file_preserves_explicit_nvidia_model_controls(
    tmp_path: Path,
) -> None:
    """Model overrides reach the restricted container without widening its keys."""
    host_env = tmp_path / "models.env"
    model_values = {
        "NVIDIA_TEXT_MODEL": "nvidia/nemotron-3-super-120b-a12b",
        "NVIDIA_VISION_MODEL": "nvidia/nemotron-3.5-super-vl-120b-a12b",
        "NVIDIA_MODEL": "meta/llama-3.2-90b-vision-instruct",
    }
    host_env.write_text(
        "NODE_ENV=production\n"
        + "".join(f"{key}='{value}'\n" for key, value in model_values.items())
        + "NVIDIA_MODEL_PRIVATE=excluded\n"
        + "NVIDIA_VISION_MODEL_SUFFIX=excluded\n"
        + "NVIDIA_UNRELATED_KEY=excluded\n"
        + "CLERK_SECRET_KEY=excluded\n"
        + QUOTED_GATEWAY_SECRET_LINE,
        encoding="utf-8",
    )
    original = host_env.read_text(encoding="utf-8")
    result = _run(
        tmp_path, ["run", "radon-model-gateway.service"],
        extra_env={"RADON_TEST_ENV_FILE": str(host_env)},
    )
    assert result.returncode == 0, result.stderr + result.stdout
    match = re.search(r"--env-file (\S+)", _run_line(result))
    assert match, _run_line(result)
    rendered = Path(match.group(1))
    values = dict(
        line.split("=", 1)
        for line in rendered.read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.startswith("#")
    )
    assert values == model_values
    assert stat.S_IMODE(rendered.stat().st_mode) == 0o600
    assert host_env.read_text(encoding="utf-8") == original


def test_run_newsfeed_env_file_carries_the_themarketear_login(
    tmp_path: Path,
) -> None:
    """The scraper re-authenticates itself when the session cookie dies.

    `scripts/newsfeed/auth.js` reads THEMARKETEAR_EMAIL / THEMARKETEAR_PASSWORD
    and throws `Missing THEMARKETEAR_EMAIL or THEMARKETEAR_PASSWORD environment
    variable.` without them. The allowlist omitted both, so the container ran on
    the cookie it started with and every cycle after the 2026-09-20 15:11 UTC
    `paywall stubs detected in 25/25 posts - re-auth scheduled` died in
    pre-cycle. The dashboard feed stopped at 14:00 UTC that day.
    """
    host_env = tmp_path / "secrets.env"
    host_env.write_text(
        "NODE_ENV=production\n"
        "THEMARKETEAR_EMAIL=reader@example.invalid\n"
        "THEMARKETEAR_PASSWORD='pw-with-$dollar'\n"
        "MARKETDESK_FIREBASE_REFRESH_TOKEN=same-account-grant\n"
        "CLERK_SECRET_KEY=sk\n",
        encoding="utf-8",
    )
    result = _run(
        tmp_path, ["run", "radon-newsfeed.service"],
        extra_env={"RADON_TEST_ENV_FILE": str(host_env)},
    )
    assert result.returncode == 0, result.stderr + result.stdout
    match = re.search(r"--env-file (\S+)", _run_line(result))
    assert match, _run_line(result)
    lines = Path(match.group(1)).read_text(encoding="utf-8").splitlines()
    assert "THEMARKETEAR_EMAIL=reader@example.invalid" in lines, lines
    # Quote stripping applies here too, or the password reaches the browser
    # wrapped in the quotes /etc/radon/env needs for its `$`.
    assert "THEMARKETEAR_PASSWORD=pw-with-$dollar" in lines, lines
    assert "MARKETDESK_FIREBASE_REFRESH_TOKEN=same-account-grant" in lines
    keys = {line.split("=", 1)[0] for line in lines if "=" in line}
    assert "CLERK_SECRET_KEY" not in keys


def test_run_non_newsfeed_units_keep_the_full_env_file(tmp_path: Path) -> None:
    host_env = tmp_path / "secrets.env"
    host_env.write_text(
        "NODE_ENV=production\n" + UNQUOTED_GATEWAY_SECRET_LINE,
        encoding="utf-8",
    )
    result = _run(
        tmp_path, ["run", "radon-api.service"],
        extra_env={"RADON_TEST_ENV_FILE": str(host_env)},
    )
    assert result.returncode == 0, result.stderr + result.stdout
    match = re.search(r"--env-file (\S+)", _run_line(result))
    assert match, _run_line(result)
    rendered = Path(match.group(1)).read_text(encoding="utf-8")
    assert UNQUOTED_GATEWAY_SECRET_LINE.strip() in rendered.splitlines()


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


def test_research_runtime_is_private_and_uses_python_image(tmp_path):
    result = _run(tmp_path, ["run", "radon-research.service"])
    assert result.returncode == 0, result.stderr
    log = result.docker_log.read_text()
    assert "radon-python:" in log and "python -m research.worker --daemon" in log
    assert f"{tmp_path / 'state' / 'private' / 'research'}:/var/lib/radon/research:rw" in log
    assert ":/var/lib/radon/media" not in log
    assert ":/home/radon/radon/data" not in log
    assert ":/var/lib/radon/ib-lease" not in log
    assert (tmp_path / "state" / "private" / "research").stat().st_mode & 0o777 == 0o700


def test_research_archive_runtime_gets_only_the_private_research_bind(tmp_path):
    result = _run(tmp_path, ["run", "radon-research-archive.service"])
    assert result.returncode == 0, result.stderr
    log = result.docker_log.read_text()
    assert "radon-python:" in log and "python scripts/research/archive.py" in log
    assert f"{tmp_path / 'state' / 'private' / 'research'}:/var/lib/radon/research:rw" in log
    assert ":/var/lib/radon/media" not in log
    assert ":/home/radon/radon/data" not in log
    assert ":/var/lib/radon/ib-lease" not in log
    assert "/.claude:" not in log and "/.codex:" not in log
    assert "CREDENTIALS_DIRECTORY" not in log


def test_research_asset_archive_runtime_gets_only_the_private_research_bind(tmp_path):
    result = _run(tmp_path, ["run", "radon-research-asset-archive.service"])
    assert result.returncode == 0, result.stderr
    log = result.docker_log.read_text()
    assert "radon-python:" in log and "python scripts/research/asset_archive.py" in log
    assert f"{tmp_path / 'state' / 'private' / 'research'}:/var/lib/radon/research:rw" in log
    assert ":/var/lib/radon/media" not in log
    assert ":/home/radon/radon/data" not in log
    assert ":/var/lib/radon/ib-lease" not in log
    assert "/.claude:" not in log and "/.codex:" not in log
    assert "CREDENTIALS_DIRECTORY" not in log


def test_api_research_mount_is_read_only(tmp_path):
    result = _run(tmp_path, ["run", "radon-api.service"])
    assert result.returncode == 0, result.stderr
    assert f"{tmp_path / 'state' / 'private' / 'research'}:/var/lib/radon/research:ro" in result.docker_log.read_text()


def test_other_apps_cannot_read_private_research(tmp_path):
    result = _run(tmp_path, ["run", "radon-newsfeed.service"])
    assert result.returncode == 0, result.stderr
    assert ":/var/lib/radon/research" not in result.docker_log.read_text()

@pytest.mark.parametrize('attack', ['anchor_symlink', 'child_symlink', 'writable_parent', 'public_anchor'])
def test_research_mount_refuses_untrusted_ancestors(tmp_path, attack):
    state = tmp_path / 'state'
    state.mkdir()
    anchor = state / 'private'
    outside = tmp_path / 'outside'
    outside.mkdir(mode=0o700)
    if attack == 'anchor_symlink':
        anchor.symlink_to(outside, target_is_directory=True)
    elif attack == 'child_symlink':
        anchor.mkdir(mode=0o700)
        (anchor / 'research').symlink_to(outside, target_is_directory=True)
    elif attack == 'writable_parent':
        state.chmod(0o777)
    else:
        anchor.mkdir(mode=0o755)
    result = _run(tmp_path, ['run', 'radon-research.service'])
    assert result.returncode == 78, result.stderr
    assert not any(line.startswith('run ') for line in result.docker_log.read_text().splitlines())
    assert outside.stat().st_mode & 0o777 == 0o700
    assert list(outside.iterdir()) == []


def test_api_private_anchor_rejection_cleans_staged_credentials(tmp_path):
    state = tmp_path / 'state'
    state.mkdir()
    (state / 'private').mkdir(mode=0o755)
    result = _run(tmp_path, ['run', 'radon-api.service'])
    assert result.returncode == 78, result.stderr
    credential_dir = Path(result.proxy_dir) / 'credentials' / 'radon-api.service'
    assert not credential_dir.exists()
    assert not any(line.startswith('run ') for line in result.docker_log.read_text().splitlines())


def test_nextjs_runtime_executes_the_baked_key_guard(tmp_path: Path) -> None:
    result = _run(tmp_path, ["run", "radon-nextjs.service"])
    assert result.returncode == 0, result.stderr
    assert _run_line(result).endswith(" /usr/local/bin/next-clerk-guard")


# -- the gateway is the sole subscription credential owner -------------------


def _subscription_home(tmp_path: Path) -> Path:
    home = tmp_path / "radon-home"
    (home / ".grok").mkdir(parents=True)
    (home / ".grok" / "auth.json").write_text("{}", encoding="utf-8")
    (home / ".codex").mkdir()
    (home / ".gemini" / "antigravity-cli").mkdir(parents=True)
    (home / ".gemini" / "config").mkdir()
    (home / ".gemini" / "oauth_creds.json").write_text("{}", encoding="utf-8")
    (home / ".local" / "bin").mkdir(parents=True)
    return home


def test_run_api_binds_subscription_credential_dirs_readonly(tmp_path: Path) -> None:
    home = _subscription_home(tmp_path)
    result = _run(
        tmp_path,
        ["run", "radon-api.service"],
        extra_env={"RADON_SUBSCRIPTION_HOME": str(home)},
    )
    assert result.returncode == 0, result.stderr
    log = _run_line(result)
    assert "/run/radon-model-gateway:ro" in log
    assert "RADON_MODEL_GATEWAY_REQUIRED=1" in log
    for grant in (".grok", ".codex", ".claude", ".gemini", ".local/bin"):
        assert f"{home}/{grant}" not in log
    assert "HOME=/home/radon" in log


@pytest.mark.parametrize("cred_dir", (".claude", ".gemini/config", ".codex"))
def test_run_refuses_a_symlinked_subscription_bind_source(tmp_path: Path, cred_dir: str) -> None:
    """Subscription dirs sit in radon's home; a link there is refused, not bound."""
    home = _subscription_home(tmp_path)
    _swap_for_link(home / cred_dir, tmp_path / "subscription-elsewhere")
    result = _run(
        tmp_path,
        ["run", "radon-model-gateway.service"],
        extra_env={"RADON_SUBSCRIPTION_HOME": str(home)},
    )
    assert result.returncode == 78, result.stderr
    assert "bind source" in result.stderr
    assert not _engine_ran(result)


def test_run_research_binds_subscription_and_antigravity(tmp_path: Path) -> None:
    home = _subscription_home(tmp_path)
    result = _run(
        tmp_path,
        ["run", "radon-research.service"],
        extra_env={"RADON_SUBSCRIPTION_HOME": str(home)},
    )
    assert result.returncode == 0, result.stderr
    log = _run_line(result)
    assert "/run/radon-model-gateway:ro" in log
    assert "RADON_MODEL_GATEWAY_REQUIRED=1" in log
    for grant in (".grok", ".codex", ".claude", ".gemini", ".local/bin"):
        assert f"{home}/{grant}" not in log
    assert "HOME=/home/radon" in log


def test_run_nextjs_binds_subscription_credential_dirs_readonly(tmp_path: Path) -> None:
    # 2026-09-19: excluding nextjs produced POST /api/newsfeed/share 502
    # "Missing Anthropic subscription" while api/newsfeed had the grants.
    home = _subscription_home(tmp_path)
    result = _run(
        tmp_path,
        ["run", "radon-nextjs.service"],
        extra_env={"RADON_SUBSCRIPTION_HOME": str(home)},
    )
    assert result.returncode == 0, result.stderr
    log = _run_line(result)
    assert "/run/radon-model-gateway:ro" in log
    assert "RADON_MODEL_GATEWAY_REQUIRED=1" in log
    for grant in (".grok", ".codex", ".claude", ".gemini", ".local/bin"):
        assert f"{home}/{grant}" not in log
    assert "HOME=/home/radon" in log


def test_run_relay_gets_no_subscription_credential_binds(tmp_path: Path) -> None:
    home = _subscription_home(tmp_path)
    result = _run(
        tmp_path,
        ["run", "radon-relay.service"],
        extra_env={"RADON_SUBSCRIPTION_HOME": str(home)},
    )
    assert result.returncode == 0, result.stderr
    log = result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert ".grok" not in log and ".codex" not in log and ".claude" not in log


def test_run_newsfeed_binds_subscription_credential_dirs_readonly(tmp_path: Path) -> None:
    home = _subscription_home(tmp_path)
    result = _run(
        tmp_path,
        ["run", "radon-newsfeed.service"],
        extra_env={"RADON_SUBSCRIPTION_HOME": str(home)},
    )
    assert result.returncode == 0, result.stderr
    log = _run_line(result)
    assert "/run/radon-model-gateway:ro" in log
    assert "RADON_MODEL_GATEWAY_REQUIRED=1" in log
    for grant in (".grok", ".codex", ".claude", ".gemini", ".local/bin"):
        assert f"{home}/{grant}" not in log
    assert "HOME=/home/radon" in log


def test_run_skips_subscription_binds_when_no_dir_exists(tmp_path: Path) -> None:
    home = tmp_path / "empty-home"
    home.mkdir()
    result = _run(
        tmp_path,
        ["run", "radon-api.service"],
        extra_env={"RADON_SUBSCRIPTION_HOME": str(home)},
    )
    assert result.returncode == 0, result.stderr
    log = result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert ".grok" not in log and ".codex" not in log and ".claude" not in log


def test_run_api_mounts_robinhood_token_dir_rw_and_points_the_client_at_it(
    tmp_path: Path,
) -> None:
    """The containerized API could not see /etc/radon/rh-mcp.json, so every
    Robinhood rung inside it silently fell through to UW. The token lives in
    its own dir (never /etc/radon), mounted rw so rotation's atomic replace
    and the flock sidecar work."""
    rh_dir = tmp_path / "rh-mcp"
    rh_dir.mkdir()
    result = _run(
        tmp_path,
        ["run", "radon-api.service"],
        extra_env={"RADON_RH_TOKEN_DIR": str(rh_dir)},
    )
    assert result.returncode == 0, result.stderr
    log = result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert f"-v {rh_dir}:{rh_dir} " in log or log.rstrip().endswith(f"-v {rh_dir}:{rh_dir}"), log
    assert f"{rh_dir}:{rh_dir}:ro" not in log
    assert f"ROBINHOOD_MCP_TOKEN_FILE={rh_dir}/rh-mcp.json" in log
    assert ":/etc/radon " not in log and ":/etc/radon:" not in log


def test_run_api_skips_robinhood_token_mount_when_absent(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        ["run", "radon-api.service"],
        extra_env={"RADON_RH_TOKEN_DIR": str(tmp_path / "no-rh-mcp")},
    )
    assert result.returncode == 0, result.stderr
    log = result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert "rh-mcp" not in log


def test_run_newsfeed_does_not_mount_robinhood_token_dir(tmp_path: Path) -> None:
    rh_dir = tmp_path / "rh-mcp"
    rh_dir.mkdir()
    result = _run(
        tmp_path,
        ["run", "radon-newsfeed.service"],
        extra_env={"RADON_RH_TOKEN_DIR": str(rh_dir)},
    )
    assert result.returncode == 0, result.stderr
    log = result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert "rh-mcp" not in log


def test_api_boot_bounds_the_schema_migration() -> None:
    # 2026-09-25: an unbounded migrate.py held :8321 closed for 156s during a
    # Turso brownout, failing the deploy and rollback gates.
    text = RUNTIME.read_text(encoding="utf-8")
    api_command = text.split("radon-api.service)", 1)[1].split(";;", 1)[0]
    assert "python scripts/db/migrate.py --boot &&" in api_command


@pytest.mark.parametrize("fault,unit", [
    (fault, unit)
    for fault in ("sed", "grep", "missing_input")
    for unit in ("radon-relay.service", "radon-newsfeed.service")
    if fault != "grep" or unit == "radon-newsfeed.service"
])
def test_rel158_failed_env_render_never_starts_container(tmp_path: Path, unit: str, fault: str) -> None:
    """R-438: failed render/filter must refuse before the fake engine run."""
    rendered_dir = tmp_path / "rendered"
    rendered_dir.mkdir()
    prior = rendered_dir / (unit + ".env")
    prior.write_text("NODE_ENV=production\n")
    extra = {"NOTIFY_SOCKET": "", "RADON_TEST_NOTIFY_PROXY_DIR": str(rendered_dir)}
    if fault == "missing_input":
        extra["RADON_TEST_ENV_FILE"] = str(tmp_path / "missing-input")
    else:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _write_executable(bin_dir / fault, "#!/bin/bash\nexit 2\n")
        extra["PATH"] = str(bin_dir) + os.pathsep + os.environ["PATH"]
    result = _run(tmp_path, ["run", unit], extra_env=extra)
    assert result.returncode == 71, result.stderr
    assert not any(line.startswith("run ") for line in result.docker_log.read_text().splitlines())
    assert prior.read_text() == "NODE_ENV=production\n"
    assert list(rendered_dir.iterdir()) == [prior]


def test_rel158_notify_proxy_filters_container_control_messages() -> None:
    """R-439: container notices cannot change the host unit's lifecycle/PID."""
    with tempfile.TemporaryDirectory(prefix="rdn", dir="/tmp") as directory:
        root = Path(directory)
        upstream_path, listen_path = root / "up", root / "in"
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as upstream:
            upstream.bind(str(upstream_path))
            upstream.settimeout(5)
            proc = subprocess.Popen(
                ["bash", str(RUNTIME), "notify-proxy", str(listen_path), str(upstream_path)],
                env={**_runtime_env(root), "RADON_TEST_PYTHON": sys.executable},
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            )
            try:
                deadline = time.monotonic() + 5
                while not listen_path.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                assert listen_path.exists(), "notify proxy did not bind"
                with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
                    client.sendto(b"MAINPID=1\nSTOPPING=1\n", str(listen_path))
                    client.sendto(
                        b"READY=1\nMAINPID=2\nWATCHDOG=trigger\nWATCHDOG=1\n"
                        b"RELOADING=1\nSTATUS=collecting fills\nEXTEND_TIMEOUT_USEC=999999\n",
                        str(listen_path),
                    )
                    # The first packet must disappear entirely; the mixed
                    # packet retains exactly the three permitted notices.
                    assert upstream.recv(65536).splitlines() == [
                        b"READY=1", b"WATCHDOG=1", b"STATUS=collecting fills",
                    ]
                    client.sendto(b"READY=1\n", str(listen_path))
                    assert upstream.recv(65536).splitlines() == [b"READY=1"]
            finally:
                proc.terminate()
                proc.wait(timeout=5)
                proc.stderr.close()


# DS-2026-10-17-20: media.radon.run serves /var/lib/radon/media through Caddy
# file_server, which follows symlinks, and that directory was bind-mounted
# READ-WRITE into every app unit. Any one of them could plant a symlink and
# have the host Caddy process read a file the container cannot. The newsfeed
# is the only writer -- it downloads the images -- so every other unit loses
# the capability. The newsfeed's own write path is a publication-topology
# change and is carried as an operator action.

@pytest.mark.parametrize(
    "unit",
    [
        "radon-api.service",
        "radon-nextjs.service",
        "radon-monitor.service",
        "radon-relay.service",
    ],
)
def test_run_mounts_media_read_only_outside_the_newsfeed(tmp_path: Path, unit: str) -> None:
    result = _run(tmp_path, ["run", unit])
    assert result.returncode == 0, result.stderr
    log = result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    media = [arg for arg in log.split() if ":/var/lib/radon/media" in arg]
    assert media, log
    for arg in media:
        assert arg.endswith(":/var/lib/radon/media:ro"), arg


def test_run_newsfeed_keeps_write_on_the_media_volume_it_fills(tmp_path: Path) -> None:
    result = _run(tmp_path, ["run", "radon-newsfeed.service"])
    assert result.returncode == 0, result.stderr
    log = result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    media = [arg for arg in log.split() if ":/var/lib/radon/media" in arg]
    assert media, log
    for arg in media:
        assert not arg.endswith(":ro"), arg


# -- radon-marketdesk.service (2026-10-26) ------------------------------------
#
# The marketdesk.ai ingest drives Chromium over third-party pages AND writes
# the private research assets. It is a research unit (rw private bind, no
# data/media/lease binds) with the newsfeed's isolation: bridge network, the
# vendored Chromium seccomp profile, a sized /dev/shm and an env allowlist.

MARKETDESK = "radon-marketdesk.service"


def _marketdesk_args(tmp_path: Path, extra_env: dict[str, str] | None = None) -> list[str]:
    result = _run(tmp_path, ["run", MARKETDESK], extra_env=extra_env)
    assert result.returncode == 0, result.stderr
    return _run_line(result).split()


def test_marketdesk_is_an_app_unit_in_the_runtime() -> None:
    text = RUNTIME.read_text(encoding="utf-8")
    app_units = re.search(r'^readonly APP_UNITS="([^"]*)"', text, re.M).group(1).split()
    assert MARKETDESK in app_units


def test_marketdesk_runs_the_job_in_the_python_image(tmp_path: Path) -> None:
    result = _run(tmp_path, ["run", MARKETDESK])
    assert result.returncode == 0, result.stderr
    line = _run_line(result)
    assert f"{PYTHON_REPO}:{TEST_SHA} python scripts/research/marketdesk_job.py" in line
    assert "-w /home/radon/radon " in line


def test_marketdesk_gets_only_the_private_research_bind_read_write(tmp_path: Path) -> None:
    result = _run(tmp_path, ["run", MARKETDESK])
    assert result.returncode == 0, result.stderr
    log = result.docker_log.read_text()
    assert f"{tmp_path / 'state' / 'private' / 'research'}:/var/lib/radon/research:rw" in log
    assert "RADON_RESEARCH_DIR=/var/lib/radon/research" in log
    assert ":/var/lib/radon/media" not in log
    assert ":/home/radon/radon/data" not in log
    assert ":/var/lib/radon/ib-lease" not in log
    assert "CREDENTIALS_DIRECTORY" not in log
    assert "/run/radon-control" not in log


def test_marketdesk_uses_an_isolated_bridge_network(tmp_path: Path) -> None:
    args = _marketdesk_args(tmp_path)
    assert args[args.index("--network") + 1] == "bridge"
    assert "--ipc" not in args


def test_marketdesk_uses_the_chromium_seccomp_profile_and_sized_shm(tmp_path: Path) -> None:
    args = _marketdesk_args(tmp_path)
    profile = CLOUD / "config" / "seccomp" / "chromium.json"
    opts = [args[i + 1] for i, arg in enumerate(args) if arg == "--security-opt"]
    assert f"seccomp={profile}" in opts, opts
    assert "no-new-privileges" in opts
    assert "seccomp=unconfined" not in opts
    assert args[args.index("--shm-size") + 1] == "512m"
    # The Python image carries its own pinned headless shell; the newsfeed's
    # host browser cache and source overlay are not this unit's.
    assert not any(":/ms-playwright" in arg for arg in args)
    assert not any(":/home/radon/radon/scripts/newsfeed" in arg for arg in args)


def test_marketdesk_refuses_to_start_without_the_seccomp_profile(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        ["run", MARKETDESK],
        extra_env={"RADON_TEST_SECCOMP_PROFILE": str(tmp_path / "absent.json")},
    )
    assert result.returncode == 78, result.stderr
    assert "seccomp profile" in result.stderr
    log = result.docker_log.read_text(encoding="utf-8") if result.docker_log.exists() else ""  # type: ignore[attr-defined]
    assert not [line for line in log.splitlines() if line.startswith("run ")]


def test_marketdesk_env_file_carries_only_its_allowlisted_keys(tmp_path: Path) -> None:
    host_env = tmp_path / "secrets.env"
    host_env.write_text(
        "NODE_ENV=production\n"
        + QUOTED_GATEWAY_SECRET_LINE +
        "TURSO_DB_URL=libsql://example.turso.io\n"
        "TURSO_AUTH_TOKEN='tok'\n"
        "CLAUDE_CODE_OAUTH_TOKEN=k-cc\n"
        "RADON_LADDER_ALLOW_PREPAID=0\n"
        "XAI_API_KEY=k-xai\n"
        "OPENAI_API_KEY=k-oai\n"
        "NVIDIA_API_KEY=k-nv\n"
        "CEREBRAS_API_KEY=k2\n"
        "THEMARKETEAR_EMAIL=e@example.com\n"
        "THEMARKETEAR_PASSWORD='pw'\n"
        "MARKETDESK_FIREBASE_REFRESH_TOKEN=rt\n"
        "MARKETDESK_MAX_PER_CYCLE=5\n"
        "MARKETDESK_LOOKBACK_HOURS=48\n"
        "MARKETDESK_SUMMARY_WAIT_HOURS=6\n"
        "MARKETDESK_ASSET_HOSTS=cdn.example.com\n"
        "PLAYWRIGHT_CHROMIUM_EXECUTABLE=/opt/chrome\n"
        "PLAYWRIGHT_CHROMIUM_SANDBOX=1\n"
        "DROPBOX_WRITE_APP_KEY=wk\n"
        "DROPBOX_WRITE_REFRESH_TOKEN=wr\n"
        "DROPBOX_WRITE_ACCOUNT_ID=dbid:x\n"
        "DROPBOX_WRITE_ROOT_NAMESPACE_ID=910135760\n"
        "DROPBOX_WRITE_ROOT=/Radon/MarketDesk\n"
        # Never this unit's: the read-only research reader, B2, IB, Clerk.
        "DROPBOX_APP_KEY=rk\n"
        "DROPBOX_REFRESH_TOKEN=rr\n"
        "RADON_ARCHIVE_S3_SECRET_ACCESS_KEY=b2\n"
        "RADON_SERVICE_TOKEN=svc\n"
        "IB_FLEX_TOKEN=flex\n"
        "CLERK_SECRET_KEY=sk\n"
        "MENTHORQ_PASS=mq\n"
        "PUSHOVER_TOKEN=po\n",
        encoding="utf-8",
    )
    result = _run(
        tmp_path, ["run", MARKETDESK],
        extra_env={"RADON_TEST_ENV_FILE": str(host_env)},
    )
    assert result.returncode == 0, result.stderr + result.stdout
    match = re.search(r"--env-file (\S+)", _run_line(result))
    assert match, _run_line(result)
    rendered = Path(match.group(1)).read_text(encoding="utf-8")
    keys = {
        line.split("=", 1)[0]
        for line in rendered.splitlines()
        if "=" in line and not line.startswith("#")
    }
    assert {
        "TURSO_DB_URL",
        "TURSO_AUTH_TOKEN",
        "THEMARKETEAR_EMAIL",
        "THEMARKETEAR_PASSWORD",
        "MARKETDESK_FIREBASE_REFRESH_TOKEN",
        "MARKETDESK_MAX_PER_CYCLE",
        "MARKETDESK_LOOKBACK_HOURS",
        "MARKETDESK_SUMMARY_WAIT_HOURS",
        "MARKETDESK_ASSET_HOSTS",
        "DROPBOX_WRITE_APP_KEY",
        "DROPBOX_WRITE_REFRESH_TOKEN",
        "DROPBOX_WRITE_ACCOUNT_ID",
        "DROPBOX_WRITE_ROOT_NAMESPACE_ID",
        "DROPBOX_WRITE_ROOT",
    } <= keys
    for forbidden in (
        GATEWAY_SECRET_KEY,
        # Subscriptions only (2026-10-26): prepaid keys never reach a container.
        "RADON_LADDER_ALLOW_PREPAID",
        "XAI_API_KEY",
        "OPENAI_API_KEY",
        "PLAYWRIGHT_CHROMIUM_EXECUTABLE",  # no browser in this unit since the JSON API rewrite
        "PLAYWRIGHT_CHROMIUM_SANDBOX",
        "DROPBOX_APP_KEY",
        "DROPBOX_REFRESH_TOKEN",
        "RADON_ARCHIVE_S3_SECRET_ACCESS_KEY",
        "RADON_SERVICE_TOKEN",
        "IB_FLEX_TOKEN",
        "CLERK_SECRET_KEY",
        "MENTHORQ_PASS",
        "PUSHOVER_TOKEN",
    ):
        assert forbidden not in keys, forbidden
    assert "TURSO_AUTH_TOKEN=tok" in rendered.splitlines()
    assert "THEMARKETEAR_PASSWORD=pw" in rendered.splitlines()


def test_marketdesk_binds_read_only_ladder_grants_only(tmp_path: Path) -> None:
    home = _subscription_home(tmp_path)
    (home / ".claude").mkdir()
    result = _run(
        tmp_path,
        ["run", MARKETDESK],
        extra_env={"RADON_SUBSCRIPTION_HOME": str(home)},
    )
    assert result.returncode == 0, result.stderr
    log = _run_line(result)
    assert "/run/radon-model-gateway:ro" in log
    assert "RADON_MODEL_GATEWAY_REQUIRED=1" in log
    for grant in (".grok", ".codex", ".claude", ".gemini", ".local/bin"):
        assert f"{home}/{grant}" not in log
    assert "HOME=/home/radon" in log
