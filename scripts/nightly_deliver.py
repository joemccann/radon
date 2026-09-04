#!/usr/bin/env python3
"""Deliver-phase helper shared by the five nightly loops.

The third phase of every nightly cycle (audit -> remediate -> deliver) pushes
the loop's dated branch, opens ONE pull request, waits for CI, fixes what is
red, and tells the operator what is ready to merge. The loop never merges.
This module owns the deterministic parts so each SKILL.md does not re-invent
them:

- ``verdict``: the exact line the skill prints as the phase's last stdout
  line and the wrapper greps (``NIGHTLY DELIVER READY: ...`` /
  ``NIGHTLY DELIVER INCOMPLETE: ...``);
- ``status``: the operator string the wrapper derives from that line, kept
  here so a test can pin the bash and the Python agree
  (``N PR(s) green, ready to merge: <urls>`` / ``INCOMPLETE: <check>``);
- ``watch``: a bounded poll of ``gh pr checks <n> --json name,bucket,link``;
- ``record`` / ``show``: the resume record (branch + PR number + failing
  check) the next fire reads, kept OUTSIDE the clone so a ``git clean``
  cannot reach it.

The wrapper never executes this file (it is agent-writable); the wrapper's
parse of the verdict line is in-main bash. Stdlib only. Nothing here posts an
issue comment or a Pushover page.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

LOOPS = ("reliability", "testing", "ci-performance", "documentation", "security")

READY_PREFIX = "NIGHTLY DELIVER READY:"
INCOMPLETE_PREFIX = "NIGHTLY DELIVER INCOMPLETE:"
NO_VERDICT_STATUS = "INCOMPLETE (exit 0 without the deliver verdict line)"
NOTHING_TO_MERGE = "0 PR(s), nothing to merge"

# `gh pr checks --json` buckets.
_GREEN_BUCKETS = {"pass", "skipping"}
_RED_BUCKETS = {"fail", "cancel"}

_EXIT_FOR_STATE = {"green": 0, "red": 1, "timeout": 3}


def _loop(loop: str) -> str:
    if loop not in LOOPS:
        raise ValueError(f"unknown loop {loop!r}; expected one of: {', '.join(LOOPS)}")
    return loop


def _token(value: str) -> str:
    """One whitespace-free token: the wrapper splits the line on whitespace."""
    return "-".join((value or "").split()) or "unnamed"


# --- verdict line ------------------------------------------------------------


def ready_line(loop: str, urls: list[str]) -> str:
    clean = [u.strip() for u in urls if u and u.strip()]
    line = f"{READY_PREFIX} loop={_loop(loop)} prs={len(clean)}"
    if clean:
        line += " " + " ".join(clean)
    return line


def incomplete_line(loop: str, check: str, pr_url: str | None) -> str:
    line = f"{INCOMPLETE_PREFIX} loop={_loop(loop)} check={_token(check)}"
    if pr_url and pr_url.strip():
        line += f" pr={pr_url.strip()}"
    return line


def last_verdict(text: str) -> str:
    """The last verdict line in a log slice, or an empty string."""
    found = ""
    for line in (text or "").splitlines():
        if line.startswith(READY_PREFIX) or line.startswith(INCOMPLETE_PREFIX):
            found = line.rstrip()
    return found


def notify_status(line: str) -> str:
    """The wrapper's status string for the deliver phase. Mirrors the bash."""
    line = (line or "").strip()
    if line.startswith(READY_PREFIX):
        count = 0
        urls: list[str] = []
        for tok in line[len(READY_PREFIX):].split():
            if tok.startswith("prs="):
                try:
                    count = int(tok[4:])
                except ValueError:
                    count = 0
            elif tok.startswith(("http://", "https://")):
                urls.append(tok)
        if count == 0:
            return NOTHING_TO_MERGE
        return f"{count} PR(s) green, ready to merge: {' '.join(urls)}"
    if line.startswith(INCOMPLETE_PREFIX):
        check = "unnamed check"
        for tok in line[len(INCOMPLETE_PREFIX):].split():
            if tok.startswith("check="):
                check = tok[6:]
        return f"INCOMPLETE: {check}"
    return NO_VERDICT_STATUS


# --- CI watch ----------------------------------------------------------------


def classify_checks(rows: list[dict]) -> tuple[str, list[dict]]:
    """('green'|'red'|'pending', the rows that decided it)."""
    red = [r for r in rows if r.get("bucket") in _RED_BUCKETS]
    if red:
        return "red", red
    pending = [r for r in rows if r.get("bucket") not in _GREEN_BUCKETS]
    if pending or not rows:
        return "pending", pending
    return "green", []


#: Distinct from ``[]``. R-599: an unparseable answer used to read as "no
#: checks reported yet", so a broken `gh` polled for the whole cap and then
#: reported `no-checks-reported` — three hours spent learning nothing. A
#: query that could not run is not a state of the PR.
CHECKS_UNAVAILABLE: list[dict] = []

#: Consecutive query failures after which ``watch`` gives up.
MAX_CONSECUTIVE_QUERY_FAILURES = 3


def gh_pr_checks(pr_number: int) -> list[dict]:
    """Rows, or ``CHECKS_UNAVAILABLE`` when the query itself failed.

    `gh pr checks` exits 8 while pending and 1 on failure, so the exit code
    alone cannot separate "red" from "gh is broken" — but output that is not
    a JSON list can only be the latter.
    """
    try:
        proc = subprocess.run(
            ["gh", "pr", "checks", str(pr_number), "--json", "name,bucket,link"],
            capture_output=True, text=True, check=False, timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return CHECKS_UNAVAILABLE
    try:
        rows = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return CHECKS_UNAVAILABLE
    return rows if isinstance(rows, list) else CHECKS_UNAVAILABLE


def watch(
    pr_number: int,
    *,
    cap_secs: int,
    interval: int = 60,
    run_checks=gh_pr_checks,
    clock=time.monotonic,
    sleep=time.sleep,
) -> dict:
    """Poll until green, red, or the cap. Never longer than ``cap_secs``."""
    start = clock()
    consecutive_failures = 0
    while True:
        rows = run_checks(pr_number)
        if rows is CHECKS_UNAVAILABLE:
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_QUERY_FAILURES:
                return {
                    "state": "timeout",
                    "elapsed_secs": int(clock() - start),
                    "pending": [],
                    "check": (
                        f"gh pr checks unavailable "
                        f"({consecutive_failures} consecutive failures)"
                    ),
                }
            if clock() - start + interval > cap_secs:
                return {
                    "state": "timeout",
                    "elapsed_secs": int(clock() - start),
                    "pending": [],
                    "check": "gh pr checks unavailable at the cap",
                }
            sleep(interval)
            continue
        consecutive_failures = 0
        state, decided = classify_checks(rows)
        elapsed = int(clock() - start)
        if state == "green":
            return {"state": "green", "elapsed_secs": elapsed, "checks": rows}
        if state == "red":
            return {
                "state": "red",
                "elapsed_secs": elapsed,
                "failing": decided,
                "check": decided[0]["name"] if decided else "unnamed",
            }
        if clock() - start + interval > cap_secs:
            return {
                "state": "timeout",
                "elapsed_secs": elapsed,
                "pending": decided,
                "check": decided[0]["name"] if decided else "no-checks-reported",
            }
        sleep(interval)


def exit_code_for(state: str) -> int:
    return _EXIT_FOR_STATE.get(state, 3)


# --- resume record -----------------------------------------------------------


def _root(root: Path | str | None) -> Path:
    if root is not None:
        return Path(root)
    env = os.environ.get("RADON_WEEKEND_ROOT")
    return Path(env) if env else Path.home() / "radon-weekend"


def record_path(loop: str, *, root: Path | str | None = None) -> Path:
    return _root(root) / f".{_loop(loop)}-deliver" / "record.json"


def write_record(
    loop: str,
    *,
    branch: str,
    pr: int | None,
    url: str | None,
    status: str,
    check: str | None = None,
    run_id: str | None = None,
    root: Path | str | None = None,
) -> Path:
    path = record_path(loop, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    record = {
        "loop": loop,
        "branch": branch,
        "pr": int(pr) if pr is not None else None,
        "url": url,
        "status": status,
        "check": check if status != "green" else None,
        "run_id": run_id,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def read_record(loop: str, *, root: Path | str | None = None) -> dict | None:
    path = record_path(loop, root=root)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def resumable(loop: str, *, root: Path | str | None = None) -> dict | None:
    """The record when its deliver did not reach green; else None."""
    record = read_record(loop, root=root)
    if record and record.get("status") != "green" and (
        record.get("pr") or record.get("branch")
    ):
        # R-611: a branch-only record is what the wrapper writes BEFORE the
        # agent starts, so a cap kill mid-phase is resumable from the branch
        # even though no PR number exists yet.
        return record
    return None


def status_from_record(loop: str, *, root: Path | str | None = None) -> str:
    """The operator string, read from the durable record.

    R-613: the wrapper used to grep the agent's own transcript for its
    verdict line, so a marker recited inside agent prose could be mistaken
    for the verdict and a cap kill left nothing behind at all. The record is
    written by the phase itself and survives the kill.
    """
    record = read_record(loop, root=root)
    if record is None:
        return "INCOMPLETE (no deliver record — not resumable)"
    status = record.get("status")
    url = record.get("url")
    if status == "green" and url:
        return f"1 PR(s) green, ready to merge: {url}"
    if status == "green":
        return NOTHING_TO_MERGE
    if record.get("pr") is None:
        return (
            "INCOMPLETE (deliver record has a branch but no PR — "
            f"resume {record.get('branch') or 'the dated branch'})"
        )
    return f"INCOMPLETE: {record.get('check') or 'unnamed check'}"


# --- CLI ---------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Nightly deliver-phase helper.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_verdict = sub.add_parser("verdict", help="print the verdict line (the phase's LAST stdout line)")
    p_verdict.add_argument("--loop", required=True, choices=LOOPS)
    group = p_verdict.add_mutually_exclusive_group(required=True)
    group.add_argument("--ready", nargs="*", metavar="PR_URL", help="every PR is green")
    group.add_argument("--incomplete", metavar="CHECK", help="the check still red or pending at the cap")
    p_verdict.add_argument("--pr-url", default=None)

    p_status = sub.add_parser("status", help="render the operator string for a verdict line")
    p_status.add_argument("--line", required=True)

    p_watch = sub.add_parser("watch", help="poll gh pr checks until green, red, or the cap")
    p_watch.add_argument("--pr", required=True, type=int)
    p_watch.add_argument("--cap-secs", required=True, type=int)
    p_watch.add_argument("--interval", type=int, default=60)

    p_record = sub.add_parser("record", help="write the resume record")
    p_record.add_argument("--loop", required=True, choices=LOOPS)
    p_record.add_argument("--branch", required=True)
    p_record.add_argument("--pr", type=int, default=None)
    p_record.add_argument("--url", default=None)
    p_record.add_argument(
        "--status", required=True,
        choices=("launched", "pending", "incomplete", "green"),
    )
    p_record.add_argument("--check", default=None)
    p_record.add_argument("--run-id", default=None)

    p_show = sub.add_parser("show", help="print the resume record as JSON")
    p_show.add_argument("--loop", required=True, choices=LOOPS)

    p_dstatus = sub.add_parser(
        "deliver-status", help="the operator string, read from the record (R-613)"
    )
    p_dstatus.add_argument("--loop", required=True, choices=LOOPS)

    args = parser.parse_args(argv)

    if args.cmd == "verdict":
        if args.incomplete is not None:
            print(incomplete_line(args.loop, args.incomplete, args.pr_url))
        else:
            print(ready_line(args.loop, args.ready or []))
        return 0
    if args.cmd == "status":
        print(notify_status(args.line))
        return 0
    if args.cmd == "watch":
        verdict = watch(args.pr, cap_secs=args.cap_secs, interval=args.interval)
        json.dump(verdict, sys.stdout)
        sys.stdout.write("\n")
        return exit_code_for(verdict["state"])
    if args.cmd == "record":
        path = write_record(
            args.loop, branch=args.branch, pr=args.pr, url=args.url,
            status=args.status, check=args.check, run_id=args.run_id,
        )
        print(path)
        return 0
    if args.cmd == "deliver-status":
        print(status_from_record(args.loop))
        return 0
    if args.cmd == "show":
        record = read_record(args.loop) or {}
        record["resumable"] = resumable(args.loop) is not None
        json.dump(record, sys.stdout)
        sys.stdout.write("\n")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
