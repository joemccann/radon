"""CIP-024: a rebuilt gateway image with the same client payload must not
export again.

Rebuild mixed run 41487857404 (job 125428712620) still spent 86s exporting
CLIs after a scripts/ change that did not touch vendor-clis or model_gateway
helpers. CIP-022/023 only reuse when the image id matches. The payload hash
is recorded as an image label so deploy can compare without streaming 1.6GB.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
HASHER = REPO / "scripts" / "ci" / "model_client_payload_inputs.py"
DOCKERFILE = REPO / "docker" / "app" / "Dockerfile.python"
WORKFLOW = REPO / ".github" / "workflows" / "app-images.yml"
LABEL = "radon.model-clients.payload-sha256"
PAYLOAD_ARG = "MODEL_CLIENTS_PAYLOAD_SHA256"

PAYLOAD_FILES = (
    "docker/app/Dockerfile.python",
    "docker/app/vendor-clis/package.json",
    "docker/app/vendor-clis/package-lock.json",
    "docker/app/vendor-clis/install-native.cjs",
    "docker/app/vendor-clis/install-darwin.cjs",
    "docker/app/vendor-clis/antigravity.json",
    "docker/app/vendor-clis/install-antigravity.py",
    "docker/app/chart-runtime/package.json",
    "docker/app/chart-runtime/package-lock.json",
    "scripts/model_gateway/__init__.py",
    "scripts/model_gateway/client.py",
    "scripts/model_gateway/cli.py",
    "scripts/model_gateway/cli_bridge.py",
)


def _load_hasher():
    spec = importlib.util.spec_from_file_location("model_client_payload_inputs", HASHER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_payload_hasher_exists_and_covers_export_inputs() -> None:
    assert HASHER.is_file()
    module = _load_hasher()
    files = module.payload_files(REPO)
    for path in PAYLOAD_FILES:
        assert path in files
    assert "scripts/phase0_capture.sh" not in files
    assert "cloud/scripts/model_clients.py" not in files
    digest = module.payload_hash(REPO)
    assert len(digest) == 64
    int(digest, 16)


def test_payload_hash_ignores_unrelated_scripts(tmp_path: Path) -> None:
    module = _load_hasher()
    original = module.payload_hash(REPO)
    # The hasher reads REPO paths; a sibling tree with the same payload
    # files must match, and a helper edit must miss.
    import shutil

    tree = tmp_path / "radon"
    for path in PAYLOAD_FILES:
        dest = tree / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / path, dest)
    assert module.payload_hash(tree) == original
    helper = tree / "scripts/model_gateway/cli.py"
    helper.write_text(helper.read_text(encoding="utf-8") + "\n# payload change\n")
    assert module.payload_hash(tree) != original


def test_payload_hash_tracks_the_base_images_that_build_the_clients(tmp_path: Path) -> None:
    """The exported `node` is copied out of the pinned node base image, and the
    CLI installs run on it. A digest bump with no lockfile change must miss."""
    import shutil

    module = _load_hasher()
    tree = tmp_path / "radon"
    for path in PAYLOAD_FILES:
        dest = tree / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / path, dest)
    original = module.payload_hash(tree)
    dockerfile = tree / "docker/app/Dockerfile.python"
    text = dockerfile.read_text(encoding="utf-8")
    assert "AS chart-runtime" in text
    bumped = text.replace("@sha256:", "@sha256:0", 1)
    assert bumped != text
    dockerfile.write_text(bumped, encoding="utf-8")
    assert module.payload_hash(tree) != original


def test_dockerfile_records_the_payload_label_only_on_the_gateway_target() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    gateway = text.split("FROM runtime-deps AS model-gateway", 1)[1].split(
        "FROM runtime AS python", 1
    )[0]
    python = text.split("FROM runtime AS python", 1)[1]
    assert f"ARG {PAYLOAD_ARG}" in gateway
    assert f"LABEL {LABEL}=${PAYLOAD_ARG}" in gateway
    assert PAYLOAD_ARG not in python
    # The label layer must follow the client COPY/install steps so a payload
    # miss still rebuilds those layers; an ARG at the top of the stage would
    # bust the torch cache on every hash change.
    arg_at = gateway.rfind(f"ARG {PAYLOAD_ARG}")
    copy_clis = gateway.find("COPY --from=vendor-clis")
    assert copy_clis != -1 and arg_at > copy_clis


def test_gateway_build_passes_the_payload_hash_build_arg() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["gateway-image"]["steps"]
    hashed = next(step for step in steps if step.get("name") == "Hash python image inputs")
    assert "model_client_payload_inputs.py" in hashed["run"]
    assert "payload=" in hashed["run"]
    build = next(
        step
        for step in steps
        if step.get("with", {}).get("target") == "model-gateway"
    )
    args = build["with"].get("build-args") or ""
    assert f"{PAYLOAD_ARG}=${{{{ steps.pyimage.outputs.payload }}}}" in args


def test_dockerfile_client_copy_sources_are_in_the_payload_hasher() -> None:
    """A new COPY into the exported trees must join the hasher or this fails."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    covered = set(PAYLOAD_FILES)
    stage = None
    client_stages = {"vendor-clis", "vendor-clis-darwin", "chart-runtime"}
    for raw in text.splitlines():
        line = raw.strip()
        parts = line.split()
        if not parts:
            continue
        if parts[0].upper() == "FROM" and len(parts) >= 4 and parts[-2].upper() == "AS":
            stage = parts[-1]
            continue
        if parts[0].upper() != "COPY" or "--from=" in line:
            continue
        sources = parts[1:-1]
        dest = parts[-1]
        if stage in client_stages or dest.startswith("/opt/radon-clis") or dest.startswith(
            "/opt/radon-darwin"
        ):
            for source in sources:
                if source.startswith("-"):
                    continue
                assert source in covered, source
