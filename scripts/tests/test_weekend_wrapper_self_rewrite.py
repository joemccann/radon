"""A nightly launcher must survive its own file being rewritten mid-run.

2026-08-23: the reliability remediate fire died after the agent finished
because bash re-read its wrapper from disk at a byte offset the agent had
rewritten in place. The per-loop wrappers are gone: every nightly loop runs
the root-owned /usr/local/radon-runner/run_loop.sh as the unprivileged
_radonbot user, outside every clone, so the agent cannot rewrite what runs
it. The grok fix-pickup job still execs from a clone and keeps its own rule.
"""
from __future__ import annotations

import plistlib
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash") or "/bin/bash"


@pytest.mark.parametrize("loop", sorted(p.stem for p in (REPO / "scripts" / "runner" / "loops").glob("*.env")))
def test_every_nightly_loop_runs_the_root_owned_runner_outside_the_clone(loop: str) -> None:
    out = subprocess.run([BASH, str(REPO / "scripts" / "runner" / "install.sh"), "--print-plist", loop],
                         capture_output=True, check=True)
    plist = plistlib.loads(out.stdout)
    assert plist["ProgramArguments"] == ["/bin/bash", "/usr/local/radon-runner/run_loop.sh", loop]
    assert plist["UserName"] == "_radonbot"


def test_grok_fix_pickup_plist_resets_main_before_python_exec() -> None:
    plist = plistlib.loads((REPO / "config" / "com.radon.grok-fix-pickup.plist").read_bytes())
    argv = plist["ProgramArguments"]
    assert argv[0] == "/bin/bash" and argv[1] == "-c", argv
    cmd = argv[2]
    assert "fetch" in cmd and "checkout" in cmd and "reset --hard" in cmd, cmd
    assert cmd.index("fetch") < cmd.index("checkout") < cmd.index("reset --hard"), cmd
    assert cmd.index("reset --hard") < cmd.index("exec /usr/bin/env python3.13"), cmd
    assert "grok_fix_pickup.py" in cmd, cmd
    assert 'git -C' in cmd and ".gitdirs/" not in cmd, cmd
    assert "--git-dir=" not in cmd, cmd
