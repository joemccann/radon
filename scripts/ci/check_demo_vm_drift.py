#!/usr/bin/env python3
"""Fail when the demo VM backend has drifted behind main.

The demo VM is deployed by hand from the operator host
(``cloud/scripts/deploy-demo-vm.sh <sha>``); GitHub cannot reach it over SSH.
That deploy writes REVISION, and the VM's public ``/health`` returns it as
``revision``. This check, run off-box against main's full history:

  * fails when ``/health`` is unreachable or reports no valid revision
    (a VM shipped any other way is unobservable, which is the failure);
  * fails when the revision is not an ancestor of main (a branch build);
  * fails when the oldest main commit touching the backend paths that the
    VM lacks is older than ``--max-days``.

Measuring from the OLDEST undeployed change means a fresh commit on top
never resets the clock on an old one.

Stdlib only. Usage:
    python3 scripts/ci/check_demo_vm_drift.py [--max-days 7] [--ref origin/main]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

DEFAULT_URL = "https://demo-api.radon.run/health"
BACKEND_PATHS = ("scripts", "requirements.txt", "pyproject.toml")
DEFAULT_MAX_DAYS = 7.0
_SHA = re.compile(r"^[0-9a-f]{40}$")
_DEPLOY_HINT = "deploy from the operator host: cloud/scripts/deploy-demo-vm.sh <main sha>"


def parse_revision(body: str) -> str | None:
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    revision = payload.get("revision")
    return revision if isinstance(revision, str) and _SHA.fullmatch(revision) else None


def fetch_health(url: str, timeout: float) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "radon-demo-vm-drift"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https URL
        return response.read().decode("utf-8", "replace")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def is_known_revision(repo: Path, revision: str, ref: str) -> bool:
    return _git(repo, "merge-base", "--is-ancestor", revision, ref).returncode == 0


def undeployed_commits(repo: Path, revision: str, ref: str) -> list[tuple[str, int]]:
    """Commits on ``ref`` touching the backend paths that ``revision`` lacks, oldest first."""
    result = _git(repo, "log", "--reverse", "--format=%H %ct", f"{revision}..{ref}", "--", *BACKEND_PATHS)
    if result.returncode != 0:
        raise RuntimeError(f"git log failed: {result.stderr.strip()}")
    commits = []
    for line in result.stdout.splitlines():
        sha, _, stamp = line.partition(" ")
        commits.append((sha, int(stamp)))
    return commits


def evaluate(
    revision: str | None,
    *,
    known: bool,
    undeployed: list[tuple[str, int]],
    now: float,
    max_days: float,
) -> list[str]:
    if revision is None:
        return [f"demo VM /health reports no revision; it was not shipped by deploy-demo-vm.sh. {_DEPLOY_HINT}"]
    if not known:
        return [f"demo VM revision {revision[:12]} is not an ancestor of main. {_DEPLOY_HINT}"]
    if not undeployed:
        return []
    oldest_sha, oldest_ct = undeployed[0]
    lag_days = (now - oldest_ct) / 86_400
    if lag_days <= max_days:
        return []
    return [
        f"demo VM revision {revision[:12]} is {len(undeployed)} backend commit(s) behind main; "
        f"oldest undeployed {oldest_sha[:12]} landed {lag_days:.1f} days ago (limit {max_days:g}). "
        f"{_DEPLOY_HINT}"
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--ref", default="HEAD", help="main's tip (the workflow checks out main)")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--max-days", type=float, default=DEFAULT_MAX_DAYS)
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args(argv)
    repo = Path(args.repo)

    try:
        body = fetch_health(args.url, args.timeout)
    except Exception as exc:  # any network / HTTP failure is a failed check
        print(f"FAIL: {args.url} unreachable: {type(exc).__name__}: {exc}")
        return 1

    revision = parse_revision(body)
    known = bool(revision) and is_known_revision(repo, revision, args.ref)
    undeployed = undeployed_commits(repo, revision, args.ref) if revision and known else []
    problems = evaluate(revision, known=known, undeployed=undeployed, now=time.time(), max_days=args.max_days)
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}")
        return 1
    if undeployed:
        print(f"OK: demo VM at {revision[:12]}, {len(undeployed)} backend commit(s) behind, within {args.max_days:g} days")
    else:
        print(f"OK: demo VM at {revision[:12]}, up to date with main's backend paths")
    return 0


if __name__ == "__main__":
    sys.exit(main())
