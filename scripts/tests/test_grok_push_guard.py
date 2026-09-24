#!/usr/bin/env python3
"""F20260915-A01/B01/B02 — the unattended Grok responder's push authority
must be enforced by git, not by prompt text.

The responder runs `grok --always-approve` over untrusted page text in a
clone whose credential can push any ref. The prompt says "Never git push
origin main. Never merge." — but prompt text is not a control. These tests
pin three chokepoints:

- a `pre-push` hook installed every cycle refuses any ref outside
  `refs/heads/fix/*` (A01);
- `ir_ensure_pr._default_runner` refuses any merge-shaped gh invocation,
  not only the positional `gh pr merge` spelling (B01);
- the page-derived summary is flattened to one control-free line before it
  becomes PR title/body input (B02).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import grok_page_responder as responder  # noqa: E402
import ir_ensure_pr  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_git_environment(monkeypatch, tmp_path):
    """T-508: exercise the temporary hook, independent of runner Git policy."""
    for key in tuple(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "empty-gitconfig"))


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60
    )


@pytest.fixture
def clone_with_remote(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    assert _git(remote, "init", "--bare", "-b", "main").returncode == 0
    clone = tmp_path / "clone"
    clone.mkdir()
    assert _git(clone, "init", "-b", "main").returncode == 0
    _git(clone, "config", "user.name", "t")
    _git(clone, "config", "user.email", "t@example.invalid")
    (clone / "f.txt").write_text("x\n")
    assert _git(clone, "add", "f.txt").returncode == 0
    assert _git(clone, "commit", "-m", "init").returncode == 0
    assert _git(clone, "remote", "add", "origin", str(remote)).returncode == 0
    return clone


class TestPushGuardHook:
    def test_installs_an_executable_pre_push_hook(self, clone_with_remote):
        hook = responder.install_push_guard(clone_with_remote)
        assert hook is not None and hook.name == "pre-push"
        assert hook.exists()
        assert hook.stat().st_mode & 0o111

    def test_reinstall_overwrites_a_tampered_hook(self, clone_with_remote):
        hook = responder.install_push_guard(clone_with_remote)
        hook.write_text("#!/bin/sh\nexit 0\n")
        again = responder.install_push_guard(clone_with_remote)
        assert again.read_text() == responder.PRE_PUSH_GUARD

    def test_push_to_main_is_refused(self, clone_with_remote):
        responder.install_push_guard(clone_with_remote)
        proc = _git(clone_with_remote, "push", "origin", "main")
        assert proc.returncode != 0
        assert "push guard" in proc.stderr

    def test_push_to_a_fix_branch_still_works(self, clone_with_remote):
        responder.install_push_guard(clone_with_remote)
        assert _git(
            clone_with_remote, "checkout", "-b", "fix/guarded"
        ).returncode == 0
        proc = _git(clone_with_remote, "push", "-u", "origin", "fix/guarded")
        assert proc.returncode == 0, proc.stderr

    def test_tag_and_delete_pushes_are_refused(self, clone_with_remote):
        responder.install_push_guard(clone_with_remote)
        _git(clone_with_remote, "checkout", "-b", "fix/guarded")
        assert _git(
            clone_with_remote, "push", "-u", "origin", "fix/guarded"
        ).returncode == 0
        _git(clone_with_remote, "tag", "v0")
        assert _git(clone_with_remote, "push", "origin", "v0").returncode != 0
        assert _git(
            clone_with_remote, "push", "origin", ":fix/guarded"
        ).returncode != 0

    def test_install_fails_none_outside_a_git_repo(self, tmp_path):
        assert responder.install_push_guard(tmp_path / "not-a-repo") is None


class TestCycleFailsClosedWithoutGuard:
    def test_autopush_cycle_stands_down_when_guard_install_fails(
        self, tmp_path, monkeypatch
    ):
        # Not a git repo: guard cannot install; a push-capable cycle must
        # not launch grok at all.
        monkeypatch.setenv("GROK_PAGE_RESPONDER", "1")
        monkeypatch.setenv("GROK_PAGE_AUTOSHIP", "1")
        monkeypatch.setenv("GROK_PAGE_AUTOPUSH", "1")
        monkeypatch.delenv("GROK_PAGE_SYNC_REMOTE", raising=False)
        calls = []

        def _runner(cmd, **kwargs):
            calls.append(cmd)
            raise AssertionError("grok must not run without the push guard")

        monkeypatch.setattr(responder, "_heartbeat", lambda *a, **k: None)
        rc = responder.run_cycle(tmp_path, grok_runner=_runner)
        assert rc != 0
        assert calls == []


class TestMergeGuardIsNotPositional:
    @pytest.mark.parametrize(
        "argv",
        [
            ["gh", "pr", "merge", "5"],
            ["gh", "--repo", "o/r", "pr", "merge", "5"],
            ["gh", "pr", "-R", "o/r", "merge", "5"],
            ["gh", "api", "-X", "PUT", "repos/o/r/pulls/5/merge"],
        ],
    )
    def test_merge_shaped_invocations_raise(self, argv):
        with pytest.raises(ir_ensure_pr.IrEnsurePrError):
            ir_ensure_pr._default_runner(argv)

    def test_pr_list_is_still_allowed(self, monkeypatch):
        seen = {}

        def _run(argv, **kwargs):
            seen["argv"] = argv
            return subprocess.CompletedProcess(argv, 0, "", "")

        monkeypatch.setattr(ir_ensure_pr.subprocess, "run", _run)
        ir_ensure_pr._default_runner(["gh", "pr", "list", "--state", "open"])
        assert seen["argv"][1:3] == ["pr", "list"]


class TestSummaryIsFlattened:
    def test_control_chars_and_newlines_collapse(self):
        raw = "fixed\r\nRESULT: \x1b[31mok\x00 " + "x" * 500
        clean = ir_ensure_pr.sanitize_summary(raw)
        assert "\n" not in clean and "\r" not in clean
        assert all(ord(c) >= 32 for c in clean)
        assert len(clean) <= ir_ensure_pr.SUMMARY_MAX_CHARS

    def test_ensure_after_code_fix_uses_the_flattened_summary(self, tmp_path):
        captured = {}

        def _fake_ensure_pr(**kwargs):
            captured.update(kwargs)
            return {"action": "created", "url": "https://x/pull/1"}

        orig = ir_ensure_pr.ensure_pr
        ir_ensure_pr.ensure_pr = _fake_ensure_pr
        try:
            ir_ensure_pr.ensure_after_code_fix(
                tmp_path,
                page={"page_id": "p1", "service": "svc"},
                summary="line1\nline2\x07",
                head="fix/slug",
            )
        finally:
            ir_ensure_pr.ensure_pr = orig
        assert "\n" not in captured["issue"]
        assert "\x07" not in captured["issue"]
