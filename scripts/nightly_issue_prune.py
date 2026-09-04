"""Prune a nightly loop's rolling dead-man issue once its PR has merged.

Each of the five loops posts one phase comment per ``report()`` call (see
``scripts/nightly_issue_format.py``) to a single rolling "dead-man" GitHub
issue. Nothing ever revisited old comments, so an operator scrolls through
weeks of history to find today's status.

The rule: while this loop still has an OPEN pull request (branch name
starting with ``--branch-prefix``), every existing comment belongs to that
pending run and stays untouched — the operator still needs the full history
until they merge it. Once no PR is open for this loop (merged, closed, or
none was ever needed — a zero-finding night), there is nothing left to keep:
delete every existing comment before the next one posts, so the issue always
shows at most one loop's worth of current status.

Stdlib only, 3.9-clean (invoked via ``python3 -I -``, same as
``weekend_prune.py``). ``gh`` does all network I/O; this module only decides
whether to prune and drives the two ``gh`` calls that do it. Best-effort by
design: the wrapper treats any failure here as non-fatal.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys


def has_open_pr(open_head_refs: list[str], *, branch_prefix: str) -> bool:
    """True when any open PR's head branch belongs to this loop."""
    return any(ref.startswith(branch_prefix) for ref in open_head_refs)


#: How many open PRs the listing asks for. A FULL page is indistinguishable
#: from a truncated one, so it is treated as unknown rather than as "no PR
#: for this loop" — the answer gates a destructive delete.
PR_LIST_LIMIT = 100


def _run(gh_bin: str, args: list[str], *, timeout: int) -> str | None:
    """stdout on success, ``None`` on any failure. Never raises.

    R-596 (P0): this used to return ``""`` for both "gh printed nothing" and
    "gh could not run at all", which made a network outage look exactly like
    "this loop has no open PR" — and that answer authorises deleting every
    comment on the dead-man issue. Failure now has its own value so callers
    can fail CLOSED.
    """
    try:
        proc = subprocess.run(
            [gh_bin, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def open_pr_head_refs(gh_bin: str, *, timeout: int) -> list[str] | None:
    """Open head refs, or ``None`` when the listing could not be trusted.

    Untrusted means either the ``gh`` call failed (non-zero, timeout, unparsable)
    or the page came back full, in which case this loop's PR may simply be off
    the end of it (R-597).
    """
    out = _run(
        gh_bin,
        ["pr", "list", "--state", "open", "--limit", str(PR_LIST_LIMIT), "--json", "headRefName"],
        timeout=timeout,
    )
    if out is None:
        return None
    try:
        rows = json.loads(out or "[]")
    except json.JSONDecodeError:
        return None
    if not isinstance(rows, list):
        return None
    if len(rows) >= PR_LIST_LIMIT:
        return None
    return [row.get("headRefName", "") for row in rows if isinstance(row, dict)]


def issue_comment_ids(gh_bin: str, issue: str, *, timeout: int) -> list[str] | None:
    out = _run(
        gh_bin,
        [
            "api",
            f"repos/{{owner}}/{{repo}}/issues/{issue}/comments",
            "--paginate",
            "--jq",
            ".[].id",
        ],
        timeout=timeout,
    )
    if out is None:
        return None
    return [line.strip() for line in out.splitlines() if line.strip()]


def delete_comment(gh_bin: str, comment_id: str, *, timeout: int) -> bool:
    try:
        proc = subprocess.run(
            [
                gh_bin,
                "api",
                "-X",
                "DELETE",
                f"repos/{{owner}}/{{repo}}/issues/comments/{comment_id}",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prune a nightly loop's dead-man issue once its PR has merged."
    )
    parser.add_argument("--gh-bin", required=True)
    parser.add_argument("--issue", required=True, help="issue number")
    parser.add_argument(
        "--branch-prefix", required=True, help="this loop's dated-branch prefix, e.g. reliability/"
    )
    parser.add_argument("--timeout", type=int, default=10)
    parser.add_argument(
        "--keep",
        action="append",
        default=[],
        help="comment id to preserve — the phase comment just posted (R-612)",
    )
    args = parser.parse_args(argv)

    refs = open_pr_head_refs(args.gh_bin, timeout=args.timeout)
    if refs is None:
        print("skip: open-PR state unknown — pruning nothing", file=sys.stderr)
        return 0
    if has_open_pr(refs, branch_prefix=args.branch_prefix):
        print("skip: an open PR is still pending for this loop", file=sys.stderr)
        return 0

    ids = issue_comment_ids(args.gh_bin, args.issue, timeout=args.timeout)
    if ids is None:
        print("skip: comment listing unknown — pruning nothing", file=sys.stderr)
        return 0
    keep = {str(k).strip() for k in args.keep if str(k).strip()}
    ids = [cid for cid in ids if cid not in keep]
    deleted = sum(1 for cid in ids if delete_comment(args.gh_bin, cid, timeout=args.timeout))
    print(f"pruned {deleted}/{len(ids)} comments", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
