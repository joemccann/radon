"""A non-Claude CLI runs the rendered prompt, so the render must not go stale.

codex and grok cannot load `.claude/skills/*/SKILL.md` or resolve a
`/testing-weekend audit` slash command. They get `.claude/portable-prompts/`
instead, rendered by `scripts/render_loop_prompt.py` and committed. Editing a
SKILL.md without re-rendering would leave the fallback providers running last
week's manual — silently, and only on the nights Claude was capped.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
_R = REPO / "scripts" / "render_loop_prompt.py"
_spec = importlib.util.spec_from_file_location("_render_loop_prompt", _R)
render_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = render_mod
_spec.loader.exec_module(render_mod)

LOOPS = render_mod.LOOPS
PHASES = render_mod.PHASES
CASES = [(s, p) for s in sorted(LOOPS) for p in PHASES]


@pytest.mark.parametrize("skill,phase", CASES)
def test_the_rendered_prompt_matches_a_fresh_render(skill, phase):
    path = render_mod.target(skill, phase)
    assert path.exists(), f"{path.name} missing — run render_loop_prompt.py --write"
    assert path.read_text(encoding="utf-8") == render_mod.render(skill, phase), (
        f"{path.name} is stale; run: python3 scripts/render_loop_prompt.py --write"
    )


def test_every_loop_and_phase_is_covered():
    have = {p.name for p in render_mod.OUT_DIR.glob("*.md")}
    want = {f"{s}.{p}.md" for s, p in CASES}
    assert have == want, (have ^ want)


def test_the_security_loop_has_no_portable_prompt():
    """It is claude-exclusive on purpose: it is the one loop whose findings are
    sanitized before reaching a public issue."""
    assert "security-nightly" not in LOOPS
    assert not list(render_mod.OUT_DIR.glob("security-nightly.*"))


@pytest.mark.parametrize("skill,phase", CASES)
def test_the_prompt_carries_the_body_not_just_a_pointer(skill, phase):
    text = render_mod.target(skill, phase).read_text(encoding="utf-8")
    assert len(text) > 10_000, (
        "a foreign CLI cannot be told to 'read SKILL.md' — the body must be "
        f"inlined: {len(text)} bytes"
    )
    assert not text.startswith("---\n"), "frontmatter was not stripped"


@pytest.mark.parametrize("skill,phase", CASES)
def test_the_overrides_disable_the_claude_only_machinery(skill, phase):
    text = render_mod.target(skill, phase).read_text(encoding="utf-8")
    tail = text[text.index("# OVERRIDES") :]
    for needed in ("subagent", "Playwright", "RADON_WEEKEND_REDUCED", "CONTRACT"):
        assert needed in tail, f"{needed} missing from the overrides"


@pytest.mark.parametrize("skill,phase", CASES)
def test_the_contract_names_what_the_wrapper_actually_greps(skill, phase):
    text = render_mod.target(skill, phase).read_text(encoding="utf-8")
    contract = text[text.index("# CONTRACT") :]
    if phase == "deliver":
        assert "NIGHTLY DELIVER READY:" in contract
        assert "NIGHTLY DELIVER INCOMPLETE:" in contract
    else:
        assert render_mod.BRANCH_PREFIX[skill] in contract, (
            "the wrapper scores audit and remediate on a commit to the dated "
            "branch; the prompt has to say which branch"
        )


def test_the_deliver_verdict_matches_the_wrapper_regex():
    """The wrapper's deliver_status greps for these two strings; a prompt that
    teaches a different wording turns every fallback deliver INCOMPLETE."""
    wrapper = (REPO / "scripts" / "testing_weekend.sh").read_text(encoding="utf-8")
    for verdict in ("NIGHTLY DELIVER READY:", "NIGHTLY DELIVER INCOMPLETE:"):
        assert verdict in wrapper, f"{verdict} is not what the wrapper reads"
