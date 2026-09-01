"""Format GitHub PR titles and bodies for Radon nightly (and related) loops.

The five unattended loops open PRs. Operators read those PRs for three
things: what went wrong, what this PR changed, and whether anything still
must happen outside of CI pushing a new deployment. Audit tables, SHA
ranges, finding inventories and gate counts belong on the rolling GitHub
issue and in the loop ledgers, not here.

This module does not post issues, issue comments, or Pushover pages.
"""

from __future__ import annotations

import argparse
import json
import sys

LOOP_TITLES = {
    "reliability": "Reliability",
    "testing": "Testing",
    "documentation": "Documentation",
    "ci-performance": "CI Performance",
    "security": "Security",
}

ISSUE_HEADING = "Issue discovered"
FIX_HEADING = "What was done to fix it"
NEXT_HEADING = "Next"
GREEN_DEPLOYMENT = "Fixed with green deployment"
GITHUB_PR_TITLE_MAX = 256


def _clean(value: str, *, field: str) -> str:
    text = " ".join((value or "").split())
    if not text:
        raise ValueError(f"{field} must be non-empty plain language")
    return text


def format_pr_title(*, loop: str, date: str, issue: str) -> str:
    """`Reliability 2026-09-01: the handshake froze Gateway`."""
    prefix = LOOP_TITLES.get(loop)
    if prefix is None:
        known = ", ".join(sorted(LOOP_TITLES))
        raise ValueError(f"unknown loop {loop!r}; expected one of: {known}")
    day = _clean(date, field="date")
    summary = _clean(issue, field="issue")
    title = f"{prefix} {day}: {summary}"
    if len(title) > GITHUB_PR_TITLE_MAX:
        return title[:GITHUB_PR_TITLE_MAX]
    return title


def format_pr_body(
    *,
    issue: str,
    fix: str,
    next_action: str | None = None,
) -> str:
    """Three sections, in order. Default Next is a green deployment."""
    issue_text = _clean(issue, field="issue")
    fix_text = _clean(fix, field="fix")
    leftover = " ".join((next_action or "").split())
    next_text = leftover or GREEN_DEPLOYMENT
    return (
        f"## {ISSUE_HEADING}\n\n"
        f"{issue_text}\n\n"
        f"## {FIX_HEADING}\n\n"
        f"{fix_text}\n\n"
        f"## {NEXT_HEADING}\n\n"
        f"{next_text}\n"
    )


def render(*, loop: str, date: str, issue: str, fix: str, next_action: str | None) -> dict[str, str]:
    return {
        "title": format_pr_title(loop=loop, date=date, issue=issue),
        "body": format_pr_body(issue=issue, fix=fix, next_action=next_action),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Format a nightly-loop GitHub PR title and body."
    )
    parser.add_argument("--loop", required=True, choices=sorted(LOOP_TITLES))
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--issue", required=True, help="what went wrong, in plain language")
    parser.add_argument("--fix", required=True, help="what this PR actually changed")
    parser.add_argument(
        "--next",
        dest="next_action",
        default=None,
        help="only an action outside CI pushing a new deployment",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit {title, body} JSON (not a POST /pulls create payload)",
    )
    args = parser.parse_args(argv)
    payload = render(
        loop=args.loop,
        date=args.date,
        issue=args.issue,
        fix=args.fix,
        next_action=args.next_action,
    )
    if args.json:
        json.dump(payload, sys.stdout)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(f"{payload['title']}\n\n{payload['body']}")
        if not payload["body"].endswith("\n"):
            sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(2) from exc
