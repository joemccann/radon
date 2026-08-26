#!/usr/bin/env python3
"""Classify a SHA range as python-gate, web-gate, both, or neither.

Used by .github/workflows/ci.yml to skip a test gate that cannot be affected
by the range. A gate is skipped only when NOTHING in the range is owned or
read by it: the trees each gate's own tests read across the tree boundary are
routed to that gate too (WEB_READS / PYTHON_READS below), so a change can
never skip the gate that asserts on it. Documentation-only ranges (.md, and
images under the skip prefixes) skip both gates.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

WEB_PREFIXES = (
    "web/",
    "site/",
    "lib/",
    "brand/",
    "vitest.config.ts",
    "package.json",
    "bun.lock",
)
PYTHON_PREFIXES = (
    "scripts/",
    "cloud/",
    "tests/",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
)
SHARED_PREFIXES = (".github/",)
SKIP_PREFIXES = (
    "docs/",
    "tasks/",
    ".claude/",
    ".agents/",
    "notebooks/",
)
# A skip prefix skips DOCUMENTATION, not the whole subtree. docs/ also holds
# runtime data (docs/options-structures.json is read by two vitest suites,
# docs/owners.json is the map the docs contract runs on) and .claude/ holds
# executable hooks/workflows. T-159.
DOC_SUFFIXES = (".md", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico")

# Cross-tree reads: trees whose files are consumed by the OTHER gate's tests.
# Both tuples are derived from the checkout and pinned by
# scripts/tests/test_path_filter.py, which re-derives them on every run from
# vitest.config.ts's test.include globs and from the path literals inside
# web/tests, lib/tools/__tests__, site/lib, scripts/lib, .pi/tests and
# tests/, scripts/tests/, cloud/tests/. Adding a new cross-tree read without
# updating these turns that test red. They only ever switch a gate ON.
#
# WEB: vitest is the sole runner of scripts/lib/**/*.test.js (pytest cannot
# collect .js) and web/tests reads scripts/ and cloud/ source directly
# (refresh-schedule reads cloud/services/*, market-state-holiday imports
# scripts/config/market_holidays.json). T-156.
WEB_READS = (
    ".pi/",
    "cloud/",
    "data/",
    "docs/",
    "lib/",
    "logs/",
    "scripts/",
    "site/",
    "tasks/",
    "tests/",
    "web/",
)
# PYTHON: the ⛔ PII plate guard reads site/public/plates, the account-figure
# guard reads web/lib/chat.ts, and the DUR-07 replica guard scans web/lib,
# web/app, web/components. T-157.
PYTHON_READS = (
    ".claude/",
    ".github/",
    ".pi/",
    "cloud/",
    "config/",
    "data/",
    "docker/",
    "docs/",
    "lib/",
    "logs/",
    "scripts/",
    "site/",
    "tasks/",
    "tests/",
    "web/",
)


def _matches(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in prefixes)


def _is_documentation(path: str) -> bool:
    if path.endswith(".md"):
        return True
    return _matches(path, SKIP_PREFIXES) and path.endswith(DOC_SUFFIXES)


def classify(paths: list[str]) -> tuple[bool, bool]:
    """Return (python, web). Unknown runtime paths force both gates on."""
    if not paths:
        return True, True
    python = False
    web = False
    saw_runtime = False
    for path in paths:
        if _is_documentation(path):
            continue
        saw_runtime = True
        if _matches(path, SHARED_PREFIXES):
            python = True
            web = True
            continue
        if _matches(path, WEB_PREFIXES + WEB_READS):
            web = True
        if _matches(path, PYTHON_PREFIXES + PYTHON_READS):
            python = True
        # Unknown runtime trees still run both gates. Deliberately measured
        # against the OWNED prefixes only: a cross-tree read must never be the
        # thing that stops an unclassified path from running both gates.
        if not _matches(path, WEB_PREFIXES + PYTHON_PREFIXES + SHARED_PREFIXES):
            python = True
            web = True
    if not saw_runtime:
        return False, False
    return python, web


def changed_paths(base: str, head: str, cwd: Path | None = None) -> list[str]:
    if not base or set(base) <= {"0"}:
        return []
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head}"],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def write_output(python: bool, web: bool, output_path: Path) -> None:
    line = f"python={'true' if python else 'false'}\nweb={'true' if web else 'false'}\n"
    with output_path.open("a", encoding="utf-8") as handle:
        handle.write(line)


def main(argv: list[str] | None = None) -> int:
    del argv
    base = os.environ.get("BASE_SHA", "")
    head = os.environ.get("HEAD_SHA", "") or "HEAD"
    output = os.environ.get("GITHUB_OUTPUT")
    if not output:
        print("GITHUB_OUTPUT is required", file=sys.stderr)
        return 2
    paths = changed_paths(base, head)
    python, web = classify(paths)
    write_output(python, web, Path(output))
    print(f"python={python} web={web} files={len(paths)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
