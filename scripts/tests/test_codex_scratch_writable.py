"""The codex rung's sandbox must let the agent write the scratch dir its phase contract names.

Without it every documentation audit ended INCOMPLETE (exit 75) with
"required durable scratch directory is not writable".
"""
import re
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]

CASES = {
    "documentation_nightly.sh": r"\$WEEKEND_ROOT/\.\$LOOP_SLUG-nightly-scratch",
    "security_nightly.sh": r"\$PRIVATE_SCRATCH",
    "security_deepsec_nightly.sh": r"\$PRIVATE_SCRATCH",
}


@pytest.mark.parametrize("script,scratch", sorted(CASES.items()))
def test_codex_writable_roots_include_scratch(script, scratch):
    text = (SCRIPTS / script).read_text()
    roots = re.search(r"writable_roots=\[(.*?)\]", text)
    assert roots, f"{script}: no codex writable_roots"
    assert re.search(scratch, roots.group(1)), f"{script}: scratch dir not writable"


def test_documentation_scratch_matches_skill():
    skill = (SCRIPTS.parent / ".claude/skills/documentation-nightly/SKILL.md").read_text()
    assert "~/radon-weekend/.documentation-nightly-scratch/" in skill
    assert 'LOOP_SLUG="documentation"' in (SCRIPTS / "documentation_nightly.sh").read_text()
