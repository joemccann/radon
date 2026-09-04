"""The newest CI-green `main` SHA a nightly loop should execute.

REL-187 (R-519): every loop reset its clone to the raw tip of `origin/main`,
so a loop that fired minutes after a red push spent its whole cycle auditing,
remediating and testing against a tree CI had already rejected — and the
resulting PR mixed the loop's own work with someone else's broken commit.

Stale-but-green beats fresh-but-red. When GitHub cannot be reached the answer
is empty and the caller keeps the tip it already has, with a logged warning:
an unreachable API must never stop a nightly run.

Stdlib only, 3.9-clean (invoked via ``python3 -I -``, same as
``nightly_issue_prune.py``). ``gh`` and ``git`` do all the I/O.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

GATE_WORKFLOW = "ci.yml"
GATE_BRANCH = "main"
#: How far back to look for a green push run. Beyond this the tree is old
#: enough that running against the tip is the better trade.
LOOKBACK = 20


def green_main_push_shas(repo: str, *, gh_bin: str, timeout: int) -> list:
    """head_sha of the newest successful push runs of the gate workflow.

    A push run of ci.yml concludes `success` only when every job that ran
    passed, so this answers exactly the question the caller asks: did the gate
    pass at that SHA.
    """
    query = (
        "repos/%s/actions/workflows/%s/runs"
        "?branch=%s&event=push&status=success&per_page=%d"
        % (repo, GATE_WORKFLOW, GATE_BRANCH, LOOKBACK)
    )
    try:
        result = subprocess.run(
            [gh_bin, "api", query, "--jq", ".workflow_runs[].head_sha"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    return result.stdout.split()


def is_ancestor(sha: str, head: str, *, repo_dir: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", repo_dir, "merge-base", "--is-ancestor", sha, head],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def resolve(head, candidates, ancestor_of):
    """The newest candidate `head` descends from, or '' when none qualifies."""
    for sha in candidates:
        if sha and ancestor_of(sha, head):
            return sha
    return ""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Print the newest CI-green main SHA this clone descends from."
    )
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--repo-dir", required=True)
    parser.add_argument("--head", default="origin/main")
    parser.add_argument("--gh-bin", default="gh")
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args(argv)

    shas = green_main_push_shas(args.repo, gh_bin=args.gh_bin, timeout=args.timeout)
    if not shas:
        print("no green run reachable; keeping the current tip", file=sys.stderr)
        return 0
    resolved = resolve(
        args.head, shas, lambda sha, head: is_ancestor(sha, head, repo_dir=args.repo_dir)
    )
    if not resolved:
        print("no green run is an ancestor of the tip; keeping it", file=sys.stderr)
        return 0
    print(resolved)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
