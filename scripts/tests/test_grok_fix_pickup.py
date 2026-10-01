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

import os
import plistlib
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import grok_fix_pickup as pickup  # noqa: E402


@pytest.fixture(autouse=True)
def _autopush_on(monkeypatch):
    """Pickup fails closed without it; the off case is tested explicitly."""
    monkeypatch.setenv("GROK_PAGE_AUTOPUSH", "1")


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60
    )
    return proc


def _valid_ir_message(name: str) -> str:
    return (
        f"fix: repair {name} timeout\n\n"
        "## What broke\n"
        f"The {name} unit failed with Result=exit-code and paged P1. "
        "Page abcdef0123456789abcdef0123456789 first seen 2026-09-26T17:15:00Z. "
        "Error excerpt: TimeoutError on the ledger read.\n\n"
        "## Root cause\n"
        "A Turso read timeout escaped run_cycle and marked the oneshot failed.\n\n"
        "## What changed\n"
        "- app.py: treat the ledger timeout as non-fatal and exit 0.\n\n"
        "## How it was verified\n"
        "Focused pytest for the timeout path passed locally.\n\n"
        "## Risk and rollback\n"
        "The timeout matcher is broad. Rollback by reverting this commit.\n\n"
        "## Still open\n"
        "Whether a duplicate grok run can follow a complete_page timeout.\n"
    )


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


def _vps_branch(
    world: dict,
    name: str,
    relpath: str = "app.py",
    message: str | None = None,
) -> None:
    vps = world["vps"]
    assert _git(vps, "checkout", "-q", "-b", name).returncode == 0
    _commit(vps, relpath, "x = 2\n", message or _valid_ir_message(name))


class _FakeEnsurePr:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return {"action": "created", "url": "https://github.com/x/y/pull/1"}


def _run(world: dict, ensure=None, **kwargs):
    kwargs.setdefault("list_ci_urls", lambda _head: [])
    kwargs.setdefault("lookup_page", lambda **_kw: None)
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
        assert ensure.calls[0]["title"].startswith("IR: ")
        assert "Result=exit-code" in ensure.calls[0]["issue"]
        body = ensure.calls[0]["body"]
        for heading in (
            "What broke",
            "Root cause",
            "What changed",
            "How it was verified",
            "Risk and rollback",
            "Still open",
        ):
            assert f"## {heading}" in body
        assert "grok incident fix on" not in body
        assert ensure.calls[0]["update_existing"] is True

    def test_second_run_reconciles_the_pr_without_another_push(self, world):
        _vps_branch(world, "fix/relay-restart")
        _run(world)
        ensure = _FakeEnsurePr()

        results = _run(world, ensure=ensure)

        assert [r["action"] for r in results] == ["picked_up"]
        assert len(ensure.calls) == 1
        assert results[0]["url"] == "https://github.com/x/y/pull/1"

    def test_enriches_body_from_page_row_and_ci_urls(self, world):
        _vps_branch(world, "fix/relay-restart")
        ensure = _FakeEnsurePr()
        page = {
            "page_id": "abcdef0123456789abcdef0123456789",
            "severity": "P1",
            "paged_at": "2026-09-26T17:15:04Z",
            "result": "code_fix: ledger timeout no longer fails the oneshot",
        }
        results = _run(
            world,
            ensure=ensure,
            lookup_page=lambda **_kw: page,
            list_ci_urls=lambda _head: [
                "https://github.com/joemccann/radon/actions/runs/36361801938"
            ],
        )
        assert results[0]["action"] == "picked_up"
        body = ensure.calls[0]["body"]
        assert "abcdef0123456789abcdef0123456789" in body
        assert "36361801938" in body
        assert "P1" in body
        assert ensure.calls[0]["incident_id"] == page["page_id"]


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


class TestDescriptionGate:
    def test_placeholder_body_is_refused_without_a_pr(self, world):
        _vps_branch(
            world,
            "fix/x",
            message="grok incident fix on fix/x",
        )
        ensure = _FakeEnsurePr()
        alerts: list[tuple[str, str]] = []
        results = _run(
            world,
            ensure=ensure,
            alerter=lambda branch, reason: alerts.append((branch, reason)),
        )
        assert [r["action"] for r in results] == ["refused"]
        assert "IR description" in results[0]["reason"]
        assert ensure.calls == []
        listed = _git(world["origin"], "for-each-ref", "--format=%(refname)")
        assert "fix/x" not in listed.stdout
        assert alerts and alerts[0][0] == "fix/x"

    def test_empty_commit_body_is_refused(self, world):
        _vps_branch(world, "fix/empty", message="fix: empty")
        ensure = _FakeEnsurePr()
        results = _run(world, ensure=ensure)
        assert results[0]["action"] == "refused"
        assert ensure.calls == []

    def test_current_bad_body_is_rejected(self, world):
        _vps_branch(
            world,
            "fix/grok-page-ledger-timeout",
            message="grok incident fix on fix/grok-page-ledger-timeout",
        )
        ensure = _FakeEnsurePr()
        results = _run(world, ensure=ensure)
        assert results[0]["action"] == "refused"
        assert "placeholder" in results[0]["reason"].lower() or "missing" in results[0]["reason"].lower()
        assert ensure.calls == []


def test_main_exits_nonzero_when_a_description_is_invalid(world, monkeypatch):
    monkeypatch.setattr(
        pickup,
        "pickup_once",
        lambda *_a, **_k: [{
            "branch": "fix/x",
            "action": "refused",
            "reason": "IR description: missing sections: What broke",
        }],
    )
    assert pickup.main(["--repo", str(world["mini"]), "--source", str(world["vps"])]) == 1


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


PLIST = _SCRIPTS_DIR.parent / "config" / "com.radon.grok-fix-pickup.plist"


def _pickup_script(clone: Path | str = "/tmp/radon-grok-pickup") -> str:
    plist = plistlib.loads(PLIST.read_bytes())
    argv = plist["ProgramArguments"]
    assert argv[:2] == ["/bin/bash", "-c"], argv
    return argv[2].replace("__PICKUP_REPO__", str(clone)).replace(
        "__HOME__", str(Path(clone).parent)
    )


def _executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class TestPickupPlistSelfRefresh:
    def test_plist_parses_and_bash_n_accepts_the_substituted_script(self) -> None:
        plist = plistlib.loads(PLIST.read_bytes())
        assert plist["Label"] == "com.radon.grok-fix-pickup"
        script = _pickup_script()
        checked = subprocess.run(
            ["/bin/bash", "-n", "-c", script],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert checked.returncode == 0, checked.stderr

    def test_refresh_precedes_python_and_uses_plain_git_c(self) -> None:
        script = _pickup_script()
        assert "GIT_CONFIG_COUNT=2" in script
        assert "core.hooksPath" in script and "core.fsmonitor" in script
        assert "index.lock" in script and "HEAD.lock" in script
        assert "timeout" in script and "gtimeout" in script
        assert "-k 15 180" in script
        assert "for i in 1 2 3" in script
        assert "exit 70" in script
        assert "api.pushover.net" in script
        assert script.index("fetch") < script.index("checkout") < script.index(
            "reset --hard"
        )
        assert script.index("reset --hard") < script.index(
            "exec /usr/bin/env python3.13"
        )
        assert "grok_fix_pickup.py" in script
        assert "--repo" in script
        assert 'git -C "$C"' in script or 'git -C "$C" ' in script
        assert ".gitdirs/" not in script
        assert "--git-dir=" not in script
        assert "branch -D" not in script and "branch -d" not in script

    @pytest.mark.parametrize("failed_step", ["fetch", "checkout", "reset"])
    def test_failed_refresh_pages_and_skips_python(
        self, failed_step: str, tmp_path: Path
    ) -> None:
        clone = tmp_path / "clone"
        (clone / "scripts").mkdir(parents=True)
        script = _pickup_script(clone)
        ran = tmp_path / "python-ran"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _executable(
            bin_dir / "git",
            "#!/bin/sh\n"
            f'for arg do [ "$arg" = "{failed_step}" ] && exit 1; done\n'
            "exit 0\n",
        )
        _executable(
            bin_dir / "timeout",
            "#!/bin/sh\nshift 3\nexec \"$@\"\n",
        )
        _executable(
            bin_dir / "python3.13",
            "#!/bin/sh\n"
            f'touch "{ran}"\n'
            "exit 0\n",
        )
        (tmp_path / ".env").write_text(
            "PUSHOVER_USER=test-user\nPUSHOVER_TOKEN=test-token\n",
            encoding="utf-8",
        )
        curl_log = tmp_path / "curl.log"
        _executable(
            tmp_path / "usr-curl",
            "#!/bin/sh\n"
            f'printf "%s\\n" "$*" >> "{curl_log}"\n'
            "exit 0\n",
        )
        # The launcher calls /usr/bin/curl; rewrite that path in a copy.
        script = script.replace("/usr/bin/curl", str(tmp_path / "usr-curl"))
        proc = subprocess.run(
            ["/bin/bash", "-c", script],
            env={
                "PATH": f"{bin_dir}:/usr/bin:/bin",
                "HOME": str(tmp_path),
                "RADON_LAUNCHD_FETCH_PAUSE_SECS": "0",
            },
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 70, proc.stdout + proc.stderr
        assert not ran.exists()
        assert curl_log.exists()
        assert "pushover.net" in curl_log.read_text(encoding="utf-8")

    def test_successful_refresh_execs_python_after_fetch_checkout_reset(
        self, tmp_path: Path
    ) -> None:
        clone = tmp_path / "clone"
        (clone / "scripts").mkdir(parents=True)
        script = _pickup_script(clone)
        log = tmp_path / "calls.log"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _executable(
            bin_dir / "git",
            "#!/bin/sh\n"
            f'printf "%s\\n" "git $*" >> "{log}"\n'
            "exit 0\n",
        )
        _executable(
            bin_dir / "timeout",
            "#!/bin/sh\n"
            f'printf "%s\\n" "timeout $*" >> "{log}"\n'
            "shift 3\n"
            'exec "$@"\n',
        )
        _executable(
            bin_dir / "python3.13",
            "#!/bin/sh\n"
            f'printf "%s\\n" "python $*" >> "{log}"\n'
            "exit 0\n",
        )
        proc = subprocess.run(
            ["/bin/bash", "-c", script],
            env={
                "PATH": f"{bin_dir}:/usr/bin:/bin",
                "HOME": str(tmp_path),
                "RADON_LAUNCHD_FETCH_PAUSE_SECS": "0",
            },
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        calls = log.read_text(encoding="utf-8").splitlines()
        joined = "\n".join(calls)
        assert "git -C" in joined and "fetch" in joined
        assert "checkout" in joined and "reset --hard" in joined
        assert "python" in joined and "grok_fix_pickup.py" in joined
        git_idxs = [i for i, line in enumerate(calls) if line.startswith("git ")]
        py_idxs = [i for i, line in enumerate(calls) if line.startswith("python ")]
        assert git_idxs and py_idxs and max(git_idxs) < min(py_idxs), calls

    def test_reset_leaves_local_fix_branches(self, tmp_path: Path) -> None:
        origin = tmp_path / "origin.git"
        origin.mkdir()
        assert _git(origin, "init", "--bare", "-b", "main").returncode == 0
        seed = tmp_path / "seed"
        seed.mkdir()
        assert _git(seed, "init", "-b", "main").returncode == 0
        _git(seed, "config", "user.name", "t")
        _git(seed, "config", "user.email", "t@example.invalid")
        _commit(seed, "app.py", "main\n", "init")
        assert _git(seed, "remote", "add", "origin", str(origin)).returncode == 0
        assert _git(seed, "push", "-q", "origin", "main").returncode == 0

        clone = tmp_path / "clone"
        assert _git(tmp_path, "clone", "-q", str(origin), str(clone)).returncode == 0
        _git(clone, "config", "user.name", "t")
        _git(clone, "config", "user.email", "t@example.invalid")
        assert _git(clone, "checkout", "-q", "-b", "fix/keep-me").returncode == 0
        _commit(clone, "app.py", "wip\n", "local wip")
        assert _git(clone, "checkout", "-q", "main").returncode == 0
        (clone / "dirty").write_text("stale checkout\n", encoding="utf-8")

        script = _pickup_script(clone)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        timeout = shutil.which("timeout") or shutil.which("gtimeout")
        if timeout:
            _executable(
                bin_dir / Path(timeout).name,
                "#!/bin/sh\nshift 3\nexec \"$@\"\n",
            )
        else:
            _executable(bin_dir / "timeout", "#!/bin/sh\nshift 3\nexec \"$@\"\n")
        _executable(
            bin_dir / "python3.13",
            "#!/bin/sh\nexit 0\n",
        )
        path = os.environ.get("PATH", "/usr/bin:/bin")
        proc = subprocess.run(
            ["/bin/bash", "-c", script],
            env={
                "PATH": f"{bin_dir}:{path}",
                "HOME": str(tmp_path),
                "RADON_LAUNCHD_FETCH_PAUSE_SECS": "0",
            },
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        head = _git(clone, "rev-parse", "--abbrev-ref", "HEAD")
        assert head.stdout.strip() == "main"
        listed = _git(clone, "for-each-ref", "--format=%(refname:short)")
        assert "fix/keep-me" in listed.stdout
        tip = _git(clone, "rev-parse", "fix/keep-me").stdout
        assert tip.strip()
        assert (clone / "app.py").read_text(encoding="utf-8") == "main\n"


class TestPushGate:
    """2026-09-30: pickup pushed fix/* and opened public PRs while the
    responder env said GROK_PAGE_AUTOPUSH=0; one diff and its commit
    messages carried private account and exec ids. Ids are invented."""

    @pytest.mark.parametrize("raw", [None, "0", ""])
    def test_autopush_off_pushes_nothing_and_opens_no_pr(
        self, world, monkeypatch, raw
    ):
        if raw is None:
            monkeypatch.delenv("GROK_PAGE_AUTOPUSH", raising=False)
        else:
            monkeypatch.setenv("GROK_PAGE_AUTOPUSH", raw)
        _vps_branch(world, "fix/relay-restart")
        ensure = _FakeEnsurePr()

        results = _run(world, ensure=ensure)

        assert [r["action"] for r in results] == ["disabled"]
        assert "GROK_PAGE_AUTOPUSH" in results[0]["reason"]
        listed = _git(world["origin"], "for-each-ref", "--format=%(refname)")
        assert "fix/relay-restart" not in listed.stdout
        assert ensure.calls == []

    def test_cli_exits_zero_when_disabled(self, world, monkeypatch, capsys):
        monkeypatch.delenv("GROK_PAGE_AUTOPUSH", raising=False)
        rc = pickup.main(["--repo", str(world["mini"]), "--source", str(world["vps"])])
        assert rc == 0
        assert '"action": "disabled"' in capsys.readouterr().out

    @pytest.mark.parametrize(
        "where, leak",
        [
            ("diff", "U" + "1234567"),
            ("diff", "0000abcd" + ".1234ef56" + ".01.01"),
            ("message", "98765" + "43210987"),
            ("message", "DU" + "7654321"),
            ("diff", "gh" + "p_" + "Z" * 36),
        ],
        ids=["account", "ib_exec", "flex_exec", "paper_account", "gh_token"],
    )
    def test_private_ids_keep_the_branch_local(self, world, where, leak):
        vps = world["vps"]
        assert _git(vps, "checkout", "-q", "-b", "fix/journal-leak").returncode == 0
        message = _valid_ir_message("fix/journal-leak")
        content = "x = 2\n"
        if where == "diff":
            content += f"ROW = '{leak}'\n"
        else:
            message += f"\nJournal row: {leak} filled.\n"
        _commit(vps, "app.py", content, message)
        ensure = _FakeEnsurePr()
        alerts: list[tuple[str, str]] = []

        results = _run(world, ensure=ensure, alerter=lambda b, r: alerts.append((b, r)))

        assert [r["action"] for r in results] == ["refused"]
        reason = results[0]["reason"]
        assert reason.startswith("private identifiers found")
        assert leak not in reason
        assert alerts and leak not in alerts[0][1]
        listed = _git(world["origin"], "for-each-ref", "--format=%(refname)")
        assert "fix/journal-leak" not in listed.stdout
        assert ensure.calls == []

    def test_plist_ships_with_autopush_off(self):
        plist = plistlib.loads(PLIST.read_bytes())
        env = plist["EnvironmentVariables"]
        assert env.get("GROK_PAGE_AUTOPUSH") == "0"

    def test_stale_pickup_code_refuses_to_run(self, world, monkeypatch):
        """A clone that stopped refreshing ran pre-#773 code with none of
        these gates. Running from a clone behind origin/main is refused."""
        monkeypatch.setattr(
            pickup, "__file__", str(world["mini"] / "scripts" / "grok_fix_pickup.py")
        )
        seed = world["origin"].parent / "seed"
        _commit(seed, "app.py", "x = 3\n", "main moves on")
        assert _git(seed, "push", "-q", "origin", "main").returncode == 0
        _vps_branch(world, "fix/relay-restart")

        with pytest.raises(pickup.PickupError, match="older than origin/main"):
            _run(world)
