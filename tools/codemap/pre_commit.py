#!/usr/bin/env python3
"""Regenerate the code-path map when staged source files changed.

Called from the host git pre-commit hook. Stages only the generated artifacts.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from generate_codemap import ARTIFACT_RELS, REPO, build_graph, should_refresh, write_graph


def staged_paths() -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "-z"],
        cwd=REPO,
        check=True,
        capture_output=True,
    )
    return [path for path in result.stdout.decode().split("\0") if path]


def main() -> int:
    if not should_refresh(staged_paths()):
        return 0
    write_graph(build_graph(REPO))
    subprocess.run(["git", "add", "--", *ARTIFACT_RELS], cwd=REPO, check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
