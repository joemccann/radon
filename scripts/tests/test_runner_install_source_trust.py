#!/usr/bin/env python3
"""`scripts/runner/install.sh` check_source is the trust boundary for a root
install: root copies LIB_FILES out of the agent-writable clone into
root-owned $PREFIX/lib, so every path it reads must be one only root or the
operator can change.

The directory list was hardcoded as the tree, `scripts` and `scripts/ci`, and
missed `scripts/utils` even though `utils/atomic_io.py` is in LIB_FILES. The
file was checked as a leaf, so a group-writable `scripts/utils` was accepted
and anyone in that group could swap the module between the check and the copy.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALL = REPO / "scripts" / "runner" / "install.sh"


def _lib_files() -> list[str]:
    text = INSTALL.read_text(encoding="utf-8")
    block = re.search(r'LIB_FILES="([^"]+)"', text)
    assert block, "install.sh must still declare LIB_FILES"
    return block.group(1).split()


def _fake_tree(tmp_path: Path) -> Path:
    tree = tmp_path / "radon"
    (tree / "scripts" / "runner" / "loops").mkdir(parents=True)
    shutil.copy2(INSTALL, tree / "scripts" / "runner" / "install.sh")
    (tree / "scripts" / "runner" / "loops" / "security-deepsec.env").write_text(
        "PHASES=audit\n", encoding="utf-8"
    )
    for name in _lib_files():
        target = tree / "scripts" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# fixture\n", encoding="utf-8")
    (tree / "scripts" / "ci").mkdir(parents=True, exist_ok=True)
    return tree


def _run(tree: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(tree / "scripts" / "runner" / "install.sh"), "security-deepsec"],
        env={**os.environ, "RADON_RUNNER_INSTALL_TRUSTED_UID": str(os.getuid())},
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_a_trusted_tree_passes_the_source_check(tmp_path: Path) -> None:
    """Baseline: check_source accepts the tree and the run stops at the root
    requirement, which is the next gate."""
    result = _run(_fake_tree(tmp_path))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "run with sudo" in result.stderr


@pytest.mark.parametrize("subdir", ["scripts", "scripts/ci", "scripts/utils"])
def test_a_group_writable_lib_directory_is_refused(tmp_path: Path, subdir: str) -> None:
    tree = _fake_tree(tmp_path)
    victim = tree / subdir
    victim.chmod(stat.S_IMODE(victim.stat().st_mode) | stat.S_IWGRP)

    result = _run(tree)

    assert result.returncode == 77, result.stdout + result.stderr
    assert "untrusted source path" in result.stderr


def test_every_lib_file_directory_is_covered(tmp_path: Path) -> None:
    """Derived, not hardcoded: a new LIB_FILES subdirectory must be checked
    without anyone remembering to extend a literal list."""
    directories = {
        str(Path(name).parent) for name in _lib_files() if "/" in name
    }
    assert directories, "LIB_FILES must still carry at least one subdirectory"
    for subdir in directories:
        tree = _fake_tree(tmp_path / subdir.replace("/", "_"))
        victim = tree / "scripts" / subdir
        victim.chmod(stat.S_IMODE(victim.stat().st_mode) | stat.S_IWOTH)
        result = _run(tree)
        assert result.returncode == 77, (subdir, result.stdout + result.stderr)


def test_the_source_root_is_resolved_physically() -> None:
    """check_source walks ancestors with `pwd -P`; SRC must be built the same
    way, or validation and the later reads can disagree when an ancestor is a
    symlink."""
    text = INSTALL.read_text(encoding="utf-8")
    src = re.search(r'(?m)^SRC="\$\(cd "\$\(dirname "\$\{BASH_SOURCE\[0\]\}"\)" && (pwd[^)]*)\)"', text)
    assert src, "install.sh must still define SRC from BASH_SOURCE"
    assert src.group(1).strip() == "pwd -P", (
        "SRC uses logical pwd while check_source resolves with pwd -P"
    )
