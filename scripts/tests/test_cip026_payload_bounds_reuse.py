"""CIP-026: skip the 77s client-export bounds check when that payload
already passed on a previous gateway rebuild.

Mixed rebuild 42142783710 spent 77s in 'Enforce pinned model client disk
bounds' after a 202s gateway build. export_from_image only reads vendor-clis,
Darwin, node, and the four model_gateway helpers — the same files
model_client_payload_inputs.py hashes. A scripts-only miss still rebuilds
the gateway (new image id) and re-exports 1.62GB + 701MB of identical
clients. A pbounds-<64 hex> receipt, written after a passing check, lets
the next identical payload skip the export. A miss, a missing payload
hash, or a malformed tag still measures. Not a deploy tag; :latest is
never a donor.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "app-images.yml"
DEPLOY = REPO / "cloud" / "scripts" / "deploy.sh"
RUNTIME = REPO / "cloud" / "scripts" / "radon-app-runtime.sh"

REUSE = "Reuse verified client disk bounds"
CHECK = "Enforce pinned model client disk bounds"
RECORD = "Record client disk bounds receipt"
BUILD = "Build and publish model gateway image"
SMOKE = "Smoke model gateway runtime"
PAYLOAD = "a" * 64
SHA = "c" * 40
PBOUNDS = f"ghcr.io/joemccann/radon-model-gateway:pbounds-{PAYLOAD}"


def _python_job() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["python-image"]


def _step(name: str) -> dict:
    for step in _python_job()["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(f"missing step {name}")


def _run(name: str) -> str:
    return _step(name)["run"]


def _names() -> list[str]:
    return [step.get("name", "") for step in _python_job()["steps"]]


def test_bounds_reuse_step_inspects_only_a_payload_receipt() -> None:
    reuse = _step(REUSE)
    script = reuse["run"]
    assert reuse["id"] == "reuse-bounds"
    assert reuse["if"] == (
        "steps.reuse-gateway.outputs.hit != 'true'"
        " && (github.event_name != 'pull_request' || steps.unhashed.outputs.gateway == 'unchanged')"
    )
    assert "docker buildx imagetools inspect" in script
    assert "pbounds-" in script
    assert "${{ steps.pyimage.outputs.payload }}" in script
    assert "hit=false" in script
    assert "hit=true" in script
    assert "docker tag " not in script
    assert "docker push " not in script
    assert "docker build " not in script
    assert "imagetools create" not in script
    assert ":latest" not in script
    assert "radon-model-gateway:ihash-" not in script
    assert "continue-on-error" not in reuse


def test_bounds_check_still_runs_unless_gateway_or_payload_reused() -> None:
    names = _names()
    assert names.index(BUILD) < names.index(REUSE) < names.index(CHECK)
    check = _step(CHECK)
    assert check["if"] == (
        "steps.reuse-gateway.outputs.hit != 'true'"
        " && steps.reuse-bounds.outputs.hit != 'true'"
    )
    assert "scripts/ci/check_model_client_bounds.py" in check["run"]
    assert '--image "ghcr.io/joemccann/radon-model-gateway:${{ github.sha }}"' in check["run"]
    assert "continue-on-error" not in check
    smoke = _step(SMOKE)
    assert smoke["if"] == "steps.reuse-gateway.outputs.hit != 'true'"


def test_bounds_receipt_is_recorded_only_after_a_passing_check_on_main() -> None:
    names = _names()
    assert names.index(CHECK) < names.index(RECORD)
    record = _step(RECORD)
    assert record["if"] == (
        "github.ref == 'refs/heads/main' && github.event_name == 'push'"
        " && steps.reuse-gateway.outputs.hit != 'true'"
        " && steps.reuse-bounds.outputs.hit != 'true'"
    )
    script = record["run"]
    assert "docker buildx imagetools create" in script
    assert "pbounds-" in script
    assert "radon-model-gateway:${{ github.sha }}" in script
    assert ":latest" not in script
    assert "continue-on-error" not in record


def test_pbounds_is_not_a_deploy_or_runtime_tag() -> None:
    for path in (DEPLOY, RUNTIME):
        text = path.read_text(encoding="utf-8")
        assert "pbounds-" not in text
        assert "payloadbounds" not in text


def _run_reuse(
    tmp_path: Path, *, payload: str, inspect_status: int
) -> tuple[subprocess.CompletedProcess[str], list[str], str]:
    script = _run(REUSE)
    script = script.replace("${{ steps.pyimage.outputs.payload }}", payload)
    assert "${{" not in script, "new interpolation: teach this fixture its value"
    log = tmp_path / "docker.log"
    output = tmp_path / "github-output"
    docker = tmp_path / "bin" / "docker"
    docker.parent.mkdir()
    docker.write_text(
        "#!/bin/bash\n"
        'printf "%s\\n" "$*" >> "$CALL_LOG"\n'
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
def test_payload_receipt_inspect_is_read_only(
    tmp_path: Path, inspect_status: int, hit: str
) -> None:
    completed, calls, output = _run_reuse(
        tmp_path, payload=PAYLOAD, inspect_status=inspect_status
    )
    assert completed.returncode == 0, completed.stderr
    assert calls == [f"buildx imagetools inspect {PBOUNDS}"]
    assert "hit=" + hit in output


@pytest.mark.parametrize("payload", ["", "latest", "not-a-hash", "a" * 63, PAYLOAD.upper()])
def test_malformed_payload_hash_measures_without_inspect(
    tmp_path: Path, payload: str
) -> None:
    completed, calls, output = _run_reuse(
        tmp_path, payload=payload, inspect_status=0
    )
    assert completed.returncode == 0, completed.stderr
    assert calls == []
    assert "hit=false" in output
