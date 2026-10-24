"""Deploy serialization remains shared while installed consumers transition."""
from __future__ import annotations

import fcntl
import importlib.util
from pathlib import Path

import pytest

from _bash_toolchain import requires_flock


ROOT = Path(__file__).resolve().parents[2]
CANONICAL = "/var/lib/radon-lock/deploy.lock"
LEGACY = "/home/radon/.radon-deploy.lock"


@pytest.mark.parametrize("relative", [
    "cloud/scripts/deploy.sh",
    "cloud/scripts/bootstrap-control-plane.sh",
    "cloud/scripts/operator-radon.sh",
    "cloud/scripts/ib-gateway-control.sh",
    "cloud/scripts/radon-app-runtime.sh",
    "cloud/scripts/disk_cleanup.py",
    "scripts/api/services.py",
    ".github/workflows/ci.yml",
])
def test_consumers_name_the_provisioned_shared_lock(relative):
    source = (ROOT / relative).read_text()
    assert CANONICAL in source


def _cleanup():
    path = ROOT / "cloud/scripts/disk_cleanup.py"
    spec = importlib.util.spec_from_file_location("migration_cleanup", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("held_name", ["canonical", "legacy"])
def test_cleanup_defers_to_either_generation(tmp_path, held_name):
    cleanup = _cleanup()
    paths = {name: tmp_path / name for name in ("canonical", "legacy")}
    for path in paths.values():
        path.touch()
    with paths[held_name].open() as holder:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with cleanup.acquire_deploy_lock(
            path=paths["canonical"], legacy_path=paths["legacy"], timeout_s=0,
        ) as admitted:
            assert admitted is False
    with cleanup.acquire_deploy_lock(
        path=paths["canonical"], legacy_path=paths["legacy"], timeout_s=0,
    ) as admitted:
        assert admitted is True


def test_bootstrap_preserves_the_compatibility_lock_inode():
    source = (ROOT / "cloud/scripts/bootstrap-control-plane.sh").read_text()
    assert CANONICAL in source and LEGACY in source
    assert "LEGACY_DEPLOY_LOCK" in source


def test_boot_provisions_lock_before_consumers():
    config = ROOT / "cloud/config/tmpfiles.d/radon-deploy.conf"
    assert config.is_file()
    rows = config.read_text().splitlines()
    assert any(row.startswith("d /var/lib/radon-lock 0750 root radon") for row in rows)
    assert any(row.startswith("f /var/lib/radon-lock/deploy.lock 0640 root radon") for row in rows)


def test_cleanup_refuses_fifo_without_waiting_for_writer(tmp_path):
    import os
    cleanup = _cleanup()
    primary, legacy = tmp_path / "primary", tmp_path / "legacy"
    primary.touch()
    os.mkfifo(legacy)
    with cleanup.acquire_deploy_lock(path=primary, legacy_path=legacy, timeout_s=0) as admitted:
        assert admitted is False
    with primary.open() as holder:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)


@pytest.mark.parametrize("relative", [
    "cloud/scripts/bootstrap-control-plane.sh", "cloud/scripts/setup-vps.sh",
])
def test_provisioning_keeps_existing_primary_and_legacy_inodes(tmp_path, relative):
    import subprocess
    import sys
    source = (ROOT / relative).read_text()
    payload = source.split("<<'PY_LOCKS'", 1)[1].split("\n", 1)[1].split("\nPY_LOCKS", 1)[0]
    primary = tmp_path / "protected" / "deploy.lock"
    legacy = tmp_path / "home" / "legacy.lock"
    for _ in range(2):
        result = subprocess.run([sys.executable, "-I", "-", str(primary), str(legacy), "1"],
                                input=payload, text=True, capture_output=True, timeout=5)
        assert result.returncode == 0, result.stderr
        inodes = (primary.stat().st_ino, legacy.stat().st_ino)
        if _ == 0:
            initial = inodes
        assert inodes == initial
    assert primary.stat().st_mode & 0o777 == 0o640
    assert primary.parent.stat().st_mode & 0o777 == 0o750
    assert legacy.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("unsafe", ["symlink", "writable-directory", "writable-file"])
def test_provisioning_refuses_unsafe_existing_primary(tmp_path, unsafe):
    import subprocess
    import sys
    source = (ROOT / "cloud/scripts/bootstrap-control-plane.sh").read_text()
    payload = source.split("<<'PY_LOCKS'", 1)[1].split("\n", 1)[1].split("\nPY_LOCKS", 1)[0]
    primary = tmp_path / "protected" / "deploy.lock"
    primary.parent.mkdir(mode=0o750)
    elsewhere = tmp_path / "untouched"
    elsewhere.write_text("keep")
    if unsafe == "symlink":
        primary.symlink_to(elsewhere)
    else:
        primary.touch(mode=0o640)
        (primary.parent if unsafe == "writable-directory" else primary).chmod(0o777)
    result = subprocess.run([sys.executable, "-I", "-", str(primary), str(tmp_path / "legacy"), "1"],
                            input=payload, text=True, capture_output=True, timeout=5)
    assert result.returncode != 0
    assert elsewhere.read_text() == "keep"


@requires_flock
@pytest.mark.parametrize("recover_only", [False, True])
def test_first_upgrade_only_recovery_may_use_legacy_alone(tmp_path, recover_only):
    import os
    import subprocess
    import sys
    primary, legacy, marker = tmp_path / "primary", tmp_path / "legacy", tmp_path / "ran"
    legacy.touch(mode=0o600)
    script = ROOT / "cloud/scripts/deploy.sh"
    shell = f'''set -euo pipefail
source "{script}"
supervise_deploy_command sh -c 'echo ran > "{marker}"'
'''
    result = subprocess.run(["bash", "-c", shell], env={**os.environ,
        "RADON_DEPLOY_SOURCE_ONLY": "1", "RADON_DEPLOY_LOCK_FILE": str(primary),
        "RADON_DEPLOY_LEGACY_LOCK_FILE": str(legacy), "RADON_SYSTEM_PYTHON": sys.executable,
        "RADON_DEPLOY_RECOVER_ONLY": "1" if recover_only else "0",
        "RADON_DEPLOY_TRANSITION_JOURNAL": str(tmp_path / "absent-transition"),
    }, text=True, capture_output=True, timeout=5)
    assert result.returncode == 0 if recover_only else result.returncode != 0
    assert not marker.exists()
    if recover_only:
        assert "Recovery pass complete" in result.stdout
    assert not primary.exists()
    with legacy.open() as holder:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_installed_mode_contract_uses_stat_canonical_strings():
    import re
    source = (ROOT / "cloud/scripts/deploy-root-helper.sh").read_text()
    declaration = source.split("readonly -a CONTROL_PLANE_MODES=(", 1)[1].split(")", 1)[0]
    modes = re.findall(r"\b[0-7]+\b", declaration)
    assert modes
    assert all(mode == format(int(mode, 8), "o") for mode in modes)


@pytest.mark.parametrize("relative, variable", [
    ("cloud/scripts/deploy.sh", "LEGACY_DEPLOY_LOCK_FILE"),
    ("cloud/scripts/operator-radon.sh", "LEGACY_DEPLOY_LOCK_PATH"),
    ("cloud/scripts/ib-gateway-control.sh", "LEGACY_DEPLOY_LOCK_PATH"),
])
@pytest.mark.parametrize("primary, override, expected", [
    (None, None, LEGACY),
    ("", None, LEGACY),
    (CANONICAL, None, LEGACY),
    ("/isolated/deploy.lock", None, ""),
    (CANONICAL, "/isolated/compatibility.lock", "/isolated/compatibility.lock"),
])
def test_selector_preserves_canonical_compatibility(relative, variable, primary, override, expected):
    """Exercise only assignment logic; never execute a deploy or Gateway helper."""
    import os
    import re
    import subprocess
    source = (ROOT / relative).read_text()
    selector = re.search(
        r'^if \[\[ -n "\$\{RADON_DEPLOY_LOCK_FILE:-\}".*?^fi$',
        source, re.MULTILINE | re.DOTALL,
    )
    assert selector, "The isolated custom-path selector must remain identifiable"
    env = {key: value for key, value in os.environ.items()
           if key not in {"RADON_DEPLOY_LOCK_FILE", "RADON_DEPLOY_LEGACY_LOCK_FILE"}}
    if primary is not None:
        env["RADON_DEPLOY_LOCK_FILE"] = primary
    if override is not None:
        env["RADON_DEPLOY_LEGACY_LOCK_FILE"] = override
    result = subprocess.run(
        ["bash", "-c", selector.group() + '\nprintf "%s" "$' + variable + '"'],
        env=env, text=True, capture_output=True, timeout=5, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected


def _probe_deploy_entrypoint(tmp_path, *, state, stage=True, selector="default", python_fault=None, journal_present=True):
    """Execute the outer entrypoint; a child sensor never executes a deploy."""
    import os
    import subprocess
    import sys

    canonical = tmp_path / "canonical-parent/deploy.lock"
    canonical.parent.mkdir(mode=0o750)
    legacy = tmp_path / "legacy.lock"
    legacy.write_text("existing compatibility inode\n")
    legacy.chmod(0o600)
    journal = tmp_path / "transition.json"
    if journal_present:
        journal.write_text("existing transition must remain untouched\n")
    victim = tmp_path / "victim"
    victim.write_text("untouched\n")
    selected = canonical
    if selector.startswith("custom"):
        selected = tmp_path / "custom.lock"
    elif state == "missing-parent":
        canonical.parent.rmdir()
    elif state in {"missing-file", "writable-parent", "writable-authority", "foreign-authority", "foreign-parent-group", "wrong-parent-mode"}:
        if state == "writable-parent":
            canonical.parent.chmod(0o777)
        elif state == "writable-authority":
            tmp_path.chmod(0o777)
        elif state == "wrong-parent-mode":
            canonical.parent.chmod(0o700)
    elif state == "symlink":
        canonical.symlink_to(victim)
    elif state == "dangling-symlink":
        canonical.symlink_to(tmp_path / "absent-victim")
    elif state == "fifo":
        os.mkfifo(canonical)
    elif state == "directory":
        canonical.mkdir()
    else:
        canonical.write_text("existing protected inode\n")
        if state == "symlink-parent":
            real_parent = tmp_path / "real-parent"
            canonical.parent.rename(real_parent)
            canonical.parent.symlink_to(real_parent, target_is_directory=True)
        elif state in {"unreadable-parent", "unreadable-file"}:
            assert os.geteuid() != 0, "permission regression requires the unprivileged cloud runner"
            (canonical.parent if state == "unreadable-parent" else canonical).chmod(0)
        else:
            assert state == "regular"

    # Map fixed production names only in this temporary script copy. An unset
    # override still takes the default/canonical branch; explicit canonical and
    # custom overrides retain the production selector's equality semantics.
    source = (ROOT / "cloud/scripts/deploy.sh").read_text()
    assert CANONICAL in source and LEGACY in source
    # Production authority is UID 0 from filesystem root. Deliberately map
    # that fixed namespace/data pair to this real UID-owned temporary root;
    # no ancestor outside tmp_path is part of the synthetic authority domain.
    # Every directory inside it retains real stat/mode checks. A foreign
    # authority case changes only the expected UID, so the actual stat owner
    # is observably foreign; neither os.stat nor the supervisor is mocked.
    authority_uid = os.getuid() + (1 if state == "foreign-authority" else 0)
    assert tmp_path.stat().st_uid == os.getuid()
    if state in {"missing-parent", "missing-file"}:
        assert tmp_path.stat().st_mode & 0o022 == 0
        if state == "missing-file":
            assert canonical.parent.stat().st_mode & 0o777 == 0o750
            assert canonical.parent.stat().st_gid == os.getgid()
    if state not in {"missing-parent", "symlink-parent"}:
        assert canonical.parent.stat().st_uid == os.getuid()
    copied_source = source.replace(CANONICAL, str(canonical)).replace(LEGACY, str(legacy))
    copied_source = copied_source.replace('authority_root = "/"', f'authority_root = {str(tmp_path)!r}')
    copied_source = copied_source.replace('authority_uid = 0', f'authority_uid = {authority_uid}')
    authority_gid = os.getgid() + (1 if state == "foreign-parent-group" else 0)
    copied_source = copied_source.replace('authority_gid = pwd.getpwnam("radon").pw_gid', f'authority_gid = {authority_gid}')
    script = tmp_path / "deploy.sh"
    script.write_text(copied_source)
    calls = tmp_path / "calls"
    calls.touch()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    timeout = fake_bin / "timeout"
    timeout.write_text('#!/bin/sh\nprintf "child\\n" >> "$PROBE_CALLS"\nexit 97\n')
    timeout.chmod(0o755)
    helper = fake_bin / "deploy-root"
    helper.write_text('#!/bin/sh\nprintf "helper\\n" >> "$PROBE_CALLS"\nexit 98\n')
    helper.chmod(0o755)
    for command in ("sudo", "git", "systemctl"):
        path = fake_bin / command
        path.write_text(helper.read_text())
        path.chmod(0o755)
    python = sys.executable
    if python_fault:
        wrapper = tmp_path / "python-wrapper"
        wrapper.write_text(f'''#!{sys.executable}
import os, sys
if os.environ["PROBE_PYTHON_FAULT"] == "unknown":
    raise SystemExit(74)
if any("os.fstat(9)" in arg for arg in sys.argv[1:]):
    os.unlink(os.environ["PROBE_PRIMARY"])
    os.symlink(os.environ["PROBE_VICTIM"], os.environ["PROBE_PRIMARY"])
os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])
''')
        wrapper.chmod(0o755)
        python = str(wrapper)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "PROBE_CALLS": str(calls), "PROBE_PRIMARY": str(canonical),
        "PROBE_VICTIM": str(victim), "PROBE_PYTHON_FAULT": python_fault or "",
        "RADON_DIR": str(tmp_path / "checkout"),
        "RADON_CLOUD_DIR": str(ROOT / "cloud"),
        "RADON_DEPLOY_INTERNAL": "0", "RADON_DEPLOY_RECOVER_ONLY": "0",
        "RADON_DEPLOY_STAGE": "1" if stage else "0",
        "RADON_DEPLOY_ROOT_HELPER": str(helper), "RADON_APP_RUNTIME_HELPER": str(helper),
        "RADON_SYSTEM_PYTHON": python,
        "RADON_DEPLOY_TRANSITION_JOURNAL": str(journal),
        "RADON_DEPLOY_GREEN_MARKER": str(tmp_path / "green"),
        "RADON_DEPLOY_PRESTAGE": str(tmp_path / "prestage"),
        "RADON_DEPLOY_ENV_FILE": str(tmp_path / "canonical.env"),
        "RADON_MCP_ENV_FILE": str(tmp_path / "mcp.env"),
    }
    if selector == "default":
        env.pop("RADON_DEPLOY_LOCK_FILE", None)
    else:
        env["RADON_DEPLOY_LOCK_FILE"] = str(selected)
    if selector == "custom-with-legacy":
        env["RADON_DEPLOY_LEGACY_LOCK_FILE"] = str(legacy)
    else:
        env.pop("RADON_DEPLOY_LEGACY_LOCK_FILE", None)
    before_legacy = (legacy.stat().st_ino, legacy.read_bytes())
    before_journal = (journal.stat().st_ino, journal.read_bytes()) if journal_present else None
    try:
        result = subprocess.run(["bash", str(script), "a" * 40], env=env,
                                text=True, capture_output=True, timeout=5, check=False)
    finally:
        if state == "writable-authority":
            tmp_path.chmod(0o700)
        if state == "unreadable-parent":
            canonical.parent.chmod(0o750)
        elif state == "unreadable-file":
            canonical.chmod(0o600)
    assert (legacy.stat().st_ino, legacy.read_bytes()) == before_legacy
    if journal_present:
        assert (journal.stat().st_ino, journal.read_bytes()) == before_journal
    else:
        assert not journal.exists()
    assert victim.read_text() == "untouched\n"
    for name in ("green", "prestage", "canonical.env", "mcp.env"):
        assert not (tmp_path / name).exists()
    return result, calls.read_text().splitlines(), selected, canonical


@pytest.mark.parametrize("selector", ["default", "canonical"])
@pytest.mark.parametrize("state", ["missing-parent", "missing-file"])
def test_entrypoint_prestage_defers_unprovisioned_canonical_lock_without_work(tmp_path, selector, state):
    result, calls, selected, _ = _probe_deploy_entrypoint(tmp_path, state=state, selector=selector)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not calls, "prestage deferral must not start a child or privileged helper"
    assert not selected.exists()
    if state == "missing-parent":
        assert not selected.parent.exists()


@pytest.mark.parametrize("selector", ["default", "canonical"])
@pytest.mark.parametrize("state", ["missing-parent", "missing-file"])
def test_entrypoint_normal_deploy_still_requires_provisioned_canonical_lock(tmp_path, selector, state):
    result, calls, selected, _ = _probe_deploy_entrypoint(tmp_path, state=state, stage=False, selector=selector)
    assert result.returncode != 0
    assert not calls
    assert not selected.exists()
    if state == "missing-parent":
        assert not selected.parent.exists()


@pytest.mark.parametrize("state", [
    "symlink", "dangling-symlink", "symlink-parent", "fifo", "directory",
    "unreadable-parent", "unreadable-file",
])
def test_entrypoint_prestage_refuses_unsafe_existing_lock_without_work(tmp_path, state):
    result, calls, _, _ = _probe_deploy_entrypoint(tmp_path, state=state)
    assert result.returncode != 0, result.stdout + result.stderr
    assert not calls


@pytest.mark.parametrize("selector", ["default", "canonical"])
@pytest.mark.parametrize("state", ["writable-parent", "writable-authority", "foreign-authority", "foreign-parent-group", "wrong-parent-mode"])
def test_entrypoint_prestage_refuses_missing_leaf_without_directory_authority(tmp_path, selector, state):
    result, calls, selected, _ = _probe_deploy_entrypoint(tmp_path, state=state, selector=selector)
    assert result.returncode != 0, result.stdout + result.stderr
    assert not calls
    assert not selected.exists()
    assert selected.parent.is_dir(), "unsafe existing authority must not be removed or rebuilt"


@pytest.mark.parametrize("selector", ["default", "canonical"])
@pytest.mark.parametrize("state", ["missing-parent", "missing-file"])
def test_entrypoint_prestage_does_not_treat_unknown_lookup_as_first_bootstrap(tmp_path, selector, state):
    result, calls, selected, _ = _probe_deploy_entrypoint(
        tmp_path, state=state, selector=selector, python_fault="unknown",
    )
    assert result.returncode != 0
    assert not calls
    assert not selected.exists()


@requires_flock
def test_entrypoint_prestage_refuses_inode_replacement_after_open(tmp_path):
    result, calls, _, canonical = _probe_deploy_entrypoint(
        tmp_path, state="regular", python_fault="replace-after-open", journal_present=False,
    )
    assert result.returncode != 0
    assert not calls
    assert canonical.is_symlink(), "the isolated race sensor must actually replace the name"


@requires_flock
@pytest.mark.parametrize("selector", ["custom", "custom-with-legacy"])
def test_entrypoint_custom_lock_override_keeps_existing_creation_policy(tmp_path, selector):
    result, calls, selected, _ = _probe_deploy_entrypoint(
        tmp_path, state="missing-file", selector=selector, journal_present=False,
    )
    if selector == "custom":
        assert result.returncode == 97, result.stdout + result.stderr
        assert calls == ["child"], "custom standalone lock must reach the bounded child sensor"
        assert selected.is_file()
    else:
        assert result.returncode != 0
        assert not calls
        assert not selected.exists()


@requires_flock
def test_entrypoint_ready_canonical_locks_reach_bounded_child_sensor(tmp_path):
    result, calls, _, _ = _probe_deploy_entrypoint(tmp_path, state="regular", journal_present=False)
    assert result.returncode == 97, result.stdout + result.stderr
    assert calls == ["child"], "the fixture must detect real work when both locks are ready"
