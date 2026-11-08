"""`radon repair-nextjs-replica` unlinks only inside the pinned data directory.

The verb is a NOPASSWD root grant for the `radon` account
(`config/sudoers.d/radon-monitor`), and every component of
`/home/radon/radon/data` is owned by that same account. `rm` does not follow a
final-component symlink but it does resolve parents, so root must pin the
directory itself with `O_NOFOLLOW` and unlink fixed basenames relative to it.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from _bash_toolchain import MODERN_BASH, requires_modern_bash

pytestmark = requires_modern_bash

CLOUD_ROOT = Path(__file__).resolve().parents[1]
OPERATOR = CLOUD_ROOT / "scripts" / "operator-radon.sh"
FUNC_RE = re.compile(
    r"^unlink_replica_files\(\)\s*\{.*?^\}", re.DOTALL | re.MULTILINE
)

REPLICA_NAMES = ("replica.db", "replica.db-wal", "replica.db-shm", "replica.db-info")


def _run(data_dir: Path) -> subprocess.CompletedProcess[str]:
    match = FUNC_RE.search(OPERATOR.read_text(encoding="utf-8"))
    assert match, "unlink_replica_files() not found in operator-radon.sh"
    snippet = (
        "set -uo pipefail\n"
        + match.group(0)
        + f'\nunlink_replica_files "{data_dir}"\n'
    )
    return subprocess.run(
        [str(MODERN_BASH), "-c", snippet],
        env={**os.environ, "OPERATOR_PYTHON": sys.executable},
        capture_output=True,
        text=True,
        check=False,
    )


def test_removes_the_four_replica_artifacts(tmp_path: Path) -> None:
    data = tmp_path / "home" / "radon" / "radon" / "data"
    data.mkdir(parents=True)
    keep = data / "watchlist.json"
    keep.write_text("{}", encoding="utf-8")
    for name in REPLICA_NAMES:
        (data / name).write_text("stale", encoding="utf-8")

    result = _run(data)

    assert result.returncode == 0, result.stderr
    assert [path.name for path in data.iterdir()] == ["watchlist.json"]
    assert keep.read_text(encoding="utf-8") == "{}"


def test_missing_artifacts_are_not_an_error(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    assert _run(data).returncode == 0


def test_refuses_a_symlinked_parent_component(tmp_path: Path) -> None:
    """A radon-plantable parent must not be resolved by a root unlink."""
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "replica.db"
    victim.write_text("operator data", encoding="utf-8")
    root = tmp_path / "home" / "radon" / "radon"
    root.mkdir(parents=True)
    (root / "data").symlink_to(outside, target_is_directory=True)

    result = _run(root / "data")

    assert result.returncode == 74, result.stdout + result.stderr
    assert "REFUSING" in result.stderr
    assert victim.read_text(encoding="utf-8") == "operator data"


def test_a_planted_artifact_symlink_is_unlinked_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside.db"
    outside.write_text("operator data", encoding="utf-8")
    data = tmp_path / "data"
    data.mkdir()
    link = data / "replica.db"
    link.symlink_to(outside)

    result = _run(data)

    assert result.returncode == 0, result.stderr
    assert not link.exists() and not link.is_symlink()
    assert outside.read_text(encoding="utf-8") == "operator data"


def test_repair_arm_calls_the_pinned_helper() -> None:
    body = OPERATOR.read_text(encoding="utf-8")
    arm = body[body.index('"$requested_action" == "repair-nextjs-replica"'):]
    arm = arm[: arm.index("\nfi\n")]
    assert 'unlink_replica_files "/home/radon/radon/data"' in arm
    assert "rm -f" not in arm
    # A refusal still restarts Next.js before it propagates the exit code.
    assert arm.index("unlink_replica_files") < arm.index(
        "run_systemctl_bounded start radon-nextjs.service")
    assert arm.index("run_systemctl_bounded start radon-nextjs.service") < arm.index(
        'exit "$unlink_rc"')
