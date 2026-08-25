"""A weekend wrapper must survive its own file being rewritten mid-run.

2026-08-23: the reliability remediate fire started at 10:00 on f54ce626 and
died at 19:51 — AFTER the agent had finished and the OK dead-man comment was
already posted — with ``line 135: remain: unbound variable``. Line 135 of the
file that started the run is blank; in the file that was on disk by then
``remain`` is declared by ``local`` at 154 and used at 156. Bash reads a
script lazily by byte offset and re-reads it from disk after every fork, so it
resumed inside different content, never executed the ``local`` line, and
``set -u`` killed it. The run's real exit code (0, PR #85 opened) was replaced
by 1 and the dead-man got a CRASHED comment for a successful run.

The writer that does this is an IN-PLACE truncate+rewrite — the agent's own
edit tool, or a ``>`` redirection — because that keeps the inode bash is
reading. ``git checkout -f`` / ``git reset --hard`` write a NEW inode, so the
running interpreter keeps reading the old one; a red case built on git alone
passes against the unfixed wrapper and proves nothing. The stubs here
therefore rewrite the file in place, hung off two triggers: the ``reset
--hard`` call (the moment the incident report names) and the agent itself
(the real writer across a multi-hour run).

House style follows scripts/tests/test_run_vcg_refresh_wrapper.py: stage the
wrapper into a tmp clone, stub the binaries it shells out to on PATH, run it
with /bin/bash under a launchd-shaped minimal environment, assert on the exit
code and on what the dead-man was told.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

# loop -> (wrapper filename, log dir / dead-man label, plist)
LOOPS = {
    "reliability": (
        "reliability_weekend.sh",
        "reliability-weekend",
        "com.radon.reliability-daily.plist",
    ),
    "testing": (
        "testing_weekend.sh",
        "testing-weekend",
        "com.radon.testing-daily.plist",
    ),
}
LOOP_IDS = sorted(LOOPS)

COMMENT_MARK = "<<<COMMENT>>>"


def _executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _build(
    tmp_path: Path,
    loop: str,
    *,
    attack: str = "none",
    attack_on: str = "git",
    agent_rc: int = 0,
    agent_sleep: int = 0,
    timeout_124: bool = False,
    marker: bool = True,
    extra_env: dict[str, str] | None = None,
) -> dict:
    """Stage a fake runner clone plus the stub binaries the wrapper calls."""
    script, label, _plist = LOOPS[loop]

    clone = tmp_path / "clone"
    (clone / "scripts").mkdir(parents=True)
    (clone / "logs" / label).mkdir(parents=True)
    wrapper = clone / "scripts" / script
    shutil.copy2(REPO / "scripts" / script, wrapper)
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    if marker:
        (clone / ".radon-weekend-runner").touch()
    # notify_phase shells `python3 $REPO/scripts/weekend_notify.py`; python3
    # is stubbed, but the file must exist for the call to look real.
    (clone / "scripts" / "weekend_notify.py").write_text("# stub\n", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh_log = tmp_path / "gh-comments.log"
    push_log = tmp_path / "pushover.log"
    git_log = tmp_path / "git.log"
    agent_log = tmp_path / "agent.log"
    attack_sh = tmp_path / "attack.sh"

    # The rewriter. `> "$f"` truncates in place: same inode, which is the only
    # writer shape that can strand a running interpreter at a stale offset.
    _executable(
        attack_sh,
        "#!/bin/bash\n"
        'f="$STUB_ATTACK_TARGET"\n'
        'case "${STUB_ATTACK_KIND:-none}" in\n'
        "  longer)\n"
        # A longer file: every byte offset past the top now holds different
        # content, exactly like main advancing under a running cycle.
        "    { echo '#!/usr/bin/env bash'\n"
        "      i=0\n"
        "      while [ $i -lt 1200 ]; do echo \"RADON_STALE_OFFSET_TOKEN_$i\"; i=$((i+1)); done\n"
        '    } > "$f" ;;\n'
        "  shorter)\n"
        "    printf '#!/usr/bin/env bash\\necho short\\n' > \"$f\" ;;\n"
        "  zero)\n"
        '    : > "$f" ;;\n'
        "  symlink)\n"
        '    rm -f "$f"; ln -s /dev/null "$f" ;;\n'
        "esac\n"
        "exit 0\n",
    )

    _executable(
        bin_dir / "git",
        "#!/bin/bash\n"
        f'echo "git $*" >> "{git_log}"\n'
        'case " $* " in\n'
        '  *" reset --hard "*)\n'
        '    if [ "${STUB_ATTACK_ON:-off}" = "git" ]; then "%s"; fi ;;\n'
        "esac\n"
        "exit 0\n" % attack_sh,
    )

    _executable(
        bin_dir / "claude",
        "#!/bin/bash\n"
        f'echo "claude $*" >> "{agent_log}"\n'
        'if [ "${STUB_ATTACK_ON:-off}" = "agent" ]; then "%s"; fi\n'
        'if [ "${STUB_CLAUDE_SLEEP:-0}" != "0" ]; then sleep "$STUB_CLAUDE_SLEEP"; fi\n'
        "echo 'stub agent output'\n"
        'exit "${STUB_CLAUDE_RC:-0}"\n' % attack_sh,
    )

    _executable(
        bin_dir / "timeout",
        "#!/bin/bash\n"
        "shift\n"
        'if [ "${STUB_TIMEOUT_124:-0}" = "1" ]; then exit 124; fi\n'
        'exec "$@"\n',
    )

    _executable(
        bin_dir / "gh",
        "#!/bin/bash\n"
        'case "$1 $2" in\n'
        '  "issue list") echo 42 ;;\n'
        '  "pr list") echo "" ;;\n'
        '  "issue comment")\n'
        "    while [ $# -gt 0 ]; do\n"
        '      if [ "$1" = "--body" ]; then shift\n'
        f'        printf \'{COMMENT_MARK}%s\\n\' "$1" >> "{gh_log}"\n'
        "        break\n"
        "      fi\n"
        "      shift\n"
        "    done ;;\n"
        "esac\n"
        "exit 0\n",
    )

    _executable(
        bin_dir / "python3",
        "#!/bin/bash\n" f'echo "notify $*" >> "{push_log}"\n' "exit 0\n",
    )

    env = {
        # launchd hands the job a minimal environment; mirror it.
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "RADON_WEEKEND_REPO": str(clone),
        "RADON_WEEKEND_FETCH_PAUSE_SECS": "0",
        "STUB_ATTACK_ON": attack_on if attack != "none" else "off",
        "STUB_ATTACK_KIND": attack,
        "STUB_ATTACK_TARGET": str(wrapper),
        "STUB_CLAUDE_RC": str(agent_rc),
        "STUB_CLAUDE_SLEEP": str(agent_sleep),
        "STUB_TIMEOUT_124": "1" if timeout_124 else "0",
    }
    env.update(extra_env or {})

    return {
        "clone": clone,
        "wrapper": wrapper,
        "script": script,
        "env": env,
        "gh_log": gh_log,
        "push_log": push_log,
        "agent_log": agent_log,
    }


def _argv(cfg: dict, mode: str, relative: bool = False) -> list[str]:
    target = f"scripts/{cfg['script']}" if relative else str(cfg["wrapper"])
    return ["/bin/bash", target, mode]


def _run(cfg: dict, mode: str, relative: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        _argv(cfg, mode, relative),
        cwd=cfg["clone"],
        env=cfg["env"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _comments(cfg: dict) -> list[str]:
    if not cfg["gh_log"].exists():
        return []
    raw = cfg["gh_log"].read_text(encoding="utf-8")
    return [c for c in raw.split(COMMENT_MARK) if c.strip()]


def _pages(cfg: dict) -> int:
    if not cfg["push_log"].exists():
        return 0
    return len([ln for ln in cfg["push_log"].read_text(encoding="utf-8").splitlines() if ln.strip()])


def _why(result: subprocess.CompletedProcess, cfg: dict) -> str:
    return (
        f"rc={result.returncode}\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}\n"
        f"--- comments ---\n{_comments(cfg)}\n"
    )


# --------------------------------------------------------------------------
# The incident: the file the interpreter is executing is replaced mid-run.
# --------------------------------------------------------------------------
@pytest.mark.parametrize("loop", LOOP_IDS)
@pytest.mark.parametrize("attack_on", ["git", "agent"])
@pytest.mark.parametrize("attack", ["longer", "shorter", "zero", "symlink"])
def test_a_mid_run_rewrite_cannot_change_the_runs_outcome(
    tmp_path: Path, loop: str, attack: str, attack_on: str
) -> None:
    """The wrapper's own file is destroyed while it runs; the run is unmoved."""
    cfg = _build(tmp_path, loop, attack=attack, attack_on=attack_on, agent_rc=7)
    result = _run(cfg, "audit")

    assert result.returncode == 7, _why(result, cfg)
    comments = _comments(cfg)
    assert len(comments) == 1, _why(result, cfg)
    assert "**audit**" in comments[0], comments
    assert "FAILED (exit 7)" in comments[0], comments
    assert "CRASHED" not in comments[0], "a finished agent is not a wrapper crash"
    assert _pages(cfg) == 1, "exactly one Pushover per phase"


@pytest.mark.parametrize("loop", LOOP_IDS)
@pytest.mark.parametrize("agent_rc", [0, 7])
def test_the_agent_exit_code_survives_the_rewrite(
    tmp_path: Path, loop: str, agent_rc: int
) -> None:
    cfg = _build(tmp_path, loop, attack="longer", attack_on="agent", agent_rc=agent_rc)
    result = _run(cfg, "remediate")

    assert result.returncode == agent_rc, _why(result, cfg)
    assert "**remediate**" in _comments(cfg)[-1]


@pytest.mark.parametrize("loop", LOOP_IDS)
def test_a_capped_round_is_still_reported_as_a_timeout(tmp_path: Path, loop: str) -> None:
    cfg = _build(tmp_path, loop, attack="longer", attack_on="git", timeout_124=True)
    result = _run(cfg, "audit")

    assert result.returncode == 124, _why(result, cfg)
    assert "TIMEOUT" in _comments(cfg)[-1], _comments(cfg)


@pytest.mark.parametrize("loop", LOOP_IDS)
def test_a_cycle_still_reports_both_phases(tmp_path: Path, loop: str) -> None:
    """cycle spans a full day of pushes to main and rewrites both wrappers."""
    cfg = _build(tmp_path, loop, attack="longer", attack_on="agent", agent_rc=0)
    result = _run(cfg, "cycle")

    assert result.returncode == 0, _why(result, cfg)
    bodies = "\n".join(_comments(cfg))
    assert "**audit**" in bodies and "**remediate**" in bodies, bodies
    assert "CRASHED" not in bodies, bodies


@pytest.mark.parametrize("loop", LOOP_IDS)
def test_a_stale_exec_copy_guard_variable_is_inert(tmp_path: Path, loop: str) -> None:
    """No env var may switch the protection off (the exec-copy designs had one)."""
    cfg = _build(
        tmp_path,
        loop,
        attack="longer",
        attack_on="agent",
        agent_rc=5,
        extra_env={"RADON_WEEKEND_SELF_COPY": "1", "RADON_WEEKEND_SELF_EXEC": "1"},
    )
    result = _run(cfg, "audit")
    assert result.returncode == 5, _why(result, cfg)


@pytest.mark.parametrize("loop", LOOP_IDS)
def test_the_printed_smoke_test_line_still_works(tmp_path: Path, loop: str) -> None:
    """`bash scripts/x_weekend.sh audit` from the clone root, per setup_*.sh."""
    cfg = _build(tmp_path, loop, attack="longer", attack_on="agent", agent_rc=0)
    result = _run(cfg, "audit", relative=True)
    assert result.returncode == 0, _why(result, cfg)


@pytest.mark.parametrize("loop", LOOP_IDS)
def test_a_clone_without_the_marker_is_refused_silently(tmp_path: Path, loop: str) -> None:
    cfg = _build(tmp_path, loop, marker=False)
    result = _run(cfg, "audit")
    assert result.returncode == 2, _why(result, cfg)
    assert "REFUSING" in result.stderr
    assert _comments(cfg) == []
    assert _pages(cfg) == 0


@pytest.mark.parametrize("loop", LOOP_IDS)
def test_an_unknown_mode_is_refused(tmp_path: Path, loop: str) -> None:
    cfg = _build(tmp_path, loop)
    result = _run(cfg, "bogus")
    assert result.returncode == 2, _why(result, cfg)
    assert "unknown mode" in result.stderr


@pytest.mark.parametrize("loop", LOOP_IDS)
def test_two_instances_in_one_clone_do_not_both_run(tmp_path: Path, loop: str) -> None:
    """R-116: the runner clone is single-writer. The holder must COMPLETE."""
    cfg = _build(tmp_path, loop, attack="longer", attack_on="agent", agent_sleep=4)
    holder = subprocess.Popen(
        _argv(cfg, "audit"),
        cwd=cfg["clone"],
        env=cfg["env"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        lock = cfg["clone"] / ".weekend-runner.lock"
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not lock.exists():
            time.sleep(0.05)
        second = _run(cfg, "audit")
    finally:
        out, err = holder.communicate(timeout=120)

    assert holder.returncode == 0, f"holder rc={holder.returncode}\n{out}\n{err}"
    assert second.returncode == 3, second.stderr
    assert "another weekend run owns" in second.stderr


# --------------------------------------------------------------------------
# Static pins. cycle mode has no dynamic witness (both run_phase calls and
# both exits sit inside one parsed `if` compound), and the guarantee rests on
# structure, so the structure is asserted directly.
# --------------------------------------------------------------------------
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\S*$")
GUARD = '[[ "${1:-}" == "--lock-lib-only" ]] && return 0 2>/dev/null'


def _prologue_top_level(src: str) -> list[str]:
    """Executed, non-comment lines that run BEFORE `main() {` is parsed."""
    out: list[str] = []
    in_func = False
    for line in src.splitlines():
        stripped = line.strip()
        if stripped == "main() {":
            break
        if in_func:
            if stripped == "}":
                in_func = False
            continue
        if not stripped or stripped.startswith("#"):
            continue
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\(\)\s*\{.*\}$", stripped):
            continue  # one-line definition: defines, executes nothing
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\(\)\s*\{$", stripped):
            in_func = True
            continue
        out.append(stripped)
    return out


def _epilogue_top_level(src: str) -> list[str]:
    """Executed, non-comment lines between main's closing `}` and the call.

    A fork here re-opens the same window: the tail of the file is still
    unparsed, so bash discards its buffer and re-reads from disk after the
    fork. main's closer is the LAST `}` at column 0; the nested definitions
    inside the body are not re-indented, so their closers look identical.
    """
    lines = src.splitlines()
    close = max(i for i, ln in enumerate(lines) if ln.rstrip() == "}")
    out: list[str] = []
    for line in lines[close + 1:]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.append(stripped)
    if out and out[-1] == 'main "$@"; exit':
        out.pop()
    return out


@pytest.mark.parametrize("loop", LOOP_IDS)
class TestTheRunBodyIsParsedBeforeItRuns:
    def test_the_whole_run_body_is_one_function(self, loop: str) -> None:
        src = (REPO / "scripts" / LOOPS[loop][0]).read_text(encoding="utf-8")
        assert len(re.findall(r"(?m)^main\(\) \{$", src)) == 1, (
            "the run body must be a single function: bash parses a function "
            "body in full before its first statement runs, and never reads it "
            "from disk again"
        )

    def test_the_call_and_the_exit_share_one_line(self, loop: str) -> None:
        src = (REPO / "scripts" / LOOPS[loop][0]).read_text(encoding="utf-8")
        last = [ln for ln in src.splitlines() if ln.strip()][-1]
        assert last == 'main "$@"; exit', (
            "a bare `main \"$@\"` lets bash read the NEXT input unit from disk "
            "if main ever returns; `main \"$@\"; exit` is one parsed list, so "
            "the last top-level statement always exits from memory"
        )

    def test_nothing_before_main_forks(self, loop: str) -> None:
        # Bash re-reads the script from disk after every fork, so a forking
        # command above `main() {` re-opens the window the wrap closes.
        src = (REPO / "scripts" / LOOPS[loop][0]).read_text(encoding="utf-8")
        for line in _prologue_top_level(src):
            if line in ("set -Eeuo pipefail", GUARD):
                continue
            assert ASSIGNMENT.match(line) or re.match(r'^[A-Za-z_][A-Za-z0-9_]*="[^`]*"$', line), (
                f"unexpected top-level statement before main(): {line!r}"
            )
            assert "$(" not in line and "`" not in line, (
                f"a command substitution before main() forks: {line!r}"
            )

    def test_nothing_between_main_and_the_call_forks(self, loop: str) -> None:
        # Same hazard on the other side of the body, and the one a plausible
        # edit lands on: `STARTED_AT="$(date +%s)"` above the call forks with
        # the tail of the file still unparsed.
        src = (REPO / "scripts" / LOOPS[loop][0]).read_text(encoding="utf-8")
        assert _epilogue_top_level(src) == [], (
            "only `main \"$@\"; exit` may follow main(): a statement here is "
            "read from disk after the body is defined"
        )


class TestTheRunnerLockIsTakenOnce:
    @pytest.mark.parametrize("loop", LOOP_IDS)
    def test_each_loop_takes_the_lock_exactly_once(self, loop: str) -> None:
        src = (REPO / "scripts" / LOOPS[loop][0]).read_text(encoding="utf-8")
        assert src.count('acquire_runner_lock "$RUNNER_LOCK"') == 1, (
            "zero acquires make the plist pre-reset and the setup in-flight "
            "gate dead code, so both hard-reset the tree under a live agent; "
            "a duplicated acquire reads back its own pid, `kill -0 $$` "
            "succeeds, and every invocation exits 3 before any git command "
            "with no dead-man comment and no page"
        )

    @pytest.mark.parametrize("loop", LOOP_IDS)
    def test_no_loop_cleans_away_the_runner_lock(self, loop: str) -> None:
        src = (REPO / "scripts" / LOOPS[loop][0]).read_text(encoding="utf-8")
        for line in src.splitlines():
            if "git clean" in line and not line.strip().startswith("#"):
                assert "--exclude=.weekend-runner.lock" in line, line


class TestTheJobRestoresTheEntryPointBeforeReadingIt:
    """A wrapper left corrupt on disk dies at the top, before the ground_truth
    that would restore it — every later fire repeats it, silently, forever."""

    @pytest.mark.parametrize("loop", LOOP_IDS)
    def test_the_plist_resets_the_clone_before_it_execs_the_wrapper(self, loop: str) -> None:
        import plistlib

        plist = plistlib.loads((REPO / "config" / LOOPS[loop][2]).read_bytes())
        argv = plist["ProgramArguments"]
        assert argv[0] == "/bin/bash" and argv[1] == "-c", argv
        cmd = argv[2]
        assert "reset --hard" in cmd, cmd
        assert cmd.index("reset --hard") < cmd.index("exec /bin/bash"), cmd
        assert f"scripts/{LOOPS[loop][0]}" in cmd, cmd
        assert ".weekend-runner.lock" in cmd, (
            "the pre-reset must skip a clone a live run owns, or the fire "
            "hard-resets the tree under a running agent"
        )

    @pytest.mark.parametrize(
        "setup",
        ["setup_reliability_weekend.sh", "setup_testing_weekend.sh"],
    )
    def test_the_setup_script_states_the_deploy_rule(self, setup: str) -> None:
        src = (REPO / "scripts" / setup).read_text(encoding="utf-8")
        assert "cp / cat / tee" in src and "in flight" in src, (
            "cp / cat / tee write the wrapper IN PLACE, which is the exact "
            "hazard this fix exists to stop; the operator needs the rule"
        )

    @pytest.mark.parametrize(
        "setup",
        ["setup_reliability_weekend.sh", "setup_testing_weekend.sh"],
    )
    def test_the_setup_script_refuses_while_a_run_is_in_flight(self, setup: str) -> None:
        src = (REPO / "scripts" / setup).read_text(encoding="utf-8")
        gate = src.index(".weekend-runner.lock")
        assert gate < src.index("reset --hard"), (
            "setup unloads the job and hard-resets the clone; doing that "
            "under a live cycle orphans the agent onto a reset tree"
        )
