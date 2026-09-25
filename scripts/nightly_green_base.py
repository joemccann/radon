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
import time

GATE_WORKFLOW = "ci.yml"
GATE_BRANCH = "main"
#: How far back to look for a green push run. Beyond this the tree is old
#: enough that running against the tip is the better trade.
LOOKBACK = 20
#: Unfiltered runs fetched per call; PR runs share the listing with main pushes.
UNFILTERED_PAGE = 100
#: A tip this old with no run of its own in the listing means the listing is
#: stale, not that CI has yet to start.
TIP_GRACE_SECS = 3600


def main_push_runs(repo: str, *, gh_bin: str, timeout: int) -> list:
    """(head_sha, conclusion) of the newest main push runs of the gate workflow.

    A push run of ci.yml concludes `success` only when every job that ran
    passed, so a `success` answers exactly the question the caller asks: did
    the gate pass at that SHA.
    """
    # Filtered HERE, not by GitHub. The `branch`/`event`/`status` query
    # parameters are served from a search index, which intermittently answered
    # from a replica stuck near 2026-09-19 14:00Z. Its newest "green" run was
    # e1a0237d, still an ancestor of every later tip, so eight phases between
    # 09-21 and 09-24 were silently rolled back up to five days, onto skills
    # that predated the fixes they needed.
    query = "repos/%s/actions/workflows/%s/runs?per_page=%d" % (repo, GATE_WORKFLOW, UNFILTERED_PAGE)
    select = (
        '.workflow_runs[] | select(.head_branch == "%s" and .event == "push") '
        '| "\\(.head_sha) \\(.conclusion // "pending")"' % GATE_BRANCH
    )
    try:
        result = subprocess.run(
            [gh_bin, "api", query, "--jq", select],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    runs = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if parts:
            runs.append((parts[0], parts[1] if len(parts) > 1 else "success"))
    return runs


def green_main_push_shas(repo: str, *, gh_bin: str, timeout: int) -> list:
    runs = main_push_runs(repo, gh_bin=gh_bin, timeout=timeout)
    return [sha for sha, conclusion in runs if conclusion == "success"][:LOOKBACK]


def _git(repo_dir: str, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", repo_dir, *args], capture_output=True, text=True, check=False, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def listing_is_stale(runs: list, head: str, *, repo_dir: str, now: float) -> bool:
    """True when the listing has no run for a tip that should have one by now.

    Judged by the LISTING, not by how far the green base trails the tip: after
    a quiet weekend a red Monday tip legitimately trails Friday's green by days,
    and its failed run is in the listing.
    """
    tip = _git(repo_dir, "rev-parse", head)
    committed = _git(repo_dir, "log", "-1", "--format=%ct", head)
    if not tip or not committed.isdigit():
        return False
    if any(sha == tip for sha, _ in runs):
        return False
    return now - int(committed) > TIP_GRACE_SECS


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

    runs = main_push_runs(args.repo, gh_bin=args.gh_bin, timeout=args.timeout)
    shas = [sha for sha, conclusion in runs if conclusion == "success"][:LOOKBACK]
    if not shas:
        print("no green run reachable; keeping the current tip", file=sys.stderr)
        return 0
    if listing_is_stale(runs, args.head, repo_dir=args.repo_dir, now=time.time()):
        print(
            "green-base: the run listing has no run for the tip, which is over %ds old; "
            "treating it as stale and keeping the tip (saw green %s)"
            % (TIP_GRACE_SECS, " ".join(s[:12] for s in shas)),
            file=sys.stderr,
        )
        return 0
    resolved = resolve(
        args.head, shas, lambda sha, head: is_ancestor(sha, head, repo_dir=args.repo_dir)
    )
    if not resolved:
        print("no green run is an ancestor of the tip; keeping it", file=sys.stderr)
        return 0
    print("green-base: %s from %d green run(s): %s" % (resolved[:12], len(shas), " ".join(s[:8] for s in shas)), file=sys.stderr)
    print(resolved)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
