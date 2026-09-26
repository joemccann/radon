"""Adversarial lifecycle tests for the six nightly loops (2026-09-24).

Every case here was reproduced against the wrappers and plists as they stood on
origin/main before this change, then fixed:

- Three runs reclaiming one stale lock at once ended with TWO owners of the
  clone in 7 of 80 trials (judge-then-move with no guard).
- A lock directory whose pid was never published refused every later run
  forever.
- The lock's start fingerprint followed the caller's TZ/locale, so a live
  owner read as dead under a different TZ.
- An agent child that detached into its own session (the testing skill's
  `start_new_session=True` pytest) outlived the round: on 2026-09-22/23 it was
  still writing into the testing clone after the audit ended.
- A wrapper SIGKILLed mid-round left the agent running under `timeout`, and
  the next run took the lock and worked the same clone alongside it.
- The launchd pre-lock step read a REUSED pid as a live owner and exited 0,
  and skipped the night silently on every refresh failure (exit 70/78). On
  2026-09-24 it skipped testing with no comment and no page.
- A git killed mid-write left `*.lock` files in the host gitdir that the
  plist's checkout then failed on, before any wrapper could clear them.
- launchd SIGKILLed the wrapper 5s after SIGTERM, before its KILLED page.
"""

from __future__ import annotations

import os
import plistlib
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import _loop_harness as harness  # noqa: E402
from test_weekend_lock_hygiene import needs_host_ps  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash") or "/bin/bash"
LOOPS = harness.LOOPS
LOOP_PLISTS = {
    "reliability": "com.radon.reliability-daily.plist",
    "testing": "com.radon.testing-daily.plist",
    "ci-performance": "com.radon.ci-performance-daily.plist",
    "documentation": "com.radon.documentation-daily.plist",
    "security": "com.radon.security-daily.plist",
    "security-deepsec": "com.radon.security-deepsec.plist",
}
# The shared lifecycle block. Each wrapper carries its own copy, so the copies
# must not drift: a fix landed in one wrapper is the 2026-09 failure pattern.
SHARED_FUNCTIONS = (
    "pid_alive", "_proc_lstart", "_proc_lstart_local", "_write_runner_lock_files",
    "_stale_lock_dest", "_read_runner_lock_identity", "_path_older_than_mins", "_guard_abandoned",
    "acquire_runner_lock", "_moved_lock_was_judged", "_acquire_runner_lock_guarded",
    "release_runner_lock", "_cwd_listing", "_pid_cwd_in", "_proc_age_secs", "_round_scan", "_ingest_declarations", "_inode",
    "_round_state_dir", "_prepare_round", "_record_round", "_reap_pids", "_reap_group",
    "_round_leftover_pids", "_stop_round_sampler", "_clear_round_record", "_reap_dead_owner",
    "clear_stale_git_locks", "kill_round_group", "sweep_shared_parent_lock",
)
DEAD_START = "Thu Jan  1 00:00:00 1970"
real_timeout = shutil.which("timeout") or shutil.which("gtimeout")


def _fn(text: str, name: str) -> str:
    start = text.index(f"\n{name}() {{\n") + 1
    return text[start:text.index("\n}\n", start) + 3]


def _lib(loop: str, snippet: str, tmp_path: Path, env: dict | None = None, timeout: int = 120):
    runner = tmp_path / f"lib-{os.getpid()}-{time.monotonic_ns()}.sh"
    runner.write_text(
        f"set -Eeuo pipefail\nsource {LOOPS[loop]} --lock-lib-only\n" + snippet, encoding="utf-8"
    )
    full_env = dict(os.environ)
    full_env.update(env or {})
    return subprocess.run([BASH, str(runner)], capture_output=True, text=True, env=full_env, timeout=timeout)


def _detached(cwd: Path, seconds: int = 60) -> subprocess.Popen:
    """A process the way an escaped agent child looks: own session, no tty."""
    return subprocess.Popen(
        ["/bin/sleep", str(seconds)], cwd=cwd, start_new_session=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


_ORPHAN = """
import os, sys
os.setsid()
pid = os.fork()
if pid:
    print(pid, flush=True)
    os._exit(0)
fd = os.open(os.devnull, os.O_RDWR)
for n in (0, 1, 2):
    os.dup2(fd, n)
os.execv(sys.argv[1], sys.argv[1:])
"""


def _orphan(cwd: Path, *argv: str) -> int:
    """An escaped agent child: own session, no tty, parent gone (ppid 1)."""
    argv = argv or ("/bin/sleep", "60")
    out = subprocess.run([sys.executable, "-c", _ORPHAN, *argv], cwd=cwd, capture_output=True, text=True, timeout=30)
    pid = int(out.stdout.strip())
    for _ in range(50):
        ppid = subprocess.run(["/bin/ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
        if ppid == "1":
            break
        time.sleep(0.1)
    return pid


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    zombie = subprocess.run(["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout
    return bool(zombie.strip()) and not zombie.strip().startswith("Z")


def _wait_gone(pid: int, within: float = 20.0) -> bool:
    deadline = time.time() + within
    while time.time() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.2)
    return False


def _kill(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _gone(proc: subprocess.Popen, within: float = 20.0) -> bool:
    try:
        proc.wait(timeout=within)
        return True
    except subprocess.TimeoutExpired:
        return False


def _lstart(pid: int, **env) -> str:
    e = dict(os.environ)
    e.update(env)
    out = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "lstart="], capture_output=True, text=True, env=e)
    return out.stdout.strip()


# --- the copies do not drift ------------------------------------------------

@pytest.mark.parametrize("name", SHARED_FUNCTIONS)
def test_lifecycle_helpers_are_identical_in_all_six_wrappers(name):
    bodies = {loop: _fn(path.read_text(), name) for loop, path in LOOPS.items()}
    assert len(set(bodies.values())) == 1, f"{name} differs between wrappers: {sorted(bodies)}"


@pytest.mark.parametrize("loop", sorted(LOOPS))
def test_every_wrapper_clears_git_locks_records_rounds_and_reaps(loop):
    text = LOOPS[loop].read_text()
    ground = _fn(text, "ground_truth")
    assert ground.splitlines()[1].strip() == 'clear_stale_git_locks "$HOST_GITDIR"'
    assert '    _prepare_round\n    launch_round "$remain"\n    _record_round\n' in text
    on_signal = _fn(text, "on_signal")
    # The round must be dead before the lock is released, or the next run
    # takes the clone while this one's agent is still in it.
    assert on_signal.index("kill_round_group") < on_signal.index("release_runner_lock")
    # ...and fast enough that launchd's ExitTimeOut still leaves time to page.
    assert on_signal.index("REAP_GRACE_SECS=3") < on_signal.index("kill_round_group")


# --- the lock ---------------------------------------------------------------

@needs_host_ps
def test_concurrent_reclaim_of_a_stale_lock_never_yields_two_owners(tmp_path):
    acquire = tmp_path / "acquire.sh"
    acquire.write_text(
        f'source {LOOPS["reliability"]} --lock-lib-only\n'
        'WEEKEND_ROOT="$1"; REPO="$1"\n'
        'if acquire_runner_lock "$1/lock.d" 2>/dev/null; then echo "WIN $$" >> "$1/wins"; sleep 1; fi\n',
        encoding="utf-8",
    )
    doubled = []
    for trial in range(30):
        d = tmp_path / f"t{trial}"
        (d / "lock.d").mkdir(parents=True)
        (d / "lock.d" / "pid").write_text("99999\n")
        (d / "lock.d" / "start").write_text(DEAD_START + "\n")
        racers = [subprocess.Popen([BASH, str(acquire), str(d)]) for _ in range(3)]
        for racer in racers:
            racer.wait(timeout=120)
        wins = (d / "wins").read_text().splitlines() if (d / "wins").exists() else []
        assert wins, f"trial {trial}: nobody took a stale lock"
        if len(wins) > 1:
            doubled.append(trial)
    assert not doubled, f"two owners in trials {doubled}"


@pytest.mark.parametrize("loop", sorted(LOOPS))
def test_an_abandoned_unpublished_lock_is_reclaimed_after_the_grace(loop, tmp_path):
    lock = tmp_path / "lock.d"
    lock.mkdir()
    old = time.time() - 3600
    os.utime(lock, (old, old))
    out = _lib(loop, f'WEEKEND_ROOT="{tmp_path}"; acquire_runner_lock "{lock}"\ncat "{lock}/pid"\n', tmp_path)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().isdigit()
    assert "reclaiming abandoned runner lock" in out.stderr
    assert list((tmp_path / ".stale-locks").glob("*.nopid.*"))


@pytest.mark.parametrize("loop", sorted(LOOPS))
def test_a_fresh_unpublished_lock_is_still_in_flight(loop, tmp_path):
    lock = tmp_path / "lock.d"
    lock.mkdir()
    out = _lib(loop, f'acquire_runner_lock "{lock}"\n', tmp_path)
    assert out.returncode != 0
    assert "pid not yet published" in out.stderr


@pytest.mark.parametrize("loop", sorted(LOOPS))
def test_an_abandoned_reclaim_guard_does_not_wedge_the_clone(loop, tmp_path):
    lock = tmp_path / "lock.d"
    guard = tmp_path / "lock.d.reclaim"
    lock.mkdir()
    (lock / "pid").write_text("99999\n")
    (lock / "start").write_text(DEAD_START + "\n")
    guard.mkdir()
    (guard / "pid").write_text("99998\n")  # its reclaimer died
    (guard / "start").write_text(DEAD_START + "\n")
    out = _lib(loop, f'WEEKEND_ROOT="{tmp_path}"; acquire_runner_lock "{lock}"\n', tmp_path)
    assert out.returncode == 0, out.stderr
    assert not guard.exists()


@needs_host_ps
def test_a_slow_live_reclaimers_guard_is_never_broken(tmp_path):
    """Breaking the guard by age let a second run move the first run's fresh
    lock aside and both own the clone (a 3-minute-old guard, reproduced)."""
    owner = _detached(Path("/"))
    try:
        lock = tmp_path / "lock.d"
        guard = tmp_path / "lock.d.reclaim"
        lock.mkdir()
        (lock / "pid").write_text("99999\n")
        (lock / "start").write_text(DEAD_START + "\n")
        guard.mkdir()
        (guard / "pid").write_text(f"{owner.pid}\n")
        (guard / "start").write_text(_fingerprint(owner.pid) + "\n")
        old = time.time() - 600
        os.utime(guard, (old, old))
        out = _lib("reliability", f'WEEKEND_ROOT="{tmp_path}"; acquire_runner_lock "{lock}"\n', tmp_path)
        assert out.returncode != 0
        assert "being reclaimed by another run" in out.stderr
        assert guard.exists()
    finally:
        owner.kill()
        owner.wait()


@pytest.mark.parametrize("loop", sorted(LOOPS))
def test_a_find_that_loses_a_race_never_fires_the_crash_trap(loop, tmp_path):
    """Under `set -E` the ERR trap also runs inside $(...): a find failing on a
    concurrently pruned gitdir fired on_crash (a false CRASHED page)."""
    g = tmp_path / "host.git"
    (g / "refs" / "locked").mkdir(parents=True)
    (g / "refs" / "locked").chmod(0)
    lock = tmp_path / "vanished.d"
    try:
        out = _lib(
            loop,
            "trap 'echo ERR-TRAP-FIRED >&2' ERR\n"
            f'clear_stale_git_locks "{g}"\n'
            f'_path_older_than_mins "{lock}" 1 || echo not-old\n',
            tmp_path,
        )
    finally:
        (g / "refs" / "locked").chmod(0o755)
    assert out.returncode == 0, out.stderr
    assert "ERR-TRAP-FIRED" not in out.stderr
    assert "not-old" in out.stdout


@needs_host_ps
@pytest.mark.parametrize("tz", ["UTC", "Asia/Tokyo", "America/Los_Angeles"])
def test_a_live_owner_stays_live_whatever_tz_the_checker_runs_under(tz, tmp_path):
    owner = _detached(tmp_path / ".." if False else Path("/"), 60)
    try:
        lock = tmp_path / "lock.d"
        lock.mkdir()
        # Written by the owner's own environment (launchd: system TZ)...
        write = _lib(
            "security",
            f'_proc_lstart {owner.pid} > "{lock}/start"; echo {owner.pid} > "{lock}/pid"\n',
            tmp_path, env={"TZ": "America/New_York"},
        )
        assert write.returncode == 0, write.stderr
        # ...checked from a shell with another TZ and locale.
        out = _lib("security", f'acquire_runner_lock "{lock}"\n', tmp_path, env={"TZ": tz, "LC_ALL": "C"})
        assert out.returncode != 0, "a live owner was reclaimed"
        assert f"held by pid {owner.pid}" in out.stderr
    finally:
        owner.kill()
        owner.wait()


@needs_host_ps
def test_a_legacy_local_format_fingerprint_still_matches(tmp_path):
    owner = _detached(Path("/"), 60)
    try:
        lock = tmp_path / "lock.d"
        lock.mkdir()
        (lock / "pid").write_text(f"{owner.pid}\n")
        (lock / "start").write_text(_lstart(owner.pid) + "\n")
        out = _lib("reliability", f'acquire_runner_lock "{lock}"\n', tmp_path)
        assert out.returncode != 0
        assert "held by pid" in out.stderr
    finally:
        owner.kill()
        owner.wait()


# --- reaping: only what is positively the round's ------------------------------
# On the runner `~/radon-weekend/radon` doubles as the Remote Control
# workspace: radon-rc.sh agents, Claude Code daemons, interactive sessions and
# whatever they background sit in it with no terminal, and they are often
# orphaned (ppid 1). None of them may ever be reaped, so round membership is
# never inferred from what a process looks like.

def _state(weekend: Path, clone: Path) -> Path:
    d = weekend / ".runner-state" / clone.name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _fingerprint(pid: int) -> str:
    return _lstart(pid, LC_ALL="C", TZ="UTC")


def _dead_lock(clone: Path) -> Path:
    lock = clone / ".weekend-runner.lock"
    lock.mkdir()
    (lock / "pid").write_text("99999\n")
    (lock / "start").write_text(DEAD_START + "\n")
    return lock


def _rc_style_background(clone: Path, marker: Path) -> int:
    """What a Remote Control session's `cmd &` looks like once its tool shell
    exits: orphaned, no tty, working in the clone, started 'during the round',
    in a group whose leader is dead."""
    subprocess.run(
        [sys.executable, "-c", _ORPHAN, "/bin/sh", "-c", f'/bin/sleep 60 & echo $! > "{marker}"; exit 0'],
        cwd=clone, capture_output=True, timeout=30,
    )
    for _ in range(50):
        if marker.exists() and marker.read_text().strip():
            break
        time.sleep(0.1)
    return int(marker.read_text())


@needs_host_ps
@pytest.mark.parametrize("loop", sorted(LOOPS))
def test_reclaim_reaps_the_dead_owners_recorded_agents_and_nothing_else(loop, tmp_path):
    weekend = tmp_path
    clone = weekend / "clone"
    (clone / "sub").mkdir(parents=True)
    lock = _dead_lock(clone)
    since = int(time.time()) - 5
    sampled = _orphan(clone / "sub")                      # recorded by the dead run's sampler
    stale_decl = _orphan(clone / "sub")                   # only in the dead run's pidfile
    rc_bg = _rc_style_background(clone, tmp_path / "rc")  # an RC session's `cmd &`
    state = _state(weekend, clone)
    (state / "round").write_text(f"99998\n{DEAD_START}\n{since}\n")
    (state / "observed").write_text(f"{sampled}\t{_fingerprint(sampled)}\n")
    (clone / ".weekend-detached-pids").write_text(f"{stale_decl}\n")
    try:
        out = _lib(loop, f'WEEKEND_ROOT="{weekend}"; acquire_runner_lock "{lock}"\n', tmp_path)
        assert out.returncode == 0, out.stderr
        assert _wait_gone(sampled), "a recorded process of the dead round survived"
        assert _alive(stale_decl), "a reclaim trusted a stale, unrecorded pidfile entry"
        assert _alive(rc_bg), "an untracked Remote Control background process was reaped"
        assert not (state / "round").exists()
    finally:
        for pid in (sampled, stale_decl, rc_bg):
            _kill(pid)


@needs_host_ps
def test_an_untracked_orphan_in_the_clone_is_never_reaped(tmp_path):
    """The reviewer's break of the first design: an RC session's background
    job, orphaned mid-round in a group with a dead leader, plus a sibling of
    that group outside the clone."""
    weekend = tmp_path
    clone = weekend / "clone"
    clone.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    rc_bg = _rc_style_background(clone, tmp_path / "m1")
    elsewhere = _rc_style_background(outside, tmp_path / "m2")
    try:
        out = _lib(
            "reliability",
            f'WEEKEND_ROOT="{weekend}"; REPO="{clone}"; since=$(( $(date +%s) - 60 ))\n'
            # kill_round_group's leftover step, as the lock library exposes it.
            '_reap_pids "$(_round_leftover_pids "$REPO" "$(_round_state_dir "$REPO")" "$since")"\n',
            tmp_path,
        )
        assert out.returncode == 0, out.stderr
        time.sleep(1)
        assert _alive(rc_bg) and _alive(elsewhere), "an untracked process was reaped"
    finally:
        _kill(rc_bg)
        _kill(elsewhere)


@needs_host_ps
@pytest.mark.parametrize(
    "shape", ["remote-control", "under-remote-control", "outside-clone", "older-than-round", "has-tty-parentless"]
)
def test_a_declared_pid_is_reaped_only_when_it_is_really_the_rounds(shape, tmp_path):
    """The declaration file lives in the agent-writable clone, so a forged or
    stale entry must not reach anything that is not plausibly the round's."""
    weekend = tmp_path
    clone = weekend / "clone"
    clone.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    since = int(time.time()) - 5
    if shape == "remote-control":
        rc = tmp_path / "radon-rc.sh"
        rc.write_text("#!/bin/sh\n/bin/sleep 60\n")  # like radon-rc.sh: the shell stays
        rc.chmod(0o755)
        pid = _orphan(clone, "/bin/sh", str(rc))
    elif shape == "under-remote-control":
        # A tool a Remote Control session started: its own argv is clean, its parent's is not.
        kids = tmp_path / "kids"
        rc = tmp_path / "radon-rc.sh"
        rc.write_text(f'#!/bin/sh\n/bin/sleep 60 &\necho $! > "{kids}"\nwait\n')
        rc.chmod(0o755)
        _orphan(clone, "/bin/sh", str(rc))
        for _ in range(50):
            if kids.exists() and kids.read_text().strip():
                break
            time.sleep(0.1)
        pid = int(kids.read_text())
    elif shape == "outside-clone":
        pid = _orphan(outside)
    elif shape == "older-than-round":
        pid = _orphan(clone)
        time.sleep(3)
        since = int(time.time())
    else:
        if not sys.stdin.isatty():
            pytest.skip("needs a controlling terminal to model an operator process")
        pid = subprocess.Popen(["/bin/sleep", "60"], cwd=clone).pid
    pidfile = clone / ".weekend-detached-pids"
    pidfile.write_text(f"{pid}\n")
    try:
        out = _lib(
            "security",
            f'RADON_WEEKEND_DETACHED_PIDFILE="{pidfile}"\n'
            f'_round_leftover_pids "{clone}" "{_state(weekend, clone)}" "{since}"\n',
            tmp_path,
        )
        assert out.returncode == 0, out.stderr
        assert str(pid) not in out.stdout.split(), f"{shape}: a declared pid that is not the round's was selected"
    finally:
        _kill(pid)
        subprocess.run(["/usr/bin/pkill", "-KILL", "-f", str(tmp_path / "radon-rc.sh")], check=False)


@needs_host_ps
def test_a_prompt_declaration_is_reaped_with_its_children(tmp_path):
    weekend = tmp_path
    clone = weekend / "clone"
    clone.mkdir()
    since = int(time.time()) - 5
    kids = tmp_path / "kids"
    job = _orphan(clone, "/bin/sh", "-c", f'/bin/sleep 60 & echo $! > "{kids}"; wait')
    for _ in range(50):
        if kids.exists() and kids.read_text().strip():
            break
        time.sleep(0.1)
    worker = int(kids.read_text())
    pidfile = clone / ".weekend-detached-pids"
    pidfile.write_text(f"{job}\n")
    state = _state(weekend, clone)
    try:
        out = _lib(
            "testing",
            f'RADON_WEEKEND_DETACHED_PIDFILE="{pidfile}"\n'
            f'_reap_pids "$(_round_leftover_pids "{clone}" "{state}" "{since}")"\n',
            tmp_path,
        )
        assert out.returncode == 0, out.stderr
        assert _wait_gone(job) and _wait_gone(worker)
    finally:
        _kill(job)
        _kill(worker)


@needs_host_ps
def test_a_late_or_sloppy_declaration_is_ignored(tmp_path):
    """`pgrep -f pytest >> $RADON_WEEKEND_DETACHED_PIDFILE` names other
    sessions' long-running processes: only a job declared within the window
    after it started counts."""
    weekend = tmp_path
    clone = weekend / "clone"
    clone.mkdir()
    since = int(time.time()) - 5
    old_job = _orphan(clone)
    time.sleep(3)
    pidfile = clone / ".weekend-detached-pids"
    pidfile.write_text(f"{old_job}\n")
    try:
        out = _lib(
            "security",
            f'RADON_WEEKEND_DECLARE_WINDOW_SECS=2; RADON_WEEKEND_DETACHED_PIDFILE="{pidfile}"\n'
            f'_round_leftover_pids "{clone}" "{_state(weekend, clone)}" "{since}"\n',
            tmp_path,
        )
        assert out.returncode == 0, out.stderr
        assert str(old_job) not in out.stdout.split()
    finally:
        _kill(old_job)


@needs_host_ps
def test_a_pidfile_line_never_joins_two_numbers(tmp_path):
    weekend = tmp_path
    clone = weekend / "clone"
    clone.mkdir()
    since = int(time.time()) - 5
    a = _orphan(clone)
    pidfile = clone / ".weekend-detached-pids"
    text = str(a)
    pidfile.write_text(f"{text[:-1]} {text[-1]}\n")  # "9247 6" must not become 92476
    try:
        out = _lib(
            "reliability",
            f'RADON_WEEKEND_DETACHED_PIDFILE="{pidfile}"\n'
            f'_round_leftover_pids "{clone}" "{_state(weekend, clone)}" "{since}"\n',
            tmp_path,
        )
        assert str(a) not in out.stdout.split()
    finally:
        _kill(a)


@needs_host_ps
def test_descendant_expansion_never_crosses_into_a_remote_control_process(tmp_path):
    weekend = tmp_path
    clone = weekend / "clone"
    clone.mkdir()
    rc = tmp_path / "radon-rc.sh"
    rc.write_text("#!/bin/sh\n/bin/sleep 60\n")
    rc.chmod(0o755)
    kids = tmp_path / "kids"
    launcher = tmp_path / "launcher.sh"  # the job's own argv must not name radon-rc.sh
    launcher.write_text(f'#!/bin/sh\n"{rc}" &\necho $! > "{kids}"\nwait\n')
    job = _orphan(clone, "/bin/sh", str(launcher))
    for _ in range(50):
        if kids.exists() and kids.read_text().strip():
            break
        time.sleep(0.1)
    rc_child = int(kids.read_text())
    state = _state(weekend, clone)
    (state / "observed").write_text(f"{job}\t{_fingerprint(job)}\n")
    try:
        out = _lib("security", f'_round_scan "" "{state / "observed"}"\n', tmp_path)
        picked = [line.split("\t")[0] for line in out.stdout.splitlines()]
        assert str(job) in picked
        assert str(rc_child) not in picked, "a Remote Control process was swept in as a descendant"
    finally:
        _kill(job)
        subprocess.run(["/usr/bin/pkill", "-KILL", "-f", str(rc)], check=False)


@needs_host_ps
def test_the_recorded_set_stays_bounded_by_what_is_alive(tmp_path):
    """`observed` grew without bound and the reap forked one ps per line."""
    live = _orphan(tmp_path)
    rec = tmp_path / "observed"
    lines = [f"{90000 + i}\t{DEAD_START}" for i in range(5000)]
    lines.append(f"{live}\t{_fingerprint(live)}")
    rec.write_text("\n".join(lines) + "\n")
    try:
        started = time.monotonic()
        out = _lib("reliability", f'_round_scan "" "{rec}"\n', tmp_path)
        elapsed = time.monotonic() - started
        assert out.returncode == 0, out.stderr
        assert [line.split("\t")[0] for line in out.stdout.splitlines()] == [str(live)]
        assert elapsed < 5, f"a 5,000-line record took {elapsed:.1f}s to scan"
    finally:
        _kill(live)


@needs_host_ps
def test_a_reaped_processs_children_go_with_it(tmp_path):
    weekend = tmp_path
    clone = weekend / "clone"
    clone.mkdir()
    kids = tmp_path / "kids"
    leader = _orphan(clone, "/bin/sh", "-c", f'/bin/sleep 60 & echo $! > "{kids}"; wait')
    for _ in range(50):
        if kids.exists() and kids.read_text().strip():
            break
        time.sleep(0.1)
    worker = int(kids.read_text())
    state = _state(weekend, clone)
    (state / "observed").write_text(f"{leader}\t{_fingerprint(leader)}\n")  # the worker came later
    try:
        out = _lib(
            "testing",
            f'WEEKEND_ROOT="{weekend}"; REPO="{clone}"; since=$(( $(date +%s) - 60 ))\n'
            # kill_round_group's leftover step, as the lock library exposes it.
            '_reap_pids "$(_round_leftover_pids "$REPO" "$(_round_state_dir "$REPO")" "$since")"\n',
            tmp_path,
        )
        assert out.returncode == 0, out.stderr
        assert _wait_gone(leader)
        assert _wait_gone(worker), "the reaped process's worker survived it"
    finally:
        _kill(leader)
        _kill(worker)


@needs_host_ps
def test_a_recycled_pid_in_the_sampled_set_is_not_reaped(tmp_path):
    weekend = tmp_path
    clone = weekend / "clone"
    clone.mkdir()
    stranger = _orphan(clone)
    state = _state(weekend, clone)
    # Sampled under this pid, but with another process's start time.
    (state / "observed").write_text(f"{stranger}\t{DEAD_START}\n")
    try:
        out = _lib("reliability", f'_round_leftover_pids "{clone}" "{state}" 0\n', tmp_path)
        assert str(stranger) not in out.stdout.split()
    finally:
        _kill(stranger)


@needs_host_ps
def test_reclaim_kills_the_recorded_round_group_even_outside_the_clone_tree(tmp_path):
    weekend = tmp_path
    clone = weekend / "clone"
    clone.mkdir()
    # Leader in the clone (as `timeout` is); a member that chdir'd away.
    leader = subprocess.Popen(
        ["/bin/sh", "-c", '(cd / && exec /bin/sleep 60) & echo $! > "$M"; wait'],
        cwd=clone, start_new_session=True, env={**os.environ, "M": str(tmp_path / "member")},
    )
    try:
        for _ in range(50):
            if (tmp_path / "member").exists() and (tmp_path / "member").read_text().strip():
                break
            time.sleep(0.1)
        member = int((tmp_path / "member").read_text())
        lock = _dead_lock(clone)
        (_state(weekend, clone) / "round").write_text(f"{leader.pid}\n{_fingerprint(leader.pid)}\n{int(time.time())}\n")
        out = _lib("testing", f'WEEKEND_ROOT="{weekend}"; acquire_runner_lock "{lock}"\n', tmp_path)
        assert out.returncode == 0, out.stderr
        assert "reaping round group" in out.stderr
        assert _gone(leader)
        assert _wait_gone(member), "a round-group member outside the clone survived"
    finally:
        if leader.poll() is None:
            os.killpg(leader.pid, signal.SIGKILL)


@needs_host_ps
def test_a_round_record_naming_a_process_outside_the_clone_is_ignored(tmp_path):
    weekend = tmp_path
    clone = weekend / "clone"
    clone.mkdir()
    victim = _detached(tmp_path)  # outside the clone
    try:
        lock = _dead_lock(clone)
        (_state(weekend, clone) / "round").write_text(f"{victim.pid}\n{_fingerprint(victim.pid)}\n0\n")
        out = _lib("security", f'WEEKEND_ROOT="{weekend}"; acquire_runner_lock "{lock}"\n', tmp_path)
        assert out.returncode == 0, out.stderr
        time.sleep(1)
        assert victim.poll() is None, "a round record aimed the reaper at a process outside the clone"
    finally:
        victim.kill()
        victim.wait()


@needs_host_ps
def test_a_record_planted_inside_the_clone_is_ignored(tmp_path):
    """The agent can write anywhere in the clone; the records live outside."""
    weekend = tmp_path
    clone = weekend / "clone"
    clone.mkdir()
    bystander = _orphan(clone)
    try:
        lock = _dead_lock(clone)
        (lock / "round").write_text(f"{bystander}\n{_fingerprint(bystander)}\n0\n")
        (lock / "observed").write_text(f"{bystander}\t{_fingerprint(bystander)}\n")
        out = _lib("security", f'WEEKEND_ROOT="{weekend}"; acquire_runner_lock "{lock}"\n', tmp_path)
        assert out.returncode == 0, out.stderr
        time.sleep(1)
        assert _alive(bystander)
    finally:
        _kill(bystander)


# --- whole-wrapper runs -------------------------------------------------------

def _wrapper_env(tmp_path: Path, loop: str, agent_body: str):
    models_log, gh_log, exhausted = tmp_path / "m", tmp_path / "gh", tmp_path / "ex"
    exhausted.write_text("")
    bin_dir = harness._stub_bin(tmp_path, models_log, exhausted, gh_log)
    (bin_dir / "timeout").unlink()
    (bin_dir / "timeout").symlink_to(real_timeout)
    for name in ("claude", "codex", "grok"):
        (bin_dir / name).write_text(agent_body)
        (bin_dir / name).chmod(0o755)
    repo = harness._clone(tmp_path, LOOPS[loop])
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path / "home"), "RADON_WEEKEND_REPO": str(repo)}
    return repo, env


_DETACH = """/usr/bin/python3 -c 'import os,sys,time
os.setsid()
pid = os.fork()
if pid:
    {declare}
    time.sleep({linger})
    sys.exit(0)
open("{pids}","a").write("%d\\n" % os.getpid())
time.sleep(6)
open("{late}","a").write("wrote into the clone after the round\\n")' &
"""


@needs_host_ps
@pytest.mark.skipif(not real_timeout, reason="needs GNU timeout for real process-group semantics")
@pytest.mark.parametrize("loop", ["security", "security-deepsec"])
@pytest.mark.parametrize("shape", ["declared-fast-detach", "sampled-slow-detach"])
def test_a_session_detached_agent_child_does_not_outlive_its_round(loop, shape, tmp_path):
    late = tmp_path / "late"
    pids = tmp_path / "pids"
    if shape == "declared-fast-detach":
        # The launcher exits at once (the testing skill's Popen shape), too fast
        # for any sampler; the skill has the agent declare the pid.
        declare = 'open(os.environ["RADON_WEEKEND_DETACHED_PIDFILE"], "a").write("%d\\n" % pid)'
        linger = 0
    else:
        # Undeclared, but its launcher lives long enough to be sampled.
        declare = "pass"
        linger = 3
    agent = (
        '#!/bin/bash\nif [ "$1" = "models" ]; then exit 1; fi\n'
        + _DETACH.format(declare=declare, linger=linger, pids=pids, late=late)
        + f'sleep {linger + 2}\necho "{harness.COMPLETION}"\necho "{harness.COMPLETION_DEEPSEC}"\nexit 0\n'
    )
    repo, env = _wrapper_env(tmp_path, loop, agent)
    proc = subprocess.run(
        [BASH, str(repo / "scripts" / LOOPS[loop].name), "audit"],
        cwd=repo, env=env, capture_output=True, text=True, timeout=300,
    )
    time.sleep(8)
    try:
        assert pids.exists(), proc.stderr[-2000:]
        assert not late.exists(), f"{shape}: a detached agent child kept writing after the round ended"
    finally:
        for line in pids.read_text().split() if pids.exists() else []:
            _kill(int(line))


@needs_host_ps
@pytest.mark.skipif(not real_timeout, reason="needs GNU timeout for real process-group semantics")
def test_the_next_run_after_a_sigkilled_wrapper_stops_the_orphaned_agent(tmp_path):
    loop = "security"
    beat = tmp_path / "beat"
    agent_pid = tmp_path / "agentpid"
    agent = f"""#!/bin/bash
if [ "$1" = "models" ]; then exit 1; fi
echo $$ > "{agent_pid}"
for i in $(seq 1 60); do date +%s >> "{beat}"; sleep 1; done
"""
    repo, env = _wrapper_env(tmp_path, loop, agent)
    wrapper = subprocess.Popen(
        [BASH, str(repo / "scripts" / LOOPS[loop].name), "audit"], cwd=repo, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    apid = None
    try:
        for _ in range(120):
            if agent_pid.exists() and agent_pid.read_text().strip():
                break
            time.sleep(0.25)
        apid = int(agent_pid.read_text())
        time.sleep(1)
        os.kill(wrapper.pid, signal.SIGKILL)
        wrapper.wait()
        os.kill(apid, 0)  # the agent outlives its wrapper; that is the hazard
        state = tmp_path / ".runner-state" / repo.name
        assert (state / "round").exists(), "the live round was never recorded outside the clone"
        lock = repo / ".weekend-runner.lock"
        out = _lib(loop, f'WEEKEND_ROOT="{tmp_path}"; REPO="{repo}"; acquire_runner_lock "{lock}"\n', tmp_path, env=env)
        assert out.returncode == 0, out.stderr
        assert _wait_gone(apid), "the next run took the clone while the orphaned agent still ran"
    finally:
        if apid:
            try:
                os.killpg(os.getpgid(apid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


# --- git lock files -----------------------------------------------------------

@pytest.mark.parametrize("loop", sorted(LOOPS))
def test_abandoned_git_locks_are_cleared_and_live_shared_ones_kept(loop, tmp_path):
    g = tmp_path / "host.git"
    (g / "refs" / "heads").mkdir(parents=True)
    (g / "worktrees" / "wt").mkdir(parents=True)
    (g / "objects").mkdir()
    old = time.time() - 3600
    files = {
        "index": g / "index.lock",
        "head": g / "HEAD.lock",
        "old_ref": g / "refs" / "heads" / "main.lock",
        "old_packed": g / "packed-refs.lock",
        "new_ref": g / "refs" / "heads" / "busy.lock",
        "worktree": g / "worktrees" / "wt" / "index.lock",
    }
    for f in files.values():
        f.write_text("")
    for key in ("old_ref", "old_packed", "worktree"):
        os.utime(files[key], (old, old))
    out = _lib(loop, f'clear_stale_git_locks "{g}"\n', tmp_path)
    assert out.returncode == 0, out.stderr
    for key in ("index", "head", "old_ref", "old_packed"):
        assert not files[key].exists(), key
    # A shared ref lock minutes old may be a live interactive git's.
    assert files["new_ref"].exists()
    # Interactive worktrees own their own index locks.
    assert files["worktree"].exists()


# --- the launchd pre-lock program ---------------------------------------------

def _plist_program(loop: str, clone: Path) -> str:
    plist = plistlib.loads((REPO / "config" / LOOP_PLISTS[loop]).read_bytes())
    return re.sub(r"__[A-Z]+_REPO__", str(clone), plist["ProgramArguments"][2])


def _launch_fixture(tmp_path: Path, loop: str, fail_step: str = ""):
    root = tmp_path / "weekend"
    clone = root / "clone"
    (clone / "scripts").mkdir(parents=True)
    gitdir_name = re.search(r'\.gitdirs/([^"]+)"', _plist_program(loop, clone)).group(1)
    g = root / ".gitdirs" / gitdir_name
    (g / "refs" / "heads").mkdir(parents=True)
    wrapper = LOOPS[loop].name
    canary = tmp_path / "wrapper-ran"
    (clone / "scripts" / wrapper).write_text(f'touch "{canary}"\n', encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "git-calls"
    (bin_dir / "git").write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> "{calls}"\n'
        f'for a do [ "$a" = "{fail_step or "__none__"}" ] && exit 1; done\nexit 0\n'
    )
    (bin_dir / "git").chmod(0o755)
    (bin_dir / "timeout").write_text('#!/bin/sh\nshift 3\nexec "$@"\n')
    (bin_dir / "timeout").chmod(0o755)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "RADON_LAUNCHD_FETCH_PAUSE_SECS": "0"}
    return clone, g, canary, calls, env


@pytest.mark.parametrize("loop", sorted(LOOP_PLISTS))
def test_launchd_gives_the_wrapper_time_to_reap_and_page(loop):
    plist = plistlib.loads((REPO / "config" / LOOP_PLISTS[loop]).read_bytes())
    assert plist.get("ExitTimeOut", 0) >= 60


@needs_host_ps
@pytest.mark.parametrize("loop", sorted(LOOP_PLISTS))
def test_launchd_does_not_skip_the_night_for_a_reused_pid(loop, tmp_path):
    clone, g, canary, calls, env = _launch_fixture(tmp_path, loop)
    stranger = _detached(Path("/"))
    try:
        lock = clone / ".weekend-runner.lock"
        lock.mkdir()
        (lock / "pid").write_text(f"{stranger.pid}\n")
        (lock / "start").write_text(DEAD_START + "\n")  # someone else's start time
        proc = subprocess.run([BASH, "-c", _plist_program(loop, clone)], env=env, capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
        assert canary.exists(), "a reused pid skipped the night"
    finally:
        stranger.kill()
        stranger.wait()


@needs_host_ps
@pytest.mark.parametrize("loop", sorted(LOOP_PLISTS))
def test_launchd_stands_down_loudly_for_a_live_owner(loop, tmp_path):
    clone, g, canary, calls, env = _launch_fixture(tmp_path, loop)
    owner = _detached(Path("/"))
    try:
        lock = clone / ".weekend-runner.lock"
        lock.mkdir()
        (lock / "pid").write_text(f"{owner.pid}\n")
        (lock / "start").write_text(_lstart(owner.pid, LC_ALL="C", TZ="UTC") + "\n")
        proc = subprocess.run([BASH, "-c", _plist_program(loop, clone)], env=env, capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0
        assert not canary.exists()
        assert not calls.exists(), "git touched a clone a live run owns"
        assert re.search(rf"\[launchd\] .*skipped: pid {owner.pid} still holds", proc.stderr), proc.stderr
    finally:
        owner.kill()
        owner.wait()


@pytest.mark.parametrize("loop", sorted(LOOP_PLISTS))
@pytest.mark.parametrize("pid", ["-1", "0", "1", "abc", ""])
def test_launchd_does_not_stand_down_for_a_garbage_pid(loop, pid, tmp_path):
    """`kill -0 -1` and `kill -0 0` succeed: a garbage pid skipped every night."""
    clone, g, canary, calls, env = _launch_fixture(tmp_path, loop)
    lock = clone / ".weekend-runner.lock"
    lock.mkdir()
    (lock / "pid").write_text(pid + "\n")
    proc = subprocess.run([BASH, "-c", _plist_program(loop, clone)], env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert canary.exists(), f"pid {pid!r} skipped the night"


@pytest.mark.parametrize("loop", sorted(LOOP_PLISTS))
def test_launchd_retries_the_fetch_then_fails_loudly_without_running_the_clone(loop, tmp_path):
    clone, g, canary, calls, env = _launch_fixture(tmp_path, loop, fail_step="fetch")
    proc = subprocess.run([BASH, "-c", _plist_program(loop, clone)], env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 70
    assert not canary.exists()
    assert calls.read_text().count(" fetch ") == 3
    assert "[launchd]" in proc.stderr and "refresh of" in proc.stderr


@pytest.mark.parametrize("loop", sorted(LOOP_PLISTS))
def test_launchd_recovers_from_a_transient_fetch_failure(loop, tmp_path):
    clone, g, canary, calls, env = _launch_fixture(tmp_path, loop)
    flaky = tmp_path / "bin" / "git"
    marker = tmp_path / "failed-once"
    flaky.write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> "{calls}"\n'
        f'case "$*" in *" fetch "*) [ -e "{marker}" ] || {{ touch "{marker}"; exit 1; }} ;; esac\nexit 0\n'
    )
    proc = subprocess.run([BASH, "-c", _plist_program(loop, clone)], env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert canary.exists()


@pytest.mark.parametrize("loop", sorted(LOOP_PLISTS))
def test_launchd_clears_abandoned_git_locks_before_the_checkout(loop, tmp_path):
    clone, g, canary, calls, env = _launch_fixture(tmp_path, loop)
    stale_ref = g / "refs" / "heads" / "main.lock"
    fresh_ref = g / "refs" / "heads" / "other.lock"
    for f in (g / "index.lock", stale_ref, fresh_ref):
        f.write_text("")
    old = time.time() - 3600
    os.utime(stale_ref, (old, old))
    proc = subprocess.run([BASH, "-c", _plist_program(loop, clone)], env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert not (g / "index.lock").exists()
    assert not stale_ref.exists()
    assert fresh_ref.exists()


@pytest.mark.parametrize("loop", sorted(LOOP_PLISTS))
def test_launchd_names_a_missing_timeout_binary(loop, tmp_path):
    clone, g, canary, calls, env = _launch_fixture(tmp_path, loop)
    (tmp_path / "bin" / "timeout").unlink()
    env["PATH"] = f"{tmp_path / 'bin'}:/usr/bin:/bin"
    if shutil.which("timeout", path="/usr/bin:/bin") or shutil.which("gtimeout", path="/usr/bin:/bin"):
        pytest.skip("this host ships timeout in /usr/bin or /bin")
    proc = subprocess.run([BASH, "-c", _plist_program(loop, clone)], env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 78
    assert "no timeout or gtimeout" in proc.stderr
    assert not canary.exists()


# --- agents are not told to second-guess the wrapper's lock -------------------

def test_no_loop_skill_tells_the_agent_to_verify_the_runner_lock():
    pattern = re.compile(r"verify[^.\n]{0,60}\b(exclusive lock|marker and lock|markers and lock)", re.I)
    offenders = []
    for base in (REPO / ".claude" / "skills", REPO / ".claude" / "portable-prompts", REPO / ".codex" / "skills"):
        for path in base.rglob("*.md"):
            if pattern.search(path.read_text(encoding="utf-8")):
                offenders.append(str(path.relative_to(REPO)))
    assert not offenders, offenders
