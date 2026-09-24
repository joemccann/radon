"""TCC keys a bare Mach-O's privacy grants by file path, and the native Claude
Code installer gives every release a new path (~/.local/share/claude/versions/
<version>). Full Disk Access therefore grew one row per CLI version, and the
nightly loops prompted again after every update. The loops now run a fixed-path,
signature-checked copy that scripts/claude_stable_sync.sh keeps current."""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SYNC = REPO / "scripts" / "claude_stable_sync.sh"
STABLE = "__HOME__/.local/share/radon/claude-stable"
LOOP_PLISTS = [
    "com.radon.reliability-daily.plist",
    "com.radon.testing-daily.plist",
    "com.radon.ci-performance-daily.plist",
    "com.radon.documentation-daily.plist",
    "com.radon.security-daily.plist",
    "com.radon.security-deepsec.plist",
]


def _plist(name: str) -> dict:
    return plistlib.loads((REPO / "config" / name).read_bytes())


@pytest.mark.parametrize("name", LOOP_PLISTS)
def test_loop_plists_resolve_claude_from_the_stable_path_first(name: str) -> None:
    path = _plist(name)["EnvironmentVariables"]["PATH"].split(":")
    assert STABLE in path
    # ~/.local/bin stays as the fallback for a host that has not run setup yet.
    assert path.index(STABLE) < path.index("__HOME__/.local/bin")


def test_sync_agent_runs_an_installed_copy_outside_every_runner_clone() -> None:
    plist = _plist("com.radon.claude-stable-sync.plist")
    script = plist["ProgramArguments"][-1]
    assert script == f"{STABLE}/sync.sh"
    assert "radon-weekend" not in script
    assert plist["RunAtLoad"] is True
    assert "__HOME__/.local/share/claude/versions" in plist["WatchPaths"]


def _run(tmp_path: Path, source: Path) -> subprocess.CompletedProcess:
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "RADON_CLAUDE_SOURCE": str(source),
        "RADON_CLAUDE_STABLE_DIR": str(tmp_path / "stable"),
    }
    return subprocess.run(["/bin/bash", str(SYNC)], env=env, capture_output=True, text=True, check=False)


@pytest.mark.skipif(sys.platform != "darwin", reason="codesign is macOS-only")
def test_unsigned_binary_is_refused_and_never_copied(tmp_path: Path) -> None:
    fake = tmp_path / "fake"
    fake.write_text("#!/bin/sh\necho planted\n")
    fake.chmod(0o755)
    link = tmp_path / "claude"
    link.symlink_to(fake)

    result = _run(tmp_path, link)

    assert result.returncode == 1
    assert "REFUSED" in result.stdout
    assert not (tmp_path / "stable" / "claude").exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="codesign is macOS-only")
def test_apple_signed_non_claude_binary_is_refused(tmp_path: Path) -> None:
    link = tmp_path / "claude"
    link.symlink_to("/bin/ls")

    result = _run(tmp_path, link)

    assert result.returncode == 1
    assert not (tmp_path / "stable" / "claude").exists()


def _installed_claude() -> Path | None:
    link = Path.home() / ".local" / "bin" / "claude"
    return link if sys.platform == "darwin" and link.exists() else None


@pytest.mark.skipif(_installed_claude() is None, reason="needs the native Claude Code install")
def test_installed_claude_is_copied_once_as_a_separate_file(tmp_path: Path) -> None:
    link = _installed_claude()
    assert link is not None

    first = _run(tmp_path, link)
    dest = tmp_path / "stable" / "claude"
    assert first.returncode == 0, first.stdout + first.stderr
    assert "UPDATED" in first.stdout
    # A hard link would let proc_pidpath() report the versioned name to TCC.
    assert os.stat(dest).st_ino != os.stat(os.path.realpath(link)).st_ino
    assert shutil.which("claude", path=str(dest.parent)) == str(dest)

    second = _run(tmp_path, link)
    assert second.returncode == 0
    assert second.stdout == ""
