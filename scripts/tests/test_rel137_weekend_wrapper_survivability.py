"""R-384/385/386/409/410/411/426 / REL-137: the weekend loop cannot die quietly.

Every finding here is about the wrapper's own failure modes, which are exactly
the ones nobody sees: a SIGTERM produced no dead-man comment and no page, so
launchd's ExitTimeOut kill and "the runner did not fire" were the same
observable; the cap was advisory because `timeout` sent only SIGTERM and only to
`claude`; the runner lock had a create-then-write race and released without
checking ownership; every network call in the dead-man channel itself was
unbounded; the four TERMINAL failure reports suppressed Pushover; and the
TRUNCATED detector grepped a cumulative per-phase log, so a round-1 ceiling
message made a finished round 8 report TRUNCATED forever.

Each item applies to BOTH twins.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RELIABILITY = REPO / "scripts" / "reliability_weekend.sh"
TESTING = REPO / "scripts" / "testing_weekend.sh"
CI_PERFORMANCE = REPO / "scripts" / "ci_performance_nightly.sh"
DOCUMENTATION = REPO / "scripts" / "documentation_nightly.sh"
LOOPS = {
    "reliability": RELIABILITY,
    "testing": TESTING,
    "ci-performance": CI_PERFORMANCE,
    "documentation": DOCUMENTATION,
}
BASH = shutil.which("bash") or "/bin/bash"


def _uncommented(path: Path) -> str:
    return "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


def _runner_clone(tmp_path: Path, name: str) -> Path:
    repo = tmp_path / f"radon-{name}"
    (repo / "scripts").mkdir(parents=True)
    (repo / ".radon-weekend-runner").write_text("", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return repo


def _stub_bin(tmp_path: Path, *, claude_body: str) -> tuple[Path, Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
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
    claude = bin_dir / "claude"
    claude.write_text(claude_body, encoding="utf-8")
    claude.chmod(0o755)
    git = bin_dir / "git"
    git.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    git.chmod(0o755)
    return bin_dir, gh_log, py_log


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


# --- (a) R-384: a signalled cycle reports its own death ----------------------


class TestSignalledRunReportsItsDeath:
    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_int_term_and_hup_are_all_trapped(self, name):
        body = _uncommented(LOOPS[name])
        traps = re.findall(r"^\s*trap\s+(.+)$", body, re.M)
        signalled = [t for t in traps if re.search(r"\b(INT|TERM|HUP)\b", t)]
        assert signalled, (
            "only ERR and EXIT are trapped, so a SIGTERM from launchd's "
            f"ExitTimeOut, a bootout or a reboot reports nothing: {traps}"
        )
        for sig in ("INT", "TERM", "HUP"):
            assert any(re.search(rf"\b{sig}\b", t) for t in signalled), (sig, signalled)

    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_a_sigterm_mid_phase_posts_a_comment_and_pages(self, name, tmp_path):
        repo = _runner_clone(tmp_path, name)
        started = tmp_path / "claude-started"
        bin_dir, gh_log, py_log = _stub_bin(
            tmp_path,
            claude_body=f"#!/bin/sh\ntouch {started}\nsleep 60\n",
        )
        proc = subprocess.Popen(
            [BASH, str(LOOPS[name]), "audit"],
            env={
                **os.environ,
                "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                "RADON_WEEKEND_REPO": str(repo),
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 60
        while not started.exists() and time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            time.sleep(0.2)
        assert started.exists(), (proc.poll(), proc.communicate(timeout=10))

        proc.send_signal(signal.SIGTERM)
        try:
            proc.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            pytest.fail("the wrapper did not exit within 60s of SIGTERM")

        calls = gh_log.read_text(encoding="utf-8") if gh_log.exists() else ""
        assert "issue comment" in calls, f"no dead-man comment on SIGTERM: {calls!r}"
        assert "KILLED" in calls, calls
        pages = py_log.read_text(encoding="utf-8") if py_log.exists() else ""
        assert "weekend_notify.py" in pages, f"no Pushover on SIGTERM: {pages!r}"
        assert proc.returncode == 143, proc.returncode
        assert not (repo / ".weekend-runner.lock").exists(), "the lock was not released"


# --- (b) R-386: the cap is enforceable --------------------------------------


class TestTheCapIsEnforceable:
    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_timeout_escalates_to_sigkill(self, name):
        body = _uncommented(LOOPS[name])
        line = next(ln for ln in body.splitlines() if "timeout" in ln and "claude" in ln)
        assert re.search(r"-k\s+\"?\$?\{?[A-Za-z0-9_]", line), (
            "no --kill-after: only SIGTERM is sent, so a claude blocked on a "
            f"hung child makes the cap advisory: {line}"
        )
        # R-386 asked for `--foreground` as well. It is deliberately NOT used:
        # --foreground stops timeout(1) creating its own process group and makes
        # it signal only its direct child, which is the opposite of what reaping
        # orphaned subagents needs. Without it, timeout IS the group leader, so
        # the negative-pid kill below reaches everything the round spawned.
        assert "--foreground" not in line, line

    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_the_remaining_process_group_is_killed_after_the_wait(self, name):
        body = _uncommented(LOOPS[name])
        assert "kill_round_group" in body, (
            "orphaned grandchildren keep writing into the clone while the next "
            "round runs git clean -fdq"
        )
        assert re.search(r'kill -TERM -- "-\$ROUND_PID"', body), body
        # The round must be waited on, not run in the foreground: bash defers
        # trap handling until a foreground child completes, so a SIGTERM to the
        # wrapper was not acted on until claude finished on its own.
        assert re.search(r"^\s*wait \"\$ROUND_PID\"", body, re.M), body

    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_the_short_deadline_override_is_test_only_and_bounded(self, name):
        body = _uncommented(LOOPS[name])
        assert "RADON_WEEKEND_TEST_ROUND_TIMEOUT_SECS" in body, body
        assert "PYTEST_CURRENT_TEST" in body, (
            "a short deadline must fail closed outside pytest so production "
            "cannot silently weaken its phase cap"
        )
        assert re.search(r'RADON_WEEKEND_TEST_ROUND_TIMEOUT_SECS.*1\|2\|5', body), body
        assert "if [[ $remain -le 60 ]]" in body, (
            "production must retain at least a full minute before launching a round"
        )

    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_a_claude_that_ignores_term_still_ends_the_round(self, name, tmp_path):
        repo = _runner_clone(tmp_path, name)
        pids_file = tmp_path / "claude-pids"
        bin_dir, gh_log, _py = _stub_bin(
            tmp_path,
            claude_body=(
                "#!/bin/sh\n"
                "trap '' TERM\n"
                "sleep 600 &\n"
                "child=$!\n"
                f'printf "%s %s\\n" "$$" "$child" > "{pids_file}"\n'
                'wait "$child"\n'
            ),
        )
        start = time.monotonic()
        proc = subprocess.run(
            [BASH, str(LOOPS[name]), "audit"],
            env={
                **os.environ,
                "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                "RADON_WEEKEND_REPO": str(repo),
                "RADON_WEEKEND_AUDIT_CAP_SECS": "70",
                "RADON_WEEKEND_KILL_AFTER_SECS": "1",
                # One second is enough in isolation but can expire before the
                # stub records its process tree under the full xdist load.
                # Five remains bounded and cuts more than two minutes from the
                # old production-length regression without scheduler flake.
                "RADON_WEEKEND_TEST_ROUND_TIMEOUT_SECS": "5",
            },
            capture_output=True,
            text=True,
            timeout=30,
        )
        elapsed = time.monotonic() - start
        assert 1 <= elapsed < 20, f"the test-only deadline did not bite: {elapsed}s"
        calls = gh_log.read_text(encoding="utf-8") if gh_log.exists() else ""
        assert "TIMEOUT" in calls, (calls, proc.stdout, proc.stderr)
        assert not (repo / ".weekend-runner.lock").exists(), "the lock was not released"

        assert pids_file.exists(), (proc.stdout, proc.stderr)
        pids = [int(value) for value in pids_file.read_text(encoding="utf-8").split()]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if all(not _pid_exists(pid) for pid in pids):
                break
            time.sleep(0.05)
        assert all(not _pid_exists(pid) for pid in pids), (
            "timeout left the ignored-SIGTERM claude process or its child alive",
            pids,
        )

    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_short_deadline_override_is_refused_outside_pytest(self, name, tmp_path):
        repo = _runner_clone(tmp_path, name)
        started = tmp_path / "claude-started"
        bin_dir, gh_log, py_log = _stub_bin(
            tmp_path,
            claude_body=f"#!/bin/sh\ntouch {started}\nexit 0\n",
        )
        env = {
            key: value
            for key, value in os.environ.items()
            if key != "PYTEST_CURRENT_TEST"
        }
        proc = subprocess.run(
            [BASH, str(LOOPS[name]), "audit"],
            env={
                **env,
                "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
                "RADON_WEEKEND_REPO": str(repo),
                "RADON_WEEKEND_TEST_ROUND_TIMEOUT_SECS": "1",
            },
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert proc.returncode == 2, (proc.stdout, proc.stderr)
        assert "test-only" in proc.stderr, proc.stderr
        assert not started.exists(), "the agent launched despite a refused test override"
        assert not (repo / ".weekend-runner.lock").exists(), "the lock was not released"
        calls = gh_log.read_text(encoding="utf-8") if gh_log.exists() else ""
        assert "REFUSED" in calls, f"the fail-closed refusal was not reported: {calls!r}"
        pages = py_log.read_text(encoding="utf-8") if py_log.exists() else ""
        assert "weekend_notify.py" in pages, f"the fail-closed refusal did not page: {pages!r}"


# --- (c) R-385: the continuation re-ground cannot end the run silently -------


class TestContinuationRegroundIsGuarded:
    def test_the_reground_is_wrapped_and_clears_a_stale_index_lock(self):
        body = _uncommented(RELIABILITY)
        assert "rm -f .git/index.lock" in body, (
            "the cap SIGTERMs claude mid-commit, leaving .git/index.lock; the "
            "next round's checkout then fails"
        )
        # Everything after `trap - ERR` runs with errexit and no ERR trap, so a
        # BARE git call there ends the run with nothing reported at all.
        tail = body[body.index("trap - ERR"):]
        bare = [
            ln.strip() for ln in tail.splitlines()
            if re.match(r"^\s*git\s+(checkout|reset|clean)\b", ln)
        ]
        assert not bare, bare
        assert "reground_for_continuation" in tail, tail
        # ...and its failure is reported rather than swallowed.
        idx = tail.index("reground_for_continuation")
        line_start = tail.rindex("\n", 0, idx) + 1
        call = tail[line_start:]
        assert call.lstrip().startswith("if ! reground_for_continuation"), call[:120]
        assert "report" in call[: call.index("fi")], call[: call.index("fi")]


# --- (d) R-409: every network call is bounded -------------------------------


class TestNetworkCallsAreBounded:
    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_no_unbounded_gh_or_git_network_call(self, name):
        body = _uncommented(LOOPS[name])
        offenders = []
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith(("echo ", "printf ", "log_")):
                continue  # a message that NAMES a command is not a call
            for match in re.finditer(r"\b(gh|git)\s+(?!-c\b)[a-z]", stripped):
                head = stripped[: match.start()]
                if "timeout" in head or "net_bounded" in head:
                    continue
                if match.group(1) == "git" and not re.search(
                    r"\bgit\s+(fetch|push|pull|clone|ls-remote)\b", stripped[match.start():]
                ):
                    continue  # local git (checkout/reset/clean) touches no network
                offenders.append(stripped)
        assert not offenders, (
            "a hung gh issue comment inside the crash handler wedges the wrapper "
            f"WHILE it is reporting its own death: {offenders}"
        )

    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_the_fetch_ssh_command_carries_keepalives(self, name):
        body = _uncommented(LOOPS[name])
        assert "ConnectTimeout" in body, (
            "a VPN flap that establishes TCP and then stalls hangs git fetch "
            "with no keepalive"
        )
        assert "ServerAliveInterval" in body


# --- (e) R-410: terminal failures page --------------------------------------


class TestTerminalFailuresPage:
    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_only_the_interim_round_report_suppresses_the_page(self, name):
        body = _uncommented(LOOPS[name])
        suppressed = [
            ln.strip() for ln in body.splitlines()
            if re.search(r'\breport\b.*"\s+0(\s|$)', ln) or re.search(r"\breport\b.*\s0\s*\|\|", ln)
        ]
        for line in suppressed:
            assert "continuing" in line, (
                "a terminal outcome where the agent never ran must page; only "
                f"an interim continuation round stays issue-only: {line}"
            )


# --- (f) R-411: the lock is atomic and ownership-checked ---------------------


LOCK_LIB = """
set -Eeuo pipefail
source %s --lock-lib-only
"""


def _lock_lib(script: Path, snippet: str, tmp_path: Path) -> subprocess.CompletedProcess:
    runner = tmp_path / "lockcheck.sh"
    runner.write_text((LOCK_LIB % script) + snippet, encoding="utf-8")
    return subprocess.run([BASH, str(runner)], capture_output=True, text=True, timeout=60)


class TestRunnerLockIsRaceFree:
    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_the_lock_library_is_sourceable(self, name, tmp_path):
        out = _lock_lib(LOOPS[name], "echo READY\n", tmp_path)
        assert out.returncode == 0, (out.stdout, out.stderr)
        assert "READY" in out.stdout

    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_the_published_lock_always_carries_a_pid(self, name, tmp_path):
        lock = tmp_path / "lock.d"
        out = _lock_lib(
            LOOPS[name],
            f'acquire_runner_lock "{lock}"\ncat "{lock}/pid"\n',
            tmp_path,
        )
        assert out.returncode == 0, (out.stdout, out.stderr)
        assert out.stdout.strip().isdigit(), out.stdout
        assert not list(tmp_path.glob("lock.d.*")), "the staging directory leaked"

    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_a_second_holder_cannot_be_unlocked_by_the_first(self, name, tmp_path):
        """The EXIT trap of run 1 must not rm run 2's lock."""
        lock = tmp_path / "lock.d"
        out = _lock_lib(
            LOOPS[name],
            f'acquire_runner_lock "{lock}"\n'
            f'echo 999999 > "{lock}/pid"\n'
            f'release_runner_lock "{lock}"\n'
            f'[[ -d "{lock}" ]] && echo STILL_HELD || echo UNLOCKED\n',
            tmp_path,
        )
        assert "STILL_HELD" in out.stdout, (out.stdout, out.stderr)

    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_the_owner_can_still_release(self, name, tmp_path):
        lock = tmp_path / "lock.d"
        out = _lock_lib(
            LOOPS[name],
            f'acquire_runner_lock "{lock}"\n'
            f'release_runner_lock "{lock}"\n'
            f'[[ -d "{lock}" ]] && echo STILL_HELD || echo UNLOCKED\n',
            tmp_path,
        )
        assert "UNLOCKED" in out.stdout, (out.stdout, out.stderr)

    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_exactly_one_of_two_simultaneous_acquires_wins(self, name, tmp_path):
        lock = tmp_path / "lock.d"
        # The winner HOLDS the lock while the losers try. A winner that had
        # already exited would leave a genuinely stale lock, and reclaiming
        # that is correct behaviour, not the race under test.
        snippet = (
            f'if acquire_runner_lock "{lock}"; then echo WON; sleep 3; '
            'else echo LOST; fi\n'
        )
        runner = tmp_path / "race.sh"
        runner.write_text((LOCK_LIB % LOOPS[name]) + snippet, encoding="utf-8")
        procs = [
            subprocess.Popen([BASH, str(runner)], stdout=subprocess.PIPE, text=True)
            for _ in range(8)
        ]
        outs = [p.communicate(timeout=90)[0] for p in procs]
        assert sum("WON" in o for o in outs) == 1, outs


# --- (g) R-426: TRUNCATED is scoped to the last invocation ------------------


class TestTruncatedIsScopedToThisRound:
    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_a_round_one_ceiling_message_does_not_taint_a_finished_round(self, name, tmp_path):
        text = LOOPS[name].read_text(encoding="utf-8")
        start = text.index("phase_status() {")
        end = text.index("\n}\n", start) + 3
        block = text[start:end]
        marker = text[text.index('BG_CEILING_MARKER="') :].split('"')[1]

        run_log = tmp_path / "phase.log"
        run_log.write_text(f"round 1 output\n{marker} 600s\n", encoding="utf-8")
        mark = run_log.stat().st_size
        with run_log.open("a", encoding="utf-8") as fh:
            fh.write("round 2 finished cleanly\n")

        runner = tmp_path / "status.sh"
        runner.write_text(
            "set -uo pipefail\n"
            f'BG_CEILING_MARKER="{marker}"\n'
            'CAP_SECS=7200\n'
            f"{block}\n"
            f'phase_status 0 "{run_log}" {mark}\n',
            encoding="utf-8",
        )
        out = subprocess.run([BASH, str(runner)], capture_output=True, text=True, timeout=60)
        assert out.stdout.strip() == "OK", (
            "a round-1 ceiling message in the cumulative per-phase log makes a "
            f"finished later round report TRUNCATED forever: {out.stdout!r} {out.stderr!r}"
        )

    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_a_ceiling_message_in_this_round_still_reports_truncated(self, name, tmp_path):
        text = LOOPS[name].read_text(encoding="utf-8")
        start = text.index("phase_status() {")
        end = text.index("\n}\n", start) + 3
        block = text[start:end]
        marker = text[text.index('BG_CEILING_MARKER="') :].split('"')[1]

        run_log = tmp_path / "phase.log"
        run_log.write_text("round 1 output\n", encoding="utf-8")
        mark = run_log.stat().st_size
        with run_log.open("a", encoding="utf-8") as fh:
            fh.write(f"{marker} 600s\n")

        runner = tmp_path / "status.sh"
        runner.write_text(
            "set -uo pipefail\n"
            f'BG_CEILING_MARKER="{marker}"\n'
            'CAP_SECS=7200\n'
            f"{block}\n"
            f'phase_status 0 "{run_log}" {mark}\n',
            encoding="utf-8",
        )
        out = subprocess.run([BASH, str(runner)], capture_output=True, text=True, timeout=60)
        assert out.stdout.strip().startswith("TRUNCATED"), (out.stdout, out.stderr)
