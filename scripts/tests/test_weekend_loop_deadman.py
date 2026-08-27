"""The weekend loops' own dead-man contract.

R-237: `ground_truth` is called inside `run_phase` with `trap on_crash ERR`
armed. `on_crash` reports and returns 0, but under `set -Eeuo pipefail` the
shell exits anyway, so `run_phase audit` never returns and `run_phase
remediate` is never reached — contradicting the comment directly above it
("Remediate runs regardless of the audit rc"). The operator sees one
`audit ... CRASHED` comment and then complete silence, which is
indistinguishable from the remediate phase hanging.

R-238: the cycle budget is tested at exactly one place — AFTER `run_round`
returns, inside the continuation loop. A check that passes at
`SECONDS = CYCLE_BUDGET_SECS - 1` still launches a round that runs a further
`CAP_SECS`, so the effective cap is `CYCLE_BUDGET_SECS + CAP_SECS` = 26 h
against a 24 h `StartCalendarInterval`. launchd will not start a second
instance of a running label, so the next 00:00 fire is dropped with no record.

R-239: everything between `main()` entry and `run_phase` runs with NO ERR trap
— `cd`, the marker check, `acquire_runner_lock`, `mkdir`, the rotation
pipeline — so a full disk, a moved clone or a held lock exits with nothing but
a line on stderr. The lock branch is the expensive one: a recorded pid reused
by any live unrelated process makes every subsequent daily fire exit 3 in
under a second, silently.

R-240: the testing plist has no `~/.bun/bin` on PATH, and the testing loop is
the one whose entire remit is JS/vitest health. `setup_testing_weekend.sh`
checks `bun` in the operator's INTERACTIVE shell, which does have it, so setup
prints ok and installs a plist under which bun is not resolvable.

R-267: log rotation has no exclusion list and the plists point
StandardOutPath/StandardErrorPath into that same directory, so the launchd
sinks — the only forensics for the prologue deaths above — are eventually
unlinked, and unlinked BEFORE `run_phase`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RELIABILITY = REPO / "scripts" / "reliability_weekend.sh"
TESTING = REPO / "scripts" / "testing_weekend.sh"
PLISTS = {
    "reliability": REPO / "config" / "com.radon.reliability-daily.plist",
    "testing": REPO / "config" / "com.radon.testing-daily.plist",
}
LOOPS = {"reliability": RELIABILITY, "testing": TESTING}


def _uncommented(path: Path) -> str:
    return "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


class TestGroundTruthFailureStillReportsRemediate:
    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_ground_truth_cannot_kill_the_cycle(self, name):
        body = _uncommented(LOOPS[name])
        match = re.search(r"^\s*ground_truth\s*$", body, re.M)
        assert match is None, (
            "ground_truth is called bare under `set -e` with the ERR trap "
            "armed, so a fetch failure exits the shell and the remediate "
            "phase is never run and never reported"
        )

    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_the_cycle_runs_both_phases_even_when_the_first_fails(self, name):
        body = _uncommented(LOOPS[name])
        cycle = body[body.index('MODE" == "cycle"'):]
        assert "run_phase audit" in cycle and "run_phase remediate" in cycle
        # The audit call must not be able to abort the cycle.
        audit_line = next(
            line for line in cycle.splitlines() if "run_phase audit" in line
        )
        assert "||" in audit_line or "set +e" in cycle, (
            "run_phase audit is called bare, so any uncaught failure inside it "
            "skips the remediate phase entirely"
        )


class TestCycleBudgetBoundsTheWholeRun:
    def test_the_budget_plus_one_cap_fits_inside_a_day(self):
        body = _uncommented(RELIABILITY)
        budget = int(re.search(r"RADON_WEEKEND_CYCLE_BUDGET_SECS:-(\d+)", body).group(1))
        # Every round — including round 1 of each phase — is launched only
        # with room for its own CAP_SECS, so the WHOLE cycle is bounded by the
        # budget rather than by budget + one more cap.
        assert budget < 86400, (
            f"a {budget}s cycle budget cannot leave a 24h launchd period "
            "clear; launchd will not start a second instance of a running "
            "label, so the next 00:00 fire is dropped with no record"
        )
        checks = re.findall(r"CYCLE_DEADLINE - CAP_SECS", body)
        assert len(checks) >= 2, (
            "the deadline is subtracted from in the continuation loop but not "
            "before the first round of a phase, so a cycle already past its "
            f"budget still launches a full round: {len(checks)} check(s)"
        )

    def test_a_round_is_not_started_without_room_for_its_cap(self):
        body = _uncommented(RELIABILITY)
        assert "CYCLE_DEADLINE - CAP_SECS" in body or "room_for_another_round" in body, (
            "the deadline is checked without accounting for the cap of the "
            "round it is about to launch"
        )


class TestPrologueDeathsAreReported:
    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_the_err_trap_is_armed_before_the_prologue(self, name):
        body = _uncommented(LOOPS[name])
        first_trap = body.index("trap on_crash ERR")
        # The CALL site, not the function definition near the top of the file.
        for marker in ('acquire_runner_lock "$RUNNER_LOCK"', "not the dedicated"):
            assert marker in body
            assert body.index(marker) > first_trap, (
                f"{marker} runs before any ERR trap is armed, so it exits with "
                "nothing but a line on stderr — no Pushover, no issue comment"
            )

    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_a_held_lock_is_reported_not_just_exited(self, name):
        body = _uncommented(LOOPS[name])
        lock_branch = body[body.index("acquire_runner_lock"):]
        lock_branch = lock_branch[: lock_branch.index("LOG_DIR=")]
        assert "report" in lock_branch, (
            "a stale pid reused by a live unrelated process makes every daily "
            "fire exit 3 in under a second, with no signal at all"
        )


class TestTestingPlistCanResolveBun:
    def test_bun_is_on_the_testing_plist_path(self):
        text = PLISTS["testing"].read_text(encoding="utf-8")
        path = re.search(r"<key>PATH</key>\s*<string>([^<]*)</string>", text).group(1)
        assert ".bun/bin" in path, (
            "the loop whose entire remit is JS/vitest health cannot resolve "
            "bun; setup checks it in the operator's interactive shell, which "
            "can"
        )

    def test_both_plists_agree_on_path(self):
        paths = {}
        for name, plist in PLISTS.items():
            text = plist.read_text(encoding="utf-8")
            paths[name] = re.search(r"<key>PATH</key>\s*<string>([^<]*)</string>", text).group(1)
        assert paths["testing"] == paths["reliability"], paths


class TestRotationSparesTheLaunchdSinks:
    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_rotation_never_unlinks_the_launchd_sinks(self, name):
        body = _uncommented(LOOPS[name])
        rotation = body[body.index("ls -1t \"$LOG_DIR\""):]
        rotation = rotation[: rotation.index("done") + 4]
        assert "launchd-cycle" in rotation, (
            "the plists point StandardOutPath/StandardErrorPath into the "
            "rotated directory, so the only forensics for a prologue death "
            "are eventually unlinked — and unlinked BEFORE run_phase, so the "
            "rest of the invocation writes to a deleted inode"
        )


class TestSetupGuardsTheSharedVenv:
    """R-266: `WEEKEND_VENV` is the same path in both setup scripts.

    Both wrappers prepend it to the running agent's `PATH`, so running one
    setup while the other loop's cycle is live re-creates the interpreter and
    re-installs site-packages under a running agent. The in-flight guard was
    written for the clone and never extended to the shared root.
    """

    SETUPS = {
        "reliability": REPO / "scripts" / "setup_reliability_weekend.sh",
        "testing": REPO / "scripts" / "setup_testing_weekend.sh",
    }

    def test_the_two_setups_really_do_share_a_venv(self):
        venvs = {
            name: re.search(r'WEEKEND_VENV="([^"]+)"', path.read_text(encoding="utf-8")).group(1)
            for name, path in self.SETUPS.items()
        }
        assert venvs["reliability"] == venvs["testing"], (
            f"precondition changed: {venvs}"
        )

    @pytest.mark.parametrize("name", ["reliability", "testing"])
    def test_each_setup_checks_the_sibling_clone_lock(self, name):
        # Comments stripped first: the guard's own comment quotes the
        # `python3.13 -m venv` line it protects, and a naive slice ends there.
        body = "\n".join(
            line for line in self.SETUPS[name].read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )
        install = body[: body.index("python3.13 -m venv")]
        assert "SIBLING_REPO" in install, (
            f"{name} setup re-creates the shared venv without checking whether "
            "the other loop's cycle is executing against it"
        )
