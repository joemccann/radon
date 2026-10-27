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
        self.public_statuses = {"sha": HEAD, "total_count": 0, "statuses": []}
        self.public_comparison = {"status": "ahead", "ahead_by": 1, "merge_base_commit": {"sha": BASE}, "files": [{"filename": "fix.py"}]}
        self.calls = []
        self.check_runs = _green_runs()
        self.fresh_check_runs = None
        self.private_pr = _pr()
        self.fresh_private_pr = None
        # ahead_by must be 1: the release path pushes the head commit's whole
        # ancestry into the public repository, so a multi-commit candidate
        # would publish intermediate trees that review never saw.
        self.comparison = {"status": "ahead", "ahead_by": 1, "merge_base_commit": {"sha": BASE}}
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
        if path == f"repos/{PUBLIC}/commits/{HEAD}/status?per_page=100":
            return self.public_statuses
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

    def review(self, pr, diff, **kwargs):
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


_REAL_REQUIRE_PLAIN_COMMIT = bridge_module._require_plain_commit
_PLAIN_COMMIT = (
    f"tree {'2' * 40}\nparent {BASE}\n"
    "author radon bot <bot@example.test> 1793000000 +0000\n"
    "committer radon bot <bot@example.test> 1793000000 +0000\n\nfix: repair\n"
).encode()


@pytest.fixture(autouse=True)
def _commit_object_check_stubbed(monkeypatch):
    # Bridge fakes return no commit object; the check has its own tests below.
    monkeypatch.setattr(bridge_module, "_require_plain_commit", lambda raw: None)


@pytest.mark.parametrize("raw", [
    _PLAIN_COMMIT,
    _PLAIN_COMMIT.replace(f"parent {BASE}\n".encode(), b""),
    _PLAIN_COMMIT.replace(f"parent {BASE}\n".encode(), f"parent {BASE}\nparent {HEAD}\n".encode()),
], ids=["one-parent", "root", "merge"])
def test_plain_commit_object_is_accepted(raw):
    _REAL_REQUIRE_PLAIN_COMMIT(raw)


@pytest.mark.parametrize("raw", [
    _PLAIN_COMMIT.replace(b"\n\n", b"\nx-note unreviewed text\n\n"),
    _PLAIN_COMMIT.replace(b"\n\n", b"\ngpgsig -----BEGIN-----\n unreviewed\n -----END-----\n\n"),
    _PLAIN_COMMIT.replace(b"\n\n", b"\nencoding unreviewed\n\n"),
    _PLAIN_COMMIT.replace(b"committer", b"author", 1),
    _PLAIN_COMMIT.replace(b"author radon bot <bot@example.test> 1793000000 +0000\n", b""),
], ids=["extra-header", "continuation", "encoding", "duplicate-author", "missing-author"])
def test_commit_object_with_unreviewed_headers_is_refused(raw):
    with pytest.raises(ValueError, match="headers"):
        _REAL_REQUIRE_PLAIN_COMMIT(raw)


def test_publish_refuses_unreviewed_commit_headers_before_push(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge_module, "_require_plain_commit", _REAL_REQUIRE_PLAIN_COMMIT)
    commands = []

    def fake_run(args, **kwargs):
        commands.append(args)
        if "cat-file" in args:
            return subprocess.CompletedProcess(args, 0, stdout=_PLAIN_COMMIT.replace(b"\n\n", b"\nx-note unreviewed\n\n"))
        return subprocess.CompletedProcess(args, 0, stdout=b"")

    monkeypatch.setattr(bridge_module.subprocess, "run", fake_run)
    bridge = _bridge(tmp_path)
    bridge.directory.mkdir(parents=True, exist_ok=True)
    with pytest.raises(ValueError, match="headers"):
        bridge._publish(HEAD, f"security-private-release/{HEAD}")
    assert any("fetch" in args for args in commands)
    assert not any("push" in args for args in commands)


def test_publish_pushes_a_plain_commit(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge_module, "_require_plain_commit", _REAL_REQUIRE_PLAIN_COMMIT)
    commands = []

    def fake_run(args, **kwargs):
        commands.append(args)
        if "cat-file" in args:
            return subprocess.CompletedProcess(args, 0, stdout=_PLAIN_COMMIT)
        return subprocess.CompletedProcess(args, 0, stdout=b"")

    monkeypatch.setattr(bridge_module.subprocess, "run", fake_run)
    bridge = _bridge(tmp_path)
    bridge.directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(bridge, "_api", lambda path, *args: [{"html_url": "https://github.com/joemccann/radon/pull/1"}])
    assert bridge._publish(HEAD, f"security-private-release/{HEAD}") == "https://github.com/joemccann/radon/pull/1"
    assert any(f"{HEAD}:refs/heads/security-private-release/{HEAD}" in args for args in commands if "push" in args)


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
        if "rev-list" in args:
            return subprocess.CompletedProcess(args, 0, stdout="1\n")
        return subprocess.CompletedProcess(args, 0, stdout="")

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
        FakeGitHub(refs=[{"ref": "refs/heads/validation/fix/notsha/" + HEAD, "object": {"sha": HEAD}}]),
    ],
    ids=("ref-object-mismatch", "malformed-ref"),
)
def test_bridge_ignores_mismatched_or_malformed_private_refs(tmp_path, github):
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
        def review(self, pr, diff, **kwargs):
            result = super().review(pr, diff, **kwargs)
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
        "base_sha": BASE,
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
    assert json.loads(marker.read_text())["status"] == "private-review-rejected"
    assert json.loads(marker.read_text())["feedback"]["head_sha"] == HEAD


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


@pytest.mark.parametrize('public_state', ['pending', 'stale-check', 'changed-head', 'green', 'review-rejected'])
def test_bridge_queues_only_exact_public_ci_green_even_if_review_rejects(tmp_path, monkeypatch, public_state):
    github = FakeGitHub()
    reviewer = FakeReviewer()
    events = []
    monkeypatch.setattr(bridge_module.subprocess, 'run', lambda args, **kwargs: subprocess.CompletedProcess(args, 0, stdout=DIFF))
    bridge = _bridge(tmp_path, github=github, reviewer=reviewer)
    bridge.on_public_green = lambda number, head: events.append((number, head))
    assert bridge.run_once() == 'public-ci-pending'
    if public_state != 'pending': github.public_check_runs = _green_runs()
    if public_state == 'stale-check': github.public_check_runs = _green_runs(head=MERGE)
    if public_state == 'changed-head': github.public_pull['head']['sha'] = MERGE
    if public_state == 'review-rejected': reviewer.approved = False
    result = bridge.run_once()
    assert events == ([(1118, HEAD)] if public_state in ('green', 'review-rejected') else [])
    if public_state == 'review-rejected': assert result == 'private-validation-failed'


@pytest.mark.parametrize('legacy', ['pending', 'failure', 'error', 'success', 'wrong-sha', 'truncated', 'count-mismatch'])
def test_public_pr_green_and_readiness_include_bounded_exact_head_legacy_statuses(tmp_path, monkeypatch, legacy):
    github = FakeGitHub()
    events = []
    monkeypatch.setattr(bridge_module.subprocess, 'run', lambda args, **kwargs: subprocess.CompletedProcess(args, 0, stdout=DIFF))
    bridge = _bridge(tmp_path, github=github)
    bridge.on_public_green = lambda number, head: events.append((number, head))
    assert bridge.run_once() == 'public-ci-pending'
    github.public_check_runs = _green_runs()
    state = legacy if legacy in ('pending', 'failure', 'error', 'success') else 'success'
    github.public_statuses = {'sha': HEAD, 'total_count': 1, 'statuses': [{'context': 'external preview', 'state': state}]}
    if legacy == 'wrong-sha': github.public_statuses['sha'] = MERGE
    if legacy == 'truncated': github.public_statuses['total_count'] = 101
    if legacy == 'count-mismatch': github.public_statuses['total_count'] = 2
    result = bridge.run_once()
    assert events == ([(1118, HEAD)] if legacy == 'success' else [])
    assert result == ('public-ready' if legacy == 'success' else 'public-ci-pending' if legacy == 'pending' else 'private-validation-failed')
    assert bridge.priority_pr_number == (1118 if legacy == 'success' else None)


def test_feedback_schema_is_bounded_identity_bound_and_redacts_secrets():
    from types import SimpleNamespace
    receipt = SimpleNamespace(reviewer='sol', review_input_digest='d' * 64,
        findings=('token=credential Bearer opaquevalue github_pat_abcdef sk-abcdefghijklmno UNUSUAL_WHALES_API_KEY=anothercredential',))
    value = bridge_module._make_feedback(BASE, HEAD, 'private-review-rejected', receipts=[receipt])
    encoded = bridge_module._validate_feedback(value, BASE, HEAD)
    assert b'anothercredential' not in encoded and b'credential' not in encoded and b'opaquevalue' not in encoded and b'github_pat_' not in encoded and b'sk-abcdefgh' not in encoded
    assert value['review_input_digests'] == ['d' * 64]
    for mutation in ({**value, 'head_sha': MERGE}, {**value, 'extra': True}, {**value, 'schema': True},
                     {**value, 'findings': [{'reviewer': 'sol', 'message': 'x' * 140000}]}):
        with pytest.raises(ValueError): bridge_module._validate_feedback(mutation, BASE, HEAD)
    with pytest.raises(ValueError): bridge_module._feedback_ref('../escape', HEAD)
    with pytest.raises(ValueError): bridge_module._make_feedback(BASE, HEAD, 'released')


def _feedback_api(monkeypatch, bridge, *, fail_ref_once=False):
    blobs, trees, commits, refs, calls = {}, {}, {}, {}, []
    def api(path, *args, **kwargs):
        calls.append((path, args, kwargs))
        payload = kwargs.get('payload', {})
        suffix = path.removeprefix(f'repos/{PRIVATE}/')
        if path == f'repos/{PRIVATE}': return {'private': True}
        if suffix.startswith('git/ref/'):
            ref = 'refs/' + suffix.removeprefix('git/ref/')
            if ref not in refs: raise RuntimeError('not found')
            return {'object': {'sha': refs[ref]}}
        if suffix == 'git/blobs':
            sha = hashlib.sha1(payload['content'].encode()).hexdigest(); blobs[sha] = payload['content']
            return {'sha': sha}
        if suffix == 'git/trees':
            sha = hashlib.sha1(json.dumps(payload).encode()).hexdigest(); trees[sha] = payload['tree']
            return {'sha': sha}
        if suffix == 'git/commits':
            sha = hashlib.sha1(json.dumps(payload).encode()).hexdigest(); commits[sha] = payload
            return {'sha': sha}
        if suffix == 'git/refs':
            if fail_ref_once and not getattr(api, 'failed', False):
                api.failed = True; raise RuntimeError('network unavailable')
            assert payload['ref'] not in refs
            refs[payload['ref']] = payload['sha']; return {'ref': payload['ref']}
        if suffix.startswith('git/commits/'):
            row = commits[suffix.rsplit('/', 1)[1]]
            return {'parents': row['parents'], 'tree': {'sha': row['tree']}}
        if suffix.startswith('git/trees/'):
            return {'tree': trees[suffix.rsplit('/', 1)[1]], 'truncated': False}
        if suffix.startswith('git/blobs/'):
            text = blobs[suffix.rsplit('/', 1)[1]]
            return {'size': len(text.encode()), 'encoding': 'base64', 'content': base64.b64encode(text.encode()).decode()}
        raise AssertionError(path)
    monkeypatch.setattr(bridge, '_api', api)
    return refs, commits, calls


def test_terminal_feedback_is_durable_before_transport_and_retries_immutable_orphan(tmp_path, monkeypatch):
    bridge = _bridge(tmp_path)
    marker = bridge.directory / f'{HEAD}.json'
    bridge._terminal_marker(marker, BASE, HEAD, 'private-checks-failed')
    refs, commits, calls = _feedback_api(monkeypatch, bridge, fail_ref_once=True)
    with pytest.raises(RuntimeError): bridge.flush_feedback({})
    assert json.loads(marker.read_text())['feedback']['status'] == 'private-checks-failed'
    bridge.flush_feedback({}); bridge.flush_feedback({})
    assert list(refs) == [f'refs/heads/validation/feedback/{BASE}/{HEAD}']
    assert all(value['parents'] == [] for value in commits.values())
    assert sum(path.endswith('git/refs') for path, _, _ in calls) == 2
    assert not any('PATCH' in args for _, args, _ in calls)


def test_released_feedback_requires_persisted_green_matching_both_host_receipts(tmp_path, monkeypatch):
    bridge = _bridge(tmp_path)
    marker = bridge.directory / f'{HEAD}.json'
    bridge._save_marker(marker, {'head_sha': HEAD, 'base_sha': BASE, 'public_pr': f'https://github.com/{PUBLIC}/pull/42'})
    refs, _, _ = _feedback_api(monkeypatch, bridge)
    record = {'status': 'green', 'pr_number': 42, 'head_sha': HEAD, 'merge_sha': MERGE,
              'sync_receipt': {'main_sha': MERGE, 'hosts': {'app': True, 'broker': False}}}
    bridge.flush_feedback({f'42:{HEAD}': record}); assert not refs
    record['sync_receipt']['hosts']['broker'] = True
    bridge.flush_feedback({f'42:{HEAD}': record})
    assert json.loads(marker.read_text())['feedback']['status'] == 'released'
    assert len(refs) == 1


@pytest.mark.parametrize('kind', ['valid', 'missing', 'parent', 'path', 'near-path', 'symlink', 'extra', 'oversize', 'head', 'schema', 'secret'])
def test_bot_polls_only_bounded_identity_bound_orphan_feedback_with_existing_git_key(tmp_path, monkeypatch, kind):
    archive = tmp_path / 'archive.git'
    subprocess.run(['git', 'init', '--bare', '-q', str(archive)], check=True)
    def git(*args, input=None):
        return subprocess.run(['git', '-C', str(archive), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', *args], input=input, check=True, capture_output=True).stdout.strip().decode()
    value = bridge_module._make_feedback(BASE, HEAD, 'private-review-rejected')
    if kind == 'head': value['head_sha'] = MERGE
    if kind == 'schema': value['extra'] = True
    if kind == 'secret': value['findings'] = [{'reviewer': 'sol', 'message': 'Bearer credentialliteral'}]
    raw = json.dumps(value).encode() if kind != 'oversize' else b'x' * (bridge_module.MAX_FEEDBACK_BYTES + 1)
    blob = git('hash-object', '-w', '--stdin', input=raw)
    tree_input = f'{"120000" if kind == "symlink" else "100644"} blob {blob}\t{"other.json" if kind == "path" else "feedbackXjson" if kind == "near-path" else "feedback.json"}\n'
    if kind == 'extra': tree_input += f'100644 blob {blob}\textra.json\n'
    tree = git('mktree', input=tree_input.encode())
    parent = git('commit-tree', tree, input=b'orphan\n')
    commit = git('commit-tree', tree, '-p', parent, input=b'parented\n') if kind == 'parent' else parent
    if kind != 'missing': git('update-ref', f'refs/heads/validation/feedback/{BASE}/{HEAD}', commit)
    key = tmp_path / '.radon-runner-reports-key'; key.write_text('fixture'); key.chmod(0o600)
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(bridge_module, '_require_private_archive', lambda: None)
    real_run = subprocess.run
    environments = []
    def local_transport(args, **kwargs):
        environments.append(kwargs.get('env', {}))
        return real_run([str(archive) if a == f'git@github.com:{PRIVATE}.git' else a for a in args], **kwargs)
    monkeypatch.setattr(bridge_module.subprocess, 'run', local_transport)
    if kind == 'valid': assert bridge_module.poll_feedback(BASE, HEAD, cwd=tmp_path) == value
    elif kind == 'missing': assert bridge_module.poll_feedback(BASE, HEAD, cwd=tmp_path)['status'] == 'private-validation-pending'
    else:
        with pytest.raises(ValueError): bridge_module.poll_feedback(BASE, HEAD, cwd=tmp_path)
    assert environments
    assert all(not {'GH_TOKEN', 'GITHUB_TOKEN', 'SSH_AUTH_SOCK'}.intersection(env) for env in environments)
    assert all('IdentitiesOnly=yes' in env['GIT_SSH_COMMAND'] and 'StrictHostKeyChecking=yes' in env['GIT_SSH_COMMAND'] for env in environments)


def test_ci_feedback_contains_bounded_redacted_log_tail_from_only_fixed_repo_job(monkeypatch):
    import sys
    real_popen = subprocess.Popen
    calls = []
    def fixture(args, **kwargs):
        calls.append(args)
        return real_popen([sys.executable, '-c', "print('x'*20000); print('\\x1b]0;untrusted title\\x07\\x1b[31massert expected == actual\\x1b[0m; token=credentialliteral\\r')"], **kwargs)
    monkeypatch.setattr(bridge_module.subprocess, 'Popen', fixture)
    page = {'check_runs': [{'head_sha': HEAD, 'status': 'completed', 'conclusion': 'failure', 'name': 'pytest',
                           'details_url': f'https://github.com/{PRIVATE}/actions/runs/1/job/2'}]}
    messages = bridge_module._ci_diagnostics(page, PRIVATE, HEAD)
    assert 'expected == actual' in ' '.join(messages)
    assert 'credentialliteral' not in ' '.join(messages)
    assert len(' '.join(messages).encode()) < 18000
    assert calls[0] == ['/opt/homebrew/bin/gh', 'api', '--allow-escape-sequences', f'repos/{PRIVATE}/actions/jobs/2/logs']
    assert not any(ord(c) < 32 and c not in '\n\t' for c in ''.join(messages))
    assert 'untrusted title' not in ''.join(messages)
    page['check_runs'][0]['details_url'] = 'https://untrusted.invalid/job/2'
    bridge_module._ci_diagnostics(page, PRIVATE, HEAD)
    assert len(calls) == 1


def test_feedback_prompts_poll_checkpoint_and_independently_repair_new_head():
    root = Path(__file__).resolve().parents[2]
    for name in ('security.md', 'security-deepsec.md'):
        text = (root / '.claude/runner-prompts' / name).read_text()
        assert '--status --base <base-sha> --head <fixed-sha>' in text
        assert 'untrusted diagnostic' in text and 'NEW tested head' in text
        assert 'Do not blindly amend/retry' in text


def test_feedback_publication_recovers_lost_ref_response_without_duplicate_write(tmp_path, monkeypatch):
    bridge = _bridge(tmp_path)
    bridge._terminal_marker(bridge.directory / f'{HEAD}.json', BASE, HEAD, 'private-review-rejected')
    refs, _, calls = _feedback_api(monkeypatch, bridge)
    api = bridge._api
    def lost_response(path, *args, **kwargs):
        result = api(path, *args, **kwargs)
        if path.endswith('git/refs'): raise RuntimeError('response lost after acceptance')
        return result
    monkeypatch.setattr(bridge, '_api', lost_response)
    bridge.flush_feedback({}); bridge.flush_feedback({})
    assert len(refs) == 1
    assert sum(path.endswith('git/refs') for path, _, _ in calls) == 1


def test_feedback_never_rewrites_a_conflicting_immutable_ref(tmp_path, monkeypatch):
    bridge = _bridge(tmp_path)
    marker = bridge.directory / f'{HEAD}.json'
    bridge._terminal_marker(marker, BASE, HEAD, 'private-review-rejected')
    refs, _, calls = _feedback_api(monkeypatch, bridge)
    bridge.flush_feedback({}); before = dict(refs)
    data = json.loads(marker.read_text()); del data['feedback_published']
    data['feedback']['status'] = 'private-review-invalid'
    bridge._replace_marker(marker, data)
    with pytest.raises(PermissionError, match='immutable'): bridge.flush_feedback({})
    assert refs == before
    assert sum(path.endswith('git/refs') for path, _, _ in calls) == 1


@pytest.mark.parametrize('stage', ['private', 'public'])
def test_invalid_review_feedback_explains_context_capacity_failure(tmp_path, monkeypatch, stage):
    github = FakeGitHub()
    reviewer = FakeReviewer()
    monkeypatch.setattr(bridge_module.subprocess, 'run', lambda args, **kwargs: subprocess.CompletedProcess(args, 0, stdout=DIFF))
    bridge = _bridge(tmp_path, github=github, reviewer=reviewer)
    if stage == 'public':
        assert bridge.run_once() == 'public-ci-pending'
        github.public_check_runs = _green_runs()
    reviewer.failure_reason = 'Full review source exceeds capacity; reduce the atomic patch while retaining complete context.'
    reviewer.review = lambda *a, **k: []
    assert bridge.run_once() == 'private-validation-failed'
    feedback = json.loads((bridge.directory / f'{HEAD}.json').read_text())['feedback']
    assert feedback['status'] == f'{stage}-review-invalid'
    assert 'capacity' in feedback['findings'][0]['message']
    assert feedback['findings'][0]['reviewer'] == 'controller'


@pytest.mark.parametrize('failure_stage', ['probe', 'source'])
def test_transient_real_review_probe_retries_same_private_head_without_terminal_feedback(tmp_path, monkeypatch, failure_stage):
    import codex_capability_probe as probe
    reviewer = ur.CodexReviews(_policy(), tmp_path / 'reviews')
    binary = tmp_path / 'codex'; binary.write_text('signed-binary-fixture'); binary.chmod(0o500)
    if failure_stage == 'probe':
        monkeypatch.setattr(reviewer, '_review_payload', lambda *a, **k: json.dumps({'changed_files': []}))
    else:
        raw = b'def repaired():\n    return True\n'
        blob = hashlib.sha1(f'blob {len(raw)}\0'.encode() + raw).hexdigest()
        objects = {
            f'repos/{PRIVATE}/git/commits/{revision}': {
                'sha': revision, 'tree': {'sha': tree},
                # A real commit object carries these, and the reviewer now
                # reads them: _publish transfers the commit object, not just
                # its tree (DS-2026-10-17-21).
                'message': 'fix: repair the admission check',
                'author': {'name': 'radon bot', 'email': 'bot@example.test', 'date': '2026-10-18T00:00:00Z'},
                'committer': {'name': 'radon bot', 'email': 'bot@example.test', 'date': '2026-10-18T00:00:00Z'},
            }
            for revision, tree in ((BASE, '1' * 40), (HEAD, '2' * 40))
        }
        objects.update({
            f'repos/{PRIVATE}/git/trees/{"1" * 40}?recursive=1': {'sha': '1' * 40, 'truncated': False, 'tree': []},
            f'repos/{PRIVATE}/git/trees/{"2" * 40}?recursive=1': {'sha': '2' * 40, 'truncated': False, 'tree': [
                {'path': 'scripts/repaired.py', 'type': 'blob', 'mode': '100644', 'sha': blob, 'size': len(raw)}]},
            f'repos/{PRIVATE}/git/blobs/{blob}': {'sha': blob, 'size': len(raw), 'encoding': 'base64', 'content': base64.b64encode(raw).decode()},
        })
        fetches = []
        def intermittent_source(path):
            fetches.append(path)
            if len(fetches) == 1: raise RuntimeError('temporary GitHub fetch unavailable')
            return objects[path]
        monkeypatch.setattr(reviewer, '_api', intermittent_source)
    monkeypatch.setattr(reviewer, '_model_context', lambda model: {'context_window': 272000, 'effective_context_window_percent': 95})
    monkeypatch.setattr(probe, 'snapshot_binary', lambda *a: binary)
    monkeypatch.setattr(probe, 'assert_subscription', lambda *a: None)
    attempts = []
    def intermittent_probe(*args, **kwargs):
        attempts.append(kwargs['prompt'])
        if failure_stage == 'probe' and len(attempts) == 1: raise RuntimeError('temporary local probe unavailable')
    monkeypatch.setattr(probe, 'assert_tool_free', intermittent_probe)
    def inference(args, **kwargs):
        Path(args[args.index('--output-last-message') + 1]).write_text(json.dumps({'approved': True, 'findings': []}))
        return ''
    monkeypatch.setattr(ur, '_command', inference)
    monkeypatch.setattr(bridge_module.subprocess, 'run', lambda args, **kwargs: subprocess.CompletedProcess(args, 0, stdout=DIFF))
    github = FakeGitHub()
    bridge = _bridge(tmp_path, github=github, reviewer=reviewer)
    marker = bridge.directory / f'{HEAD}.json'
    with pytest.raises(RuntimeError):
        bridge.run_once()
    assert not marker.exists()
    assert not any(path.endswith(('git/refs', '/pulls')) for path, args in github.calls)
    assert bridge.run_once() == 'public-ci-pending'
    saved = json.loads(marker.read_text())
    assert saved['head_sha'] == HEAD and saved['base_sha'] == BASE
    assert saved['status'] == 'public-ci-pending' and 'feedback' not in saved
    assert sum(path == f'repos/{PUBLIC}/pulls' for path, args in github.calls) == 1


def test_export_candidate_refuses_a_candidate_more_than_one_commit_ahead(
    monkeypatch, tmp_path
):
    """`_publish` fetches the candidate head with no depth restriction and
    pushes it into the PUBLIC repository, while review covers only the
    aggregate base...head diff and its changed blobs. Content committed in an
    intermediate commit and removed before the final tree stays permanently
    reachable in public git history, which makes this the loop's own rail-7
    disclosure channel. A squash would break the deliberate SHA pinning, so a
    multi-commit candidate is refused instead.
    """
    key = tmp_path / ".radon-runner-reports-key"
    key.write_text("synthetic-private-key", encoding="utf-8")
    key.chmod(0o600)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    def private_archive_404(*args, **kwargs):
        raise urllib.error.HTTPError("url", 404, "private fixture", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", private_archive_404)
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if "rev-list" in args:
            return subprocess.CompletedProcess(args, 0, stdout="2\n")
        return subprocess.CompletedProcess(args, 0, stdout="")

    monkeypatch.setattr(bridge_module.subprocess, "run", fake_run)

    with pytest.raises(ValueError, match="one commit"):
        bridge_module.export_candidate(BASE, HEAD, severity="P1", cwd=tmp_path)

    assert not any("--atomic" in args for args in calls), (
        "a multi-commit candidate must be refused before anything is pushed"
    )


def test_export_candidate_accepts_a_single_commit_candidate(monkeypatch, tmp_path):
    key = tmp_path / ".radon-runner-reports-key"
    key.write_text("synthetic-private-key", encoding="utf-8")
    key.chmod(0o600)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    def private_archive_404(*args, **kwargs):
        raise urllib.error.HTTPError("url", 404, "private fixture", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", private_archive_404)
    pushed = []

    def fake_run(args, **kwargs):
        if "--atomic" in args:
            pushed.append(args)
        if "rev-list" in args:
            return subprocess.CompletedProcess(args, 0, stdout="1\n")
        return subprocess.CompletedProcess(args, 0, stdout="")

    monkeypatch.setattr(bridge_module.subprocess, "run", fake_run)

    result = bridge_module.export_candidate(BASE, HEAD, severity="P1", cwd=tmp_path)

    assert result == {"status": "private-validation-pending", "head_sha": HEAD}
    assert len(pushed) == 1


def test_bridge_multi_commit_rejection_persists_compatible_actionable_feedback(tmp_path, monkeypatch):
    github = FakeGitHub()
    github.comparison['ahead_by'] = 2
    reviewer = FakeReviewer()
    bridge = _bridge(tmp_path, github=github, reviewer=reviewer)
    monkeypatch.setattr(bridge, '_publish', lambda *args: pytest.fail('invalid candidate must never publish'))
    monkeypatch.setattr(bridge_module.subprocess, 'run', lambda *args, **kwargs: pytest.fail('invalid candidate must not fetch diff or execute Git'))

    assert bridge.run_once() == 'private-validation-failed'
    marker = bridge.directory / f'{HEAD}.json'
    saved = json.loads(marker.read_text())
    # Existing schema/readers already understand this invalid review input.
    assert saved['status'] == 'private-review-invalid'
    feedback = saved['feedback']
    assert feedback['status'] == saved['status']
    assert bridge_module._validate_feedback(feedback, BASE, HEAD)
    assert 'exactly one commit' in feedback['findings'][0]['message']
    assert reviewer.calls == []
    assert not any(path.startswith((f'repos/{PUBLIC}/pulls', f'repos/{PUBLIC}/git/refs')) for path, _ in github.calls)

    comparisons = sum('/compare/' in path for path, _ in github.calls)
    assert bridge.run_once() == 'private-validation-failed'
    assert sum('/compare/' in path for path, _ in github.calls) == comparisons
    refs, _, calls = _feedback_api(monkeypatch, bridge)
    bridge.flush_feedback({}); bridge.flush_feedback({})
    assert list(refs) == [f'refs/heads/validation/feedback/{BASE}/{HEAD}']
    assert sum(path.endswith('git/refs') for path, _, _ in calls) == 1
    assert json.loads(marker.read_text())['feedback_published'] == refs[f'refs/heads/validation/feedback/{BASE}/{HEAD}']


_CI_PATH = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
_PR = "  pull_request:\n"
_PUSH = "  push:\n    branches: [main]\n"
_MERGE_GROUP = "  merge_group:\n    branches: [main]\n"
_VALIDATION_ON = "on:\n  pull_request:\n    branches: ['validation/base/**']\n"
_WORKFLOW_TAIL = (
    "jobs:\n"
    "  tests:\n"
    "    if: github.event_name == 'push'\n"
    "    # on:\n"
    "    #   pull_request:\n"
    "    #   push:\n"
    "    #     branches: [main]\n"
)


def _workflow(on_body):
    block = on_body if on_body.startswith("on:") else "on:\n" + on_body
    if not block.endswith("\n"):
        block += "\n"
    return "name: CI\n\n" + block + "\n" + _WORKFLOW_TAIL


def _validation_ci(tmp_path, source):
    github = FakeGitHub()
    github.workflow_sources[github.workflow_paths[0]] = source
    _bridge(tmp_path, github=github).ensure_validation_workflows(BASE)
    _archive, entries = github.workflow_pushes[0]
    return next(entry["content"] for entry in entries if entry["path"].endswith("/ci.yml"))


def _assert_validation_trigger(source, rewritten):
    head = source[: source.index("\non:") + 1]
    assert rewritten.startswith(head)
    trigger, separator, tail = rewritten[len(head) :].partition("\n" + _WORKFLOW_TAIL)
    assert separator
    assert trigger == _VALIDATION_ON
    assert tail == ""
    assert "merge_group" not in trigger
    assert "push:" not in trigger


@pytest.mark.parametrize(
    "on_body",
    [
        _PR + _PUSH,
        _MERGE_GROUP + _PR + _PUSH,
        _PR + _MERGE_GROUP + _PUSH,
        _PR + _PUSH + _MERGE_GROUP,
        _PR + "  # queue trigger, any position\n" + _MERGE_GROUP + _PUSH,
        _PUSH + _PR,
        _PR + "  push:\n    branches:\n      - main\n",
        _PR + "  push: {branches: [main]}\n",
        _PR + "  push:\n    branches: ['main']\n",
        _PR + '  push:\n    branches: ["main"]\n',
        _PR + "  push:\n    branches: [main,]\n" + _MERGE_GROUP,
        _PR.replace("pull_request:", "pull_request: {}") + _PUSH,
        _PR.replace("pull_request:", "pull_request: null") + _PUSH,
        "on: {pull_request: null, push: {branches: [main]}}\n",
        "on: # public contract\n" + _PR + _MERGE_GROUP + _PUSH,
        _PR + _PUSH + "  workflow_dispatch:\n",
        _PR + _PUSH + "  schedule:\n    - cron: '17 */6 * * *'\n",
    ],
    ids=(
        "merge-group-absent",
        "merge-group-before",
        "merge-group-between",
        "merge-group-after",
        "merge-group-with-comment",
        "push-before-pull-request",
        "block-branch-list",
        "flow-push",
        "single-quoted-main",
        "double-quoted-main",
        "trailing-comma",
        "empty-pull-request-map",
        "null-pull-request",
        "inline-on-map",
        "comment-on-header",
        "extra-workflow-dispatch",
        "extra-schedule",
    ),
)
def test_validation_workflow_accepts_merge_group_in_any_position(tmp_path, on_body):
    source = _workflow(on_body)
    rewritten = _validation_ci(tmp_path, source)
    _assert_validation_trigger(source, rewritten)


def test_current_public_ci_workflow_matches_validation_event_contract(tmp_path):
    source = _CI_PATH.read_text(encoding="utf-8")
    rewritten = _validation_ci(tmp_path, source)
    head = source[: source.index("\non:\n") + 1]
    kept = source[source.index("# Least privilege:") :]
    assert rewritten.startswith(head)
    assert rewritten.endswith(kept)
    assert rewritten[: len(rewritten) - len(kept)] == head + _VALIDATION_ON + "\n"
    assert bridge_module._public_ci_events(rewritten) == {
        "pull_request": {"branches": ["validation/base/**"]}
    }


def test_public_ci_event_parser_matches_pyyaml_for_the_checked_in_workflow():
    import yaml

    source = _CI_PATH.read_text(encoding="utf-8")
    assert bridge_module._public_ci_events(source) == yaml.safe_load(source)[True]


@pytest.mark.parametrize(
    "on_body",
    [
        _PR + _MERGE_GROUP + _PUSH,
        _PR + "  push:\n    branches:\n      - main\n",
        "on: {pull_request: null, push: {branches: [main]}, merge_group: {branches: [main]}}\n",
        _PR + _PUSH + "  schedule:\n    - cron: '17 */6 * * *'\n      display: nightly\n",
        "  pull_request:\n    paths:\n      - web/**\n" + _PUSH,
    ],
)
def test_public_ci_event_parser_matches_pyyaml(on_body):
    import yaml

    source = _workflow(on_body)
    assert bridge_module._public_ci_events(source) == yaml.safe_load(source)[True]


@pytest.mark.parametrize(
    "on_body",
    [
        _PR,
        _PUSH,
        "  pull_request:\n    branches: [main]\n" + _PUSH,
        "  pull_request:\n    types: [opened, synchronize, reopened]\n" + _PUSH,
        "  pull_request:\n    paths:\n      - web/**\n" + _PUSH,
        _PR + "  push:\n    branches: [main, develop]\n",
        _PR + "  push:\n    branches: [develop]\n",
        _PR + "  push:\n    branches: [main]\n    paths:\n      - web/**\n",
        _PR + "  push:\n    tags: ['v*']\n",
        _PR + "  push:\n",
        _PR + "  push:\n    branches: [main]\n    branches-ignore: [release]\n",
        "  workflow_dispatch:\n",
        "on: [pull_request, push]\n",
        "on: push\n",
        "name: CI\n\njobs: {}\n",
        "on:\n\tpull_request:\n\tpush:\n\t  branches: [main]\n",
        _PR + "  pull_request:\n" + _PUSH,
        _PR + "  push: |\n    branches: [main]\n",
        "  pull_request: *alias\n" + _PUSH,
    ],
    ids=(
        "missing-push",
        "missing-pull-request",
        "filtered-pull-request-branches",
        "filtered-pull-request-types",
        "filtered-pull-request-paths",
        "extra-push-branch",
        "push-not-main",
        "push-paths",
        "push-tags",
        "bare-push",
        "push-branches-ignore",
        "workflow-dispatch-only",
        "on-list",
        "on-scalar",
        "missing-on",
        "tab-indent",
        "duplicate-pull-request",
        "block-scalar",
        "yaml-alias",
    ),
)
def test_validation_workflow_rejects_incompatible_ci_event_contract(tmp_path, on_body):
    github = FakeGitHub()
    source = on_body if on_body.startswith("name:") else _workflow(on_body)
    github.workflow_sources[github.workflow_paths[0]] = source
    with pytest.raises(RuntimeError, match="unrecognized public CI event contract"):
        _bridge(tmp_path, github=github).ensure_validation_workflows(BASE)
    assert github.workflow_pushes == []
    assert github.private_variables == {}


def test_validation_copy_is_not_a_public_ci_event_contract():
    with pytest.raises(RuntimeError, match="unrecognized public CI event contract"):
        bridge_module._ci_workflow_for_validation(_VALIDATION_ON)


def test_stale_unpublished_candidate_gets_private_revalidation_feedback(tmp_path):
    stale = "e" * 40
    github = FakeGitHub(refs=[_ref(base=stale)])
    reviewer = FakeReviewer()
    bridge = _bridge(tmp_path, github=github, reviewer=reviewer)

    assert bridge.run_once() == "private-validation-failed"
    marker = bridge.directory / f"{HEAD}.json"
    saved = json.loads(marker.read_text())
    assert saved["base_sha"] == stale and saved["head_sha"] == HEAD
    feedback = saved["feedback"]
    assert feedback["status"] == "private-review-invalid"
    assert feedback["release"] is None
    assert feedback["checks"] == [] and feedback["review_input_digests"] == []
    assert "revalidate" in feedback["findings"][0]["message"].lower()
    assert BASE in feedback["findings"][0]["message"]
    bridge_module._validate_feedback(feedback, stale, HEAD)
    assert reviewer.calls == []
    assert github.calls == [(f"repos/{PRIVATE}", ()), (f"repos/{PRIVATE}/git/matching-refs/heads/validation/fix/", ())]
    assert github.workflow_pushes == []
    assert bridge.run_once() == "waiting"
    assert json.loads(marker.read_text()) == saved


@pytest.mark.parametrize("published", [False, True])
def test_stale_candidate_preserves_existing_feedback_marker(tmp_path, published):
    stale = "e" * 40
    github = FakeGitHub(refs=[_ref(base=stale)])
    bridge = _bridge(tmp_path, github=github)
    marker = bridge.directory / f"{HEAD}.json"
    if published:
        bridge._save_marker(marker, {"base_sha": stale, "head_sha": HEAD, "status": "public-ci-pending", "public_pr": f"https://github.com/{PUBLIC}/pull/17"})
    else:
        bridge._terminal_marker(marker, stale, HEAD, "private-checks-failed", diagnostics=("Original immutable feedback",))
    before = marker.read_bytes()
    assert bridge.run_once() == "waiting"
    assert marker.read_bytes() == before
