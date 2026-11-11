"""The suite must never reach the developer's global git config.

A global `core.hooksPath` moves `git rev-parse --git-path hooks` out of a
test's tmp repo and onto a real directory in the developer's home. Tests that
install or overwrite a hook then write there: on 2026-11-10 a local run of
test_pre_push.py replaced `~/.git-hooks/pre-push` with a three-line stub that
refused every push from every other repository on the machine.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def test_global_and_system_git_config_are_blank_for_the_suite() -> None:
    assert os.environ.get("GIT_CONFIG_GLOBAL") == os.devnull
    assert os.environ.get("GIT_CONFIG_SYSTEM") == os.devnull


def test_a_tmp_repo_keeps_its_hooks_inside_itself(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    hooks = Path(_git(repo, "rev-parse", "--git-path", "hooks"))
    resolved = (hooks if hooks.is_absolute() else repo / hooks).resolve()
    assert resolved.is_relative_to(repo.resolve()), resolved
