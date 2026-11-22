"""CIP-036: node rebuilds must cache-from the previous exact SHA image.

Scripts-only mixed run 44594349045 job 135808679573 imported
`radon-node:buildcache` in 0.8s, then still ran playwright install for
16.9s and `bun run build` for 24.7s. GHA docker caches are gone. The
previous main SHA image already has those layers; BuildKit can reuse
them without treating `:latest` as a deploy tag. Empty, all-zero, or
non-40-hex `github.event.before` keeps buildcache only.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "app-images.yml"
NODE_REPO = "ghcr.io/joemccann/radon-node"
ZERO_SHA = "0" * 40


def _jobs() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def _node_steps() -> list[dict]:
    return _jobs()["node-image"]["steps"]


def _cache_step() -> dict:
    return next(step for step in _node_steps() if step.get("id") == "image-cache")


def test_node_selects_layer_cache_from_the_previous_sha_image() -> None:
    step = _cache_step()
    body = step["run"]
    assert step["env"]["BEFORE_SHA"] == "${{ github.event.before }}"
    assert (
        f"type=registry,ref={NODE_REPO}:" + "${BEFORE_SHA}" in body
        or "type=registry,ref=${IMAGE_REPO}:${BEFORE_SHA}" in body
    )
    assert "^[0-9a-f]{40}$" in body
    assert ZERO_SHA in body
    assert f"{NODE_REPO}:latest" not in body
    assert "ihash-" not in body


def test_node_build_consumes_the_selected_layer_cache() -> None:
    build = next(
        step
        for step in _node_steps()
        if step.get("uses", "").startswith("docker/build-push-action")
    )
    assert build["with"]["cache-from"] == "${{ steps.image-cache.outputs.cache_from }}"
    tags = build["with"]["tags"]
    assert f"{NODE_REPO}:${{{{ github.sha }}}}" in tags
    assert f"{NODE_REPO}:latest" in tags
    assert build["with"]["push"] == (
        "${{ github.ref == 'refs/heads/main' && github.event_name == 'push' }}"
    )
    assert "continue-on-error" not in build


def test_cache_step_runs_before_the_node_build() -> None:
    names = [step.get("name", "") for step in _node_steps()]
    assert names.index("Select image cache") < names.index(
        "Build and publish node image"
    )
