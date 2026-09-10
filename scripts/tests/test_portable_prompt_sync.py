"""A non-Claude CLI runs a rendered copy of the manual, so it must not go stale.

Neither codex nor grok resolves a `/testing-weekend audit` slash command, so
the wrapper pipes `.claude/portable-prompts/<skill>.<phase>.md` — rendered by
`scripts/render_loop_prompt.py` and committed. Editing a SKILL.md without
re-rendering would leave the fallback providers running last week's manual,
silently, and only on the nights Claude was capped.

The same source also renders `.codex/skills/<skill>/`, the layout codex's own
skill discovery expects. Probed on this runner (codex-cli 0.153.4,
2026-09-08): codex reads `./.codex/skills/` and does NOT read
`.claude/skills/`, while grok scans `./.claude/skills/` natively and needs no
render at all. Both rendered artifacts are checked here for the same reason:
two copies of a manual are two chances to drift.
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


# --- native codex skills (2026-09-08) ---------------------------------------
# codex discovers `./.codex/skills/<name>/SKILL.md` relative to its working
# directory. grok is deliberately absent from this section: it reads
# `./.claude/skills/` itself, so rendering a copy for it would be a second
# source of truth for no gain.


@pytest.mark.parametrize("skill", sorted(LOOPS))
def test_the_codex_skill_matches_a_fresh_render(skill):
    for path, fresh in render_mod.codex_targets(skill):
        assert path.exists(), (
            f"{path.relative_to(REPO)} missing — run render_loop_prompt.py --write"
        )
        assert path.read_text(encoding="utf-8") == fresh, (
            f"{path.relative_to(REPO)} is stale; run: "
            "python3 scripts/render_loop_prompt.py --write"
        )


@pytest.mark.parametrize("skill", sorted(LOOPS))
def test_the_codex_skill_keeps_the_frontmatter_codex_reads(skill):
    """A skill without `name`/`description` frontmatter is not discovered."""
    text = (render_mod.CODEX_SKILL_DIR / skill / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert text.startswith("---\n"), "frontmatter must be first"
    head = text[4 : text.index("\n---\n", 3)]
    assert f"name: {skill}" in head
    assert "description: " in head


@pytest.mark.parametrize("skill", sorted(LOOPS))
def test_the_codex_skill_carries_the_whole_manual(skill):
    """Same rule as the portable prompt: a foreign CLI cannot be pointed at a
    file it does not know how to find."""
    text = (render_mod.CODEX_SKILL_DIR / skill / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert len(text) > 10_000, f"body not inlined: {len(text)} bytes"
    assert "# OVERRIDES" in text
    assert "# CONTRACT" in text


@pytest.mark.parametrize("skill", sorted(LOOPS))
def test_the_codex_skill_covers_all_three_phases(skill):
    """One skill document, not three: the phase arrives in the invocation, so
    the contract for every phase has to be present in the one file."""
    text = (render_mod.CODEX_SKILL_DIR / skill / "SKILL.md").read_text(
        encoding="utf-8"
    )
    contract = text[text.index("# CONTRACT") :]
    assert render_mod.BRANCH_PREFIX[skill] in contract
    assert "NIGHTLY DELIVER READY:" in contract
    assert "NIGHTLY DELIVER INCOMPLETE:" in contract


@pytest.mark.parametrize("skill", sorted(LOOPS))
def test_the_codex_agent_interface_block(skill):
    """codex reads the interface from agents/openai.yaml, not the frontmatter."""
    text = (
        render_mod.CODEX_SKILL_DIR / skill / "agents" / "openai.yaml"
    ).read_text(encoding="utf-8")
    assert text.startswith("interface:\n")
    for key in ("display_name:", "short_description:", "default_prompt:"):
        assert key in text, f"{key} missing"
    assert f"${skill}" in text, "the default prompt must name the skill"


def test_the_security_loop_has_no_codex_skill():
    """Claude-exclusive for the same reason it has no portable prompt."""
    assert not (render_mod.CODEX_SKILL_DIR / "security-nightly").exists()


def test_no_codex_skill_exists_without_a_loop_behind_it():
    have = {d.name for d in render_mod.CODEX_SKILL_DIR.iterdir() if d.is_dir()}
    assert have == set(LOOPS), (have ^ set(LOOPS))
