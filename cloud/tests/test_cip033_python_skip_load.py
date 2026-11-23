"""CIP-033: main python publish must not pay the docker-load tarball.

Rebuild run 44357745454 job 134990848552 spent 10.8s on
`exporting to docker image format` / `loading layer` (228MB + 242MB) then
~9s on `docker run --pull=never` after the image build had already smoked
playwright as USER radon. Run 44386917407 job 135094530994 spent 16.6s on
the same load. Pull requests cannot push, so they still load and smoke
locally.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "app-images.yml"
DOCKERFILE = ROOT / "docker" / "app" / "Dockerfile.python"
PYTHON_REPO = "ghcr.io/joemccann/radon-python"
MAIN_PUSH_SKIP_LOAD = (
    "${{ github.ref != 'refs/heads/main' || github.event_name != 'push' }}"
)


def _jobs() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def _python_steps() -> list[dict]:
    return _jobs()["python-image"]["steps"]


def test_main_python_publish_does_not_load_the_tarball() -> None:
    build = next(
        step
        for step in _python_steps()
        if step.get("name") == "Build and publish python image"
    )
    assert build["with"]["load"] == MAIN_PUSH_SKIP_LOAD
    assert build["with"]["push"] == (
        "${{ github.ref == 'refs/heads/main' && github.event_name == 'push' }}"
    )
    tags = build["with"]["tags"]
    assert f"{PYTHON_REPO}:${{{{ github.sha }}}}" in tags
    assert "continue-on-error" not in build
    assert build["with"].get("target") != "model-gateway"


def test_dockerfile_still_smokes_playwright_as_radon() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    runtime = text.split("FROM runtime-deps AS runtime", 1)[1].split(
        "FROM runtime-deps AS model-gateway", 1
    )[0]
    assert "USER radon" in runtime
    assert "playwright.sync_api" in runtime
    assert "radon-browser-smoke" in runtime
    user_at = runtime.index("USER radon")
    smoke_at = runtime.index("playwright.sync_api")
    assert user_at < smoke_at


def test_pr_path_still_docker_runs_the_loaded_image() -> None:
    smoke = next(
        step
        for step in _python_steps()
        if step.get("name") == "Smoke hardened Python browser runtime"
    )
    body = smoke["run"]
    assert smoke["if"] == "steps.reuse.outputs.hit != 'true'"
    assert "refs/heads/main" in body
    assert "GITHUB_EVENT_NAME" in body
    assert "docker run" in body
    assert "--pull=never" in body
    assert "--user 1000:1000" in body
    assert "--network none" in body
    assert "--cap-drop ALL" in body
    assert "playwright.sync_api" in body
    main_branch, pr_branch = body.split("docker run", 1)
    assert "exit 0" in main_branch
    assert "--pull=never" in pr_branch
    assert "already smoked" in main_branch
