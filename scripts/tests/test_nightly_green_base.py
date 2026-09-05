"""REL-187 (R-519): the nightly loops execute a CI-green SHA, never the raw tip.

A loop that fires minutes after a red push spent its whole cycle against a
tree CI had already rejected. Stale-but-green beats fresh-but-red; GitHub
being unreachable keeps the current tip rather than failing the run.
"""
from __future__ import annotations

import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import nightly_green_base as base

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
WRAPPERS = (
    SCRIPTS / "reliability_weekend.sh",
    SCRIPTS / "testing_weekend.sh",
    SCRIPTS / "ci_performance_nightly.sh",
    SCRIPTS / "documentation_nightly.sh",
    SCRIPTS / "security_nightly.sh",
)


class TestResolve:
    def test_picks_the_newest_ancestor(self):
        assert base.resolve("HEAD", ["red", "green", "older"], lambda s, h: s != "red") == "green"

    def test_no_ancestor_is_empty(self):
        assert base.resolve("HEAD", ["a", "b"], lambda s, h: False) == ""

    def test_no_candidates_is_empty(self):
        assert base.resolve("HEAD", [], lambda s, h: True) == ""

    def test_blank_shas_are_skipped(self):
        assert base.resolve("HEAD", ["", "good"], lambda s, h: True) == "good"


class TestCli:
    def _gh(self, tmp_path: Path, body: str) -> Path:
        script = tmp_path / "fake-gh"
        script.write_text("#!/usr/bin/env python3\nimport sys\n" + body, encoding="utf-8")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        return script

    def _run(self, gh: Path, repo_dir: Path, head: str = "HEAD"):
        return subprocess.run(
            [
                sys.executable, str(SCRIPTS / "nightly_green_base.py"),
                "--repo", "joemccann/radon", "--repo-dir", str(repo_dir),
                "--head", head, "--gh-bin", str(gh), "--timeout", "10",
            ],
            capture_output=True, text=True, timeout=60,
        )

    @pytest.fixture()
    def git_repo(self, tmp_path):
        d = tmp_path / "repo"
        d.mkdir()
        run = lambda *a: subprocess.run(["git", "-C", str(d), *a], check=True,
                                        capture_output=True, text=True)
        run("init", "-q")
        run("config", "user.email", "t@t")
        run("config", "user.name", "t")
        shas = []
        for i in range(3):
            (d / f"f{i}").write_text(str(i))
            run("add", f"f{i}")
            run("commit", "-qm", f"c{i}")
            shas.append(
                subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD"],
                               capture_output=True, text=True, check=True).stdout.strip()
            )
        return d, shas

    def test_an_ancestor_green_sha_is_printed(self, tmp_path, git_repo):
        repo_dir, shas = git_repo
        gh = self._gh(tmp_path, f"print({shas[1]!r})\n")
        proc = self._run(gh, repo_dir)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == shas[1]

    def test_a_tip_that_is_already_green_is_printed(self, tmp_path, git_repo):
        repo_dir, shas = git_repo
        gh = self._gh(tmp_path, f"print({shas[2]!r})\n")
        assert self._run(gh, repo_dir).stdout.strip() == shas[2]

    def test_an_unreachable_github_keeps_the_tip(self, tmp_path, git_repo):
        repo_dir, _ = git_repo
        gh = self._gh(tmp_path, "sys.exit(1)\n")
        proc = self._run(gh, repo_dir)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == ""
        assert "keeping the current tip" in proc.stderr

    def test_a_green_sha_from_another_branch_is_not_used(self, tmp_path, git_repo):
        repo_dir, _ = git_repo
        gh = self._gh(tmp_path, "print('0' * 40)\n")
        proc = self._run(gh, repo_dir)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == ""


class TestWrapperWiring:
    @pytest.mark.parametrize("wrapper", WRAPPERS, ids=lambda p: p.name)
    def test_ground_truth_pins_a_green_sha(self, wrapper: Path):
        text = wrapper.read_text(encoding="utf-8")
        start = text.index("ground_truth() {")
        end = text.index("\n}", start)
        body = "\n".join(
            l for l in text[start:end].splitlines() if not l.lstrip().startswith("#")
        )
        assert "resolve_green_main_sha" in body, wrapper.name
        assert body.index("origin/main") < body.index("resolve_green_main_sha"), wrapper.name

    @pytest.mark.parametrize("wrapper", WRAPPERS, ids=lambda p: p.name)
    def test_the_resolver_runs_through_the_isolated_pipe(self, wrapper: Path):
        text = wrapper.read_text(encoding="utf-8")
        start = text.index("resolve_green_main_sha() {")
        end = text.index("\n}", start)
        body = text[start:end]
        assert "origin/main:scripts/nightly_green_base.py" in body, wrapper.name
        assert "/usr/bin/python3 -I -" in body, wrapper.name
        assert "$TIMEOUT_BIN" in body, wrapper.name

    @pytest.mark.parametrize("wrapper", WRAPPERS, ids=lambda p: p.name)
    def test_an_unresolvable_green_sha_is_never_fatal(self, wrapper: Path):
        text = wrapper.read_text(encoding="utf-8")
        start = text.index("resolve_green_main_sha() {")
        end = text.index("\n}", start)
        assert "|| true" in text[start:end], wrapper.name


SENTINEL = "f" * 40
TIP = "1" * 40

FAKE_GIT = f"""#!/bin/bash
printf '%s\\n' "$*" >> "$GIT_LOG"
case "$*" in
  *"rev-parse origin/main") echo "{TIP}" ;;
  *"rev-parse --verify --quiet"*) exit 0 ;;
esac
exit 0
"""


def _extract_ground_truth(wrapper: Path) -> str:
    text = wrapper.read_text(encoding="utf-8")
    start = text.index("ground_truth() {")
    end = text.index("\n}", start) + 2
    return text[start:end]


def _run_ground_truth(tmp_path: Path, snippet: str) -> list[str]:
    """Execute a ground_truth snippet with a fake git + fake resolver.

    Returns the argv lines the fake git recorded, in call order.
    """
    fakes = tmp_path / "fakes"
    fakes.mkdir(exist_ok=True)
    git = fakes / "git"
    git.write_text(FAKE_GIT, encoding="utf-8")
    git.chmod(0o755)
    git_log = tmp_path / "git.log"
    git_log.write_text("", encoding="utf-8")
    driver = "\n".join(
        [
            "set -eo pipefail",
            "fetch_origin_with_retry() { :; }",
            f"resolve_green_main_sha() {{ echo {SENTINEL}; }}",
            snippet,
            "ground_truth",
        ]
    )
    env = {
        "PATH": f"{fakes}:/usr/bin:/bin",
        "GIT_LOG": str(git_log),
        "REPO": str(tmp_path),
        "HOME": str(tmp_path),
    }
    proc = subprocess.run(
        ["bash", "-c", driver], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return git_log.read_text(encoding="utf-8").splitlines()


def _ref_moves(git_lines: list[str]) -> list[str]:
    """Targets of every ref-moving git call (checkout / reset --hard), in order."""
    moves = []
    for line in git_lines:
        argv = line.split()
        if "checkout" in argv or ("reset" in argv and "--hard" in argv):
            moves.append(argv[-1])
    return moves


class TestWrapperBehaviour:
    """REL-187: execute the branch-selection snippet — the resolver's answer must
    be the ref the tree actually ends up on, not just a string in the source."""

    @pytest.mark.parametrize("wrapper", WRAPPERS, ids=lambda p: p.name)
    def test_the_tree_ends_on_the_resolver_sha(self, wrapper: Path, tmp_path):
        lines = _run_ground_truth(tmp_path, _extract_ground_truth(wrapper))
        moves = _ref_moves(lines)
        assert moves, "no ref-moving git command ran"
        assert moves[-1] == SENTINEL, f"final ref is {moves[-1]!r}, not the green SHA"
        after_sentinel = moves[moves.index(SENTINEL) + 1:]
        assert "origin/main" not in after_sentinel, "checked out origin/main after resolving"

    def test_a_rel187_defective_wrapper_fails_the_assertion(self, tmp_path):
        """Red proof: a wrapper that computes the green SHA then resets to
        origin/main anyway (the REL-187 defect verbatim) must be caught."""
        defective = (
            "ground_truth() {\n"
            "  fetch_origin_with_retry\n"
            "  local green_sha\n"
            '  green_sha="$(resolve_green_main_sha)"\n'
            "  git checkout -f --quiet main\n"
            "  git reset --hard --quiet origin/main\n"
            "}\n"
        )
        moves = _ref_moves(_run_ground_truth(tmp_path, defective))
        assert moves[-1] != SENTINEL  # the behavioural check above would fail this wrapper
