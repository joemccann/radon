"""CIP-014: the two pytest tails must be splittable by ``--dist loadfile``.

Run 36379054426 kept ``test_weekend_subscription_only.py`` (184.5s, 900
tests) on one worker of ``pytest (scripts-gh)`` and
``test_loop_lifecycle_adversarial.py`` (118.8s) on one worker of
``pytest (scripts-jm)``. ``loadfile`` will not divide a module, so they were
split into per-class modules the existing shard globs already collect.

2026-09-28: both families drove the per-loop wrappers
(``scripts/security_nightly.sh``, ``scripts/security_deepsec_nightly.sh``),
which the runner cutover retired; their rails are pinned by
``test_runner_run_loop.py`` and ``test_runner_security_hooks.py``. The
inventory below is recomputed from ``pytest --collect-only``: nothing in
either family remains, so neither tail can come back as one module.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
TESTS = ROOT / "scripts" / "tests"

# Recomputed from `pytest --collect-only` after the runner cutover.
SUBSCRIPTION_TESTS = 0
LIFECYCLE_TESTS = 0


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_py_tests_stay_on_loadfile_without_a_new_shard() -> None:
    job = _workflow()["jobs"]["py-tests"]
    commands = "\n".join(str(step.get("run", "")) for step in job["steps"])
    assert "--dist loadfile" in commands
    shards = job["strategy"]["matrix"]["shard"]
    assert shards == [
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


def test_the_retired_tails_keep_the_measured_inventory() -> None:
    """A monolith or a split module reappearing would be a new tail."""
    sub = sorted(TESTS.glob("test_weekend_subscription_only*.py")) + sorted(TESTS.glob("weekend_subscription_only_lib.py"))
    life = sorted(TESTS.glob("test_loop_lifecycle_adversarial*.py")) + sorted(TESTS.glob("loop_lifecycle_adversarial_lib.py"))
    assert len(sub) == SUBSCRIPTION_TESTS and len(life) == LIFECYCLE_TESTS, (sub, life)
