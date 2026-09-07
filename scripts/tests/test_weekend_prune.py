"""The durable per-runner prune step: allowlist contract + wrapper wiring.

``~/radon-weekend`` is shared by five loop clones, five per-loop venvs, a
private audit scratch dir and whatever worktrees a remediate phase left
behind. Nothing reclaimed it between fires, so it crept up until a human had
to go in with ``rm -rf`` — the single most dangerous command to run by hand in
a directory that also holds the only copy of unpushed work.

``scripts/weekend_prune.py`` replaces that with an ALLOWLIST: it enumerates
the categories it may delete and refuses everything else by construction.
These tests pin the refusals (a denylist regression would silently start
eating a venv or a node_modules tree) and pin the wrapper wiring: the prune
runs at the END of a cycle in all five loops, under a bounded timeout, and a
prune failure never changes the cycle's status or exit code.

House style follows scripts/tests/test_weekend_wrapper_self_rewrite.py: stage
the wrapper into a tmp clone, stub the binaries it shells out to on PATH, run
it with /bin/bash under a launchd-shaped minimal environment.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import weekend_prune  # noqa: E402

# 2026-09-06: these suites stub `claude` and are about wrapper behaviour around the agent, not about which provider runs it. Pin a claude rung so the provider ladder is not what decides the outcome; the ladder itself is covered by test_provider_failover.py.
CLAUDE_RUNG_LADDER = "claude:claude-fable-5[1m]"


# --------------------------------------------------------------------------
# Fixture: a miniature ~/radon-weekend.
# --------------------------------------------------------------------------
GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}


def _git(*args: str, cwd: Path, when: str | None = None) -> str:
    env = dict(GIT_ENV)
    if when is not None:
        env["GIT_AUTHOR_DATE"] = when
        env["GIT_COMMITTER_DATE"] = when
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True
    ).stdout.strip()


def _days_ago(days: float) -> str:
    return f"{int(time.time() - days * 86400)} +0000"


def _fill(path: Path, kb: int = 4) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * (kb * 1024))


@pytest.fixture()
def weekend_root(tmp_path: Path) -> Path:
    """A weekend root with a real clone, a real remote and real worktrees."""
    root = tmp_path / "radon-weekend"
    root.mkdir()

    remote = tmp_path / "remote.git"
    _git("init", "--bare", "-b", "main", str(remote), cwd=tmp_path)

    clone = root / "radon"
    _git("clone", str(remote), str(clone), cwd=tmp_path)
    _fill(clone / "README.md", 1)
    # As in the real clones: a bootstrapped node_modules and a provisioned
    # web/.env are GITIGNORED, so `status --porcelain` never lists them.
    (clone / ".gitignore").write_text(
        "node_modules/\n.env\n.venv/\n.deepsec/\n.weekend-keep\n", encoding="utf-8"
    )
    _git("add", "README.md", ".gitignore", cwd=clone)
    _git("commit", "-m", "init", cwd=clone)
    _git("push", "-u", "origin", "main", cwd=clone)
    (clone / ".radon-weekend-runner").touch()

    # Protected: the per-loop venvs, a bootstrapped node_modules, the private
    # audit scratch, a DeepSec export dir.
    _fill(root / "venv-reliability" / "lib" / "site.py", 64)
    _fill(root / "venv-reliability" / "lib" / "__pycache__" / "site.pyc", 64)
    _fill(clone / "web" / "node_modules" / "pkg" / "index.js", 64)
    _fill(clone / "web" / "node_modules" / "pkg" / "__pycache__" / "x.pyc", 16)
    _fill(root / ".security-nightly-scratch" / "findings.jsonl", 8)
    _fill(clone / ".deepsec" / "export.json", 8)

    # Reclaimable: caches and stale run logs.
    _fill(clone / "scripts" / "__pycache__" / "mod.cpython-313.pyc", 32)
    _fill(clone / ".pytest_cache" / "v" / "cache" / "lastfailed", 8)

    return root


def _add_worktree(root: Path, name: str, *, pushed: bool, age_days: float = 30.0) -> Path:
    """A worktree whose newest commit is ``age_days`` old.

    Age matters: a worktree is only ever a candidate once its branch has been
    quiet for WORKTREE_MIN_IDLE_DAYS, so a freshly committed one is refused
    whatever else is true of it.
    """
    clone = root / "radon"
    wt = root / name
    _git("worktree", "add", "-b", f"loops/{name}", str(wt), "main", cwd=clone)
    _fill(wt / "work.txt", 4)
    _git("add", "work.txt", cwd=wt)
    _git("commit", "-m", name, cwd=wt, when=_days_ago(age_days))
    if pushed:
        _git("push", "-u", "origin", f"loops/{name}", cwd=wt)
    return wt


# --------------------------------------------------------------------------
# 1. The allowlist refuses every protected path, by construction.
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "relative,expected",
    [
        ("radon/web/node_modules", "node_modules"),
        ("radon/web/node_modules/pkg/__pycache__", "node_modules"),
        ("venv-reliability", "venv"),
        ("venv-reliability/lib/__pycache__", "venv"),
        ("radon/.deepsec", "deepsec"),
        (".security-nightly-scratch", "scratch"),
        ("radon", "loop clone"),
    ],
)
def test_protected_paths_are_refused(weekend_root: Path, relative: str, expected: str) -> None:
    reason = weekend_prune.refusal_reason(
        weekend_root / relative, root=weekend_root, temp_root=Path("/nonexistent-temp")
    )
    assert reason is not None, f"{relative} must be refused"
    assert expected in reason


@pytest.mark.parametrize("outside", ["..", "../elsewhere", "/etc", "/"])
def test_paths_outside_the_weekend_root_are_refused(weekend_root: Path, outside: str) -> None:
    target = (weekend_root / outside) if not outside.startswith("/") else Path(outside)
    reason = weekend_prune.refusal_reason(
        target, root=weekend_root, temp_root=Path("/nonexistent-temp")
    )
    assert reason is not None and "outside" in reason


def test_the_weekend_root_itself_is_refused(weekend_root: Path) -> None:
    reason = weekend_prune.refusal_reason(
        weekend_root, root=weekend_root, temp_root=Path("/nonexistent-temp")
    )
    assert reason is not None


def test_a_protected_path_is_refused_even_when_handed_in_as_a_candidate(
    weekend_root: Path,
) -> None:
    """Belt and braces: delete() itself re-checks, so a future category that
    enumerated a venv could still not delete one."""
    victim = weekend_root / "venv-reliability"
    with pytest.raises(weekend_prune.Refused):
        weekend_prune.remove_candidate(victim, root=weekend_root, temp_root=Path("/nonexistent"))
    assert victim.is_dir()


# --------------------------------------------------------------------------
# 2. Categories: what it DOES reclaim, and what it leaves alone.
# --------------------------------------------------------------------------
def test_pycache_inside_a_venv_or_node_modules_is_never_a_candidate(weekend_root: Path) -> None:
    report = weekend_prune.run(root=weekend_root, temp_root=weekend_root / "no-temp", dry_run=True)
    planned = {str(p) for c in report["categories"].values() for p in c["paths"]}
    assert not [p for p in planned if "node_modules" in p or "venv-" in p]
    assert os.path.realpath(weekend_root / "radon" / "scripts" / "__pycache__") in planned


def test_the_run_deletes_caches_and_leaves_every_protected_tree_intact(weekend_root: Path) -> None:
    report = weekend_prune.run(root=weekend_root, temp_root=weekend_root / "no-temp")
    assert report["reclaimed_bytes"] > 0
    assert not (weekend_root / "radon" / "scripts" / "__pycache__").exists()
    assert not (weekend_root / "radon" / ".pytest_cache").exists()
    for keep in (
        "venv-reliability/lib/site.py",
        "radon/web/node_modules/pkg/index.js",
        "radon/web/node_modules/pkg/__pycache__/x.pyc",
        "venv-reliability/lib/__pycache__/site.pyc",
        ".security-nightly-scratch/findings.jsonl",
        "radon/.deepsec/export.json",
        "radon/README.md",
    ):
        assert (weekend_root / keep).exists(), f"{keep} must survive the prune"


def test_dry_run_deletes_nothing(weekend_root: Path) -> None:
    report = weekend_prune.run(
        root=weekend_root, temp_root=weekend_root / "no-temp", dry_run=True
    )
    assert report["dry_run"] is True
    assert (weekend_root / "radon" / "scripts" / "__pycache__").exists()


# --------------------------------------------------------------------------
# 3. Worktrees: pushed goes, unpushed stays.
# --------------------------------------------------------------------------
def test_a_worktree_with_unpushed_commits_is_refused(weekend_root: Path) -> None:
    wt = _add_worktree(weekend_root, "wt-mandate", pushed=False)
    report = weekend_prune.run(root=weekend_root, temp_root=weekend_root / "no-temp")
    assert wt.is_dir(), "a worktree carrying unpushed work must never be removed"
    reasons = {r["path"]: r["reason"] for r in report["refused"]}
    assert "unpushed" in reasons[os.path.realpath(wt)]


def test_a_fully_pushed_worktree_is_removed(weekend_root: Path) -> None:
    wt = _add_worktree(weekend_root, "wt-merged", pushed=True)
    report = weekend_prune.run(root=weekend_root, temp_root=weekend_root / "no-temp")
    assert not wt.exists(), "a worktree 0 commits ahead of its remote is reclaimable"
    assert os.path.realpath(wt) in report["categories"]["worktrees"]["paths"]


def test_a_dirty_worktree_is_refused_even_when_pushed(weekend_root: Path) -> None:
    wt = _add_worktree(weekend_root, "wt-dirty", pushed=True)
    _fill(wt / "uncommitted.txt", 4)
    report = weekend_prune.run(root=weekend_root, temp_root=weekend_root / "no-temp")
    assert wt.is_dir()
    reasons = {r["path"]: r["reason"] for r in report["refused"]}
    assert "dirty" in reasons[os.path.realpath(wt)]


# --------------------------------------------------------------------------
# 4. A clone whose runner lock is held by a live pid is skipped whole.
# --------------------------------------------------------------------------
def test_a_clone_with_a_live_runner_lock_is_skipped(weekend_root: Path) -> None:
    clone = weekend_root / "radon"
    lock = clone / ".weekend-runner.lock"
    lock.mkdir()
    (lock / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
    _add_worktree(weekend_root, "wt-merged", pushed=True)

    report = weekend_prune.run(root=weekend_root, temp_root=weekend_root / "no-temp")

    assert (clone / "scripts" / "__pycache__").exists(), "a running cycle's clone is untouched"
    assert (weekend_root / "wt-merged").is_dir(), "its worktrees are untouched too"
    reasons = " ".join(r["reason"] for r in report["refused"])
    assert "lock" in reasons


def test_a_clone_with_a_stale_runner_lock_is_still_pruned(weekend_root: Path) -> None:
    clone = weekend_root / "radon"
    lock = clone / ".weekend-runner.lock"
    lock.mkdir()
    (lock / "pid").write_text("2147480000\n", encoding="utf-8")

    weekend_prune.run(root=weekend_root, temp_root=weekend_root / "no-temp")

    assert not (clone / "scripts" / "__pycache__").exists()


# --------------------------------------------------------------------------
# 5. The report shape the loops already use.
# --------------------------------------------------------------------------
def test_the_cli_reports_bytes_per_category_and_free_space_before_and_after(
    weekend_root: Path,
) -> None:
    proc = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "weekend_prune.py"),
         "--root", str(weekend_root), "--temp-dir", str(weekend_root / "no-temp")],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "free_before=" in out and "free_after=" in out
    for category in weekend_prune.CATEGORIES:
        assert re.search(rf"^\[prune\] {re.escape(category)}\s", out, re.M), (category, out)
    assert "reclaimed" in out


def test_the_cli_emits_a_machine_readable_report(weekend_root: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "weekend_prune.py"),
         "--root", str(weekend_root), "--temp-dir", str(weekend_root / "no-temp"), "--json"],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert set(report["categories"]) == set(weekend_prune.CATEGORIES)
    assert report["free_before_bytes"] > 0 and report["free_after_bytes"] > 0


def test_the_cli_refuses_a_root_that_is_not_a_weekend_root(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "weekend_prune.py"), "--root", "/"],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode != 0
    assert "refus" in (proc.stdout + proc.stderr).lower()


# --------------------------------------------------------------------------
# Wrapper wiring. Same staging shape as the survivability suites.
# --------------------------------------------------------------------------
LOOPS = {
    "reliability": "reliability_weekend.sh",
    "testing": "testing_weekend.sh",
    "ci-performance": "ci_performance_nightly.sh",
    "documentation": "documentation_nightly.sh",
    "security": "security_nightly.sh",
}
LOOP_IDS = sorted(LOOPS)


def _executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _stage(tmp_path: Path, loop: str, *, agent_rc: int = 0,
           prune_rc: int = 0, extra_env: dict | None = None) -> dict:
    script = LOOPS[loop]
    clone = tmp_path / "clone"
    (clone / "scripts").mkdir(parents=True)
    wrapper = clone / "scripts" / script
    shutil.copy2(REPO / "scripts" / script, wrapper)
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    (clone / ".radon-weekend-runner").touch()
    for marker in (".radon-security-runner", ".radon-reliability-runner",
                   ".radon-testing-runner", ".radon-ci-performance-runner",
                   ".radon-documentation-runner"):
        (clone / marker).touch()
    (clone / "scripts" / "weekend_notify.py").write_text("# unused\n", encoding="utf-8")
    (clone / "scripts" / "weekend_prune.py").write_text("# unused\n", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    order = tmp_path / "order.log"
    curl_stub = bin_dir / "curl"
    wrapper.write_text(
        wrapper.read_text(encoding="utf-8").replace("/usr/bin/curl", str(curl_stub)),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "PUSHOVER_USER=u\nPUSHOVER_TOKEN=t\n", encoding="utf-8"
    )

    # The wrapper pipes the trusted prune out of origin/main into an isolated
    # /usr/bin/python3 — never a python FILE from the agent-writable clone. The
    # git stub serves a stand-in program that records its own argv.
    prune_src = tmp_path / "prune_src.py"
    prune_src.write_text(
        "import os, sys\n"
        f"open({str(order)!r}, 'a').write('PY ' + ' '.join(sys.argv[1:]) + '\\n')\n"
        "sys.exit(int(os.environ.get('STUB_PRUNE_RC', '0')))\n",
        encoding="utf-8",
    )
    _executable(
        bin_dir / "git",
        "#!/bin/bash\n"
        'case "$*" in\n'
        f'  *"show origin/main:scripts/weekend_prune.py"*)\n'
        f'    echo "GIT show weekend_prune" >> "{order}"\n'
        f'    cat "{prune_src}" ;;\n'
        # REL-188: the wrapper calls a phase OK only on commit evidence.
        '  *"rev-parse HEAD"*) date +%s%N; exit 0 ;;\n'
        '  *"--format=%ct"*) date +%s; exit 0 ;;\n'
        "esac\n"
        "exit 0\n",
    )
    _executable(curl_stub, "#!/bin/bash\nexit 0\n")

    complete_line = ""
    if loop == "security":
        src = (REPO / "scripts" / script).read_text(encoding="utf-8")
        marker = re.search(r'PHASE_COMPLETE_MARKER="([^"]+)"', src).group(1)
        complete_line = f"echo '{marker} stub run_id=stub'\n"
    # The deliver phase keys OK on the skill's verdict line, which every loop's
    # wrapper greps for; the stub prints it when invoked for deliver.
    deliver_line = (
        f"case \" $* \" in *\" deliver\"*)"
        f" echo 'NIGHTLY DELIVER READY: loop={loop} prs=0' ;; esac\n"
    )
    _executable(
        bin_dir / "claude",
        "#!/bin/bash\n"
        f'echo "CLAUDE" >> "{order}"\n'
        "echo 'stub agent output'\n" + deliver_line + complete_line
        + 'exit "${STUB_CLAUDE_RC:-0}"\n',
    )
    _executable(
        bin_dir / "timeout",
        "#!/bin/bash\n"
        f'echo "TIMEOUT $*" >> "{order}"\n'
        'while [ $# -gt 0 ]; do\n'
        '  case "$1" in\n'
        '    -k|--kill-after) shift 2 ;;\n'
        '    --foreground|--preserve-status) shift ;;\n'
        '    *) shift; break ;;\n'
        '  esac\n'
        'done\n'
        'exec "$@"\n',
    )
    _executable(
        bin_dir / "gh",
        "#!/bin/bash\n"
        f'echo "GH $1 $2" >> "{order}"\n'
        'case "$1 $2" in\n'
        '  "issue list") echo 42 ;;\n'
        '  "pr list") echo "" ;;\n'
        "esac\n"
        "exit 0\n",
    )
    _executable(
        bin_dir / "python3",
        "#!/bin/bash\n" f'echo "PATHPY $*" >> "{order}"\n' "exit 0\n",
    )

    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "RADON_WEEKEND_REPO": str(clone),
        "RADON_WEEKEND_PROVIDER_LADDER": CLAUDE_RUNG_LADDER,
        "RADON_WEEKEND_FETCH_PAUSE_SECS": "0",
        "STUB_CLAUDE_RC": str(agent_rc),
        "STUB_PRUNE_RC": str(prune_rc),
    }
    env.update(extra_env or {})
    return {"clone": clone, "wrapper": wrapper, "env": env, "order": order, "root": tmp_path}


def _run(cfg: dict, mode: str = "cycle") -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", str(cfg["wrapper"]), mode],
        cwd=cfg["clone"], env=cfg["env"], capture_output=True, text=True,
        timeout=180, check=False,
    )


def _lines(cfg: dict) -> list[str]:
    if not cfg["order"].exists():
        return []
    return [ln for ln in cfg["order"].read_text(encoding="utf-8").splitlines() if ln.strip()]


def _why(result: subprocess.CompletedProcess, cfg: dict) -> str:
    return (f"rc={result.returncode}\n--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}\n--- order ---\n" + "\n".join(_lines(cfg)))


@pytest.mark.parametrize("loop", LOOP_IDS)
def test_the_prune_runs_at_the_end_of_a_cycle(tmp_path: Path, loop: str) -> None:
    cfg = _stage(tmp_path, loop)
    result = _run(cfg, "cycle")

    assert result.returncode == 0, _why(result, cfg)
    lines = _lines(cfg)
    prune = [i for i, ln in enumerate(lines) if ln.startswith("PY ")]
    assert prune, _why(result, cfg)
    assert len(prune) == 1, "one prune per cycle, not one per phase"
    reports = [i for i, ln in enumerate(lines) if ln.startswith("GH issue comment")]
    assert reports and prune[0] > max(reports), "the prune runs AFTER the last phase reports"


@pytest.mark.parametrize("loop", LOOP_IDS)
def test_the_prune_is_bounded_by_a_timeout(tmp_path: Path, loop: str) -> None:
    cfg = _stage(tmp_path, loop)
    result = _run(cfg, "cycle")
    bounded = [ln for ln in _lines(cfg)
               if ln.startswith("TIMEOUT ") and "/usr/bin/python3" in ln]
    assert bounded, _why(result, cfg)
    # report()'s own dead-man comment prune (nightly_issue_prune.py) also
    # pipes a bounded, isolated python3 call, so this loop's disk-prune is
    # no longer necessarily the first one in a cycle — find it by shape.
    weekend_prune_calls = [ln for ln in bounded if "--root" in ln]
    assert weekend_prune_calls, bounded
    assert re.search(
        r"^TIMEOUT \d+ /usr/bin/python3 -I - --root .+ --self ", weekend_prune_calls[0]
    ), weekend_prune_calls[0]


@pytest.mark.parametrize("loop", LOOP_IDS)
def test_the_prune_targets_the_weekend_root(tmp_path: Path, loop: str) -> None:
    cfg = _stage(tmp_path, loop)
    result = _run(cfg, "cycle")
    call = [ln for ln in _lines(cfg) if ln.startswith("PY ")]
    assert call, _why(result, cfg)
    assert call[0] == f"PY --root {cfg['root']} --self {cfg['clone']}", call[0]


@pytest.mark.parametrize("loop", LOOP_IDS)
def test_the_prune_never_execs_a_python_file_from_the_agent_writable_clone(loop: str) -> None:
    """Rail 5: the clone and the venv are agent-writable after a phase, so the
    prune is piped out of origin/main into an ISOLATED system interpreter.

    This is defence in depth, NOT a trust boundary: refs/remotes/origin/main
    lives in the same agent-writable $REPO/.git. What it does buy is that a
    planted WORKING-TREE scripts/weekend_prune.py is never the file that runs,
    and -I keeps cwd, the clone dir and user site off sys.path so a planted
    json.py cannot be imported either. The wrapper comment says exactly this.
    """
    body = (REPO / "scripts" / LOOPS[loop]).read_text(encoding="utf-8")
    assert 'git -C "$REPO" show origin/main:scripts/weekend_prune.py' in body
    assert "/usr/bin/python3 -I -" in body
    assert 'python3 "$REPO/scripts/weekend_prune.py"' not in body
    assert "python3 $REPO/scripts/weekend_prune.py" not in body


@pytest.mark.parametrize("loop", LOOP_IDS)
@pytest.mark.parametrize("agent_rc", [0, 7])
def test_a_prune_failure_leaves_the_cycle_status_unchanged(
    tmp_path: Path, loop: str, agent_rc: int
) -> None:
    clean = _stage(tmp_path / "clean", loop, agent_rc=agent_rc)
    baseline = _run(clean, "cycle")
    broken = _stage(tmp_path / "broken", loop, agent_rc=agent_rc, prune_rc=3)
    result = _run(broken, "cycle")

    assert [ln for ln in _lines(broken) if ln.startswith("PY ")], _why(result, broken)
    assert result.returncode == baseline.returncode, _why(result, broken)
    assert [ln for ln in _lines(broken) if ln.startswith("GH issue comment")] == \
        [ln for ln in _lines(clean) if ln.startswith("GH issue comment")]
    assert "CRASHED" not in result.stdout + result.stderr


@pytest.mark.parametrize("loop", LOOP_IDS)
def test_the_prune_is_skippable_by_env(tmp_path: Path, loop: str) -> None:
    cfg = _stage(tmp_path, loop, extra_env={"RADON_WEEKEND_SKIP_PRUNE": "1"})
    result = _run(cfg, "cycle")
    assert result.returncode == 0, _why(result, cfg)
    assert not [ln for ln in _lines(cfg) if ln.startswith("PY ")]
    assert not [ln for ln in _lines(cfg) if ln.startswith("GIT show weekend_prune")]


# --------------------------------------------------------------------------
# 6. `git worktree remove` deletes GITIGNORED files, so the refusal has to be
#    applied to the worktree's contents, not only to its path.
# --------------------------------------------------------------------------
def test_a_worktree_holding_an_env_file_is_refused_whole(weekend_root: Path) -> None:
    """`status --porcelain` never lists an ignored file, and applying the path
    refusal to the worktree PATH says nothing about what is inside it. A
    hand-provisioned env file is not rebuildable by a command."""
    wt = _add_worktree(weekend_root, "wt-provisioned", pushed=True)
    (wt / "web").mkdir(parents=True, exist_ok=True)
    (wt / "web" / ".env").write_text("UW_TOKEN=secret\n", encoding="utf-8")
    assert _git("status", "--porcelain", cwd=wt) == "", "ignored, so status is clean"

    report = weekend_prune.run(root=weekend_root, temp_root=weekend_root / "no-temp")

    assert (wt / "web" / ".env").exists()
    reasons = {r["path"]: r["reason"] for r in report["refused"]}
    assert ".env" in reasons[os.path.realpath(wt)]


def test_a_worktree_holding_audit_state_is_refused_whole(weekend_root: Path) -> None:
    wt = _add_worktree(weekend_root, "wt-audited", pushed=True)
    _fill(wt / ".deepsec" / "export.json", 8)

    report = weekend_prune.run(root=weekend_root, temp_root=weekend_root / "no-temp")

    assert (wt / ".deepsec" / "export.json").exists()
    reasons = {r["path"]: r["reason"] for r in report["refused"]}
    assert "deepsec" in reasons[os.path.realpath(wt)]


def test_node_modules_inside_an_idle_pushed_worktree_is_reclaimed_with_it(
    weekend_root: Path,
) -> None:
    """The narrow half of the same rule. A bootstrapped tree inside a worktree
    that is already clean, already fully pushed, past the idle floor and
    unmarked belongs to nothing that will run, and it is the bulk of what this
    step exists to reclaim. The CLONE's own node_modules is a different thing
    and stays refused by path."""
    wt = _add_worktree(weekend_root, "wt-bootstrapped", pushed=True)
    _fill(wt / "web" / "node_modules" / "vitest" / "bin" / "v.js", 256)
    _fill(wt / ".venv" / "bin" / "python", 64)

    report = weekend_prune.run(root=weekend_root, temp_root=weekend_root / "no-temp")

    assert not wt.exists()
    assert report["categories"]["worktrees"]["bytes"] > 256 * 1024
    assert (weekend_root / "radon" / "web" / "node_modules" / "pkg" / "index.js").exists()


def test_a_bootstrapped_worktree_still_inside_the_idle_floor_is_kept(
    weekend_root: Path,
) -> None:
    wt = _add_worktree(weekend_root, "wt-live", pushed=True, age_days=0.0)
    _fill(wt / "web" / "node_modules" / "vitest" / "bin" / "v.js", 64)

    weekend_prune.run(root=weekend_root, temp_root=weekend_root / "no-temp")

    assert (wt / "web" / "node_modules" / "vitest" / "bin" / "v.js").exists()


# --------------------------------------------------------------------------
# 7. Idleness, not just cleanliness. A branch awaiting CI triage is
#    indistinguishable from an abandoned one except by age.
# --------------------------------------------------------------------------
def test_a_recently_committed_worktree_is_refused_however_clean(weekend_root: Path) -> None:
    wt = _add_worktree(weekend_root, "wt-mandate", pushed=True, age_days=0.0)
    report = weekend_prune.run(root=weekend_root, temp_root=weekend_root / "no-temp")
    assert wt.is_dir(), "a branch committed today is not abandoned"
    reasons = {r["path"]: r["reason"] for r in report["refused"]}
    assert "idle floor" in reasons[os.path.realpath(wt)]


def test_a_worktree_just_past_the_idle_floor_is_reclaimable(weekend_root: Path) -> None:
    wt = _add_worktree(
        weekend_root, "wt-idle", pushed=True,
        age_days=weekend_prune.WORKTREE_MIN_IDLE_DAYS + 1,
    )
    weekend_prune.run(root=weekend_root, temp_root=weekend_root / "no-temp")
    assert not wt.exists()


def test_a_weekend_keep_marker_pins_a_worktree_forever(weekend_root: Path) -> None:
    wt = _add_worktree(weekend_root, "wt-keep", pushed=True, age_days=400.0)
    (wt / weekend_prune.WORKTREE_KEEP_MARKER).write_text("", encoding="utf-8")

    report = weekend_prune.run(root=weekend_root, temp_root=weekend_root / "no-temp")

    assert wt.is_dir()
    reasons = {r["path"]: r["reason"] for r in report["refused"]}
    assert weekend_prune.WORKTREE_KEEP_MARKER in reasons[os.path.realpath(wt)]


def test_a_worktree_whose_upstream_is_a_local_branch_is_refused(weekend_root: Path) -> None:
    """`branch.<b>.remote = .` makes @{upstream} a LOCAL ref, so ahead-count
    reads 0 while the commits exist on no remote at all."""
    wt = _add_worktree(weekend_root, "wt-local-upstream", pushed=False, age_days=90.0)
    branch = "loops/wt-local-upstream"
    _git("config", f"branch.{branch}.remote", ".", cwd=wt)
    _git("config", f"branch.{branch}.merge", f"refs/heads/{branch}", cwd=wt)
    assert _git("rev-list", "--count", "@{upstream}..HEAD", cwd=wt) == "0"

    report = weekend_prune.run(root=weekend_root, temp_root=weekend_root / "no-temp")

    assert wt.is_dir(), "commits on no remote must never be removed"
    reasons = {r["path"]: r["reason"] for r in report["refused"]}
    assert "remote" in reasons[os.path.realpath(wt)]


# --------------------------------------------------------------------------
# 8. Run-log rotation stays inside its own clone, whatever `logs` really is.
# --------------------------------------------------------------------------
def _old(path: Path, days: float) -> None:
    stamp = time.time() - days * 86400
    os.utime(path, (stamp, stamp))


def test_stale_run_logs_rotate_but_fresh_ones_and_launchd_sinks_do_not(
    weekend_root: Path,
) -> None:
    logs = weekend_root / "radon" / "logs" / "audit"
    stale, fresh, sink = logs / "audit-old.log", logs / "audit-new.log", logs / "launchd-out.log"
    for f in (stale, fresh, sink):
        _fill(f, 4)
    _old(stale, weekend_prune.RUN_LOG_MAX_AGE_DAYS + 1)
    _old(sink, weekend_prune.RUN_LOG_MAX_AGE_DAYS + 1)

    report = weekend_prune.run(root=weekend_root, temp_root=weekend_root / "no-temp")

    assert not stale.exists()
    assert fresh.exists()
    assert sink.exists(), "the plists point StandardOutPath here; it sorts old (R-267)"
    assert report["categories"]["run_logs"]["items"] == 1


def test_a_symlinked_logs_dir_never_rotates_anything_outside_its_clone(
    weekend_root: Path,
) -> None:
    """`logs.is_dir()` follows a symlink. Without the confinement check,
    rotation walks whatever it points at - here a sibling clone's .git."""
    victim = weekend_root / "radon-testing"
    _fill(victim / ".git" / "config", 1)
    (victim / weekend_prune.RUNNER_MARKER).touch()
    for entry in (victim / ".git").iterdir():
        _old(entry, weekend_prune.RUN_LOG_MAX_AGE_DAYS + 5)

    docs = weekend_root / "radon-documentation"
    docs.mkdir()
    (docs / weekend_prune.RUNNER_MARKER).touch()
    (docs / "logs").symlink_to(victim)

    weekend_prune.run(root=weekend_root, temp_root=weekend_root / "no-temp")

    assert (victim / ".git" / "config").exists(), "a sibling clone's .git is not a run log"


def test_run_logs_can_never_delete_under_the_os_temp_dir(
    weekend_root: Path, tmp_path: Path, monkeypatch
) -> None:
    """Only tmp_pytest may look outside the root. Every other category is
    confined to the root even when $TMPDIR points somewhere writable."""
    temp_root = tmp_path / "ostemp"
    victim = temp_root / "precious.txt"
    _fill(victim, 1)
    monkeypatch.setattr(weekend_prune, "_stale_run_logs", lambda *a, **k: [victim])

    report = weekend_prune.run(root=weekend_root, temp_root=temp_root)

    assert victim.exists()
    assert report["categories"]["run_logs"]["items"] == 0
    assert any("outside" in r["reason"] for r in report["refused"])


def test_abandoned_pytest_tmp_trees_are_reclaimed_and_fresh_ones_are_not(
    weekend_root: Path, tmp_path: Path
) -> None:
    temp_root = tmp_path / "ostemp"
    stale = temp_root / "pytest-of-runner" / "pytest-1"
    fresh = temp_root / "pytest-of-runner" / "pytest-99"
    for d in (stale, fresh):
        _fill(d / "junk.bin", 16)
    _old(stale, weekend_prune.TMP_MAX_AGE_DAYS + 1)

    report = weekend_prune.run(root=weekend_root, temp_root=temp_root)

    assert not stale.exists()
    assert fresh.exists()
    assert report["categories"]["tmp_pytest"]["items"] == 1


# --------------------------------------------------------------------------
# 9. The calling clone. It holds its own lock for the whole cycle.
# --------------------------------------------------------------------------
def _lock(clone: Path, pid: int) -> None:
    lock = clone / weekend_prune.RUNNER_LOCK
    lock.mkdir()
    (lock / "pid").write_text(f"{pid}\n", encoding="utf-8")


def test_the_calling_clone_prunes_itself_despite_holding_its_own_lock(
    weekend_root: Path,
) -> None:
    """Without --self the loop that generates the garbage is the one clone
    that can never clean it: the wrapper calls the prune from inside its own
    lock, at the end of the cycle."""
    clone = weekend_root / "radon"
    _lock(clone, os.getpid())

    weekend_prune.run(root=weekend_root, temp_root=weekend_root / "no-temp",
                      caller=str(clone))

    assert not (clone / "scripts" / "__pycache__").exists()
    assert (clone / "web" / "node_modules" / "pkg" / "index.js").exists(), \
        "--self waives the LOCK refusal and nothing else"


def test_self_never_waives_another_clones_live_lock(weekend_root: Path) -> None:
    caller = weekend_root / "radon"
    peer = weekend_root / "radon-testing"
    _fill(peer / "scripts" / "__pycache__" / "mod.pyc", 8)
    (peer / weekend_prune.RUNNER_MARKER).touch()
    _lock(peer, os.getpid())

    report = weekend_prune.run(root=weekend_root, temp_root=weekend_root / "no-temp",
                               caller=str(caller))

    assert (peer / "scripts" / "__pycache__").exists()
    assert any("lock" in r["reason"] for r in report["refused"])


def test_a_lock_taken_while_the_prune_walks_is_still_honoured(
    weekend_root: Path, monkeypatch
) -> None:
    """`locked` is read again immediately before the first unlink and the two
    snapshots are unioned, so a cycle that starts mid-walk is not deleted into."""
    peer = weekend_root / "radon-testing"
    _fill(peer / "scripts" / "__pycache__" / "mod.pyc", 8)
    (peer / weekend_prune.RUNNER_MARKER).touch()

    original = weekend_prune._tmp_pytest

    def _late_lock(*args, **kwargs):
        if not (peer / weekend_prune.RUNNER_LOCK).exists():
            _lock(peer, os.getpid())
        return original(*args, **kwargs)

    monkeypatch.setattr(weekend_prune, "_tmp_pytest", _late_lock)

    weekend_prune.run(root=weekend_root, temp_root=weekend_root / "no-temp")

    assert (peer / "scripts" / "__pycache__").exists(), \
        "enumerated before the lock, refused at the unlink"


# --------------------------------------------------------------------------
# 10. Hardening: macOS is case-insensitive, and operator copy carries no em dash.
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["NODE_MODULES", "Node_Modules", "VENV-testing", ".VENV"])
def test_protection_is_case_insensitive_like_the_filesystem(name: str) -> None:
    assert weekend_prune._protected_part(name) is not None


def test_the_operator_report_carries_no_em_dash(weekend_root: Path) -> None:
    """CLAUDE.md rule 6. These lines land in the phase log a human reads."""
    _add_worktree(weekend_root, "wt-mandate", pushed=False)
    report = weekend_prune.run(root=weekend_root, temp_root=weekend_root / "no-temp",
                               dry_run=True)
    text = weekend_prune.format_report(report)
    assert "kept " in text
    assert "—" not in text, text


# --------------------------------------------------------------------------
# 11. Wrapper wiring the earlier suite left unpinned.
# --------------------------------------------------------------------------
@pytest.mark.parametrize("loop", LOOP_IDS)
def test_the_prune_runs_on_the_single_phase_path_too(tmp_path: Path, loop: str) -> None:
    """`run_phase "$MODE"; prune_weekend_root; exit "$RC"` is the branch an
    operator takes by hand, and the one call site under `set -e` with on_crash
    armed. The cycle tests never reach it."""
    cfg = _stage(tmp_path, loop, agent_rc=0)
    result = _run(cfg, "audit")

    assert result.returncode == 0, _why(result, cfg)
    assert [ln for ln in _lines(cfg) if ln.startswith("PY ")], _why(result, cfg)
    assert "CRASHED" not in result.stdout + result.stderr


@pytest.mark.parametrize("loop", LOOP_IDS)
def test_a_single_phase_failure_still_survives_a_failing_prune(
    tmp_path: Path, loop: str
) -> None:
    clean = _stage(tmp_path / "clean", loop, agent_rc=7)
    baseline = _run(clean, "audit")
    broken = _stage(tmp_path / "broken", loop, agent_rc=7, prune_rc=3)
    result = _run(broken, "audit")

    assert [ln for ln in _lines(broken) if ln.startswith("PY ")], _why(result, broken)
    assert result.returncode == baseline.returncode, _why(result, broken)
    assert "CRASHED" not in result.stdout + result.stderr


@pytest.mark.parametrize("loop", LOOP_IDS)
def test_the_prune_never_degrades_to_an_empty_command_word(loop: str) -> None:
    """TIMEOUT_BIN comes from `command -v timeout`, which is empty on a macOS
    host without coreutils. `"${TIMEOUT_BIN:-}"` does not defend against that:
    it executes the EMPTY command word, rc 127, and the prune silently never
    runs while the log claims a bound. Guard on the value instead."""
    body = (REPO / "scripts" / LOOPS[loop]).read_text(encoding="utf-8")
    assert '| "${TIMEOUT_BIN:-}"' not in body, "an empty command word, not a default"
    block = body.split("prune_weekend_root() {", 1)[1].split("\n}", 1)[0]
    assert '[[ -z "${TIMEOUT_BIN:-}" ]]' in block, "guard on the value"
    assert '| "$TIMEOUT_BIN"' in block, "and then use it bare, like every other site"


@pytest.mark.parametrize("loop", LOOP_IDS)
def test_the_wrapper_does_not_claim_origin_main_is_a_trust_boundary(loop: str) -> None:
    """refs/remotes/origin/main lives in the same agent-writable $REPO/.git, so
    the pipe defeats a working-tree plant and nothing stronger. Say that."""
    body = (REPO / "scripts" / LOOPS[loop]).read_text(encoding="utf-8")
    block = body.split("prune_weekend_root() {")[0].rsplit("# Durable per-runner", 1)[-1]
    assert "NOT a network trust anchor" in block or "not a boundary" in block.lower()
