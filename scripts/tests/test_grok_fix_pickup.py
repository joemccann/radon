#!/usr/bin/env python3
"""The Mac mini picks up grok's fix branches; the VPS holds no GitHub token.

The responder runs `grok --always-approve` over untrusted page text. Any
GitHub credential on that host can merge to main (push and merge need the
same permission), so the credential moves to the Mac mini and the VPS clone
becomes a plain git source fetched over ssh.

The branch content is still untrusted, so pickup is gated:
  * only `fix/<slug>` refs, no refspec or path tricks;
  * nothing touching `.github/` — a PR-triggered workflow runs on PR head
    and would execute attacker-authored CI on this repo;
  * the branch must descend from origin/main, and stay within a commit cap;
  * pickup pushes the branch and opens a PR. It never merges.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import grok_fix_pickup as pickup  # noqa: E402


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60
    )
    return proc


def _commit(repo: Path, relpath: str, body: str, message: str) -> None:
    target = repo / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    assert _git(repo, "add", relpath).returncode == 0
    assert _git(repo, "commit", "-m", message).returncode == 0


@pytest.fixture
def world(tmp_path: Path) -> dict:
    """origin (bare) + vps clone (grok's) + mini clone (pickup runs here)."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    assert _git(origin, "init", "--bare", "-b", "main").returncode == 0

    seed = tmp_path / "seed"
    seed.mkdir()
    assert _git(seed, "init", "-b", "main").returncode == 0
    _git(seed, "config", "user.name", "t")
    _git(seed, "config", "user.email", "t@example.invalid")
    _commit(seed, "app.py", "x = 1\n", "init")
    assert _git(seed, "remote", "add", "origin", str(origin)).returncode == 0
    assert _git(seed, "push", "-q", "origin", "main").returncode == 0

    vps = tmp_path / "vps"
    assert _git(tmp_path, "clone", "-q", str(origin), str(vps)).returncode == 0
    _git(vps, "config", "user.name", "grok")
    _git(vps, "config", "user.email", "grok@example.invalid")

    mini = tmp_path / "mini"
    assert _git(tmp_path, "clone", "-q", str(origin), str(mini)).returncode == 0
    _git(mini, "config", "user.name", "mini")
    _git(mini, "config", "user.email", "mini@example.invalid")
    return {"origin": origin, "vps": vps, "mini": mini}


def _vps_branch(world: dict, name: str, relpath: str = "app.py") -> None:
    vps = world["vps"]
    assert _git(vps, "checkout", "-q", "-b", name).returncode == 0
    _commit(vps, relpath, "x = 2\n", f"fix: {name}")


class _FakeEnsurePr:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return {"action": "created", "url": "https://github.com/x/y/pull/1"}


def _run(world: dict, ensure=None, **kwargs):
    return pickup.pickup_once(
        world["mini"],
        source=str(world["vps"]),
        ensure_pr=ensure or _FakeEnsurePr(),
        **kwargs,
    )


class TestHappyPath:
    def test_pushes_the_branch_and_opens_one_pr(self, world):
        _vps_branch(world, "fix/relay-restart")
        ensure = _FakeEnsurePr()

        results = _run(world, ensure=ensure)

        assert [r["branch"] for r in results] == ["fix/relay-restart"]
        assert [r["action"] for r in results] == ["picked_up"]
        listed = _git(world["origin"], "for-each-ref", "--format=%(refname)")
        assert "refs/heads/fix/relay-restart" in listed.stdout
        assert len(ensure.calls) == 1
        assert ensure.calls[0]["head"] == "fix/relay-restart"

    def test_second_run_reconciles_the_pr_without_another_push(self, world):
        _vps_branch(world, "fix/relay-restart")
        _run(world)
        ensure = _FakeEnsurePr()

        results = _run(world, ensure=ensure)

        assert [r["action"] for r in results] == ["picked_up"]
        assert len(ensure.calls) == 1
        assert results[0]["url"] == "https://github.com/x/y/pull/1"


class TestRefusals:
    def test_refuses_a_branch_touching_dot_github(self, world):
        _vps_branch(world, "fix/ci-tweak", relpath=".github/workflows/evil.yml")
        ensure = _FakeEnsurePr()

        results = _run(world, ensure=ensure)

        assert [r["action"] for r in results] == ["refused"]
        assert "github" in results[0]["reason"].lower()
        listed = _git(world["origin"], "for-each-ref", "--format=%(refname)")
        assert "fix/ci-tweak" not in listed.stdout
        assert ensure.calls == []

    def test_ignores_refs_outside_fix(self, world):
        vps = world["vps"]
        assert _git(vps, "checkout", "-q", "-b", "main-ish").returncode == 0
        _commit(vps, "app.py", "x = 3\n", "not a fix branch")

        assert _run(world) == []

    def test_refuses_a_branch_that_does_not_descend_from_main(self, world):
        vps = world["vps"]
        assert _git(vps, "checkout", "-q", "--orphan", "fix/orphan").returncode == 0
        _commit(vps, "app.py", "x = 9\n", "orphan root")
        ensure = _FakeEnsurePr()

        results = _run(world, ensure=ensure)

        assert [r["action"] for r in results] == ["refused"]
        assert "descend" in results[0]["reason"].lower()
        assert ensure.calls == []

    def test_refuses_a_branch_over_the_commit_cap(self, world):
        _vps_branch(world, "fix/too-many")
        for i in range(3):
            _commit(world["vps"], "app.py", f"x = {i}\n", f"more {i}")
        ensure = _FakeEnsurePr()

        results = _run(world, ensure=ensure, max_commits=2)

        assert [r["action"] for r in results] == ["refused"]
        assert "commit" in results[0]["reason"].lower()
        assert ensure.calls == []


class TestNeverMerges:
    def test_module_issues_no_merge_command(self):
        source = (_SCRIPTS_DIR / "grok_fix_pickup.py").read_text(encoding="utf-8")
        assert "pr merge" not in source
        assert "/merge" not in source

    def test_branch_name_validation_rejects_tricks(self):
        for bad in (
            "fix/../../etc/passwd",
            "fix/a b",
            "fix/--upload-pack=x",
            "fix/",
            "main",
            "refs/heads/fix/x",
            "fix/x\nmain",
        ):
            assert not pickup.is_pickup_branch(bad), bad
        assert pickup.is_pickup_branch("fix/relay-restart")
        assert pickup.is_pickup_branch("fix/leap_reports.502")


def test_pr_failure_after_push_is_retried_without_new_commit(world):
    _vps_branch(world, "fix/retry-pr")
    def unavailable(**kwargs):
        raise pickup.ir_ensure_pr.IrEnsurePrError("temporary GitHub failure")
    with pytest.raises(pickup.ir_ensure_pr.IrEnsurePrError):
        _run(world, ensure=unavailable)
    before = _git(world["origin"], "rev-parse", "refs/heads/fix/retry-pr").stdout
    ensure = _FakeEnsurePr()
    result = _run(world, ensure=ensure)
    assert len(ensure.calls) == 1
    assert result[0]["url"] == "https://github.com/x/y/pull/1"
    assert _git(world["origin"], "rev-parse", "refs/heads/fix/retry-pr").stdout == before


def test_changed_origin_head_is_refused_without_push_or_pr(world):
    _vps_branch(world, "fix/changed")
    _run(world)
    _commit(world["vps"], "app.py", "x = 3\n", "new unreviewed head")
    ensure = _FakeEnsurePr()
    result = _run(world, ensure=ensure)
    assert result[0]["action"] == "refused"
    assert "head" in result[0]["reason"]
    assert ensure.calls == []


@pytest.mark.parametrize("result", [None, {}, {"action": "created"}])
def test_pickup_requires_a_confirmed_pr_url(world, result):
    _vps_branch(world, "fix/no-pr-proof")
    with pytest.raises(pickup.PickupError, match="PR URL"):
        _run(world, ensure=lambda **kwargs: result)


def test_default_pickup_hook_requests_terminal_dispositions(monkeypatch):
    calls = []
    monkeypatch.setattr(pickup.ir_ensure_pr, "ensure_pr", lambda **kwargs: calls.append(kwargs) or {"url": "https://github.com/x/y/pull/1"})
    pickup._ensure_pr_default(head="fix/example")
    assert calls[0]["include_terminal"] is True
