"""DeepSec is the sixth nightly loop, on the same protocol as the other five.

Until 2026-09-18 DeepSec was a sibling worker: it exported findings into a
shared scratch and the security loop harvested them into its own remediate /
deliver. Two things were wrong with that. Its dead-man label
(`security-deepsec`) had never been created, so `gh issue create --label`
failed and the wrapper swallowed it — no rolling issue was ever opened, and a
quiet worker was indistinguishable from one that never fired. And the
remediate / deliver / PR contract every other loop carries did not apply to
it, so a verified DeepSec finding reached the operator only if the security
loop's night happened to reach it.

This file pins the loop's own identity: its wrapper defaults to its own
clone, requires its own marker, posts to its own label, publishes from its
own branch prefix, greps its own completion marker, and the security loop no
longer runs or harvests DeepSec. The shared survivability / dead-man /
subscription contracts register the wrapper alongside the other five.
"""

from __future__ import annotations

import plistlib
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WRAPPER = REPO / "scripts" / "security_deepsec_nightly.sh"
SECURITY_WRAPPER = REPO / "scripts" / "security_nightly.sh"
SETUP = REPO / "scripts" / "setup_security_nightly.sh"
PLIST = REPO / "config" / "com.radon.security-deepsec.plist"
SKILL = REPO / ".claude" / "skills" / "security-deepsec" / "SKILL.md"
SECURITY_SKILL = REPO / ".claude" / "skills" / "security-nightly" / "SKILL.md"

CLONE = "radon-security-deepsec"
LABEL = "security-deepsec"
MARKER = ".radon-security-deepsec-runner"
LOG_DIR = "logs/security-deepsec"
BASH = shutil.which("bash") or "/bin/bash"


def _uncommented(path: Path) -> str:
    return "\n".join(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


def _phase_complete_marker() -> str:
    match = re.search(r'PHASE_COMPLETE_MARKER="([^"]+)"', WRAPPER.read_text(encoding="utf-8"))
    assert match, "the wrapper lost PHASE_COMPLETE_MARKER"
    return match.group(1)


def _clone(tmp_path: Path, *, deepsec_marker: bool) -> Path:
    repo = tmp_path / "clone"
    (repo / "scripts").mkdir(parents=True)
    (repo / "logs" / LABEL).mkdir(parents=True)
    shutil.copy2(WRAPPER, repo / "scripts" / WRAPPER.name)
    (repo / "scripts" / WRAPPER.name).chmod(0o755)
    (repo / "scripts" / "weekend_notify.py").write_text("# stub\n", encoding="utf-8")
    (repo / ".radon-weekend-runner").write_text("", encoding="utf-8")
    # The security loop's marker alone must NOT satisfy this wrapper.
    (repo / ".radon-security-runner").write_text("", encoding="utf-8")
    if deepsec_marker:
        (repo / MARKER).write_text("", encoding="utf-8")
    return repo


def _stub_bin(tmp_path: Path, claude_body: str) -> tuple[Path, Path]:
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
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir, gh_log


def _run(repo: Path, bin_dir: Path, tmp_path: Path, mode: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [BASH, str(repo / "scripts" / WRAPPER.name), mode],
        cwd=repo,
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path / "home"),
             "RADON_WEEKEND_REPO": str(repo), "RADON_WEEKEND_SKIP_PRUNE": "1"},
        capture_output=True, text=True, timeout=120,
    )


class TestTheLoopOwnsItsOwnLane:
    def test_the_wrapper_exists_and_is_executable(self):
        assert WRAPPER.is_file(), "scripts/security_deepsec_nightly.sh is the DeepSec loop"
        assert WRAPPER.stat().st_mode & stat.S_IXUSR

    def test_the_wrapper_defaults_to_its_own_clone(self):
        body = _uncommented(WRAPPER)
        assert f'REPO="${{RADON_WEEKEND_REPO:-$HOME/radon-weekend/{CLONE}}}"' in body, body

    @pytest.mark.parametrize(
        "sibling",
        ("radon-weekend/radon\"", "radon-testing", "radon-ci-performance", "radon-documentation"),
    )
    def test_no_executable_line_names_a_sibling_clone(self, sibling):
        assert sibling not in _uncommented(WRAPPER)

    def test_no_executable_line_names_the_security_clone(self):
        body = _uncommented(WRAPPER)
        assert not re.search(r"radon-weekend/radon-security(?!-deepsec)", body), (
            "the DeepSec wrapper still points at the security loop's clone"
        )

    def test_the_deadman_label_and_branch_prefix_are_this_loop(self):
        body = _uncommented(WRAPPER)
        assert f'DEADMAN_LABEL="{LABEL}"' in body, body
        assert 'DEADMAN_TITLE="Nightly DeepSec runner"' in body, body
        assert 'PR_BRANCH_PREFIX="security-deepsec/"' in body, body

    def test_the_wrapper_invokes_this_loops_skill(self):
        body = _uncommented(WRAPPER)
        assert 'LOOP_SKILL="security-deepsec"' in body, body
        assert '"/$LOOP_SKILL $PHASE"' in body, body
        assert SKILL.is_file(), f"{SKILL} does not exist, so the run has no prompt"

    def test_the_skill_is_not_gitignored(self):
        proc = subprocess.run(
            ["git", "check-ignore", "-q", ".claude/skills/security-deepsec/SKILL.md"],
            cwd=REPO, capture_output=True, timeout=60,
        )
        assert proc.returncode != 0, ".claude/skills/security-deepsec/ is gitignored"

    def test_the_log_directory_is_this_loops(self):
        assert f'LOG_DIR="$REPO/{LOG_DIR}"' in _uncommented(WRAPPER)

    def test_the_notifier_accepts_this_loop(self):
        body = _uncommented(WRAPPER)
        assert 'LOOP_SLUG="security-deepsec"' in body, body
        assert '_notify_curl "$LOOP_SLUG"' in body, body

    def test_the_completion_marker_is_this_loops(self):
        assert _phase_complete_marker() == "SECURITY-DEEPSEC PHASE COMPLETE:"

    def test_the_audit_cap_fits_a_deepsec_process_run(self):
        # The former sibling worker capped DeepSec at 8h; the security loop's
        # 2h fast-scanner cap would TIMEOUT every real process run.
        body = _uncommented(WRAPPER)
        assert 'audit) CAP_SECS="${RADON_WEEKEND_AUDIT_CAP_SECS:-28800}"' in body, body

    def test_the_per_round_clean_keeps_deepsec_state(self):
        clean = next(ln for ln in _uncommented(WRAPPER).splitlines() if "git clean" in ln)
        assert "--exclude=.deepsec/" in clean, clean
        assert "--exclude=data/radon/" in clean, (
            "DeepSec keeps its untracked project state under data/radon/; the "
            "per-round clean would wipe every incremental finding"
        )
        assert f"--exclude={MARKER}" in clean, clean


class TestTheTwoMarkerGate:
    def test_the_wrapper_requires_the_deepsec_marker_in_source(self):
        body = _uncommented(WRAPPER)
        assert MARKER in body
        assert ".radon-security-runner\"" not in body and "-f .radon-security-runner" not in body, (
            "the DeepSec wrapper gates on the security loop's marker"
        )

    def test_a_clone_with_only_the_security_markers_is_refused(self, tmp_path):
        bin_dir, gh_log = _stub_bin(
            tmp_path, "#!/bin/sh\necho 'stub claude must not run here' >&2\nexit 9\n"
        )
        repo = _clone(tmp_path, deepsec_marker=False)
        proc = _run(repo, bin_dir, tmp_path, "audit")
        assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
        assert MARKER in proc.stderr or "DEEPSEC runner" in proc.stderr, proc.stderr
        calls = gh_log.read_text(encoding="utf-8") if gh_log.exists() else ""
        assert "issue comment" in calls, f"the refusal must reach the dead-man: {calls!r}"
        assert f"--label {LABEL}" in calls, calls


class TestThePhaseContractIsTheSharedOne:
    def test_a_phase_that_prints_the_marker_reports_ok_on_this_label(self, tmp_path):
        marker = _phase_complete_marker()
        bin_dir, gh_log = _stub_bin(
            tmp_path,
            "#!/bin/sh\n"
            "echo 'deepsec process rc=0; export archived; OPERATOR_REQUIRED recorded'\n"
            f"echo '{marker} audit run_id=20260918-audit'\n"
            "exit 0\n",
        )
        repo = _clone(tmp_path, deepsec_marker=True)
        proc = _run(repo, bin_dir, tmp_path, "audit")
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
        calls = gh_log.read_text(encoding="utf-8")
        assert "**OK**" in calls, calls
        assert f"--label {LABEL}" in calls, calls
        assert "--label security-nightly" not in calls, calls

    def test_exit_zero_without_the_marker_is_incomplete(self, tmp_path):
        bin_dir, gh_log = _stub_bin(
            tmp_path,
            "#!/bin/sh\necho \"process at 40%; I'll pick up when it completes.\"\nexit 0\n",
        )
        repo = _clone(tmp_path, deepsec_marker=True)
        proc = _run(repo, bin_dir, tmp_path, "audit")
        assert proc.returncode == 75, (proc.returncode, proc.stdout, proc.stderr)
        calls = gh_log.read_text(encoding="utf-8")
        assert "INCOMPLETE" in calls and "NOT advanced" in calls, calls

    def test_the_security_wrappers_marker_does_not_complete_a_deepsec_phase(self, tmp_path):
        bin_dir, gh_log = _stub_bin(
            tmp_path, "#!/bin/sh\necho 'SECURITY-NIGHTLY PHASE COMPLETE: audit run_id=x'\nexit 0\n",
        )
        repo = _clone(tmp_path, deepsec_marker=True)
        proc = _run(repo, bin_dir, tmp_path, "audit")
        assert proc.returncode == 75, (proc.returncode, proc.stdout, proc.stderr)


class TestTheSecurityLoopNoLongerHarvestsDeepSec:
    def test_the_security_wrapper_has_no_harvest_hook(self):
        body = _uncommented(SECURITY_WRAPPER)
        for token in ("harvest_deepsec", "security_deepsec.py", "classify_audit_timeout",
                      "SECURITY_SCRATCH", "fast engines complete"):
            assert token not in body, token

    def test_the_sibling_worker_is_gone(self):
        assert not (REPO / "scripts" / "security_deepsec_worker.sh").exists()
        assert not (REPO / "scripts" / "security_deepsec.py").exists()

    def test_the_security_skill_hands_deepsec_to_its_own_loop(self):
        text = SECURITY_SKILL.read_text(encoding="utf-8")
        assert "security_deepsec_worker.sh" not in text
        assert "harvests a ready export" not in text and "export ready" not in text.lower()
        assert "/security-deepsec" in text or "security-deepsec" in text


class TestTheSetupInstallsThisLoop:
    def test_setup_stamps_the_deepsec_marker_and_clone(self):
        body = _uncommented(SETUP)
        assert f'DEEPSEC_REPO="$WEEKEND_ROOT/{CLONE}"' in body, body
        assert f'touch "$DEEPSEC_REPO/{MARKER}"' in body, body
        assert 'touch "$DEEPSEC_REPO/.radon-weekend-runner"' in body, body

    def test_setup_creates_the_github_label(self):
        assert f"gh label create {LABEL}" in _uncommented(SETUP)

    def test_setup_installs_the_cycle_job(self):
        body = _uncommented(SETUP)
        assert "com.radon.security-deepsec.plist" in body
        assert "security_deepsec_worker.sh" not in body

    def test_setup_provisions_this_loops_venv(self):
        body = _uncommented(SETUP)
        assert 'DEEPSEC_VENV="$WEEKEND_ROOT/venv-security-deepsec"' in body, body
        assert 'python3.13 -m venv "$DEEPSEC_VENV"' in body, body

    def test_setup_converts_a_worktree_into_a_standalone_clone(self):
        # A git worktree cannot check out `main` while the security clone holds
        # it (launchd-deepsec.err 2026-09-17: "fatal: 'main' is already used by
        # worktree"), so the plist's pre-reset and the wrapper's ground_truth
        # both fail. The loop needs a clone of its own.
        body = _uncommented(SETUP)
        assert "worktree add" not in body, "the DeepSec clone must not be a worktree"
        assert 'git clone "$ORIGIN_URL" "$DEEPSEC_REPO"' in body, body
        # While the old worktree still holds `main`, the security clone's own
        # `checkout main` dies with that same fatal, so the conversion must
        # run BEFORE the setup resets the security clone (Mini, 2026-09-19).
        convert = body.index('if [[ -f "$DEEPSEC_REPO/.git" ]]; then')
        sec_checkout = body.index('git -C "$WEEKEND_REPO" checkout -f --quiet main')
        assert convert < sec_checkout, 'convert the DeepSec worktree before checking out main in the security clone'


class TestThePlistRunsTheCycle:
    def _job(self):
        resolved = (
            PLIST.read_text(encoding="utf-8")
            .replace("__DEEPSEC_REPO__", f"/tmp/{CLONE}")
            .replace("__HOME__", "/tmp/home")
        )
        return plistlib.loads(resolved.encode("utf-8"))

    def test_the_plist_points_at_the_cycle(self):
        job = self._job()
        assert job["Label"] == "com.radon.security-deepsec"
        program = " ".join(job["ProgramArguments"])
        assert "scripts/security_deepsec_nightly.sh" in program, program
        assert program.rstrip().endswith("cycle"), program
        assert "security_deepsec_worker.sh" not in program
        assert job["WorkingDirectory"].endswith(f"/{CLONE}")
        assert job["StandardOutPath"].endswith(f"{LOG_DIR}/launchd-cycle.log")

    def test_the_pre_reset_stands_down_on_the_runner_lock(self):
        program = " ".join(self._job()["ProgramArguments"])
        assert ".weekend-runner.lock/pid" in program and "kill -0" in program, program
        assert ".security-deepsec.lock" not in program

    def test_the_plist_carries_no_radon_secret_and_freezes_updates(self):
        env = self._job()["EnvironmentVariables"]
        assert env.get("DISABLE_AUTOUPDATER") == "1"
        assert set(env) <= {"PATH", "HOME", "DISABLE_AUTOUPDATER", "RADON_WEEKEND_REPO"}, env

    def test_the_detached_stage_inherits_the_launchd_path(self):
        # 2026-09-19 audit: the agent launched the detached deepsec stage with
        # a hand-built `env -i` PATH, so `node` (only under ~/.local/bin on the
        # runner, which the plist PATH carries) was missing and every deepsec
        # call exited 127. Both skills must pass the wrapper's PATH verbatim.
        assert "__HOME__/.local/bin" in self._job()["EnvironmentVariables"]["PATH"].replace("/tmp/home", "__HOME__")
        for skill in (SKILL, SECURITY_SKILL):
            text = " ".join(skill.read_text(encoding="utf-8").split())
            assert 'nohup env -i PATH="$PATH"' in text, skill
            assert "<minimal env>" not in text, skill

    def test_the_job_fires_daily_in_the_midnight_hour(self):
        job = self._job()
        assert job["StartCalendarInterval"]["Hour"] == 0
        assert 0 <= job["StartCalendarInterval"]["Minute"] < 60
        assert job["RunAtLoad"] is False


class TestTheSkillCarriesTheProtocol:
    @pytest.mark.parametrize(
        "rail",
        [
            MARKER,
            "Never test production or third parties",
            "Never touch live trading",
            "Never use production credentials or data",
            "Never publish a vulnerability",
            "Never auto-update security tooling",
            "Never trust a scanner verdict",
            "Never push `main` or deploy",
            "Fail closed",
            "OPERATOR_REQUIRED",
            "security-deepsec/<YYYY-MM-DD>",
            "deepsec process",
            "deepsec revalidate",
            "deepsec export",
            "--loop security-deepsec",
            "NIGHTLY DELIVER READY: loop=security-deepsec",
            ".security-deepsec-scratch",
            "run-record.md",
            "claude auth status",
        ],
    )
    def test_the_rail_is_present(self, rail):
        text = " ".join(SKILL.read_text(encoding="utf-8").split())
        assert rail in text, rail

    def test_the_skill_declares_all_three_phases(self):
        text = SKILL.read_text(encoding="utf-8")
        assert "## Audit pipeline" in text
        assert "## Remediation mode" in text
        assert "## Mode: deliver" in text

    def test_the_skill_prints_the_exact_marker_the_wrapper_greps(self):
        assert _phase_complete_marker() in SKILL.read_text(encoding="utf-8")

    def test_the_skill_classifies_the_incomplete_conditions(self):
        text = SKILL.read_text(encoding="utf-8")
        for condition in ("budget", "SIGTERM", "I'll pick up later"):
            assert condition in text, condition

    def test_the_skill_never_authors_the_deadman_comment(self):
        text = SKILL.read_text(encoding="utf-8")
        assert "gh issue comment" in text and "Wrapper-only" in text
