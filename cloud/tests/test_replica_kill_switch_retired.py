"""D5 step 2: the libsql replica kill switch is gone from the fleet.

No code path can open an embedded replica (step 1, #1532), so the
RADON_DB_NO_REPLICA environment lines have no reader. A unit, drop-in or
container wrapper that still sets them is noise that reads like a live
safety control.
"""

from __future__ import annotations

from pathlib import Path

import pytest

CLOUD = Path(__file__).resolve().parents[1]
SERVICES = CLOUD / "services"
REPLICA_VARIABLES = ("RADON_DB_NO_REPLICA", "RADON_DB_USE_REPLICA")


def _unit_artifacts() -> list[Path]:
    return sorted(path for path in SERVICES.rglob("*") if path.is_file())


@pytest.mark.parametrize("path", _unit_artifacts(), ids=lambda path: path.relative_to(SERVICES).as_posix())
def test_unit_artifact_sets_no_replica_variable(path: Path) -> None:
    body = path.read_text(encoding="utf-8")
    assert not [name for name in REPLICA_VARIABLES if name in body]


@pytest.mark.parametrize("script", ["radon-app-runtime.sh", "setup-vps.sh", "drift_audit.py"])
def test_cloud_script_carries_no_replica_variable(script: str) -> None:
    body = (CLOUD / "scripts" / script).read_text(encoding="utf-8")
    assert not [name for name in REPLICA_VARIABLES if name in body]
