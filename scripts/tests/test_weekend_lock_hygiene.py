"""L1-L5 weekend runner-lock hygiene (plan cases 1-13)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from test_rel137_weekend_wrapper_survivability import (
    BASH,
    CLAUDE_RUNG_LADDER,
    LOOPS,
    _cloned_wrapper,
    _lock_lib,
    _runner_clone,
    _stub_bin,
)

REPO = Path(__file__).resolve().parents[2]
SKILL_ROOTS = (
    REPO / ".claude" / "skills",
    REPO / ".claude" / "portable-prompts",
    REPO / ".codex" / "skills",
)


def _fn_body(text: str, name: str) -> str:
    start = text.index(f"{name}() {{")
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise AssertionError(f"unclosed {name}")


def _lib(name: str, snippet: str, tmp_path: Path) -> subprocess.CompletedProcess:
    prelude = (
        f'export WEEKEND_ROOT="{tmp_path}"\n'
        f'export REPO="{tmp_path}/radon-testing"\n'
        f'export STAMP="TESTSTAMP"\n'
    )
    return _lock_lib(LOOPS[name], prelude + snippet, tmp_path)


class TestLockHygieneL1ToL5:
    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_case1_file_shaped_dead_pid_is_reclaimed(self, name, tmp_path):
        lock = tmp_path / "lock.d"
        lock.write_text("999999\n", encoding="utf-8")
        out = _lib(
            name,
            f'acquire_runner_lock "{lock}"\n'
            f'echo RC:$?\n'
            f'cat "{lock}/pid"\n'
            f'test -f "{lock}/start" && echo HAS_START\n',
            tmp_path,
        )
        assert out.returncode == 0, (out.stdout, out.stderr)
        assert lock.is_dir()
        assert (lock / "pid").read_text(encoding="utf-8").strip().isdigit()
        assert (lock / "start").exists()
        preserved = list((tmp_path / ".stale-locks").glob("*.weekend-runner.lock.999999.*"))
        assert preserved, out.stderr
        assert "999999" in preserved[0].read_text(encoding="utf-8")
        assert "reclaiming stale runner lock (pid 999999, file)" in out.stderr

    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_case2_file_shaped_live_pid_must_not_steal(self, name, tmp_path):
        lock = tmp_path / "lock.d"
        lock.write_text(f"{os.getpid()}\n", encoding="utf-8")
        out = _lib(name, f'acquire_runner_lock "{lock}"; echo RC:$?\n', tmp_path)
        assert out.returncode != 0
        assert lock.is_file()
        assert str(os.getpid()) in lock.read_text(encoding="utf-8")
        assert "held by pid" in out.stderr

    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_case3_empty_plain_file_is_reclaimed(self, name, tmp_path):
        lock = tmp_path / "lock.d"
        lock.write_text("", encoding="utf-8")
        out = _lib(name, f'acquire_runner_lock "{lock}"\n', tmp_path)
        assert out.returncode == 0, (out.stdout, out.stderr)
        assert lock.is_dir()
        assert "foreign plain-file lock (no pid)" in out.stderr
        preserved = list((tmp_path / ".stale-locks").glob("*.weekend-runner.lock.nopid.*"))
        assert preserved, out.stderr

    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_case4_directory_dead_pid_reclaimed_with_and_without_start(self, name, tmp_path):
        lock = tmp_path / "lock.d"
        lock.mkdir()
        (lock / "pid").write_text("999999\n", encoding="utf-8")
        out = _lib(name, f'acquire_runner_lock "{lock}"\n', tmp_path)
        assert out.returncode == 0, out.stderr
        assert (lock / "pid").read_text(encoding="utf-8").strip() != "999999"
        lock2 = tmp_path / "lock2.d"
        lock2.mkdir()
        (lock2 / "pid").write_text("999999\n", encoding="utf-8")
        (lock2 / "start").write_text("Thu Jan  1 00:00:00 1970\n", encoding="utf-8")
        out2 = _lib(name, f'acquire_runner_lock "{lock2}"\n', tmp_path)
        assert out2.returncode == 0, out2.stderr

    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_case5_directory_live_pid_must_not_steal(self, name, tmp_path):
        lock = tmp_path / "lock.d"
        lock.mkdir()
        (lock / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
        out = _lib(name, f'acquire_runner_lock "{lock}"\n', tmp_path)
        assert out.returncode != 0
        assert (lock / "pid").read_text(encoding="utf-8").strip() == str(os.getpid())
        start = subprocess.check_output(
            ["/bin/ps", "-p", str(os.getpid()), "-o", "lstart="],
            text=True,
        ).strip()
        lock2 = tmp_path / "lock2.d"
        lock2.mkdir()
        (lock2 / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
        (lock2 / "start").write_text(start + "\n", encoding="utf-8")
        out2 = _lib(name, f'acquire_runner_lock "{lock2}"\n', tmp_path)
        assert out2.returncode != 0, out2.stderr

    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_case6_live_pid_stale_start_is_reclaimed(self, name, tmp_path):
        lock = tmp_path / "lock.d"
        lock.mkdir()
        (lock / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
        (lock / "start").write_text("Thu Jan  1 00:00:00 1970\n", encoding="utf-8")
        out = _lib(name, f'acquire_runner_lock "{lock}"\n', tmp_path)
        assert out.returncode == 0, out.stderr
        assert (lock / "pid").read_text(encoding="utf-8").strip() != str(os.getpid()) or (
            lock / "start"
        ).read_text(encoding="utf-8").strip() != "Thu Jan  1 00:00:00 1970"
        assert "reclaiming stale runner lock" in out.stderr

    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_case7_empty_directory_pid_refuses_r411(self, name, tmp_path):
        lock = tmp_path / "lock.d"
        lock.mkdir()
        (lock / "pid").write_text("", encoding="utf-8")
        out = _lib(name, f'acquire_runner_lock "{lock}"; echo RC:$?\n', tmp_path)
        assert out.returncode != 0
        assert "pid not yet published" in out.stderr
        assert lock.is_dir()

    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_case8_eperm_is_not_death(self, name, tmp_path):
        live = os.getpid()
        out = _lib(
            name,
            f'kill() {{ return 1; }}\n'
            f'if pid_alive {live}; then echo ALIVE; else echo DEAD; fi\n'
            f'if pid_alive 999999; then echo ALIVE999; else echo DEAD999; fi\n',
            tmp_path,
        )
        assert out.returncode == 0, (out.stdout, out.stderr)
        assert "ALIVE" in out.stdout
        assert "DEAD999" in out.stdout

    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_case11_eight_way_race_exactly_one_won(self, name, tmp_path):
        lock = tmp_path / "lock.d"
        snippet = (
            f'export WEEKEND_ROOT="{tmp_path}"\n'
            f'export REPO="{tmp_path}/radon-testing"\n'
            f'export STAMP="TESTSTAMP"\n'
            f'if acquire_runner_lock "{lock}"; then echo WON; sleep 3; '
            "else echo LOST; fi\n"
        )
        runner = tmp_path / "race.sh"
        runner.write_text(
            f"set -Eeuo pipefail\nsource {LOOPS[name]} --lock-lib-only\n" + snippet,
            encoding="utf-8",
        )
        procs = [
            subprocess.Popen([BASH, str(runner)], stdout=subprocess.PIPE, text=True)
            for _ in range(8)
        ]
        outs = [p.communicate(timeout=90)[0] for p in procs]
        assert sum("WON" in o for o in outs) == 1, outs

    def test_case13_helper_bodies_are_byte_identical_and_sweep_is_first(self):
        bodies = {key: [] for key in (
            "pid_alive",
            "acquire_runner_lock",
            "release_runner_lock",
            "sweep_shared_parent_lock",
        )}
        for path in LOOPS.values():
            text = path.read_text(encoding="utf-8")
            for key in bodies:
                bodies[key].append(_fn_body(text, key))
            sweep_call = text.index('sweep_shared_parent_lock "$WEEKEND_ROOT/.weekend-runner.lock"')
            acq_call = text.index('acquire_runner_lock "$RUNNER_LOCK"')
            assert sweep_call < acq_call, path
        for key, found in bodies.items():
            assert len(set(found)) == 1, f"{key} drifted across wrappers"


class TestSharedParentSweepAndLiveRefuse:
    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_case9_dead_shared_parent_file_is_swept(self, name, tmp_path):
        repo = _runner_clone(tmp_path, name)
        shared = tmp_path / ".weekend-runner.lock"
        shared.write_text("999999\n", encoding="utf-8")
        started = tmp_path / "claude-started"
        bin_dir, gh_log, _py = _stub_bin(
            tmp_path,
            claude_body=f"#!/bin/sh\ntouch {started}\nexit 0\n",
        )
        proc = subprocess.run(
            [BASH, str(_cloned_wrapper(repo, name)), "audit"],
            env={
                **os.environ,
                "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                "RADON_WEEKEND_REPO": str(repo),
                "RADON_WEEKEND_PROVIDER_LADDER": CLAUDE_RUNG_LADDER,
                "RADON_WEEKEND_SKIP_PRUNE": "1",
                "RADON_WEEKEND_BROWSER_HOST_WAIT_SECS": "2",
                "HOME": str(tmp_path / "home"),
            },
            capture_output=True,
            text=True,
            timeout=60,
        )
        logs = (proc.stdout + proc.stderr)
        combined = logs
        log_dir = repo / "logs"
        if log_dir.exists():
            for path in log_dir.rglob("*.log"):
                combined += path.read_text(encoding="utf-8")
        assert "foreign-lock=removed:999999" in combined, combined
        assert not shared.exists()
        preserved = list((tmp_path / ".stale-locks").glob("shared.weekend-runner.lock.999999.*"))
        assert preserved
        assert "999999" in preserved[0].read_text(encoding="utf-8")

    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_case9_live_shared_parent_is_left_and_named(self, name, tmp_path):
        repo = _runner_clone(tmp_path, name)
        shared = tmp_path / ".weekend-runner.lock"
        shared.write_text(f"{os.getpid()}\n", encoding="utf-8")
        started = tmp_path / "claude-started"
        bin_dir, gh_log, _py = _stub_bin(
            tmp_path,
            claude_body=f"#!/bin/sh\ntouch {started}\nexit 0\n",
        )
        proc = subprocess.run(
            [BASH, str(_cloned_wrapper(repo, name)), "audit"],
            env={
                **os.environ,
                "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                "RADON_WEEKEND_REPO": str(repo),
                "RADON_WEEKEND_PROVIDER_LADDER": CLAUDE_RUNG_LADDER,
                "RADON_WEEKEND_SKIP_PRUNE": "1",
                "RADON_WEEKEND_BROWSER_HOST_WAIT_SECS": "2",
                "HOME": str(tmp_path / "home"),
            },
            capture_output=True,
            text=True,
            timeout=60,
        )
        combined = proc.stdout + proc.stderr
        if gh_log.exists():
            combined += gh_log.read_text(encoding="utf-8")
        log_dir = repo / "logs"
        if log_dir.exists():
            for path in log_dir.rglob("*.log"):
                combined += path.read_text(encoding="utf-8")
        assert shared.exists()
        assert f"foreign-lock=live:{os.getpid()}" in combined, combined
        assert str(os.getpid()) in combined

    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_case9_empty_shared_parent_and_idempotent_second_run(self, name, tmp_path):
        repo = _runner_clone(tmp_path, name)
        shared = tmp_path / ".weekend-runner.lock"
        shared.write_text("", encoding="utf-8")
        snippet = (
            f'export WEEKEND_ROOT="{tmp_path}"\n'
            f'export REPO="{repo}"\n'
            f'export STAMP="TESTSTAMP"\n'
            f'sweep_shared_parent_lock "{shared}"\n'
            f'sweep_shared_parent_lock "{shared}"\n'
        )
        out = _lock_lib(LOOPS[name], snippet, tmp_path)
        assert out.returncode == 0, out.stderr
        assert "foreign-lock=removed:nopid" in out.stderr
        assert out.stderr.count("foreign-lock=") == 1
        assert not shared.exists()

    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_case9_directory_shared_parent_dead_pid(self, name, tmp_path):
        shared = tmp_path / ".weekend-runner.lock"
        shared.mkdir()
        (shared / "pid").write_text("999999\n", encoding="utf-8")
        snippet = (
            f'export WEEKEND_ROOT="{tmp_path}"\n'
            f'export REPO="{tmp_path}/radon-testing"\n'
            f'export STAMP="TESTSTAMP"\n'
            f'sweep_shared_parent_lock "{shared}"\n'
        )
        out = _lock_lib(LOOPS[name], snippet, tmp_path)
        assert out.returncode == 0, out.stderr
        assert "foreign-lock=removed:999999" in out.stderr
        assert not shared.exists()

    @pytest.mark.parametrize("name", sorted(LOOPS))
    def test_case10_live_clone_lock_exits_3_and_pages(self, name, tmp_path):
        repo = _runner_clone(tmp_path, name)
        lock = repo / ".weekend-runner.lock"
        lock.mkdir()
        (lock / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
        start = subprocess.check_output(
            ["/bin/ps", "-p", str(os.getpid()), "-o", "lstart="],
            text=True,
        ).strip()
        (lock / "start").write_text(start + "\n", encoding="utf-8")
        bin_dir, gh_log, py_log = _stub_bin(
            tmp_path,
            claude_body="#!/bin/sh\nexit 0\n",
        )
        proc = subprocess.run(
            [BASH, str(_cloned_wrapper(repo, name)), "audit"],
            env={
                **os.environ,
                "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                "RADON_WEEKEND_REPO": str(repo),
                "RADON_WEEKEND_PROVIDER_LADDER": CLAUDE_RUNG_LADDER,
                "HOME": str(tmp_path / "home"),
            },
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 3, (proc.stdout, proc.stderr)
        calls = gh_log.read_text(encoding="utf-8") if gh_log.exists() else ""
        assert "REFUSED (lock held)" in calls, calls
        assert str(os.getpid()) in calls, calls
        pages = py_log.read_text(encoding="utf-8") if py_log.exists() else ""
        assert "pushover.net" in pages, pages


class TestSkillsNeverTouchTheLock:
    def test_no_skill_tells_an_agent_to_reclaim_or_kill0(self):
        instructional = re.compile(
            r"(?i)(?<!never )(?<!not )(?<!don't )(?<!do not )"
            r"(take an exclusive(?: loop| security-loop)? lock|"
            r"kill -0|reclaim(?:ing)? (?:the |a |stale )?(?:runner )?lock|"
            r"create (?:a |the |an )?(?:exclusive )?loop lock)"
        )
        hits = []
        for root in SKILL_ROOTS:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                text = path.read_text(encoding="utf-8")
                for i, line in enumerate(text.splitlines(), 1):
                    if instructional.search(line) and not re.search(
                        r"(?i)never|do not|don't|must not|forbids",
                        line,
                    ):
                        hits.append(f"{path}:{i}:{line.strip()}")
        assert hits == []
