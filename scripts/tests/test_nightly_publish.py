"""Publication gates use net Git trees; examples run only on isolated repos."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts import nightly_publish as subject


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def write(repo: Path, path: str, text: str = "content\n") -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


def commit(repo: Path, message: str = "change") -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "--allow-empty", "-m", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Nightly test")
    git(path, "config", "user.email", "nightly@example.test")
    write(path, "README.md", "base\n")
    commit(path, "base")
    git(path, "switch", "-c", "nightly/test")
    return path


def classify(repo: Path) -> dict:
    return subject.classify("main", "HEAD", repo=repo)


def test_empty_and_empty_commit_are_noop(repo):
    assert classify(repo)["status"] == "noop"
    commit(repo, "empty nightly run")
    assert classify(repo)["status"] == "noop"


@pytest.mark.parametrize("path", sorted(subject.BOOKKEEPING_PATHS))
def test_bookkeeping_alone_does_not_publish(repo, path):
    write(repo, path, "Completed audit with no actionable findings.\n")
    commit(repo)
    result = classify(repo)
    assert result["status"] == "noop"
    assert result["ignored_paths"] == [path]


def test_task_remediation_status_is_bookkeeping(repo):
    write(repo, "tasks/security-remediation-status-2026-09-17.md")
    commit(repo)
    assert classify(repo)["status"] == "noop"


@pytest.mark.parametrize("path", [
    "docs/runbook.md", "scripts/tests/test_feature.py", ".github/workflows/ci.yml",
    ".claude/skills/testing-weekend/SKILL.md", "web/package.json",
    "docs/research/SECURITY_AUDIT.md", "scripts/research/state.py",
])
def test_real_docs_tests_skills_config_and_similarly_named_paths_count(repo, path):
    write(repo, path)
    write(repo, "tasks/todo.md", "done\n")
    commit(repo)
    result = classify(repo)
    assert result["status"] == "substantive"
    assert result["paths"] == [path]
    assert result["ignored_paths"] == ["tasks/todo.md"]


def test_reverted_net_change_is_noop(repo):
    write(repo, "feature.py", "print('new')\n")
    changed = commit(repo)
    git(repo, "revert", "--no-edit", changed)
    assert classify(repo)["status"] == "noop"


@pytest.mark.parametrize("operation", ["delete", "rename"])
def test_deletions_and_renames_are_substantive(repo, operation):
    if operation == "delete":
        (repo / "README.md").unlink()
    else:
        git(repo, "mv", "README.md", "GUIDE.md")
    commit(repo)
    assert classify(repo)["status"] == "substantive"
    assert "README.md" in classify(repo)["paths"]


def test_base_only_advance_does_not_turn_bookkeeping_into_work(repo):
    write(repo, "tasks/todo.md", "done\n")
    commit(repo)
    git(repo, "switch", "main")
    write(repo, "base-only.py")
    commit(repo)
    git(repo, "switch", "nightly/test")
    assert classify(repo)["status"] == "noop"


def test_already_squash_merged_change_is_noop(repo):
    write(repo, "fixed.py")
    commit(repo)
    git(repo, "switch", "main")
    git(repo, "merge", "--squash", "nightly/test")
    commit(repo, "squashed")
    git(repo, "switch", "nightly/test")
    assert classify(repo)["status"] == "noop"


def test_conflict_fails_closed(repo):
    write(repo, "README.md", "feature\n")
    commit(repo)
    git(repo, "switch", "main")
    write(repo, "README.md", "different base\n")
    commit(repo)
    git(repo, "switch", "nightly/test")
    with pytest.raises(subject.PublishError, match="merge-tree failed"):
        classify(repo)


@pytest.mark.parametrize("path", sorted(subject.CODEMAP_PATHS))
def test_codemap_timestamp_only_is_noop_but_graph_changes_count(repo, path):
    def content(stamp, count):
        value = ({"generated_at": stamp, "node_count": count} if path.endswith("architecture.json")
                 else {"meta": {"generated_at": stamp}, "nodes": [{"id": str(count)}], "edges": []})
        raw = json.dumps(value)
        return f"window.CODEMAP = {raw};\n" if path.endswith(".js") else raw
    write(repo, path, content("before", 1))
    commit(repo)
    git(repo, "branch", "-f", "main", "HEAD")
    write(repo, path, content("after", 1))
    commit(repo)
    assert classify(repo)["status"] == "noop"
    write(repo, path, content("after", 2))
    commit(repo)
    assert classify(repo)["paths"] == [path]


def test_index_check_does_not_commit_or_modify_index(repo):
    write(repo, "docs/new.md")
    git(repo, "add", "docs/new.md")
    head, index = git(repo, "rev-parse", "HEAD"), (repo / ".git/index").read_bytes()
    result = subject.classify("HEAD", repo=repo, index=True)
    assert result["paths"] == ["docs/new.md"]
    assert git(repo, "rev-parse", "HEAD") == head
    assert (repo / ".git/index").read_bytes() == index


def test_index_check_rejects_a_different_base(repo):
    write(repo, "tasks/todo.md")
    commit(repo)
    with pytest.raises(subject.PublishError, match="use --base HEAD"):
        subject.classify("main", repo=repo, index=True)


def test_invalid_generated_json_fails_closed(repo):
    path = "tools/codemap/codemap.json"
    write(repo, path, '{"meta":{"generated_at":"old"},"nodes":[],"edges":[]}')
    commit(repo)
    git(repo, "branch", "-f", "main", "HEAD")
    write(repo, path, "{broken")
    commit(repo)
    with pytest.raises(subject.PublishError, match="Invalid codemap JSON"):
        classify(repo)


@pytest.fixture
def remote(repo, tmp_path):
    target = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(target)], check=True, capture_output=True)
    git(repo, "remote", "add", "origin", str(target))
    git(repo, "push", "origin", "main")
    return target


def publication(repo):
    body = repo.parent / "body.md"
    body.write_text("A substantive correction.\n\nValidation: CI.\n")
    return subject.publish(base="main", head="nightly/test", title="Nightly correction", body_file=body, repo=repo)


def mock_gh(monkeypatch, repo, *, existing=False, fail=None):
    original = subject.run
    calls = []
    url = "https://github.com/example/repo/pull/42"
    def run(args, cwd):
        calls.append(args)
        if args[0] != "gh":
            return original(args, cwd)
        if args[2] == fail:
            raise subject.PublishError("gh unavailable")
        if args[2] == "list":
            return json.dumps([{"url": url, "isCrossRepository": False}] if existing else [])
        if args[2] == "create":
            return url + "\n"
        if args[2] == "view":
            return json.dumps({"url": url, "headRefOid": git(repo, "rev-parse", "HEAD"),
                               "state": "OPEN", "baseRefName": "main", "headRefName": "nightly/test",
                               "isCrossRepository": False})
        raise AssertionError(args)
    monkeypatch.setattr(subject, "run", run)
    return calls


def test_noop_publisher_never_pushes_or_calls_github(repo, remote, monkeypatch):
    write(repo, "TEST_AUDIT.md", "No findings\n")
    commit(repo)
    calls = mock_gh(monkeypatch, repo)
    assert publication(repo)["status"] == "noop"
    assert not any(cmd[:2] == ["git", "push"] or cmd[0] == "gh" for cmd in calls)


@pytest.mark.parametrize("existing", [False, True])
def test_publish_pushes_checked_commit_and_reuses_existing_pr(repo, remote, monkeypatch, existing):
    write(repo, "fix.py")
    head = commit(repo)
    calls = mock_gh(monkeypatch, repo, existing=existing)
    result = publication(repo)
    assert result["status"] == "published"
    assert result["head_sha"] == head
    assert result["reused"] is existing
    assert ["git", "push", "origin", f"{head}:refs/heads/nightly/test"] in calls
    assert len([cmd for cmd in calls if cmd[:3] == ["gh", "pr", "create"]]) == (0 if existing else 1)
    assert not any("close" in cmd or "merge" in cmd for cmd in calls)


def test_github_read_failure_prevents_push_and_create(repo, remote, monkeypatch):
    write(repo, "fix.py")
    commit(repo)
    calls = mock_gh(monkeypatch, repo, fail="list")
    with pytest.raises(subject.PublishError, match="gh unavailable"):
        publication(repo)
    assert not any(cmd[:2] == ["git", "push"] or cmd[:3] == ["gh", "pr", "create"] for cmd in calls)


def test_missing_remote_fails_closed_without_github(repo, monkeypatch):
    write(repo, "fix.py")
    commit(repo)
    calls = mock_gh(monkeypatch, repo)
    with pytest.raises(subject.PublishError, match="fetch failed"):
        publication(repo)
    assert not any(cmd[0] == "gh" or cmd[:2] == ["git", "push"] for cmd in calls)


def test_github_create_failure_is_not_reported_as_published(repo, remote, monkeypatch):
    write(repo, "fix.py")
    commit(repo)
    calls = mock_gh(monkeypatch, repo, fail="create")
    with pytest.raises(subject.PublishError, match="gh unavailable"):
        publication(repo)
    assert len([cmd for cmd in calls if cmd[:3] == ["gh", "pr", "create"]]) == 1


def test_diverged_remote_branch_is_not_force_pushed(repo, remote, monkeypatch):
    write(repo, "previous.py")
    commit(repo)
    git(repo, "push", "origin", "HEAD:refs/heads/nightly/test")
    git(repo, "reset", "--hard", "main")
    write(repo, "different.py")
    commit(repo)
    calls = mock_gh(monkeypatch, repo, existing=True)
    with pytest.raises(subject.PublishError, match="push failed"):
        publication(repo)
    assert not any(cmd[:3] == ["gh", "pr", "create"] or "--force" in cmd for cmd in calls)


def test_unrelated_dirty_source_is_not_pushed(repo, remote, monkeypatch):
    write(repo, "fix.py", "committed\n")
    head = commit(repo)
    write(repo, "fix.py", "unfinished local work\n")
    mock_gh(monkeypatch, repo)
    assert publication(repo)["head_sha"] == head
    assert git(repo, "show", "refs/remotes/origin/nightly/test:fix.py") == "committed"


def test_publication_waits_for_exact_head_confirmation(repo, remote, monkeypatch):
    write(repo, "fix.py")
    head = commit(repo)
    mock_gh(monkeypatch, repo)
    original = subject.run
    views = []
    def delayed(args, cwd, **kwargs):
        result = original(args, cwd, **kwargs)
        if args[:3] == ["gh", "pr", "view"]:
            views.append(args)
            if len(views) == 1:
                data = json.loads(result)
                data["headRefOid"] = "0" * 40
                return json.dumps(data)
        return result
    monkeypatch.setattr(subject, "run", delayed)
    monkeypatch.setattr(subject.time, "sleep", lambda _delay: None)
    assert publication(repo)["head_sha"] == head
    assert len(views) == 2


def test_same_named_fork_pr_is_not_reused(repo, remote, monkeypatch):
    write(repo, "fix.py")
    commit(repo)
    calls = mock_gh(monkeypatch, repo)
    original = subject.run
    def fork_list(args, cwd, **kwargs):
        if args[:3] == ["gh", "pr", "list"]:
            return json.dumps([{"url": "https://github.com/example/repo/pull/7", "isCrossRepository": True}])
        return original(args, cwd, **kwargs)
    monkeypatch.setattr(subject, "run", fork_list)
    result = publication(repo)
    assert result["reused"] is False
    assert result["pr_url"].endswith("/42")
    assert len([cmd for cmd in calls if cmd[:3] == ["gh", "pr", "create"]]) == 1


@pytest.mark.parametrize("field,value,error", [
    ("headRefOid", "0" * 40, "head does not match"),
    ("baseRefName", "different", "target changed"),
    ("headRefName", "different", "target changed"),
    ("state", "CLOSED", "target changed"),
    ("isCrossRepository", True, "origin repository"),
    ("url", "https://github.com/example/repo/pull/99", "URL changed"),
])
def test_wrong_or_stale_pr_is_never_reported_published(repo, remote, monkeypatch, field, value, error):
    write(repo, "fix.py")
    commit(repo)
    mock_gh(monkeypatch, repo, existing=True)
    original = subject.run
    def changed_view(args, cwd, **kwargs):
        result = original(args, cwd, **kwargs)
        if args[:3] == ["gh", "pr", "view"]:
            data = json.loads(result)
            data[field] = value
            return json.dumps(data)
        return result
    monkeypatch.setattr(subject, "run", changed_view)
    monkeypatch.setattr(subject.time, "sleep", lambda _delay: None)
    with pytest.raises(subject.PublishError, match=error):
        publication(repo)


def test_cli_is_json_with_explicit_noop_and_error_exits(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    assert subject.main(["check", "--base", "main"]) == 3
    assert json.loads(capsys.readouterr().out)["status"] == "noop"
    assert subject.main(["check", "--base", "missing"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "error"


def test_cli_from_subdirectory_classifies_repo_relative_paths(repo, monkeypatch, capsys):
    write(repo, "tools/codemap/architecture.json", '{"generated_at":"old","node_count":1}')
    commit(repo)
    git(repo, "branch", "-f", "main", "HEAD")
    write(repo, "tools/codemap/architecture.json", '{"generated_at":"new","node_count":1}')
    commit(repo)
    (repo / "web").mkdir()
    monkeypatch.chdir(repo / "web")
    assert subject.main(["check", "--base", "main"]) == 3
    assert json.loads(capsys.readouterr().out)["ignored_paths"] == ["tools/codemap/architecture.json"]


@pytest.mark.parametrize("response,error", [
    ("not json", "Invalid JSON"),
    ("{}", "repository identity"),
    ('["unexpected"]', "repository identity"),
    ('[{"url":"https://github.com/example/repo/pull/42"}]', "repository identity"),
    ('[{"isCrossRepository":false}]', "no URL"),
    ('[{"isCrossRepository":false,"url":"one"},{"isCrossRepository":false,"url":"two"}]', "at most one"),
])
def test_untrusted_github_listing_never_pushes_or_creates(repo, remote, monkeypatch, response, error):
    write(repo, "fix.py")
    commit(repo)
    calls = mock_gh(monkeypatch, repo)
    original = subject.run
    def listing(args, cwd, **kwargs):
        result = original(args, cwd, **kwargs)
        return response if args[:3] == ["gh", "pr", "list"] else result
    monkeypatch.setattr(subject, "run", listing)
    with pytest.raises(subject.PublishError, match=error):
        publication(repo)
    assert not any(cmd[:2] == ["git", "push"] or cmd[:3] == ["gh", "pr", "create"] for cmd in calls)


def test_branch_race_never_pushes_unchecked_work(repo, remote, monkeypatch):
    write(repo, "fix.py")
    commit(repo)
    calls = mock_gh(monkeypatch, repo)
    original = subject.run
    def racing(args, cwd, **kwargs):
        result = original(args, cwd, **kwargs)
        if args[:3] == ["gh", "pr", "list"]:
            write(repo, "arrived-during-check.py")
            commit(repo)
        return result
    monkeypatch.setattr(subject, "run", racing)
    with pytest.raises(subject.PublishError, match="Branch changed"):
        publication(repo)
    assert not any(cmd[:2] == ["git", "push"] or cmd[:3] == ["gh", "pr", "create"] for cmd in calls)


@pytest.mark.parametrize("bad_body", ["missing", "invalid_utf8"])
def test_unreadable_body_cannot_publish_a_branch(repo, remote, monkeypatch, bad_body):
    write(repo, "fix.py")
    commit(repo)
    calls = mock_gh(monkeypatch, repo)
    body = repo.parent / "bad-body.md"
    if bad_body == "invalid_utf8":
        body.write_bytes(b"\xff")
    with pytest.raises((subject.PublishError, UnicodeError)):
        subject.publish(base="main", head="nightly/test", title="Fix", body_file=body, repo=repo)
    assert not any(cmd[:2] == ["git", "push"] or cmd[0] == "gh" for cmd in calls)


def test_main_branch_cannot_be_published_as_its_own_pr(repo, monkeypatch):
    calls = mock_gh(monkeypatch, repo)
    with pytest.raises(subject.PublishError, match="must differ"):
        subject.publish(base="main", head="main", title="Invalid", body_file=repo / "unused", repo=repo)
    assert not any(cmd[:2] == ["git", "push"] or cmd[0] == "gh" for cmd in calls)


@pytest.mark.parametrize("operation,response,error", [
    ("create", "unexpected output", "pull request URL"),
    ("view", "[]", "Invalid pull request response"),
])
def test_malformed_publication_response_is_not_reported_as_success(repo, remote, monkeypatch, operation, response, error):
    write(repo, "fix.py")
    commit(repo)
    calls = mock_gh(monkeypatch, repo)
    original = subject.run
    def malformed(args, cwd, **kwargs):
        result = original(args, cwd, **kwargs)
        return response if args[:3] == ["gh", "pr", operation] else result
    monkeypatch.setattr(subject, "run", malformed)
    with pytest.raises(subject.PublishError, match=error):
        publication(repo)
    assert len([cmd for cmd in calls if cmd[:3] == ["gh", "pr", "create"]]) == 1


@pytest.mark.parametrize("path,content,error", [
    ("tools/codemap/codemap.data.js", "window.OTHER = {};", "wrapper"),
    ("tools/codemap/architecture.json", "[]", "object"),
    ("tools/codemap/codemap.json", '{"nodes":[]}', "metadata"),
])
def test_malformed_generated_updates_fail_closed(repo, path, content, error):
    initial = '{"meta":{"generated_at":"old"},"nodes":[]}'
    if path.endswith(".js"):
        initial = f"window.CODEMAP = {initial};"
    write(repo, path, initial)
    commit(repo)
    git(repo, "branch", "-f", "main", "HEAD")
    write(repo, path, content)
    commit(repo)
    with pytest.raises(subject.PublishError, match=error):
        classify(repo)


@pytest.mark.parametrize("operation", ["add", "delete", "executable"])
def test_generated_artifact_presence_and_mode_changes_are_substantive(repo, operation):
    path = "tools/codemap/architecture.json"
    write(repo, path, '{"generated_at":"same","node_count":1}')
    if operation != "add":
        commit(repo)
        git(repo, "branch", "-f", "main", "HEAD")
        if operation == "delete":
            (repo / path).unlink()
        else:
            git(repo, "config", "core.filemode", "true")
            (repo / path).chmod(0o755)
    commit(repo)
    assert classify(repo)["paths"] == [path]


@pytest.mark.parametrize("failure", [OSError("missing executable"), subprocess.TimeoutExpired("gh", 120)])
def test_process_start_and_timeout_fail_closed_without_leaking_upstream_output(repo, monkeypatch, failure):
    def unavailable(*_args, **_kwargs):
        raise failure
    monkeypatch.setattr(subject.subprocess, "run", unavailable)
    with pytest.raises(subject.PublishError, match="Unable to run gh pr") as caught:
        subject.run(["gh", "pr", "list"], repo)
    assert str(caught.value) == f"Unable to run gh pr: {type(failure).__name__}"


def test_invalid_cli_arguments_produce_error_json_without_side_effects(monkeypatch, capsys):
    monkeypatch.setattr(subject, "run", lambda *_a, **_kw: pytest.fail("No command may run for invalid arguments"))
    assert subject.main(["publish", "--head", "nightly/test"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "error"


def test_publish_cli_returns_verified_pr_json(repo, remote, monkeypatch, capsys):
    write(repo, "fix.py")
    commit(repo)
    mock_gh(monkeypatch, repo)
    body = repo.parent / "body.md"
    body.write_text("Concrete correction and validation.\n")
    monkeypatch.chdir(repo)
    assert subject.main(["publish", "--head", "nightly/test", "--title", "Fix", "--body-file", str(body)]) == 0
    assert json.loads(capsys.readouterr().out)["pr_url"].endswith("/42")
