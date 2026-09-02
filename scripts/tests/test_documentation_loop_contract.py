"""The nightly documentation loop's own identity contract.

The four nightly loops share one wrapper shape, one runner-lock primitive and
per-loop venvs, and all four fire at 00:00. Everything that keeps
them from destroying each other is a NAME: the clone directory, the dead-man
label, the PR branch prefix, the log directory, the launchd label and the
skill the wrapper invokes. A copy-paste that leaves one of those pointing at a
sibling is silent — the run looks healthy and lands its work in, or resets,
the other loop's tree (the 2026-08-16 incident, from exactly that shape).

The shared survivability/dead-man contracts live in
`test_weekend_loop_deadman.py`, `test_rel137_weekend_wrapper_survivability.py`
and `test_weekend_wrapper_self_rewrite.py`; this loop is registered in all
three. What is asserted here is only what is specific to this loop.
"""

from __future__ import annotations

import plistlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WRAPPER = REPO / "scripts" / "documentation_nightly.sh"
SETUP = REPO / "scripts" / "setup_documentation_nightly.sh"
PLIST = REPO / "config" / "com.radon.documentation-daily.plist"
SKILL = REPO / ".claude" / "skills" / "documentation-nightly" / "SKILL.md"

CLONE = "radon-documentation"
LABEL = "documentation-nightly"
LOG_DIR = "logs/documentation-nightly"
SIBLING_CLONES = ("radon-testing", "radon-ci-performance", "radon-security")


def _uncommented(path: Path) -> str:
    return "\n".join(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


class TestTheLoopOwnsItsOwnLane:
    def test_the_wrapper_defaults_to_its_own_clone(self):
        body = _uncommented(WRAPPER)
        default = re.search(r'REPO="\$\{RADON_WEEKEND_REPO:-([^}]+)\}"', body).group(1)
        assert default.endswith(f"/{CLONE}"), (
            f"the wrapper defaults to {default!r}, not this loop's clone; a "
            "launchd fire without RADON_WEEKEND_REPO would hard-reset another "
            "loop's working tree mid-run"
        )

    @pytest.mark.parametrize("sibling", SIBLING_CLONES)
    def test_no_executable_line_names_a_sibling_clone(self, sibling):
        body = _uncommented(WRAPPER)
        assert sibling not in body, (
            f"{sibling} appears in executable wrapper code; only the header "
            "comment may name a sibling loop's clone"
        )

    def test_the_deadman_label_and_branch_prefix_are_this_loop(self):
        body = _uncommented(WRAPPER)
        assert f'DEADMAN_LABEL="{LABEL}"' in body, body
        assert 'PR_BRANCH_PREFIX="documentation/"' in body, (
            "the wrapper resolves the PR to page about by head-ref prefix; a "
            "sibling prefix pages this loop's status against that loop's PR"
        )

    def test_the_wrapper_invokes_this_loops_skill(self):
        body = _uncommented(WRAPPER)
        assert "/documentation-nightly $PHASE" in body, (
            "the wrapper spawns the agent with another loop's slash command"
        )
        assert SKILL.is_file(), f"{SKILL} does not exist, so the run has no prompt"

    def test_the_skill_is_not_gitignored(self):
        """`.claude/skills/*` is ignored by default with per-skill whitelists.

        A missing whitelist line is silent locally (the file sits on disk) and
        fatal on the runner: the hard-reset clone never receives the skill, so
        every nightly `claude -p "/documentation-nightly ..."` runs without a
        prompt.
        """
        proc = subprocess.run(
            ["git", "check-ignore", "-q",
             ".claude/skills/documentation-nightly/SKILL.md"],
            cwd=REPO,
            capture_output=True,
            timeout=60,
        )
        assert proc.returncode != 0, (
            ".claude/skills/documentation-nightly/ is gitignored; add the "
            "whitelist line to .gitignore or the runner clone never gets the "
            "skill"
        )

    def test_the_log_directory_is_this_loops(self):
        body = _uncommented(WRAPPER)
        assert f'LOG_DIR="$REPO/{LOG_DIR}"' in body, body

    def test_the_notifier_accepts_this_loop(self):
        """`weekend_notify.py` validates `--loop` with argparse `choices`.

        An unlisted value exits 2 BEFORE the Pushover call, and the wrapper
        sends the page with `|| true`, so the loop would silently never page.
        """
        proc = subprocess.run(
            [
                sys.executable,
                str(REPO / "scripts" / "weekend_notify.py"),
                "--loop",
                "documentation",
                "--phase",
                "audit",
                "--status",
                "OK",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            env={"PATH": "/usr/bin:/bin"},
        )
        assert proc.returncode == 0, (
            "weekend_notify rejects --loop documentation, so every phase page "
            f"is dropped: {proc.stdout}{proc.stderr}"
        )


class TestTheLaunchdJobPointsAtThisLoop:
    def test_the_plist_parses_and_carries_this_loops_label_and_wrapper(self):
        raw = PLIST.read_text(encoding="utf-8")
        # The shipped template is placeholder-substituted by the setup script;
        # substitute the same way so the result is real plist XML.
        resolved = raw.replace("__WEEKEND_REPO__", f"/tmp/{CLONE}").replace(
            "__HOME__", "/tmp/home"
        )
        job = plistlib.loads(resolved.encode("utf-8"))
        assert job["Label"] == "com.radon.documentation-daily"
        program = " ".join(job["ProgramArguments"])
        assert "scripts/documentation_nightly.sh" in program, program
        assert program.rstrip().endswith("cycle"), (
            "the daily fire must run the full cycle (audit then remediate)"
        )
        assert job["WorkingDirectory"].endswith(f"/{CLONE}")
        assert job["EnvironmentVariables"]["RADON_WEEKEND_REPO"].endswith(f"/{CLONE}")
        assert job["StandardOutPath"].endswith(f"{LOG_DIR}/launchd-cycle.log")
        assert job["StandardErrorPath"].endswith(f"{LOG_DIR}/launchd-cycle.err")

    def test_the_job_fires_daily_at_midnight_and_not_at_load(self):
        resolved = (
            PLIST.read_text(encoding="utf-8")
            .replace("__WEEKEND_REPO__", f"/tmp/{CLONE}")
            .replace("__HOME__", "/tmp/home")
        )
        job = plistlib.loads(resolved.encode("utf-8"))
        # REL-180 (R-503): the five loops are staggered inside the 00:xx hour
        # (pairwise-distinct minutes are pinned in test_rel180_loop_launchers).
        assert job["StartCalendarInterval"]["Hour"] == 0
        assert 0 <= job["StartCalendarInterval"]["Minute"] < 60
        assert "Weekday" not in job["StartCalendarInterval"], (
            "a Weekday key would make this a weekend-only loop"
        )
        assert job["RunAtLoad"] is False, (
            "RunAtLoad would start a cycle every login and every reinstall"
        )

    def test_the_pre_reset_stands_down_on_a_live_lock(self):
        program = " ".join(
            plistlib.loads(
                PLIST.read_text(encoding="utf-8")
                .replace("__WEEKEND_REPO__", f"/tmp/{CLONE}")
                .replace("__HOME__", "/tmp/home")
                .encode("utf-8")
            )["ProgramArguments"]
        )
        assert ".weekend-runner.lock/pid" in program and "kill -0" in program, (
            "the plist resets the clone unconditionally, so a fire during a "
            f"live cycle hard-resets the tree under a running agent: {program}"
        )


class TestSetupProvisionsTheDeadmanLabel:
    def test_setup_creates_the_github_label(self):
        body = _uncommented(SETUP)
        assert f"gh label create {LABEL}" in body, (
            "`gh issue create --label` fails on a label that does not exist "
            "and the wrapper swallows that failure, so a missing label turns "
            "the dead-man channel off with no signal at all"
        )

    def test_setup_targets_this_loops_clone_and_job(self):
        body = _uncommented(SETUP)
        assert f'WEEKEND_REPO="$WEEKEND_ROOT/{CLONE}"' in body, body
        assert "com.radon.documentation-daily.plist" in body, body

    @pytest.mark.parametrize(
        "sibling", ("radon", "radon-testing", "radon-ci-performance", "radon-security")
    )
    def test_setup_stands_down_on_every_sibling_lock(self, sibling):
        """Each setup writes its own `$WEEKEND_ROOT/venv-<loop>`.

        Sibling in-flight locks stay so a setup still stands down on a live
        sibling clone. R-266.
        """
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
            "setup_security_nightly.sh",
        ],
    )
    def test_every_sibling_setup_stands_down_on_this_loops_lock(self, setup_name):
        """The guard is symmetric: their setups must also wait on THIS clone."""
        body = _uncommented(REPO / "scripts" / setup_name)
        install = body[: body.index("python3.13 -m venv")]
        assert f'"$WEEKEND_ROOT/{CLONE}"' in install, (
            f"{setup_name} re-creates the shared venv without checking {CLONE}"
        )


class TestTheSkillCarriesTheNonNegotiableRails:
    """The rails that keep an unattended documentation maintainer from

    inventing prose, touching live systems, or manufacturing busywork. Each is
    quoted from the skill because the agent has no other source of them at
    00:00.
    """

    @pytest.mark.parametrize(
        "rail",
        [
            ".radon-weekend-runner",
            "Never push to `main`",
            "Never touch live trading or production state",
            "Never read or reproduce secret values",
            "Never invent reality",
            "Do not create proof-of-life documentation",
            "documentation/<YYYY-MM-DD>",
            "Documentation <YYYY-MM-DD>",
            "NO_ACTIONABLE_DRIFT",
            "audited-through",
            "docs/owners.json",
            "OPERATOR_REQUIRED",
        ],
    )
    def test_the_rail_is_present(self, rail):
        # Normalized: every rail here is prose that markdown wraps, so a
        # reflow must not read as a deleted rail.
        text = " ".join(SKILL.read_text(encoding="utf-8").split())
        assert rail in text, (
            f"the skill no longer states the rail {rail!r}; the unattended run "
            "has no other source for it"
        )

    def test_the_skill_declares_both_modes(self):
        text = SKILL.read_text(encoding="utf-8")
        assert "## Mode: audit" in text and "## Mode: remediate" in text
