"""PR body template gate (scripts/ci/check_pr_body.py).

Every PR, human or agent, carries the six sections of
.github/pull_request_template.md in order, each filled in. Agents open PRs
with `gh pr create --body`, which skips the template entirely, so the
template alone enforces nothing: this validator backs the CI check, the
nightly gh guard, the IR/security formatters and the Claude Code hook.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from ci import check_pr_body as cpb

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "ci" / "check_pr_body.py"
TEMPLATE = REPO / ".github" / "pull_request_template.md"
WORKFLOW = REPO / ".github" / "workflows" / "pr-body.yml"

GOOD = """## Summary

The chart validator approved unlike valuation definitions.

## What changed

Per-observation evidence filtering rejects approximate values as exact points.

## Test plan

- Regression-only head failed 25 new cases in CI run 38315381303.
- Repaired head: pending.

## Risk and rollback

Fails closed. Revert the merge commit to undo.

## Operator Next Steps

None

## Checklist

- [x] Red/green TDD
- [ ] Previous main deploy finished before this push
"""


def _errors(body: str, **kw) -> list[str]:
    return cpb.find_errors(body, **kw)


class TestTemplateIsTheSource:
    def test_required_headings_match_the_template_in_order(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        headings = [m.group(1).strip() for m in re.finditer(r"^## (.+)$", text, re.M)]
        assert tuple(headings) == cpb.REQUIRED_SECTIONS

    def test_required_sections_are_pr_1047s(self):
        assert cpb.REQUIRED_SECTIONS == (
            "Summary",
            "What changed",
            "Test plan",
            "Risk and rollback",
            "Operator Next Steps",
            "Checklist",
        )

    def test_every_template_placeholder_is_known(self):
        text = cpb.strip_comments(TEMPLATE.read_text(encoding="utf-8"))
        found = set(re.findall(r"<[^<>\n]+>", text))
        assert found and found <= set(cpb.TEMPLATE_PLACEHOLDERS)

    def test_the_unedited_template_fails(self):
        errors = _errors(TEMPLATE.read_text(encoding="utf-8"))
        assert any("placeholder" in e for e in errors)

    def test_template_checklist_matches_the_rendered_checklist(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        checklist = text.split("## Checklist", 1)[1].strip()
        assert checklist == cpb.render_checklist()


class TestValidBodies:
    def test_pr_1047_shape_passes(self):
        assert _errors(GOOD) == []

    def test_crlf_body_from_the_github_editor_passes(self):
        assert _errors(GOOD.replace("\n", "\r\n")) == []

    def test_extra_sections_and_subheadings_are_allowed(self):
        body = GOOD.replace(
            "## Risk and rollback",
            "### Screenshots\n\n![plate](https://media.radon.run/x.png)\n\n## Notes\n\nExtra.\n\n## Risk and rollback",
        )
        assert _errors(body) == []

    def test_heading_case_and_trailing_colon_are_tolerated(self):
        body = GOOD.replace("## Test plan", "## Test Plan:")
        assert _errors(body) == []

    def test_operator_next_steps_none_is_content(self):
        assert "None" in GOOD.split("## Operator Next Steps", 1)[1]
        assert _errors(GOOD) == []

    def test_footer_after_checklist_is_allowed(self):
        body = GOOD + "\n🤖 Generated with [Claude Code](https://claude.com/claude-code)\n"
        assert _errors(body) == []


class TestInvalidBodies:
    def test_empty_body(self):
        assert _errors("") == ["PR body is empty"]
        assert _errors(None) == ["PR body is empty"]

    @pytest.mark.parametrize("section", cpb.REQUIRED_SECTIONS)
    def test_each_missing_section_is_named(self, section):
        lines = GOOD.split("\n")
        body = "\n".join(ln for ln in lines if ln != f"## {section}")
        errors = _errors(body)
        assert any(f"missing section: ## {section}" == e for e in errors), errors

    def test_out_of_order_sections(self):
        body = GOOD.replace("## Summary", "## TMP").replace("## What changed", "## Summary").replace("## TMP", "## What changed")
        errors = _errors(body)
        assert any("out of order" in e for e in errors), errors

    def test_duplicate_section(self):
        body = GOOD + "\n## Summary\n\nAgain.\n"
        assert any("duplicate section: ## Summary" == e for e in _errors(body))

    @pytest.mark.parametrize("section", [s for s in cpb.REQUIRED_SECTIONS if s != "Checklist"])
    def test_empty_section(self, section):
        sections = cpb.parse_sections(GOOD)
        body = GOOD.replace(sections[section], "<!-- nothing yet -->")
        assert f"empty section: ## {section}" in _errors(body)

    def test_comment_only_section_is_empty(self):
        body = GOOD.replace(
            "Fails closed. Revert the merge commit to undo.",
            "<!--\nmulti-line\ncomment\n-->",
        )
        assert "empty section: ## Risk and rollback" in _errors(body)

    def test_unfilled_placeholder(self):
        body = GOOD.replace(
            "The chart validator approved unlike valuation definitions.",
            "<what this is and why>",
        )
        errors = _errors(body)
        assert "unfilled template placeholder in ## Summary: <what this is and why>" in errors

    def test_partly_filled_test_plan_placeholder(self):
        body = GOOD.replace(
            "- Repaired head: pending.",
            "- [ ] Focused local: <command and result>",
        )
        assert any("<command and result>" in e for e in _errors(body))

    def test_checklist_without_task_items(self):
        body = GOOD.replace(
            "- [x] Red/green TDD\n- [ ] Previous main deploy finished before this push",
            "Did everything.",
        )
        assert "## Checklist has no task-list items (- [ ] / - [x])" in _errors(body)

    def test_headings_inside_code_fences_do_not_count(self):
        body = "```\n" + GOOD + "```\n"
        errors = _errors(body)
        assert "missing section: ## Summary" in errors

    def test_level_one_or_three_headings_do_not_satisfy(self):
        body = GOOD.replace("## Summary", "### Summary")
        assert "missing section: ## Summary" in _errors(body)

    def test_bold_pseudo_headings_do_not_satisfy(self):
        body = GOOD.replace("## Summary", "**Summary**")
        assert "missing section: ## Summary" in _errors(body)

    def test_legacy_nightly_three_section_body_fails(self):
        body = (
            "## Issue discovered\n\nX broke.\n\n"
            "## What was done to fix it\n\nFixed X.\n\n"
            "## Next\n\nFixed with green deployment\n"
        )
        errors = _errors(body)
        assert "missing section: ## Summary" in errors
        assert "missing section: ## Checklist" in errors


class TestExemption:
    def test_dependabot_is_exempt(self):
        assert _errors("", author="dependabot[bot]") == []

    @pytest.mark.parametrize("author", [
        "dependabot", "Dependabot[bot]", "dependabot[bot] ", "renovate[bot]",
        "github-actions[bot]", "radon-runner-bot", "joemccann",
    ])
    def test_nothing_else_is_exempt(self, author):
        assert _errors("", author=author) == ["PR body is empty"]

    def test_exempt_authors_is_exactly_dependabot(self):
        assert cpb.EXEMPT_AUTHORS == frozenset({"dependabot[bot]"})


class TestCli:
    def _run(self, *args: str, stdin: str = "") -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            input=stdin, capture_output=True, text=True, timeout=30,
        )

    def test_body_file_ok(self, tmp_path):
        f = tmp_path / "body.md"
        f.write_text(GOOD, encoding="utf-8")
        proc = self._run("--body-file", str(f))
        assert proc.returncode == 0, proc.stderr

    def test_stdin_failure_lists_errors_and_names_the_template(self):
        proc = self._run("--stdin", stdin="## Summary\n\nx\n")
        assert proc.returncode == 1
        assert "missing section: ## Checklist" in proc.stderr
        assert ".github/pull_request_template.md" in proc.stderr

    def test_env_body_and_author(self, tmp_path):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--body-env", "PR_BODY", "--author", "dependabot[bot]"],
            env={"PR_BODY": "", "PATH": "/usr/bin:/bin"},
            capture_output=True, text=True, timeout=30,
        )
        assert proc.returncode == 0, proc.stderr
        assert "exempt" in proc.stdout

    def test_missing_body_file_is_an_error(self, tmp_path):
        proc = self._run("--body-file", str(tmp_path / "nope.md"))
        assert proc.returncode == 2

    def test_runs_on_the_stdlib_only(self):
        # CI runs it with the runner's bare python3; the nightly gh guard
        # runs it with `python -I` from a root-owned lib directory.
        tree = SCRIPT.read_text(encoding="utf-8")
        imports = set(re.findall(r"^(?:from|import) ([a-zA-Z_]+)", tree, re.M))
        assert imports <= {"__future__", "argparse", "json", "os", "re", "shlex", "sys", "pathlib"}


def _hook(command: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    if cwd:
        payload["cwd"] = str(cwd)
    return subprocess.run(
        [sys.executable, str(SCRIPT), "hook"],
        input=json.dumps(payload), capture_output=True, text=True, timeout=30,
    )


class TestClaudeCodeHook:
    def test_unrelated_command_passes(self):
        assert _hook("git status").returncode == 0

    def test_heredoc_body_that_follows_the_template_passes(self):
        cmd = f"gh pr create --title t --body \"$(cat <<'EOF'\n{GOOD}EOF\n)\""
        proc = _hook(cmd)
        assert proc.returncode == 0, proc.stderr

    def test_heredoc_body_that_skips_the_template_is_blocked(self):
        cmd = "gh pr create --title t --body \"$(cat <<'EOF'\n## Summary\n\nx\nEOF\n)\""
        proc = _hook(cmd)
        assert proc.returncode == 2
        assert "missing section: ## Checklist" in proc.stderr

    def test_inline_body_is_checked(self):
        proc = _hook("git push -u origin x && gh pr create --base main --title t --body 'fixed it'")
        assert proc.returncode == 2
        assert "missing section: ## Summary" in proc.stderr

    def test_body_file_is_read_relative_to_cwd(self, tmp_path):
        (tmp_path / "pr.md").write_text(GOOD, encoding="utf-8")
        assert _hook("gh pr create --title t --body-file pr.md", cwd=tmp_path).returncode == 0
        (tmp_path / "bad.md").write_text("nope\n", encoding="utf-8")
        assert _hook("gh pr create --title t -F bad.md", cwd=tmp_path).returncode == 2

    def test_pr_edit_body_is_checked(self):
        assert _hook("gh pr edit 12 --body 'short'").returncode == 2

    def test_pr_edit_without_a_body_passes(self):
        assert _hook("gh pr edit 12 --add-label factory").returncode == 0

    def test_fill_is_blocked(self):
        proc = _hook("gh pr create --fill")
        assert proc.returncode == 2
        assert "--fill" in proc.stderr

    def test_unresolvable_body_is_left_to_ci(self):
        assert _hook('gh pr create --title t --body "$BODY"').returncode == 0

    def test_malformed_payload_never_blocks(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "hook"],
            input="not json", capture_output=True, text=True, timeout=30,
        )
        assert proc.returncode == 0

    def test_project_settings_register_the_hook(self):
        settings = json.loads((REPO / ".claude" / "settings.json").read_text(encoding="utf-8"))
        hooks = settings["hooks"]["PreToolUse"]
        commands = [
            h["command"] for entry in hooks if entry.get("matcher") == "Bash"
            for h in entry["hooks"]
        ]
        assert any("scripts/ci/check_pr_body.py" in c and c.rstrip().endswith(" hook") for c in commands)


class TestWorkflow:
    def _wf(self) -> dict:
        return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)

    def test_runs_on_every_body_change(self):
        on = self._wf()["on"]
        types = set(on["pull_request"]["types"])
        assert {"opened", "edited", "synchronize", "reopened", "ready_for_review"} <= types

    def test_job_name_is_stable_for_branch_protection(self):
        jobs = self._wf()["jobs"]
        assert jobs["pr-body"]["name"] == "PR body template"

    def test_body_reaches_the_shell_only_through_env(self):
        job = self._wf()["jobs"]["pr-body"]
        step = next(s for s in job["steps"] if "check_pr_body.py" in s.get("run", ""))
        assert step["env"]["PR_BODY"] == "${{ github.event.pull_request.body }}"
        assert step["env"]["PR_AUTHOR"] == "${{ github.event.pull_request.user.login }}"
        assert "${{" not in step["run"]
        assert '--body-env PR_BODY' in step["run"]
        assert '--author "$PR_AUTHOR"' in step["run"]

    def test_read_only_token_and_pinned_checkout(self):
        wf = self._wf()
        assert wf["permissions"] == {"contents": "read"}
        job = wf["jobs"]["pr-body"]
        assert job["timeout-minutes"]
        uses = [s["uses"] for s in job["steps"] if "uses" in s]
        assert uses == ["actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"]
