"""CIP-027: build the model-gateway image beside the python image.

Scripts-rebuild mixed run 42314939571 job 128225458846 spent 51s publishing
the python image, then 115s publishing the gateway, so Exact app images
authorized at 273s. Torch layers were CACHED; 53s of the gateway step was
`load: true` tarball into docker. The two targets share no `needs` edge:
python is `FROM runtime AS python`, gateway is `FROM runtime-deps AS
model-gateway`. Sibling jobs keep Darwin-before-publish, exact SHA tags,
smokes, bounds, and the 40-character deploy admission.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "app-images.yml"
CI = ROOT / ".github" / "workflows" / "ci.yml"
GATEWAY_REPO = "ghcr.io/joemccann/radon-model-gateway"
BUILD_PUSH = "docker/build-push-action@53b7df96c91f9c12dcc8a07bcb9ccacbed38856a"


def _jobs() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def _needs(job: dict) -> list[str]:
    raw = job.get("needs", [])
    if isinstance(raw, str):
        return [raw]
    return list(raw)


def test_gateway_image_is_a_sibling_of_python_and_node() -> None:
    jobs = _jobs()
    assert "gateway-image" in jobs
    assert jobs["python-image"]["name"] == "Build python image"
    assert jobs["gateway-image"]["name"] == "Build gateway image"
    assert _needs(jobs["python-image"]) == ["darwin-clients"]
    assert _needs(jobs["gateway-image"]) == ["darwin-clients"]
    assert "needs" not in jobs["node-image"]
    assert "gateway-image" not in _needs(jobs["python-image"])
    assert "python-image" not in _needs(jobs["gateway-image"])
    assert "node-image" not in _needs(jobs["gateway-image"])
    assert jobs["gateway-image"]["runs-on"] == "ubuntu-24.04"


def test_python_image_job_no_longer_builds_the_gateway() -> None:
    steps = _jobs()["python-image"]["steps"]
    names = [step.get("name", "") for step in steps]
    assert "Build and publish model gateway image" not in names
    assert "Smoke model gateway runtime" not in names
    assert "Reuse identical model gateway image" not in names
    assert "Enforce pinned model client disk bounds" not in names
    builds = [step for step in steps if step.get("uses") == BUILD_PUSH]
    assert len(builds) == 1
    assert builds[0]["with"].get("target") != "model-gateway"
    assert f"{GATEWAY_REPO}:" not in str(builds[0].get("with", {}).get("tags", ""))


def test_gateway_job_still_publishes_the_exact_sha_and_smokes() -> None:
    job = _jobs()["gateway-image"]
    steps = job["steps"]
    names = [step.get("name", "") for step in steps]
    build = next(step for step in steps if step.get("with", {}).get("target") == "model-gateway")
    assert build["uses"] == BUILD_PUSH
    assert build["if"] == "steps.reuse-gateway.outputs.hit != 'true'"
    tags = build["with"]["tags"]
    assert f"{GATEWAY_REPO}:${{{{ github.sha }}}}" in tags
    assert f"{GATEWAY_REPO}:latest" in tags
    assert build["with"]["load"] is True
    assert build["with"]["push"] == (
        "${{ github.ref == 'refs/heads/main' && github.event_name == 'push' }}"
    )
    assert build["with"]["provenance"] is False
    smoke = next(step for step in steps if step.get("name") == "Smoke model gateway runtime")
    assert smoke["if"] == "steps.reuse-gateway.outputs.hit != 'true'"
    assert "--pull=never" in smoke["run"]
    assert "--cap-drop ALL" in smoke["run"]
    assert "--user 1000:1000" in smoke["run"]
    assert "import chronos, torch" in smoke["run"]
    assert names.index("Build and publish model gateway image") < names.index(
        "Smoke model gateway runtime"
    )
    assert names.index("Smoke model gateway runtime") < names.index(
        "Enforce pinned model client disk bounds"
    )
    assert "continue-on-error" not in job
    for step in steps:
        assert "continue-on-error" not in step


def test_ci_still_gates_deploy_on_the_whole_app_images_workflow() -> None:
    ci = yaml.safe_load(CI.read_text(encoding="utf-8"))
    deploy = ci["jobs"]["deploy"]
    needs = deploy["needs"]
    assert "app-images" in needs
    assert ci["jobs"]["app-images"]["uses"] == "./.github/workflows/app-images.yml"
    assert ci["jobs"]["app-images"]["needs"] == ["secret-scan"]
