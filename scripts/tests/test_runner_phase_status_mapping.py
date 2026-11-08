"""scripts/runner/phase_status.sh maps a status by its LEADING keyword.

The old case ladder matched prefixes in a fixed order with one substring
pattern (*GROUND TRUTH*) in the middle, so a status that merely mentioned
ground truth mapped to 70 whatever its verdict, and a status with leading
whitespace fell through to the process exit code.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

BASH_LIB = Path(__file__).resolve().parents[2] / "scripts" / "runner" / "phase_status.sh"


def _exit(rc, status):
    env = {**os.environ, "PHASE_RC": str(rc), "PHASE_REFUSED": "", "PHASE_KILLED": "",
           "PHASE_HOOK_REQUIRED": ""}
    proc = subprocess.run(
        ["bash", "-c", f'. "{BASH_LIB}"\nphase_status_exit "$1"\n', "x", status],
        capture_output=True, text=True, env=env, timeout=15, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return int(proc.stdout.strip())


@pytest.mark.parametrize("status,expected", [
    ("OK", 0),
    ("OK: 0 PR(s), nothing to merge", 0),
    ("OK: audit clean; GROUND TRUTH recheck matched", 0),
    ("FAILED (exit 3)", 1),
    ("FAILED: GROUND TRUTH unreachable", 1),
    ("KILLED (SIGTERM)", 1),
    ("CRASHED", 1),
    ("REFUSED: credential file present", 2),
    ("GROUND TRUTH FAILED", 70),
    ("INCOMPLETE (exit 0 without the phase-completion marker)", 75),
    ("INCOMPLETE: deliver cap hit (TIMEOUT)", 75),
    ("TRUNCATED (background work killed before the phase finished)", 75),
    ("TIMEOUT", 124),
])
def test_leading_keyword_decides(status, expected):
    assert _exit(0, status) == expected


def test_leading_whitespace_is_trimmed_like_the_python_parser():
    assert _exit(3, "  OK") == 0


@pytest.mark.parametrize("rc,expected", [(0, 0), (75, 75), (3, 1)])
def test_unknown_or_empty_status_falls_back_to_the_process_exit(rc, expected):
    assert _exit(rc, "") == expected
    assert _exit(rc, "SOMETHING ELSE") == expected
