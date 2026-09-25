"""Split gitdirs: the agent gets its own writable gitdir; R02-A still holds.

2026-09-23 on the Mac mini: R02-A (26b858ca) moved host git to
`$WEEKEND_ROOT/.gitdirs/<loop>.git` and kept it out of the codex writable
roots. The clone's `.git` gitfile pointed at that same gitdir, so the codex
rung could not write git metadata at all: `git switch -c reliability/<date>`
died on `index.lock: Operation not permitted`, deliver's `git fetch` could not
write FETCH_HEAD, and `nightly_publish.py check` failed on merge-tree. Four
loops could not land any work.

The split: host git keeps `$WEEKEND_ROOT/.gitdirs/<loop>.git` and always names
it explicitly. The clone's gitfile names a second gitdir,
`$WEEKEND_ROOT/.gitdirs-agent/<loop>.git`, the only one in the codex writable
roots. Host git never opens the agent gitdir: it rewrites the agent gitdir's
config, gitfile and alternates from host state before every round, and reads
the agent's commits through a throwaway host-written gitdir.
"""

from __future__ import annotations

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

WRAPPERS = {
    "reliability": REPO / "scripts" / "reliability_weekend.sh",
    "testing": REPO / "scripts" / "testing_weekend.sh",
    "ci-performance": REPO / "scripts" / "ci_performance_nightly.sh",
    "documentation": REPO / "scripts" / "documentation_nightly.sh",
    "security": REPO / "scripts" / "security_nightly.sh",
    "security-deepsec": REPO / "scripts" / "security_deepsec_nightly.sh",
}
HELPERS = (
    "_agent_git_write",
    "sanitize_agent_gitdir",
    "align_agent_gitdir",
    "_agent_head_sha",
    "agent_git_ro",
)
BASH = shutil.which("bash") or "/bin/bash"
GIT_ENV = {
    "PATH": os.environ["PATH"],
    "HOME": "/nonexistent-home",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
}


def _fn(text: str, name: str) -> str:
    m = re.search(rf"^{re.escape(name)}\(\) \{{\n(?:.*\n)*?^\}}$", text, re.M)
    assert m, f"{name}() not found"
    return m.group(0)


def _git(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, env=GIT_ENV, capture_output=True, text=True, timeout=60,
    )
    assert not check or proc.returncode == 0, (args, proc.stderr)
    return proc


class Layout:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.root = tmp_path / "radon-weekend"
        self.root.mkdir()
        seed = tmp_path / "seed"
        seed.mkdir()
        _git("init", "-q", "-b", "main", cwd=seed)
        (seed / "README").write_text("x\n", encoding="utf-8")
        _git("add", "README", cwd=seed)
        _git("commit", "-q", "-m", "seed", cwd=seed)
        self.origin = tmp_path / "origin.git"
        _git("clone", "-q", "--bare", str(seed), str(self.origin), cwd=tmp_path)
        self.repo = self.root / "radon"
        self.host = self.root / ".gitdirs" / "reliability.git"
        self.agent = self.root / ".gitdirs-agent" / "reliability.git"
        self.host.parent.mkdir(mode=0o700)
        _git("clone", "-q", f"--separate-git-dir={self.host}", str(self.origin),
             str(self.repo), cwd=tmp_path)

    def run(self, wrapper: Path, body: str, *, pins: bool = False) -> subprocess.CompletedProcess:
        text = wrapper.read_text(encoding="utf-8")
        env = dict(GIT_ENV)
        if pins:
            env.update(GIT_CONFIG_COUNT="2", GIT_CONFIG_KEY_0="core.hooksPath",
                       GIT_CONFIG_VALUE_0="/dev/null", GIT_CONFIG_KEY_1="core.fsmonitor",
                       GIT_CONFIG_VALUE_1="false")
        env["TMPDIR"] = str(self.tmp)
        driver = "\n".join(
            [
                "set -Eeuo pipefail",
                f"REPO={self.repo}",
                f"WEEKEND_ROOT={self.root}",
                f"HOST_GITDIR={self.host}",
                f"AGENT_GITDIR={self.agent}",
                *(_fn(text, h) for h in HELPERS),
                body,
            ]
        )
        return subprocess.run([BASH, "-c", driver], cwd=self.repo, env=env,
                              capture_output=True, text=True, timeout=60)

    def agent_git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        """What a rung runs: plain `git` in the clone, gitdir by discovery."""
        return _git(*args, cwd=self.repo, check=check)

    def host_state(self) -> dict:
        refs = sorted(
            str(p.relative_to(self.host)) for p in (self.host / "refs").rglob("*") if p.is_file()
        )
        return {"config": (self.host / "config").read_bytes(), "refs": refs,
                "fetch_head": (self.host / "FETCH_HEAD").exists()}


@pytest.fixture
def layout(tmp_path: Path) -> Layout:
    return Layout(tmp_path)


def _freeze(path: Path) -> None:
    for p in [path, *path.rglob("*")]:
        if p.is_symlink():
            continue
        p.chmod(p.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def _thaw(path: Path) -> None:
    for p in [path, *path.rglob("*")]:
        if not p.is_symlink():
            p.chmod(p.stat().st_mode | stat.S_IWUSR)


# --- 1. the codex grant names the agent gitdir and never the host gitdir ----
@pytest.mark.parametrize("loop", sorted(WRAPPERS))
def test_codex_writable_roots_hold_the_agent_gitdir_not_the_host_gitdir(loop):
    body = WRAPPERS[loop].read_text(encoding="utf-8")
    fn = _fn(body, "launch_round")
    roots = fn[fn.index("writable_roots=") : fn.index("]", fn.index("writable_roots=")) + 1]
    assert '\\"$AGENT_GITDIR\\"' in roots, roots
    assert "HOST_GITDIR" not in roots and ".gitdirs/" not in roots, roots
    assert "$REPO/.git" not in roots, roots
    assert 'AGENT_GITDIR="$WEEKEND_ROOT/.gitdirs-agent/${LOOP_SLUG}.git"' in body
    assert 'HOST_GITDIR="$WEEKEND_ROOT/.gitdirs/${LOOP_SLUG}.git"' in body


@pytest.mark.parametrize("helper", HELPERS)
def test_the_split_helpers_are_byte_identical_across_the_six_wrappers(helper):
    bodies = {n: _fn(p.read_text(encoding="utf-8"), helper) for n, p in WRAPPERS.items()}
    assert len(set(bodies.values())) == 1, sorted(bodies)


@pytest.mark.parametrize("loop", sorted(WRAPPERS))
def test_the_wrapper_resyncs_the_agent_gitdir_at_every_boundary(loop):
    body = WRAPPERS[loop].read_text(encoding="utf-8")
    assert "align_agent_gitdir || return 1" in _fn(body, "ground_truth")
    if "reground_for_continuation() {" in body:
        assert "&& align_agent_gitdir" in _fn(body, "reground_for_continuation")
    launch = body.index('    launch_round "$remain"\n')
    guard = body.rindex("    if ! sanitize_agent_gitdir; then\n", 0, launch)
    assert launch - guard < 800 and "RC=70" in body[guard:launch], body[guard:launch]
    # The security loops are scored on a completion marker, not a commit.
    if "phase_committed() {" in body:
        committed = _fn(body, "phase_committed")
        assert "agent_git_ro rev-parse HEAD" in committed, committed
        assert "agent_git_ro log -1 --format=%ct HEAD" in committed, committed
        assert 'PHASE_HEAD_BEFORE="$(agent_git_ro rev-parse HEAD' in body
    assert '--work-tree="$REPO" rev-parse HEAD' not in body
    # nightly_green_base.py runs `git -C "$REPO"`; discovery would open the
    # agent gitdir, so the host pins GIT_DIR for that helper.
    green = _fn(body, "resolve_green_main_sha")
    assert 'GIT_DIR="$HOST_GITDIR"' in green, green


# --- 2. the agent can do the whole phase contract in its own gitdir ---------
@pytest.mark.parametrize("loop", sorted(WRAPPERS))
def test_align_points_the_clone_at_a_clean_agent_gitdir(layout, loop):
    proc = layout.run(WRAPPERS[loop], "align_agent_gitdir")
    assert proc.returncode == 0, proc.stderr
    assert (layout.repo / ".git").read_text() == f"gitdir: {layout.agent}\n"
    assert layout.agent_git("status", "--porcelain").stdout == ""
    host_head = _git(f"--git-dir={layout.host}", "rev-parse", "HEAD", cwd=layout.tmp).stdout
    assert layout.agent_git("rev-parse", "HEAD").stdout == host_head
    assert layout.agent_git("rev-parse", "origin/main").stdout == host_head
    assert stat.S_IMODE((layout.root / ".gitdirs-agent").stat().st_mode) == 0o700


def test_the_agent_branches_commits_fetches_merge_trees_and_pushes_without_the_host_gitdir(layout):
    assert layout.run(WRAPPERS["reliability"], "align_agent_gitdir").returncode == 0
    before = layout.host_state()
    _freeze(layout.host)  # the codex sandbox: host gitdir readable, not writable
    try:
        layout.agent_git("switch", "-q", "-c", "reliability/2026-09-23", "origin/main")
        (layout.repo / "fix.txt").write_text("fix\n", encoding="utf-8")
        layout.agent_git("add", "fix.txt")
        layout.agent_git("commit", "-q", "-m", "fix")
        layout.agent_git("fetch", "-q", "origin")
        tree = layout.agent_git("merge-tree", "--write-tree", "origin/main", "HEAD")
        assert re.match(r"^[0-9a-f]{40}$", tree.stdout.splitlines()[0])
        layout.agent_git("push", "-q", "origin", "reliability/2026-09-23")
    finally:
        _thaw(layout.host)
    assert layout.host_state() == before
    pushed = _git(f"--git-dir={layout.origin}", "rev-parse", "refs/heads/reliability/2026-09-23",
                  cwd=layout.tmp).stdout
    assert pushed == layout.agent_git("rev-parse", "HEAD").stdout


def test_commit_evidence_is_read_without_opening_the_agent_gitdir(layout):
    assert layout.run(WRAPPERS["reliability"], "align_agent_gitdir").returncode == 0
    before = layout.run(WRAPPERS["reliability"], "agent_git_ro rev-parse HEAD").stdout
    layout.agent_git("switch", "-q", "-c", "reliability/x")
    (layout.repo / "f").write_text("f\n", encoding="utf-8")
    layout.agent_git("add", "f")
    layout.agent_git("commit", "-q", "-m", "f")
    after = layout.run(WRAPPERS["reliability"], "agent_git_ro rev-parse HEAD").stdout
    assert after.strip() == layout.agent_git("rev-parse", "HEAD").stdout.strip() != before.strip()
    epoch = layout.run(WRAPPERS["reliability"], "agent_git_ro log -1 --format=%ct HEAD").stdout
    assert int(epoch) > 0


# --- 2b. an empty dated branch never pins a round to a stale main ---------
# 2026-09-24: the 00:00 remediate created reliability/2026-09-24 at e69de638
# and committed nothing. The fixes for its baseline blockers (#674) merged at
# 06:15; the 19:40 rerun reset main to 80bbfdb2, then resumed the dated branch
# and re-ran the baseline on e69de638, failing the same nine tests again.
def _advance_main(layout: Layout) -> str:
    seed = layout.tmp / "seed"
    (seed / "later").write_text("later\n", encoding="utf-8")
    _git("add", "later", cwd=seed)
    _git("commit", "-q", "-m", "later", cwd=seed)
    _git("push", "-q", str(layout.origin), "main", cwd=seed)
    host = (f"--git-dir={layout.host}", f"--work-tree={layout.repo}")
    _git(*host, "fetch", "-q", "origin", cwd=layout.tmp)
    _git(*host, "reset", "-q", "--hard", "origin/main", cwd=layout.tmp)
    return _git(f"--git-dir={layout.host}", "rev-parse", "HEAD", cwd=layout.tmp).stdout.strip()


@pytest.mark.parametrize("loop", sorted(WRAPPERS))
def test_align_moves_an_empty_dated_branch_to_the_new_main(layout, loop):
    prefix = f"PR_BRANCH_PREFIX={loop}/"
    assert layout.run(WRAPPERS[loop], f"{prefix}; align_agent_gitdir").returncode == 0
    layout.agent_git("branch", "-q", f"{loop}/2026-09-24", "origin/main")
    new_main = _advance_main(layout)

    proc = layout.run(WRAPPERS[loop], f"{prefix}; align_agent_gitdir")

    assert proc.returncode == 0, proc.stderr
    assert layout.agent_git("rev-parse", f"{loop}/2026-09-24").stdout.strip() == new_main


def test_align_never_moves_a_dated_branch_that_holds_work(layout):
    wrapper = WRAPPERS["reliability"]
    assert layout.run(wrapper, "PR_BRANCH_PREFIX=reliability/; align_agent_gitdir").returncode == 0
    layout.agent_git("switch", "-q", "-c", "reliability/2026-09-24", "origin/main")
    (layout.repo / "fix.txt").write_text("fix\n", encoding="utf-8")
    layout.agent_git("add", "fix.txt")
    layout.agent_git("commit", "-q", "-m", "fix")
    work = layout.agent_git("rev-parse", "HEAD").stdout.strip()
    _advance_main(layout)

    proc = layout.run(wrapper, "PR_BRANCH_PREFIX=reliability/; align_agent_gitdir")

    assert proc.returncode == 0, proc.stderr
    assert layout.agent_git("rev-parse", "reliability/2026-09-24").stdout.strip() == work


def test_align_never_writes_through_a_planted_branch_directory_symlink(layout):
    wrapper = WRAPPERS["reliability"]
    assert layout.run(wrapper, "PR_BRANCH_PREFIX=reliability/; align_agent_gitdir").returncode == 0
    outside = layout.tmp / "outside"
    outside.mkdir()
    head = layout.agent_git("rev-parse", "HEAD").stdout
    (outside / "2026-09-24").write_text(head, encoding="utf-8")
    (layout.agent / "refs" / "heads" / "reliability").symlink_to(outside)
    _advance_main(layout)

    proc = layout.run(wrapper, "PR_BRANCH_PREFIX=reliability/; align_agent_gitdir")

    assert proc.returncode == 0, proc.stderr
    assert (outside / "2026-09-24").read_text(encoding="utf-8") == head


def test_align_without_a_branch_prefix_leaves_dated_branches_alone(layout):
    wrapper = WRAPPERS["reliability"]
    assert layout.run(wrapper, "align_agent_gitdir").returncode == 0
    layout.agent_git("branch", "-q", "reliability/2026-09-24", "origin/main")
    old = layout.agent_git("rev-parse", "reliability/2026-09-24").stdout.strip()
    _advance_main(layout)

    proc = layout.run(wrapper, "align_agent_gitdir")

    assert proc.returncode == 0, proc.stderr
    assert layout.agent_git("rev-parse", "reliability/2026-09-24").stdout.strip() == old


# --- 3. nothing the agent plants runs on the host or survives into a rung ---
def _plant(layout: Layout) -> Path:
    marker = layout.tmp / "PWNED"
    evil = layout.tmp / "evil-hooks"
    evil.mkdir()
    for hook in ("post-checkout", "reference-transaction", "pre-commit"):
        _hook = evil / hook
        _hook.write_text(f"#!/bin/sh\necho {hook} >> {marker}\n", encoding="utf-8")
        _hook.chmod(0o755)
    shutil.copytree(evil, layout.agent / "hooks", dirs_exist_ok=True)
    with (layout.agent / "config").open("a", encoding="utf-8") as fh:
        fh.write(f"[core]\n\thooksPath = {evil}\n")
    return marker


def test_planted_agent_config_is_live_for_the_agent_before_sanitize(layout):
    """Control: the vector works, so the next test's silence means something."""
    assert layout.run(WRAPPERS["reliability"], "align_agent_gitdir").returncode == 0
    marker = _plant(layout)
    layout.agent_git("checkout", "-q", "-b", "probe")
    assert marker.exists()


def test_host_git_never_reads_planted_agent_config(layout):
    wrapper = WRAPPERS["reliability"]
    assert layout.run(wrapper, "align_agent_gitdir").returncode == 0
    marker = _plant(layout)
    planted = layout.repo / "planted.git"
    shutil.copytree(layout.agent, planted)
    (layout.repo / ".git").write_text(f"gitdir: {planted}\n", encoding="utf-8")
    host = f'git --git-dir="$HOST_GITDIR" --work-tree="$REPO"'
    proc = layout.run(
        wrapper,
        f"{host} checkout -f -q main\n{host} reset --hard -q origin/main\n"
        f"{host} status --porcelain >/dev/null\n"
        "agent_git_ro rev-parse HEAD\nagent_git_ro log -1 --format=%ct HEAD\n"
        "align_agent_gitdir",
    )
    assert proc.returncode == 0, proc.stderr
    assert not marker.exists(), marker.read_text()


def test_sanitize_drops_planted_config_hooks_and_gitfile_before_the_next_rung(layout):
    wrapper = WRAPPERS["reliability"]
    assert layout.run(wrapper, "align_agent_gitdir").returncode == 0
    marker = _plant(layout)
    (layout.agent / "commondir").write_text(str(layout.tmp), encoding="utf-8")
    (layout.repo / ".git").write_text(f"gitdir: {layout.tmp / 'elsewhere'}\n", encoding="utf-8")
    assert layout.run(wrapper, "sanitize_agent_gitdir").returncode == 0
    assert (layout.repo / ".git").read_text() == f"gitdir: {layout.agent}\n"
    assert not (layout.agent / "hooks").exists()
    assert not (layout.agent / "commondir").exists()
    assert "hooksPath" not in (layout.agent / "config").read_text()
    layout.agent_git("checkout", "-q", "-b", "next-rung")
    (layout.repo / "g").write_text("g\n", encoding="utf-8")
    layout.agent_git("add", "g")
    layout.agent_git("commit", "-q", "-m", "g")
    assert not marker.exists(), marker.read_text()


def test_sanitize_never_writes_through_a_planted_symlink(layout):
    wrapper = WRAPPERS["reliability"]
    assert layout.run(wrapper, "align_agent_gitdir").returncode == 0
    victim = layout.host / "config"
    original = victim.read_bytes()
    (layout.agent / "config").unlink()
    (layout.agent / "config").symlink_to(victim)
    (layout.agent / "index").unlink()
    (layout.agent / "index").symlink_to(victim)
    assert layout.run(wrapper, "align_agent_gitdir").returncode == 0
    assert victim.read_bytes() == original
    assert not (layout.agent / "config").is_symlink()
    assert not (layout.agent / "index").is_symlink()


def test_a_symlinked_agent_gitdir_is_replaced_not_followed(layout):
    wrapper = WRAPPERS["reliability"]
    layout.agent.parent.mkdir(parents=True)
    layout.agent.symlink_to(layout.host)
    original = (layout.host / "config").read_bytes()
    assert layout.run(wrapper, "align_agent_gitdir").returncode == 0
    assert not layout.agent.is_symlink() and layout.agent.is_dir()
    assert (layout.host / "config").read_bytes() == original


# --- 4. host-side python helpers never discover the agent gitdir ------------
def test_prune_never_runs_git_in_a_gitfile_clone(tmp_path, monkeypatch):
    root = tmp_path / "radon-weekend"
    clone = root / "radon"
    clone.mkdir(parents=True)
    (clone / ".radon-weekend-runner").write_text("", encoding="utf-8")
    (clone / ".git").write_text(f"gitdir: {root / '.gitdirs-agent' / 'reliability.git'}\n")
    calls: list = []
    monkeypatch.setattr(weekend_prune, "_git", lambda c, *a: calls.append((c, a)))
    removable, refused = weekend_prune._worktrees(root, {}, 0.0)
    assert calls == [] and removable == []
    assert refused and "agent gitdir" in refused[0]["reason"], refused


def test_prune_protects_the_agent_gitdir_root(tmp_path):
    root = tmp_path / "radon-weekend"
    (root / ".gitdirs-agent").mkdir(parents=True)
    reason = weekend_prune.refusal_reason(root / ".gitdirs-agent", root=root,
                                          temp_root=Path("/nonexistent-temp"))
    assert reason is not None and "gitdir" in reason


SETUPS = [p for p in sorted((REPO / "scripts").glob("setup_*.sh"))
          if "HOST_GITDIR=" in p.read_text(encoding="utf-8")]


@pytest.mark.parametrize("setup", SETUPS, ids=lambda p: p.stem)
def test_setup_provisions_the_agent_gitdir_root(setup):
    text = setup.read_text(encoding="utf-8")
    assert 'AGENT_GITDIR_ROOT="$WEEKEND_ROOT/.gitdirs-agent"' in text
    assert 'mkdir -p "$AGENT_GITDIR_ROOT"' in text
    assert 'chmod 700 "$AGENT_GITDIR_ROOT"' in text
