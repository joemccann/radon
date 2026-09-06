"""REL-242 (R-647, R-648 residual, R-649).

R-647: deploy.sh preflight tries the root shim's config-check first and falls
back to a direct `docker compose config` render. The fallback was silent: a
shim that refused (exit 78 on a tampered compose/env file) left preflight
green with no trace. The fallback now logs one countable line naming the shim
exit code, so "how often does the direct path carry preflight" is a journal
grep, not a guess.

R-648 residual: the refresh path (deploy-root-helper.sh refresh_install_file)
must render-validate the INCOMING staged compose body before install. REL-234's
shared compose_body_is_valid already does this on that path; these tests pin
it so the arm cannot be dropped.

R-649: drift_audit FILE_PAIRS now covers the four root-run surfaces it missed:
radon-docker-gw, the installed ib-gateway-compose.yml (compared against its
git-blob source per R-636, never the radon-writable working tree),
radon-app-runtime and radon-deploy-root. App-role hosts skip only the gateway
surfaces they do not own.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_refresh_control_plane as trcp  # noqa: E402
from test_drift_audit import da  # noqa: E402

CLOUD_ROOT = Path(__file__).resolve().parents[1]
DEPLOY = CLOUD_ROOT / "scripts" / "deploy.sh"

SHIM_FALLBACK_MARKER = "shim refused (exit 78), direct render covered"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


# --- R-647: preflight logs the shim-vs-direct fallback ----------------------


def _run_preflight(tmp_path: Path, *, shim_exit: int, docker_exit: int = 0):
    env_file = tmp_path / "env"
    env_file.write_text(
        "IB_GATEWAY_MODE=cloud\n"
        "RADON_MODE=hetzner\n"
        "NODE_ENV=production\n"
        "RADON_HOST_ROLE=combined\n"
        "IB_GATEWAY_HOST=127.0.0.1\n"
        "TRADING_MODE=paper\n"
        "IB_GATEWAY_PORT=4002\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    required = tmp_path / "required-env.txt"
    required.write_text("", encoding="utf-8")

    bindir = tmp_path / "bin"
    bindir.mkdir()
    shim_log = tmp_path / "shim.log"
    docker_log = tmp_path / "docker.log"
    shim = tmp_path / "radon-docker-gw"
    _write_executable(
        shim,
        f"#!/bin/bash\nprintf '%s\\n' \"$*\" >> '{shim_log}'\nexit {shim_exit}\n",
    )
    # sudo -n <shim> config-check -> run the shim directly.
    _write_executable(
        bindir / "sudo",
        '#!/bin/bash\nwhile [[ "${1:-}" == -* ]]; do shift; done\nexec "$@"\n',
    )
    _write_executable(
        bindir / "docker",
        f"#!/bin/bash\nprintf '%s\\n' \"$*\" >> '{docker_log}'\nexit {docker_exit}\n",
    )

    command = f"""
set -uo pipefail
export PATH='{bindir}':"$PATH"
export RADON_DEPLOY_ENV_FILE='{env_file}'
export RADON_REQUIRED_ENV_FILE='{required}'
export RADON_ENV_CHECKER_PYTHON='{sys.executable}'
export RADON_DOCKER_GW='{shim}'
export RADON_CLOUD_DIR='{CLOUD_ROOT}'
source '{DEPLOY}'
preflight_env
"""
    result = subprocess.run(
        ["bash", "-c", command], capture_output=True, text=True, timeout=60
    )
    return result, shim_log, docker_log


def test_shim_refusal_with_direct_cover_logs_one_countable_line(tmp_path: Path) -> None:
    result, shim_log, docker_log = _run_preflight(tmp_path, shim_exit=78)
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert shim_log.read_text(encoding="utf-8").splitlines() == ["config-check"]
    assert "config" in docker_log.read_text(encoding="utf-8")
    assert combined.count(SHIM_FALLBACK_MARKER) == 1, combined


def test_shim_success_emits_no_fallback_line(tmp_path: Path) -> None:
    result, shim_log, docker_log = _run_preflight(tmp_path, shim_exit=0)
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert SHIM_FALLBACK_MARKER not in combined
    assert not docker_log.exists()


def test_shim_refusal_without_direct_cover_still_fails_preflight(tmp_path: Path) -> None:
    result, _shim_log, _docker_log = _run_preflight(
        tmp_path, shim_exit=78, docker_exit=1
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert "docker compose config validation failed" in combined
    assert SHIM_FALLBACK_MARKER not in combined


# --- R-648 residual: refresh path renders the incoming staged body ----------


def test_refresh_refuses_an_invalid_staged_compose_body(tmp_path: Path) -> None:
    box = trcp.Sandbox(tmp_path)
    source = box.cloud / "docker-compose.yml"
    source.write_text(
        source.read_text(encoding="utf-8") + "    cap_add:\n      - SYS_ADMIN\n",
        encoding="utf-8",
    )
    box.commit_sources()

    trcp._refused_without_install(
        box, "refresh-control-plane-privileged", "compose validation failed"
    )


# --- R-649: drift audit covers the root-run helper surfaces -----------------


EXPECTED_NEW_PAIRS = [
    (
        "/usr/local/sbin/radon-deploy-root",
        "scripts/deploy-root-helper.sh",
        "radon-deploy-root",
    ),
    (
        "/usr/local/sbin/radon-app-runtime",
        "scripts/radon-app-runtime.sh",
        "radon-app-runtime",
    ),
    (
        "/usr/local/sbin/radon-docker-gw",
        "scripts/radon-docker-gw.sh",
        "radon-docker-gw",
    ),
    (
        "/etc/radon/ib-gateway-compose.yml",
        "git:docker-compose.yml",
        "ib-gateway-compose",
    ),
]


def test_root_run_helper_surfaces_are_drift_audited() -> None:
    for pair in EXPECTED_NEW_PAIRS:
        assert pair in da.FILE_PAIRS, pair


def test_modified_installed_shim_emits_file_mismatch(monkeypatch) -> None:
    repo_text = (CLOUD_ROOT / "scripts" / "radon-docker-gw.sh").read_text(
        encoding="utf-8"
    )
    monkeypatch.setattr(da, "REPO", CLOUD_ROOT)
    monkeypatch.setattr(da, "_read", lambda path: repo_text + "\n# tampered\n")
    drift = da._compare_file_pair(
        "/usr/local/sbin/radon-docker-gw",
        "scripts/radon-docker-gw.sh",
        "radon-docker-gw",
    )
    assert drift is not None
    assert drift["id"] == "file-mismatch:radon-docker-gw"


def _blob_repo(tmp_path: Path) -> Path:
    cloud = tmp_path / "cloud"
    cloud.mkdir()
    (cloud / "docker-compose.yml").write_text(
        "services:\n  ib-gateway:\n    container_name: ib-gateway\n",
        encoding="utf-8",
    )
    trcp._git(tmp_path, "init", "-b", "main", ".")
    trcp._git(tmp_path, "config", "user.email", "rel242@test")
    trcp._git(tmp_path, "config", "user.name", "rel242")
    trcp._git(tmp_path, "add", "cloud")
    trcp._git(tmp_path, "commit", "-m", "blob")
    return cloud


def test_installed_compose_is_compared_against_the_git_blob(
    tmp_path: Path, monkeypatch
) -> None:
    """R-636: the comparison basis is HEAD's blob, not the radon-writable tree."""
    cloud = _blob_repo(tmp_path)
    blob_text = (cloud / "docker-compose.yml").read_text(encoding="utf-8")
    monkeypatch.setattr(da, "REPO", cloud)
    monkeypatch.setattr(da, "GIT_REPO", tmp_path)

    # A working-tree edit that never landed on HEAD must not move the basis.
    (cloud / "docker-compose.yml").write_text(
        blob_text + "    cap_add:\n      - SYS_ADMIN\n", encoding="utf-8"
    )

    monkeypatch.setattr(da, "_read", lambda path: blob_text)
    assert (
        da._compare_file_pair(
            "/etc/radon/ib-gateway-compose.yml",
            "git:docker-compose.yml",
            "ib-gateway-compose",
        )
        is None
    )

    monkeypatch.setattr(da, "_read", lambda path: blob_text + "# tampered\n")
    drift = da._compare_file_pair(
        "/etc/radon/ib-gateway-compose.yml",
        "git:docker-compose.yml",
        "ib-gateway-compose",
    )
    assert drift is not None
    assert drift["id"] == "file-mismatch:ib-gateway-compose"


def test_app_role_still_audits_the_app_host_surfaces_it_owns(monkeypatch) -> None:
    checked: list[str] = []

    def compare_file_pair(_live, _repo, label):
        checked.append(label)
        return None

    monkeypatch.setattr(da, "resolve_host_role", lambda environ=None: "app")
    monkeypatch.setattr(da, "_compare_file_pair", compare_file_pair)
    monkeypatch.setattr(da, "_check_compose", lambda drifts: None)
    monkeypatch.setattr(da, "_check_units", lambda drifts, known: None)
    monkeypatch.setattr(da, "_check_sudoers", lambda drifts, known: None)
    monkeypatch.setattr(da, "_check_env_invariants", lambda drifts: None)
    monkeypatch.setattr(da, "_read_repo", lambda relative: "")
    _drifts, _allowed, known = da.gather()

    # App hosts own the deploy helper and the app runtime; audited.
    assert "radon-deploy-root" in checked
    assert "radon-app-runtime" in checked
    # Gateway-plane surfaces are absent by design on app; skipped, visibly.
    assert "radon-docker-gw" not in checked
    assert "ib-gateway-compose" not in checked
    assert "role-skipped:radon-docker-gw" in known
    assert "role-skipped:ib-gateway-compose" in known
