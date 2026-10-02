"""The security loops pin their model, and the nested scan uses the same one.

2026-09-01: `~/.claude/settings.json` carried a model whose quota was gone and
the unpinned loops died on it. The runner resolves the rung from
security_claude_ladder.py (skip newest, never fable), passes it as
`claude --model <m> --effort medium`, and exports it as RADON_RUNNER_MODEL
(test_runner_run_loop.py drives both).

DOC-042: the security prompt's Stage 4 spawns a SECOND, nested `claude` (the
Claude Security scan), which a `--model` flag on the outer process does not
reach, so the prompt must pass the exported rung itself.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PROMPT = REPO / ".claude" / "runner-prompts" / "security.md"
SCAN_INVOCATION = "claude --agent claude-security:claude-security"


def _stage4_block() -> str:
    text = PROMPT.read_text(encoding="utf-8")
    at = text.index(SCAN_INVOCATION)
    return text[text.rindex("```sh", 0, at):text.index("```", at)]


def test_the_nested_scan_uses_the_runners_rung_and_medium_effort():
    invocation = _stage4_block()[_stage4_block().index(SCAN_INVOCATION):]
    flags = invocation.split(" -p ", 1)[0]
    assert '--model "$RADON_RUNNER_MODEL"' in flags
    assert "--effort medium" in flags


def test_the_runner_passes_model_and_medium_effort_to_every_claude_launch():
    body = (REPO / "scripts" / "runner" / "run_loop.sh").read_text()
    arm = body[body.index("    claude) "):body.index(";;", body.index("    claude) "))]
    assert '${provider:+--model "$provider"} --effort medium' in arm
    assert "fable" not in arm
    assert 'export RADON_RUNNER_AGENT="$agent" RADON_RUNNER_MODEL="$provider"' in body
