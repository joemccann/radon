"""Root deploy state lives under a parent only root can rename.

The helper journals topology snapshots, unit backups and the install-units
transaction in its state directory and acts on them as root. A parent the
radon account owns lets radon rename that directory and substitute its own,
so every root write and read there must sit under a root-owned parent, and
the helper must refuse a state directory that is a link or not its own.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from _bash_toolchain import MODERN_BASH, requires_modern_bash


CLOUD_ROOT = Path(__file__).resolve().parent.parent
ROOT_HELPER = CLOUD_ROOT / "scripts" / "deploy-root-helper.sh"
BOOTSTRAP = CLOUD_ROOT / "scripts" / "bootstrap-control-plane.sh"
SETUP = CLOUD_ROOT / "scripts" / "setup-vps.sh"

# Parents setup-vps.sh hands to the radon account.
RADON_OWNED_PARENTS = ("/var/lib/radon", "/home/radon", "/etc/radon")


def _production_state_dir() -> str:
    helper = ROOT_HELPER.read_text(encoding="utf-8")
    match = re.search(r"^\s*readonly STATE_DIR=(/\S+)$", helper, re.MULTILINE)
    assert match, "production STATE_DIR not found"
    return match.group(1)


def test_production_state_dir_is_outside_every_radon_owned_parent() -> None:
    state_dir = _production_state_dir()
    for parent in RADON_OWNED_PARENTS:
        assert state_dir != parent and not state_dir.startswith(parent + "/"), state_dir


def test_radon_owned_parents_are_still_radon_owned_in_setup() -> None:
    setup = SETUP.read_text(encoding="utf-8")
    assert 'state_dir="${RADON_STATE_DIR:-/var/lib/radon}"' in setup
    assert 'install -d -m 0750 -o radon -g radon "$state_dir"' in setup


def test_bootstrap_checks_the_helper_state_dir_for_pending_transitions() -> None:
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
    state_dir = _production_state_dir()
    assert f'ROOT_ACTIVE_UNITS_PATH="$(root_path {state_dir}/active-units)"' in bootstrap


def _helper_env(state_file: Path) -> dict[str, str]:
    true_bin = shutil.which("true")
    rm_bin = shutil.which("rm")
    assert true_bin and rm_bin
    return {
        **os.environ,
        "RADON_DEPLOY_HELPER_TEST_MODE": "1",
        "RADON_TEST_SYSTEMCTL": true_bin,
        "RADON_TEST_RM": rm_bin,
        "RADON_TEST_SYNC": true_bin,
        "RADON_TEST_FLOCK": true_bin,
        "RADON_TEST_SESSION_PYTHON": sys.executable,
        "RADON_TEST_ACTIVE_STATE_FILE": str(state_file),
        "RADON_TEST_ROOT_LOCK_FILE": str(state_file.parent.parent / "root.lock"),
        "RADON_TEST_REPLICA_PREFIX": str(state_file.parent.parent / "replica.db"),
    }


@requires_modern_bash
def test_helper_refuses_a_linked_state_dir(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    state_dir = tmp_path / "deploy"
    state_dir.symlink_to(elsewhere, target_is_directory=True)

    result = subprocess.run(
        [str(MODERN_BASH), str(ROOT_HELPER), "verify-restored"],
        env=_helper_env(state_dir / "active-units"),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 77, result.stderr
    assert "state directory" in result.stderr
    assert list(elsewhere.iterdir()) == []


@requires_modern_bash
def test_helper_accepts_its_own_state_dir(tmp_path: Path) -> None:
    state_dir = tmp_path / "deploy"

    result = subprocess.run(
        [str(MODERN_BASH), str(ROOT_HELPER), "verify-restored"],
        env=_helper_env(state_dir / "active-units"),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert state_dir.is_dir() and not state_dir.is_symlink()


def _verify_restored(state_dir: Path, legacy: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(MODERN_BASH), str(ROOT_HELPER), "verify-restored"],
        env={**_helper_env(state_dir / "active-units"), "RADON_TEST_LEGACY_STATE_DIR": str(legacy)},
        capture_output=True,
        text=True,
    )


@requires_modern_bash
def test_helper_moves_a_legacy_transition_into_its_own_state_dir(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    legacy.mkdir(mode=0o700)
    (legacy / "active-units").write_text("radon-api.service\n", encoding="utf-8")
    (legacy / "unit-backup.radon-api.service").write_text("[Unit]\n", encoding="utf-8")
    (legacy / "unrelated").write_text("x\n", encoding="utf-8")
    state_dir = tmp_path / "deploy"

    _verify_restored(state_dir, legacy)

    assert (state_dir / "active-units").read_text(encoding="utf-8") == "radon-api.service\n"
    assert (state_dir / "unit-backup.radon-api.service").is_file()
    assert sorted(path.name for path in legacy.iterdir()) == ["unrelated"]
    assert not (state_dir / "unrelated").exists()


@requires_modern_bash
def test_helper_never_reads_a_linked_or_writable_legacy_dir(tmp_path: Path) -> None:
    planted = tmp_path / "planted"
    planted.mkdir(mode=0o700)
    (planted / "unit-backup.radon-api.service").write_text("[Unit]\n", encoding="utf-8")
    linked = tmp_path / "linked"
    linked.symlink_to(planted, target_is_directory=True)
    writable = tmp_path / "writable"
    writable.mkdir()
    writable.chmod(0o777)
    (writable / "unit-backup.radon-api.service").write_text("[Unit]\n", encoding="utf-8")

    for legacy in (linked, writable):
        state_dir = tmp_path / f"deploy-{legacy.name}"
        result = _verify_restored(state_dir, legacy)
        assert result.returncode == 0, result.stderr
        assert list(state_dir.iterdir()) == []
    assert (planted / "unit-backup.radon-api.service").is_file()


@requires_modern_bash
def test_helper_never_lets_legacy_state_overwrite_current_state(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    legacy.mkdir(mode=0o700)
    (legacy / "active-units").write_text("old\n", encoding="utf-8")
    state_dir = tmp_path / "deploy"
    state_dir.mkdir()
    (state_dir / "active-units").write_text("current\n", encoding="utf-8")

    _verify_restored(state_dir, legacy)

    assert (state_dir / "active-units").read_text(encoding="utf-8") == "current\n"
    assert (legacy / "active-units").is_file()
