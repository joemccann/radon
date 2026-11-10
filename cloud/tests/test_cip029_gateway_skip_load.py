"""CIP-029: main gateway publish must not pay the docker-load tarball.

Rebuild run 42507490215 job 128813422372 spent 137s on
`exporting to docker image format` / `loading layer` after torch pip, because
`load: true` dumped ~2GB into the daemon so `docker run --pull=never` could
smoke. The image build already runs the same chronos/torch assertions as
USER radon with `--network=none`. Pull requests cannot push, so they still
load and smoke locally. Bounds still runs against a local image: pull the
exact SHA only when inspect misses (main pbounds miss).
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "app-images.yml"
DOCKERFILE = ROOT / "docker" / "app" / "Dockerfile.python"
GATEWAY_REPO = "ghcr.io/joemccann/radon-model-gateway"
MAIN_PUSH_SKIP_LOAD = (
    "${{ github.ref != 'refs/heads/main' || github.event_name != 'push' }}"
)


def _jobs() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def _gateway_steps() -> list[dict]:
    return _jobs()["gateway-image"]["steps"]


def test_main_gateway_publish_does_not_load_the_tarball() -> None:
    build = next(
        step
        for step in _gateway_steps()
        if step.get("with", {}).get("target") == "model-gateway"
    )
    assert build["with"]["load"] == MAIN_PUSH_SKIP_LOAD
    assert build["with"]["push"] == (
        "${{ github.ref == 'refs/heads/main' && github.event_name == 'push' }}"
    )
    tags = build["with"]["tags"]
    assert f"{GATEWAY_REPO}:${{{{ github.sha }}}}" in tags
    assert "continue-on-error" not in build


def test_dockerfile_still_smokes_chronos_torch_as_radon() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    gateway = text.split("FROM runtime-deps AS model-gateway", 1)[1].split(
        "FROM runtime AS python", 1
    )[0]
    assert "USER radon" in gateway
    assert "PY_FORECAST" in gateway
    assert "import chronos, torch" in gateway
    assert 'version("chronos-forecasting") == "2.3.0"' in gateway
    assert 'torch.__version__ == "2.12.1+cpu"' in gateway
    forecast_at = gateway.index("PY_FORECAST")
    user_at = gateway.index("USER radon")
    assert user_at < forecast_at


def test_pr_path_still_docker_runs_the_loaded_image() -> None:
    smoke = next(
        step
        for step in _gateway_steps()
        if step.get("name") == "Smoke model gateway runtime"
    )
    body = smoke["run"]
    assert smoke["if"] == "steps.reuse-gateway.outputs.hit != 'true'"
    assert "refs/heads/main" in body
    assert "GITHUB_EVENT_NAME" in body
    assert "docker run" in body
    assert "--pull=never" in body
    assert "--user 1000:1000" in body
    assert "--network none" in body
    assert "--cap-drop ALL" in body
    assert "import chronos, torch" in body
    # Main must not docker-run before a pull; that is the tarball we removed.
    main_branch, pr_branch = body.split("docker run", 1)
    assert "exit 0" in main_branch
    assert "--pull=never" in pr_branch


def test_bounds_pulls_the_exact_sha_when_the_daemon_has_no_image() -> None:
    check = next(
        step
        for step in _gateway_steps()
        if step.get("name") == "Enforce pinned model client disk bounds"
    )
    body = check["run"]
    assert "docker image inspect" in body
    assert "docker pull" in body
    assert f"{GATEWAY_REPO}:${{{{ github.sha }}}}" in body
    assert "scripts/ci/check_model_client_bounds.py" in body
    inspect_at = body.index("docker image inspect")
    pull_at = body.index("docker pull")
    check_at = body.index("check_model_client_bounds.py")
    assert inspect_at < pull_at < check_at
