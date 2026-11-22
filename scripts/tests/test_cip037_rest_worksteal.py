"""CIP-037: rest steals unfinished tests so load batches cannot pin a worker.

Mixed warm run 44979169331 job 137075708225 collected 2597 tests in 159.67s.
JUnit sum was 275.1s across 4 workers (balanced wall ≈69s) but CIP-031's
``--dist load`` still ran 2.32× that: ``test_runner_run_loop`` 90.17s and
``test_runner_security_hooks`` 69.83s. Those modules already use
function-scoped ``tmp_path`` sandboxes (CIP-031). ``worksteal`` (pytest-xdist
3.6+, pinned 3.8.0) lets idle workers take leftover tests. Other py shards
stay on loadfile. Shard count, inventory, coverage, and deploy needs stay.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _py_tests() -> dict:
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)["jobs"]["py-tests"]


def _rows() -> dict[str, dict]:
    return {str(row["shard"]): row for row in _py_tests()["strategy"]["matrix"]["include"]}


def test_rest_shard_uses_xdist_worksteal() -> None:
    rows = _rows()
    assert rows["rest"]["xdist"] == "-n auto --dist worksteal"
    commands = "\n".join(str(step.get("run", "")) for step in _py_tests()["steps"])
    assert "matrix.xdist" in commands


def test_rest_worksteal_does_not_restore_loadfile() -> None:
    rows = _rows()
    assert "loadfile" not in str(rows["rest"]["xdist"])
    assert "--dist loadfile" not in str(rows["rest"]["xdist"])


def test_other_py_shards_stay_on_loadfile() -> None:
    rows = _rows()
    others = [name for name in rows if name != "rest"]
    assert others, "expected loadfile shards beside rest"
    for name in others:
        assert rows[name]["xdist"] == "-n auto --dist loadfile", name


def test_rest_shard_count_and_membership_unchanged() -> None:
    job = _py_tests()
    assert job["strategy"]["matrix"]["shard"] == [
        "scripts-ac",
        "scripts-df",
        "scripts-i",
        "scripts-gh",
        "scripts-jm",
        "scripts-npsz",
        "scripts-rs",
        "scripts-daemons",
        "rest",
    ]
    rest_paths = str(_rows()["rest"]["paths"])
    assert "scripts/tests/test_runner_run_loop.py" in rest_paths
    assert "scripts/tests/test_runner_security_hooks.py" in rest_paths
    assert "scripts/api/tests" in rest_paths
