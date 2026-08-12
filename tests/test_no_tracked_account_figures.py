"""Live account state must never be git-tracked.

Claude Security scan 2026-08-11, finding F14: context/memory/fact/ is written by
the runtime agent-memory constructor, and two of its records carried real
brokerage state -- net liquidation value, position count, and the full open
option inventory with short strikes and expiries. The tree is tracked and the
GitHub repository is public, so every clone (and every reader of git history)
got the operator's account size and short-strike inventory.

Two independent signals are checked, because either one alone misses a case:
account-scoped figures catch a hand-written record, and an ib-sync provenance
stamp catches anything the constructor derived from a live account pull -- the
position inventory that leaked here carries no dollar figure at all.

The guard lives here rather than in .git/hooks/pre-commit because hooks are
per-clone and untracked: CI runs this suite, a local hook does not travel.
"""

import json
import pathlib
import re
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Runtime-owned memory trees. Agent memory is regenerated from live ib-sync
# runs, so a new account-bearing record can appear at any time -- the guard has
# to watch the directory, not a list of known-bad filenames.
WATCHED_DIRS = ("context/memory",)

# Account-SCOPED only. A bare "$95M" is not a leak: market-flow lessons quote
# institutional trade sizes, and matching those would train the next editor to
# ignore this test.
ACCOUNT_FIGURE_PATTERNS = (
    re.compile(r"net\s*liq(uidation)?", re.IGNORECASE),
    re.compile(r"buying\s*power", re.IGNORECASE),
    re.compile(r"(account|portfolio)\s+value", re.IGNORECASE),
)

# Provenance stamp written by the memory constructor for records derived from a
# live broker pull.
LIVE_ACCOUNT_SOURCE = re.compile(r"ib[-_]sync", re.IGNORECASE)

GIT_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "PATH": "/usr/bin:/bin:/usr/local/bin",
}


def _tracked_files(directory: str) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--", directory],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=GIT_ENV,
    )
    if result.returncode != 0:
        pytest.skip("not a git checkout")
    return [line for line in result.stdout.splitlines() if line.strip()]


def _watched_records():
    for directory in WATCHED_DIRS:
        for relative_path in _tracked_files(directory):
            path = ROOT / relative_path
            if path.is_file():
                yield relative_path, path.read_text(errors="ignore")


def _remediation(offenders: list[str]) -> str:
    return (
        "Git-tracked agent-memory records carry live account state. The repository "
        "is public, so this discloses the operator's account size and open position "
        "inventory to anyone who clones it, and it stays readable in git history "
        "after any later deletion.\n  " + "\n  ".join(offenders) + "\n\n"
        "Fix: `git rm --cached <path>` and cover the pattern in context/.gitignore "
        "so the runtime keeps writing it locally without tracking it."
    )


def test_no_tracked_memory_record_carries_account_figures():
    offenders = [
        f"{relative_path} (matched /{pattern.pattern}/)"
        for relative_path, text in _watched_records()
        for pattern in ACCOUNT_FIGURE_PATTERNS
        if pattern.search(text)
    ]
    assert not offenders, _remediation(offenders)


def test_no_tracked_memory_record_is_derived_from_a_live_account_pull():
    offenders = []
    for relative_path, text in _watched_records():
        try:
            source = json.loads(text).get("source", "")
        except (json.JSONDecodeError, AttributeError):
            continue
        if isinstance(source, str) and LIVE_ACCOUNT_SOURCE.search(source):
            offenders.append(f"{relative_path} (source: {source})")

    assert not offenders, _remediation(offenders)
