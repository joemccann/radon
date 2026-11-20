"""CIP-031: rest shard distributes per test so loadfile floors cannot pin a worker.

Mixed warm run 44017827310 job 133819978797 collected 2482 tests in 105.67s.
JUnit sum was 231.2s across 4 workers, but ``test_runner_run_loop`` spent
81.16s on one worker and ``test_runner_security_hooks`` spent 52.86s on
another because ``--dist loadfile`` cannot split a module. Theoretical
balanced wall is 231/4 ≈ 58s. Those two modules use function-scoped
``tmp_path`` sandboxes (same shape as cloud heavy). Other py shards stay
on loadfile. Shard count, inventory, coverage, and deploy needs stay.
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


def test_rest_shard_uses_per_test_xdist_load() -> None:
    rows = _rows()
    assert rows["rest"]["xdist"] == "-n auto --dist load"
    commands = "\n".join(str(step.get("run", "")) for step in _py_tests()["steps"])
    assert "matrix.xdist" in commands
    assert "-n auto --dist loadfile" not in commands


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
