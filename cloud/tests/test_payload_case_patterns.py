"""``payload_paths_changed``'s case arms must actually be reachable.

REL-070 / R-264(b): the runtime arm is written as a multi-line pattern ending
in a backslash continuation. Bash strips only the backslash-newline, so the
next line's leading indentation becomes part of the glob and
``requirements*`` can never match. Today the unconditional ``return 0``
fallthrough masks it, but anyone adding an arm below inherits a dead pattern.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "scripts" / "deploy.sh"

# The runtime arm — the one whose job is to say "this change restarts units".
RUNTIME_ARM_ANCHOR = "web/*|lib/*|scripts/*"


def _runtime_arm_pattern() -> str:
    """Extract the runtime arm's glob exactly as bash will see it."""
    text = DEPLOY.read_text(encoding="utf-8")
    start = text.index(RUNTIME_ARM_ANCHOR)
    # Consume through the arm's terminating ')' , honouring line continuations
    # the same way bash does: the backslash-newline vanishes, nothing else.
    rest = text[start:]
    end = rest.index(")\n")
    raw = rest[: end + 1].rstrip()
    assert raw.endswith(")"), raw
    return re.sub(r"\\\n", "", raw[:-1])


def _bash_case_matches(pattern: str, path: str) -> bool:
    script = f'case "$1" in\n{pattern}) exit 0 ;;\n*) exit 1 ;;\nesac'
    result = subprocess.run(
        ["bash", "-c", script, "bash", path],
        capture_output=True,
        text=True,
    )
    assert result.returncode in (0, 1), result.stdout + result.stderr
    return result.returncode == 0


def test_runtime_arm_matches_the_paths_it_names() -> None:
    pattern = _runtime_arm_pattern()
    unmatched = [
        path
        for path in (
            "requirements.txt",
            "requirements-dev.txt",
            "pyproject.toml",
            "bun.lock",
            "package.json",
            "radon-api.service",
        )
        if not _bash_case_matches(pattern, path)
    ]
    assert not unmatched, (
        "these paths are named by the runtime arm but the glob cannot match "
        f"them: {unmatched} (pattern: {pattern!r})"
    )


def test_runtime_arm_still_matches_its_first_line_paths() -> None:
    pattern = _runtime_arm_pattern()
    for path in ("web/app/page.tsx", "scripts/ib_sync.py", "cloud/services/x.service"):
        assert _bash_case_matches(pattern, path), path


def test_runtime_arm_does_not_match_a_docs_path() -> None:
    pattern = _runtime_arm_pattern()
    assert not _bash_case_matches(pattern, "docs/cloud-services.md")
