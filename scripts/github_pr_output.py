"""Format GitHub PR titles and bodies for Radon nightly (and related) loops.

The five unattended loops open PRs. Operators read those PRs for three
things: what went wrong, what this PR changed, and whether anything still
must happen outside of CI pushing a new deployment. Audit tables, SHA
ranges, finding inventories and gate counts belong on the rolling GitHub
issue and in the loop ledgers, not here.

--issue/--fix/--next take one bullet per line (`- **Component**: what
happened.`), not a single dense paragraph; each bullet's own internal
whitespace collapses but the line breaks between bullets survive. A plain
one-line sentence still works unchanged.

This module does not post issues, issue comments, or Pushover pages.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date

from nightly_issue_format import sanitize

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
_DATE = re.compile(r"\A\d{4}-\d{2}-\d{2}\Z")


def _normalize_multiline(value: str | None) -> str:
    """Collapse repeated whitespace within each line but keep line breaks,
    so a caller-supplied bullet list survives instead of being flattened
    into one paragraph. A plain single-line value is unaffected."""
    lines = (" ".join(line.split()) for line in (value or "").splitlines())
    return "\n".join(line for line in lines if line)


def _clean(value: str, *, field: str) -> str:
    text = _normalize_multiline(value)
    if not text:
        raise ValueError(f"{field} must be non-empty plain language")
    return text


def _title_summary(value: str, *, field: str) -> str:
    """A title is one GitHub line: take the first bullet (or sentence) and
    strip its Markdown so a bulleted --issue still yields a plain title."""
    first_line = _clean(value, field=field).splitlines()[0]
    return first_line.removeprefix("- ").replace("**", "")


def _calendar_date(value: str) -> str:
    day = _clean(value, field="date")
    if not _DATE.fullmatch(day):
        raise ValueError("date must be YYYY-MM-DD")
    try:
        date.fromisoformat(day)
    except ValueError as exc:
        raise ValueError("date must be YYYY-MM-DD") from exc
    return day


def format_pr_title(*, loop: str, date: str, issue: str) -> str:
    """`Reliability 2026-09-01: the handshake froze Gateway`.

    Security titles stay date-only (`Security 2026-09-01`); the issue lives
    only in the body.
    """
    prefix = LOOP_TITLES.get(loop)
    if prefix is None:
        known = ", ".join(sorted(LOOP_TITLES))
        raise ValueError(f"unknown loop {loop!r}; expected one of: {known}")
    day = _calendar_date(date)
    summary = _title_summary(issue, field="issue")
    if loop == "security":
        title = f"{prefix} {day}"
    else:
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
    next_text = _normalize_multiline(next_action) or GREEN_DEPLOYMENT
    return (
        f"## {ISSUE_HEADING}\n\n"
        f"{issue_text}\n\n"
        f"## {FIX_HEADING}\n\n"
        f"{fix_text}\n\n"
        f"## {NEXT_HEADING}\n\n"
        f"{next_text}\n"
    )


def render(*, loop: str, date: str, issue: str, fix: str, next_action: str | None) -> dict[str, str]:
    title = format_pr_title(loop=loop, date=date, issue=issue)
    if loop == "security":
        # Public repo: the body gets the same redaction as the rolling-issue
        # comment (routes, file:line, secrets, accounts).
        issue = sanitize(issue)
        fix = sanitize(fix)
        next_action = sanitize(next_action) if next_action else next_action
    return {
        "title": title,
        "body": format_pr_body(issue=issue, fix=fix, next_action=next_action),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Format a nightly-loop GitHub PR title and body."
    )
    parser.add_argument("--loop", required=True, choices=sorted(LOOP_TITLES))
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument(
        "--issue",
        required=True,
        help="what went wrong: one bullet per line, e.g. '- **X**: happened.'",
    )
    parser.add_argument(
        "--fix",
        required=True,
        help="what this PR actually changed: one bullet per line",
    )
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
