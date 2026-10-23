"""The root .gitignore keeps host-local credential files out of the index.

A merge once joined section banners to the pattern on the next line, which
turned ``.env`` (and seven other patterns) into comment text.
"""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _ignored(paths: list[str]) -> list[str]:
    proc = subprocess.run(
        ["git", "-c", "core.excludesFile=/dev/null", "check-ignore", "--no-index", *paths],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return proc.stdout.splitlines()


def test_env_files_are_ignored_at_every_depth():
    paths = [".env", "web/.env", "scripts/.env", "cloud/sub/.env"]
    assert _ignored(paths) == paths


def test_every_section_pattern_is_live():
    paths = [".DS_Store", ".vscode/settings.json", "__pycache__/m.pyc", "x.bak", "vendor/bin", "r.pid"]
    assert _ignored(paths) == paths


def test_no_pattern_is_joined_to_a_banner():
    for n, line in enumerate((ROOT / ".gitignore").read_text().splitlines(), 1):
        assert not re.match(r"^# =+[^=\s]", line), f".gitignore:{n} joins a banner to text"
