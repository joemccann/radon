"""Fail-closed tests for private P1 validation before public publication."""
from __future__ import annotations

import hashlib
import base64
import json
import os
from pathlib import Path
import subprocess
import urllib.error
import urllib.request

import pytest

import private_security_release as bridge_module
import unattended_release as ur

BASE = "a" * 40
HEAD = "b" * 40
MERGE = "c" * 40
PRIVATE = "joemccann/radon-security-reports"
PUBLIC = "joemccann/radon"
DIFF = "diff --git a/private.py b/private.py\n+private P1 route /internal/account\n"
DIGEST = hashlib.sha256(DIFF.encode()).hexdigest()
CHECKS = ("pytest", "vitest")


def _policy():
    return ur.TrustedPolicy(
        repository=PUBLIC,
        authors=("radon-runner-bot",),
        branch_prefixes=("security-private-release/",),
        required_checks=CHECKS,
        reviewers=("gpt-6.1-sol", "gpt-6-luna"),
        hosts=("app", "broker"),
    )


def _ref(base=BASE, head=HEAD):
    return {"ref": f"refs/heads/validation/fix/{base}/{head}", "object": {"sha": head}}


def _pr(head=HEAD, base=BASE):
    return {
        "number": 17,
        "head": {"sha": head},
        "base": {"sha": base},
        "html_url": "https://github.com/joemccann/radon-security-reports/pull/17",
    }


def _green_runs(head=HEAD):
    return {
        "total_count": len(CHECKS),
        "check_runs": [
            {"name": name, "status": "completed", "conclusion": "success", "head_sha": head}
            for name in CHECKS
        ],
    }


class FakeGitHub:
    def __init__(self, *, private=True, refs=None, private_prs=None, public_prs=None):
        self.private = private
        self.refs = list(refs if refs is not None else [_ref()])
        self.private_prs = list(private_prs if private_prs is not None else [_pr()])
        self.public_prs = list(public_prs or [])
        self.public_pull = {
            "number": 17, "state": "open", "merged": False,
            "head": {"sha": HEAD}, "base": {"ref": "main", "sha": BASE},
        }
        self.public_check_runs = {"total_count": 0, "check_runs": []}
        self.public_comparison = {"status": "ahead", "merge_base_commit": {"sha": BASE}, "files": [{"filename": "fix.py"}]}
        self.calls = []
        self.check_runs = _green_runs()
        self.fresh_check_runs = None
        self.private_pr = _pr()
        self.fresh_private_pr = None
        self.comparison = {"status": "ahead", "merge_base_commit": {"sha": BASE}}
        self.public_main = BASE
        self.archive_main = "f" * 40
        self.workflow_paths = [f".github/workflows/{name}" for name in ("ci.yml", "pr-body.yml", "app-images-pr.yml", "app-images.yml")]
        self.workflow_sources = {
            self.workflow_paths[0]: "on:\n  pull_request:\n  push:\n    branches: [main]",
            **{path: f"name: {Path(path).name}\n" for path in self.workflow_paths[1:]},
        }
        self.workflow_tree = []
        self.public_variables = {
            "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY": "pk_test_fixture",
            "NEXT_PUBLIC_RADON_API_URL": "https://api.example.invalid",
            "NEXT_PUBLIC_IB_REALTIME_WS_URL": "wss://ws.example.invalid",
        }
        self.private_variables = {}
        self.call_kwargs = []

    def api(self, path, *args, **kwargs):
        self.calls.append((path, args))
        self.call_kwargs.append((path, kwargs))
        if path == f"repos/{PRIVATE}":
            return {"private": self.private}
        if path == f"repos/{PRIVATE}/git/ref/heads/main":
            return {"object": {"sha": self.archive_main}}
        if path == f"repos/{PRIVATE}/git/commits/{self.archive_main}":
            return {"tree": {"sha": "tree-sha"}}
        if path == f"repos/{PRIVATE}/git/trees/tree-sha?recursive=1":
            return {"truncated": False, "tree": self.workflow_tree}
        if path == f"repos/{PRIVATE}/actions/permissions":
            return {"enabled": True}
        if path.startswith(f"repos/{PUBLIC}/actions/variables/"):
            name = path.rsplit("/", 1)[-1]
            return {"value": self.public_variables[name]}
        if path.startswith(f"repos/{PRIVATE}/actions/variables/"):
            name = path.rsplit("/", 1)[-1]
            if "--method" in args:
                payload = kwargs["payload"]
                self.private_variables[payload["name"]] = payload["value"]
                return {"name": payload["name"]}
            if name not in self.private_variables:
                raise RuntimeError("variable not found")
            return {"value": self.private_variables[name]}
        if path == f"repos/{PRIVATE}/actions/variables":
            payload = kwargs["payload"]
            self.private_variables[payload["name"]] = payload["value"]
            return {"name": payload["name"]}
        if path.startswith(f"repos/{PUBLIC}/contents/"):
            filename = path.split("?ref=", 1)[0].removeprefix(f"repos/{PUBLIC}/contents/")
            return {"content": base64.b64encode(self.workflow_sources[filename].encode()).decode()}
        if path == f"repos/{PRIVATE}/git/trees":
            return {"sha": "new-tree"}
        if path == f"repos/{PRIVATE}/git/commits":
            return {"sha": "new-commit"}
        if path == f"repos/{PRIVATE}/git/refs/heads/main":
            return {"ref": "refs/heads/main"}
        if path.startswith(f"repos/{PRIVATE}/git/matching-refs/"):
            return self.refs
        if path.startswith(f"repos/{PRIVATE}/pulls?state=open"):
            return self.private_prs
        if path == f"repos/{PRIVATE}/pulls/17":
            return self.fresh_private_pr or self.private_pr
        if path == f"repos/{PUBLIC}/pulls/1118":
            return self.public_pull
        if path == f"repos/{PRIVATE}/commits/{HEAD}/check-runs?per_page=100":
            result = self.fresh_check_runs or self.check_runs
            if self.fresh_check_runs is not None:
                self.fresh_check_runs = None
            return result
        if path == f"repos/{PRIVATE}/compare/{BASE}...{HEAD}":
            return self.comparison
        if path == f"repos/{PUBLIC}/commits/{HEAD}/check-runs?per_page=100":
            return self.public_check_runs
        if path == f"repos/{PUBLIC}/compare/{BASE}...{HEAD}":
            return self.public_comparison
        if path.startswith(f"repos/{PUBLIC}/pulls?state=all"):
            return self.public_prs
        if path == f"repos/{PUBLIC}/pulls":
            return {"html_url": "https://github.com/joemccann/radon/pull/1118"}
        raise AssertionError(f"unexpected API call {path} {args}")

    def main_sha(self):
        return self.public_main


class FakeReviewer:
    def __init__(self, *, approved=True, head=HEAD, digest=DIGEST):
        self.approved, self.head, self.digest = approved, head, digest
        self.calls = []

    def review(self, pr, diff):
        self.calls.append((pr, diff))
        return [
            ur.ReviewReceipt(model, self.head, self.digest, self.approved, authenticated=True)
            for model in _policy().reviewers
        ]


def _bridge(tmp_path, *, github=None, reviewer=None):
    github = github or FakeGitHub()
    result = bridge_module.PrivateReleaseBridge(
        github, reviewer or FakeReviewer(), _policy(), tmp_path / "private-state"
    )
    github.workflow_pushes = []
    result._push_validation_workflows = lambda archive, changed: github.workflow_pushes.append((archive, changed))
    return result


def test_export_candidate_rejects_non_p1_and_invalid_immutable_refs_before_io(monkeypatch, tmp_path):
    called = False

    def forbidden(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("invalid export must not make network or git calls")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    with pytest.raises(ValueError, match="P1"):
        bridge_module.export_candidate(BASE, HEAD, severity="P0", cwd=tmp_path)
    with pytest.raises(ValueError):
        bridge_module.export_candidate("not-a-sha", HEAD, severity="P1", cwd=tmp_path)
    assert not called


def test_both_security_nightly_prompts_export_completed_p1_without_manual_intervention():
    repo = Path(__file__).resolve().parents[2]
    command = "python3.13 scripts/private_security_release.py --severity P1 --base <base-sha> --head <fixed-sha>"
    for name in ("security", "security-deepsec"):
        prompt = (repo / ".claude" / "runner-prompts" / f"{name}.md").read_text(encoding="utf-8")
        assert command in prompt
        assert "Report `private-validation-pending` as automatic work" in prompt
        assert "P0 remains private" in prompt


@pytest.mark.parametrize("status", [200, 403, 500])
def test_export_candidate_requires_unauthed_private_archive_404(monkeypatch, tmp_path, status):
    class Response:
        def close(self):
            pass

    def urlopen(*args, **kwargs):
        if status == 200:
            return Response()
        raise urllib.error.HTTPError("url", status, "fixture", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(PermissionError if status == 200 else RuntimeError):
        bridge_module.export_candidate(BASE, HEAD, severity="P1", cwd=tmp_path)


def test_export_candidate_uses_only_pinned_private_atomic_refs(monkeypatch, tmp_path):
    key = tmp_path / ".radon-runner-reports-key"
    key.write_text("synthetic-private-key", encoding="utf-8")
    key.chmod(0o600)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("GH_TOKEN", "synthetic-public-token")
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-app-token")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/synthetic-agent.sock")
    def private_archive_404(*args, **kwargs):
        raise urllib.error.HTTPError("url", 404, "private fixture", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", private_archive_404)
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        if args[2:4] == ["merge-base", "--is-ancestor"]:
            return subprocess.CompletedProcess(args, 0)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(bridge_module.subprocess, "run", fake_run)
    result = bridge_module.export_candidate(BASE, HEAD, severity="P1", cwd=tmp_path)

    assert result == {"status": "private-validation-pending", "head_sha": HEAD}
    args, kwargs = calls[-1]
    assert args[1:4] == ["-c", "core.hooksPath=/dev/null", "-c"]
    assert "--atomic" in args
    ancestry = calls[0][0]
    assert ancestry[-4:] == ["merge-base", "--is-ancestor", BASE, HEAD]
    assert f"{BASE}:refs/heads/validation/base/{BASE}" in args
    assert f"{HEAD}:refs/heads/validation/fix/{BASE}/{HEAD}" in args
    ssh = kwargs["env"]["GIT_SSH_COMMAND"]
    assert "IdentitiesOnly=yes" in ssh and "StrictHostKeyChecking=yes" in ssh
    assert "UserKnownHostsFile=" in ssh and "synthetic-private-key" not in str(kwargs)
    assert not {"GH_TOKEN", "GITHUB_TOKEN", "SSH_AUTH_SOCK"}.intersection(kwargs["env"])


def test_bridge_refuses_public_validation_repository_before_followup_queries(tmp_path):
    github = FakeGitHub(private=False)

    with pytest.raises(PermissionError, match="not private"):
        _bridge(tmp_path, github=github).run_once()

    assert [path for path, _ in github.calls] == [f"repos/{PRIVATE}"]


def test_private_workflow_bootstrap_copies_pinned_pr_only_workflows_and_fixed_public_variables(tmp_path):
    github = FakeGitHub()
    _bridge(tmp_path, github=github).ensure_validation_workflows(BASE)

    archive, entries = github.workflow_pushes[0]
    assert archive == github.archive_main
    workflows = {entry["path"]: entry["content"] for entry in entries}
    assert set(workflows) == set(github.workflow_paths)
    assert not any(path in (f"repos/{PRIVATE}/git/trees", f"repos/{PRIVATE}/git/commits", f"repos/{PRIVATE}/git/refs/heads/main") for path, _ in github.calls)
    ci = workflows[".github/workflows/ci.yml"]
    assert "pull_request:\n    branches: ['validation/base/**']" in ci
    assert "push:" not in ci
    assert set(github.private_variables) == set(github.public_variables)
    assert all(github.private_variables[name] == value for name, value in github.public_variables.items())
    queried = {path.rsplit("/", 1)[-1] for path, _ in github.calls if path.startswith(f"repos/{PUBLIC}/actions/variables/")}
    assert queried == set(github.public_variables)
    assert any(path == f"repos/{PUBLIC}/contents/.github/workflows/ci.yml?ref={BASE}" for path, _ in github.calls)


@pytest.mark.parametrize("value", ["https://api.example.invalid?token=secret", "wss://ws.example.invalid#secret"])
def test_private_workflow_bootstrap_rejects_urls_with_query_or_fragment_secrets(tmp_path, value):
    github = FakeGitHub()
    github.public_variables["NEXT_PUBLIC_RADON_API_URL"] = value
    with pytest.raises(ValueError, match="public build URL"):
        _bridge(tmp_path, github=github).ensure_validation_workflows(BASE)
    assert github.private_variables == {}


@pytest.mark.parametrize(
    "github",
    [
        FakeGitHub(refs=[{"ref": _ref(head="e" * 40)["ref"], "object": {"sha": "f" * 40}}]),
        FakeGitHub(refs=[_ref(base="e" * 40)]),
        FakeGitHub(refs=[{"ref": "refs/heads/validation/fix/notsha/" + HEAD, "object": {"sha": HEAD}}]),
    ],
    ids=("ref-object-mismatch", "stale-base", "malformed-ref"),
)
def test_bridge_ignores_stale_or_malformed_private_refs(tmp_path, github):
    reviewer = FakeReviewer()

    assert _bridge(tmp_path, github=github, reviewer=reviewer).run_once() == "waiting"
    assert reviewer.calls == []
    assert not any(path.startswith(f"repos/{PRIVATE}/pulls?") for path, _ in github.calls)


@pytest.mark.parametrize(
    "runs",
    [
        {"total_count": 0, "check_runs": []},
        {"total_count": 2, "check_runs": [_green_runs()["check_runs"][0]]},
        {
            "total_count": 2,
            "check_runs": [
                {"name": "pytest", "status": "completed", "conclusion": "failure", "head_sha": HEAD},
                _green_runs()["check_runs"][1],
            ],
        },
        {
            "total_count": 2,
            "check_runs": [
                {"name": name, "status": "completed", "conclusion": "success", "head_sha": "e" * 40}
                for name in CHECKS
            ],
        },
        {
            "total_count": 2,
            "check_runs": [
                {"name": "pytest", "status": "completed", "conclusion": "skipped", "head_sha": HEAD},
                _green_runs()["check_runs"][1],
            ],
        },
    ],
    ids=("no-runs", "absent-suite", "failed-suite", "stale-head", "required-skipped"),
)
def test_bridge_requires_full_green_private_exact_head_ci(tmp_path, runs):
    github = FakeGitHub()
    github.check_runs = runs
    reviewer = FakeReviewer()

    expected = "private-validation-failed" if runs["check_runs"] and (
        any(run["head_sha"] != HEAD for run in runs["check_runs"])
        or any(run["status"] == "completed" and run["conclusion"] not in ("success", "neutral", "skipped") for run in runs["check_runs"])
        or any(run["name"] in CHECKS and run["status"] == "completed" and run["conclusion"] != "success" for run in runs["check_runs"])
    ) else "private-ci-pending"
    assert _bridge(tmp_path, github=github, reviewer=reviewer).run_once() == expected
    assert reviewer.calls == []


def test_bridge_requires_authenticated_approvals_for_exact_private_diff(tmp_path, monkeypatch):
    github = FakeGitHub()
    reviewer = FakeReviewer(digest="f" * 64)
    monkeypatch.setattr(bridge_module.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=DIFF))

    assert _bridge(tmp_path, github=github, reviewer=reviewer).run_once() == "private-validation-failed"
    assert reviewer.calls and not any(path == f"repos/{PUBLIC}/pulls" for path, _ in github.calls)


def test_bridge_rechecks_head_base_ci_and_main_after_slow_review(tmp_path, monkeypatch):
    github = FakeGitHub()
    github.fresh_private_pr = _pr(head="e" * 40)
    reviewer = FakeReviewer()
    monkeypatch.setattr(bridge_module.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=DIFF))

    assert _bridge(tmp_path, github=github, reviewer=reviewer).run_once() == "waiting"
    assert not any(path.startswith(f"repos/{PUBLIC}/pulls") for path, _ in github.calls)


@pytest.mark.parametrize("mutation", ["main", "private-checks"])
def test_bridge_rechecks_public_base_and_private_ci_after_review(tmp_path, monkeypatch, mutation):
    github = FakeGitHub()

    class ChangingReviewer(FakeReviewer):
        def review(self, pr, diff):
            result = super().review(pr, diff)
            if mutation == "main":
                github.public_main = "e" * 40
            else:
                github.fresh_check_runs = {
                    "total_count": 2,
                    "check_runs": [
                        {"name": "pytest", "status": "completed", "conclusion": "failure", "head_sha": HEAD},
                        _green_runs()["check_runs"][1],
                    ],
                }
            return result

    reviewer = ChangingReviewer()
    monkeypatch.setattr(bridge_module.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=DIFF))

    assert _bridge(tmp_path, github=github, reviewer=reviewer).run_once() == "waiting"
    assert not any(path.startswith(f"repos/{PUBLIC}/pulls") for path, _ in github.calls)


def test_bridge_publishes_only_reviewed_sha_and_sanitized_public_metadata_then_is_idempotent(tmp_path, monkeypatch):
    github = FakeGitHub()
    reviewer = FakeReviewer()
    commands = []

    def fake_run(args, **kwargs):
        commands.append((args, kwargs))
        if args[0] == "/opt/homebrew/bin/gh":
            return subprocess.CompletedProcess(args, 0, stdout=DIFF)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(bridge_module.subprocess, "run", fake_run)
    bridge = _bridge(tmp_path, github=github, reviewer=reviewer)

    result = bridge.run_once()

    assert result == "public-ci-pending"
    assert reviewer.calls == [(reviewer.calls[0][0], DIFF)]
    assert reviewer.calls[0][0].head_sha == HEAD and reviewer.calls[0][0].diff_digest == DIGEST
    git_push = [args for args, _ in commands if args[0] == "/usr/bin/git" and "push" in args]
    assert any(f"{HEAD}:refs/heads/security-private-release/{HEAD}" in args for args in git_push)
    public_create = next(args for path, args in github.calls if path == f"repos/{PUBLIC}/pulls")
    body = next(value.removeprefix("body=") for value in public_create if value.startswith("body="))
    assert "internal/account" not in body and "private P1 route" not in body
    marker = tmp_path / "private-state" / f"{HEAD}.json"
    assert json.loads(marker.read_text(encoding="utf-8")) == {
        "head_sha": HEAD,
        "public_pr": "https://github.com/joemccann/radon/pull/1118",
        "status": "public-ci-pending",
    }

    call_count = len(commands)
    assert bridge.run_once() == "public-ci-pending"
    assert len(commands) == call_count


def test_published_private_candidate_waits_for_public_ci_and_becomes_priority(tmp_path, monkeypatch):
    github = FakeGitHub()
    reviewer = FakeReviewer()
    monkeypatch.setattr(bridge_module.subprocess, "run", lambda args, **kwargs: subprocess.CompletedProcess(args, 0, stdout=DIFF))
    bridge = _bridge(tmp_path, github=github, reviewer=reviewer)

    assert bridge.run_once() == "public-ci-pending"
    assert bridge.run_once() == "public-ci-pending"
    github.public_check_runs = _green_runs()
    assert bridge.run_once() == "public-ready"
    assert bridge.priority_pr_number == 1118


def test_private_review_rejection_releases_other_public_candidates(tmp_path, monkeypatch):
    github = FakeGitHub()
    reviewer = FakeReviewer(approved=False)
    monkeypatch.setattr(bridge_module.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=DIFF))

    assert _bridge(tmp_path, github=github, reviewer=reviewer).run_once() == "private-validation-failed"
    marker = tmp_path / "private-state" / f"{HEAD}.json"
    assert json.loads(marker.read_text()) == {"head_sha": HEAD, "status": "private-review-rejected"}


def test_marker_creation_failure_never_leaves_a_partial_authority_file(tmp_path, monkeypatch):
    bridge = _bridge(tmp_path)
    marker = bridge.directory / f"{HEAD}.json"

    def interrupted_dump(value, stream):
        stream.write('{"head_sha":')
        raise OSError("simulated process interruption")

    monkeypatch.setattr(bridge_module.json, "dump", interrupted_dump)
    with pytest.raises(OSError, match="simulated"):
        bridge._save_marker(marker, {"head_sha": HEAD, "public_pr": "https://github.com/joemccann/radon/pull/17"})
    assert not marker.exists()
    assert not list(bridge.directory.glob(marker.name + ".*"))


def test_marker_creation_never_overwrites_existing_state(tmp_path):
    bridge = _bridge(tmp_path)
    marker = bridge.directory / f"{HEAD}.json"
    marker.write_text('{"head_sha":"owner-state"}')
    marker.chmod(0o600)

    with pytest.raises(FileExistsError):
        bridge._save_marker(marker, {"head_sha": HEAD, "public_pr": "https://github.com/joemccann/radon/pull/17"})
    assert marker.read_text() == '{"head_sha":"owner-state"}'


def _local_workflow_archive(tmp_path):
    seed, archive = tmp_path / 'seed', tmp_path / 'archive.git'
    seed.mkdir()
    def git(*args, input=None):
        return subprocess.run(['/usr/bin/git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                               '-C', str(seed), *args], input=input, check=True, capture_output=True, text=True).stdout.strip()
    git('init', '-q', '-b', 'main')
    (seed / 'report.md').write_text('private archive must remain unchanged\n')
    other = seed / '.github/workflows/unrelated.yml'
    other.parent.mkdir(parents=True)
    other.write_text('name: Unrelated\n')
    git('add', '--', 'report.md', '.github/workflows/unrelated.yml')
    git('commit', '-q', '-m', 'archive baseline')
    head = git('rev-parse', 'HEAD')
    git('clone', '--bare', '-q', str(seed), str(archive))
    return archive, head


def test_workflow_ssh_transport_preserves_archive_and_only_fixed_paths(monkeypatch, tmp_path):
    archive, head = _local_workflow_archive(tmp_path)
    original, calls = subprocess.run, []
    remote = f'git@github.com:{PRIVATE}.git'
    monkeypatch.setenv('GH_TOKEN', 'must-not-copy-token')
    monkeypatch.setenv('GITHUB_TOKEN', 'must-not-copy-app-token')
    def local_run(args, **kwargs):
        calls.append((list(args), kwargs))
        return original([str(archive) if arg == remote else arg for arg in args], **kwargs)
    monkeypatch.setattr(bridge_module.subprocess, 'run', local_run)
    bridge = bridge_module.PrivateReleaseBridge(FakeGitHub(), None, _policy(), tmp_path / 'state')
    paths = FakeGitHub().workflow_paths
    changed = [{'path': path, 'content': 'name: Fixed approved source\n'} for path in paths]
    bridge._push_validation_workflows(head, changed)
    def show(ref):
        return original(['/usr/bin/git', '--git-dir', str(archive), 'show', ref], check=True, capture_output=True, text=True).stdout
    assert show('main:report.md') == 'private archive must remain unchanged\n'
    assert show('main:.github/workflows/unrelated.yml') == 'name: Unrelated\n'
    for path in paths:
        assert show('main:' + path) == 'name: Fixed approved source\n'
    for args, kwargs in calls:
        assert 'core.hooksPath=/dev/null' in args and 'core.fsmonitor=false' in args
        assert 'StrictHostKeyChecking=yes' in kwargs['env']['GIT_SSH_COMMAND']
        assert 'BatchMode=yes' in kwargs['env']['GIT_SSH_COMMAND']
        assert not {'GH_TOKEN', 'GITHUB_TOKEN'}.intersection(kwargs['env'])
        assert not any(arg in ('checkout', 'switch', '--force') for arg in args)
    pushes = [args for args, _ in calls if 'push' in args]
    assert len(pushes) == 1 and remote in pushes[0] and pushes[0][-1].endswith(':refs/heads/main')


def test_workflow_ssh_transport_rejects_changed_archive_and_unapproved_paths(monkeypatch, tmp_path):
    archive, _ = _local_workflow_archive(tmp_path)
    original, calls = subprocess.run, []
    remote = f'git@github.com:{PRIVATE}.git'
    def local_run(args, **kwargs):
        calls.append(args)
        return original([str(archive) if arg == remote else arg for arg in args], **kwargs)
    monkeypatch.setattr(bridge_module.subprocess, 'run', local_run)
    bridge = bridge_module.PrivateReleaseBridge(FakeGitHub(), None, _policy(), tmp_path / 'state')
    with pytest.raises(ValueError):
        bridge._push_validation_workflows('a' * 40, [{'path': 'report.md', 'content': 'forbidden'}])
    assert not calls
    with pytest.raises(RuntimeError, match='archive main changed'):
        bridge._push_validation_workflows('a' * 40, [{'path': '.github/workflows/ci.yml', 'content': 'name: Fixed\n'}])
    assert not any('push' in args for args in calls)


def test_workflow_ssh_transport_rejects_concurrent_archive_writer(monkeypatch, tmp_path):
    archive, head = _local_workflow_archive(tmp_path)
    original = subprocess.run
    remote = f'git@github.com:{PRIVATE}.git'
    seed = tmp_path / 'seed'
    def local_run(args, **kwargs):
        if 'push' in args:
            (seed / 'report.md').write_text('concurrent report must survive\n')
            prefix = ['/usr/bin/git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', '-C', str(seed)]
            for command in (['add', 'report.md'], ['commit', '-q', '-m', 'concurrent report'],
                            ['push', '-q', str(archive), 'HEAD:refs/heads/main']):
                original(prefix + command, check=True, capture_output=True, text=True)
        return original([str(archive) if arg == remote else arg for arg in args], **kwargs)
    monkeypatch.setattr(bridge_module.subprocess, 'run', local_run)
    bridge = bridge_module.PrivateReleaseBridge(FakeGitHub(), None, _policy(), tmp_path / 'state')
    with pytest.raises(subprocess.CalledProcessError):
        bridge._push_validation_workflows(head, [{'path': '.github/workflows/ci.yml', 'content': 'name: Fixed\n'}])
    report = original(['/usr/bin/git', '--git-dir', str(archive), 'show', 'main:report.md'],
                      check=True, capture_output=True, text=True).stdout
    assert report == 'concurrent report must survive\n'
    assert original(['/usr/bin/git', '--git-dir', str(archive), 'cat-file', '-e', 'main:.github/workflows/ci.yml'],
                    capture_output=True).returncode != 0


def test_workflow_source_requires_immutable_sha_before_any_api(tmp_path):
    github = FakeGitHub()
    with pytest.raises(ValueError, match='immutable'):
        _bridge(tmp_path, github=github).ensure_validation_workflows('main')
    assert not github.calls
