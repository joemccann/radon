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
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import weekend_prune  # noqa: E402


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


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, env=GIT_ENV, capture_output=True, text=True, check=True
    ).stdout.strip()


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
    _git("add", "README.md", cwd=clone)
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


def _add_worktree(root: Path, name: str, *, pushed: bool) -> Path:
    clone = root / "radon"
    wt = root / name
    _git("worktree", "add", "-b", f"loops/{name}", str(wt), "main", cwd=clone)
    _fill(wt / "work.txt", 4)
    _git("add", "work.txt", cwd=wt)
    _git("commit", "-m", name, cwd=wt)
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
    assert re.search(r"^TIMEOUT \d+ /usr/bin/python3 -I - --root ", bounded[0]), bounded[0]


@pytest.mark.parametrize("loop", LOOP_IDS)
def test_the_prune_targets_the_weekend_root(tmp_path: Path, loop: str) -> None:
    cfg = _stage(tmp_path, loop)
    result = _run(cfg, "cycle")
    call = [ln for ln in _lines(cfg) if ln.startswith("PY ")]
    assert call, _why(result, cfg)
    assert call[0] == f"PY --root {cfg['root']}", call[0]


@pytest.mark.parametrize("loop", LOOP_IDS)
def test_the_prune_never_execs_a_python_file_from_the_agent_writable_clone(loop: str) -> None:
    """Rail 5: the clone and the venv are agent-writable after a phase, so the
    prune is piped out of origin/main into an ISOLATED system interpreter."""
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
