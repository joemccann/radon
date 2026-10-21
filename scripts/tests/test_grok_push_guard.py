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
from types import SimpleNamespace

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


def test_controller_git_drops_credentials_and_repo_hooks(tmp_path, monkeypatch):
    """DS-2026-10-21-17: controller sync inherited the process environment,
    so a post-checkout hook the agent wrote could read Turso and Pushover."""
    monkeypatch.setenv("GROK_PAGE_SYNC_REMOTE", "1")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "SENTINELNOTAREALTOKEN")
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["env"] = kwargs.get("env")
        return SimpleNamespace(returncode=0, stdout=" M dirty\n")

    monkeypatch.setattr(responder.subprocess, "run", fake_run)
    assert responder.sync_remote_clone(tmp_path) == "dirty"
    assert "core.hooksPath=/dev/null" in seen["argv"]
    assert "TURSO_AUTH_TOKEN" not in (seen["env"] or {})


def test_controller_checkout_does_not_run_a_repo_hook(tmp_path, monkeypatch):
    monkeypatch.setenv("GROK_PAGE_SYNC_REMOTE", "1")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "SENTINELNOTAREALTOKEN")
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    clone = tmp_path / "clone"
    assert _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote)).returncode == 0
    assert _git(tmp_path, "clone", "-q", str(remote), str(seed)).returncode == 0
    _git(seed, "config", "user.email", "t@example.invalid")
    _git(seed, "config", "user.name", "t")
    (seed / "a.txt").write_text("1\n")
    assert _git(seed, "add", "a.txt").returncode == 0
    assert _git(seed, "commit", "-qm", "base").returncode == 0
    assert _git(seed, "push", "-q", str(remote), "HEAD:main").returncode == 0
    assert _git(tmp_path, "clone", "-q", str(remote), str(clone)).returncode == 0
    _git(clone, "config", "user.email", "t@example.invalid")
    _git(clone, "config", "user.name", "t")
    _git(clone, "checkout", "-qb", "fix/parked")
    hook = clone / ".git" / "hooks" / "post-checkout"
    hook.write_text('#!/bin/sh\nprintf "%s" "$TURSO_AUTH_TOKEN" > "$(dirname "$0")/hook-ran"\n')
    hook.chmod(0o755)
    monkeypatch.setattr(responder, "PUBLIC_REPO_URL", str(remote))
    assert responder.sync_remote_clone(clone) == "synced"
    assert not (clone / ".git" / "hooks" / "hook-ran").exists()


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

    def test_ensure_after_code_fix_uses_the_structured_commit(self, tmp_path):
        captured = {}
        commit = (
            "fix: ledger timeout\n\n"
            "## What broke\n"
            "The oneshot exited Result=exit-code and paged P1 about itself.\n"
            "Page p1 first seen 2026-09-26T17:15:00Z. Error: TimeoutError.\n\n"
            "## Root cause\n"
            "A Turso read timeout escaped run_cycle and failed the unit.\n\n"
            "## What changed\n"
            "- scripts/grok_page_responder.py: exit 0 on ledger timeout.\n\n"
            "## How it was verified\n"
            "Focused pytest for the timeout path passed locally.\n\n"
            "## Risk and rollback\n"
            "The matcher is broad. Rollback by reverting this commit.\n\n"
            "## Still open\n"
            "Whether a duplicate grok run can follow a complete_page timeout.\n"
        )

        def _fake_ensure_pr(**kwargs):
            captured.update(kwargs)
            return {"action": "created", "url": "https://x/pull/1"}

        def runner(argv, **_kwargs):
            if list(argv)[:2] == ["git", "log"]:
                return SimpleNamespace(returncode=0, stdout=commit, stderr="")
            return SimpleNamespace(returncode=1, stdout="", stderr="no")

        orig = ir_ensure_pr.ensure_pr
        ir_ensure_pr.ensure_pr = _fake_ensure_pr
        try:
            ir_ensure_pr.ensure_after_code_fix(
                tmp_path,
                page={"page_id": "p1", "service": "svc"},
                summary="line1\nline2\x07",
                head="fix/slug",
                runner=runner,
            )
        finally:
            ir_ensure_pr.ensure_pr = orig
        assert "\n" not in captured["issue"]
        assert "\x07" not in captured["issue"]
        # The PR body is the repo template rendered from the IR commit.
        assert "## Summary" in captured["body"]
        assert "The oneshot exited Result=exit-code" in captured["body"]
        assert "## What broke" not in captured["body"]
        assert captured["update_existing"] is True

    def test_ensure_after_code_fix_refuses_a_placeholder_commit(self, tmp_path):
        def runner(argv, **_kwargs):
            if list(argv)[:2] == ["git", "log"]:
                return SimpleNamespace(
                    returncode=0,
                    stdout="grok incident fix on fix/slug\n",
                    stderr="",
                )
            return SimpleNamespace(returncode=1, stdout="", stderr="no")

        with pytest.raises(ir_ensure_pr.IrEnsurePrError, match="IR PR description"):
            ir_ensure_pr.ensure_after_code_fix(
                tmp_path,
                page={"page_id": "p1", "service": "svc"},
                summary="grok incident fix on fix/slug",
                head="fix/slug",
                runner=runner,
            )
