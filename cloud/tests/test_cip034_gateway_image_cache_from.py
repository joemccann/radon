"""CIP-034: gateway rebuilds must cache-from the previous exact SHA image.

Scripts-only mixed rebuild 44612924566 job 135874396489 imported
`radon-model-gateway:buildcache` in 0.3s, then still ran
`Collecting torch==2.12.1+cpu` for 36.8s and `exporting layers` for 49.8s.
GHA docker caches are gone (Actions cache listing is uv/bun only). The
previous main SHA image already has the torch layer; BuildKit can reuse it
without treating `:latest` as a deploy tag. Pull requests with no
`github.event.before` keep the existing buildcache sources. Darwin-before-
publish, exact SHA tags, and fail-closed reuse stay.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "app-images.yml"
GATEWAY_REPO = "ghcr.io/joemccann/radon-model-gateway"
ZERO_SHA = "0" * 40


def _jobs() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def _gateway_steps() -> list[dict]:
    return _jobs()["gateway-image"]["steps"]


def _cache_step() -> dict:
    return next(
        step
        for step in _gateway_steps()
        if step.get("id") == "gateway-cache"
    )


def test_gateway_selects_layer_cache_from_the_previous_sha_image() -> None:
    step = _cache_step()
    body = step["run"]
    assert step["env"]["BEFORE_SHA"] == "${{ github.event.before }}"
    assert f"type=registry,ref={GATEWAY_REPO}:" + "${BEFORE_SHA}" in body
    assert "^[0-9a-f]{40}$" in body
    assert ZERO_SHA in body
    assert f"type=registry,ref={GATEWAY_REPO}:buildcache" in body
    assert "type=registry,ref=ghcr.io/joemccann/radon-python:buildcache" in body
    assert "type=gha,scope=radon-model-gateway" in body
    # Moving tags are not cache donors; the previous 40-hex SHA is.
    assert f"{GATEWAY_REPO}:latest" not in body
    assert "ihash-" not in body


def test_gateway_build_consumes_the_selected_layer_cache() -> None:
    build = next(
        step
        for step in _gateway_steps()
        if step.get("with", {}).get("target") == "model-gateway"
    )
    assert build["with"]["cache-from"] == (
        "${{ steps.gateway-cache.outputs.cache_from }}"
    )
    tags = build["with"]["tags"]
    assert f"{GATEWAY_REPO}:${{{{ github.sha }}}}" in tags
    assert f"{GATEWAY_REPO}:latest" in tags
    assert build["with"]["push"] == (
        "${{ github.ref == 'refs/heads/main' && github.event_name == 'push' }}"
    )
    assert "continue-on-error" not in build


def test_cache_step_runs_before_the_gateway_build() -> None:
    names = [step.get("name", "") for step in _gateway_steps()]
    assert names.index("Select gateway layer cache") < names.index(
        "Build and publish model gateway image"
    )
