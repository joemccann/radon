"""The Playwright P0 smoke is ONE job running three Playwright workers.

One serial `playwright test` step (workers=1) made the smoke the longest check
on every PR (~386s wall, 263s of it in a single step). A 3-shard job matrix
fixed the wall but each shard paid ~65s of duplicated setup (container init
25s, next build 28s, apt 6s, bun 6s) plus ~18s of uploads, for ~140s of specs
per shard on one worker. One job with `--workers=3` builds once and runs the
same curated files in parallel (fullyParallel:false still parallelizes across
files). web/playwright.config.ts keeps `workers: 1` for local runs; only the
CI command line raises it.

With no matrix, artifact names carry no shard suffix and must still be unique
within the run (upload-artifact v4+ rejects duplicates), the concurrency group
has no matrix key, and the demo rebuild pair runs unconditionally, LAST
(test_ci_demo_build_order.py, T-448).
"""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
WORKERS = "--workers=3"


def _jobs() -> dict:
    return yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)["jobs"]


def _job() -> dict:
    return _jobs()["e2e-financial-smoke"]


def _curated(job: dict) -> dict:
    curated = [s for s in job["steps"] if "Run curated P0" in s.get("name", "")]
    assert len(curated) == 1
    return curated[0]


def test_smoke_is_one_job_not_a_shard_matrix() -> None:
    job = _job()
    assert "strategy" not in job, (
        "a shard matrix repeats ~65s of setup and ~18s of uploads per shard"
    )
    assert "matrix." not in yaml.safe_dump(job), (
        "no matrix exists; a leftover matrix reference renders empty"
    )


def test_curated_specs_run_on_three_workers_in_one_invocation() -> None:
    run = " ".join(_curated(_job())["run"].split())
    assert run.startswith(f"npx playwright test {WORKERS} "), run
    assert "--shard" not in run


def test_job_name_stays_non_gating_without_shard_text() -> None:
    name = _job()["name"]
    assert "(non-gating)" in name
    assert "shard" not in name.lower()


def test_concurrency_group_has_no_matrix_key() -> None:
    concurrency = _job()["concurrency"]
    assert concurrency["group"] == "ci-e2e-smoke-${{ github.ref }}"
    assert concurrency["cancel-in-progress"] == "true"


def _artifact_names(job: dict) -> list[str]:
    return [
        step["with"]["name"]
        for step in job.get("steps") or []
        if "upload-artifact" in step.get("uses", "")
    ]


def test_every_artifact_name_is_unique_within_the_run() -> None:
    jobs = _jobs()
    smoke = _artifact_names(jobs["e2e-financial-smoke"])
    assert smoke
    for name in smoke:
        assert "shard" not in name and "matrix." not in name, name
    assert len(smoke) == len(set(smoke)), "upload-artifact v4+ rejects duplicate names"
    others = {
        name
        for job_name, job in jobs.items()
        if job_name != "e2e-financial-smoke"
        for name in _artifact_names(job)
    }
    assert not set(smoke) & others, set(smoke) & others


def test_demo_pair_runs_unconditionally() -> None:
    steps = _job()["steps"]
    demo = [s for s in steps if s.get("env", {}).get("NEXT_PUBLIC_RADON_DEMO") == "1"]
    assert len(demo) == 2
    for step in demo:
        assert "if" not in step, (
            f"{step.get('name')}: with one job the demo pair runs once; a "
            "leftover shard condition would render against an absent matrix"
        )
