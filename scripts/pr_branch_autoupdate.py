#!/usr/bin/env python3
"""Bring every open PR that is behind main up to date, so strict protection never waits on a click.

main requires up-to-date branches (docs/unattended-releases.md). Each merge
puts every other open PR behind. This runs on the Mac mini every five minutes
(config/com.radon.pr-branch-autoupdate.plist) under the operator's gh login:
a branch update from that login starts CI on the new head, which an update
made with a workflow's GITHUB_TOKEN would not.

Drafts, fork PRs, dependabot (it rebases its own) and PRs not based on main
are skipped. The update is GitHub's own merge of main into the branch, bound
to the head we read; a conflict is reported and left for the branch owner.
Stdlib only: it runs from an installed copy under the system interpreter.
"""
from __future__ import annotations

import json
import subprocess
import sys

REPO = "joemccann/radon"
GH = "/opt/homebrew/bin/gh"
SKIPPED_AUTHORS = {"app/dependabot", "dependabot[bot]"}


def run_gh(args: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run([GH, *args], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""
    return proc.returncode, proc.stdout


def update_behind(gh=run_gh, repo: str = REPO) -> list[dict]:
    rc, out = gh(["pr", "list", "--repo", repo, "--state", "open", "--limit", "100", "--json",
                  "number,headRefOid,isDraft,isCrossRepository,baseRefName,author"])
    if rc:
        raise RuntimeError("gh pr list failed")
    results = []
    for row in sorted(json.loads(out), key=lambda r: r["number"]):
        if (row["isDraft"] or row["isCrossRepository"] or row["baseRefName"] != "main"
                or row["author"]["login"] in SKIPPED_AUTHORS):
            continue
        number, head = row["number"], row["headRefOid"]
        rc, behind = gh(["api", f"repos/{repo}/compare/main...{head}", "--jq", ".behind_by"])
        if rc:
            results.append({"pr": number, "status": "compare-failed"})
            continue
        if int(behind.strip() or 0) == 0:
            continue
        rc, _ = gh(["api", "--method", "PUT", f"repos/{repo}/pulls/{number}/update-branch",
                    "-f", f"expected_head_sha={head}"])
        results.append({"pr": number, "status": "updated" if rc == 0 else "update-failed"})
    return results


def main(gh=run_gh) -> int:
    try:
        results = update_behind(gh)
    except (RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}))
        return 1
    for result in results:
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
