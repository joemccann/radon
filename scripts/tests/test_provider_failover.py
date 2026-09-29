"""The security loops stay claude-exclusive on the runner.

They are the loops whose output is sanitized before it reaches a public
issue, and a fallback CLI cannot be held to that contract. On the per-loop
wrappers this was refuse_non_claude_rung; on scripts/runner/run_loop.sh it
is ALLOWED_AGENTS=claude in the root-owned loop env: any rung naming another
agent (from AGENTS or from the resolver) is refused before cloning.
The runner behaviour itself is driven in test_runner_run_loop.py.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SECURITY_LOOPS = ("security", "security-deepsec")


def _env(loop: str) -> dict[str, str]:
    out = {}
    for line in (REPO / "scripts" / "runner" / "loops" / f"{loop}.env").read_text().splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            out[key] = value.strip('"')
    return out


@pytest.mark.parametrize("loop", SECURITY_LOOPS)
def test_only_claude_rungs_are_allowed(loop):
    env = _env(loop)
    assert env["ALLOWED_AGENTS"] == "claude"
    assert all(rung.startswith("claude:") for rung in env["AGENTS"].split())


@pytest.mark.parametrize("loop", SECURITY_LOOPS)
def test_the_ladder_comes_from_the_skip_newest_resolver_and_never_leads_with_fable(loop):
    env = _env(loop)
    assert env["AGENTS_RESOLVER"] == "lib/security_claude_ladder.py"
    assert "fable" not in env["AGENTS"]


def test_the_runner_refuses_a_disallowed_rung_before_anything_runs():
    body = (REPO / "scripts" / "runner" / "run_loop.sh").read_text()
    main = body[body.index("main() {"):]
    assert main.index("refuse_disallowed_agents") < main.index("fresh_clone")
    assert main.index("resolve_agents") < main.index("refuse_disallowed_agents")
