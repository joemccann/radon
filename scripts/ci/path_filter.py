#!/usr/bin/env python3
"""Classify a SHA range as python-gate, web-gate, both, or neither.

Used by .github/workflows/ci.yml so a web/-only push skips pytest and a
scripts/-only push skips vitest. Shared files (CI yaml, lockfiles that both
gates consume) run both. Docs-only ranges skip both test gates.
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


def _matches(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in prefixes)


def classify(paths: list[str]) -> tuple[bool, bool]:
    """Return (python, web). Unknown runtime paths force both gates on."""
    if not paths:
        return True, True
    python = False
    web = False
    saw_runtime = False
    for path in paths:
        if _matches(path, SKIP_PREFIXES) or path.endswith(".md"):
            continue
        saw_runtime = True
        if _matches(path, SHARED_PREFIXES):
            python = True
            web = True
            continue
        if _matches(path, WEB_PREFIXES):
            web = True
        if _matches(path, PYTHON_PREFIXES):
            python = True
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
