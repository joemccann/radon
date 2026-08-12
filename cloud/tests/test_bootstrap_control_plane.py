"""Regression tests for the root-only control-plane artifact bootstrap."""

from __future__ import annotations

import fcntl
import hashlib
import os
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest


CLOUD_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = CLOUD_ROOT / "scripts" / "bootstrap-control-plane.sh"
ROOT_HELPER = CLOUD_ROOT / "scripts" / "deploy-root-helper.sh"


@dataclass(frozen=True)
class Artifact:
    source: str
    target: str
    mode: int


ARTIFACTS = (
    Artifact("scripts/deploy-root-helper.sh", "/usr/local/sbin/radon-deploy-root", 0o755),
    Artifact("scripts/ib-gateway-control.sh", "/usr/local/bin/radon-ib-gateway-control", 0o755),
    Artifact("scripts/operator-radon.sh", "/usr/local/bin/radon", 0o755),
    # Root runs the audit, so its payload cannot live in the radon-writable
    # checkout. It is data for a root-owned interpreter, hence 0644 not 0755.
    Artifact("scripts/drift_audit.py", "/usr/local/lib/radon/drift_audit.py", 0o644),
    Artifact("config/sudoers.d/radon-deploy", "/etc/sudoers.d/radon-deploy", 0o440),
    Artifact("config/sudoers.d/radon-monitor", "/etc/sudoers.d/radon-monitor", 0o440),
    Artifact("config/sudoers.d/radon-ops", "/etc/sudoers.d/radon-ops", 0o440),
    Artifact("config/sudoers.d/radon-caddy", "/etc/sudoers.d/radon-caddy", 0o440),
    Artifact(
        "config/polkit/50-radon-services.rules",
        "/etc/polkit-1/rules.d/50-radon-services.rules",
        0o644,
    ),
    Artifact("services/radon-health.service", "/etc/systemd/system/radon-health.service", 0o644),
    Artifact(
        "services/radon-ib-gateway-preheld-restart.service",
        "/etc/systemd/system/radon-ib-gateway-preheld-restart.service",
        0o644,
    ),
    Artifact(
        "services/radon-ib-watchdog.service",
        "/etc/systemd/system/radon-ib-watchdog.service",
        0o644,
    ),
    Artifact(
        "services/radon-ib-gateway.service",
        "/etc/systemd/system/radon-ib-gateway.service",
        0o644,
    ),
    Artifact("services/radon-api.service", "/etc/systemd/system/radon-api.service", 0o644),
    Artifact(
        "services/radon-monitor.service", "/etc/systemd/system/radon-monitor.service", 0o644
    ),
    Artifact("services/radon-relay.service", "/etc/systemd/system/radon-relay.service", 0o644),
    Artifact(
        "services/radon-portfolio-sync.service",
        "/etc/systemd/system/radon-portfolio-sync.service",
        0o644,
    ),
    Artifact(
        "services/radon-refresh.service", "/etc/systemd/system/radon-refresh.service", 0o644
    ),
    Artifact(
        "services/radon-db-backup.service",
        "/etc/systemd/system/radon-db-backup.service",
        0o644,
    ),
    Artifact(
        "services/radon-drift-audit.service",
        "/etc/systemd/system/radon-drift-audit.service",
        0o644,
    ),
    Artifact(
        "services/radon-nextjs-db-watchdog.service",
        "/etc/systemd/system/radon-nextjs-db-watchdog.service",
        0o644,
    ),
)


def _write_executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _installed_path(rootfs: Path, artifact: Artifact) -> Path:
    return rootfs / artifact.target.removeprefix("/")


def _manifest_path(rootfs: Path) -> Path:
    return rootfs / "var/lib/radon/control-plane-manifest.sha256"


def _ready_path(rootfs: Path) -> Path:
    return rootfs / "var/lib/radon/control-plane-ready"


@dataclass
class Sandbox:
    source: Path
    rootfs: Path
    command_log: Path
    env: dict[str, str]

    @property
    def lock_path(self) -> Path:
        return self.rootfs / "home/radon/.radon-deploy.lock"

    def run(self, **env_overrides: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(BOOTSTRAP)],
            env={**self.env, **env_overrides},
            capture_output=True,
            text=True,
        )


def _sandbox(tmp_path: Path) -> Sandbox:
    source = tmp_path / "cloud"
    rootfs = tmp_path / "rootfs"
    fake_bin = tmp_path / "bin"
    command_log = tmp_path / "commands.log"

    for artifact in ARTIFACTS:
        destination = source / artifact.source
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(CLOUD_ROOT / artifact.source, destination)

    _write_executable(
        fake_bin / "flock",
        f"""#!{sys.executable}
import fcntl
import sys

fd = int(sys.argv[-1])
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit(75)
""",
    )
    _write_executable(
        fake_bin / "visudo",
        """#!/bin/bash
set -euo pipefail
printf 'visudo\\t%s\\n' "$*" >> "$RADON_BOOTSTRAP_TEST_COMMAND_LOG"
[[ "${RADON_BOOTSTRAP_TEST_FAIL_VISUDO:-0}" != "1" ]]
""",
    )
    _write_executable(
        fake_bin / "node",
        """#!/bin/bash
set -euo pipefail
printf 'node\\t%s\\n' "$*" >> "$RADON_BOOTSTRAP_TEST_COMMAND_LOG"
if [[ "$#" -ne 1 || "$1" != "--check" ]]; then
  exit 93
fi
[[ "${RADON_BOOTSTRAP_TEST_FAIL_POLKIT:-0}" != "1" ]]
""",
    )
    gateway_helper = rootfs / "usr/local/bin/radon-ib-gateway-control"
    _write_executable(
        fake_bin / "systemd-analyze",
        f"""#!/bin/bash
set -euo pipefail
printf 'systemd-analyze\\t%s\\n' "$*" >> "$RADON_BOOTSTRAP_TEST_COMMAND_LOG"
if [[ "$#" -lt 1 || "$1" != "verify" ]]; then
  exit 94
fi
if [[ "${{RADON_BOOTSTRAP_TEST_FAIL_SYSTEMD:-0}}" == "1" ]]; then
  exit 1
fi
# Mirror production systemd-analyze: unit files reference absolute helpers
# that must already exist when verify runs.
helper="{gateway_helper}"
if [[ ! -x "$helper" ]]; then
  printf 'Command %s is not executable: No such file or directory\\n' "$helper" >&2
  exit 1
fi
""",
    )
    _write_executable(
        fake_bin / "systemctl",
        """#!/bin/bash
set -euo pipefail
printf 'systemctl\\t%s\\n' "$*" >> "$RADON_BOOTSTRAP_TEST_COMMAND_LOG"
[[ "$#" -eq 1 && "$1" == "daemon-reload" ]] || exit 91
while IFS= read -r candidate; do
  [[ -z "$candidate" || -f "$candidate" ]] || exit 92
done <<< "${RADON_BOOTSTRAP_TEST_EXPECTED_TARGETS:-}"
[[ "${RADON_BOOTSTRAP_TEST_FAIL_RELOAD:-0}" != "1" ]]
""",
    )

    expected_targets = "\n".join(
        str(_installed_path(rootfs, artifact)) for artifact in ARTIFACTS
    )
    env = {
        **os.environ,
        "RADON_BOOTSTRAP_TEST_MODE": "1",
        "RADON_BOOTSTRAP_ALLOW_NONROOT": "1",
        "RADON_BOOTSTRAP_TEST_EUID": "0",
        "RADON_BOOTSTRAP_CLOUD_ROOT": str(source),
        "RADON_BOOTSTRAP_ROOT": str(rootfs),
        "RADON_BOOTSTRAP_FLOCK_BIN": str(fake_bin / "flock"),
        "RADON_BOOTSTRAP_VISUDO_BIN": str(fake_bin / "visudo"),
        "RADON_BOOTSTRAP_NODE_BIN": str(fake_bin / "node"),
        "RADON_BOOTSTRAP_SYSTEMD_ANALYZE_BIN": str(fake_bin / "systemd-analyze"),
        "RADON_BOOTSTRAP_SYSTEMCTL_BIN": str(fake_bin / "systemctl"),
        "RADON_BOOTSTRAP_TEST_COMMAND_LOG": str(command_log),
        "RADON_BOOTSTRAP_TEST_EXPECTED_TARGETS": expected_targets,
    }
    return Sandbox(source=source, rootfs=rootfs, command_log=command_log, env=env)


def _seed_bundle(sandbox: Sandbox) -> dict[Path, bytes]:
    snapshot: dict[Path, bytes] = {}
    for index, artifact in enumerate(ARTIFACTS):
        target = _installed_path(sandbox.rootfs, artifact)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(f"old-artifact-{index}\n".encode())
        target.chmod(artifact.mode)
        snapshot[target] = target.read_bytes()
    manifest = _manifest_path(sandbox.rootfs)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_bytes(b"old-manifest\n")
    snapshot[manifest] = manifest.read_bytes()
    ready = _ready_path(sandbox.rootfs)
    ready.write_bytes(b"old-ready\n")
    snapshot[ready] = ready.read_bytes()
    return snapshot


def _run_root_control_plane_verifier(sandbox: Sandbox) -> subprocess.CompletedProcess[str]:
    true_bin = shutil.which("true")
    rm_bin = shutil.which("rm")
    sha256sum = shutil.which("sha256sum")
    assert true_bin and rm_bin and sha256sum
    return subprocess.run(
        ["bash", str(ROOT_HELPER), "verify-control-plane"],
        env={
            **os.environ,
            "RADON_DEPLOY_HELPER_TEST_MODE": "1",
            "RADON_TEST_SYSTEMCTL": true_bin,
            "RADON_TEST_RM": rm_bin,
            "RADON_TEST_SYNC": true_bin,
            "RADON_TEST_FLOCK": true_bin,
            "RADON_TEST_SESSION_PYTHON": sys.executable,
            "RADON_TEST_ACTIVE_STATE_FILE": str(sandbox.rootfs / "var/lib/radon/deploy/active-units"),
            "RADON_TEST_REPLICA_PREFIX": str(sandbox.rootfs / "data/replica.db"),
            "RADON_TEST_CONTROL_PLANE_ROOT": str(sandbox.rootfs),
            "RADON_TEST_SHA256SUM": sha256sum,
        },
        capture_output=True,
        text=True,
    )


def _assert_snapshot(snapshot: dict[Path, bytes]) -> None:
    assert {path: path.read_bytes() for path in snapshot} == snapshot


def test_bootstrap_exists_and_is_executable() -> None:
    assert BOOTSTRAP.is_file()
    assert os.access(BOOTSTRAP, os.X_OK)


def test_non_root_is_refused_before_lock_or_install(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path)
    env = dict(sandbox.env)
    env.pop("RADON_BOOTSTRAP_ALLOW_NONROOT")

    result = subprocess.run(
        [str(BOOTSTRAP)],
        env={**env, "RADON_BOOTSTRAP_TEST_EUID": "1000"},
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "root" in result.stderr.lower()
    assert not sandbox.lock_path.exists()
    assert not _manifest_path(sandbox.rootfs).exists()
    assert not _ready_path(sandbox.rootfs).exists()


def test_held_deploy_lock_fails_closed(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path)
    sandbox.lock_path.parent.mkdir(parents=True, exist_ok=True)
    with sandbox.lock_path.open("w") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = sandbox.run()

    assert result.returncode == 75
    assert "lock" in result.stderr.lower()
    assert not _manifest_path(sandbox.rootfs).exists()
    assert not _ready_path(sandbox.rootfs).exists()


@pytest.mark.parametrize(
    "transition",
    (
        "home/radon/.radon-deploy-transition.json",
        "var/lib/radon/ib-gateway-transition.json",
        "var/lib/radon/deploy/active-units",
        "var/lib/radon/deploy/active-units.inventory",
        "var/lib/radon/deploy/active-units.restored",
    ),
    ids=(
        "app-deploy",
        "gateway",
        "root-active-units",
        "root-active-units-inventory",
        "root-active-units-restored",
    ),
)
def test_pending_transition_refuses_install(tmp_path: Path, transition: str) -> None:
    sandbox = _sandbox(tmp_path)
    transition_path = sandbox.rootfs / transition
    transition_path.parent.mkdir(parents=True, exist_ok=True)
    transition_path.write_text('{"phase":"promoting"}\n', encoding="utf-8")

    result = sandbox.run()

    assert result.returncode != 0
    assert "transition" in result.stderr.lower()
    assert not _manifest_path(sandbox.rootfs).exists()
    assert not _ready_path(sandbox.rootfs).exists()


@pytest.mark.parametrize(
    ("failure", "env_override"),
    (
        ("shell-syntax", None),
        ("sudoers", "RADON_BOOTSTRAP_TEST_FAIL_VISUDO"),
        ("polkit", "RADON_BOOTSTRAP_TEST_FAIL_POLKIT"),
        ("systemd", "RADON_BOOTSTRAP_TEST_FAIL_SYSTEMD"),
    ),
)
def test_validation_failure_leaves_installed_bundle_unchanged(
    tmp_path: Path, failure: str, env_override: str | None
) -> None:
    sandbox = _sandbox(tmp_path)
    snapshot = _seed_bundle(sandbox)
    overrides = {}
    if failure == "shell-syntax":
        helper = sandbox.source / "scripts/operator-radon.sh"
        helper.write_text("#!/bin/bash\nif then\n", encoding="utf-8")
    else:
        assert env_override is not None
        overrides[env_override] = "1"

    result = sandbox.run(**overrides)

    assert result.returncode != 0
    _assert_snapshot(snapshot)
    commands = (
        sandbox.command_log.read_text(encoding="utf-8")
        if sandbox.command_log.exists()
        else ""
    )
    assert "systemctl\t" not in commands


def test_success_installs_exact_bundle_manifest_then_daemon_reloads_once(
    tmp_path: Path,
) -> None:
    sandbox = _sandbox(tmp_path)

    result = sandbox.run()

    assert result.returncode == 0, result.stderr
    for artifact in ARTIFACTS:
        source = sandbox.source / artifact.source
        target = _installed_path(sandbox.rootfs, artifact)
        assert target.read_bytes() == source.read_bytes()
        assert stat.S_IMODE(target.stat().st_mode) == artifact.mode

    manifest_path = _manifest_path(sandbox.rootfs)
    records = manifest_path.read_text(encoding="utf-8").splitlines()
    assert len(records) == len(ARTIFACTS)
    for artifact, record in zip(ARTIFACTS, records, strict=True):
        expected_hash = hashlib.sha256(
            (sandbox.source / artifact.source).read_bytes()
        ).hexdigest()
        assert record == f"{expected_hash}  {artifact.source} -> {artifact.target}"

    manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    assert _ready_path(sandbox.rootfs).read_text(encoding="utf-8") == (
        f"{manifest_hash}  /var/lib/radon/control-plane-manifest.sha256\n"
    )

    commands = sandbox.command_log.read_text(encoding="utf-8").splitlines()
    systemctl_commands = [line for line in commands if line.startswith("systemctl\t")]
    assert systemctl_commands == ["systemctl\tdaemon-reload"]
    assert not any(
        forbidden in line.split("\t", maxsplit=1)[-1].split()
        for line in systemctl_commands
        for forbidden in ("start", "restart", "enable", "reload")
    )


@pytest.mark.parametrize("mutation", ["missing", "symlink", "hash", "unreadable"])
def test_root_verifier_rejects_installed_target_drift(
    tmp_path: Path, mutation: str,
) -> None:
    sandbox = _sandbox(tmp_path)
    installed = sandbox.run()
    assert installed.returncode == 0, installed.stderr
    valid = _run_root_control_plane_verifier(sandbox)
    assert valid.returncode == 0, valid.stdout + valid.stderr

    artifact = ARTIFACTS[8]
    target = _installed_path(sandbox.rootfs, artifact)
    if mutation == "missing":
        target.unlink()
    elif mutation == "symlink":
        target.unlink()
        target.symlink_to(sandbox.source / artifact.source)
    elif mutation == "hash":
        target.write_text("drifted\n", encoding="utf-8")
    else:
        target.chmod(0o000)

    rejected = _run_root_control_plane_verifier(sandbox)
    assert rejected.returncode != 0
    assert "control-plane target" in rejected.stdout + rejected.stderr


def test_daemon_reload_failure_restores_bundle_but_withdraws_readiness(
    tmp_path: Path,
) -> None:
    sandbox = _sandbox(tmp_path)
    snapshot = _seed_bundle(sandbox)

    result = sandbox.run(RADON_BOOTSTRAP_TEST_FAIL_RELOAD="1")

    assert result.returncode != 0
    assert "daemon reload failed" in result.stderr.lower()
    snapshot.pop(_ready_path(sandbox.rootfs))
    _assert_snapshot(snapshot)
    assert not _ready_path(sandbox.rootfs).exists()
    commands = sandbox.command_log.read_text(encoding="utf-8").splitlines()
    assert [line for line in commands if line.startswith("systemctl\t")] == [
        "systemctl\tdaemon-reload"
    ]


def test_current_bundle_rerun_is_a_noop(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path)
    first = sandbox.run()
    assert first.returncode == 0, first.stderr
    tracked = [
        *(_installed_path(sandbox.rootfs, artifact) for artifact in ARTIFACTS),
        _manifest_path(sandbox.rootfs),
        _ready_path(sandbox.rootfs),
    ]
    before = {
        path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
        for path in tracked
    }
    sandbox.command_log.unlink()

    second = sandbox.run()

    assert second.returncode == 0, second.stderr
    assert "already current" in second.stdout.lower()
    assert {
        path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
        for path in tracked
    } == before
    commands = (
        sandbox.command_log.read_text(encoding="utf-8")
        if sandbox.command_log.exists()
        else ""
    )
    assert "systemctl\t" not in commands
