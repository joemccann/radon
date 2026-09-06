"""The nightly CI/deploy performance loop's own identity contract.

The three nightly loops share one wrapper shape, one runner-lock primitive and
per-loop venvs, and all three fire at 00:00. Everything that keeps
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
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WRAPPER = REPO / "scripts" / "ci_performance_nightly.sh"
SETUP = REPO / "scripts" / "setup_ci_performance.sh"
PLIST = REPO / "config" / "com.radon.ci-performance-daily.plist"
SKILL = REPO / ".claude" / "skills" / "ci-performance" / "SKILL.md"

CLONE = "radon-ci-performance"
LABEL = "ci-performance-nightly"
LOG_DIR = "logs/ci-performance"
SIBLING_CLONES = ("radon-testing", "radon-documentation", "radon-security")


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
        assert 'PR_BRANCH_PREFIX="ci-performance/"' in body, (
            "the wrapper resolves the PR to page about by head-ref prefix; a "
            "sibling prefix pages this loop's status against that loop's PR"
        )

    def test_the_wrapper_invokes_this_loops_skill(self):
        body = _uncommented(WRAPPER)
        assert "/ci-performance $PHASE" in body, (
            "the wrapper spawns the agent with another loop's slash command"
        )
        assert SKILL.is_file(), f"{SKILL} does not exist, so the run has no prompt"

    def test_the_log_directory_is_this_loops(self):
        body = _uncommented(WRAPPER)
        assert f'LOG_DIR="$REPO/{LOG_DIR}"' in body, body

    def test_the_notifier_accepts_this_loop(self):
        body = _uncommented(WRAPPER)
        assert 'LOOP_SLUG="ci-performance"' in body, body
        assert '_notify_curl "$LOOP_SLUG"' in body, (
            "the wrapper must page this loop via in-main _notify_curl; "
            "weekend_notify.py is not the pager"
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
        assert job["Label"] == "com.radon.ci-performance-daily"
        program = " ".join(job["ProgramArguments"])
        assert "scripts/ci_performance_nightly.sh" in program, program
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
        assert "com.radon.ci-performance-daily.plist" in body, body

    @pytest.mark.parametrize("sibling", ("radon", "radon-testing", "radon-documentation", "radon-security"))
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


class TestTheSkillCarriesTheNonNegotiableRails:
    """The rails that keep an unattended optimizer from buying speed with

    safety. Each is quoted from the skill because the agent has no other
    source of them at 00:00.
    """

    @pytest.mark.parametrize(
        "rail",
        [
            ".radon-weekend-runner",
            "Never push to `main`",
            "Never trigger a dummy production deployment",
            "Never weaken a gate",
            "40-second production stability window",
            "exact 40-character commit SHA",
            "CI_PERFORMANCE_LOG.md",
            "ci-performance/<YYYY-MM-DD>",
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
        assert "## Mode: deliver" in text


class TestDeliverReportsCiBuildTimeDelta:
    """A time-saving fix on #196 must show before, after, and % change."""

    def test_skill_requires_the_four_column_table_and_tbd_path(self):
        text = " ".join(SKILL.read_text(encoding="utf-8").split())
        assert "| Job | Before | After | % change |" in text
        assert "TBD until" in text
        assert "do not invent" in text
        assert "(after - before) / before * 100" in text
        assert "ci-time-savings" in text

    def test_skill_puts_the_table_on_the_issue_writeup_and_deliver(self):
        text = SKILL.read_text(encoding="utf-8")
        report = text[text.index("## Required nightly report") :]
        deliver = text[text.index("## Mode: deliver") : text.index("## Required nightly report")]
        assert "CI build time" in report
        assert "ci-time-savings" in report
        assert "ci-time-savings" in deliver or "CI build time" in deliver
