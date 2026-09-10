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


class TestBulletedSections:
    """--issue/--fix/--next may carry one bullet per line so a PR reads as
    a scannable list instead of one dense paragraph. Each bullet's own
    internal whitespace still collapses; line breaks between bullets do
    not."""

    def test_issue_bullets_survive_as_separate_lines(self):
        body = _body(
            issue=(
                "- **Validator**: contacted any host a caller named.\n"
                "- **Wizard**: wrote unknown env keys on backend errors."
            ),
            fix="B.",
        )
        issue_block = body.split(ISSUE_HEADING, 1)[1].split(FIX_HEADING, 1)[0].strip()
        assert issue_block == (
            "- **Validator**: contacted any host a caller named.\n"
            "- **Wizard**: wrote unknown env keys on backend errors."
        )

    def test_internal_whitespace_still_collapses_per_line(self):
        body = _body(issue="-   **Validator**:   too   many   spaces.", fix="B.")
        assert "- **Validator**: too many spaces." in body

    def test_blank_lines_between_bullets_are_dropped(self):
        body = _body(issue="- First finding.\n\n\n- Second finding.", fix="B.")
        issue_block = body.split(ISSUE_HEADING, 1)[1].split(FIX_HEADING, 1)[0].strip()
        assert issue_block == "- First finding.\n- Second finding."

    def test_next_action_bullets_also_survive(self):
        body = _body(
            issue="A.",
            fix="B.",
            next_action=(
                "- **Deploy**: writes the derived env file.\n"
                "- **Operator**: pins the repository key by fingerprint."
            ),
        )
        next_block = body.split(NEXT_HEADING, 1)[1].strip()
        assert next_block == (
            "- **Deploy**: writes the derived env file.\n"
            "- **Operator**: pins the repository key by fingerprint."
        )

    def test_single_line_issue_is_unaffected(self):
        body = _body(issue="The TLS handshake ran on the accept thread.", fix="B.")
        issue_block = body.split(ISSUE_HEADING, 1)[1].split(FIX_HEADING, 1)[0].strip()
        assert issue_block == "The TLS handshake ran on the accept thread."


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

    def test_bulleted_issue_collapses_to_a_single_line_title(self):
        issue = (
            "- **Validator**: contacted any host a caller named.\n"
            "- **Wizard**: wrote unknown env keys on backend errors."
        )
        title = pr.format_pr_title(loop="reliability", date="2026-09-01", issue=issue)
        assert "\n" not in title
        assert title == (
            "Reliability 2026-09-01: Validator: contacted any host a caller named."
        )

    def test_unknown_loop_is_refused(self):
        with pytest.raises(ValueError, match="loop"):
            pr.format_pr_title(loop="factory", date="2026-09-01", issue="A.")

    def test_empty_issue_is_refused(self):
        with pytest.raises(ValueError, match="issue"):
            pr.format_pr_title(loop="testing", date="2026-09-01", issue="  ")

    @pytest.mark.parametrize(
        "loop, bad_date",
        [
            ("security", "2026-09-01 A sanitized one-line description of the patched class."),
            ("reliability", "2026-09-01 The TLS handshake ran on the accept thread."),
            ("security", "2026-9-1"),
            ("testing", "09/01/2026"),
            ("documentation", "2026-13-01"),
        ],
    )
    def test_date_must_be_yyyy_mm_dd(self, loop, bad_date):
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            pr.format_pr_title(loop=loop, date=bad_date, issue="A.")

    @pytest.mark.parametrize("loop", list(pr.LOOP_TITLES))
    def test_every_loop_title_fits_github_256_char_limit(self, loop):
        issue = ("The TLS handshake ran on the accept thread. " * 20).strip()
        title = pr.format_pr_title(loop=loop, date="2026-09-01", issue=issue)
        assert len(title) <= pr.GITHUB_PR_TITLE_MAX
        assert title.startswith(pr.LOOP_TITLES[loop] + " 2026-09-01")

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

    def test_json_refuses_a_pasted_issue_in_date(self):
        proc = self._run(
            "--loop", "security",
            "--date", "2026-09-01 A sanitized one-line description of the patched class.",
            "--issue", "A sanitized one-line description of the patched class.",
            "--fix", "The chokepoint now refuses the input.",
            "--json",
        )
        assert proc.returncode == 2
        assert "YYYY-MM-DD" in proc.stderr
        assert proc.stdout == ""


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
        # Captain d1: wrapper comments are **PHASE** STAMP **status**,
        # not the three-section write-up. report() uses the GH_BIN snapshot.
        for path in WRAPPERS:
            text = path.read_text(encoding="utf-8")
            assert '"$GH_BIN" issue comment' in text, path.name
            assert "gh issue comment" not in text, path.name
            fmt_start = text.index("_format_issue_body() {")
            fmt_end = text.index("\nreport() {", fmt_start)
            fmt = text[fmt_start:fmt_end]
            assert "'**%s** %s **%s**'" in fmt, path.name
            assert "**Issue discovered**" not in fmt, path.name
            assert "**What was done to fix it**" not in fmt, path.name
            assert "Fixed with green deployment" not in fmt, path.name


class TestSecurityBodyIsRedacted:
    """The security title is date-only; the body must pass the same public
    sanitizer as the rolling-issue comment (scripts/nightly_issue_format.py)."""

    # Runtime-join so the synthetic assignment never sits in the tree as a
    # contiguous secret-shaped literal.
    SECRET = "UW_" + "TOKEN" + "=" + "NOT-A-REAL-KEY-1234"
    ROUTE = "/api/orders/place"

    def test_render_redacts_secret_and_route_in_security_body(self):
        payload = pr.render(
            loop="security",
            date="2026-09-02",
            issue=f"Reached {self.ROUTE} with {self.SECRET} in the request.",
            fix="The chokepoint now refuses the input.",
            next_action=f"Rotate {self.SECRET} before merge.",
        )
        assert "NOT-A-REAL-KEY-1234" not in payload["body"]
        assert self.ROUTE not in payload["body"]
        assert "[REDACTED]" in payload["body"]
        assert ISSUE_HEADING in payload["body"]
        assert FIX_HEADING in payload["body"]
        assert NEXT_HEADING in payload["body"]

    def test_non_security_loops_keep_the_body_verbatim(self):
        payload = pr.render(
            loop="reliability",
            date="2026-09-02",
            issue=f"{self.ROUTE} hung the accept thread.",
            fix="Handshake now runs per connection.",
            next_action=None,
        )
        assert self.ROUTE in payload["body"]

    def test_cli_security_json_body_is_redacted(self):
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "github_pr_output.py"),
                "--loop", "security",
                "--date", "2026-09-02",
                "--issue", f"Reached {self.ROUTE} with {self.SECRET} in the request.",
                "--fix", "The chokepoint now refuses the input.",
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        assert payload["title"] == "Security 2026-09-02"
        assert "NOT-A-REAL-KEY-1234" not in payload["body"]
        assert self.ROUTE not in payload["body"]
        assert "[REDACTED]" in payload["body"]
