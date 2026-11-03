"""The private operator report has one template and one set of formatting
rules (2026-09-19: the first hand-published DeepSec audit was a run-record
dump the operator could not read). Both security runner prompts point at
docs/security-report-template.md; this test pins the template's shape so a
prompt edit cannot silently drift the reports back into log dumps."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "docs" / "security-report-template.md"
SKILLS = (
    REPO / ".claude" / "runner-prompts" / "security.md",
    REPO / ".claude" / "runner-prompts" / "security-deepsec.md",
)
REQUIRED_SECTIONS = (
    "## Summary", "## Operator actions", "## Stages", "## Findings",
    "## Rejected", "## Fixes", "## Gate results", "## Resume state",
)


def _template_block() -> str:
    text = TEMPLATE.read_text(encoding="utf-8")
    m = re.search(r"````markdown\n(.*?)\n````\n", text, re.S)
    assert m, "the template file lost its fenced ````markdown block"
    return m.group(1)


class TestTheTemplate:
    def test_every_section_is_present_in_order(self):
        block = _template_block()
        positions = [block.index(s) for s in REQUIRED_SECTIONS]
        assert positions == sorted(positions), REQUIRED_SECTIONS

    def test_the_summary_is_a_table_that_leads(self):
        block = _template_block()
        summary = block[block.index("## Summary"):block.index("## Operator actions")]
        assert "| Field | Value |" in summary and "|---|---|" in summary
        for field in ("Run id", "Status", "Head", "Range", "Findings", "PR", "Operator actions"):
            assert f"| {field} |" in summary, field

    def test_findings_and_fixes_are_tables_with_fixed_columns(self):
        block = _template_block()
        assert "| Id | Severity | CWE | Location | Summary | Disposition |" in block
        assert "| Finding | Commit | Branch | Regression test | Gates |" in block
        assert "| Candidate | Reason |" in block

    def test_the_rules_forbid_the_dump_shape(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        rules = text[text.index("## Formatting rules"):text.index("## Template")]
        for rule in ("never in `key: value` line dumps", "No emoji", "fenced\n   code block",
                     "Secret literals never appear", "Nothing is omitted for brevity"):
            assert rule in rules, rule
        assert "—" not in text, "no em dashes in the template"

    @pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.stem)
    def test_each_skill_points_at_the_template_and_its_rules(self, skill):
        text = " ".join(skill.read_text(encoding="utf-8").split())
        assert "docs/security-report-template.md" in text, skill
        assert "copy that template verbatim" in text, skill
        assert "never `key: value` line dumps" in text, skill
        assert "pastes the run-record or a scanner's own Markdown is a defect" in text, skill


class TestCoverageAndActionLimits:
    """2026-10-31: a DeepSec rc=75 showed only in the private report, and two
    operator-only findings rolled forward nightly with no recorded decision."""

    def test_not_reviewed_is_a_status_and_a_stage_outcome(self):
        block = _template_block()
        assert "| Status | COMPLETE / INCOMPLETE / OPERATOR_REQUIRED / NOT_REVIEWED |" in block
        assert "not reviewed" in block[block.index("## Stages"):block.index("## Findings")]

    def test_the_summary_carries_unreviewed_and_coverage_limits(self):
        block = _template_block()
        summary = block[block.index("## Summary"):block.index("## Operator actions")]
        assert "| Unreviewed |" in summary
        assert "| Coverage limits |" in summary

    def test_zero_findings_over_an_unreviewed_range_is_not_complete(self):
        rules = " ".join(TEMPLATE.read_text(encoding="utf-8").split())
        assert "Zero findings over a range that was not reviewed is `NOT_REVIEWED`, never `COMPLETE`." in rules

    def test_operator_actions_are_capped_at_three_with_a_count_line(self):
        block = _template_block()
        actions = block[block.index("## Operator actions"):block.index("## Stages")]
        assert "At most three." in actions
        assert "more in the open queue" in actions

    def test_accepted_risks_carry_reason_date_and_expiry(self):
        block = _template_block()
        assert block.index("## Closed tonight") < block.index("## Accepted risks") < block.index("## Stages")
        assert "| Id | Reason | Accepted | Expires |" in block
        rules = " ".join(TEMPLATE.read_text(encoding="utf-8").split())
        assert "`docs/security-accepted-risks.json`" in rules
        assert "The agent never writes an acceptance." in rules

    @pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.stem)
    def test_each_skill_requires_the_coverage_rows(self, skill):
        text = " ".join(skill.read_text(encoding="utf-8").split())
        assert "Fill the Summary's Unreviewed and Coverage limits rows every phase." in text, skill
        assert "Status `NOT_REVIEWED`, never `COMPLETE`" in text, skill
        assert "counts-only `unreviewed:` line" in text, skill
