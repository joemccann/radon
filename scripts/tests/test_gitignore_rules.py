"""Root .gitignore must keep its secret and runtime rules live.

A merge once folded every section banner onto the following line, turning
`.env`, `*.bak` and friends into comments. Pin both the effect (git ignores
these paths) and the shape (no rule text trailing a banner line).
"""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

MUST_IGNORE = [
    ".env",
    "web/.env",
    "data/example.bak",
    "radon.pid",
    ".DS_Store",
    ".vscode/settings.json",
    "vendor/tool",
    "scripts/__pycache__/x.pyc",
]


@pytest.mark.parametrize("path", MUST_IGNORE)
def test_path_is_ignored(path):
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", path],
        cwd=ROOT,
        capture_output=True,
    )
    assert result.returncode == 0, f"{path} is not ignored by .gitignore"


def test_banner_lines_carry_no_rule_text():
    folded = [
        f"{n}: {line}"
        for n, line in enumerate((ROOT / ".gitignore").read_text().splitlines(), 1)
        if re.match(r"^# ={10,}[^=\s]", line)
    ]
    assert folded == []


def test_a_split_banner_is_not_an_ignore_rule():
    """A banner whose last '=' falls onto the next line is a live pattern.

    git check-ignore exit 0 means the path is ignored. A file named '=' must
    not be, and no rule may be a run of '=' left over from a section banner.
    """
    lines = (ROOT / ".gitignore").read_text().splitlines()
    equals_rules = [
        f"{n}: {line!r}"
        for n, line in enumerate(lines, 1)
        if line.strip() and not line.startswith("#") and set(line.strip()) <= {"="}
    ]
    assert equals_rules == []
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", "--", "="],
        cwd=ROOT,
        capture_output=True,
    )
    assert result.returncode == 1, "a file named '=' is ignored"
