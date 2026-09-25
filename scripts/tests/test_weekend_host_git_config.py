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
def _plist_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


PLISTS = sorted(
    p
    for p in (REPO / "config").glob("com.radon.*.plist")
    if "git " in _plist_text(p)
    and ("__WEEKEND_REPO__" in _plist_text(p) or "__DEEPSEC_REPO__" in _plist_text(p))
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


def _timeout_passthrough(bin_dir: Path) -> None:
    # The launcher fails closed without timeout/gtimeout; macOS ships neither
    # on /usr/bin:/bin, so stand in a pass-through for the bounded fetch.
    stub = bin_dir / "timeout"
    stub.write_text('#!/bin/sh\nshift 3\nexec "$@"\n', encoding="utf-8")
    stub.chmod(0o755)


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
    _timeout_passthrough(bin_dir)
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


def test_every_clone_plist_uses_host_gitdir():
    assert PLISTS, "no runner plists matched"
    for plist in PLISTS:
        text = plist.read_text(encoding="utf-8")
        assert ".gitdirs/" in text, plist.name
        assert 'git -C "$C"' not in text, plist.name


@pytest.mark.parametrize("name", sorted(LOOPS))
def test_wrapper_host_git_uses_gitdirs_not_clone(name, tmp_path):
    repo = _runner_clone(tmp_path, name)
    bin_dir, _gh, _py = _stub_bin(tmp_path, claude_body="#!/bin/sh\nexit 0\n")
    log = tmp_path / "git.log"
    git = bin_dir / "git"
    git.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{log}"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    git.chmod(0o755)
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
    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    assert lines, "no git invocation recorded"
    host_dir = str(tmp_path / ".gitdirs" / f"{name}.git")
    agent_dir = str(tmp_path / ".gitdirs-agent" / f"{name}.git")
    clone_git = str(repo / ".git")
    saw_host = False
    for line in lines:
        if "--git-dir=" in line or line.startswith("--git-dir"):
            # agent_git_ro: a throwaway host-written gitdir, never the agent's.
            if "/radon-agent-ro." in line.split()[0]:
                continue
            saw_host = True
            assert host_dir in line, line
            assert agent_dir not in line.split()[0], line
            assert clone_git not in line.split(), line
    assert saw_host, lines


@pytest.mark.parametrize("plist", PLISTS, ids=lambda p: p.stem)
@pytest.mark.parametrize("failed_step", ["fetch", "checkout", "reset"])
def test_launchd_never_executes_clone_after_failed_refresh(plist, failed_step, tmp_path):
    clone = tmp_path / "clone"
    (clone / "scripts").mkdir(parents=True)
    program = plistlib.loads(plist.read_bytes())["ProgramArguments"][2]
    script = re.sub(r"__[A-Z]+_REPO__", str(clone), program)
    target = script.rsplit('"$C/', 1)[1].split('"', 1)[0]
    canary = tmp_path / "wrapper-ran"
    (clone / target).write_text(f'touch "{canary}"\n', encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    git = bin_dir / "git"
    git.write_text(f'#!/bin/sh\nfor arg do [ "$arg" = "{failed_step}" ] && exit 1; done\nexit 0\n')
    git.chmod(0o755)
    _timeout_passthrough(bin_dir)
    proc = subprocess.run(
        [BASH, "-c", script],
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "RADON_LAUNCHD_FETCH_PAUSE_SECS": "0"},
        timeout=30,
    )
    assert proc.returncode == 70
    assert not canary.exists()


@pytest.mark.parametrize("name", sorted(LOOPS))
@pytest.mark.parametrize("failed_step", ["fetch", "checkout", "reset"])
def test_ground_truth_propagates_failure_inside_conditional(name, failed_step, tmp_path):
    source = LOOPS[name].read_text()
    start = source.index("ground_truth() {")
    function = source[start:source.index("\n}", start) + 2]
    # Bash suppresses errexit throughout functions used as conditions.
    script = '''set -e
HOST_GITDIR=/unused
REPO=/unused
fetch_origin_with_retry() { [[ "$FAIL_STEP" != fetch ]]; }
clear_stale_git_locks() { :; }
resolve_green_main_sha() { :; }
git() { for arg do [[ "$arg" == "$FAIL_STEP" ]] && return 1; done; return 0; }
''' + function + '\nif ! ground_truth; then exit 70; fi\nexit 0\n'
    proc = subprocess.run([BASH, "-c", script], cwd=tmp_path, env={**os.environ, "FAIL_STEP": failed_step}, timeout=10)
    assert proc.returncode == 70


def test_deepsec_setup_preserves_existing_separate_gitdir(tmp_path):
    source = (REPO / "scripts/setup_security_nightly.sh").read_text()
    start = source.index('if [[ -f "$DEEPSEC_REPO/.git"')
    block = source[start:source.index('\nfi', start) + 3]
    clone = tmp_path / "deepsec"
    clone.mkdir()
    gitdir = tmp_path / "gitdirs/security-deepsec.git"
    gitdir.mkdir(parents=True)
    (clone / ".git").write_text(f"gitdir: {gitdir}\n")
    canary = clone / "operator-state"
    canary.write_text("preserve")
    proc = subprocess.run([BASH, "-c", 'set -e\n' + block], env={**os.environ, "DEEPSEC_REPO": str(clone), "DEEPSEC_GITDIR": str(gitdir)}, timeout=10)
    assert proc.returncode == 0
    assert canary.read_text() == "preserve"
    assert not list(tmp_path.glob("deepsec.worktree-*"))
