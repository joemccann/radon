"""REL-180 (R-477, R-503..R-508): the loop launchers cannot hang, collide,
leak or go silently quiet. Every nightly loop now runs through
scripts/runner/run_loop.sh as a LaunchDaemon (scripts/runner/install.sh); the
per-loop wrappers are gone. Credential-free clones and the page on a skipped
notification are covered by test_runner_security_hooks.py and
test_runner_run_loop.py.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LOOPS = REPO / "scripts" / "runner" / "loops"


def _setting(env: Path, key: str) -> int:
    line = next(line for line in env.read_text().splitlines() if line.startswith(f"{key}="))
    return int(line.split("=", 1)[1])


# --- R-503: the fires are staggered -----------------------------------------


class TestStaggeredFires:
    def test_calendar_slots_are_pairwise_distinct(self) -> None:
        slots = {env.stem: (_setting(env, "SCHEDULE_HOUR"), _setting(env, "SCHEDULE_MINUTE"))
                 for env in sorted(LOOPS.glob("*.env"))}
        assert {"security", "security-deepsec"} <= set(slots), slots
        assert len(set(slots.values())) == len(slots), slots
        assert all(minute % 10 == 0 for _, minute in slots.values()), slots


# --- R-507: runner artifacts are gitignored ---------------------------------


class TestRunnerArtifactsAreIgnored:
    @pytest.mark.parametrize("path", [".deepsec/package.json", "data/radon/files/source.txt"])
    def test_ignored(self, path: str) -> None:
        result = subprocess.run(["git", "check-ignore", "-q", "--no-index", path], cwd=REPO, check=False)
        assert result.returncode == 0, f"{path} is not gitignored"
