"""Vercel fx lives at ~/.local/bin/fx and `fx upgrade` replaces that file, so
the operator re-added fx to Full Disk Access after upgrades, like the
per-version Claude Code rows before claude-stable. The loops now run a
fixed-path, signature-checked copy that scripts/fx_stable_sync.sh keeps current,
and Full Disk Access is granted once to ~/.local/share/radon/fx-stable/fx."""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SYNC = REPO / "scripts" / "fx_stable_sync.sh"
STABLE = "__HOME__/.local/share/radon/fx-stable"
WRAPPERS = [
    "reliability_weekend.sh",
    "testing_weekend.sh",
    "ci_performance_nightly.sh",
    "documentation_nightly.sh",
    "security_nightly.sh",
    "security_deepsec_nightly.sh",
]


def test_sync_agent_runs_an_installed_copy_outside_every_runner_clone() -> None:
    plist = plistlib.loads((REPO / "config" / "com.radon.fx-stable-sync.plist").read_bytes())
    script = plist["ProgramArguments"][-1]
    assert script == f"{STABLE}/sync.sh"
    assert "radon-weekend" not in script
    assert plist["RunAtLoad"] is True
    assert "__HOME__/.local/bin" in plist["WatchPaths"]


def _provider_bin(wrapper: str, home: Path, env_bin: str = "") -> str:
    """Run the wrapper's own provider_bin, extracted verbatim."""
    body = (REPO / "scripts" / wrapper).read_text(encoding="utf-8")
    start = body.index("provider_bin() {")
    fn = body[start : body.index("\n}\n", start) + 3]
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home)}
    if env_bin:
        env["RADON_WEEKEND_FX_BIN"] = env_bin
    return subprocess.run(
        ["/bin/bash", "-c", fn + "provider_bin fx"],
        env=env, capture_output=True, text=True, check=True,
    ).stdout


@pytest.mark.parametrize("wrapper", WRAPPERS)
def test_wrappers_run_the_stable_fx_copy_when_it_exists(wrapper: str, tmp_path: Path) -> None:
    # ~/.local/bin/fx stays the fallback for a host that has not run setup yet.
    assert _provider_bin(wrapper, tmp_path) == f"{tmp_path}/.local/bin/fx"
    stable = tmp_path / ".local" / "share" / "radon" / "fx-stable" / "fx"
    stable.parent.mkdir(parents=True)
    stable.write_text("#!/bin/sh\n")
    stable.chmod(0o755)
    assert _provider_bin(wrapper, tmp_path) == str(stable)
    assert _provider_bin(wrapper, tmp_path, "/x/fx") == "/x/fx"


def _run(tmp_path: Path, source: Path) -> subprocess.CompletedProcess:
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "RADON_FX_SOURCE": str(source),
        "RADON_FX_STABLE_DIR": str(tmp_path / "stable"),
    }
    return subprocess.run(["/bin/bash", str(SYNC)], env=env, capture_output=True, text=True, check=False)


@pytest.mark.skipif(sys.platform != "darwin", reason="codesign is macOS-only")
def test_unsigned_binary_is_refused_and_never_copied(tmp_path: Path) -> None:
    fake = tmp_path / "fx"
    fake.write_text("#!/bin/sh\necho planted\n")
    fake.chmod(0o755)

    result = _run(tmp_path, fake)

    assert result.returncode == 1
    assert "REFUSED" in result.stdout
    assert not (tmp_path / "stable" / "fx").exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="codesign is macOS-only")
def test_apple_signed_non_fx_binary_is_refused(tmp_path: Path) -> None:
    result = _run(tmp_path, Path("/bin/ls"))

    assert result.returncode == 1
    assert not (tmp_path / "stable" / "fx").exists()


def test_missing_source_is_a_clean_skip(tmp_path: Path) -> None:
    result = _run(tmp_path, tmp_path / "absent")

    assert result.returncode == 0
    assert "SKIP" in result.stdout


def _installed_fx() -> Path | None:
    exe = Path.home() / ".local" / "bin" / "fx"
    return exe if sys.platform == "darwin" and exe.exists() else None


@pytest.mark.skipif(_installed_fx() is None, reason="needs the fx install")
def test_installed_fx_is_copied_once_as_a_separate_file(tmp_path: Path) -> None:
    src = _installed_fx()
    assert src is not None

    first = _run(tmp_path, src)
    dest = tmp_path / "stable" / "fx"
    assert first.returncode == 0, first.stdout + first.stderr
    assert "UPDATED" in first.stdout
    # A hard link would let proc_pidpath() report the other name to TCC.
    assert os.stat(dest).st_ino != os.stat(src).st_ino
    assert shutil.which("fx", path=str(dest.parent)) == str(dest)

    second = _run(tmp_path, src)
    assert second.returncode == 0
    assert second.stdout == ""
