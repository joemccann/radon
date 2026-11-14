"""The maintenance loops' pre-run hook writes an author-filtered checkpoint.

Drives the real hook against a local bare origin with gh stubbed. The
rolling issue is public, so the hook, not the prompt, decides which comments
count: owner, member and collaborator authors only.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / "scripts" / "runner" / "hooks" / "checkpoint_pre.sh"
BASH = shutil.which("bash") or "/bin/bash"
UNTRUSTED = "UNTRUSTED-COMMENT-TEXT-MUST-NOT-REACH-THE-AGENT"

GH_STUB = r"""#!/bin/bash
printf '%s\n' "$*" >> "$CALLS/gh.argv"
case "$*" in
  *"issue list"*) echo '[{"number": 81, "title": "Nightly reliability runner"}]' ;;
  *"issues/81/comments"*)
    printf '{"html_url":"u1","created_at":"2026-01-01T00:00:00Z","body":"audited-through: %s\\nR-100 open: fix the thing","author_association":"OWNER"}\n' "$BASE_SHA"
    printf '{"html_url":"u2","created_at":"2026-01-02T00:00:00Z","body":"audited-through: %s\\n%s","author_association":"NONE"}\n' "$BASE_SHA" "UNTRUSTED-COMMENT-TEXT-MUST-NOT-REACH-THE-AGENT"
    ;;
esac
"""


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def rig(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "README").write_text("x\n")
    _git("init", "-q", "-b", "main", cwd=src)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "add", ".", cwd=src)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init", cwd=src)
    origin = tmp_path / "origin.git"
    _git("clone", "-q", "--bare", str(src), str(origin), cwd=tmp_path)
    work = tmp_path / "work"
    _git("clone", "-q", str(origin), str(work), cwd=tmp_path)
    base_sha = _git("rev-parse", "HEAD", cwd=work)

    runner = tmp_path / "runner"
    (runner / "lib").mkdir(parents=True)
    (runner / "lib" / "nightly_green_base.py").write_text("print('')\n")
    for helper in ("nightly_audit_context.py", "nightly_phase_status.py"):
        shutil.copy2(REPO / "scripts" / helper, runner / "lib" / helper)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(GH_STUB)
    (bin_dir / "gh").chmod(0o755)
    state = tmp_path / "state"
    state.mkdir()
    calls = tmp_path / "calls"
    calls.mkdir()

    class Rig:
        pass

    r = Rig()
    r.work, r.state, r.calls, r.base_sha = work, state, calls, base_sha
    r.context = state / "scratch" / "audit-context.md"

    def run(**extra):
        timeout_dir = os.path.dirname(shutil.which("gtimeout") or shutil.which("timeout") or "/usr/bin/timeout")
        env = {
            "HOME": str(tmp_path / "home"), "CALLS": str(calls), "BASE_SHA": base_sha,
            "LOOP": "reliability", "WORK": str(work), "LOOP_STATE": str(state), "RUNNER_DIR": str(runner),
            "REPO_URL": str(origin), "RUNNER_PYTHON": sys.executable,
            "RADON_RUNNER_PATH": f"{timeout_dir}:/usr/bin:/bin:{os.path.dirname(shutil.which('git'))}",
            "RADON_RUNNER_GH": str(bin_dir / "gh"), **extra,
        }
        return subprocess.run([BASH, str(HOOK)], cwd=work, env=env, capture_output=True, text=True, timeout=120)

    r.run = run
    return r


def test_the_checkpoint_keeps_only_repository_insiders(rig):
    proc = rig.run()

    assert proc.returncode == 0, proc.stderr
    assert f"base={rig.base_sha}" in proc.stdout and "checkpoint=written" in proc.stdout
    text = rig.context.read_text()
    assert f"base: {rig.base_sha}" in text
    assert "R-100 open" in text
    assert UNTRUSTED not in text
    assert stat.S_IMODE(rig.context.stat().st_mode) == 0o600
    assert any("--label reliability-nightly" in c for c in (rig.calls / "gh.argv").read_text().splitlines())


def test_the_hook_filters_by_its_own_loop_label_and_refuses_others(rig):
    assert rig.run(LOOP="testing").returncode == 0
    assert "--label testing-nightly" in (rig.calls / "gh.argv").read_text()

    proc = rig.run(LOOP="security")
    assert proc.returncode == 2 and "refused=" in proc.stdout


def test_an_unreachable_github_still_writes_the_file_and_never_refuses(rig):
    proc = rig.run(RADON_RUNNER_GH="/usr/bin/false")

    assert proc.returncode == 0, proc.stderr
    text = rig.context.read_text()
    assert "base: UNRESOLVED" in text and UNTRUSTED not in text


def test_a_foreign_origin_is_refused(rig):
    proc = rig.run(REPO_URL="https://example.invalid/other.git")

    assert proc.returncode == 2
    assert "refused=the clone's origin is not the loop's repository" in proc.stdout
    assert not rig.context.exists()
