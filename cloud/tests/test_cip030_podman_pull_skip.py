"""CIP-030: skip a second registry pull when the exact SHA set is already local.

The VPS engine is podman (cloud/CLAUDE.md, radon-app-runtime.sh ENGINE=podman).
`image_matches_registry` used to return 1 immediately on podman because
`docker buildx imagetools` does not exist there, so every deploy's
`prepull_app_images` re-pulled python, node, and gateway even after the
gated Prepull job had already fetched the SHA tags.

Evidence, attempt-1 main deploys (`gh run view --log --job`):
- 43675533211 job 132695469001: Prepull 26s, then Deploy still
  `Trying to pull ghcr.io/joemccann/radon-model-gateway:<sha>` at +20.8s
  and only finished that pull around +44s (38s on the clock).
- 43671158271 job 132682814703: the same pull sat from +21.3s to the
  Turso probe at +123.5s (102s).
- 43630752169 job 132557498370 (python-only): +18.9s to +115.4s (96s).

A confirmed digest mismatch still refreshes. An unreachable registry with
the exact SHA already local does not. No `latest` fallback.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_app_runtime import _run

TARGET = "a" * 40
PREVIOUS = "b" * 40
DIGEST = "c" * 64
OTHER = "d" * 64
PYTHON = f"ghcr.io/joemccann/radon-python:{TARGET}"
NODE = f"ghcr.io/joemccann/radon-node:{TARGET}"
GATEWAY = f"ghcr.io/joemccann/radon-model-gateway:{TARGET}"

# Podman has no buildx. Manifest inspect plus RepoDigests (including the
# localhost/ prefix podman stores after a pull) have to be enough to skip.
# Doubled braces survive test_app_runtime._run's .format(log=...).
_PODMAN_STORE = r'''#!/bin/bash
printf '%s\n' "$*" >> {log}
case "$1 $2" in
  "buildx imagetools")
    exit 1
    ;;
  "manifest inspect")
    if [[ "${{RADON_STUB_MANIFEST_FAIL:-0}}" == "1" ]]; then exit 1; fi
    printf '{{"digest":"sha256:%s"}}\n' "${{RADON_STUB_REMOTE_DIGEST}}"
    ;;
  "image inspect")
    if [[ "$4" == "--format" ]]; then
      ref="$3"
      repo="${{ref%:*}}"
      printf '["localhost/%s@sha256:%s"]\n' "$repo" "${{RADON_STUB_LOCAL_DIGEST}}"
    fi
    ;;
  "ps --format")
    printf '%s\n' "ghcr.io/joemccann/radon-python:''' + PREVIOUS + r'''"
    ;;
  "images --format")
    for t in ${{RADON_STUB_TAGS:-}}; do printf '%s:%s\n' "$4" "$t"; done
    ;;
esac
exit 0
'''


def _env(**digest_kw: str) -> dict[str, str]:
    return {
        "RADON_TEST_ENGINE": "podman",
        "RADON_STUB_REMOTE_DIGEST": digest_kw.get("remote", DIGEST),
        "RADON_STUB_LOCAL_DIGEST": digest_kw.get("local", DIGEST),
        "RADON_STUB_TAGS": f"{TARGET} {PREVIOUS}",
    }


def _calls(tmp_path, **digest_kw: str) -> list[str]:
    result = _run(
        tmp_path,
        ["pull", TARGET],
        extra_env=_env(**digest_kw),
        docker_body=_PODMAN_STORE,
    )
    assert result.returncode == 0, result.stderr
    assert "buildx imagetools" not in result.docker_log.read_text()
    return result.docker_log.read_text().splitlines()


def test_podman_matching_local_sha_set_skips_layer_pulls(tmp_path):
    calls = _calls(tmp_path)
    assert f"pull {PYTHON}" not in calls
    assert f"pull {NODE}" not in calls
    assert f"pull {GATEWAY}" not in calls


def test_podman_localhost_repodigest_prefix_still_counts_as_a_match(tmp_path):
    """Podman records RepoDigests as localhost/ghcr.io/... after a pull."""
    calls = _calls(tmp_path)
    assert any(c.startswith("image inspect ") and "--format" in c for c in calls)
    assert f"pull {GATEWAY}" not in calls
    assert f"pull {PYTHON}" not in calls


def test_podman_changed_registry_digest_still_refreshes(tmp_path):
    calls = _calls(tmp_path, remote=OTHER, local=DIGEST)
    assert f"pull {PYTHON}" in calls
    assert f"pull {NODE}" in calls
    assert f"pull {GATEWAY}" in calls


def test_podman_unreachable_registry_keeps_the_local_sha_set(tmp_path):
    """A GHCR miss is not a rebuilt tag. Prepull already left the SHA local."""
    result = _run(
        tmp_path,
        ["pull", TARGET],
        extra_env={**_env(), "RADON_STUB_MANIFEST_FAIL": "1"},
        docker_body=_PODMAN_STORE,
    )
    assert result.returncode == 0, result.stderr
    calls = result.docker_log.read_text().splitlines()
    assert f"pull {PYTHON}" not in calls
    assert f"pull {NODE}" not in calls
    assert f"pull {GATEWAY}" not in calls
    assert "exact release image set already local" in result.stderr
