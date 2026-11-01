"""CIP-020: a main push whose Python image inputs are unchanged must retag
the already-smoked digest instead of exporting that image again.

Run 38597907211 built a fully cached python image for a web-only commit,
spent 53.9s sending the docker tarball, and that job closed the deploy
gates. The hardened smoke stays on the build path. The reuse path may
publish only a digest recorded after that smoke.
"""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "app-images.yml"
DOCKERFILE = REPO / "docker" / "app" / "Dockerfile.python"
DOCKERIGNORE = REPO / "docker" / "app" / ".dockerignore"
HASHER = REPO / "scripts" / "ci" / "python_image_inputs.py"
DEPLOY_FILES = (
    REPO / "cloud" / "scripts" / "deploy.sh",
    REPO / "cloud" / "scripts" / "deploy-root-helper.sh",
    REPO / "cloud" / "scripts" / "radon-app-runtime.sh",
)
def _python_job() -> dict:
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    return jobs["python-image"]


def _step(name: str) -> dict:
    for step in _python_job()["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(f"missing step {name}")


def _run(step_name: str) -> str:
    return _step(step_name)["run"]


def test_reuse_step_retags_only_an_input_hash_manifest() -> None:
    reuse = _step("Reuse identical python image")
    script = reuse["run"]
    # Main pushes retag; pull requests only look (see the behavior tests),
    # and only when they edit no unhashed file a hit would skip
    # (test_app_images_pr_unhashed_edits.py).
    assert reuse["if"] == (
        "(github.ref == 'refs/heads/main' && github.event_name == 'push')"
        " || (github.event_name == 'pull_request'"
        " && steps.unhashed.outputs.python == 'unchanged')"
    )
    assert "docker buildx imagetools inspect" in script
    assert "docker buildx imagetools create" in script
    assert "docker tag " not in script
    assert "docker push " not in script
    assert "docker build " not in script
    assert "buildcache" not in script
    assert "radon-python:ihash-" in script
    assert "radon-python:${{ github.sha }}" in script
    assert "radon-python:latest" in script
    assert "hit=false" in script
    assert "hit=true" in script
    assert "manifest bytes do not match" in script


SHA = "c" * 40
IHASH = "ghcr.io/joemccann/radon-python:ihash-" + "a" * 64


def _run_reuse(
    tmp_path: Path, *, event: str, ref: str, inspect_status: int, tag: str = IHASH
) -> tuple[subprocess.CompletedProcess[str], list[str], str]:
    """Run the shipped reuse script with a docker that logs every call."""
    script = _run("Reuse identical python image")
    script = script.replace("${{ steps.pyimage.outputs.tag }}", tag)
    script = script.replace("${{ github.sha }}", SHA)
    assert "${{" not in script, "new interpolation: teach this fixture its value"
    log = tmp_path / "docker.log"
    output = tmp_path / "github-output"
    docker = tmp_path / "bin" / "docker"
    docker.parent.mkdir()
    docker.write_text(
        "#!/bin/bash\n"
        'printf "%s\\n" "$*" >> "$CALL_LOG"\n'
        'if [ "$3" = inspect ] && [ "${5:-}" = --raw ]; then echo manifest; exit 0; fi\n'
        'if [ "$3" = inspect ]; then exit "$INSPECT_STATUS"; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    env = {
        "PATH": f"{docker.parent}{os.pathsep}{os.environ['PATH']}",
        "CALL_LOG": str(log),
        "INSPECT_STATUS": str(inspect_status),
        "GITHUB_OUTPUT": str(output),
        "GITHUB_EVENT_NAME": event,
        "GITHUB_REF": ref,
    }
    completed = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    hits = output.read_text(encoding="utf-8") if output.exists() else ""
    return completed, calls, hits


@pytest.mark.parametrize(("inspect_status", "hit"), [(0, "true"), (1, "false")])
def test_pull_request_reuse_only_reads_the_input_hash_tag(
    tmp_path: Path, inspect_status: int, hit: str
) -> None:
    """A PR with main's exact inputs skips the build and smoke main already
    ran. It holds no packages:write token, so it must never write a tag."""
    completed, calls, output = _run_reuse(
        tmp_path, event="pull_request", ref="refs/pull/7/merge", inspect_status=inspect_status
    )
    assert completed.returncode == 0, completed.stderr
    assert calls == [f"buildx imagetools inspect {IHASH}"]
    assert output == f"hit={hit}\n"


def test_pull_request_reuse_refuses_a_malformed_tag(tmp_path: Path) -> None:
    completed, calls, output = _run_reuse(
        tmp_path,
        event="pull_request",
        ref="refs/pull/7/merge",
        inspect_status=0,
        tag="ghcr.io/joemccann/radon-python:latest",
    )
    assert completed.returncode == 1
    assert calls == []
    assert output == ""


def test_main_push_reuse_still_retags_and_compares_manifest_bytes(tmp_path: Path) -> None:
    completed, calls, output = _run_reuse(
        tmp_path, event="push", ref="refs/heads/main", inspect_status=0
    )
    assert completed.returncode == 0, completed.stderr
    sha_tag = f"ghcr.io/joemccann/radon-python:{SHA}"
    assert calls == [
        f"buildx imagetools inspect {IHASH}",
        f"buildx imagetools inspect {IHASH} --raw",
        f"buildx imagetools create -t {sha_tag} -t ghcr.io/joemccann/radon-python:latest {IHASH}",
        f"buildx imagetools inspect {sha_tag} --raw",
    ]
    assert output == "hit=true\n"


def test_main_push_miss_builds(tmp_path: Path) -> None:
    completed, calls, output = _run_reuse(
        tmp_path, event="push", ref="refs/heads/main", inspect_status=1
    )
    assert completed.returncode == 0, completed.stderr
    assert calls == [f"buildx imagetools inspect {IHASH}"]
    assert output == "hit=false\n"


def test_build_and_hardened_smoke_run_only_on_a_miss() -> None:
    steps = _python_job()["steps"]
    build_index = next(
        index
        for index, step in enumerate(steps)
        if step.get("uses", "").startswith("docker/build-push-action@")
    )
    build = steps[build_index]
    smoke = steps[build_index + 1]
    assert build["with"]["load"] is True
    assert build["if"] == "steps.reuse.outputs.hit != 'true'"
    assert smoke["if"] == "steps.reuse.outputs.hit != 'true'"
    assert "docker run" in smoke["run"]
    assert "--pull=never" in smoke["run"]
    assert "--cap-drop ALL" in smoke["run"]
    assert "--network none" in smoke["run"]
    assert "ghcr.io/joemccann/radon-python:${{ github.sha }}" in smoke["run"]
    record = steps[build_index + 2]
    assert record["name"] == "Record python image input hash"
    assert "steps.reuse.outputs.hit != 'true'" in record["if"]
    assert "github.event_name == 'push'" in record["if"]
    assert "docker buildx imagetools create" in record["run"]
    # The index write is not a deploy gate. A registry hiccup must not
    # block a release whose SHA tag is already pushed and smoked.
    assert "input-hash tag was not recorded" in record["run"]
    assert record["run"].strip().endswith("exit 0") or "exit 0" in record["run"]


def test_deploy_paths_do_not_pull_the_input_hash_tag() -> None:
    for path in DEPLOY_FILES:
        text = path.read_text(encoding="utf-8")
        assert "ihash-" not in text, path.name
        assert "buildcache" not in text, path.name


def _load_hasher():
    import importlib.util

    spec = importlib.util.spec_from_file_location("python_image_inputs", HASHER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hasher_matches_dockerfile_copies_and_ignores_tests() -> None:
    assert HASHER.is_file()
    module = _load_hasher()
    files = module.input_files(REPO)
    assert DOCKERFILE.relative_to(REPO).as_posix() in files
    assert DOCKERIGNORE.relative_to(REPO).as_posix() in files
    assert "requirements.txt" in files
    assert "scripts/requirements-api.txt" in files
    assert any(path.startswith("scripts/") and "/tests/" not in path for path in files)
    assert not any(
        path.startswith("scripts/tests/") or path.startswith("scripts/api/tests/")
        for path in files
    )
    assert "web/public/fonts/Inter-Variable.woff2" in files
    assert "web/public/fonts/IBMPlexMono-400.woff2" in files
    assert "web/lib/order/types.ts" not in files
    assert not any("__pycache__" in path or path.endswith(".pyc") for path in files)
    blob = {path: (REPO / path).read_bytes() for path in files}
    digest = module.hash_files(blob)
    assert len(digest) == 64
    int(digest, 16)
    assert module.hash_files(dict(blob)) == digest
    changed = dict(blob)
    changed["requirements.txt"] = changed["requirements.txt"] + b"\n"
    assert module.hash_files(changed) != digest


def test_unknown_dockerignore_pattern_fails_closed(tmp_path: Path) -> None:
    module = _load_hasher()
    broken = tmp_path / "Dockerfile.python"
    broken.write_text(DOCKERFILE.read_text(encoding="utf-8"), encoding="utf-8")
    ignore = tmp_path / ".dockerignore"
    ignore.write_text(DOCKERIGNORE.read_text(encoding="utf-8") + "\n**/*.pyo\n", encoding="utf-8")
    with pytest.raises(module.InputHashError, match="dockerignore"):
        module.input_files_from(broken, ignore, REPO)


def test_input_hash_changes_when_copied_file_permissions_change(tmp_path: Path) -> None:
    module = _load_hasher()
    config = tmp_path / "docker" / "app"
    config.mkdir(parents=True)
    (config / "Dockerfile.python").write_text("FROM scratch\nCOPY payload /payload\n")
    (config / ".dockerignore").write_text("")
    payload = tmp_path / "payload"
    payload.write_text("unchanged bytes")
    payload.chmod(0o644)
    original = module.image_input_hash(tmp_path)
    payload.chmod(0o755)
    assert module.image_input_hash(tmp_path) != original


def test_input_hash_refuses_a_symlink_directory_in_a_copy(tmp_path: Path) -> None:
    module = _load_hasher()
    config = tmp_path / "docker" / "app"
    config.mkdir(parents=True)
    (config / "Dockerfile.python").write_text("FROM scratch\nCOPY payload /payload\n")
    (config / ".dockerignore").write_text("")
    payload = tmp_path / "payload"
    payload.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "script").write_text("executable payload")
    (payload / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(module.InputHashError, match="symlink"):
        module.image_input_hash(tmp_path)


def test_unparsed_copy_fails_closed(tmp_path: Path) -> None:
    module = _load_hasher()
    dockerfile = tmp_path / "Dockerfile.python"
    dockerfile.write_text(
        textwrap.dedent(
            """
            FROM python:3.13.16-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f
            COPY requirements.txt ./requirements.txt
            COPY scripts/*.py ./scripts/
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    ignore = tmp_path / ".dockerignore"
    ignore.write_text(DOCKERIGNORE.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(module.InputHashError, match="COPY"):
        module.input_files_from(dockerfile, ignore, REPO)


@pytest.mark.parametrize("instruction", [
    "copy payload /payload",
    "COPY\tpayload /payload",
    "ADD payload /payload",
    "COPY --from=ghcr.io/example/untracked:latest /payload /payload",
])
def test_unsupported_source_instruction_cannot_be_silently_omitted(instruction):
    module = _load_hasher()
    dockerfile = "FROM python:3.13\nCOPY requirements.txt /requirements.txt\n" + instruction
    with pytest.raises(module.InputHashError):
        module.copy_sources(dockerfile)


def test_known_stage_sources_remain_covered_by_their_original_copy():
    module = _load_hasher()
    assert module.copy_sources("FROM node AS vendor\nCOPY payload /payload\nFROM python\nCOPY --from=vendor /payload /payload") == ["payload"]


def test_ignored_directory_links_cannot_change_the_input_hash(tmp_path):
    module = _load_hasher()
    config = tmp_path / "docker" / "app"
    config.mkdir(parents=True)
    (config / "Dockerfile.python").write_text("FROM scratch\nCOPY scripts /scripts\n")
    (config / ".dockerignore").write_text("scripts/tests/\n")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "runtime.py").write_text("runtime payload")
    original = module.image_input_hash(tmp_path)
    ignored = scripts / "tests"
    ignored.mkdir()
    (ignored / "external").symlink_to(tmp_path / "outside", target_is_directory=True)
    assert module.image_input_hash(tmp_path) == original


def test_cli_fails_closed_on_unknown_pattern(monkeypatch: pytest.MonkeyPatch) -> None:
    """The workflow treats a non-zero hasher exit as a cache miss.

    Two CLI passes must agree even while pytest writes bytecode under
    ``scripts/``. That bytecode is excluded from the image context.
    """
    module = _load_hasher()

    def refuse(repo: Path = REPO) -> str:
        del repo
        raise module.InputHashError("dockerignore pattern is not understood: **/*.pyo")

    monkeypatch.setattr(module, "image_input_hash", refuse)
    assert module.main() == 2

    completed = subprocess.run(
        ["python3", str(HASHER), "--self-check"],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    digest = completed.stdout.strip()
    assert len(digest) == 64
    int(digest, 16)
    bytecode = REPO / "scripts" / "ci" / "__pycache__" / "cip020-race.pyc"
    bytecode.parent.mkdir(exist_ok=True)
    bytecode.write_bytes(b"bytecode-between-hashes")
    try:
        again = subprocess.check_output(["python3", str(HASHER)], cwd=REPO, text=True).strip()
    finally:
        bytecode.unlink(missing_ok=True)
    assert again == digest
