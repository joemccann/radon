"""Deploy transition journal as an explicit machine (audit 2026-11-03 §3.2).

`deploy-journal.py phase` accepted any phase from any phase, so a stray or
reordered write could move a journal backwards (verified -> prepared) or skip
verification (prepared -> verified). The helper now owns one allowed-transition
table; `write` only creates a fresh `prepared` journal.

Recovery: the verified path writes the green marker before commit-transition.
When recovery then restores the previous release, the marker must not keep
naming the failed SHA.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_deploy_corrections import (  # noqa: E402
    DEPLOY,
    JOURNAL_HELPER,
    _create_release_artifacts,
    _transition_env,
    _write_transition_journal,
)

REQUESTED = "2" * 40
PREVIOUS = "1" * 40
FORWARD = [
    "prepared",
    "backup_started",
    "backup_complete",
    "promoting",
    "target_unverified",
    "verified",
    "finalization_pending",
]


def _helper(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(JOURNAL_HELPER), *args],
        capture_output=True, text=True, check=False,
    )


def _create(tmp_path: Path) -> Path:
    journal = tmp_path / "transition.json"
    result = _helper(
        "write", str(journal), REQUESTED, PREVIOUS,
        str(tmp_path / "stage"), str(tmp_path / "backup"), "prepared",
    )
    assert result.returncode == 0, result.stderr
    return journal


def _phase(journal: Path) -> str:
    return json.loads(journal.read_text())["phase"]


def test_forward_chain_is_accepted(tmp_path: Path) -> None:
    journal = _create(tmp_path)
    for target in FORWARD[1:]:
        result = _helper("phase", str(journal), target)
        assert result.returncode == 0, (target, result.stderr)
        assert _phase(journal) == target
    # Recovery retries the committed finalization step.
    assert _helper("phase", str(journal), "finalization_pending").returncode == 0


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("prepared", "verified"),
        ("prepared", "target_unverified"),
        ("backup_started", "promoting"),
        ("promoting", "backup_started"),
        ("target_unverified", "promoting"),
        ("verified", "prepared"),
        ("verified", "target_unverified"),
        ("finalization_pending", "verified"),
        ("target_unverified", "target_unverified"),
    ],
)
def test_illegal_transition_is_refused_and_journal_unchanged(
    tmp_path: Path, current: str, target: str,
) -> None:
    journal = _create(tmp_path)
    for step in FORWARD[1 : FORWARD.index(current) + 1]:
        assert _helper("phase", str(journal), step).returncode == 0
    before = journal.read_bytes()

    result = _helper("phase", str(journal), target)

    assert result.returncode == 3, result.stderr
    assert f"{current} -> {target}" in result.stderr
    assert journal.read_bytes() == before


@pytest.mark.parametrize("phase", FORWARD[1:])
def test_write_only_creates_a_prepared_journal(tmp_path: Path, phase: str) -> None:
    journal = tmp_path / "transition.json"
    result = _helper(
        "write", str(journal), REQUESTED, PREVIOUS,
        str(tmp_path / "stage"), str(tmp_path / "backup"), phase,
    )
    assert result.returncode != 0
    assert not journal.exists()


def test_write_refuses_to_replace_a_pending_journal(tmp_path: Path) -> None:
    journal = _create(tmp_path)
    assert _helper("phase", str(journal), "backup_started").returncode == 0
    before = journal.read_bytes()
    result = _helper(
        "write", str(journal), REQUESTED, PREVIOUS,
        str(tmp_path / "stage"), str(tmp_path / "backup"), "prepared",
    )
    assert result.returncode != 0
    assert journal.read_bytes() == before


def test_every_phase_deploy_sh_writes_is_a_table_target() -> None:
    text = DEPLOY.read_text(encoding="utf-8")
    written = set(re.findall(r"write_transition_phase ([a-z_]+)", text))
    assert written >= {"backup_started", "verified", "finalization_pending"}
    for phase in written:
        assert phase in FORWARD, phase


def test_recovery_restoring_previous_release_quarantines_green_marker(
    tmp_path: Path,
) -> None:
    releases = tmp_path / "releases"
    live = tmp_path / "live"
    stage = releases / "stage.target"
    backup = releases / "backup.previous"
    journal = tmp_path / "transition.json"
    calls = tmp_path / "calls"
    marker = tmp_path / "green"
    releases.mkdir()
    stage.mkdir()
    backup.mkdir()
    _create_release_artifacts(live, "target")
    _create_release_artifacts(backup, "old")
    _write_transition_journal(journal, REQUESTED, PREVIOUS, stage, backup, "verified")
    # The verified path wrote the marker, then commit-transition never ran.
    marker.write_text(f"{REQUESTED}\n{PREVIOUS}\n")
    shell = f"""set -euo pipefail
source {DEPLOY}
current_commit() {{ printf '%s\\n' {REQUESTED}; }}
deploy_gate() {{ return 1; }}
git() {{ return 0; }}
check_units_active() {{ return 0; }}
sleep() {{ return 0; }}
sudo() {{
  printf '%s\\n' "$*" >> {calls}
  case "$2" in
    verify-committed) return 3 ;;
    *) return 0 ;;
  esac
}}
recover_pending_transition
"""
    env = {**_transition_env(live, releases, journal), "RADON_DEPLOY_GREEN_MARKER": str(marker)}
    result = subprocess.run(["bash", "-c", shell], env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "stop-clean" in calls.read_text()
    assert not journal.exists()
    assert not marker.exists() or marker.read_text().splitlines()[0] != REQUESTED
    assert list(tmp_path.glob("green.invalid.*")), "failed-SHA marker was not quarantined"
