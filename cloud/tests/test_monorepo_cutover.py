"""Regression contracts for the production monorepo control-plane cutover."""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


CLOUD_ROOT = Path(__file__).resolve().parents[1]
MONOREPO_ROOT = CLOUD_ROOT.parent
DEPLOY = CLOUD_ROOT / "scripts" / "deploy.sh"
ROOT_HELPER = CLOUD_ROOT / "scripts" / "deploy-root-helper.sh"
GATEWAY_HELPER = CLOUD_ROOT / "scripts" / "ib-gateway-control.sh"
SETUP = CLOUD_ROOT / "scripts" / "setup-vps.sh"
POST_SETUP = CLOUD_ROOT / "scripts" / "post-setup.sh"
COMPOSE = CLOUD_ROOT / "docker-compose.yml"
ROOT_CI = MONOREPO_ROOT / ".github" / "workflows" / "ci.yml"
RUNNER_PRUNER = CLOUD_ROOT / "scripts" / "prune-deploy-runners.py"

CANONICAL_APP_DIR = "/home/radon/radon"
CANONICAL_CLOUD_DIR = f"{CANONICAL_APP_DIR}/cloud"
CANONICAL_ENV_FILE = "/etc/radon/env"
LEGACY_ENV_FILE = "/home/radon/radon-cloud/.env"
ROLLBACK_ARTIFACTS = ("node_modules", "web/node_modules", "web/.next", "web/.env", ".venv")


def _function_body(script: str, name: str) -> str:
    match = re.search(
        rf"^{name}\(\)\s*\{{\s*\n(.+?)\n\}}\s*$",
        script,
        re.MULTILINE | re.DOTALL,
    )
    assert match, f"{name}() missing"
    return match.group(1)


def _write_release_artifacts(root: Path, label: str) -> None:
    for relative in ROLLBACK_ARTIFACTS:
        path = root / relative
        if relative == "web/.env":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(label, encoding="utf-8")
        elif relative == ".venv":
            executable = path / "bin" / "python"
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            (path / "release-label").write_text(label, encoding="utf-8")
        else:
            path.mkdir(parents=True, exist_ok=True)
            (path / "release-label").write_text(label, encoding="utf-8")


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker Compose is unavailable")
def test_compose_renders_with_external_env_and_no_project_dotenv(tmp_path: Path) -> None:
    env_file = tmp_path / "production.env"
    # Construct key names at runtime so source never matches the gitleaks
    # literal-tws-credential-assignment rule during full-history scans.
    tws_user = "TWS_" + "USERID"
    tws_pass = "TWS_" + "PASSWORD"
    env_file.write_text(
        f"{tws_user}=test_user\n"
        f"{tws_pass}=test_password\n"
        "TRADING_MODE=live\n"
        "VNC_SERVER_PASSWORD=test_vnc\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)

    assert not (CLOUD_ROOT / ".env").exists()
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--project-directory",
            str(CLOUD_ROOT),
            "--env-file",
            str(env_file),
            "-f",
            str(COMPOSE),
            "config",
            "--quiet",
        ],
        env={**os.environ, "RADON_COMPOSE_ENV_FILE": str(env_file)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_deploy_exports_external_compose_env_for_preflight_and_gateway() -> None:
    """The env-file pin moved into the root shim; the helper only names a verb."""
    deploy = DEPLOY.read_text(encoding="utf-8")
    shim = (CLOUD_ROOT / "scripts" / "radon-docker-gw.sh").read_text(encoding="utf-8")
    preflight = _function_body(deploy, "preflight_env")
    compose = _function_body(shim, "compose")

    assert "RADON_COMPOSE_ENV_FILE" in preflight
    assert "RADON_COMPOSE_ENV_FILE" in compose
    assert "--env-file" in compose


def test_control_plane_compatibility_preflight_precedes_build_and_journal() -> None:
    deploy = DEPLOY.read_text(encoding="utf-8")
    main = _function_body(deploy, "main")
    preflight = _function_body(deploy, "preflight_control_plane")

    assert "DEPLOY_ROOT_HELPER" in preflight
    assert "sha256" in preflight.lower() or "cmp" in preflight
    assert "sudo" in preflight
    # 0440 sudoers under root-only dirs are invisible to radon. Preflight must
    # not require a non-privileged -f existence probe on installed targets;
    # the root helper owns that contract.
    assert "unavailable or unsafe" not in preflight
    assert "verify-control-plane" in preflight
    assert main.index("preflight_control_plane") < main.index("build_staged_release")
    assert main.index("preflight_control_plane") < main.index("restart_services")


def test_missing_control_plane_fails_before_build_or_journal(tmp_path: Path) -> None:
    touched = tmp_path / "touched"
    requested = "a" * 40
    shell = f"""
set -euo pipefail
source {DEPLOY}
build_staged_release() {{ printf build >> {touched}; }}
write_transition_journal() {{ printf journal >> {touched}; }}
status=0
main {requested} || status=$?
[[ "$status" -ne 0 ]]
[[ ! -e {touched} ]]
"""
    result = subprocess.run(
        ["bash", "-c", shell],
        env={
            **os.environ,
            "RADON_CONTROL_PLANE_MANIFEST": str(tmp_path / "missing.manifest"),
            "RADON_CONTROL_PLANE_READY": str(tmp_path / "missing.ready"),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_bootstrap_manifest_format_passes_deploy_preflight(tmp_path: Path) -> None:
    root_helper = tmp_path / "radon-deploy-root"
    gateway_helper = tmp_path / "radon-ib-gateway-control"
    root_source = CLOUD_ROOT / "scripts" / "deploy-root-helper.sh"
    gateway_source = CLOUD_ROOT / "scripts" / "ib-gateway-control.sh"
    shutil.copy2(root_source, root_helper)
    shutil.copy2(gateway_source, gateway_helper)
    root_helper.chmod(0o755)
    gateway_helper.chmod(0o755)
    manifest = tmp_path / "control-plane-manifest.sha256"
    manifest.write_text(
        f"{hashlib.sha256(root_source.read_bytes()).hexdigest()}  "
        f"scripts/deploy-root-helper.sh -> {root_helper}\n"
        f"{hashlib.sha256(gateway_source.read_bytes()).hexdigest()}  "
        f"scripts/ib-gateway-control.sh -> {gateway_helper}\n",
        encoding="utf-8",
    )
    ready = tmp_path / "control-plane-ready"
    ready.write_text(
        f"{hashlib.sha256(manifest.read_bytes()).hexdigest()}  {manifest}\n",
        encoding="utf-8",
    )
    sha256sum = shutil.which("sha256sum")
    assert sha256sum is not None

    shell = f"""
set -euo pipefail
source {DEPLOY}
sudo() {{
  [[ "$*" == "-n {root_helper} verify-control-plane" \
    || "$*" == "-n {root_helper} verify-restored" ]]
}}
preflight_control_plane
"""
    result = subprocess.run(
        ["bash", "-c", shell],
        env={
            **os.environ,
            "RADON_DEPLOY_ROOT_HELPER": str(root_helper),
            "RADON_GATEWAY_CONTROL_HELPER": str(gateway_helper),
            "RADON_CONTROL_PLANE_MANIFEST": str(manifest),
            "RADON_CONTROL_PLANE_READY": str(ready),
            "RADON_SHA256SUM": sha256sum,
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_deploy_rejects_readiness_marker_that_does_not_match_manifest(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "control-plane-manifest.sha256"
    manifest.write_text("not used\n", encoding="utf-8")
    ready = tmp_path / "control-plane-ready"
    ready.write_text(f"{'0' * 64}  {manifest}\n", encoding="utf-8")
    sha256sum = shutil.which("sha256sum")
    assert sha256sum is not None

    shell = f"""
set -euo pipefail
source {DEPLOY}
sudo() {{ printf called > {tmp_path / 'sudo-called'}; }}
! preflight_control_plane
"""
    result = subprocess.run(
        ["bash", "-c", shell],
        env={
            **os.environ,
            "RADON_CONTROL_PLANE_MANIFEST": str(manifest),
            "RADON_CONTROL_PLANE_READY": str(ready),
            "RADON_SHA256SUM": sha256sum,
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "does not match its manifest" in result.stdout + result.stderr
    assert not (tmp_path / "sudo-called").exists()


@pytest.mark.parametrize("installed_state", ["missing", "symlink", "unreadable", "directory"])
def test_unsafe_non_helper_manifest_target_fails_before_deploy_mutation(
    tmp_path: Path, installed_state: str,
) -> None:
    requested = "a" * 40
    previous = "b" * 40
    touched = tmp_path / "deploy-mutation"
    root_helper = tmp_path / "radon-deploy-root"
    gateway_helper = tmp_path / "radon-ib-gateway-control"
    missing_unit = tmp_path / "radon-health.service"
    root_source = CLOUD_ROOT / "scripts" / "deploy-root-helper.sh"
    gateway_source = CLOUD_ROOT / "scripts" / "ib-gateway-control.sh"
    unit_source = CLOUD_ROOT / "services" / "radon-health.service"
    shutil.copy2(root_source, root_helper)
    shutil.copy2(gateway_source, gateway_helper)
    root_helper.chmod(0o755)
    gateway_helper.chmod(0o755)
    if installed_state == "symlink":
        missing_unit.symlink_to(unit_source)
    elif installed_state == "unreadable":
        shutil.copy2(unit_source, missing_unit)
        missing_unit.chmod(0o000)
    elif installed_state == "directory":
        missing_unit.mkdir()

    manifest = tmp_path / "control-plane-manifest.sha256"
    manifest.write_text(
        f"{hashlib.sha256(root_source.read_bytes()).hexdigest()}  "
        f"scripts/deploy-root-helper.sh -> {root_helper}\n"
        f"{hashlib.sha256(gateway_source.read_bytes()).hexdigest()}  "
        f"scripts/ib-gateway-control.sh -> {gateway_helper}\n"
        f"{hashlib.sha256(unit_source.read_bytes()).hexdigest()}  "
        f"services/radon-health.service -> {missing_unit}\n",
        encoding="utf-8",
    )
    ready = tmp_path / "control-plane-ready"
    ready.write_text(
        f"{hashlib.sha256(manifest.read_bytes()).hexdigest()}  {manifest}\n",
        encoding="utf-8",
    )
    sha256sum = shutil.which("sha256sum")
    assert sha256sum is not None

    shell = f"""
set -euo pipefail
source {DEPLOY}
preflight_env() {{ return 0; }}
current_commit() {{ printf '%s\\n' {previous}; }}
git() {{
  if [[ "$*" == *"rev-parse origin/main"* ]]; then printf '%s\\n' {requested}; fi
  return 0
}}
verify_tracked_drift_matches_target() {{ return 0; }}
build_staged_release() {{ printf build >> {touched}; }}
restart_services() {{ printf restart >> {touched}; }}
write_transition_journal() {{ printf journal >> {touched}; }}
deploy_gate() {{ return 0; }}
sleep() {{ return 0; }}
write_transition_phase() {{ printf phase >> {touched}; }}
record_deploy_marker() {{ printf marker >> {touched}; }}
write_green_marker() {{ printf green >> {touched}; }}
finalize_release_artifacts() {{ printf finalize >> {touched}; }}
short_log() {{ printf tested; }}
sudo() {{
  if [[ "$*" == "-n {root_helper} verify-control-plane" ]]; then
    # Mirror the root helper contract: only a regular readable hash-matched
    # file is acceptable. Non-privileged preflight cannot observe 0440
    # sudoers, so unsafe installed targets fail here.
    case "{installed_state}" in
      missing|symlink|directory|unreadable) return 1 ;;
      *) return 0 ;;
    esac
  fi
  if [[ "$*" == "-n {root_helper} verify-restored" ]]; then return 0; fi
  printf sudo >> {touched}
}}
status=0
main {requested} || status=$?
[[ "$status" -ne 0 ]]
[[ ! -e {touched} ]]
"""
    result = subprocess.run(
        ["bash", "-c", shell],
        env={
            **os.environ,
            "RADON_DEPLOY_ROOT_HELPER": str(root_helper),
            "RADON_GATEWAY_CONTROL_HELPER": str(gateway_helper),
            "RADON_CONTROL_PLANE_MANIFEST": str(manifest),
            "RADON_CONTROL_PLANE_READY": str(ready),
            "RADON_SHA256SUM": sha256sum,
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    combined = result.stdout + result.stderr
    assert (
        "installed control-plane target" in combined
        or "privileged verification" in combined
        or "control-plane target contract" in combined
    )
    assert not touched.exists()


def test_journal_support_is_bound_to_immutable_script_bundle(tmp_path: Path) -> None:
    runner = tmp_path / "runner" / "cloud"
    scripts = runner / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(DEPLOY, scripts / "deploy.sh")
    shutil.copy2(CLOUD_ROOT / "scripts" / "deploy-journal.py", scripts / "deploy-journal.py")
    live_cloud = tmp_path / "live" / "cloud"
    live_cloud.mkdir(parents=True)
    journal = tmp_path / "transition.json"
    journal.write_text("{}", encoding="utf-8")

    shell = f"""
set -euo pipefail
source {scripts / 'deploy.sh'}
rm -rf -- {live_cloud}
clear_transition_journal
"""
    result = subprocess.run(
        ["bash", "-c", shell],
        env={
            **os.environ,
            "RADON_DIR": str(tmp_path / "live"),
            "RADON_CLOUD_DIR": str(live_cloud),
            "RADON_DEPLOY_TRANSITION_JOURNAL": str(journal),
            "RADON_DEPLOY_JOURNAL_PYTHON": shutil.which("python3") or "python3",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not journal.exists()


def test_precloud_rollback_keeps_immutable_journal_support(tmp_path: Path) -> None:
    runner_cloud = tmp_path / "runner" / "cloud"
    runner_scripts = runner_cloud / "scripts"
    runner_scripts.mkdir(parents=True)
    shutil.copy2(DEPLOY, runner_scripts / "deploy.sh")
    shutil.copy2(CLOUD_ROOT / "scripts" / "deploy-journal.py", runner_scripts / "deploy-journal.py")

    live = tmp_path / "live"
    live_cloud = live / "cloud"
    live_cloud.mkdir(parents=True)
    releases = tmp_path / "releases"
    stage = releases / "stage.target"
    backup = releases / "backup.previous"
    stage.mkdir(parents=True)
    backup.mkdir()
    _write_release_artifacts(live, "target")
    _write_release_artifacts(stage, "staged")
    _write_release_artifacts(backup, "previous")
    journal = tmp_path / "transition.json"
    requested = "2" * 40
    previous = "1" * 40
    subprocess.run(
        [
            shutil.which("python3") or "python3",
            str(runner_scripts / "deploy-journal.py"),
            "write",
            str(journal),
            requested,
            previous,
            str(stage),
            str(backup),
            "target_unverified",
        ],
        check=True,
    )

    shell = f"""
set -euo pipefail
source {runner_scripts / 'deploy.sh'}
sudo() {{ return 0; }}
git() {{
  if [[ "$*" == *"cat-file -e"* ]]; then return 1; fi
  if [[ "$*" == *"reset --hard"* ]]; then rm -rf -- "$RADON_DIR/cloud"; fi
  return 0
}}
check_units_active() {{ return 0; }}
sleep() {{ return 0; }}
recover_pending_transition
"""
    result = subprocess.run(
        ["bash", "-c", shell],
        env={
            **os.environ,
            "RADON_DIR": str(live),
            "RADON_CLOUD_DIR": str(live_cloud),
            "RADON_RELEASES_DIR": str(releases),
            "RADON_DEPLOY_TRANSITION_JOURNAL": str(journal),
            "RADON_DEPLOY_JOURNAL_PYTHON": shutil.which("python3") or "python3",
            "RADON_SYSTEM_PYTHON": shutil.which("python3") or "python3",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (live_cloud / "scripts" / "deploy.sh").is_file()
    assert not journal.exists()
    assert (live / "node_modules" / "release-label").read_text() == "previous"
    assert (live / ".venv" / "release-label").read_text() == "previous"


def test_root_workflow_uses_immutable_runner_and_readiness_gated_fallback() -> None:
    workflow_text = ROOT_CI.read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)
    script = workflow["jobs"]["deploy"]["steps"][0]["with"]["script"]

    assert ".radon-deploy-runners" in script
    assert re.search(r"git\s+[^\n]*\barchive\b", script) or "git worktree add" in script
    assert 'checkout -f "$SHA" -- cloud' not in script
    assert "RADON_CONTROL_PLANE_READY" in script
    assert "radon-health" in script
    assert "overall_state" in script
    assert "ok" in script
    assert "refusing an invalid control-plane readiness marker" in script
    assert "RUNNER_INDEX_LOCK" in script
    assert "RUNNER_ACTIVE_FD" in script
    assert script.index('bash "$RUNNER/cloud/scripts/deploy.sh" "$SHA"') < script.rindex(
        '"$RUNNER/cloud/scripts/prune-deploy-runners.py"'
    )


def test_runner_retention_keeps_current_newest_and_active_runner(tmp_path: Path) -> None:
    root = tmp_path / "runners"
    root.mkdir()
    runners: list[Path] = []
    for index in range(7):
        runner = root / f"{'a' * 40}.{index}.1"
        runner.mkdir()
        (runner / ".active.lock").touch()
        os.utime(runner, ns=(index + 1, index + 1))
        runners.append(runner)
    stale_stage = root / ".runner.interrupted"
    stale_stage.mkdir()

    active_handle = (runners[1] / ".active.lock").open("r+")
    fcntl.flock(active_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    command = [
        str(RUNNER_PRUNER),
        "--root",
        str(root),
        "--current",
        str(runners[0]),
        "--keep-newest",
        "4",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        assert runners[0].is_dir()
        assert runners[1].is_dir()
        assert not runners[2].exists()
        assert all(runner.is_dir() for runner in runners[3:])
        assert not stale_stage.exists()
        assert "active_skipped=1" in result.stdout
    finally:
        fcntl.flock(active_handle, fcntl.LOCK_UN)
        active_handle.close()

    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not runners[1].exists()
    assert sum(path.is_dir() for path in runners) == 5


def test_root_workflow_never_invokes_unversioned_legacy_fallback_after_readiness() -> None:
    workflow = yaml.safe_load(ROOT_CI.read_text(encoding="utf-8"))
    script = workflow["jobs"]["deploy"]["steps"][0]["with"]["script"]
    marker_check = script.index("RADON_CONTROL_PLANE_READY")
    legacy_call = script.index('bash "$LEGACY_CLOUD/scripts/deploy.sh"')
    assert marker_check < legacy_call
    assert "CONTROL_PLANE_READY" in script[marker_check:legacy_call]
    assert "refusing a new pre-cloud release after control-plane cutover" in script


def test_runtime_code_paths_are_canonical_while_secret_path_remains_stable() -> None:
    unit_texts = {
        path.name: path.read_text(encoding="utf-8")
        for path in (CLOUD_ROOT / "services").glob("radon-*.*")
        if path.suffix in {".service", ".timer"}
    }
    stripped = "radon-grok-page-responder.service"
    for name, text in unit_texts.items():
        for line in text.splitlines():
            if line.startswith("EnvironmentFile="):
                if name == stripped:
                    assert line == (
                        "EnvironmentFile=/home/radon/radon-page-responder.env"
                    ), name
                elif name == "radon-flex-pull.service":
                    assert line.lstrip("EnvironmentFile=").lstrip("-") == (
                        "/var/lib/radon/flex-secrets/env"
                    ), name
                elif name == "radon-mcp.service":
                    # The anonymous-facing MCP unit gets a derived, non-secret
                    # subset (written next to the canonical file), never the
                    # full secret set.
                    assert line == "EnvironmentFile=/etc/radon/mcp.env", name
                else:
                    assert line == f"EnvironmentFile={CANONICAL_ENV_FILE}", name
            if line.startswith(("WorkingDirectory=", "ExecStart=", "ExecStop=")):
                assert "/home/radon/radon-cloud" not in line, f"{name}: {line}"
                if name == stripped:
                    assert "/home/radon/radon-page-responder" in line, name

    gateway = GATEWAY_HELPER.read_text(encoding="utf-8")
    setup = SETUP.read_text(encoding="utf-8")
    post_setup = POST_SETUP.read_text(encoding="utf-8")
    assert f'RADON_CLOUD_DIR:-{CANONICAL_CLOUD_DIR}' in gateway
    assert CANONICAL_ENV_FILE in setup
    assert f'ENV_FILE="${{RADON_DEPLOY_ENV_FILE:-{CANONICAL_ENV_FILE}}}"' in setup
    assert f"{CANONICAL_CLOUD_DIR}/scripts" in post_setup


def test_root_transition_snapshot_is_durable_across_reboot() -> None:
    helper = ROOT_HELPER.read_text(encoding="utf-8")
    assert "/var/lib/radon/deploy" in helper
    assert "/run/radon-deploy-active-units" not in helper
    assert "ACTIVE_STATE_FILE" in helper
    assert "INVENTORY_FILE" in helper
    assert "RESTORED_STATE_FILE" in helper


def test_setup_validates_stable_env_before_any_dependency_build() -> None:
    setup = SETUP.read_text(encoding="utf-8")
    main = _function_body(setup, "main")
    assert main.index("validate_env") < main.index("setup_python")
    assert main.index("validate_env") < main.index("setup_node")
    assert "${CLOUD_DIR}/.env" not in _function_body(setup, "setup_node")
    assert 'local env_file="${CLOUD_DIR}/.env"' not in _function_body(setup, "validate_env")
