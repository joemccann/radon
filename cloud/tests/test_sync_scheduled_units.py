"""Secure CI publish path for allowlisted non-control-plane systemd units.

A green push must not give `radon` a shell into `/etc/systemd/system`.
`radon-deploy-root sync-scheduled-units` is the only write: exact sudoers
verb, git objects at the GitHub main tip (not the live checkout), manifest
hash match, regular-file install, daemon-reload only.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import re
import shlex
import subprocess

import pytest


CLOUD_ROOT = pathlib.Path(__file__).resolve().parent.parent
ROOT_HELPER = CLOUD_ROOT / "scripts" / "deploy-root-helper.sh"
DEPLOY = CLOUD_ROOT / "scripts" / "deploy.sh"
SUDOERS = CLOUD_ROOT / "config" / "sudoers.d" / "radon-deploy"
ALLOWLIST = CLOUD_ROOT / "config" / "auto-sync-units.txt"
MANIFEST = CLOUD_ROOT / "config" / "installed-units.sha256"
SERVICES_DIR = CLOUD_ROOT / "services"

CONTROL_PLANE_UNITS = {
    "radon-health.service",
    "radon-ib-gateway-preheld-restart.service",
    "radon-ib-watchdog.service",
    "radon-ib-watchdog.timer",
    "radon-ib-gateway.service",
    "radon-api.service",
    "radon-monitor.service",
    "radon-relay.service",
    "radon-portfolio-sync.service",
    "radon-portfolio-sync.timer",
    "radon-refresh.service",
    "radon-refresh.timer",
    "radon-db-backup.service",
    "radon-db-backup.timer",
    "radon-disk-cleanup.service",
    "radon-disk-cleanup.timer",
    "radon-drift-audit.service",
    "radon-drift-audit.timer",
    "radon-nextjs-db-watchdog.service",
    "radon-nextjs-db-watchdog.timer",
}


def function_body(script: str, name: str) -> str:
    match = re.search(
        rf"^{name}\(\)\s*\{{\s*\n(.+?)\n\}}\s*$",
        script,
        re.MULTILINE | re.DOTALL,
    )
    assert match, f"{name}() missing"
    return match.group(1)


def _write_executable(path: pathlib.Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _sha256_text(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()


def _init_release_repo(
    tmp_path: pathlib.Path,
    *,
    units: dict[str, str],
    allowlist: str,
    manifest: str | None = None,
) -> tuple[pathlib.Path, str]:
    repo = tmp_path / "release"
    services = repo / "cloud" / "services"
    config = repo / "cloud" / "config"
    services.mkdir(parents=True)
    config.mkdir(parents=True)
    for name, body in units.items():
        (services / name).write_text(body, encoding="utf-8")
    (config / "auto-sync-units.txt").write_text(allowlist, encoding="utf-8")
    if manifest is None:
        manifest = (
            "\n".join(
                f"{_sha256_text(body)}  {name}" for name, body in units.items()
            )
            + "\n"
        )
    (config / "installed-units.sha256").write_text(manifest, encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "unit-sync@test"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "unit-sync"],
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "release"],
        check=True,
        capture_output=True,
    )
    sha = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    return repo, sha


def _helper_env(
    tmp_path: pathlib.Path,
    repo: pathlib.Path,
    systemd_dir: pathlib.Path,
    *,
    remote: pathlib.Path | str | None = None,
    extra: dict[str, str] | None = None,
) -> tuple[dict[str, str], pathlib.Path]:
    systemctl_log = tmp_path / "systemctl.log"
    fake_systemctl = tmp_path / "systemctl"
    _write_executable(
        fake_systemctl,
        f"""#!/bin/bash
printf '%s\\n' "$*" >> {shlex.quote(str(systemctl_log))}
if [[ "${{1:-}}" == "list-jobs" ]]; then
  printf '%s\\n' "4242 radon-api.service start running"
fi
exit 0
""",
    )
    fake_sync = tmp_path / "sync"
    _write_executable(fake_sync, "#!/bin/bash\nexit 0\n")
    fake_rm = tmp_path / "rm"
    _write_executable(fake_rm, '#!/bin/bash\nexec /bin/rm "$@"\n')
    systemd_dir.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "RADON_DEPLOY_HELPER_TEST_MODE": "1",
        "RADON_TEST_SYSTEMCTL": str(fake_systemctl),
        "RADON_TEST_RM": str(fake_rm),
        "RADON_TEST_SYNC": str(fake_sync),
        "RADON_TEST_ACTIVE_STATE_FILE": str(tmp_path / "active-units"),
        "RADON_TEST_REPLICA_PREFIX": str(tmp_path / "replica.db"),
        "RADON_TEST_GIT_DIR": str(repo / ".git"),
        "RADON_TEST_UNIT_REMOTE": str(remote if remote is not None else repo),
        "RADON_TEST_SYSTEMD_DIR": str(systemd_dir),
    }
    if extra:
        env.update(extra)
    return env, systemctl_log


def _run_sync(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(ROOT_HELPER), "sync-scheduled-units"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


# R-188: the shape checks below validate each ENTRY; nothing bounded the
# LIST. Adding a unit here grants CI the right to publish it into
# /etc/systemd/system with no root bootstrap, which is a decision, not a
# detail — so membership is pinned in cloud/tests/test_control_plane_bounds.py
# (EXPECTED_AUTO_SYNC_UNITS) and growing the list means editing that ratchet.
def test_allowlist_names_are_hashed_non_control_plane_units():
    assert ALLOWLIST.is_file()
    names = []
    for line in ALLOWLIST.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            names.append(line)
    assert names, "auto-sync allowlist must name at least one unit"
    manifest = {
        name: digest
        for digest, name in (
            line.split("  ", 1)
            for line in MANIFEST.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#") and "  " in line
        )
    }
    for name in names:
        assert re.fullmatch(r"radon-[a-z0-9][a-z0-9.-]*\.(service|timer)", name), name
        assert name not in CONTROL_PLANE_UNITS, name
        assert (SERVICES_DIR / name).is_file(), name
        assert name in manifest, name
        actual = hashlib.sha256((SERVICES_DIR / name).read_bytes()).hexdigest()
        assert manifest[name] == actual, name


def test_sudoers_and_helper_pin_the_sync_verb():
    helper = ROOT_HELPER.read_text(encoding="utf-8")
    sudoers = SUDOERS.read_text(encoding="utf-8")
    assert "sync-scheduled-units" in helper
    assert "/usr/local/sbin/radon-deploy-root sync-scheduled-units" in sudoers
    assert "radon-*" not in sudoers
    assert "/usr/bin/systemctl" not in sudoers
    assert "/usr/bin/install" not in sudoers
    assert "/etc/systemd/system" not in sudoers
    assert "eval " not in helper


def test_deploy_syncs_after_green_gate_and_skips_when_ungranted():
    deploy = DEPLOY.read_text(encoding="utf-8")
    main = function_body(deploy, "main")
    recover = function_body(deploy, "recover_pending_transition")
    grant = function_body(deploy, "scheduled_units_sync_is_granted")
    sync = function_body(deploy, "sync_scheduled_units")
    assert main.count("sync_scheduled_units") == 2
    assert main.index("green_marker_matches") < main.index("sync_scheduled_units")
    assert main.index("sync_scheduled_units") < main.index("write_green_marker")
    assert main.index("commit-transition") < main.rindex("sync_scheduled_units")
    assert "sync_scheduled_units" in recover
    assert "sync_scheduled_units || return 1" not in recover
    assert "sudo -n -l --" in grant
    assert "bootstrap-control-plane.sh" in sync
    assert "return 0" in sync


def test_sync_installs_allowlisted_unit_and_reloads_only(tmp_path: pathlib.Path):
    unit = "radon-signals-refresh.service"
    body = "[Service]\nType=oneshot\n"
    repo, _sha = _init_release_repo(
        tmp_path, units={unit: body}, allowlist=f"{unit}\n"
    )
    systemd_dir = tmp_path / "systemd"
    env, systemctl_log = _helper_env(tmp_path, repo, systemd_dir)

    result = _run_sync(env)

    assert result.returncode == 0, result.stdout + result.stderr
    dest = systemd_dir / unit
    assert dest.read_text(encoding="utf-8") == body
    assert dest.stat().st_mode & 0o777 == 0o644
    commands = systemctl_log.read_text(encoding="utf-8").splitlines()
    assert commands == ["daemon-reload"]
    assert not any(
        token in " ".join(commands)
        for token in ("start", "stop", "restart", "enable", "disable", "cancel")
    )


def test_sync_is_idempotent_when_live_already_matches(tmp_path: pathlib.Path):
    unit = "radon-signals-refresh.service"
    body = "[Service]\nType=oneshot\n"
    repo, _sha = _init_release_repo(
        tmp_path, units={unit: body}, allowlist=f"{unit}\n"
    )
    systemd_dir = tmp_path / "systemd"
    dest = systemd_dir / unit
    dest.parent.mkdir(parents=True)
    dest.write_text(body, encoding="utf-8")
    env, systemctl_log = _helper_env(tmp_path, repo, systemd_dir)

    result = _run_sync(env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert dest.read_text(encoding="utf-8") == body
    assert not systemctl_log.exists() or "daemon-reload" not in systemctl_log.read_text(
        encoding="utf-8"
    )


def test_sync_refuses_when_head_is_not_github_main(tmp_path: pathlib.Path):
    unit = "radon-signals-refresh.service"
    body = "[Service]\nType=oneshot\n"
    repo, _sha = _init_release_repo(
        tmp_path, units={unit: body}, allowlist=f"{unit}\n"
    )
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "clone", "--bare", str(repo), str(remote)],
        check=True,
        capture_output=True,
    )
    (repo / "cloud" / "services" / unit).write_text("[Service]\nEvil=1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "local only"],
        check=True,
        capture_output=True,
    )
    systemd_dir = tmp_path / "systemd"
    env, _log = _helper_env(tmp_path, repo, systemd_dir, remote=remote)

    result = _run_sync(env)

    assert result.returncode != 0
    assert not (systemd_dir / unit).exists()


def test_sync_refuses_a_control_plane_unit(tmp_path: pathlib.Path):
    unit = "radon-api.service"
    body = "[Service]\nType=simple\n"
    repo, _sha = _init_release_repo(
        tmp_path, units={unit: body}, allowlist=f"{unit}\n"
    )
    systemd_dir = tmp_path / "systemd"
    env, systemctl_log = _helper_env(tmp_path, repo, systemd_dir)

    result = _run_sync(env)

    assert result.returncode != 0
    assert not (systemd_dir / unit).exists()
    assert not systemctl_log.exists() or "daemon-reload" not in systemctl_log.read_text(
        encoding="utf-8"
    )


def test_sync_refuses_manifest_mismatch(tmp_path: pathlib.Path):
    unit = "radon-signals-refresh.service"
    body = "[Service]\nType=oneshot\n"
    repo, _sha = _init_release_repo(
        tmp_path,
        units={unit: body},
        allowlist=f"{unit}\n",
        manifest=f"{'a' * 64}  {unit}\n",
    )
    systemd_dir = tmp_path / "systemd"
    env, _log = _helper_env(tmp_path, repo, systemd_dir)

    result = _run_sync(env)

    assert result.returncode != 0
    assert not (systemd_dir / unit).exists()


def test_sync_refuses_path_tokens_in_the_allowlist(tmp_path: pathlib.Path):
    unit = "radon-signals-refresh.service"
    body = "[Service]\nType=oneshot\n"
    repo, _sha = _init_release_repo(
        tmp_path,
        units={unit: body},
        allowlist="../shadow\nradon-x.service/../../etc/passwd\n",
    )
    systemd_dir = tmp_path / "systemd"
    env, _log = _helper_env(tmp_path, repo, systemd_dir)

    result = _run_sync(env)

    assert result.returncode != 0
    assert list(systemd_dir.glob("*")) == []


def test_sync_refuses_a_symlinked_live_unit(tmp_path: pathlib.Path):
    unit = "radon-signals-refresh.service"
    body = "[Service]\nType=oneshot\n"
    repo, _sha = _init_release_repo(
        tmp_path, units={unit: body}, allowlist=f"{unit}\n"
    )
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir(parents=True)
    secret = tmp_path / "shadow"
    secret.write_text("root:$6$keep\n", encoding="utf-8")
    dest = systemd_dir / unit
    dest.symlink_to(secret)
    env, systemctl_log = _helper_env(tmp_path, repo, systemd_dir)

    result = _run_sync(env)

    assert result.returncode != 0
    assert dest.is_symlink()
    assert secret.read_text(encoding="utf-8") == "root:$6$keep\n"
    assert not systemctl_log.exists() or "daemon-reload" not in systemctl_log.read_text(
        encoding="utf-8"
    )


def test_sync_refuses_a_non_github_remote_when_forced(tmp_path: pathlib.Path):
    unit = "radon-signals-refresh.service"
    body = "[Service]\nType=oneshot\n"
    repo, _sha = _init_release_repo(
        tmp_path, units={unit: body}, allowlist=f"{unit}\n"
    )
    systemd_dir = tmp_path / "systemd"
    env, _log = _helper_env(
        tmp_path,
        repo,
        systemd_dir,
        remote="https://evil.example/radon.git",
        extra={"RADON_TEST_FORCE_GITHUB_REMOTE_CHECK": "1"},
    )

    result = _run_sync(env)

    assert result.returncode != 0
    assert not (systemd_dir / unit).exists()


def test_rejected_sync_does_not_cancel_queued_radon_jobs(tmp_path: pathlib.Path):
    unit = "radon-signals-refresh.service"
    repo, _sha = _init_release_repo(
        tmp_path,
        units={unit: "[Service]\nType=oneshot\n"},
        allowlist=f"{unit}\n",
        manifest=f"{'b' * 64}  {unit}\n",
    )
    systemd_dir = tmp_path / "systemd"
    env, systemctl_log = _helper_env(tmp_path, repo, systemd_dir)

    result = _run_sync(env)

    assert result.returncode != 0
    commands = systemctl_log.read_text(encoding="utf-8") if systemctl_log.exists() else ""
    assert "cancel" not in commands


def test_ungranted_sync_does_not_fail_deploy(tmp_path: pathlib.Path):
    calls = tmp_path / "sudo.log"
    shell = f"""
set -euo pipefail
source {DEPLOY}
log_warn() {{ :; }}
log_error() {{ :; }}
log_success() {{ :; }}
sudo() {{
  printf '%s\\n' "$*" >> {shlex.quote(str(calls))}
  return 1
}}
sync_scheduled_units
"""
    result = subprocess.run(
        ["bash", "-c", shell],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls.read_text(encoding="utf-8").splitlines() == [
        f"-n -l -- /usr/local/sbin/radon-deploy-root sync-scheduled-units"
    ]


def test_granted_sync_failure_fails_deploy(tmp_path: pathlib.Path):
    calls = tmp_path / "sudo.log"
    shell = f"""
set -euo pipefail
source {DEPLOY}
log_warn() {{ :; }}
log_error() {{ :; }}
log_success() {{ :; }}
sudo() {{
  printf '%s\\n' "$*" >> {shlex.quote(str(calls))}
  [[ "$*" == "-n -l -- /usr/local/sbin/radon-deploy-root sync-scheduled-units" ]] && return 0
  return 17
}}
sync_scheduled_units
"""
    result = subprocess.run(
        ["bash", "-c", shell],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "-n -l -- /usr/local/sbin/radon-deploy-root sync-scheduled-units",
        "/usr/local/sbin/radon-deploy-root sync-scheduled-units",
    ]
