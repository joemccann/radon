"""The nightly security loop's own identity and isolation contract.

The five nightly loops share one wrapper shape, one runner-lock primitive and
per-loop venvs, and all five fire at 00:00. The security loop is the
one that must NEVER: run in a sibling clone or the operator checkout, receive a
Radon credential, or leak a scanner artifact into the public repository. Those
three properties are enforced by concrete, testable facts asserted here — the
two-marker gate, the absence of any credential provisioning in its setup, and
the sanitized dead-man. The shared survivability/dead-man contracts live in
`test_weekend_loop_deadman.py`, `test_rel137_weekend_wrapper_survivability.py`
and `test_weekend_wrapper_self_rewrite.py`; this loop is registered in all
three. The security loop is deliberately NOT registered in
`test_weekend_runner_env_provisioning.py`: that contract asserts a setup DOES
provision `web/.env`, which is the exact opposite of rail 5 here.
"""

from __future__ import annotations

import os
import plistlib
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

# The setup-execution stubs live beside this file; pytest's rootdir is the
# repo, so the directory is not on sys.path by default.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_weekend_runner_env_provisioning import DUMMY, _stub_bin as _setup_stub_bin  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
WRAPPER = REPO / "scripts" / "security_nightly.sh"
SETUP = REPO / "scripts" / "setup_security_nightly.sh"
PLIST = REPO / "config" / "com.radon.security-daily.plist"
SKILL = REPO / ".claude" / "skills" / "security-nightly" / "SKILL.md"

CLONE = "radon-security"
LABEL = "security-nightly"
LOG_DIR = "logs/security-nightly"
SIBLING_CLONES = ("radon", "radon-testing", "radon-ci-performance", "radon-documentation")
BASH = shutil.which("bash") or "/bin/bash"
COMMENT_MARK = "<<<COMMENT>>>"


def _uncommented(path: Path) -> str:
    return "\n".join(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


def _phase_complete_marker() -> str:
    body = WRAPPER.read_text(encoding="utf-8")
    match = re.search(r'PHASE_COMPLETE_MARKER="([^"]+)"', body)
    assert match, "the wrapper lost PHASE_COMPLETE_MARKER"
    return match.group(1)


def _clone(tmp_path: Path, *, security_marker: bool) -> Path:
    repo = tmp_path / "clone"
    (repo / "scripts").mkdir(parents=True)
    (repo / "logs" / LABEL).mkdir(parents=True)
    shutil.copy2(WRAPPER, repo / "scripts" / "security_nightly.sh")
    (repo / "scripts" / "security_nightly.sh").chmod(0o755)
    (repo / "scripts" / "weekend_notify.py").write_text("# stub\n", encoding="utf-8")
    (repo / ".radon-weekend-runner").write_text("", encoding="utf-8")
    if security_marker:
        (repo / ".radon-security-runner").write_text("", encoding="utf-8")
    return repo


class TestTheLoopOwnsItsOwnLane:
    def test_the_wrapper_defaults_to_its_own_clone(self):
        body = _uncommented(WRAPPER)
        default = re.search(r'REPO="\$\{RADON_WEEKEND_REPO:-([^}]+)\}"', body).group(1)
        assert default.endswith(f"/{CLONE}"), (
            f"the wrapper defaults to {default!r}, not this loop's clone; a "
            "launchd fire without RADON_WEEKEND_REPO would hard-reset another "
            "loop's working tree mid-run"
        )

    # Bare "radon" is a substring of this loop's own "radon-security" clone and
    # of "$WEEKEND_ROOT/radon-weekend", so the naked-substring check uses only
    # the distinctly named siblings (as the ci-performance/documentation
    # contracts do); the reliability clone "$WEEKEND_ROOT/radon" is covered by
    # the setup sibling-guard test via its exact path form.
    @pytest.mark.parametrize(
        "sibling", ("radon-testing", "radon-ci-performance", "radon-documentation")
    )
    def test_no_executable_line_names_a_sibling_clone(self, sibling):
        body = _uncommented(WRAPPER)
        assert sibling not in body, (
            f"{sibling} appears in executable wrapper code; only the header "
            "comment may name a sibling loop's clone"
        )

    def test_the_deadman_label_and_branch_prefix_are_this_loop(self):
        body = _uncommented(WRAPPER)
        assert f'DEADMAN_LABEL="{LABEL}"' in body, body
        assert 'PR_BRANCH_PREFIX="security/"' in body, (
            "the wrapper resolves the PR to page about by head-ref prefix; a "
            "sibling prefix pages this loop's status against that loop's PR"
        )

    def test_the_wrapper_invokes_this_loops_skill(self):
        body = _uncommented(WRAPPER)
        assert "/security-nightly $PHASE" in body, (
            "the wrapper spawns the agent with another loop's slash command"
        )
        assert SKILL.is_file(), f"{SKILL} does not exist, so the run has no prompt"

    def test_the_skill_is_not_gitignored(self):
        proc = subprocess.run(
            ["git", "check-ignore", "-q",
             ".claude/skills/security-nightly/SKILL.md"],
            cwd=REPO, capture_output=True, timeout=60,
        )
        assert proc.returncode != 0, (
            ".claude/skills/security-nightly/ is gitignored; add the whitelist "
            "line to .gitignore or the runner clone never gets the skill"
        )

    def test_the_log_directory_is_this_loops(self):
        body = _uncommented(WRAPPER)
        assert f'LOG_DIR="$REPO/{LOG_DIR}"' in body, body

    def test_the_notifier_accepts_this_loop(self):
        body = _uncommented(WRAPPER)
        assert 'LOOP_SLUG="security"' in body, body
        assert '_notify_curl "$LOOP_SLUG"' in body, (
            "the wrapper must page this loop via in-main _notify_curl; "
            "weekend_notify.py is not the pager"
        )


class TestTheTwoMarkerGate:
    """Rail 1: only ~/radon-weekend/radon-security carries BOTH markers.

    A sibling loop's clone and the operator checkout have only
    `.radon-weekend-runner`. The security wrapper must additionally require
    `.radon-security-runner`, so a stray `RADON_WEEKEND_REPO` cannot run
    credential-free security work — or scanner egress — in another tree.
    """

    def test_the_wrapper_requires_the_security_marker_in_source(self):
        body = _uncommented(WRAPPER)
        assert ".radon-security-runner" in body, (
            "the wrapper does not require the security marker, so it would run "
            "in any clone bearing only .radon-weekend-runner"
        )
        # the git clean must preserve it, or the first per-round clean deletes
        # the marker and the next round refuses.
        clean = next(ln for ln in body.splitlines() if "git clean" in ln)
        assert "--exclude=.radon-security-runner" in clean, clean

    def _stub_bin(self, tmp_path: Path) -> tuple[Path, Path]:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        gh_log = tmp_path / "gh.log"
        gh = bin_dir / "gh"
        gh.write_text(
            "#!/bin/sh\n"
            f'printf "%s\\n" "$*" >> "{gh_log}"\n'
            'if [ "$1 $2" = "issue list" ]; then echo 4242; fi\n'
            "exit 0\n",
            encoding="utf-8",
        )
        gh.chmod(0o755)
        for name in ("claude", "git", "python3", "timeout"):
            exe = bin_dir / name
            if name == "timeout":
                exe.write_text('#!/bin/sh\nshift $(( $# > 0 ? 1 : 0 ))\nexec "$@"\n', encoding="utf-8")
            elif name == "claude":
                exe.write_text("#!/bin/sh\necho 'stub claude must not run here' >&2\nexit 9\n", encoding="utf-8")
            else:
                exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return bin_dir, gh_log

    def test_a_clone_without_the_security_marker_is_refused(self, tmp_path):
        bin_dir, gh_log = self._stub_bin(tmp_path)
        repo = _clone(tmp_path, security_marker=False)
        proc = subprocess.run(
            [BASH, str(repo / "scripts" / "security_nightly.sh"), "audit"],
            cwd=repo,
            env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path / "home"),
                 "RADON_WEEKEND_REPO": str(repo)},
            capture_output=True, text=True, timeout=120,
        )
        assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
        assert ".radon-security-runner" in proc.stderr or "SECURITY runner" in proc.stderr, proc.stderr
        calls = gh_log.read_text(encoding="utf-8") if gh_log.exists() else ""
        assert "issue comment" in calls, f"the refusal must reach the dead-man: {calls!r}"

    def test_a_clone_with_only_the_weekend_marker_of_a_sibling_is_refused(self, tmp_path):
        # This is precisely the shape of every sibling loop's clone.
        bin_dir, _gh = self._stub_bin(tmp_path)
        repo = _clone(tmp_path, security_marker=False)
        proc = subprocess.run(
            [BASH, str(repo / "scripts" / "security_nightly.sh"), "cycle"],
            cwd=repo,
            env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path / "home"),
                 "RADON_WEEKEND_REPO": str(repo)},
            capture_output=True, text=True, timeout=120,
        )
        assert proc.returncode == 2, (proc.returncode, proc.stderr)


class TestTheDeadmanIsSanitized:
    """Rail 7: Radon is public. The run log holds scanner output and possibly a

    matched secret class, so the wrapper must NEVER post its tail to the public
    dead-man issue. The sibling loops do (`tail -c 1500 "$RUN_LOG"` into the
    comment body); the security wrapper must not.
    """

    def test_the_run_log_tail_is_not_piped_into_the_deadman(self):
        body = _uncommented(WRAPPER)
        assert "tail_text" not in body, (
            "the security wrapper still captures the run-log tail into the "
            "dead-man comment body; that publishes scanner output to a public "
            "GitHub issue"
        )
        assert 'tail -c 1500 "$RUN_LOG"' not in body, (
            "the run-log tail is still read into the public report path"
        )

    def test_the_deadman_still_reports_a_status(self):
        # Sanitizing the body must not remove the status itself.
        body = _uncommented(WRAPPER)
        assert 'report "$status"' in body, body

    # T-350: the two greps above are tripwires for the sibling-loop shape
    # (`tail_text` / `tail -c 1500`), but `report "$status" "$(tail -c 800
    # "$RUN_LOG")"` passes both. Rail 7 is enforced at the wire: run the real
    # wrapper with an agent that prints a canary into the run log, and read
    # every `gh issue comment --body` the dead-man received.
    CANARY = "CANARY-7f3a"

    def _phase_stub_bin(
        self, tmp_path: Path, *, claude_rc: int = 0, timeout_124: bool = False,
        truncated: bool = False, complete: bool = True,
    ) -> tuple[Path, Path, Path]:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        gh_log = tmp_path / "gh.log"
        bodies = tmp_path / "bodies.log"
        stubs = {
            # Every argv line, plus each comment body on its own (a body spans
            # lines, so `$*` alone cannot be split back into comments).
            "gh": (
                "#!/bin/bash\n"
                f'printf "%s\\n" "$*" >> "{gh_log}"\n'
                'if [ "$1 $2" = "issue list" ]; then echo 4242; fi\n'
                'if [ "$1 $2" = "issue comment" ]; then\n'
                '  while [ $# -gt 0 ]; do\n'
                '    if [ "$1" = "--body" ]; then shift\n'
                f'      printf \'{COMMENT_MARK}%s\\n\' "$1" >> "{bodies}"\n'
                "      break\n"
                "    fi\n"
                "    shift\n"
                "  done\n"
                "fi\n"
                "exit 0\n"
            ),
            # The agent: scanner-shaped output into the run log, then the
            # exit the case under test needs.
            # A phase that ends OK must print the completion marker (R-517);
            # without it the wrapper downgrades to INCOMPLETE and exits 75.
            "claude": (
                "#!/bin/sh\n"
                f"echo 'gitleaks: {self.CANARY} matched in web/lib/secret.ts:12'\n"
                + ("echo 'Background tasks still running after 600s'\n" if truncated else "")
                + (f"echo '{_phase_complete_marker()} audit'\n"
                   if complete and not truncated else "")
                + f"exit {claude_rc}\n"
            ),
            # `timeout -k <secs> <secs> cmd`: consume the options, then run the
            # command. The 124 path still runs the agent first, so the run log
            # holds the canary when the cap is reported.
            "timeout": (
                "#!/bin/bash\n"
                'while [ $# -gt 0 ]; do\n'
                '  case "$1" in\n'
                '    -k|--kill-after) shift 2 ;;\n'
                '    *) shift; break ;;\n'
                '  esac\n'
                'done\n'
                f'if [ "{int(timeout_124)}" = "1" ] && [ "${{1##*/}}" = "claude" ]; then "$@"; exit 124; fi\n'
                'exec "$@"\n'
            ),
            "git": "#!/bin/sh\nexit 0\n",
            "python3": "#!/bin/sh\nexit 0\n",
        }
        for name, body in stubs.items():
            exe = bin_dir / name
            exe.write_text(body, encoding="utf-8")
            exe.chmod(0o755)
        return bin_dir, gh_log, bodies

    def _run_audit(self, tmp_path: Path, **stub_kwargs) -> tuple[subprocess.CompletedProcess, str, list[str]]:
        bin_dir, gh_log, bodies = self._phase_stub_bin(tmp_path, **stub_kwargs)
        repo = _clone(tmp_path, security_marker=True)
        proc = subprocess.run(
            [BASH, str(repo / "scripts" / "security_nightly.sh"), "audit"],
            cwd=repo,
            env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path / "home"),
                 "RADON_WEEKEND_REPO": str(repo)},
            capture_output=True, text=True, timeout=120,
        )
        calls = gh_log.read_text(encoding="utf-8") if gh_log.exists() else ""
        raw = bodies.read_text(encoding="utf-8") if bodies.exists() else ""
        run_logs = list((repo / "logs" / LABEL).glob("audit-*.log"))
        assert run_logs and self.CANARY in run_logs[0].read_text(encoding="utf-8"), (
            "the harness must put the canary in the run log, or a clean "
            f"dead-man proves nothing: {run_logs}"
        )
        return proc, calls, [c for c in raw.split(COMMENT_MARK) if c.strip()]

    @pytest.mark.parametrize(
        "case, stub_kwargs, expected_status, expected_rc",
        [
            ("ok", {"claude_rc": 0}, "**OK**", 0),
            ("failed", {"claude_rc": 7}, "FAILED (exit 7)", 7),
            ("timeout", {"timeout_124": True}, "TIMEOUT", 124),
            # R-517: an incomplete phase exits 75, so neither the dead-man nor
            # launchd is told an unfinished night succeeded.
            ("truncated", {"truncated": True}, "TRUNCATED", 75),
            ("incomplete", {"complete": False}, "INCOMPLETE", 75),
        ],
    )
    def test_no_run_log_content_reaches_the_public_deadman(
        self, tmp_path, case, stub_kwargs, expected_status, expected_rc
    ):
        proc, calls, comments = self._run_audit(tmp_path, **stub_kwargs)
        assert proc.returncode == expected_rc, (proc.returncode, proc.stdout, proc.stderr)
        assert "issue comment" in calls, f"{case}: the phase never reached the dead-man: {calls!r}"
        assert comments, f"{case}: no --body was posted: {calls!r}"
        assert any(expected_status in c for c in comments), (case, comments)
        assert self.CANARY not in calls, (
            f"{case}: run-log content reached a public `gh` call; rail 7 forbids "
            f"posting the run-log tail to the dead-man issue: {calls!r}"
        )
        for comment in comments:
            assert self.CANARY not in comment, (case, comment)


class TestTheSetupIsCredentialFree:
    """Rail 5: the security clone receives NO Radon credential."""

    def test_setup_does_not_provision_web_env(self):
        body = _uncommented(SETUP)
        assert "provision_env_file" not in body, (
            "the security setup still provisions an env file into the clone; "
            "rail 5 forbids any Radon credential in the security clone"
        )
        assert "for env_rel in web/.env" not in body, body
        assert "install -m 600" not in body, (
            "the security setup still installs a credential file into the clone"
        )

    # T-351: the greps above pin the sibling setups' provisioning SHAPE
    # (`provision_env_file`, `install -m 600`); a plain `cp` of web/.env
    # passes all three. Rail 5 is enforced by running the real setup against
    # a staged source checkout that DOES carry every env file, with the whole
    # toolchain stubbed, and asserting none of them lands in the clone.
    def _stage_setup(self, tmp_path: Path) -> tuple[Path, dict]:
        src = tmp_path / "src"
        (src / "web").mkdir(parents=True)
        for rel, body in DUMMY.items():
            (src / rel).write_text(body, encoding="utf-8")
        root = tmp_path / "weekend"
        clone = root / CLONE
        (clone / ".git").mkdir(parents=True)
        (clone / "web").mkdir()
        (clone / "config").mkdir()
        (clone / "requirements.txt").write_text("", encoding="utf-8")
        shutil.copy(PLIST, clone / "config" / PLIST.name)
        venv_bin = root / "venv-security" / "bin"
        venv_bin.mkdir(parents=True)
        for tool in ("python", "pip"):
            exe = venv_bin / tool
            exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            exe.chmod(0o755)
        (root / ".env").write_text("PUSHOVER_USER=dummy\nPUSHOVER_TOKEN=dummy\n", encoding="utf-8")
        (tmp_path / "home").mkdir()
        env = {
            "PATH": f"{_setup_stub_bin(tmp_path)}:/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(tmp_path / "home"),
            "RADON_WEEKEND_ROOT": str(root),
            "RADON_WEEKEND_SRC_REPO": str(src),
        }
        return clone, env

    def test_an_executed_setup_lands_no_env_file_in_the_clone(self, tmp_path):
        clone, env = self._stage_setup(tmp_path)
        proc = subprocess.run(
            [BASH, str(SETUP)], env=env, cwd=str(tmp_path),
            capture_output=True, text=True, timeout=120,
        )
        out = proc.stdout + proc.stderr
        assert proc.returncode == 0, out
        assert (clone / ".radon-security-runner").is_file(), out
        for rel in DUMMY:
            assert not (clone / rel).exists(), (
                f"{rel} landed in the security clone although the source "
                f"checkout offered it; rail 5 forbids any Radon credential "
                f"in this clone:\n{out}"
            )
        assert "rail 5" in out.split("[2/4]", 1)[-1], out

    def test_setup_stamps_both_markers(self):
        body = _uncommented(SETUP)
        assert 'touch "$WEEKEND_REPO/.radon-weekend-runner"' in body, body
        assert 'touch "$WEEKEND_REPO/.radon-security-runner"' in body, (
            "setup never stamps the security marker, so the wrapper it installs "
            "will refuse to run"
        )

    def test_setup_targets_this_loops_clone_and_job(self):
        body = _uncommented(SETUP)
        assert f'WEEKEND_REPO="$WEEKEND_ROOT/{CLONE}"' in body, body
        assert "com.radon.security-daily.plist" in body, body

    def test_setup_creates_the_github_label(self):
        body = _uncommented(SETUP)
        assert f"gh label create {LABEL}" in body, (
            "a missing label makes gh issue create fail and the wrapper "
            "swallows it, turning the dead-man channel off silently"
        )

    @pytest.mark.parametrize("sibling", ("radon", "radon-testing", "radon-ci-performance", "radon-documentation"))
    def test_setup_stands_down_on_every_sibling_lock(self, sibling):
        body = _uncommented(SETUP)
        install = body[: body.index("python3.13 -m venv")]
        assert f'"$WEEKEND_ROOT/{sibling}"' in install, (
            f"setup re-creates the shared venv without checking {sibling}"
        )

    @pytest.mark.parametrize(
        "setup_name",
        [
            "setup_reliability_weekend.sh",
            "setup_testing_weekend.sh",
            "setup_ci_performance.sh",
            "setup_documentation_nightly.sh",
        ],
    )
    def test_every_sibling_setup_stands_down_on_this_loops_lock(self, setup_name):
        body = _uncommented(REPO / "scripts" / setup_name)
        install = body[: body.index("python3.13 -m venv")]
        assert f'"$WEEKEND_ROOT/{CLONE}"' in install, (
            f"{setup_name} re-creates the shared venv without checking {CLONE}"
        )


class TestThePlistFreezesUpdatesAndCarriesNoSecret:
    def _job(self):
        resolved = (
            PLIST.read_text(encoding="utf-8")
            .replace("__WEEKEND_REPO__", f"/tmp/{CLONE}")
            .replace("__HOME__", "/tmp/home")
        )
        return plistlib.loads(resolved.encode("utf-8"))

    def test_the_plist_parses_and_points_at_this_loop(self):
        job = self._job()
        assert job["Label"] == "com.radon.security-daily"
        program = " ".join(job["ProgramArguments"])
        assert "scripts/security_nightly.sh" in program, program
        assert program.rstrip().endswith("cycle"), program
        assert job["WorkingDirectory"].endswith(f"/{CLONE}")
        assert job["StandardOutPath"].endswith(f"{LOG_DIR}/launchd-cycle.log")

    def test_the_autoupdater_is_frozen(self):
        env = self._job()["EnvironmentVariables"]
        assert env.get("DISABLE_AUTOUPDATER") == "1", (
            "rail 8: the security plist must freeze Claude Code + plugin updates "
            "so no scanner/plugin/model upgrade happens unattended"
        )

    def test_the_plist_carries_no_radon_secret(self):
        env = self._job()["EnvironmentVariables"]
        allowed = {"PATH", "RADON_WEEKEND_REPO", "HOME", "DISABLE_AUTOUPDATER"}
        assert set(env).issubset(allowed), (
            f"the security plist injects unexpected env keys {set(env) - allowed}; "
            "rail 5 forbids broker/deploy/db/operator credentials in this job"
        )

    def test_the_job_fires_daily_at_midnight_and_not_at_load(self):
        job = self._job()
        # REL-180 (R-503): the five loops are staggered inside the 00:xx hour
        # (pairwise-distinct minutes are pinned in test_rel180_loop_launchers).
        assert job["StartCalendarInterval"]["Hour"] == 0
        assert 0 <= job["StartCalendarInterval"]["Minute"] < 60
        assert job["RunAtLoad"] is False

    def test_the_pre_reset_stands_down_on_a_live_lock(self):
        program = " ".join(self._job()["ProgramArguments"])
        assert ".weekend-runner.lock/pid" in program and "kill -0" in program, program


class TestTheSkillCarriesTheNonNegotiableRails:
    @pytest.mark.parametrize(
        "rail",
        [
            ".radon-security-runner",
            "Never test production or third parties",
            "Never touch live trading",
            "Never use production credentials or data",
            "Never publish a vulnerability",
            "Never auto-update security tooling",
            "Never trust a scanner verdict",
            "Never push `main` or deploy",
            "Fail closed",
            "OPERATOR_REQUIRED",
            "security/<YYYY-MM-DD>",
            "docs/security-audit-playbook.md",
            "cloud/.gitleaks.toml",
        ],
    )
    def test_the_rail_is_present(self, rail):
        text = " ".join(SKILL.read_text(encoding="utf-8").split())
        assert rail in text, (
            f"the skill no longer states the rail {rail!r}; the unattended run "
            "has no other source for it"
        )

    def test_the_skill_declares_both_phases(self):
        text = SKILL.read_text(encoding="utf-8")
        assert "## Audit pipeline" in text and "## Remediation mode" in text

    def test_the_skill_names_both_external_engines(self):
        text = SKILL.read_text(encoding="utf-8")
        assert "DeepSec" in text and "Claude Security" in text


class TestBudgetFollowsClaudeAuthMethod:
    """Operator policy 2026-08-31: the Claude Security spend cap is derived at
    run time from `claude auth status` — a claude.ai subscription session runs
    with NO --max-budget-usd, an API key runs with --max-budget-usd 50, and no
    surface invents a default when nothing is configured (a fabricated $25 cap
    killed the 2026-08-31 remediate attempt mid-scan).
    """

    def test_no_surface_documents_an_operator_budget_env(self):
        for path in (SKILL, WRAPPER, SETUP, PLIST):
            assert "CLAUDE_APPROVED_MAX_USD" not in path.read_text(encoding="utf-8"), (
                f"{path} still tells the operator/agent to source the budget "
                "from an environment variable; the cap is derived from "
                "`claude auth status` at run time"
            )

    def test_the_skill_detects_the_auth_method_at_run_time(self):
        text = SKILL.read_text(encoding="utf-8")
        assert "claude auth status" in text, (
            "the skill no longer derives the spend cap from the runner's "
            "actual Claude Code authentication"
        )
        for key in ("loggedIn", "authMethod", "subscriptionType"):
            assert key in text, f"the auth-status JSON key {key} is undocumented"
        assert "OPERATOR_REQUIRED" in text

    def test_the_only_budget_ever_passed_is_the_api_key_50(self):
        text = SKILL.read_text(encoding="utf-8")
        assert "--max-budget-usd 50" in text, "the API-key path lost its $50 cap"
        numeric = set(re.findall(r'--max-budget-usd\s+"?(\d+)', text))
        assert numeric == {"50"}, (
            f"--max-budget-usd must be exactly 50 on the API-key path and "
            f"absent everywhere else: {sorted(numeric)}"
        )
        assert not re.search(r'--max-budget-usd\s+"?\$', text), (
            "a variable-driven budget is back; the cap comes from the auth "
            "method, never an env var"
        )

    def test_the_keychain_env_for_the_subscription_session_is_kept(self):
        text = SKILL.read_text(encoding="utf-8")
        for var in ("`HOME`", "`USER`", "`LOGNAME`"):
            assert var in text, (
                f"the skill no longer keeps {var} for Claude Code invocations; "
                "macOS Keychain cannot unlock the claude.ai subscription "
                "session without it (2026-08-31 attempt 1: Not logged in)"
            )


class TestAnIncompletePhaseIsNeverReportedOk:
    """Operator policy 2026-08-31 (run 20260831T000007): `claude -p` exited 0
    with the remediate phase parked mid-flight ("Suite at ~35%; I'll pick up
    when the background run completes"), the text matched no T-239 ceiling
    marker, and the wrapper paged OK while the private run-record still had a
    full pytest suite in flight. OK now additionally requires the completion
    marker the skill prints as a finished phase's last line; without it the
    status is INCOMPLETE, the wrapper exits non-zero, and the dead-man says
    the audited SHA was not advanced so the next fire resumes the same
    private run. Both paths are RUN here, not grepped.
    """

    def _marker(self) -> str:
        body = WRAPPER.read_text(encoding="utf-8")
        match = re.search(r'PHASE_COMPLETE_MARKER="([^"]+)"', body)
        assert match, (
            "the wrapper lost PHASE_COMPLETE_MARKER, so OK is keyed on the "
            "agent's exit code alone again — the 20260831T000007 defect"
        )
        return match.group(1)

    def _harness(self, tmp_path: Path, claude_body: str) -> tuple[Path, dict, Path]:
        clone = tmp_path / "clone"
        (clone / "scripts").mkdir(parents=True)
        (clone / "logs" / LABEL).mkdir(parents=True)
        shutil.copy2(WRAPPER, clone / "scripts" / "security_nightly.sh")
        (clone / "scripts" / "security_nightly.sh").chmod(0o755)
        (clone / "scripts" / "weekend_notify.py").write_text("# stub\n", encoding="utf-8")
        (clone / ".radon-weekend-runner").write_text("", encoding="utf-8")
        (clone / ".radon-security-runner").write_text("", encoding="utf-8")

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        gh_log = tmp_path / "gh.log"
        stubs = {
            "gh": (
                "#!/bin/sh\n"
                f'printf "%s\\n" "$*" >> "{gh_log}"\n'
                'if [ "$1 $2" = "issue list" ]; then echo 4242; fi\n'
                "exit 0\n"
            ),
            "git": "#!/bin/sh\nexit 0\n",
            "python3": "#!/bin/sh\nexit 0\n",
            "claude": claude_body,
            # Consume `-k <secs>` and the duration, then run the child, the
            # same shape the self-rewrite harness uses (the mini has no
            # /usr/bin/timeout, so the wrapper's calls must go through this).
            "timeout": (
                "#!/bin/bash\n"
                'while [ $# -gt 0 ]; do\n'
                '  case "$1" in\n'
                '    -k|--kill-after) shift 2 ;;\n'
                '    --foreground|--preserve-status) shift ;;\n'
                '    *) shift; break ;;\n'
                '  esac\n'
                'done\n'
                'exec "$@"\n'
            ),
        }
        for name, body in stubs.items():
            exe = bin_dir / name
            exe.write_text(body, encoding="utf-8")
            exe.chmod(0o755)
        env = {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(tmp_path / "home"),
            "RADON_WEEKEND_REPO": str(clone),
        }
        return clone, env, gh_log

    def test_a_phase_that_prints_the_completion_marker_reports_ok(self, tmp_path):
        marker = self._marker()
        clone, env, gh_log = self._harness(
            tmp_path,
            "#!/bin/sh\n"
            "echo 'deterministic gates green; OPERATOR_REQUIRED recorded'\n"
            f"echo '{marker} audit run_id=20260831-audit'\n"
            "exit 0\n",
        )
        proc = subprocess.run(
            [BASH, str(clone / "scripts" / "security_nightly.sh"), "audit"],
            cwd=clone, env=env, capture_output=True, text=True, timeout=120,
        )
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
        calls = gh_log.read_text(encoding="utf-8") if gh_log.exists() else ""
        assert "**OK**" in calls or "**audit**" in calls, calls
        assert "**Issue discovered**" not in calls, calls
        assert "Nothing went wrong this audit phase." not in calls, calls

    def test_exit_zero_without_the_marker_is_incomplete_and_nonzero(self, tmp_path):
        # The literal shape of last night's failure: a deferral sentence that
        # matches neither the ceiling marker nor any error, and exit 0.
        clone, env, gh_log = self._harness(
            tmp_path,
            "#!/bin/sh\n"
            "echo \"Suite at ~35%; I'll pick up when the background run completes.\"\n"
            "exit 0\n",
        )
        proc = subprocess.run(
            [BASH, str(clone / "scripts" / "security_nightly.sh"), "audit"],
            cwd=clone, env=env, capture_output=True, text=True, timeout=120,
        )
        assert proc.returncode == 75, (
            "an incomplete phase must not exit 0 — launchd and the operator "
            f"were told last night's half-run succeeded: rc={proc.returncode} "
            f"{proc.stdout} {proc.stderr}"
        )
        calls = gh_log.read_text(encoding="utf-8") if gh_log.exists() else ""
        assert "INCOMPLETE" in calls, calls
        assert "Nothing went wrong this audit phase." not in calls, calls
        assert "NOT advanced" in calls, (
            f"the dead-man must say the audited SHA stayed put: {calls!r}"
        )
        assert "resumes the same private run" in calls, calls

    def test_the_skill_prints_the_exact_marker_the_wrapper_greps(self):
        marker = self._marker()
        assert marker in SKILL.read_text(encoding="utf-8"), (
            f"the wrapper greps {marker!r} but the skill never tells the agent "
            "to print it, so every finished phase would page INCOMPLETE"
        )

    def test_the_skill_owns_the_resume_contract(self):
        text = SKILL.read_text(encoding="utf-8")
        assert "run-record.md" in text, (
            "the skill no longer names the private run-record that makes an "
            "interrupted phase resumable"
        )
        assert "RESUME" in text or "resume" in text
        assert ".security-nightly-scratch" in text
        # The exact conditions the operator listed as INCOMPLETE, not OK.
        for condition in ("budget", "SIGTERM", "I'll pick up later"):
            assert condition in text, (
                f"the skill no longer classifies {condition!r} as an "
                "INCOMPLETE phase"
            )


class TestTheLoopBillsTheSubscriptionNotTheApiKey:
    """Rail 5b: model spend rides the operator's claude.ai login, never a key.

    Claude Code and every scanner the agent drives through the Claude Agent
    SDK (`deepsec process` / `revalidate`) prefer an Anthropic API key over the
    claude.ai login whenever one is visible in their environment, and say so on
    stderr: "claude.ai connectors are disabled because ANTHROPIC_API_KEY or
    another auth source is set and takes precedence over your claude.ai login".
    On 2026-09-01 that moved an 83-finding process + revalidate round onto
    metered API billing without a line in any run record.     Two halves: the wrapper unsets every billing-reroute variable AND refuses
    the run when one is set in the environment or sits in a file the scanners
    read themselves. Unset alone left a gitignored .env file as a silent
    reroute; refuse of only ANTHROPIC_API_KEY/AUTH_TOKEN left Bedrock/Vertex
    and the CLAUDE_CODE_API_KEY alias as the same hole.
    """

    KEY = "sk-ant-api03-CONTRACT-TEST-NOT-A-REAL-KEY"
    ENV_VARS = (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_API_KEY",
        "CLAUDE_API_KEY",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "AWS_BEARER_TOKEN_BEDROCK",
    )

    def _stub_bin(self, tmp_path: Path, env_dump: Path) -> Path:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        stubs = {
            "gh": (
                "#!/bin/sh\n"
                'if [ "$1 $2" = "issue list" ]; then echo 4242; fi\n'
                "exit 0\n"
            ),
            # The agent records the environment it was actually launched with.
            "claude": (
                "#!/bin/sh\n"
                f"env > '{env_dump}'\n"
                f"echo '{_phase_complete_marker()} audit'\n"
                "exit 0\n"
            ),
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
            "git": "#!/bin/sh\nexit 0\n",
            "python3": "#!/bin/sh\nexit 0\n",
        }
        for name, body in stubs.items():
            exe = bin_dir / name
            exe.write_text(body, encoding="utf-8")
            exe.chmod(0o755)
        return bin_dir

    def _audit(self, tmp_path: Path, *, env_extra=None, deepsec_env=None):
        env_dump = tmp_path / "agent-env.txt"
        bin_dir = self._stub_bin(tmp_path, env_dump)
        repo = _clone(tmp_path, security_marker=True)
        if deepsec_env is not None:
            (repo / ".deepsec").mkdir()
            (repo / ".deepsec" / ".env.local").write_text(deepsec_env, encoding="utf-8")
        env = {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(tmp_path / "home"),
            "RADON_WEEKEND_REPO": str(repo),
        }
        env.update(env_extra or {})
        proc = subprocess.run(
            [BASH, str(repo / "scripts" / "security_nightly.sh"), "audit"],
            cwd=repo, env=env, capture_output=True, text=True, timeout=120,
        )
        return proc, env_dump

    @pytest.mark.parametrize("var", ENV_VARS)
    def test_a_billing_reroute_in_the_environment_refuses_the_run(self, tmp_path, var):
        proc, env_dump = self._audit(tmp_path, env_extra={var: self.KEY})
        out = proc.stdout + proc.stderr
        assert proc.returncode == 2, (proc.returncode, out)
        assert "REFUSING" in out, out
        assert var in out, (
            f"the refusal must name {var} so the operator knows what to unset"
        )
        assert self.KEY not in out, "the refusal echoed the secret it found"
        assert not env_dump.exists(), "the agent ran anyway after the refusal"

    @pytest.mark.parametrize(
        "deepsec_env",
        (
            f"ANTHROPIC_API_KEY={KEY}\n",
            "CLAUDE_CODE_USE_BEDROCK=1\n",
            "CLAUDE_CODE_API_KEY=sk-ant-alias\n",
        ),
    )
    def test_a_key_file_the_scanners_read_themselves_refuses_the_run(
        self, tmp_path, deepsec_env
    ):
        proc, env_dump = self._audit(tmp_path, deepsec_env=deepsec_env)
        out = proc.stdout + proc.stderr
        assert proc.returncode == 2, (proc.returncode, out)
        assert "REFUSING" in out, out
        assert ".deepsec/.env.local" in out, (
            "the refusal must name the file the operator has to clear"
        )
        assert self.KEY not in out, "the refusal echoed the key it found"
        assert not env_dump.exists(), "the agent ran anyway after the refusal"

    def test_a_deepsec_env_file_without_key_material_still_runs(self, tmp_path):
        proc, _env_dump = self._audit(
            tmp_path,
            deepsec_env="# ANTHROPIC_API_KEY= (removed 2026-09-01)\nVERCEL_TOKEN=dummy\n",
        )
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)

    def test_the_skill_pins_subscription_auth_for_the_scanners(self):
        text = SKILL.read_text(encoding="utf-8")
        assert "ANTHROPIC_API_KEY" in text, (
            "the skill never tells the agent that an API key must not be used "
            "or provisioned for the scanner stages"
        )
