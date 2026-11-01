"""app-images-pr.yml skips a pull request only when no image job can see it.

The PR image build is the slowest PR check (median python job 622s) and is
not a required status check, so a workflow-level `paths-ignore` cannot hold a
merge open. A pattern is allowed only when this test proves that no file it
matches is an input to any of the three jobs app-images.yml runs:

* the docker context each Dockerfile COPYs after docker/app/.dockerignore,
  using scripts/ci/python_image_inputs.py's matcher;
* a file a workflow step names or loads. The python job runs
  cloud/scripts/check_model_transport.py, so cloud/** as a whole is an input;
* the release label. node-image folds scripts/release_version.py over the
  changed paths into RADON_RELEASE_VERSION, so a path it counts as runtime
  changes a build arg (scripts/api/tests/conftest.py is one).

Docker re-includes `!**/.env.example` names under an ignored directory. The
tracked-file check below catches such a file the moment a PR commits one.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
from functools import lru_cache
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"
PR_WORKFLOW = WORKFLOWS / "app-images-pr.yml"
IMAGES_WORKFLOW = WORKFLOWS / "app-images.yml"
DOCKERFILES = (
    REPO / "docker" / "app" / "Dockerfile.python",
    REPO / "docker" / "app" / "Dockerfile.node",
)
DOCKERIGNORE = REPO / "docker" / "app" / ".dockerignore"

# Only `<literal dir>/**` is proven here; GitHub matches it as a plain prefix.
PROVABLE = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*/\*\*")
PATH_TOKEN = re.compile(
    r"(?<![\w./-])(?:\$GITHUB_WORKSPACE/)?"
    r"((?:\.github|cloud|docker|docs|scripts|site|web)/[\w./-]*[\w-])"
)
SOURCE_LITERAL = re.compile(r"""["']([\w./-]+\.(?:py|cjs|js|json|sh))["']""")
EXPECTED_IGNORES = [
    "docs/**",
    "site/**",
    "scripts/tests/**",
    "web/tests/**",
    "web/e2e/**",
    "cloud/tests/**",
]


def _module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=1)
def _tracked() -> frozenset[str]:
    out = subprocess.check_output(["git", "ls-files", "-z"], cwd=REPO)
    return frozenset(path for path in out.decode("utf-8").split("\0") if path)


def _under(path: str, stem: str) -> bool:
    return path == stem or path.startswith(stem + "/")


def _pr_trigger() -> dict:
    workflow = yaml.load(PR_WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    return workflow["on"]


def _ignore_patterns() -> list[str]:
    trigger = _pr_trigger()["pull_request"] or {}
    return list(trigger.get("paths-ignore") or [])


def _ignored(path: str, patterns: list[str]) -> bool:
    return any(path.startswith(pattern[: -len("**")]) for pattern in patterns)


@lru_cache(maxsize=1)
def _docker_context() -> tuple[frozenset[str], tuple[tuple[str, ...], ...], tuple[str, ...]]:
    """(tracked files COPYed by either Dockerfile, COPY stems, dockerignore)."""
    hasher = _module("python_image_inputs", REPO / "scripts" / "ci" / "python_image_inputs.py")
    patterns = hasher._patterns(DOCKERIGNORE)
    inputs = {DOCKERIGNORE.relative_to(REPO).as_posix()}
    stems = []
    for dockerfile in DOCKERFILES:
        inputs.add(dockerfile.relative_to(REPO).as_posix())
        sources = tuple(
            source.rstrip("/") for source in hasher.copy_sources(dockerfile.read_text(encoding="utf-8"))
        )
        stems.append(sources)
        for path in _tracked():
            if any(_under(path, stem) for stem in sources) and not hasher.path_ignored(path, patterns):
                inputs.add(path)
    return frozenset(inputs), tuple(stems), tuple(patterns)


@lru_cache(maxsize=1)
def _step_inputs() -> tuple[frozenset[str], frozenset[str]]:
    """(tracked files the image workflow names or its scripts load, named paths)."""
    tracked = _tracked()
    named = {
        token
        for token in PATH_TOKEN.findall(IMAGES_WORKFLOW.read_text(encoding="utf-8"))
        if (REPO / token).exists()
    }
    named |= {
        IMAGES_WORKFLOW.relative_to(REPO).as_posix(),
        PR_WORKFLOW.relative_to(REPO).as_posix(),
    }
    files = {path for path in tracked if any(_under(path, token) for token in named)}
    queue = sorted(files)
    while queue:
        current = queue.pop()
        if not current.endswith((".py", ".cjs", ".js")):
            continue
        text = (REPO / current).read_text(encoding="utf-8", errors="replace")
        for literal in SOURCE_LITERAL.findall(text):
            for base in (Path(current).parent, Path()):
                candidate = (base / literal).as_posix()
                if candidate in tracked and candidate not in files:
                    files.add(candidate)
                    queue.append(candidate)
    return frozenset(files), frozenset(named)


def violations(patterns: list[str]) -> list[str]:
    """Why each pattern could hide an image input; empty means proven."""
    release = _module("release_version", REPO / "scripts" / "release_version.py")
    docker_inputs, copy_stems, docker_patterns = _docker_context()
    hasher = _module("python_image_inputs", REPO / "scripts" / "ci" / "python_image_inputs.py")
    step_files, step_named = _step_inputs()
    bad: list[str] = []
    for pattern in patterns:
        if not PROVABLE.fullmatch(pattern):
            bad.append(f"{pattern}: only <dir>/** patterns are proven")
            continue
        prefix = pattern[: -len("/**")]
        matched = sorted(path for path in _tracked() if _under(path, prefix))
        if not matched:
            bad.append(f"{pattern}: matches no tracked file")
        for path in matched:
            if path in docker_inputs:
                bad.append(f"{pattern}: docker context input {path}")
            if path in step_files:
                bad.append(f"{pattern}: workflow step input {path}")
            if release.is_runtime(path):
                bad.append(f"{pattern}: release label input {path}")
        # Files a future PR adds under the prefix.
        for stems in copy_stems:
            for stem in stems:
                if _under(stem, prefix):
                    bad.append(f"{pattern}: COPY source {stem} lies under it")
                elif _under(prefix, stem) and not hasher.path_ignored(f"{prefix}/probe.py", list(docker_patterns)):
                    bad.append(f"{pattern}: COPY {stem} takes it and .dockerignore keeps it")
        for token in step_named:
            if _under(prefix, token) or _under(token, prefix):
                bad.append(f"{pattern}: workflow names {token}")
        for probe in (f"{prefix}/probe.py", f"{prefix}/nested/__init__.py"):
            if release.is_runtime(probe):
                bad.append(f"{pattern}: release label counts {probe}")
    return bad


def test_pr_image_workflow_ignores_only_proven_non_inputs() -> None:
    patterns = _ignore_patterns()
    assert patterns == EXPECTED_IGNORES
    assert violations(patterns) == []


def test_pr_trigger_filters_by_paths_ignore_alone() -> None:
    trigger = _pr_trigger()
    assert set(trigger) == {"pull_request"}
    # `paths` or `branches` beside it would narrow the trigger further.
    assert set(trigger["pull_request"]) == {"paths-ignore"}
    workflow = yaml.load(PR_WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert workflow["permissions"] == {"contents": "read"}


def test_step_input_derivation_finds_what_each_job_runs() -> None:
    files, _ = _step_inputs()
    for path in (
        "cloud/scripts/check_model_transport.py",
        "cloud/scripts/model_transport.py",
        "cloud/scripts/model_clients.py",
        "scripts/ci/python_image_inputs.py",
        "scripts/ci/check_model_client_bounds.py",
        "scripts/ci/check_model_cli_transport.py",
        "scripts/release_version.py",
        "docker/app/vendor-clis/install-darwin.cjs",
        "docker/app/vendor-clis/package-lock.json",
        "scripts/model_gateway/cli_bridge.py",
        ".github/workflows/app-images.yml",
    ):
        assert path in files, path


def test_docker_context_derivation_covers_both_images() -> None:
    inputs, _, _ = _docker_context()
    for path in (
        "requirements.txt",
        "scripts/requirements-api.txt",
        "package.json",
        "bun.lock",
        "web/package.json",
        "web/bun.lock",
        "docker/app/next-clerk-guard.sh",
        "docker/app/vendor-clis/antigravity.json",
        "scripts/api/server.py",
        "web/next.config.mjs",
    ):
        assert path in inputs, path
    assert not any(_under(path, "scripts/tests") or _under(path, "web/e2e") for path in inputs)


def test_the_proof_rejects_real_inputs() -> None:
    """Not decorative: each known input class fails the proof."""
    assert any("check_model_transport.py" in line for line in violations(["cloud/**"]))
    assert any("release label" in line for line in violations(["scripts/api/tests/**"]))
    assert any("docker context input scripts/api/server.py" in line for line in violations(["scripts/**"]))
    assert any("docker context input web/" in line for line in violations(["web/**"]))
    assert any("workflow" in line for line in violations(["docker/app/vendor-clis/**"]))
    assert violations(["**/*.md"]) == ["**/*.md: only <dir>/** patterns are proven"]
    assert violations(["no-such-dir/**"]) == ["no-such-dir/**: matches no tracked file"]


def test_image_inputs_still_trigger_the_pr_build() -> None:
    patterns = _ignore_patterns()
    for path in (
        ".github/workflows/app-images.yml",
        ".github/workflows/app-images-pr.yml",
        "docker/app/Dockerfile.python",
        "docker/app/Dockerfile.node",
        "docker/app/.dockerignore",
        "docker/app/vendor-clis/package.json",
        "requirements.txt",
        "scripts/requirements-api.txt",
        "package.json",
        "bun.lock",
        "web/package.json",
        "web/bun.lock",
        "scripts/api/server.py",
        "scripts/model_gateway/server.py",
        "scripts/ci/python_image_inputs.py",
        "web/next.config.mjs",
        "cloud/scripts/check_model_transport.py",
        "cloud/scripts/model_clients.py",
    ):
        assert path in _tracked(), path
        assert not _ignored(path, patterns), path
