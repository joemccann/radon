"""Nightly GitHub ISSUE comments: wrapper dead-man vs agent three-section.

The five loops post to rolling issues (security #204, testing #83, reliability
#81, CI performance #196, documentation #202). Wrapper runner-health comments
are a PHASE STAMP status dead-man line. Non-security agents still write the
three-section update. Wrappers create the issue once with a timeless
rolling-dead-man description; run history stays in comments.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import nightly_issue_format as nif  # noqa: E402

WRAPPERS = [
    REPO / "scripts" / "reliability_weekend.sh",
    REPO / "scripts" / "testing_weekend.sh",
    REPO / "scripts" / "ci_performance_nightly.sh",
    REPO / "scripts" / "documentation_nightly.sh",
    REPO / "scripts" / "security_nightly.sh",
]
SKILLS = [
    REPO / ".claude" / "skills" / "reliability-weekend" / "SKILL.md",
    REPO / ".claude" / "skills" / "testing-weekend" / "SKILL.md",
    REPO / ".claude" / "skills" / "ci-performance" / "SKILL.md",
    REPO / ".claude" / "skills" / "documentation-nightly" / "SKILL.md",
    REPO / ".claude" / "skills" / "security-nightly" / "SKILL.md",
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


def _notify_cred_fn(wrapper: Path) -> str:
    body = _uncommented(wrapper)
    start = body.index("_notify_cred() {")
    end = body.index("\n_notify_curl() {", start)
    return body[start:end]


def _notify_curl_fn(wrapper: Path) -> str:
    body = _uncommented(wrapper)
    start = body.index("_notify_curl() {")
    end = body.index("\nnotify_phase() {", start)
    return body[start:end]


def _notify_phase_fn(wrapper: Path) -> str:
    body = _uncommented(wrapper)
    start = body.index("notify_phase() {")
    end = body.index("\n_sanitize_issue_text() {", start)
    return body[start:end]


def _sanitize_issue_text_fn(wrapper: Path) -> str:
    body = _uncommented(wrapper)
    start = body.index("_sanitize_issue_text() {")
    end = body.index("\n_format_issue_body() {", start)
    return body[start:end]


def _curl_log_stub(log: Path) -> str:
    """Log argv, then dump --config file or stdin (`--config -`)."""
    return (
        "#!/bin/bash\n"
        f'printf "%s\\n" "$*" >> "{log}"\n'
        "i=1\n"
        'while [ "$i" -le "$#" ]; do\n'
        '  eval "arg=\\${$i}"\n'
        '  if [ "$arg" = "--config" ] || [ "$arg" = "-K" ]; then\n'
        "    i=$((i + 1))\n"
        '    eval "cfg=\\${$i}"\n'
        f'    if [ "$cfg" = "-" ]; then cat >> "{log}"\n'
        f'    elif [ -f "$cfg" ]; then cat "$cfg" >> "{log}"; fi\n'
        "  fi\n"
        "  i=$((i + 1))\n"
        "done\n"
        "exit 0\n"
    )


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
        if wrapper.name == "security_nightly.sh":
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
        assert "'**%s** %s **%s**'" in fmt, wrapper.name

    @pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.parent.name)
    def test_skill_tells_the_agent_to_use_the_three_headings(self, skill: Path):
        text = skill.read_text(encoding="utf-8")
        assert "**Issue discovered**" in text, skill
        assert "**What was done to fix it**" in text, skill
        assert "Fixed with green deployment" in text, skill

    def test_security_skill_puts_the_write_up_on_the_github_issue(self):
        text = (
            REPO / ".claude" / "skills" / "security-nightly" / "SKILL.md"
        ).read_text(encoding="utf-8")
        assert "private archive pointer" not in text
        assert re.search(r"wrapper posts the only public", text, re.I)
        assert "no routes" in text.lower() or "never a route" in text.lower()

    def test_security_skill_forbids_agent_gh_issue_writes(self):
        text = (
            REPO / ".claude" / "skills" / "security-nightly" / "SKILL.md"
        ).read_text(encoding="utf-8")
        lowered = text.lower()
        assert "do not run `gh issue comment`" in lowered
        assert "gh issue create" in lowered
        assert "gh issue edit" in lowered
        assert "you do not author" in lowered or "cannot author" in lowered

    def test_other_skills_may_keep_agent_owned_issue_write_ups(self):
        testing = (
            REPO / ".claude" / "skills" / "testing-weekend" / "SKILL.md"
        ).read_text(encoding="utf-8")
        assert "do not run `gh issue comment`" not in testing.lower()

    def test_pr_title_body_generation_is_untouched_in_skills(self):
        # #229 owns PR title/body via github_pr_output.py. Issue comments are
        # this branch; do not retarget PR generation at nightly_issue_format.
        testing = (
            REPO / ".claude" / "skills" / "testing-weekend" / "SKILL.md"
        ).read_text(encoding="utf-8")
        reliability = (
            REPO / ".claude" / "skills" / "reliability-weekend" / "SKILL.md"
        ).read_text(encoding="utf-8")
        security = (
            REPO / ".claude" / "skills" / "security-nightly" / "SKILL.md"
        ).read_text(encoding="utf-8")
        for text in (testing, reliability, security):
            assert "scripts/github_pr_output.py" in text
            assert "nightly_issue_format.py" not in text
        assert "Title shape: `Testing <date>" in testing
        assert "Title shape: `Reliability <date>" in reliability
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
        assert "'**%s** %s **%s**'" in fmt, wrapper.name
        assert "**Issue discovered**" not in fmt, wrapper.name

    @pytest.mark.parametrize("wrapper", WRAPPERS, ids=lambda p: p.name)
    def test_crash_comment_has_no_machine_pointer(self, wrapper: Path):
        body = _uncommented(wrapper)
        assert "check the runner" not in body, wrapper.name
        assert "wrapper died before the agent finished — check the runner" not in wrapper.read_text(
            encoding="utf-8"
        )


class TestNotifyPhaseIsFenced:
    """Never exec python for Pushover. Clone/venv python3 and
    /usr/bin/python3 without -I import agent-writable WEEKEND_ROOT modules.
    Always _notify_curl via /usr/bin/curl."""

    @pytest.mark.parametrize("wrapper", WRAPPERS, ids=lambda p: p.name)
    def test_does_not_exec_clone_path_notify_via_path_python3(self, wrapper: Path):
        body = _uncommented(wrapper)
        notify = _notify_phase_fn(wrapper)
        assert 'python3 "$REPO/scripts/weekend_notify.py"' not in body, wrapper.name
        assert "python3 $REPO/scripts/weekend_notify.py" not in body, wrapper.name
        assert "python3" not in notify, wrapper.name

    @pytest.mark.parametrize("wrapper", WRAPPERS, ids=lambda p: p.name)
    def test_notify_phase_always_curls_and_never_execs_python(self, wrapper: Path):
        body = _uncommented(wrapper)
        notify = _notify_phase_fn(wrapper)
        curl = _notify_curl_fn(wrapper)
        assert "_notify_curl" in notify, wrapper.name
        assert "/usr/bin/curl" in body, wrapper.name
        assert "api.pushover.net" in body, wrapper.name
        assert "NOTIFY_SHA256" not in body, wrapper.name
        assert "_sha256_file" not in body, wrapper.name
        assert "/usr/bin/python3" not in notify, wrapper.name
        assert "weekend_notify.py" not in notify, wrapper.name
        assert "/usr/bin/tr" in curl, wrapper.name
        assert "| tr " not in curl, wrapper.name
        assert "|tr " not in curl, wrapper.name
        assert "--config" in curl, wrapper.name
        assert "--config -" in curl, wrapper.name
        assert "/usr/bin/mktemp" not in curl, wrapper.name
        assert "0600" not in curl, wrapper.name
        assert '--data-urlencode "token=${token}"' not in curl, wrapper.name
        assert '--data-urlencode "user=${user}"' not in curl, wrapper.name
        # -q must be argv[1], immediately before --config.
        assert "/usr/bin/curl -q --config -" in curl, wrapper.name
        invoke = [ln.strip() for ln in curl.splitlines() if "curl" in ln and "--config" in ln]
        assert invoke, wrapper.name
        first = invoke[-1].split()
        curl_i = next(i for i, tok in enumerate(first) if tok.endswith("curl") or "/curl" in tok)
        assert first[curl_i + 1] == "-q", (wrapper.name, first)
        assert first[curl_i + 2] == "--config", (wrapper.name, first)
        assert first[curl_i + 3] == "-", (wrapper.name, first)

    @pytest.mark.parametrize("wrapper", WRAPPERS, ids=lambda p: p.name)
    def test_gh_and_timeout_are_snapshotted_before_venv_path(self, wrapper: Path):
        body = _uncommented(wrapper)
        timeout_i = body.index('TIMEOUT_BIN="$(command -v timeout || true)"')
        gh_i = body.index('GH_BIN="$(command -v gh || true)"')
        path_i = body.index('export PATH="$VENV/bin:$PATH"')
        assert timeout_i < path_i, wrapper.name
        assert gh_i < path_i, wrapper.name
        assert 'net_bounded "$GH_BIN"' in body, wrapper.name
        assert "net_bounded gh " not in body, wrapper.name
        assert '"$TIMEOUT_BIN"' in body, wrapper.name

    @pytest.mark.parametrize("wrapper", WRAPPERS, ids=lambda p: p.name)
    def test_notify_cred_strips_cr_not_a_quoted_newline(self, wrapper: Path):
        cred = _notify_cred_fn(wrapper)
        assert r"${line%$'\r'}" in cred, wrapper.name
        assert r"${line%$'\n'}" not in cred, wrapper.name

    def test_notify_cred_reads_a_crlf_env_file(self, tmp_path: Path):
        src = (REPO / "scripts" / "security_nightly.sh").read_text(encoding="utf-8")
        start = src.index("_notify_cred() {")
        end = src.index("\n_notify_curl() {", start)
        envf = tmp_path / ".env"
        envf.write_bytes(b"PUSHOVER_TOKEN=tok-from-crlf\r\nPUSHOVER_USER=user-from-crlf\r\n")
        script = tmp_path / "run.sh"
        script.write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            f'WEEKEND_ROOT="{tmp_path}"\n'
            + src[start:end]
            + "\n_notify_cred PUSHOVER_TOKEN; printf '\\n'; _notify_cred PUSHOVER_USER; printf '\\n'\n",
            encoding="utf-8",
        )
        proc = subprocess.run(
            ["/bin/bash", str(script)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.splitlines() == ["tok-from-crlf", "user-from-crlf"], proc.stdout

    def test_rewritten_clone_notifier_is_not_execed(self, tmp_path: Path):
        clone = tmp_path / "clone"
        (clone / "scripts").mkdir(parents=True)
        (clone / "logs" / "security-nightly").mkdir(parents=True)
        wrapper = clone / "scripts" / "security_nightly.sh"
        shutil.copy2(REPO / "scripts" / "security_nightly.sh", wrapper)
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
        marker = tmp_path / "PWNED_NOTIFY"
        dotenv_marker = tmp_path / "PWNED_DOTENV"
        json_marker = tmp_path / "PWNED_JSON"
        (clone / "scripts" / "weekend_notify.py").write_text(
            f"open({str(marker)!r}, 'w').write('PWN')\n",
            encoding="utf-8",
        )
        (tmp_path / "dotenv.py").write_text(
            f"open({str(dotenv_marker)!r}, 'w').write('PWN')\n"
            "def dotenv_values(*_a, **_k):\n    return {}\n",
            encoding="utf-8",
        )
        (tmp_path / "json.py").write_text(
            f"open({str(json_marker)!r}, 'w').write('PWN')\n",
            encoding="utf-8",
        )
        for name in (".radon-weekend-runner", ".radon-security-runner"):
            (clone / name).write_text("", encoding="utf-8")
        (tmp_path / ".env").write_text(
            "PUSHOVER_USER=test-user\nPUSHOVER_TOKEN=test-token\n",
            encoding="utf-8",
        )

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        gh_log = tmp_path / "gh.log"
        py_log = tmp_path / "path-python3.log"
        curl_log = tmp_path / "curl.log"
        curl_stub = bin_dir / "curl"
        wrapper.write_text(
            wrapper.read_text(encoding="utf-8").replace("/usr/bin/curl", str(curl_stub)),
            encoding="utf-8",
        )
        for name, content in {
            "gh": (
                "#!/bin/sh\n"
                f'printf "%s\\n" "$*" >> "{gh_log}"\n'
                'if [ "$1 $2" = "issue list" ]; then echo 4242; fi\n'
                "exit 0\n"
            ),
            "git": "#!/bin/sh\nexit 0\n",
            "timeout": (
                "#!/bin/bash\n"
                "while [ $# -gt 0 ]; do\n"
                '  case "$1" in\n'
                "    -k|--kill-after) shift 2 ;;\n"
                "    *) shift; break ;;\n"
                "  esac\n"
                "done\n"
                'exec "$@"\n'
            ),
            "python3": (
                "#!/bin/sh\n"
                f'echo "$*" >> "{py_log}"\n'
                "exit 0\n"
            ),
            "curl": _curl_log_stub(curl_log),
            "claude": (
                "#!/bin/sh\n"
                "echo 'SECURITY-NIGHTLY PHASE COMPLETE: audit'\n"
                "exit 0\n"
            ),
        }.items():
            exe = bin_dir / name
            exe.write_text(content, encoding="utf-8")
            exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        proc = subprocess.run(
            ["/bin/bash", str(wrapper), "audit"],
            env={
                "PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
                "HOME": str(tmp_path / "home"),
                "RADON_WEEKEND_REPO": str(clone),
            },
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
        assert not marker.exists(), "leftover clone notifier must not run"
        assert not dotenv_marker.exists(), "WEEKEND_ROOT dotenv.py must not be imported"
        assert not json_marker.exists(), "WEEKEND_ROOT json.py must not be imported"
        py_calls = py_log.read_text(encoding="utf-8") if py_log.exists() else ""
        assert "weekend_notify.py" not in py_calls, py_calls
        assert py_calls == "", py_calls
        curl_calls = curl_log.read_text(encoding="utf-8") if curl_log.exists() else ""
        argv_line = curl_calls.splitlines()[0] if curl_calls.strip() else ""
        assert argv_line.split()[0] == "-q", argv_line
        assert "api.pushover.net" in curl_calls, curl_calls
        assert "title=radon security audit" in curl_calls, curl_calls
        assert "test-token" not in argv_line, argv_line
        assert "token=" not in argv_line, argv_line
        assert "user=" not in argv_line, argv_line

    def test_a_planted_venv_tr_cannot_skip_the_page(self, tmp_path: Path):
        clone = tmp_path / "clone"
        (clone / "scripts").mkdir(parents=True)
        (clone / "logs" / "security-nightly").mkdir(parents=True)
        wrapper = clone / "scripts" / "security_nightly.sh"
        shutil.copy2(REPO / "scripts" / "security_nightly.sh", wrapper)
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
        for name in (".radon-weekend-runner", ".radon-security-runner"):
            (clone / name).write_text("", encoding="utf-8")
        (tmp_path / ".env").write_text(
            "PUSHOVER_USER=test-user\nPUSHOVER_TOKEN=test-token\n",
            encoding="utf-8",
        )
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        gh_log = tmp_path / "gh.log"
        curl_log = tmp_path / "curl.log"
        tr_marker = tmp_path / "PWNED_TR"
        curl_stub = bin_dir / "curl"
        wrapper.write_text(
            wrapper.read_text(encoding="utf-8").replace("/usr/bin/curl", str(curl_stub)),
            encoding="utf-8",
        )
        for name, content in {
            "gh": (
                "#!/bin/sh\n"
                f'printf "%s\\n" "$*" >> "{gh_log}"\n'
                'if [ "$1 $2" = "issue list" ]; then echo 4242; fi\n'
                "exit 0\n"
            ),
            "git": "#!/bin/sh\nexit 0\n",
            "timeout": (
                "#!/bin/bash\n"
                "while [ $# -gt 0 ]; do\n"
                '  case "$1" in\n'
                "    -k|--kill-after) shift 2 ;;\n"
                "    *) shift; break ;;\n"
                "  esac\n"
                "done\n"
                'exec "$@"\n'
            ),
            "tr": (
                "#!/bin/sh\n"
                f"echo PWN > '{tr_marker}'\n"
                "exit 1\n"
            ),
            "curl": _curl_log_stub(curl_log),
            "claude": (
                "#!/bin/sh\n"
                "echo 'SECURITY-NIGHTLY PHASE COMPLETE: audit'\n"
                "exit 0\n"
            ),
        }.items():
            exe = bin_dir / name
            exe.write_text(content, encoding="utf-8")
            exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        proc = subprocess.run(
            ["/bin/bash", str(wrapper), "audit"],
            env={
                "PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
                "HOME": str(tmp_path / "home"),
                "RADON_WEEKEND_REPO": str(clone),
            },
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
        assert not tr_marker.exists(), "PATH tr must not run inside _notify_curl"
        curl_calls = curl_log.read_text(encoding="utf-8") if curl_log.exists() else ""
        argv_line = curl_calls.splitlines()[0] if curl_calls.strip() else ""
        assert argv_line.split()[0] == "-q", argv_line
        assert "api.pushover.net" in curl_calls, curl_calls

    def test_a_planted_venv_gh_cannot_author_the_security_comment(self, tmp_path: Path):
        clone = tmp_path / "clone"
        (clone / "scripts").mkdir(parents=True)
        (clone / "logs" / "security-nightly").mkdir(parents=True)
        wrapper = clone / "scripts" / "security_nightly.sh"
        shutil.copy2(REPO / "scripts" / "security_nightly.sh", wrapper)
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
        for name in (".radon-weekend-runner", ".radon-security-runner"):
            (clone / name).write_text("", encoding="utf-8")
        venv_bin = tmp_path / "venv-security" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "activate").write_text("#\n", encoding="utf-8")
        planted = tmp_path / "PWNED_VENV_GH"
        (venv_bin / "gh").write_text(
            "#!/bin/sh\n"
            f"echo PWN > '{planted}'\n"
            "exit 0\n",
            encoding="utf-8",
        )
        (venv_bin / "gh").chmod(0o755)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        gh_log = tmp_path / "gh.log"
        curl_stub = bin_dir / "curl"
        wrapper.write_text(
            wrapper.read_text(encoding="utf-8").replace("/usr/bin/curl", str(curl_stub)),
            encoding="utf-8",
        )
        for name, content in {
            "gh": (
                "#!/bin/sh\n"
                f'printf "%s\\n" "$*" >> "{gh_log}"\n'
                'if [ "$1 $2" = "issue list" ]; then echo 4242; fi\n'
                "exit 0\n"
            ),
            "git": "#!/bin/sh\nexit 0\n",
            "timeout": (
                "#!/bin/bash\n"
                "while [ $# -gt 0 ]; do\n"
                '  case "$1" in\n'
                "    -k|--kill-after) shift 2 ;;\n"
                "    *) shift; break ;;\n"
                "  esac\n"
                "done\n"
                'exec "$@"\n'
            ),
            "curl": "#!/bin/sh\nexit 0\n",
            "claude": (
                "#!/bin/sh\n"
                "echo 'SECURITY-NIGHTLY PHASE COMPLETE: audit'\n"
                "exit 0\n"
            ),
        }.items():
            exe = bin_dir / name
            exe.write_text(content, encoding="utf-8")
            exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        proc = subprocess.run(
            ["/bin/bash", str(wrapper), "audit"],
            env={
                "PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
                "HOME": str(tmp_path / "home"),
                "RADON_WEEKEND_REPO": str(clone),
            },
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
        assert not planted.exists(), "venv gh must not run after the snapshot"
        calls = gh_log.read_text(encoding="utf-8") if gh_log.exists() else ""
        assert "issue comment" in calls, calls
        assert "PWN" not in calls, calls


class TestSanitizeUsesPinnedSed:
    @pytest.mark.parametrize("wrapper", WRAPPERS, ids=lambda p: p.name)
    def test_sanitize_does_not_exec_path_sed(self, wrapper: Path):
        fn = _sanitize_issue_text_fn(wrapper)
        assert "/usr/bin/sed" in fn, wrapper.name
        assert "| sed " not in fn, wrapper.name
        assert "|sed " not in fn, wrapper.name

    def test_a_planted_venv_sed_cannot_author_the_security_comment(self, tmp_path: Path):
        src = (REPO / "scripts" / "security_nightly.sh").read_text(encoding="utf-8")
        start = src.index("_sanitize_issue_text() {")
        end = src.index("\nreport() {", start)
        fns = src[start:end]
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        planted = bin_dir / "sed"
        planted.write_text("#!/bin/sh\necho PWNED_FROM_PATH_SED\n", encoding="utf-8")
        planted.chmod(planted.stat().st_mode | stat.S_IXUSR)
        script = tmp_path / "run.sh"
        script.write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\nISSUE_SANITIZE=1\n"
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
        assert "**audit**" in proc.stdout
        assert "**OK**" in proc.stdout
        assert "**Issue discovered**" not in proc.stdout


class TestSecurityBashFallbackSanitizes:
    RAW = _secret_detail()

    def test_issue_sanitize_redacts_the_bash_body(self, tmp_path: Path):
        src = (REPO / "scripts" / "security_nightly.sh").read_text(encoding="utf-8")
        start = src.index("_sanitize_issue_text() {")
        end = src.index("\nreport() {", start)
        fns = src[start:end]
        script = tmp_path / "run.sh"
        script.write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\nISSUE_SANITIZE=1\n"
            + fns
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
        assert "**audit**" in out
        assert "**OK**" in out
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


class TestARewrittenCloneFormatterCannotAuthorTheIssue:
    """report() must not exec disk python3 or a formatter file. The agent
    can rewrite both, and chmod 0444 on a PID-guessable snapshot is reversible."""

    PWN = "PWNED_FROM_CLONE_FORMATTER"

    def test_security_comment_ignores_a_formatter_the_agent_rewrote(self, tmp_path: Path):
        clone = tmp_path / "clone"
        (clone / "scripts").mkdir(parents=True)
        (clone / "logs" / "security-nightly").mkdir(parents=True)
        shutil.copy2(REPO / "scripts" / "security_nightly.sh", clone / "scripts" / "security_nightly.sh")
        (clone / "scripts" / "security_nightly.sh").chmod(
            (clone / "scripts" / "security_nightly.sh").stat().st_mode | stat.S_IXUSR
        )
        shutil.copy2(REPO / "scripts" / "nightly_issue_format.py", clone / "scripts" / "nightly_issue_format.py")
        (clone / "scripts" / "weekend_notify.py").write_text("# stub\n", encoding="utf-8")
        for marker in (
            ".radon-weekend-runner",
            ".radon-security-runner",
        ):
            (clone / marker).write_text("", encoding="utf-8")

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        gh_log = tmp_path / "gh.log"
        pwn = self.PWN
        weekend_root = clone.parent
        for name, content in {
            "gh": (
                "#!/bin/sh\n"
                f'printf "%s\\n" "$*" >> "{gh_log}"\n'
                'if [ "$1 $2" = "issue list" ]; then echo 4242; fi\n'
                "exit 0\n"
            ),
            "git": "#!/bin/sh\nexit 0\n",
            "timeout": (
                "#!/bin/bash\n"
                "while [ $# -gt 0 ]; do\n"
                '  case "$1" in\n'
                "    -k|--kill-after) shift 2 ;;\n"
                "    *) shift; break ;;\n"
                "  esac\n"
                "done\n"
                'exec "$@"\n'
            ),
            "python3": (
                "#!/bin/sh\n"
                "echo '**Issue discovered**'\n"
                f"echo '{pwn}'\n"
                "exit 0\n"
            ),
            "claude": (
                "#!/bin/sh\n"
                f"cat > '{clone / 'scripts' / 'nightly_issue_format.py'}' <<'EOF'\n"
                "print('**Issue discovered**')\n"
                f"print('{pwn}')\n"
                "EOF\n"
                f"echo 'print({pwn!r})' > '{weekend_root / '.nightly-issue-format.1'}'\n"
                "echo 'SECURITY-NIGHTLY PHASE COMPLETE: audit'\n"
                "exit 0\n"
            ),
        }.items():
            exe = bin_dir / name
            exe.write_text(content, encoding="utf-8")
            exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        proc = subprocess.run(
            ["/bin/bash", str(clone / "scripts" / "security_nightly.sh"), "audit"],
            env={
                "PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
                "HOME": str(tmp_path / "home"),
                "RADON_WEEKEND_REPO": str(clone),
            },
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
        calls = gh_log.read_text(encoding="utf-8") if gh_log.exists() else ""
        assert "issue comment" in calls, calls
        assert pwn not in calls, calls
        assert "**audit**" in calls, calls
        assert "**OK**" in calls, calls
        assert "**Issue discovered**" not in calls, calls
        assert (clone / "scripts" / "nightly_issue_format.py").read_text(
            encoding="utf-8"
        ).count(pwn) >= 1
        assert (weekend_root / ".nightly-issue-format.1").read_text(
            encoding="utf-8"
        ).count(pwn) >= 1


def test_rewritten_formatter_class_is_defined_once():
    src = Path(__file__).read_text(encoding="utf-8")
    defs = [
        ln
        for ln in src.splitlines()
        if ln.startswith("class TestARewrittenCloneFormatterCannotAuthorTheIssue")
    ]
    assert len(defs) == 1, defs
