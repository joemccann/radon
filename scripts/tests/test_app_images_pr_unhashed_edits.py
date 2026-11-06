"""A pull request reuses main's smoked image only when it edits nothing that
the skipped steps read outside the image input hash.

scripts/ci/python_image_inputs.py hashes the Dockerfile.python context. A hit
skips that image's build, smoke and (gateway) bounds check, and those steps
also read files the hash cannot see: this workflow (build settings, smoke
assertions) and cloud/scripts/model_clients.py, whose pins the bounds check
loads (cloud/ is dockerignored). Main skips the same steps on a hit, so a pull
request that edits one of those files is the only run that can catch a break.
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "app-images.yml"
HASHER = REPO / "scripts" / "ci" / "python_image_inputs.py"

UNHASHED = "Find pull request edits outside the image inputs"
PYTHON_REUSE = "Reuse identical python image"
GATEWAY_REUSE = "Reuse identical model gateway image"
MAIN_PUSH = "(github.ref == 'refs/heads/main' && github.event_name == 'push')"
PR_REF = "refs/pull/7/merge"
IHASH = "a" * 64
SHA = "c" * 40

# (reuse step, its id, the unhashed output it needs, the env list it reads)
IMAGES = (
    (PYTHON_REUSE, "reuse", "python", "PYTHON_UNHASHED"),
    (GATEWAY_REUSE, "reuse-gateway", "gateway", "GATEWAY_UNHASHED"),
)
PATH_TOKEN = re.compile(r"(?<![\w./-])((?:\.github|cloud|docker|scripts|web)/[\w./-]*[\w-])")
REPO_LITERAL = re.compile(r"""["']([\w.-]+(?:/[\w.-]+)+)["']""")


def _steps() -> list[dict]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["python-image"]["steps"]


def _step(name: str) -> dict:
    for step in _steps():
        if step.get("name") == name:
            return step
    raise AssertionError(f"missing step {name}")


def _hashed() -> set[str]:
    spec = importlib.util.spec_from_file_location("python_image_inputs", HASHER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return set(module.input_files(REPO))


def _git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-c", "user.name=ci", "-c", "user.email=ci@example.invalid", *args],
        cwd=cwd,
        text=True,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(cwd)},
    ).strip()


BASE_FILES = {
    ".github/workflows/app-images.yml": "name: images\n",
    "cloud/scripts/model_clients.py": "LINUX_MAX_PAYLOAD_BYTES = 2_030_000_000\n",
    "scripts/ci/check_model_client_bounds.py": "MODEL_CLIENTS = 'cloud/scripts/model_clients.py'\n",
    "requirements.txt": "httpx==1\n",
    "docs/notes.md": "notes\n",
}


def _pull_request_checkout(tmp_path: Path, edits: dict[str, str]) -> tuple[Path, str]:
    """A depth-1 clone of the PR head, as actions/checkout leaves it, whose
    origin also holds the base commit the step must fetch."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    for path, text in BASE_FILES.items():
        (origin / path).parent.mkdir(parents=True, exist_ok=True)
        (origin / path).write_text(text, encoding="utf-8")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "base")
    base = _git(origin, "rev-parse", "HEAD")
    for path, text in edits.items():
        (origin / path).parent.mkdir(parents=True, exist_ok=True)
        (origin / path).write_text(text, encoding="utf-8")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "pull request")
    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", "--depth=1", f"file://{origin}", str(work))
    with pytest.raises(subprocess.CalledProcessError):
        _git(work, "cat-file", "-e", f"{base}^{{commit}}")
    return work, base


def _run_unhashed(work: Path, base_sha: str) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    step = _step(UNHASHED)
    env = dict(step["env"])
    assert env.pop("BASE_SHA") == "${{ github.event.pull_request.base.sha }}"
    assert not any("${{" in str(value) for value in env.values()), env
    assert "${{" not in step["run"], "pass values through env, not the shell text"
    output = work.parent / "unhashed-output"
    completed = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
        cwd=work,
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(work.parent),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GITHUB_OUTPUT": str(output),
            "BASE_SHA": base_sha,
            **{key: str(value) for key, value in env.items()},
        },
        capture_output=True,
        text=True,
        check=False,
    )
    lines = output.read_text(encoding="utf-8").splitlines() if output.exists() else []
    return completed, dict(line.split("=", 1) for line in lines)


def _runs(condition: str, *, event: str, ref: str, unhashed: dict[str, str]) -> bool:
    """Evaluate a reuse step's `if:` (string equality, &&, ||, parentheses)."""
    expr = re.sub(r"\bsteps\.unhashed\.outputs\.(\w+)\b", lambda m: repr(unhashed.get(m.group(1), "")), condition)
    expr = re.sub(r"\bgithub\.ref\b", repr(ref), expr)
    expr = re.sub(r"\bgithub\.event_name\b", repr(event), expr)
    expr = expr.replace("&&", " and ").replace("||", " or ")
    leftover = re.sub(r"'[^']*'|==|\band\b|\bor\b|[()\s]", "", expr)
    assert leftover == "", f"teach this evaluator: {leftover!r} in {condition!r}"
    return bool(eval(expr, {"__builtins__": {}}, {}))  # noqa: S307 - validated above


def _reuse_hit(tmp_path: Path, name: str, *, unhashed: dict[str, str]) -> str:
    """steps.<reuse>.outputs.hit on a pull request whose ihash tag exists."""
    step = _step(name)
    if not _runs(step["if"], event="pull_request", ref=PR_REF, unhashed=unhashed):
        return ""
    script = step["run"]
    script = script.replace("${{ steps.pyimage.outputs.tag }}", f"ghcr.io/joemccann/radon-python:ihash-{IHASH}")
    script = script.replace("${{ steps.pyimage.outputs.hash }}", IHASH)
    script = script.replace("${{ github.sha }}", SHA)
    assert "${{" not in script, "new interpolation: teach this fixture its value"
    bin_dir = tmp_path / f"bin-{step['id']}"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text('#!/bin/bash\nif [ "$3" = inspect ]; then exit 0; fi\nexit 9\n', encoding="utf-8")
    docker.chmod(0o755)
    output = tmp_path / f"{step['id']}-output"
    completed = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
        env={
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "GITHUB_OUTPUT": str(output),
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_REF": PR_REF,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())["hit"]


def test_the_diff_step_runs_only_on_pull_requests_before_both_lookups() -> None:
    names = [step.get("name") for step in _steps()]
    step = _step(UNHASHED)
    assert step["id"] == "unhashed"
    assert step["if"] == "github.event_name == 'pull_request'"
    assert names.index("Hash python image inputs") < names.index(UNHASHED)
    assert names.index(UNHASHED) < names.index(PYTHON_REUSE) < names.index(GATEWAY_REUSE)
    # Read-only: it fetches the public base commit and diffs. No registry,
    # no token, no write.
    assert "imagetools" not in step["run"] and "docker" not in step["run"]
    assert "git push" not in step["run"]


@pytest.mark.parametrize(("name", "step_id", "output", "_env"), IMAGES)
def test_pull_request_reuse_requires_a_proven_clean_diff(name, step_id, output, _env) -> None:
    step = _step(name)
    assert step["id"] == step_id
    assert step["if"] == (
        f"{MAIN_PUSH} || (github.event_name == 'pull_request'"
        f" && steps.unhashed.outputs.{output} == 'unchanged')"
    )
    # Main is untouched: the diff step never runs there and main still reuses.
    assert _runs(step["if"], event="push", ref="refs/heads/main", unhashed={})
    assert not _runs(step["if"], event="pull_request", ref=PR_REF, unhashed={})
    assert not _runs(step["if"], event="push", ref="refs/heads/feature", unhashed={output: "unchanged"})


def test_bounds_pin_edit_rebuilds_and_rechecks_the_gateway(tmp_path: Path) -> None:
    """The reviewer's case: a PR lowers LINUX_MAX_PAYLOAD_BYTES while the
    image inputs are identical to main's. The gateway must miss so the
    bounds check runs on this PR."""
    work, base = _pull_request_checkout(
        tmp_path, {"cloud/scripts/model_clients.py": "LINUX_MAX_PAYLOAD_BYTES = 1\n"}
    )
    completed, unhashed = _run_unhashed(work, base)
    assert completed.returncode == 0, completed.stderr
    assert unhashed == {"python": "unchanged"}
    assert _reuse_hit(tmp_path, PYTHON_REUSE, unhashed=unhashed) == "true"
    assert _reuse_hit(tmp_path, GATEWAY_REUSE, unhashed=unhashed) != "true"
    for name in ("Build and publish model gateway image", "Smoke model gateway runtime"):
        assert _step(name)["if"] == "steps.reuse-gateway.outputs.hit != 'true'"
    assert _step("Reuse verified client disk bounds")["if"] == (
        "steps.reuse-gateway.outputs.hit != 'true'"
        " && (github.event_name != 'pull_request' || steps.unhashed.outputs.gateway == 'unchanged')"
    )
    assert _step("Enforce pinned model client disk bounds")["if"] == (
        "steps.reuse-gateway.outputs.hit != 'true'"
        " && steps.reuse-bounds.outputs.hit != 'true'"
    )


def test_workflow_edit_rebuilds_both_images(tmp_path: Path) -> None:
    work, base = _pull_request_checkout(
        tmp_path, {".github/workflows/app-images.yml": "name: images\n# edited smoke\n"}
    )
    completed, unhashed = _run_unhashed(work, base)
    assert completed.returncode == 0, completed.stderr
    assert unhashed == {}
    assert _reuse_hit(tmp_path, PYTHON_REUSE, unhashed=unhashed) != "true"
    assert _reuse_hit(tmp_path, GATEWAY_REUSE, unhashed=unhashed) != "true"
    for name in ("Build and publish python image", "Smoke hardened Python browser runtime"):
        assert _step(name)["if"] == "steps.reuse.outputs.hit != 'true'"


@pytest.mark.parametrize("edited", ["docs/notes.md", "requirements.txt", "scripts/ci/check_model_client_bounds.py"])
def test_other_edits_keep_the_reuse(tmp_path: Path, edited: str) -> None:
    """Hashed files already change the ihash tag (main never recorded it, so
    the lookup misses); unrelated files cannot change any skipped step."""
    work, base = _pull_request_checkout(tmp_path, {edited: "changed\n"})
    completed, unhashed = _run_unhashed(work, base)
    assert completed.returncode == 0, completed.stderr
    assert unhashed == {"python": "unchanged", "gateway": "unchanged"}
    assert _reuse_hit(tmp_path, PYTHON_REUSE, unhashed=unhashed) == "true"
    assert _reuse_hit(tmp_path, GATEWAY_REUSE, unhashed=unhashed) == "true"


@pytest.mark.parametrize("base_sha", ["", "not-a-sha", "f" * 40])
def test_an_unproven_base_builds_both(tmp_path: Path, base_sha: str) -> None:
    work, _ = _pull_request_checkout(tmp_path, {"docs/notes.md": "changed\n"})
    completed, unhashed = _run_unhashed(work, base_sha)
    assert completed.returncode == 0, completed.stderr
    assert unhashed == {}


def _closure(paths: set[str]) -> set[str]:
    """Repo files named by the steps, plus repo-relative literals they load."""
    found = {path for path in paths if (REPO / path).is_file()}
    queue = sorted(found)
    while queue:
        current = queue.pop()
        if not current.endswith(".py"):
            continue
        for literal in REPO_LITERAL.findall((REPO / current).read_text(encoding="utf-8")):
            if (REPO / literal).is_file() and literal not in found:
                found.add(literal)
                queue.append(literal)
    return found


@pytest.mark.parametrize(("_name", "step_id", "_output", "env_name"), IMAGES)
def test_every_file_a_hit_skips_is_hashed_or_diffed(_name, step_id, _output, env_name) -> None:
    listed = _step(UNHASHED)["env"][env_name].split()
    skipped = [step for step in _steps() if step.get("if") == f"steps.{step_id}.outputs.hit != 'true'"]
    assert skipped, step_id
    named = {WORKFLOW.relative_to(REPO).as_posix()}
    for step in skipped:
        named |= set(PATH_TOKEN.findall(str(step.get("run", ""))))
        named |= set(PATH_TOKEN.findall(str((step.get("with") or {}).get("file", ""))))
    hashed = _hashed()
    read = _closure(named)
    assert read - hashed <= set(listed), sorted(read - hashed - set(listed))
    # Minimal: each listed file exists and is not already in the hash, whose
    # own change misses the ihash tag anyway.
    for path in listed:
        assert (REPO / path).is_file(), path
        assert path not in hashed, path


def test_the_derivation_sees_the_bounds_pins() -> None:
    """Not decorative: the bounds script is hashed, its pins are not."""
    hashed = _hashed()
    assert "scripts/ci/check_model_client_bounds.py" in hashed
    assert "cloud/scripts/model_clients.py" not in hashed
    assert "cloud/scripts/model_clients.py" in _closure({"scripts/ci/check_model_client_bounds.py"})
    assert _step(UNHASHED)["env"]["GATEWAY_UNHASHED"].split() == [
        ".github/workflows/app-images.yml",
        "cloud/scripts/model_clients.py",
    ]
    assert _step(UNHASHED)["env"]["PYTHON_UNHASHED"].split() == [".github/workflows/app-images.yml"]
