"""The nightly run-log directory must not be world-readable.

The wrappers write per-phase run logs (agent transcripts, findings, PR
detail) under ``$REPO/logs/<loop>`` with whatever the default umask allows,
so on a shared host any local user could read a security audit's raw
transcript. Each wrapper must restrict the log directory itself
(``chmod 700``) immediately after creating it — a dir-level clamp that
cannot regress any file mode the wrapper or the agent sets elsewhere.

Text-level contract in the house style of the other wrapper pins, plus a
run of the real snippet so the mode is proven on disk, not just greppable.
"""

from __future__ import annotations

import re
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BASH = "/bin/bash"

WRAPPERS = {
    "reliability": "reliability_weekend.sh",
    "testing": "testing_weekend.sh",
    "ci-performance": "ci_performance_nightly.sh",
    "documentation": "documentation_nightly.sh",
    "security": "security_nightly.sh",
}
LOOP_IDS = sorted(WRAPPERS)


def _mkdir_snippet(loop: str) -> str:
    """The wrapper's LOG_DIR creation lines, through the chmod (if any)."""
    src = (REPO / "scripts" / WRAPPERS[loop]).read_text(encoding="utf-8")
    m = re.search(
        r'^LOG_DIR="\$REPO/logs/[^"]+"\n'
        r'mkdir -p "\$LOG_DIR"\n'
        r'(?:#[^\n]*\n)*'
        r'(chmod 700 "\$LOG_DIR"\n)?',
        src,
        re.MULTILINE,
    )
    assert m, f"{loop}: LOG_DIR creation lines not found"
    return m.group(0)


@pytest.mark.parametrize("loop", LOOP_IDS)
def test_the_log_dir_is_restricted_right_after_creation(loop):
    snippet = _mkdir_snippet(loop)
    assert 'chmod 700 "$LOG_DIR"' in snippet, (
        f"{loop}: run logs under $REPO/logs are created with the default "
        "umask and never restricted"
    )


@pytest.mark.parametrize("loop", LOOP_IDS)
def test_the_snippet_actually_yields_a_0700_directory(tmp_path, loop):
    repo = tmp_path / "clone"
    repo.mkdir()
    proc = subprocess.run(
        [BASH, "-c", f'REPO="{repo}"\numask 022\n{_mkdir_snippet(loop)}'],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    log_dir = next((repo / "logs").iterdir())
    mode = stat.S_IMODE(log_dir.stat().st_mode)
    assert mode == 0o700, f"{loop}: {log_dir} mode is {oct(mode)}"
