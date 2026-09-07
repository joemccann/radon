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

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

# 2026-09-06: these suites stub `claude` and are about wrapper behaviour around the agent, not about which provider runs it. Pin a claude rung so the provider ladder is not what decides the outcome; the ladder itself is covered by test_provider_failover.py.
CLAUDE_RUNG_LADDER = "claude:claude-fable-5[1m]"

REPO = Path(__file__).resolve().parents[2]
RELIABILITY = REPO / "scripts" / "reliability_weekend.sh"
TESTING = REPO / "scripts" / "testing_weekend.sh"
CI_PERFORMANCE = REPO / "scripts" / "ci_performance_nightly.sh"
DOCUMENTATION = REPO / "scripts" / "documentation_nightly.sh"
SECURITY = REPO / "scripts" / "security_nightly.sh"
PLISTS = {
    "reliability": REPO / "config" / "com.radon.reliability-daily.plist",
    "testing": REPO / "config" / "com.radon.testing-daily.plist",
    "ci-performance": REPO / "config" / "com.radon.ci-performance-daily.plist",
    "documentation": REPO / "config" / "com.radon.documentation-daily.plist",
    "security": REPO / "config" / "com.radon.security-daily.plist",
}
# Every nightly loop wrapper. A new loop that is not registered here inherits
# none of the dead-man contract below, which is the whole reason the two
# original loops have it.
LOOPS = {
    "reliability": RELIABILITY,
    "testing": TESTING,
    "ci-performance": CI_PERFORMANCE,
    "documentation": DOCUMENTATION,
    "security": SECURITY,
}


def _uncommented(path: Path) -> str:
    return "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


BASH = shutil.which("bash") or "/bin/bash"


def _rotation_block(path: Path) -> str:
    """The rotation pipeline lifted verbatim, so a test can RUN it."""
    text = path.read_text(encoding="utf-8")
    start = text.index('ls -1t "$LOG_DIR"')
    end = text.index("\ndone\n", start) + len("\ndone\n")
    return text[start:end]


def _fake_runner_clone(tmp_path: Path, name: str) -> Path:
    """A marker-bearing clone whose runner lock is held by a LIVE pid."""
    repo = tmp_path / f"radon-{name}"
    repo.mkdir()
    (repo / ".radon-weekend-runner").write_text("", encoding="utf-8")
    # REL-180 (R-504): every wrapper requires its OWN loop marker as well; a
    # generic clone carries all five so each wrapper finds its own.
    for marker in (".radon-security-runner", ".radon-reliability-runner", ".radon-testing-runner",
                   ".radon-ci-performance-runner", ".radon-documentation-runner"):
        (repo / marker).write_text("", encoding="utf-8")
    lock = repo / ".weekend-runner.lock"
    lock.mkdir()
    # This process is alive, so acquire_runner_lock cannot reclaim the lock.
    (lock / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
    return repo


def _stub_bin(tmp_path: Path, gh_log: Path) -> Path:
    """`gh` that records every call; `claude` that can never run for real."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{gh_log}"\n'
        'if [ "$1 $2" = "issue list" ]; then echo 4242; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    claude = bin_dir / "claude"
    claude.write_text("#!/bin/sh\necho 'stub claude must never run' >&2\nexit 9\n", encoding="utf-8")
    claude.chmod(0o755)
    return bin_dir


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
        # Counting occurrences is satisfied by a duplicated dead line, so
        # check WHERE they sit relative to the round they must guard. T-209.
        phase = body[body.index("run_phase() {"):]
        phase = phase[: phase.index("\n}\n") + 3]
        rounds = [m.start() for m in re.finditer(r"^\s*run_round\s*$", phase, re.M)]
        assert rounds, "run_phase no longer calls run_round"
        guards = [m.start() for m in re.finditer(r"CYCLE_DEADLINE - CAP_SECS", phase)]
        assert any(g < rounds[0] for g in guards), (
            "round 1 of a phase is launched without checking the deadline, so "
            "a cycle already past its budget in the audit phase still runs a "
            f"full remediate round: guards at {guards}, first round at {rounds[0]}"
        )
        assert any(g > rounds[0] for g in guards), (
            "the continuation loop relaunches rounds without rechecking the "
            f"deadline: guards at {guards}, first round at {rounds[0]}"
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
    def test_a_held_lock_is_reported_not_just_exited(self, name, tmp_path):
        """T-209: run the prologue against a held lock and watch `gh`.

        `"report" in lock_branch` matches any identifier containing "report",
        and matched while the call itself was dead: report() interpolates
        PHASE/STAMP/RUN_LOG, none of which exist yet in the prologue, so
        `set -u` killed the shell before the first gh call.
        """
        repo = _fake_runner_clone(tmp_path, name)
        gh_log = tmp_path / "gh.log"
        bin_dir = _stub_bin(tmp_path, gh_log)
        proc = subprocess.run(
            [BASH, str(LOOPS[name]), "cycle"],
            env={
                **os.environ,
                "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                "RADON_WEEKEND_REPO": str(repo),
                "RADON_WEEKEND_PROVIDER_LADDER": CLAUDE_RUNG_LADDER,
            },
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 3, (proc.returncode, proc.stdout, proc.stderr)
        calls = gh_log.read_text(encoding="utf-8") if gh_log.exists() else ""
        assert "issue comment" in calls, (
            "a stale pid reused by a live unrelated process makes every daily "
            "fire exit 3 in under a second, with no dead-man comment at all: "
            f"gh calls={calls!r} stderr={proc.stderr!r}"
        )
        assert str(os.getpid()) in calls, (
            "the dead-man comment must name the holding pid, or the operator "
            f"cannot tell a live cycle from a reused pid: {calls!r}"
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
        assert len(set(paths.values())) == 1, paths


class TestRotationSparesTheLaunchdSinks:
    """R-267, T-209: the rotation block is EXECUTED here, not grepped.

    Asserting `"launchd-cycle" in rotation` is satisfied by the exact inverse
    behaviour: flipping the block's `grep -v` to `grep` rotates ONLY the two
    sinks, deleting exactly the forensics this class exists to protect, and
    the substring is still there.
    """

    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_rotation_prunes_run_logs_and_keeps_the_launchd_sinks(self, name, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        base = 1_700_000_000
        for i in range(38):
            run_log = log_dir / f"audit-20260827T{i:06d}.log"
            run_log.write_text("run log", encoding="utf-8")
            os.utime(run_log, (base + i, base + i))
        # The sinks sort OLDEST: launchd-cycle.err only gets an mtime bump
        # when something writes to stderr, so age alone never spares them.
        for sink in ("launchd-cycle.log", "launchd-cycle.err"):
            path = log_dir / sink
            path.write_text("forensics", encoding="utf-8")
            os.utime(path, (base - 1, base - 1))

        proc = subprocess.run(
            [BASH, "-c", "set -Eeuo pipefail\n" + _rotation_block(LOOPS[name])],
            env={**os.environ, "LOG_DIR": str(log_dir)},
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr

        after = {entry.name for entry in log_dir.iterdir()}
        for sink in ("launchd-cycle.log", "launchd-cycle.err"):
            assert sink in after, (
                f"{name}: rotation unlinked {sink}. The plists point "
                "StandardOutPath/StandardErrorPath there, so the only "
                "forensics for a prologue death are gone — and gone BEFORE "
                "run_phase, so the rest of that invocation writes to a "
                f"deleted inode: {sorted(after)}"
            )
            assert (log_dir / sink).read_text(encoding="utf-8") == "forensics"
        survivors = sorted(entry for entry in after if entry.startswith("audit-"))
        assert len(survivors) == 30, f"{name}: kept {len(survivors)} run logs: {survivors}"
        assert survivors[0] == "audit-20260827T000008.log", survivors[0]


class TestSetupGuardsPerLoopVenvs:
    """Each full-permission loop prepends its own venv to PATH.

    The five setups previously wrote one `$WEEKEND_ROOT/venv`. The shared
    path is not deleted here (operator follow-up). Sibling in-flight
    locks stay so a setup still stands down on a live sibling clone.
    """

    SETUPS = {
        "reliability": REPO / "scripts" / "setup_reliability_weekend.sh",
        "testing": REPO / "scripts" / "setup_testing_weekend.sh",
        "ci-performance": REPO / "scripts" / "setup_ci_performance.sh",
        "documentation": REPO / "scripts" / "setup_documentation_nightly.sh",
        "security": REPO / "scripts" / "setup_security_nightly.sh",
    }
    WRAPPERS = {
        "reliability": REPO / "scripts" / "reliability_weekend.sh",
        "testing": REPO / "scripts" / "testing_weekend.sh",
        "ci-performance": REPO / "scripts" / "ci_performance_nightly.sh",
        "documentation": REPO / "scripts" / "documentation_nightly.sh",
        "security": REPO / "scripts" / "security_nightly.sh",
    }
    VENV_DIR = {
        "reliability": "$WEEKEND_ROOT/venv-reliability",
        "testing": "$WEEKEND_ROOT/venv-testing",
        "ci-performance": "$WEEKEND_ROOT/venv-ci-performance",
        "documentation": "$WEEKEND_ROOT/venv-documentation",
        "security": "$WEEKEND_ROOT/venv-security",
    }

    def test_each_setup_writes_a_distinct_venv(self):
        venvs = {
            name: re.search(r'WEEKEND_VENV="([^"]+)"', path.read_text(encoding="utf-8")).group(1)
            for name, path in self.SETUPS.items()
        }
        assert venvs == self.VENV_DIR, venvs
        assert len(set(venvs.values())) == len(venvs)
        assert "$WEEKEND_ROOT/venv" not in venvs.values()

    def test_each_wrapper_prepends_its_own_venv(self):
        for name, path in self.WRAPPERS.items():
            body = path.read_text(encoding="utf-8")
            match = re.search(r'^VENV="([^"]+)"', body, re.M)
            assert match, f"{name} wrapper lost VENV="
            assert match.group(1) == self.VENV_DIR[name], (name, match.group(1))
            assert 'VENV="$WEEKEND_ROOT/venv"' not in body

    def test_no_setup_deletes_the_legacy_shared_venv(self):
        for name, path in self.SETUPS.items():
            uncommented = "\n".join(
                line for line in path.read_text(encoding="utf-8").splitlines()
                if not line.lstrip().startswith("#")
            )
            assert not re.search(r"\brm\b.*\$WEEKEND_ROOT/venv\b", uncommented), (
                f"{name} setup deletes the legacy shared venv; operator removal "
                "is a follow-up after this ships"
            )

    @pytest.mark.parametrize("name", ["reliability", "testing", "ci-performance", "documentation", "security"])
    def test_each_setup_checks_the_sibling_clone_lock(self, name):
        # Comments stripped first: the guard's own comment quotes the
        # `python3.13 -m venv` line it protects, and a naive slice ends there.
        body = "\n".join(
            line for line in self.SETUPS[name].read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )
        install = body[: body.index("python3.13 -m venv")]
        assert "SIBLING_REPO" in install, (
            f"{name} setup re-creates the venv without checking whether "
            "the other loop's cycle is executing against it"
        )

    @pytest.mark.parametrize("name", ["reliability", "testing", "ci-performance", "documentation", "security"])
    def test_each_setup_checks_the_bash_version(self, name):
        """GAP C: `/bin/bash` on this runner is 3.2, and `cloud/tests` needs 4+.

        `cloud/scripts/operator-radon.sh` uses `mapfile` and
        `cloud/scripts/bootstrap-control-plane.sh` uses `exec {fd}<>`; both
        are bash-4 only, and both suites resolve bash from `PATH` themselves.
        Neither setup ever reads `BASH_VERSINFO`, so the runner installs
        clean and 34 `cloud/tests` are permanently red with no signal.
        """
        text = self.SETUPS[name].read_text(encoding="utf-8")
        assert "BASH_VERSINFO" in text, (
            f"{name} setup verifies the toolchain without checking the bash "
            "version; on bash 3.2 the cloud suite is permanently red and the "
            "install prints ok"
        )
        assert "cloud/tests" in text, (
            f"{name} setup never names the consequence: an operator reading "
            "MISSING has no way to know which suite goes red"
        )


def _claude_invocation_line(path: Path) -> str:
    """The `"$TIMEOUT_BIN" ... claude -p` command lifted verbatim, so a test can RUN it.

    The invocation is a multi-line backslash continuation; join it back into
    one command so it can be handed to `bash -c`.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    # 2026-09-06: the launch moved into launch_round()'s claude arm and the
    # binary is now "$RUNG_BIN", so "claude -p" is no longer literal. The
    # ceiling assignment is what identifies the claude launch line.
    start = next(
        i
        for i, line in enumerate(lines)
        if "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0" in line
        and "TIMEOUT_BIN" in line
        and not line.lstrip().startswith("#")
    )
    out = []
    for line in lines[start:]:
        out.append(line.rstrip().rstrip("\\").rstrip())
        if not line.rstrip().endswith("\\"):
            break
    return " ".join(part.strip() for part in out)


def _phase_status_block(path: Path) -> str:
    """`phase_status` lifted verbatim, so a test can RUN it rather than grep it."""
    text = path.read_text(encoding="utf-8")
    assert "phase_status() {" in text and "BG_CEILING_MARKER=" in text, (
        f"{path.name} has no phase_status/BG_CEILING_MARKER pair, so the "
        "status the dead-man channels carry is keyed on the agent's exit code "
        "alone — and `claude -p` exits 0 after killing unfinished background "
        "work. That is the T-239 defect, not a renamed helper."
    )
    # From the marker constant, so the block is self-contained: a marker that
    # drifts away from the function it feeds would otherwise pass here and
    # fail under `set -u` in production.
    start = text.index("BG_CEILING_MARKER=")
    end = text.index("\n}\n", text.index("phase_status() {", start)) + len("\n}\n")
    return text[start:end]


class TestBackgroundWorkIsNotSilentlyKilled:
    """T-239: the 2026-08-28 audit phase filed nothing and reported OK.

    `claude -p` terminates unfinished background tasks at its print-mode
    background-wait ceiling (600 s by default), prints
    `Background tasks still running after 600s; terminating.` and then exits
    **0**. The wrapper keys its dead-man status purely on that exit code, so a
    phase cut off with its last agent still working is indistinguishable from
    one that finished. That run left `origin/testing/2026-08-28` an empty
    branch, no `## Delta audit 2026-08-28` section, no ledger line and no PR,
    against a 24-commit / 262-file delta — and paged **OK**.

    Two independent guarantees, both executed here rather than grepped:
    prevention (the ceiling is lifted, so the harness waits and the `timeout`
    remains the only cap) and honesty (if it is ever cut off anyway, the
    status the operator sees is not "OK").
    """

    MARKER = "Background tasks still running after"

    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_the_ceiling_is_lifted_for_the_real_child_process(self, name, tmp_path):
        """RUN the lifted invocation with a stub `claude` that records its env.

        A source grep for the variable name is satisfied by an assignment that
        never reaches the child — e.g. one set in a subshell, or after the
        call. This spawns the process and reads the value back out of it.
        """
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        env_log = tmp_path / "child-env.txt"
        claude = bin_dir / "claude"
        claude.write_text(
            "#!/bin/sh\n"
            f'printf "%s" "${{CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS-<unset>}}" > "{env_log}"\n'
            "exit 0\n",
            encoding="utf-8",
        )
        claude.chmod(0o755)
        run_log = tmp_path / "phase.log"
        proc = subprocess.run(
            [
                BASH,
                "-c",
                "set -Eeuo pipefail\n"
                f'PHASE=audit\nremain=30\nRUN_LOG="{run_log}"\n'
                f'KILL_AFTER_SECS=60\n'
                'TIMEOUT_BIN="$(command -v timeout)"\n'
                # The line also pins the rung it asks for, so the round's
                # ladder state has to exist here too — under `set -u` an unset
                # RUNG_MODEL kills the command before `claude` is ever reached
                # and this test would pass no judgement on the ceiling at all.
                'RUNG_BIN=claude\nRUNG_MODEL=stub-model\nLOOP_SKILL=stub-loop\n'
                # Since REL-137 the round is backgrounded so bash can act on a
                # SIGTERM while it is running; wait for it before reading back.
                + _claude_invocation_line(LOOPS[name])
                + "\nwait",
            ],
            env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"},
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
        seen = env_log.read_text(encoding="utf-8") if env_log.exists() else "<never ran>"
        assert seen == "0", (
            f"{name}: the agent child sees "
            f"CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS={seen!r}. At anything other "
            "than 0 the harness kills unfinished background work after that "
            "many milliseconds and exits 0 anyway, so a phase can be cut in "
            "half and still page OK — which is exactly what happened to the "
            "2026-08-28 audit. The `timeout $remain` above is the cap; the "
            "harness ceiling must not be a second, shorter, silent one."
        )

    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_a_truncated_phase_is_not_reported_as_ok(self, name, tmp_path):
        """RUN `phase_status` over a log carrying the real harness message."""
        block = _phase_status_block(LOOPS[name])
        truncated = tmp_path / "truncated.log"
        truncated.write_text(
            "[weekend] audit start 20260828T000007 repo=/x cap=7200s\n"
            "Background tasks still running after 600s; terminating. Set "
            "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0 to wait indefinitely.\n"
            "Last agent still working. Waiting on it and the drain monitor.\n",
            encoding="utf-8",
        )
        clean = tmp_path / "clean.log"
        clean.write_text("[weekend] audit start\nall done\n", encoding="utf-8")

        def status(rc: int, log: Path) -> str:
            proc = subprocess.run(
                [
                    BASH,
                    "-c",
                    "set -Eeuo pipefail\nCAP_SECS=7200\n"
                    + block
                    + f'\nphase_status {rc} "{log}"\n',
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert proc.returncode == 0, (proc.returncode, proc.stderr)
            return proc.stdout.strip()

        assert status(0, truncated) != "OK", (
            f"{name}: a phase the harness cut off mid-flight reported OK. The "
            "dead-man channels then say the run succeeded, and the operator "
            "cannot tell it from a real one without opening the branch."
        )
        assert "TRUNCATED" in status(0, truncated), status(0, truncated)
        # The other three classifications must be unchanged.
        assert status(0, clean) == "OK", status(0, clean)
        assert status(124, clean) == "TIMEOUT after 7200s", status(124, clean)
        assert status(9, clean) == "FAILED (exit 9)", status(9, clean)
        # A truncated run that ALSO timed out is a timeout, not a truncation:
        # the cap is the more specific fact and it already implies partial work.
        assert status(124, truncated) == "TIMEOUT after 7200s", status(124, truncated)


INCOMPLETE_STATUS = "INCOMPLETE (agent exited 0 without committing to the nightly branch)"


def _committing_clone(tmp_path: Path, git: str) -> Path:
    """A marker-bearing runner clone with a REAL git history.

    `main` has one commit and `origin/main` points at it, so the wrapper's
    `checkout -f main` / `reset --hard origin/main` / `clean` all run for
    real; only `fetch` is stubbed out (see `_committing_stub_bin`).
    """
    repo = tmp_path / "radon-testing"
    (repo / "scripts").mkdir(parents=True)
    (repo / ".radon-weekend-runner").write_text("", encoding="utf-8")
    # REL-180 (R-504): the testing wrapper requires its own loop marker too.
    (repo / ".radon-testing-runner").write_text("", encoding="utf-8")
    wrapper = repo / "scripts" / "testing_weekend.sh"
    shutil.copy2(TESTING, wrapper)
    wrapper.chmod(wrapper.stat().st_mode | 0o100)
    (tmp_path / ".env").write_text(
        "PUSHOVER_USER=test-user\nPUSHOVER_TOKEN=test-token\n", encoding="utf-8"
    )
    curl_stub = tmp_path / "bin" / "curl"
    (tmp_path / "bin").mkdir(exist_ok=True)
    wrapper.write_text(
        wrapper.read_text(encoding="utf-8").replace("/usr/bin/curl", str(curl_stub)),
        encoding="utf-8",
    )
    (repo / "scripts" / "weekend_notify.py").write_text("# unused\n", encoding="utf-8")
    subprocess.run([git, "init", "-q", str(repo)], check=True, env=_GIT_ENV)
    at = [git, "-C", str(repo)]
    subprocess.run([*at, "symbolic-ref", "HEAD", "refs/heads/main"], check=True, env=_GIT_ENV)
    subprocess.run(
        [*at, "add", "-f",
         "scripts/testing_weekend.sh",
         "scripts/weekend_notify.py",
         ".radon-weekend-runner",
         ".radon-testing-runner"],
        check=True,
        env=_GIT_ENV,
    )
    subprocess.run([*at, "commit", "-q", "-m", "main tip"], check=True, env=_GIT_ENV)
    subprocess.run([*at, "update-ref", "refs/remotes/origin/main", "HEAD"], check=True, env=_GIT_ENV)
    return repo


_GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}


def _curl_log_stub(log: Path) -> str:
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


def _committing_stub_bin(tmp_path: Path, *, claude_body: str) -> tuple[Path, Path, Path]:
    """`gh` / curl record their calls; `git` is REAL except `fetch`."""
    real_git = shutil.which("git")
    assert real_git, "a real git is required to exercise the commit check"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    gh_log = tmp_path / "gh.log"
    py_log = tmp_path / "py.log"
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{gh_log}"\n'
        'if [ "$1 $2" = "issue list" ]; then echo 4242; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    py = bin_dir / "python3"
    py.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{py_log}"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    py.chmod(0o755)
    curl = bin_dir / "curl"
    curl.write_text(_curl_log_stub(py_log), encoding="utf-8")
    curl.chmod(0o755)
    git = bin_dir / "git"
    git.write_text(
        "#!/bin/sh\n"
        'for a in "$@"; do [ "$a" = fetch ] && exit 0; done\n'
        f'exec "{real_git}" "$@"\n',
        encoding="utf-8",
    )
    git.chmod(0o755)
    claude = bin_dir / "claude"
    claude.write_text(claude_body, encoding="utf-8")
    claude.chmod(0o755)
    return bin_dir, gh_log, py_log


class TestAnAgentThatCommitsNothingIsNotReportedOk:
    """T-379 (T-239 recurring): the 2026-08-31 audit exited 0 and did nothing.

    `claude -p` answered a mid-run nudge with text and no tool call, print
    mode treated that as the end of the turn, and the phase ended after 18
    minutes with zero commits, no ledger advance and no PR. rc was 0 and the
    ceiling marker was absent, so `phase_status` said OK and every dead-man
    channel repeated it. SKILL.md's contract is that every phase commits at
    least once on the nightly branch (audit: ledger line + PR, even for an
    empty range; remediate: the gate-count rows), so "exit 0 and no commit
    landed during the phase" is INCOMPLETE, not OK.

    Executed, not grepped: the whole wrapper runs against a real-git clone
    with a stub `claude`, and the status is read back off the `gh issue
    comment` body and the Pushover call. Only the testing wrapper is under
    test here; the other four carry the same gap and are reported, not fixed.
    """

    def _run(self, tmp_path: Path, claude_body: str) -> tuple[subprocess.CompletedProcess, str, str]:
        repo = _committing_clone(tmp_path, shutil.which("git"))
        bin_dir, gh_log, py_log = _committing_stub_bin(tmp_path, claude_body=claude_body)
        proc = subprocess.run(
            [BASH, str(repo / "scripts" / "testing_weekend.sh"), "audit"],
            env={
                **_GIT_ENV,
                "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                "RADON_WEEKEND_REPO": str(repo),
                "RADON_WEEKEND_PROVIDER_LADDER": CLAUDE_RUNG_LADDER,
            },
            capture_output=True,
            text=True,
            timeout=120,
        )
        calls = gh_log.read_text(encoding="utf-8") if gh_log.exists() else ""
        pages = py_log.read_text(encoding="utf-8") if py_log.exists() else ""
        return proc, calls, pages

    def test_exit_0_with_no_commit_is_reported_incomplete_on_both_channels(self, tmp_path):
        proc, calls, pages = self._run(
            tmp_path,
            "#!/bin/sh\n"
            "echo 'Draft findings numbered T-346..T-378 are ready; wait on its completion.'\n"
            "exit 0\n",
        )
        # An unfinished phase must not tell launchd it succeeded either: the
        # wrapper reports INCOMPLETE on both channels AND exits 75.
        assert proc.returncode == 75, (proc.returncode, proc.stdout, proc.stderr)
        comment = next((ln for ln in calls.splitlines() if ln.startswith("issue comment")), "")
        assert comment, f"no dead-man comment at all: {calls!r} {proc.stderr!r}"
        assert "**audit**" in calls and "**INCOMPLETE" in calls, (
            f"no PHASE status dead-man comment: {calls!r} {proc.stderr!r}"
        )
        assert "**Issue discovered**" not in calls, calls
        assert INCOMPLETE_STATUS in calls, (
            "the agent exited 0 having committed nothing — no ledger line, no "
            f"PR — and the issue comment did not say so: {calls!r}"
        )
        assert "Nothing went wrong" not in calls, calls
        assert INCOMPLETE_STATUS in pages, (
            f"the Pushover page must carry the same status: {pages!r}"
        )
        assert "message=OK" not in pages, pages

    def test_a_commit_on_the_nightly_branch_during_the_phase_is_ok(self, tmp_path):
        proc, calls, pages = self._run(
            tmp_path,
            "#!/bin/sh\n"
            "git checkout -q -b testing/2026-08-31\n"
            "git commit -q --allow-empty -m 'T-379 stub: ledger line'\n"
            "echo 'ledger appended, PR opened'\n"
            "exit 0\n",
        )
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
        assert "**audit**" in calls and "**OK**" in calls, (
            f"a phase that committed must still report: {calls!r} {proc.stderr!r}"
        )
        assert "**Issue discovered**" not in calls, calls
        assert "Nothing went wrong this audit phase." not in calls, calls
        assert INCOMPLETE_STATUS not in calls, calls
        assert "message=OK" in pages, pages
        assert INCOMPLETE_STATUS not in pages, pages
