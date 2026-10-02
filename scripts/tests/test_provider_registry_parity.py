"""The two security loops share one set of rails.

The wrappers used to be byte-identical copies checked function by function.
On the runner the rails live once, in scripts/runner/run_loop.sh and the
shared hooks; what may differ is each loop's env. This pins that the two loop
envs differ only where the loops genuinely differ.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LOOPS = REPO / "scripts" / "runner" / "loops"
# Keys that are the loop's own identity, budget or workspace.
OWN = {"BRANCH_PREFIX", "PROMPT", "PHASES", "KEEP_PATHS", "SCHEDULE_MINUTE"}


def _settings(name: str) -> dict[str, str]:
    out = {}
    for line in (LOOPS / f"{name}.env").read_text().splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            out[key] = value
    return out


def test_the_security_loops_differ_only_in_their_own_settings():
    security, deepsec = _settings("security"), _settings("security-deepsec")
    shared = (set(security) | set(deepsec)) - OWN
    diff = {key for key in shared if security.get(key) != deepsec.get(key)}
    assert not diff, diff


def test_both_security_loops_use_the_shared_hooks_and_guard():
    for name in ("security", "security-deepsec"):
        env = _settings(name)
        assert env["PRE_RUN"] == "hooks/security_pre.sh"
        assert env["POST_RUN"] == "hooks/security_post.sh"
        assert env["GH_GUARD"] == "1"
