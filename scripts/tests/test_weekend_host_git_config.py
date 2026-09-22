"""Host-side git in a runner clone never runs repository hooks or fsmonitor.

The sandboxed agent can write the clone's `.git` (it has to, to commit), so
every git command the host runs there, from the launchd pre-reset through the
wrapper's own ground truth, must pin hooks and fsmonitor off.
"""

from __future__ import annotations

import os
import plistlib
import re
import subprocess
from pathlib import Path

import pytest

from test_rel137_weekend_wrapper_survivability import (
    BASH,
    CLAUDE_RUNG_LADDER,
    LOOPS,
    _cloned_wrapper,
    _runner_clone,
    _stub_bin,
)

REPO = Path(__file__).resolve().parents[2]
PLISTS = sorted(
    p
    for p in (REPO / "config").glob("com.radon.*.plist")
    if 'git -C "$C"' in p.read_text(encoding="utf-8")
)
PINNED = ("core.hooksPath=/dev/null", "core.fsmonitor=false")


def _git_recorder(bin_dir: Path, log: Path) -> None:
    git = bin_dir / "git"
    git.write_text(
        "#!/bin/sh\n"
        'n="${GIT_CONFIG_COUNT:-0}"; i=0; line="$1"\n'
        'while [ "$i" -lt "$n" ]; do\n'
        '  eval "k=\\${GIT_CONFIG_KEY_$i:-}; v=\\${GIT_CONFIG_VALUE_$i:-}"\n'
        '  line="$line $k=$v"; i=$((i + 1))\n'
        "done\n"
        f'printf "%s\\n" "$line" >> "{log}"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    git.chmod(0o755)


def _assert_pinned(log: Path) -> None:
    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    assert lines, "no git invocation recorded"
    for line in lines:
        for pin in PINNED:
            assert pin in line.split()[1:], line


def test_every_clone_plist_is_covered():
    assert len(PLISTS) >= 6, PLISTS


@pytest.mark.parametrize("plist", PLISTS, ids=lambda p: p.stem)
def test_launchd_pre_reset_pins_hooks_off(plist, tmp_path):
    program = plistlib.loads(plist.read_bytes())["ProgramArguments"]
    assert program[:2] == ["/bin/bash", "-c"], program
    clone = tmp_path / "clone"
    (clone / "scripts").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "git.log"
    _git_recorder(bin_dir, log)
    script = re.sub(r"__[A-Z]+_REPO__", str(clone), program[2])
    target = script.rsplit('"$C/', 1)[1].split('"', 1)[0]
    wrapper = clone / target
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_text("git wrapper-start\n", encoding="utf-8")
    proc = subprocess.run(
        ["/bin/bash", "-c", script],
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    _assert_pinned(log)
    assert "wrapper-start" in log.read_text(encoding="utf-8")


@pytest.mark.parametrize("name", sorted(LOOPS))
def test_wrapper_host_git_pins_hooks_off(name, tmp_path):
    repo = _runner_clone(tmp_path, name)
    bin_dir, _gh, _py = _stub_bin(tmp_path, claude_body="#!/bin/sh\nexit 0\n")
    log = tmp_path / "git.log"
    _git_recorder(bin_dir, log)
    subprocess.run(
        [BASH, str(_cloned_wrapper(repo, name)), "audit"],
        env={
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(tmp_path / "home"),
            "RADON_WEEKEND_REPO": str(repo),
            "RADON_WEEKEND_PROVIDER_LADDER": CLAUDE_RUNG_LADDER,
            "RADON_WEEKEND_MODEL_LADDER": "claude-opus-5",
            "RADON_WEEKEND_SKIP_PRUNE": "1",
        },
        capture_output=True,
        text=True,
        timeout=120,
    )
    _assert_pinned(log)


SETUPS = sorted((REPO / "scripts").glob("setup_*.sh"))
CLONE_SETUPS = [p for p in SETUPS if "WEEKEND_REPO" in p.read_text(encoding="utf-8")]


def test_every_runner_setup_is_covered():
    assert len(CLONE_SETUPS) >= 5, CLONE_SETUPS


@pytest.mark.parametrize("setup", CLONE_SETUPS, ids=lambda p: p.stem)
def test_setup_pins_hooks_off_before_any_git(setup):
    text = setup.read_text(encoding="utf-8")
    pin = text.index("export GIT_CONFIG_COUNT=2 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/dev/null")
    assert "GIT_CONFIG_KEY_1=core.fsmonitor GIT_CONFIG_VALUE_1=false" in text[pin : pin + 200]
    first_git = min(i for i in (text.find("git -C"), text.find("\ngit ")) if i >= 0)
    assert pin < first_git
