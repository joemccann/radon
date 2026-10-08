"""The Playwright P0 smoke runs as parallel shards, not one serial job.

One serial `playwright test` step (workers=1) made the smoke the longest check
on every PR (~386s wall, 263s of it in a single step). The job is a matrix of
Playwright `--shard`s. Each shard must keep its own concurrency group (a shared
group with cancel-in-progress would make the shards cancel each other) and
upload uniquely named artifacts (upload-artifact v4+ rejects duplicate names
within a run). The demo rebuild pair runs on exactly one shard.
"""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


def _job() -> dict:
    return yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)["jobs"]["e2e-financial-smoke"]


def _shards(job: dict) -> list[str]:
    return job.get("strategy", {}).get("matrix", {}).get("shard", [])


def test_smoke_is_a_shard_matrix_that_does_not_fail_fast() -> None:
    job = _job()
    shards = _shards(job)
    assert len(shards) >= 2, "e2e-financial-smoke must fan out over Playwright shards"
    assert job["strategy"].get("fail-fast") == "false", (
        "one failing shard must not cancel the others; each shard's evidence stands alone"
    )
    curated = [s for s in job["steps"] if "Run curated P0" in s.get("name", "")]
    assert len(curated) == 1
    assert f"--shard=${{{{ matrix.shard }}}}/{len(shards)}" in curated[0]["run"]


def test_each_shard_has_its_own_concurrency_group() -> None:
    group = _job()["concurrency"]["group"]
    assert "matrix.shard" in group, (
        "shards sharing one cancel-in-progress group cancel each other"
    )


def test_every_artifact_name_is_unique_per_shard() -> None:
    for step in _job()["steps"]:
        if "upload-artifact" in step.get("uses", ""):
            assert "matrix.shard" in step["with"]["name"], (
                f"{step.get('name')}: duplicate artifact names across shards fail the upload"
            )


def test_demo_pair_runs_on_exactly_one_shard() -> None:
    steps = _job()["steps"]
    demo = [s for s in steps if s.get("env", {}).get("NEXT_PUBLIC_RADON_DEMO") == "1"]
    assert len(demo) == 2
    conditions = {s.get("if", "") for s in demo}
    assert len(conditions) == 1 and "matrix.shard" in conditions.pop(), (
        "the demo rebuild + spec must be gated to one shard, not repeated per shard"
    )
