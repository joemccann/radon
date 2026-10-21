"""Nightly GitHub ISSUE comments: the security dead-man vs agent three-section.

The loops post to rolling issues (security #204, testing #83, reliability
#81, CI performance #196, documentation #202). Every loop runs on
scripts/runner/run_loop.sh; for security and DeepSec the root-owned post-run
hook (scripts/runner/hooks/security_post.sh) posts the machine-readable
phase line. Non-security agents still write the three-section update. The
hook creates the issue once with a timeless rolling-dead-man description; run
history stays in comments.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import nightly_issue_format as nif  # noqa: E402


HOOK = REPO / "scripts" / "runner" / "hooks" / "security_post.sh"
WRAPPERS = [HOOK]
SKILLS = [
    REPO / ".claude" / "runner-prompts" / "security.md",
]
HEADINGS = (
    "**Issue discovered**",
    "**What was done to fix it**",
    "**Next**",
)


def _uncommented(path: Path) -> str:
    return "\n".join(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


def _format_issue_body_fn(wrapper: Path) -> str:
    body = _uncommented(wrapper)
    start = body.index("_format_issue_body() {")
    end = body.index("\nreport() {", start)
    return body[start:end]


def _sanitize_issue_text_fn(wrapper: Path) -> str:
    body = _uncommented(wrapper)
    start = body.index("_sanitize_issue_text() {")
    end = body.index("\n_format_issue_body() {", start)
    return body[start:end]


def _secret_detail() -> str:
    # Runtime-join so gitleaks literal-tws-credential-assignment never
    # sees a contiguous TWS_ + PASSWORD assignment in this file.
    return (
        "Reached /api/orders/place as user radontrader01 via "
        "scripts/api/server.py:412; "
        + "TWS_"
        + "PASSWORD"
        + "="
        + "Hq7notreal and "
        "ops@radon.run saw the dump at https://app.radon.run/admin"
    )


class TestNoRunYetBody:
    def test_empty_issue_uses_the_same_three_headings(self):
        body = nif.no_run_yet_body()
        for heading in HEADINGS:
            assert heading in body, body
        assert "No run yet." in body
        assert "Nothing this run." in body
        assert "Waiting for the first nightly cycle." in body

    def test_no_run_yet_does_not_point_at_a_machine_log(self):
        body = nif.no_run_yet_body()
        assert "on the runner" not in body
        assert "log:" not in body.lower()
        assert "Rolling dead-man" not in body


class TestPhaseCommentShape:
    def test_ok_names_the_outcome_and_closes_with_green_deployment(self):
        body = nif.format_phase_comment(
            phase="audit", status="OK", detail="ledger appended, PR opened"
        )
        assert body.startswith("**Issue discovered**\n")
        assert "Nothing went wrong this audit phase." in body
        assert "ledger appended, PR opened" in body
        assert body.rstrip().endswith("Fixed with green deployment")
        assert "on the runner" not in body

    def test_ok_with_empty_detail_still_says_what_the_phase_did(self):
        body = nif.format_phase_comment(phase="remediate", status="OK", detail="")
        assert "The remediate phase completed." in body
        assert "Fixed with green deployment" in body

    def test_a_log_fence_in_detail_is_not_pasted_into_the_body(self):
        body = nif.format_phase_comment(
            phase="audit",
            status="OK",
            detail="```\ngitleaks: CANARY-7f3a matched in web/lib/secret.ts:12\n```",
        )
        assert "CANARY-7f3a" not in body
        assert "```" not in body
        assert "The audit phase completed." in body

    def test_incomplete_is_plain_language_and_asks_for_a_resume(self):
        status = (
            "INCOMPLETE (agent exited 0 without committing to the nightly branch)"
        )
        body = nif.format_phase_comment(
            phase="audit",
            status=status,
            detail="no ledger line / PR / gate rows",
        )
        assert status in body
        assert "Nothing this run." in body
        assert "Fixed with green deployment" not in body
        assert "next fire resumes" in body.lower() or "Do not read this as a finished run" in body

    def test_quota_exhaustion_names_the_top_up_url(self):
        body = nif.format_phase_comment(
            phase="audit",
            status="FAILED (all model quotas exhausted; top up at claude.ai/settings/usage)",
            detail="every model rung on the ladder reported an exhausted subscription quota",
        )
        assert "all model quotas exhausted" in body
        assert "claude.ai/settings/usage" in body
        assert "on the runner" not in body

    def test_lock_held_keeps_the_pid(self):
        body = nif.format_phase_comment(
            phase="prologue",
            status="REFUSED (lock held)",
            detail="another weekend run owns /tmp/clone (pid 4242); if no cycle is running, the recorded pid was reused — remove the lock",
        )
        assert "4242" in body
        assert "REFUSED" in body
        assert "Nothing this run." in body

    def test_incomplete_quota_exhaustion_asks_for_a_top_up_not_a_generic_resume(self):
        body = nif.format_phase_comment(
            phase="audit",
            status="INCOMPLETE (all model quotas exhausted; top up at claude.ai/settings/usage)",
            detail="the audited SHA was NOT advanced",
        )
        assert nif.QUOTA_NEXT in body
        assert nif.RESUME_NEXT not in body


class TestCiBuildTimeSavings:
    """#196 deliver/report: every time-saving fix cites before/after/%."""

    HEADER = "| Job | Before | After | % change |"

    def test_measured_after_reports_percent_change_negative_is_faster(self):
        table = nif.format_ci_build_time_savings(
            [
                {
                    "job": "mixed warm p50",
                    "before_secs": 238,
                    "after_secs": 218,
                }
            ]
        )
        assert "**CI build time**" in table
        assert "(after - before) / before * 100" in table
        assert "negative = faster" in table
        assert self.HEADER in table
        assert "| mixed warm p50 | 238s | 218s | -8.4% |" in table
        assert "TBD" not in table

    def test_multiple_jobs_each_get_a_row(self):
        table = nif.format_ci_build_time_savings(
            [
                {"job": "e2e wall", "before_secs": 400, "after_secs": 320},
                {"job": "full gate p50", "before_secs": 238, "after_secs": 218},
            ]
        )
        assert "| e2e wall | 400s | 320s | -20.0% |" in table
        assert "| full gate p50 | 238s | 218s | -8.4% |" in table

    def test_validating_after_is_pending_and_percent_is_tbd(self):
        table = nif.format_ci_build_time_savings(
            [
                {
                    "job": "mixed warm p50",
                    "before_secs": 238,
                    "after_status": "VALIDATING",
                    "after_samples": 2,
                    "required_samples": 5,
                }
            ]
        )
        assert "| mixed warm p50 | 238s | pending (VALIDATING, 2/5 samples) | TBD until 5 samples |" in table
        assert "-8.4%" not in table

    def test_missing_after_does_not_invent_a_time(self):
        table = nif.format_ci_build_time_savings(
            [{"job": "deploy", "before_secs": 88}]
        )
        assert "| deploy | 88s | pending | TBD until 5 samples |" in table
        assert "88s |" in table
        # After column is pending, not a fabricated second duration.
        assert "| deploy | 88s | 88s |" not in table

    def test_insufficient_sample_uses_the_status_and_keeps_before(self):
        table = nif.format_ci_build_time_savings(
            [
                {
                    "job": "python gate",
                    "before_secs": 123,
                    "after_status": "INSUFFICIENT_SAMPLE",
                    "after_samples": 1,
                    "required_samples": 5,
                }
            ]
        )
        assert "| python gate | 123s |" in table
        assert "pending (INSUFFICIENT_SAMPLE, 1/5 samples)" in table
        assert "TBD until 5 samples" in table

    def test_zero_or_missing_before_is_refused(self):
        with pytest.raises(ValueError, match="before"):
            nif.format_ci_build_time_savings(
                [{"job": "mixed warm p50", "before_secs": 0, "after_secs": 10}]
            )
        with pytest.raises(ValueError, match="before"):
            nif.format_ci_build_time_savings([{"job": "mixed warm p50"}])

    def test_empty_rows_are_refused(self):
        with pytest.raises(ValueError, match="row"):
            nif.format_ci_build_time_savings([])

    def test_format_body_appends_the_table_to_what_was_done(self):
        table = nif.format_ci_build_time_savings(
            [{"job": "mixed warm p50", "before_secs": 238, "after_secs": 218}]
        )
        body = nif.format_body(
            "Python gate tail stretched the mixed p50.",
            "Landed CIP-001 on the dated branch.",
            None,
            ci_time_savings=table,
        )
        done = body.split("**What was done to fix it**", 1)[1].split("**Next**", 1)[0]
        assert "Landed CIP-001 on the dated branch." in done
        assert self.HEADER in done
        assert "| mixed warm p50 | 238s | 218s | -8.4% |" in done

    def test_cli_emits_the_table(self):
        proc = subprocess.run(
            [
                sys.executable,
                str(REPO / "scripts" / "nightly_issue_format.py"),
                "ci-time-savings",
                "--row",
                json.dumps(
                    {"job": "mixed warm p50", "before_secs": 238, "after_secs": 218}
                ),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        assert self.HEADER in proc.stdout
        assert "| mixed warm p50 | 238s | 218s | -8.4% |" in proc.stdout


class TestSecuritySanitize:
    def test_the_write_up_stays_on_the_issue_not_a_log_pointer(self):
        body = nif.format_phase_comment(
            phase="audit",
            status="OK",
            detail="sanitized: 0 verified findings, 2 OPERATOR_REQUIRED (DeepSec workspace, Claude Security plugin)",
            sanitize=True,
        )
        assert "0 verified findings" in body
        assert "OPERATOR_REQUIRED" in body
        assert "on the runner" not in body
        assert "private security run dir" not in body
        assert "archive" not in body.lower() or "private archive" not in body.lower()

    def test_routes_file_attack_paths_secrets_and_accounts_are_stripped(self):
        raw = _secret_detail()
        body = nif.format_phase_comment(
            phase="audit", status="OK", detail=raw, sanitize=True
        )
        assert "/api/orders/place" not in body
        assert "scripts/api/server.py:412" not in body
        assert "Hq7notreal" not in body
        assert "radontrader01" not in body
        assert "ops@radon.run" not in body
        assert "app.radon.run" not in body
        for heading in HEADINGS:
            assert heading in body

    def test_operator_usage_url_survives_sanitize(self):
        body = nif.format_phase_comment(
            phase="audit",
            status="INCOMPLETE (all model quotas exhausted; top up at claude.ai/settings/usage)",
            detail="the audited SHA was NOT advanced",
            sanitize=True,
        )
        assert "claude.ai/settings/usage" in body
        assert "NOT advanced" in body

    def test_check_the_runner_is_stripped(self):
        body = nif.format_phase_comment(
            phase="prologue",
            status="CRASHED (exit 1)",
            detail="wrapper died before the agent finished — check the runner",
            sanitize=True,
        )
        assert "check the runner" not in body
        assert "on the runner" not in body

    def test_filesystem_roots_are_not_app_routes(self):
        paths = (
            "/Users/joe/radon-weekend/radon-security",
            "/Users/joe/radon-weekend/radon-security/.weekend-runner.lock",
            "/tmp/clone",
            "/tmp/clone/.weekend-runner.lock",
            "/home/runner/radon-weekend/radon-security",
            "/private/tmp/clone/.weekend-runner.lock",
            "/var/folders/xx/clone",
            "/opt/homebrew/bin/claude",
        )
        for path in paths:
            body = nif.format_phase_comment(
                phase="prologue",
                status="REFUSED (lock held)",
                detail=f"another weekend run owns {path} (pid 4242); remove {path}",
                sanitize=True,
            )
            assert path in body, (path, body)
            assert "4242" in body

    def test_cli_sanitize_matches_the_function(self):
        proc = subprocess.run(
            [
                sys.executable,
                str(REPO / "scripts" / "nightly_issue_format.py"),
                "phase",
                "--phase",
                "audit",
                "--status",
                "OK",
                "--detail",
                "found /api/admin/stop in web/app/api/x/route.ts:8",
                "--sanitize",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        assert "**Issue discovered**" in proc.stdout
        assert "/api/admin/stop" not in proc.stdout
        assert "route.ts:8" not in proc.stdout


class TestWrappersAndSkillsUseTheTemplate:
    @pytest.mark.parametrize("wrapper", WRAPPERS, ids=lambda p: p.name)
    def test_wrapper_create_body_is_timeless_deadman_not_no_run_yet(self, wrapper: Path):
        body = _uncommented(wrapper)
        assert "Rolling dead-man" in body, wrapper.name
        assert "No run yet." not in body, wrapper.name
        assert "Waiting for the first nightly cycle." not in body, wrapper.name
        assert "DEADMAN_CREATE_BODY" in body, wrapper.name
        assert "NO_RUN_YET_BODY" not in wrapper.read_text(encoding="utf-8"), wrapper.name
        assert "gh issue edit" not in body, wrapper.name
        assert 'log: \\`${RUN_LOG##*/}\\` on the runner' not in body, wrapper.name
        assert "on the runner" not in body, wrapper.name
        assert "Sanitized status only" in body, wrapper.name

    @pytest.mark.parametrize("wrapper", WRAPPERS, ids=lambda p: p.name)
    def test_wrapper_report_calls_the_formatter(self, wrapper: Path):
        body = _uncommented(wrapper)
        assert "_format_issue_body" in body, wrapper.name
        fmt = _format_issue_body_fn(wrapper)
        assert "python3" not in fmt, wrapper.name

    @pytest.mark.parametrize("wrapper", WRAPPERS, ids=lambda p: p.name)
    def test_wrapper_deadman_comment_is_phase_status_not_three_section(self, wrapper: Path):
        fmt = _format_issue_body_fn(wrapper)
        assert "**Issue discovered**" not in fmt, wrapper.name
        assert "Fixed with green deployment" not in fmt, wrapper.name
        assert "Nothing went wrong this" not in fmt, wrapper.name
        assert "phase_status_body" in fmt, wrapper.name
        assert "'**%s** %s **%s**'" not in fmt, wrapper.name

    @pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.stem)
    def test_skill_pr_body_uses_the_pr_template_not_the_issue_headings(self, skill: Path):
        # Security agents write no issue comments; the only write-up they
        # author is the PR, which follows the one PR template.
        text = skill.read_text(encoding="utf-8")
        assert ".github/pull_request_template.md" in text, skill
        assert "**Issue discovered**" not in text, skill
        assert "Fixed with green deployment" in text, skill

    def test_security_skill_puts_the_write_up_on_the_github_issue(self):
        text = (
            REPO / ".claude" / "runner-prompts" / "security.md"
        ).read_text(encoding="utf-8")
        assert "private archive pointer" not in text
        assert re.search(r"runner posts the only public", text, re.I)
        assert "no routes" in text.lower() or "never a route" in text.lower()

    def test_security_skill_forbids_agent_gh_issue_writes(self):
        text = (
            REPO / ".claude" / "runner-prompts" / "security.md"
        ).read_text(encoding="utf-8")
        lowered = text.lower()
        assert "do not run `gh issue comment`" in lowered
        assert "gh issue create" in lowered
        assert "gh issue edit" in lowered
        assert "you do not author" in lowered or "cannot author" in lowered

    def test_other_skills_post_issue_comment_only(self):
        non_security = [
            skill for skill in SKILLS if skill.stem != "security"
        ]
        for skill in non_security:
            text = skill.read_text(encoding="utf-8")
            lowered = text.lower()
            assert "`gh issue comment`" in text, skill
            assert re.search(
                r"do not run `gh issue create` or `gh issue edit`", lowered
            ), skill
            assert re.search(r"do not patch\s+the\s+issue", lowered), skill
            assert re.search(r"not\s+the\s+only\s+commenter", lowered), skill
            assert "runner posts the only public" not in lowered, skill
            assert "do not run `gh issue comment`" not in lowered, skill

    def test_pr_title_body_generation_is_untouched_in_skills(self):
        # #229 owns PR title/body via github_pr_output.py. Issue comments are
        # this branch; do not retarget PR generation at nightly_issue_format.
        security = (
            REPO / ".claude" / "runner-prompts" / "security.md"
        ).read_text(encoding="utf-8")
        assert "scripts/github_pr_output.py" in security
        assert "nightly_issue_format.py" not in security
        assert "Title shape: `Security <YYYY-MM-DD>`" in security


class TestWrapperFormatterNeverExecsDiskPython:
    @pytest.mark.parametrize("wrapper", WRAPPERS, ids=lambda p: p.name)
    def test_format_issue_body_does_not_exec_python_or_a_snapshot(self, wrapper: Path):
        body = _uncommented(wrapper)
        fmt = _format_issue_body_fn(wrapper)
        assert "python3" not in fmt, wrapper.name
        assert "ISSUE_FMT" not in body, wrapper.name
        assert "_snap_issue_formatter" not in body, wrapper.name
        assert "nightly_issue_format.py" not in fmt, wrapper.name
        assert "_sanitize_issue_text" in body, wrapper.name

    @pytest.mark.parametrize("wrapper", WRAPPERS, ids=lambda p: p.name)
    def test_wrapper_deadman_interpolates_phase_and_status(self, wrapper: Path):
        fmt = _format_issue_body_fn(wrapper)
        assert "phase_status_body" in fmt, wrapper.name
        assert "'**%s** %s **%s**'" not in fmt, wrapper.name
        assert "**Issue discovered**" not in fmt, wrapper.name

    @pytest.mark.parametrize("wrapper", WRAPPERS, ids=lambda p: p.name)
    def test_crash_comment_has_no_machine_pointer(self, wrapper: Path):
        body = _uncommented(wrapper)
        assert "check the runner" not in body, wrapper.name
        assert "wrapper died before the agent finished — check the runner" not in wrapper.read_text(
            encoding="utf-8"
        )


class TestSanitizeUsesPinnedSed:
    @pytest.mark.parametrize("wrapper", WRAPPERS, ids=lambda p: p.name)
    def test_sanitize_does_not_exec_path_sed(self, wrapper: Path):
        fn = _sanitize_issue_text_fn(wrapper)
        assert "/usr/bin/sed" in fn, wrapper.name
        assert "| sed " not in fn, wrapper.name
        assert "|sed " not in fn, wrapper.name

    def test_a_planted_venv_sed_cannot_author_the_security_comment(self, tmp_path: Path):
        src = HOOK.read_text(encoding="utf-8")
        start = src.index("_sanitize_issue_text() {")
        end = src.index("\nreport() {", start)
        fns = _secret_ere_block(src) + src[start:end]
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        planted = bin_dir / "sed"
        planted.write_text("#!/bin/sh\necho PWNED_FROM_PATH_SED\n", encoding="utf-8")
        planted.chmod(planted.stat().st_mode | stat.S_IXUSR)
        script = tmp_path / "run.sh"
        script.write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            + _phase_status_preamble()
            + "ISSUE_SANITIZE=1\n"
            + fns
            + f"\n_format_issue_body audit OK {_secret_detail()!r}\n",
            encoding="utf-8",
        )
        proc = subprocess.run(
            ["/bin/bash", str(script)],
            env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin"},
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, proc.stderr
        assert "PWNED_FROM_PATH_SED" not in proc.stdout, proc.stdout
        assert "Hq7notreal" not in proc.stdout
        assert "exit=0" in proc.stdout
        assert "**Issue discovered**" not in proc.stdout


class TestSecurityBashFallbackSanitizes:
    RAW = _secret_detail()

    def test_issue_sanitize_redacts_the_bash_body(self, tmp_path: Path):
        src = HOOK.read_text(encoding="utf-8")
        start = src.index("_sanitize_issue_text() {")
        end = src.index("\nreport() {", start)
        fns = _secret_ere_block(src) + src[start:end]
        script = tmp_path / "run.sh"
        script.write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            + _phase_status_preamble()
            + "ISSUE_SANITIZE=1\n"
            + fns
            + f"\n_sanitize_issue_text {self.RAW!r}\n"
            + f"\n_format_issue_body audit OK {self.RAW!r}\n",
            encoding="utf-8",
        )
        proc = subprocess.run(
            ["/bin/bash", str(script)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, proc.stderr
        out = proc.stdout
        assert "exit=0" in out
        assert "[REDACTED]" in out
        assert "**Issue discovered**" not in out
        assert "/api/orders/place" not in out
        assert "scripts/api/server.py:412" not in out
        assert "Hq7notreal" not in out
        assert "radontrader01" not in out
        assert "ops@radon.run" not in out
        assert "app.radon.run" not in out


def test_this_file_does_not_contain_a_literal_tws_assignment():
    src = Path(__file__).read_text(encoding="utf-8")
    rule = re.compile(
        r"""\bTWS_(?:USERID|PASSWORD)[ \t]*=[ \t]*["']?[A-Za-z0-9][A-Za-z0-9_.$!@#%^&*+:/=-]{3,}["']?"""
    )
    assert rule.search(src) is None


# Synthetic credential literals BUILT at runtime so no well-known prefix sits
# in the tree as a contiguous secret-shaped literal. None is a credential.
def _credential_literals() -> dict[str, str]:
    return {
        "anthropic": "sk-" + "ant-" + "api03-" + "NOTAREALKEY" * 4,
        "stripe_live": "sk_" + "live_" + "NOTAREALKEY" + "0" * 12,
        "xai": "xai" + "-" + "NOTAREALKEY" + "0" * 12,
        "nvidia": "nvapi" + "-" + "NOTAREALKEY" + "0" * 12,
        "cerebras": "csk" + "-" + "NOTAREALKEY" + "0" * 12,
        "github_pat_classic": "ghp" + "_" + "NOTAREALTOKEN" + "0" * 23,
        "github_pat_fine": "github" + "_pat_" + "NOTAREALTOKEN" + "0" * 30,
        "slack_bot": "xox" + "b-" + "0000000000-" + "NOTAREALTOKEN",
        "aws_access_key": "AKIA" + "NOTAREALKEY00000",
        "jwt": "eyJ" + "hbGciOiJub25lIn0" + "." + "eyJ" + "zdWIiOiJub3RyZWFsIn0"
        + "." + "NOT-A-REAL-SIG_123",
    }


class TestSanitizeCredentialLiterals:
    @pytest.mark.parametrize("kind", sorted(_credential_literals()))
    def test_bare_credential_literal_is_redacted(self, kind: str):
        literal = _credential_literals()[kind]
        out = nif.sanitize(f"gitleaks matched {literal} in the scanner output")
        assert literal not in out, (kind, out)
        assert "[REDACTED]" in out
        assert "gitleaks matched" in out

    def test_ordinary_prose_and_shas_are_untouched(self):
        text = (
            "Nothing went wrong this audit phase. The audited SHA "
            "db25990d3c1e4b7a9f0e2d6c8b1a5f4e3d2c1b0a did not advance; "
            "short SHA 1f04011f. Ask about the key rotation policy."
        )
        assert nif.sanitize(text) == text


class TestSecurityBashSanitizesCredentialLiterals:
    def test_bash_sanitizer_redacts_bare_credential_literals(self, tmp_path: Path):
        src = HOOK.read_text(encoding="utf-8")
        start = src.index("_sanitize_issue_text() {")
        end = src.index("\nreport() {", start)
        fns = _secret_ere_block(src) + src[start:end]
        literals = _credential_literals()
        detail = "scanner tail: " + " ".join(literals.values()) + " sha 1f04011f"
        script = tmp_path / "run.sh"
        script.write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            + _phase_status_preamble()
            + "ISSUE_SANITIZE=1\n"
            + fns
            + f"\n_sanitize_issue_text {detail!r}\n"
            + f"\n_format_issue_body audit OK {detail!r}\n",
            encoding="utf-8",
        )
        proc = subprocess.run(
            ["/bin/bash", str(script)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, proc.stderr
        for kind, literal in literals.items():
            assert literal not in proc.stdout, (kind, proc.stdout)
        assert "[REDACTED]" in proc.stdout
        assert "1f04011f" in proc.stdout
        assert "scanner tail:" in proc.stdout


_QUOTED_SECRETS = {
    "json_key": ('{"PUSHOVER_TOKEN": "qz7NOTREAL000"}', "qz7NOTREAL000"),
    "json_key_spaced": ('"db_password" : "two words NOTREAL"', "words NOTREAL"),
    "single_quoted_value": ("SERVICE_SECRET='kept apart NOTREAL'", "apart NOTREAL"),
    "basic_scheme": ("Authorization: Basic Zm9vOk5PVFJFQUw=", "Zm9vOk5PVFJFQUw="),
    "token_scheme": ("AUTH_HEADER=Token qz8NOTREAL111", "qz8NOTREAL111"),
    "hyphenated_header": ("X-API-Key: qz9NOTREAL222", "qz9NOTREAL222"),
    "hyphenated_lower": ("api-key=qz0NOTREAL333", "qz0NOTREAL333"),
}


def _phase_status_preamble() -> str:
    lib = (REPO / "scripts" / "runner" / "phase_status.sh").read_text(encoding="utf-8")
    return (
        "LOOP=security\nPHASE_RC=0\nPHASE_AUDITED_SHA=\nPHASE_REFUSED=\n"
        "PHASE_KILLED=\nPHASE_HOOK_REQUIRED=\n" + lib + "\n"
    )


def _opaque_value() -> str:
    """An invented opaque credential value, assembled at runtime.

    Spelled in source it is a `generic-api-key` hit for gitleaks next to any
    recognisable key name, so the fixture is built from fragments instead.
    """
    return "abcdefgh" + "12345678" + "ijklmnop"


def _secret_ere_block(src: str) -> str:
    start = src.index("# Credential assignments")
    return src[start:src.index("_redact_secret_classes() {", start)]


class TestSanitizeQuotedSecrets:
    @pytest.mark.parametrize("kind", sorted(_QUOTED_SECRETS))
    def test_quoted_key_or_value_is_redacted(self, kind: str):
        text, secret = _QUOTED_SECRETS[kind]
        out = nif.sanitize(f"the fixture held {text} before the fix")
        assert secret not in out, (kind, out)
        assert "[REDACTED]" in out
        assert out.endswith("before the fix")

    @pytest.mark.parametrize("fn", ["_sanitize_issue_text", "_redact_secret_classes"])
    def test_bash_sanitizers_redact_quoted_secrets(self, tmp_path: Path, fn: str):
        src = HOOK.read_text(encoding="utf-8")
        start = src.index(f"{fn}() {{")
        body = _secret_ere_block(src) + src[start:src.index("\n}\n", start) + 3]
        text = " ; ".join(t for t, _ in _QUOTED_SECRETS.values())
        call = (f"_sanitize_issue_text {shlex.quote(text)}" if fn == "_sanitize_issue_text"
                else f"printf '%s' {shlex.quote(text)} | _redact_secret_classes")
        script = tmp_path / "run.sh"
        script.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body + "\n" + call + "\n",
                          encoding="utf-8")
        proc = subprocess.run(["/bin/bash", str(script)], capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, proc.stderr
        for kind, (_, secret) in _QUOTED_SECRETS.items():
            assert secret not in proc.stdout, (kind, proc.stdout)


class TestSanitizeClaudeUrls:
    @pytest.mark.parametrize("url", [
        "https://claude.ai/chat/0000notreal", "https://www.claude.ai/share/0000notreal",
        "https://claude.ai/settings/usage?code=0000notreal", "https://claude.ai/x?code=0000notreal",
    ])
    def test_only_the_usage_page_survives(self, url: str):
        out = nif.sanitize(f"see {url} for details")
        assert "0000notreal" not in out, out
        assert "[REDACTED]" in out

    @pytest.mark.parametrize("text", [
        "top up at https://claude.ai/settings/usage.", "top up at claude.ai/settings/usage",
    ])
    def test_usage_page_is_kept(self, text: str):
        assert "claude.ai/settings/usage" in nif.sanitize(text)

    _SUFFIXED = [
        "https://claude.ai/settings/usage.evil.example/x?code=0000notreal",
        "claude.ai/settings/usage.evil.example/0000notreal",
        "https://claude.ai/settings/usage)0000notreal",
        "claude.ai/settings/usage,0000notreal",
        "claude.ai/settings/usage?code=0000notreal",
        "claude.ai/settings/usage/0000notreal",
        "https://claude.ai/settings/usage'0000notreal",
    ]

    @pytest.mark.parametrize("url", _SUFFIXED)
    def test_suffix_after_the_usage_page_is_redacted(self, url: str):
        out = nif.sanitize(f"see {url} now")
        assert "0000notreal" not in out, out

    @pytest.mark.parametrize("text", [
        "top up at claude.ai/settings/usage, then retry",
        "(top up at https://claude.ai/settings/usage).",
        "top up at claude.ai/settings/usage!",
    ])
    def test_usage_page_with_sentence_punctuation_is_kept(self, text: str):
        assert "claude.ai/settings/usage" in nif.sanitize(text)

    def _bash(self, tmp_path: Path, text: str) -> str:
        src = HOOK.read_text(encoding="utf-8")
        start = src.index("_sanitize_issue_text() {")
        body = _secret_ere_block(src) + src[start:src.index("\n}\n", start) + 3]
        script = tmp_path / "run.sh"
        script.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body
                          + f"\n_sanitize_issue_text {shlex.quote(text)}\n", encoding="utf-8")
        proc = subprocess.run(["/bin/bash", str(script)], capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, proc.stderr
        return proc.stdout

    @pytest.mark.parametrize("url", _SUFFIXED)
    def test_bash_suffix_after_the_usage_page_is_redacted(self, tmp_path: Path, url: str):
        out = self._bash(tmp_path, f"see {url} now")
        assert "0000notreal" not in out, out

    @pytest.mark.parametrize("text", [
        "top up at claude.ai/settings/usage", "top up at https://claude.ai/settings/usage.",
        "top up at claude.ai/settings/usage, then retry",
    ])
    def test_bash_usage_page_is_kept(self, tmp_path: Path, text: str):
        assert "claude.ai/settings/usage" in self._bash(tmp_path, text)


class TestSanitizeNonHttpUris:
    @pytest.mark.parametrize("uri", [
        "postgres://svc:pw0notreal@db/app", "redis://:pw1notreal@cache:6379/0",
        "libsql://db.example/x?authToken=pw2notreal", "wss://relay.example/feed?key=pw3notreal",
    ])
    def test_credential_bearing_uri_is_redacted(self, uri: str):
        out = nif.sanitize(f"the url {uri} was set")
        assert "notreal" not in out, out
        assert out == "the url [REDACTED] was set"

    def test_bash_issue_sanitizer_redacts_non_http_uris(self, tmp_path: Path):
        src = HOOK.read_text(encoding="utf-8")
        start = src.index("_sanitize_issue_text() {")
        body = _secret_ere_block(src) + src[start:src.index("\n}\n", start) + 3]
        script = tmp_path / "run.sh"
        script.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body
                          + "\n_sanitize_issue_text 'a redis://:pw1notreal@cache:6379/0 b'\n", encoding="utf-8")
        proc = subprocess.run(["/bin/bash", str(script)], capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, proc.stderr
        assert "notreal" not in proc.stdout, proc.stdout


@pytest.mark.parametrize("route", [
    "/api/callback?code=qz1NOTREAL", "/orders/place#frag=qz2NOTREAL", "/api?code=qz3NOTREAL",
])
def test_route_query_and_fragment_are_redacted_with_the_route(route: str):
    out = nif.sanitize(f"the call {route} returned")
    assert "NOTREAL" not in out, out
    assert out == "the call [REDACTED] returned"


@pytest.mark.parametrize("route", [
    "/api/orders/[id]?code=qz4NOTREAL", "/api/orders/:id?code=qz5NOTREAL", "/api/orders/{id}#qz6NOTREAL",
])
def test_dynamic_route_segments_are_redacted_with_their_query(route: str):
    out = nif.sanitize(f"the call {route} returned")
    assert "NOTREAL" not in out and "[id]" not in out and ":id" not in out, out
    assert out == "the call [REDACTED] returned"


_SESSION_VALUES = [
    "Cookie: sessionid=qz7NOTREAL", "Set-Cookie: __session=qz8NOTREAL; Path=/",
    "SESSION_ID=qz9NOTREAL", "csrf=qzANOTREAL", "XSRF-COOKIE: qzBNOTREAL",
]


@pytest.mark.parametrize("text", _SESSION_VALUES)
def test_cookie_and_session_values_are_redacted(text: str):
    out = nif.sanitize(f"the request sent {text} upstream")
    assert "NOTREAL" not in out, out
    assert "[REDACTED]" in out


def test_session_prose_is_not_redacted():
    text = "the session ended cleanly: no cookie was read"
    assert nif.sanitize(text) == text


def test_bash_issue_sanitizer_redacts_dynamic_routes_and_session_values(tmp_path: Path):
    src = HOOK.read_text(encoding="utf-8")
    start = src.index("_sanitize_issue_text() {")
    body = _secret_ere_block(src) + src[start:src.index("\n}\n", start) + 3]
    text = " ; ".join(["/api/orders/[id]?code=qzCNOTREAL", "/admin/x/:id?k=qzDNOTREAL", *_SESSION_VALUES])
    script = tmp_path / "run.sh"
    script.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body
                      + f"\n_sanitize_issue_text {shlex.quote(text)}\n", encoding="utf-8")
    proc = subprocess.run(["/bin/bash", str(script)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert "NOTREAL" not in proc.stdout, proc.stdout


@pytest.mark.parametrize("header", ["Cookie", "Set-Cookie", "cOoKiE"])
def test_all_cookie_header_values_are_redacted_without_losing_the_next_line(header):
    text = f"{header}: first=qzFirstNOTREAL; opaque=qzSecondNOTREAL\nretry after review"
    out = nif.sanitize(text)
    assert "NOTREAL" not in out, out
    assert out == f"{header}: [REDACTED]\nretry after review"


@pytest.mark.parametrize("header", ["Cookie", "Set-Cookie", "cOoKiE"])
@pytest.mark.parametrize("fn", ["_sanitize_issue_text", "_redact_secret_classes"])
def test_both_bash_sinks_redact_complete_cookie_headers(tmp_path: Path, header: str, fn: str):
    src = HOOK.read_text(encoding="utf-8")
    start = src.index(f"{fn}() {{")
    body = _secret_ere_block(src) + src[start:src.index("\n}\n", start) + 3]
    text = f"{header}: first=qzFirstNOTREAL; opaque=qzSecondNOTREAL\nretry after review"
    call = (f"_sanitize_issue_text {shlex.quote(text)}" if fn == "_sanitize_issue_text"
            else f"printf '%s' {shlex.quote(text)} | _redact_secret_classes")
    script = tmp_path / "cookie.sh"
    script.write_text("#!/bin/bash\nset -euo pipefail\n" + body + "\n" + call + "\n")
    proc = subprocess.run(["/bin/bash", str(script)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert "NOTREAL" not in proc.stdout, proc.stdout
    # The public sink historically removes credential names as well as values;
    # the private sink retains the header name. Both keep the next diagnostic.
    expected = "[REDACTED]" if fn == "_sanitize_issue_text" else f"{header}: [REDACTED]"
    assert proc.stdout == f"{expected}\nretry after review"


@pytest.mark.parametrize("label", ["PRIVATE KEY", "RSA PRIVATE KEY", "EC PRIVATE KEY", "OPENSSH PRIVATE KEY"])
def test_private_key_blocks_are_redacted(label: str):
    block = (f"-----BEGIN {label}-----\nb3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEqzNOTREAL\n"
             f"AAAAC3NzaC1lZDI1NTE5AAAAIqzNOTREAL\n-----END {label}-----")
    out = nif.sanitize(f"the log showed\n{block}\nthen exited")
    assert "NOTREAL" not in out and "BEGIN" not in out, out
    assert out == "the log showed\n[REDACTED]\nthen exited"
    assert "NOTREAL" not in nif.sanitize(f"cut off -----BEGIN {label}-----\nqzNOTREAL")


def test_public_key_blocks_are_not_redacted():
    text = "-----BEGIN PUBLIC KEY-----\nMFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE\n-----END PUBLIC KEY-----"
    assert nif.sanitize(text) == text


def test_advisor_master_account_ids_are_redacted():
    assert nif.sanitize("allocations under F1234567 failed") == "allocations under [REDACTED] failed"
    assert nif.sanitize("commit f1234567 landed") == "commit f1234567 landed"


def _hook_redact(tmp_path: Path, text: str) -> str:
    """Run the private-report redactor from the hook in isolation."""
    src = HOOK.read_text(encoding="utf-8")
    start = src.index("_redact_secret_classes() {")
    body = _secret_ere_block(src) + src[start:src.index("\n}\n", start) + 3]
    script = tmp_path / "run.sh"
    script.write_text("#!/bin/bash\nset -euo pipefail\n" + body
                      + f"\nprintf '%s' {shlex.quote(text)} | _redact_secret_classes\n",
                      encoding="utf-8")
    proc = subprocess.run(["/bin/bash", str(script)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _key_marker(edge: str, label: str) -> str:
    """Assemble a PEM armour line at runtime.

    The repo contract is that no credential-shaped literal is spelled in
    source, even a synthetic one, so the secret scanner cannot trip on a
    fixture. `label` never names a real key and the body below is invented.
    """
    dashes = "-" * 5
    return f"{dashes}{edge} {label}{dashes}"


@pytest.mark.parametrize("label", ["PRIVATE KEY", "RSA PRIVATE KEY", "EC PRIVATE KEY", "OPENSSH PRIVATE KEY"])
def test_the_private_report_redactor_drops_private_key_blocks(tmp_path: Path, label: str):
    report = (f"the report showed\n{_key_marker('BEGIN', label)}\nQUJDZGVmNOTREALONE\n"
              f"QUJDZGVmNOTREALTWO\n{_key_marker('END', label)}\nthen exited")
    out = _hook_redact(tmp_path, report)
    assert "NOTREAL" not in out and "PRIVATE KEY" not in out, out
    assert out == "the report showed\n[REDACTED]\nthen exited"


def test_the_private_report_redactor_drops_an_unterminated_key_block(tmp_path: Path):
    block = _key_marker("BEGIN", "OPENSSH PRIVATE KEY")
    out = _hook_redact(tmp_path, f"cut off\n{block}\nQUJDNOTREAL")
    assert "NOTREAL" not in out, out


def test_the_private_report_redactor_keeps_ordinary_report_text(tmp_path: Path):
    report = ("| phase | sha |\n|---|---|\n| audit | 5bfadbb6 |\n\n"
              "`web/lib/secret.ts:12` leaks; see -----BEGIN PUBLIC KEY-----\nMFkwEwYHKoZIzj0CAQ\n"
              "-----END PUBLIC KEY-----\ndone")
    assert _hook_redact(tmp_path, report) == report
# --- DS sanitizer drift: rules the publication gate had and this one lacked ---
# Every value below is an invented placeholder. None is a credential or a real
# execution id.


class TestSanitizeExecutionIds:
    """The public sanitizer must drop the same execution ids the push gate
    refuses (scripts/ir_push_gate.py PRIVATE_ID_PATTERNS)."""

    @pytest.mark.parametrize("exec_id", [
        "0000abcd.0000ef01.01.02",  # dotted-hex IB exec id shape
        "1234567890123",            # long numeric Flex exec id shape
    ])
    def test_execution_id_is_redacted(self, exec_id: str):
        out = nif.sanitize(f"the fill trace {exec_id} did not reconcile")
        assert exec_id not in out, out
        assert "[REDACTED]" in out
        assert out.endswith("did not reconcile")

    def test_shas_pids_and_dates_are_not_execution_ids(self):
        text = (
            "Nothing went wrong this audit phase. The audited SHA "
            "db25990d3c1e4b7a9f0e2d6c8b1a5f4e3d2c1b0a did not advance; "
            "short SHA 1f04011f; another run owns the lock (pid 4242) "
            "since 20261020."
        )
        assert nif.sanitize(text) == text


class TestSanitizeEscapedQuoteValues:
    def test_value_with_an_escaped_quote_is_redacted_whole(self):
        secret = 'NOTREALaa\\"NOTREALbb'
        out = nif.sanitize(f'the fixture held API_KEY="{secret}" before the fix')
        assert "NOTREALbb" not in out, out
        assert "[REDACTED]" in out
        assert out.endswith("before the fix")

    def test_two_quoted_assignments_on_one_line_do_not_merge(self):
        out = nif.sanitize('A_TOKEN="NOTREAL1" and B_TOKEN="NOTREAL2" seen')
        assert "NOTREAL1" not in out and "NOTREAL2" not in out, out
        assert "and" in out and out.endswith("seen")


class TestSanitizeYamlBlockScalars:
    @pytest.mark.parametrize("indicator", ["|", ">", "|-", ">-", "|2-"])
    def test_block_scalar_value_is_redacted(self, indicator: str):
        text = (
            f"the config carried\ndb_password: {indicator}\n"
            "  NOTREALLINEONE\n  NOTREALLINETWO\nnext_step: resume\n"
        )
        out = nif.sanitize(text)
        assert "NOTREALLINEONE" not in out, out
        assert "NOTREALLINETWO" not in out, out
        assert "[REDACTED]" in out
        assert "next_step: resume" in out

    def test_a_sibling_key_at_the_same_indent_is_not_swallowed(self):
        text = (
            "outer:\n  api_key: |\n    NOTREALBLOCKVALUE\n"
            "  keep_me: ordinary prose\n"
        )
        out = nif.sanitize(text)
        assert "NOTREALBLOCKVALUE" not in out, out
        assert "keep_me: ordinary prose" in out, out


class TestSanitizeCommentedBlockScalars:
    """A block scalar hides its value on the following lines whether or not
    the header line is commented or bulleted, which a report excerpt often
    is. Every value below is an invented placeholder."""

    @pytest.mark.parametrize("lead", ["# ", "#", "- ", "  - ", "> "])
    def test_a_commented_or_bulleted_block_header_still_hides_its_body(self, lead: str):
        text = f"{lead}password: |\n{' ' * (len(lead) + 2)}NOTREALBLOCKVALUE1\n"
        text += f"{' ' * (len(lead) + 2)}NOTREALBLOCKVALUE2\ntail line\n"
        out = nif.sanitize(text)
        assert "NOTREALBLOCKVALUE" not in out, out
        assert out.rstrip("\n").endswith("tail line"), out

    def test_a_commented_sibling_key_at_the_same_indent_survives(self):
        text = "# password: |\n  NOTREALBLOCKVALUE3\n# keep_me: ordinary prose\n"
        out = nif.sanitize(text)
        assert "NOTREALBLOCKVALUE3" not in out, out
        assert "keep_me: ordinary prose" in out, out


class TestSanitizeBlockScalarTrailingComment:
    def test_a_comment_after_the_indicator_still_hides_the_body(self):
        out = nif.sanitize("password: | # note\n  NOTREALTRAILING1\ntail\n")
        assert "NOTREALTRAILING1" not in out, out
        assert out.rstrip("\n").endswith("tail"), out


class TestSanitizePwdNamedKeys:
    def test_an_underscore_pwd_assignment_is_redacted(self):
        out = nif.sanitize("the fixture held DB_PWD=NOTREALPWD1x9 before")
        assert "NOTREALPWD1x9" not in out, out
        assert out.endswith("before"), out

    def test_a_bare_working_directory_variable_is_not_redacted(self):
        text = "the hook ran with PWD=/Users/bot/work and exited"
        assert nif.sanitize(text) == text


class TestSanitizeKeyNameScanIsBounded:
    def test_repeated_credential_words_do_not_stall_sanitization(self):
        """Unbounded identifier runs on both sides of the credential-word
        alternation made the engine redistribute prefix and suffix at every
        offset: 0.0084s, 0.0336s, 0.1371s and 0.5519s for 250, 500, 1000 and
        2000 repetitions. PR bodies carry attacker-shaped source excerpts."""
        import subprocess
        import sys as _sys

        probe = (
            "import sys;"
            f"sys.path.insert(0, {str(REPO / 'scripts')!r});"
            "import nightly_issue_format as n;"
            "n.sanitize('TOKEN_' * 64_000 + '!')"
        )
        subprocess.run([_sys.executable, "-c", probe], check=True, timeout=20)


class TestSanitizeLongKeyPrefixes:
    """A bound on the identifier run BEFORE the credential word was an
    evasion: a key whose prefix exceeded it never matched and its value was
    published verbatim to a PUBLIC PR body (DS-2026-10-21-06). The canonical
    scrubber matches from the credential WORD onward instead, which keeps the
    work bounded with no length at which coverage stops. Values are invented."""

    @pytest.mark.parametrize("prefix_len", [8, 31, 32, 40, 80])
    def test_a_long_key_prefix_does_not_evade_redaction(self, prefix_len: int):
        key = "N" * prefix_len + "_TOKEN"
        value = _opaque_value()
        text = f"the excerpt held {key}={value} and stopped"
        out = nif.sanitize(text)
        assert value not in out, out
        assert out.endswith("and stopped"), out

    def test_an_ordinary_prefixed_key_keeps_its_name_in_the_output(self):
        # Key and value are assembled at runtime: the repo contract is that no
        # credential-shaped literal is spelled in source, even a synthetic one,
        # so the secret scanner cannot trip on a fixture.
        key, value = "ANTHROPIC" + "_API_KEY", _opaque_value()
        out = nif.sanitize(f"set {key}={value} here")
        assert value not in out, out
        assert "API_KEY" in out, out


class TestSanitizeTripleDelimitedValues:
    """A triple-delimited value (Python or TOML) was matched as an EMPTY
    two-quote value, so the credential on the FOLLOWING lines was published
    to a PUBLIC body (DS-2026-10-20-13). Values are invented."""

    @pytest.mark.parametrize(
        "key,sep,delim",
        [
            ("TOKEN", " = ", '"""'),
            ("DB_PASSWORD", " = ", "'''"),
            ("api_key", " = ", '"""'),
            ("UW_TOKEN", ": ", '"""'),
        ],
    )
    def test_triple_delimited_value_is_redacted(self, key, sep, delim):
        text = f"the excerpt had\n{key}{sep}{delim}\nqzNOTREALopaque1234\n{delim}\nand stopped"
        out = nif.sanitize(text)
        assert "NOTREAL" not in out, out
        assert out.endswith("and stopped"), out

    def test_a_following_key_survives_the_triple_delimited_value(self):
        text = 'TOKEN = """\nqzNOTREALopaque1234\n"""\nnext_label = keepme\n'
        out = nif.sanitize(text)
        assert "NOTREAL" not in out, out
        assert "keepme" in out, out

    def test_single_line_and_empty_quoted_values_are_unchanged(self):
        assert nif.sanitize('TOKEN = ""') == "TOKEN = [REDACTED]"
        assert nif.sanitize('TOKEN = "qzNOTREALopaque1234"') == "TOKEN = [REDACTED]"


class TestSanitizeTokenScanIsBounded:
    """The URL, email and file-location rules had unbounded runs with
    OVERLAPPING candidate starts: a long hyphen- or dot-separated token with no
    delimiter was rescanned at every internal word boundary, so the scan was
    quadratic (measured 0.023s, 0.085s and 0.340s for 2k, 4k and 8k URL-rule
    repetitions, and 0.081s, 0.315s and 1.280s for the file rule). sanitize()
    has no input-size bound and contributor-shaped excerpts reach it through
    github_pr_output (DS-2026-10-20-12). The cure may not shorten coverage, so
    the coverage tests below are part of this guard."""

    @pytest.mark.parametrize("filler", ["a-", "a.", "a/"])
    def test_a_pathological_token_does_not_stall_sanitization(self, filler: str):
        import subprocess
        import sys as _sys

        probe = (
            "import sys;"
            f"sys.path.insert(0, {str(REPO / 'scripts')!r});"
            "import nightly_issue_format as n;"
            f"n.sanitize({filler!r} * 64_000 + '!')"
        )
        # One candidate start per token needs well under a second here. The
        # overlapping-start form needed minutes on this input.
        subprocess.run([_sys.executable, "-c", probe], check=True, timeout=20)

    @pytest.mark.parametrize(
        "text,leak",
        [
            ("fetched https://internal.example/path?q=1 twice", "internal.example"),
            ("fetched postgres://user@db.internal/radon twice", "db.internal"),
            ("mail went to someone@example.com twice", "someone@example.com"),
            ("mail went to first.last+tag@sub.example.co.uk twice", "first.last"),
            ("it broke at scripts/api/app.py:4120 twice", "app.py:4120"),
            ("it broke at web/lib/radonApi.ts:88 twice", "radonApi.ts:88"),
        ],
    )
    def test_coverage_is_not_narrowed_by_the_cost_fix(self, text: str, leak: str):
        out = nif.sanitize(text)
        assert leak not in out, out
        assert nif.REDACTED in out, out
        assert out.endswith("twice"), out
