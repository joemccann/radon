#!/usr/bin/env python3
"""Classify net nightly changes and publish only substantive pull requests.

Stdlib only. No worktree/index mutation, no force push, no PR closure or merge.
Exit 0 means substantive/published, 3 means no substantive change, 1 error.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


BOOKKEEPING_PATHS = frozenset({
    "RELIABILITY_AUDIT.md", "RELIABILITY_LOG.md", "REMEDIATION_LOG.md",
    "TEST_AUDIT.md", "TEST_LOG.md", "CI_PERFORMANCE_LOG.md",
    "SECURITY_AUDIT.md", "SECURITY_LOG.md",
    "tasks/todo.md", "tasks/lessons.md",
})
CODEMAP_PATHS = frozenset({
    "tools/codemap/architecture.json", "tools/codemap/codemap.json",
    "tools/codemap/codemap.data.js",
})


class PublishError(RuntimeError):
    """Insufficient evidence to publish safely."""


def run(args: list[str], repo: Path, *, env: dict[str, str] | None = None) -> str:
    try:
        result = subprocess.run(args, cwd=repo, capture_output=True, text=True, timeout=120, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PublishError(f"Unable to run {args[0]} {args[1]}: {type(exc).__name__}") from exc
    if result.returncode:
        # Upstream diagnostics may contain authenticated remote URLs. Keep
        # output machine-readable and do not copy credentials into run logs.
        raise PublishError(f"{args[0]} {args[1]} failed (exit {result.returncode})")
    return result.stdout


def commit_sha(ref: str, repo: Path) -> str:
    return run(["git", "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"], repo).strip()


def _entry(tree: str, path: str, repo: Path) -> tuple[str, str] | None:
    raw = run(["git", "ls-tree", "-z", tree, "--", path], repo)
    if not raw:
        return None
    mode, kind, oid = raw.split("\t", 1)[0].split()
    if kind != "blob":
        raise PublishError(f"Unsupported generated artifact type: {path}")
    return mode, oid


def _codemap_content(oid: str, path: str, repo: Path) -> Any:
    content = run(["git", "cat-file", "blob", oid], repo)
    if path.endswith(".js"):
        prefix = "window.CODEMAP = "
        stripped = content.strip()
        if not stripped.startswith(prefix) or not stripped.endswith(";"):
            raise PublishError(f"Invalid codemap wrapper: {path}")
        content = stripped[len(prefix):-1]
    try:
        data = json.loads(content)
    except (ValueError, TypeError) as exc:
        raise PublishError(f"Invalid codemap JSON: {path}") from exc
    if not isinstance(data, dict):
        raise PublishError(f"Invalid codemap object: {path}")
    if path.endswith("architecture.json"):
        data.pop("generated_at", None)
    else:
        meta = data.get("meta")
        if not isinstance(meta, dict):
            raise PublishError(f"Invalid codemap metadata: {path}")
        meta.pop("generated_at", None)
    return data


def _generated_change_is_substantive(base: str, tree: str, path: str, repo: Path) -> bool:
    before, after = _entry(base, path, repo), _entry(tree, path, repo)
    if before is None or after is None or before[0] != after[0]:
        return True
    return _codemap_content(before[1], path, repo) != _codemap_content(after[1], path, repo)


def classify(base: str, head: str = "HEAD", *, repo: Path, index: bool = False) -> dict[str, Any]:
    base_sha, head_sha = commit_sha(base, repo), commit_sha(head, repo)
    if index:
        if base_sha != head_sha:
            raise PublishError("Index checks require base and head to resolve to the same commit; use --base HEAD")
        # write-tree can update the index's cache-tree extension. Read a copy
        # so even that metadata never changes the operator's staging area.
        index_path = Path(run(["git", "rev-parse", "--git-path", "index"], repo).strip())
        if not index_path.is_absolute():
            index_path = repo / index_path
        with tempfile.TemporaryDirectory(prefix="nightly-index-") as temporary:
            copied = Path(temporary) / "index"
            shutil.copyfile(index_path, copied)
            tree = run(["git", "write-tree"], repo, env={**os.environ, "GIT_INDEX_FILE": str(copied)}).strip()
    else:
        # Compare the result of merging into today's base, not the historical
        # fork point or base..head. This excludes base-only advances and work
        # already squash-merged. Conflicts fail closed in `run`.
        tree = run(["git", "merge-tree", "--write-tree", base_sha, head_sha], repo).splitlines()[0].strip()
    paths = run(["git", "diff", "--name-only", "--no-renames", "-z", base_sha, tree, "--"], repo).split("\0")
    substantive, ignored = [], []
    for path in sorted(filter(None, paths)):
        if path in BOOKKEEPING_PATHS or path.startswith("tasks/"):
            ignored.append(path)
        elif path in CODEMAP_PATHS and not _generated_change_is_substantive(base_sha, tree, path, repo):
            ignored.append(path)
        else:
            substantive.append(path)
    return {
        "status": "substantive" if substantive else "noop",
        "reason": "substantive_changes" if substantive else "no_substantive_changes",
        "base_sha": base_sha, "head_sha": head_sha, "tree_sha": tree,
        "paths": substantive, "ignored_paths": ignored,
    }


def _json_command(args: list[str], repo: Path) -> Any:
    try:
        return json.loads(run(args, repo))
    except ValueError as exc:
        raise PublishError(f"Invalid JSON from {args[0]} {args[1]}") from exc


def publish(*, base: str, head: str, title: str, body_file: Path, repo: Path) -> dict[str, Any]:
    for branch in (base, head):
        run(["git", "check-ref-format", "--branch", branch], repo)
    if base == head:
        raise PublishError("Publish head must differ from base")
    head_ref = f"refs/heads/{head}"
    head_sha = commit_sha(head_ref, repo)
    run(["git", "fetch", "--no-tags", "origin", f"+refs/heads/{base}:refs/remotes/origin/{base}"], repo)
    verdict = classify(f"refs/remotes/origin/{base}", head_sha, repo=repo)
    if verdict["status"] == "noop":
        return verdict
    if not body_file.is_file():
        raise PublishError("PR body file is unavailable")
    # Readability must be established before publishing a branch.
    body_file.read_text(encoding="utf-8")
    existing = _json_command([
        "gh", "pr", "list", "--state", "open", "--base", base,
        "--head", head, "--json", "url,headRefOid,isCrossRepository", "--limit", "100",
    ], repo)
    if not isinstance(existing, list) or any(
        not isinstance(pr, dict) or not isinstance(pr.get("isCrossRepository"), bool)
        for pr in existing
    ):
        raise PublishError("Cannot establish pull request repository identity")
    existing = [pr for pr in existing if not pr["isCrossRepository"]]
    if len(existing) > 1:
        raise PublishError("Expected at most one open pull request for branch")
    if existing and (not isinstance(existing[0], dict) or not isinstance(existing[0].get("url"), str)):
        raise PublishError("Existing pull request has no URL")
    if commit_sha(head_ref, repo) != head_sha:
        raise PublishError("Branch changed during publication check")
    # Pin the transmitted commit; never push an unreviewed later HEAD or use
    # force. A divergent resumed remote branch must be reconciled first.
    run(["git", "push", "origin", f"{head_sha}:refs/heads/{head}"], repo)
    if existing:
        url = existing[0]["url"]
    else:
        url = run([
            "gh", "pr", "create", "--base", base, "--head", head,
            "--title", title, "--body-file", str(body_file.resolve()),
        ], repo).strip()
    if not url.startswith("https://") or "\n" in url:
        raise PublishError("Publisher did not receive a pull request URL")
    # GitHub may briefly expose the previous head immediately after a push.
    for attempt in range(10):
        pr = _json_command(["gh", "pr", "view", url, "--json", "url,headRefOid,state,baseRefName,headRefName,isCrossRepository"], repo)
        if not isinstance(pr, dict):
            raise PublishError("Invalid pull request response")
        if pr.get("state") != "OPEN" or pr.get("baseRefName") != base or pr.get("headRefName") != head:
            raise PublishError("Pull request target changed during publication")
        if pr.get("isCrossRepository") is not False:
            raise PublishError("Pull request does not belong to the origin repository")
        if pr.get("url") != url:
            raise PublishError("Pull request URL changed during publication")
        if pr.get("headRefOid") == head_sha:
            return {**verdict, "status": "published", "pr_url": pr["url"], "reused": bool(existing)}
        if attempt < 9:
            time.sleep(1)
    raise PublishError("Pull request head does not match checked commit")


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise PublishError(message)


def main(argv: list[str] | None = None) -> int:
    try:
        parser = Parser(description=__doc__)
        sub = parser.add_subparsers(dest="command", required=True, parser_class=Parser)
        check = sub.add_parser("check")
        check.add_argument("--base", default="origin/main")
        check.add_argument("--head", default="HEAD")
        check.add_argument("--index", action="store_true")
        create = sub.add_parser("publish")
        create.add_argument("--base", default="main")
        create.add_argument("--head", required=True)
        create.add_argument("--title", required=True)
        create.add_argument("--body-file", required=True, type=Path)
        args = parser.parse_args(argv)
        repo = Path(run(["git", "rev-parse", "--show-toplevel"], Path.cwd()).strip())
        result = classify(args.base, args.head, repo=repo, index=args.index) if args.command == "check" else publish(
            base=args.base, head=args.head, title=args.title, body_file=args.body_file, repo=repo,
        )
        print(json.dumps(result, sort_keys=True))
        return 3 if result["status"] == "noop" else 0
    except (PublishError, OSError, UnicodeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    sys.exit(main())
