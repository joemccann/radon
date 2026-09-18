"""Image cleanup cannot remove durable rollback state or trust a stale tag."""
import fcntl
import json
import sys

import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_app_runtime import _run

TARGET = "a" * 40
PREVIOUS = "b" * 40
STALE = "c" * 40
DIGEST = "d" * 64

DOCKER = r'''#!/bin/bash
printf '%s\n' "$*" >> {log}
case "$1 $2" in
  "buildx imagetools")
    printf '{{"digest":"sha256:%s"}}\n' "${{RADON_STUB_REMOTE_DIGEST}}"
    ;;
  "image inspect")
    if [[ "$4" == "--format" ]]; then
      ref="$3"
      printf '["%s@sha256:%s"]\n' "${{ref%:*}}" "${{RADON_STUB_LOCAL_DIGEST}}"
    fi
    ;;
  "ps --format")
    for image in ${{RADON_STUB_RUNNING:-}}; do printf '%s\n' "$image"; done
    ;;
  "images --format")
    for tag in ${{RADON_STUB_TAGS}}; do printf '%s:%s\n' "$4" "$tag"; done
    ;;
esac
exit 0
'''


def env(tmp_path):
    lock = tmp_path / "deploy.lock"
    lock.touch()
    marker = tmp_path / "green"
    marker.write_text(PREVIOUS + "\n")
    return {
        "RADON_TEST_PYTHON": sys.executable,
        "RADON_TEST_DEPLOY_LOCK": str(lock),
        "RADON_TEST_GREEN_MARKER": str(marker),
        "RADON_TEST_TRANSITION_JOURNAL": str(tmp_path / "transition.json"),
        "RADON_STUB_REMOTE_DIGEST": DIGEST,
        "RADON_STUB_LOCAL_DIGEST": DIGEST,
        "RADON_STUB_RUNNING": f"ghcr.io/joemccann/radon-python:{TARGET}",
        "RADON_STUB_TAGS": f"{TARGET} {PREVIOUS} {STALE}",
    }


def lines(result):
    assert result.returncode == 0, result.stderr
    return result.docker_log.read_text().splitlines()


def test_missing_previous_containers_do_not_make_rollback_images_prunable(tmp_path):
    calls = lines(_run(tmp_path, ["pull", TARGET], extra_env=env(tmp_path), docker_body=DOCKER))
    removed = [s for s in calls if s.startswith("rmi ")]
    assert removed
    assert all(PREVIOUS not in s and TARGET not in s for s in removed)
    assert all(STALE in s for s in removed)


@pytest.mark.parametrize("running", ["", "caddy:2", "ghcr.io/gnzsnz/ib-gateway:stable"])
def test_empty_running_app_population_skips_all_pruning(tmp_path, running):
    settings = {**env(tmp_path), "RADON_STUB_RUNNING": running}
    calls = lines(_run(tmp_path, ["pull", TARGET], extra_env=settings, docker_body=DOCKER))
    assert not any(s.startswith("rmi ") for s in calls)


def test_held_deploy_lock_skips_pruning_without_failing_the_pull(tmp_path):
    settings = env(tmp_path)
    with open(settings["RADON_TEST_DEPLOY_LOCK"], "r+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = _run(tmp_path, ["pull", TARGET], extra_env=settings, docker_body=DOCKER)
    assert not any(s.startswith("rmi ") for s in lines(result))
    assert "lock" in result.stderr.lower()


def test_transition_previous_release_is_protected_even_when_green_marker_differs(tmp_path):
    settings = env(tmp_path)
    Path(settings["RADON_TEST_GREEN_MARKER"]).write_text("e" * 40 + "\n")
    Path(settings["RADON_TEST_TRANSITION_JOURNAL"]).write_text(json.dumps({
        "version": 1, "requested_sha": TARGET, "previous_sha": PREVIOUS,
    }))
    calls = lines(_run(tmp_path, ["pull", TARGET], extra_env=settings, docker_body=DOCKER))
    assert not any(s.startswith("rmi ") and PREVIOUS in s for s in calls)


def test_unknown_rollback_checkpoint_skips_pruning(tmp_path):
    settings = env(tmp_path)
    Path(settings["RADON_TEST_GREEN_MARKER"]).unlink()
    calls = lines(_run(tmp_path, ["pull", TARGET], extra_env=settings, docker_body=DOCKER))
    assert not any(s.startswith("rmi ") for s in calls)


def test_changed_registry_digest_refreshes_the_local_release_pair(tmp_path):
    settings = {**env(tmp_path), "RADON_STUB_REMOTE_DIGEST": "e" * 64}
    calls = lines(_run(tmp_path, ["pull", TARGET], extra_env=settings, docker_body=DOCKER))
    assert f"pull ghcr.io/joemccann/radon-python:{TARGET}" in calls
    assert f"pull ghcr.io/joemccann/radon-node:{TARGET}" in calls


def test_matching_digests_keep_cached_images_without_pulling_layers(tmp_path):
    calls = lines(_run(tmp_path, ["pull", TARGET], extra_env=env(tmp_path), docker_body=DOCKER))
    assert not any(s.startswith("pull ") for s in calls)
    assert sum(s.startswith("buildx imagetools inspect ") for s in calls) == 2
