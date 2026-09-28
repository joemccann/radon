"""The weekend loops' own dead-man contract.

R-237: `ground_truth` is called inside `run_phase` with `trap on_crash ERR`
armed. `on_crash` reports and returns 0, but under `set -Eeuo pipefail` the
shell exits anyway, so `run_phase audit` never returns and `run_phase
remediate` is never reached — contradicting the comment directly above it
("Remediate runs regardless of the audit rc"). The operator sees one
`audit ... CRASHED` comment and then complete silence, which is
indistinguishable from the remediate phase hanging.

R-239: everything between `main()` entry and `run_phase` runs with NO ERR trap
— `cd`, the marker check, `acquire_runner_lock`, `mkdir`, the rotation
pipeline — so a full disk, a moved clone or a held lock exits with nothing but
a line on stderr. The lock branch is the expensive one: a recorded pid reused
by any live unrelated process makes every subsequent daily fire exit 3 in
under a second, silently.

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
SECURITY = REPO / "scripts" / "security_nightly.sh"
SECURITY_DEEPSEC = REPO / "scripts" / "security_deepsec_nightly.sh"
PLISTS = {
    "security": REPO / "config" / "com.radon.security-daily.plist",
    "security-deepsec": REPO / "config" / "com.radon.security-deepsec.plist",
}
# Every nightly loop wrapper. A new loop that is not registered here inherits
# none of the dead-man contract below, which is the whole reason the two
# original loops have it.
LOOPS = {
    "security": SECURITY,
    "security-deepsec": SECURITY_DEEPSEC,
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
    for marker in (".radon-security-runner", ".radon-security-deepsec-runner"):
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


class TestThePlistsAgreeOnPath:
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
        "security": REPO / "scripts" / "setup_security_nightly.sh",
    }
    WRAPPERS = {
        "security": REPO / "scripts" / "security_nightly.sh",
        "security-deepsec": REPO / "scripts" / "security_deepsec_nightly.sh",
    }
    VENV_DIR = {
        "security": "$WEEKEND_ROOT/venv-security",
        # Provisioned by setup_security_nightly.sh alongside venv-security.
        "security-deepsec": "$WEEKEND_ROOT/venv-security-deepsec",
    }

    def test_each_setup_writes_a_distinct_venv(self):
        venvs = {
            name: re.search(r'WEEKEND_VENV="([^"]+)"', path.read_text(encoding="utf-8")).group(1)
            for name, path in self.SETUPS.items()
        }
        assert venvs == {name: self.VENV_DIR[name] for name in self.SETUPS}, venvs
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

    @pytest.mark.parametrize("name", ["security"])
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

    @pytest.mark.parametrize("name", ["security"])
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
