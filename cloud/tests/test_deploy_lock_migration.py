"""Deploy serialization remains shared while installed consumers transition."""
from __future__ import annotations

import fcntl
import importlib.util
from pathlib import Path

import pytest


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
