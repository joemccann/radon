"""Nightly (and related) GitHub PR titles and bodies.

The five unattended loops used to dump audit tables, SHA ranges, finding
inventories and gate counts into the PR. Operators read the PR for what
broke, what changed, and whether anything remains outside CI. Everything
else stays on the rolling issue and in the loop ledgers.

This module pins the formatter and the skill instructions that call it.
It does not touch issue-comment or Pushover generation.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import github_pr_output as pr

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
SKILLS = REPO / ".claude" / "skills"
LOOPS = (
    "reliability-weekend",
    "testing-weekend",
    "ci-performance",
    "documentation-nightly",
    "security-nightly",
)
WRAPPERS = (
    SCRIPTS / "reliability_weekend.sh",
    SCRIPTS / "testing_weekend.sh",
    SCRIPTS / "ci_performance_nightly.sh",
    SCRIPTS / "documentation_nightly.sh",
    SCRIPTS / "security_nightly.sh",
)
ISSUE_HEADING = "## Issue discovered"
FIX_HEADING = "## What was done to fix it"
NEXT_HEADING = "## Next"
GREEN = "Fixed with green deployment"


def _body(**kwargs) -> str:
    return pr.format_pr_body(**kwargs)


class TestFormatPrBody:
    def test_three_sections_in_order(self):
        body = _body(
            issue="The TLS handshake ran on the accept thread.",
            fix="Handshake now runs per connection under a socket timeout.",
        )
        assert body.index(ISSUE_HEADING) < body.index(FIX_HEADING) < body.index(NEXT_HEADING)
        headings = [ln for ln in body.splitlines() if ln.startswith("## ")]
        assert headings == [ISSUE_HEADING, FIX_HEADING, NEXT_HEADING]

    def test_plain_language_issue_and_fix(self):
        body = _body(
            issue="The TLS handshake ran on the accept thread.",
            fix="Handshake now runs per connection under a socket timeout.",
        )
        assert "The TLS handshake ran on the accept thread." in body
        assert "Handshake now runs per connection under a socket timeout." in body

    def test_default_next_is_green_deployment(self):
        body = _body(
            issue="Shard imbalance stretched the python gate.",
            fix="Moved the 45s floor module onto its own shard.",
        )
        next_block = body.split(NEXT_HEADING, 1)[1].strip()
        assert next_block == GREEN

    def test_explicit_next_replaces_the_default(self):
        body = _body(
            issue="Control-plane units changed.",
            fix="Pinned the new unit hash in the install manifest.",
            next_action="Run the root bootstrap-control-plane.sh install-copy before merge.",
        )
        next_block = body.split(NEXT_HEADING, 1)[1].strip()
        assert next_block == (
            "Run the root bootstrap-control-plane.sh install-copy before merge."
        )
        assert GREEN not in body

    def test_blank_next_is_treated_as_none(self):
        body = _body(
            issue="A.",
            fix="B.",
            next_action="   ",
        )
        assert body.split(NEXT_HEADING, 1)[1].strip() == GREEN

    def test_no_audit_dump_scaffolding(self):
        body = _body(issue="A.", fix="B.")
        for banned in ("## Audit", "## Landed", "CIP-", "R-469", "gate counts"):
            assert banned not in body

    @pytest.mark.parametrize("field, kwargs", [
        ("issue", {"issue": "  ", "fix": "B."}),
        ("fix", {"issue": "A.", "fix": ""}),
    ])
    def test_empty_issue_or_fix_is_refused(self, field, kwargs):
        with pytest.raises(ValueError, match=field):
            _body(**kwargs)


class TestFormatPrTitle:
    @pytest.mark.parametrize(
        "loop, prefix",
        [
            ("reliability", "Reliability"),
            ("testing", "Testing"),
            ("documentation", "Documentation"),
            ("ci-performance", "CI Performance"),
        ],
    )
    def test_loop_date_and_plain_language_issue(self, loop, prefix):
        title = pr.format_pr_title(
            loop=loop,
            date="2026-09-01",
            issue="The TLS handshake ran on the accept thread.",
        )
        assert title == (
            f"{prefix} 2026-09-01: The TLS handshake ran on the accept thread."
        )

    def test_security_title_is_date_only(self):
        issue = "A sanitized one-line description of the patched class."
        title = pr.format_pr_title(
            loop="security", date="2026-09-01", issue=issue
        )
        assert title == "Security 2026-09-01"
        assert issue not in title
        body = _body(issue=issue, fix="The chokepoint now refuses the input.")
        assert issue in body

    def test_unknown_loop_is_refused(self):
        with pytest.raises(ValueError, match="loop"):
            pr.format_pr_title(loop="factory", date="2026-09-01", issue="A.")

    def test_empty_issue_is_refused(self):
        with pytest.raises(ValueError, match="issue"):
            pr.format_pr_title(loop="testing", date="2026-09-01", issue="  ")

    def test_title_fits_github_256_char_limit(self):
        issue = (
            "The TLS handshake ran on the accept thread, so one half-open "
            "TCP connect froze Gateway control, healthz, and every mTLS "
            "restart from the app host while the broker still looked up."
        ) * 4
        title = pr.format_pr_title(
            loop="reliability", date="2026-09-01", issue=issue
        )
        assert len(title) <= pr.GITHUB_PR_TITLE_MAX
        assert title.startswith("Reliability 2026-09-01:")
        body = _body(issue=issue, fix="Handshake now runs per connection.")
        assert issue[:80] in body


class TestZeroFindingDeadman:
    def test_no_deploy_needed_variant(self):
        body = _body(
            issue="No new defect this cycle.",
            fix="Recorded the audit. No code change.",
            next_action="No deploy needed.",
        )
        assert "No new defect this cycle." in body
        assert "No code change." in body
        assert body.split(NEXT_HEADING, 1)[1].strip() == "No deploy needed."
        assert GREEN not in body


class TestCli:
    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "github_pr_output.py"), *args],
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_json_emits_title_and_body(self):
        proc = self._run(
            "--loop", "reliability",
            "--date", "2026-09-01",
            "--issue", "The TLS handshake ran on the accept thread.",
            "--fix", "Handshake now runs per connection under a socket timeout.",
            "--json",
        )
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        assert payload["title"].startswith("Reliability 2026-09-01:")
        assert ISSUE_HEADING in payload["body"]
        assert payload["body"].rstrip().endswith(GREEN)

    def test_security_json_title_is_date_only(self):
        issue = "A sanitized one-line description of the patched class."
        proc = self._run(
            "--loop", "security",
            "--date", "2026-09-01",
            "--issue", issue,
            "--fix", "The chokepoint now refuses the input.",
            "--json",
        )
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        assert payload["title"] == "Security 2026-09-01"
        assert issue in payload["body"]

    def test_text_emits_title_then_body(self):
        proc = self._run(
            "--loop", "testing",
            "--date", "2026-08-31",
            "--issue", "A skipped test never ran.",
            "--fix", "The test now executes against HEAD.",
            "--next", "No deploy needed.",
        )
        assert proc.returncode == 0, proc.stderr
        title, body = proc.stdout.split("\n\n", 1)
        assert title.startswith("Testing 2026-08-31:")
        assert ISSUE_HEADING in body
        assert "No deploy needed." in body

    def test_json_still_exits_zero_when_the_issue_would_overflow_the_title(self):
        issue = ("One half-open TCP connect froze Gateway control. " * 20).strip()
        proc = self._run(
            "--loop", "reliability",
            "--date", "2026-09-01",
            "--issue", issue,
            "--fix", "Handshake now runs per connection under a socket timeout.",
            "--json",
        )
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        assert len(payload["title"]) <= pr.GITHUB_PR_TITLE_MAX
        assert issue in payload["body"]


class TestNightlyTemplateMatchesFormatter:
    def test_template_has_the_three_headings_and_green_default(self):
        text = (REPO / ".github" / "PULL_REQUEST_TEMPLATE" / "nightly.md").read_text(
            encoding="utf-8"
        )
        assert ISSUE_HEADING in text
        assert FIX_HEADING in text
        assert NEXT_HEADING in text
        assert GREEN in text

    def test_default_human_template_is_untouched(self):
        text = (REPO / ".github" / "pull_request_template.md").read_text(encoding="utf-8")
        assert "Red/green TDD" in text
        assert "Issue discovered" not in text


class TestSkillsInstructTheFormatter:
    @pytest.mark.parametrize("loop", LOOPS)
    def test_skill_names_the_formatter_and_the_three_headings(self, loop):
        raw = (SKILLS / loop / "SKILL.md").read_text(encoding="utf-8")
        text = " ".join(raw.split())
        assert "## Pull request output" in raw, loop
        assert "scripts/github_pr_output.py" in text, loop
        assert "Issue discovered" in text, loop
        assert "What was done to fix it" in text, loop
        assert GREEN in text, loop

    def test_skills_still_keep_audit_detail_off_the_pr(self):
        reliability = (SKILLS / "reliability-weekend" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        testing = (SKILLS / "testing-weekend" / "SKILL.md").read_text(encoding="utf-8")
        assert "with the delta summary in the body" not in reliability
        assert "update the PR body with: tasks DONE/BLOCKED" not in testing

    @pytest.mark.parametrize("loop", LOOPS)
    def test_pull_request_output_can_create_and_update(self, loop):
        raw = (SKILLS / loop / "SKILL.md").read_text(encoding="utf-8")
        start = raw.index("## Pull request output")
        nxt = raw.find("\n## ", start + 1)
        section = raw[start:nxt if nxt != -1 else None]
        text = " ".join(section.split())
        assert "gh pr create" in text, loop
        assert "--head" in text and "--base" in text, loop
        assert "PATCH" in text, loop
        assert "title, body" in text or "{title, body}" in text, loop

    def test_security_skill_documents_date_only_title(self):
        raw = (SKILLS / "security-nightly" / "SKILL.md").read_text(encoding="utf-8")
        start = raw.index("## Pull request output")
        nxt = raw.find("\n## ", start + 1)
        section = raw[start:nxt if nxt != -1 else None]
        text = " ".join(section.split())
        assert "`Security <YYYY-MM-DD>`" in text
        assert "`Security <YYYY-MM-DD>: <plain-language issue>`" not in text
        assert "only in the body" in text or "issue only in the body" in text


class TestIssueGenerationIsUntouched:
    """A sibling agent owns GitHub issue body/comment generation."""

    def test_wrappers_do_not_call_the_pr_formatter(self):
        for path in WRAPPERS:
            text = path.read_text(encoding="utf-8")
            assert "github_pr_output" not in text, path.name

    def test_pushover_and_issue_comment_helpers_do_not_import_it(self):
        for name in ("weekend_notify.py", "weekend_redact.py"):
            text = (SCRIPTS / name).read_text(encoding="utf-8")
            assert "github_pr_output" not in text, name

    def test_wrappers_still_post_the_rolling_issue_comment(self):
        for path in WRAPPERS:
            text = path.read_text(encoding="utf-8")
            assert "gh issue comment" in text, path.name
