"""The Playwright P0 smoke runs as two shards of two Playwright workers each.

History, all measured on CI:
- One serial `playwright test` (workers=1): ~386s wall, the longest PR check.
- Three shards on one worker each: ~264-292s wall, but every shard paid ~65s
  of duplicated setup (container init 25s, next build 28s, apt 6s, bun 6s)
  plus ~18s of uploads, ~800 runner-seconds per PR.
- One job on `--workers=3` (run 41335275763): specs 291s, job 444s. It used
  ~444 runner-seconds but became the longest check agents wait on, because
  three browsers plus `next start` saturate the 4 vCPU runner.

Two shards on `--workers=2` keep the wall near the three-shard figure while
paying the setup twice instead of three times. web/playwright.config.ts keeps
`workers: 1` for local runs; only the CI command line raises it.

Each shard keeps its own concurrency group (a shared group with
cancel-in-progress would make the shards cancel each other) and uploads
uniquely named artifacts (upload-artifact v4+ rejects duplicate names within a
run). The demo rebuild pair runs on exactly one shard, LAST
(test_ci_demo_build_order.py, T-448).
"""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
SHARDS = ["1", "2"]
WORKERS = "--workers=2"


def _jobs() -> dict:
    return yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)["jobs"]


def _job() -> dict:
    return _jobs()["e2e-financial-smoke"]


def _curated(job: dict) -> dict:
    curated = [s for s in job["steps"] if "Run curated P0" in s.get("name", "")]
    assert len(curated) == 1
    return curated[0]


def test_smoke_is_a_two_shard_matrix_that_does_not_fail_fast() -> None:
    strategy = _job()["strategy"]
    assert strategy["matrix"]["shard"] == SHARDS
    assert strategy["fail-fast"] == "false", "one red shard must not cancel the other"


def test_each_shard_runs_its_slice_on_two_workers() -> None:
    run = " ".join(_curated(_job())["run"].split())
    assert run.startswith(
        f"npx playwright test --shard=${{{{ matrix.shard }}}}/{len(SHARDS)} {WORKERS} "
    ), run


def test_job_name_carries_the_shard_and_stays_non_gating() -> None:
    name = _job()["name"]
    assert "(non-gating" in name
    assert f"shard ${{{{ matrix.shard }}}}/{len(SHARDS)}" in name


def test_each_shard_has_its_own_concurrency_group() -> None:
    concurrency = _job()["concurrency"]
    assert concurrency["group"] == "ci-e2e-smoke-${{ github.ref }}-${{ matrix.shard }}"
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
        assert name.endswith("-shard-${{ matrix.shard }}"), name
    assert len(smoke) == len(set(smoke)), "upload-artifact v4+ rejects duplicate names"
    others = {
        name
        for job_name, job in jobs.items()
        if job_name != "e2e-financial-smoke"
        for name in _artifact_names(job)
    }
    assert not set(smoke) & others, set(smoke) & others


def test_demo_pair_runs_on_exactly_one_shard() -> None:
    steps = _job()["steps"]
    demo = [s for s in steps if s.get("env", {}).get("NEXT_PUBLIC_RADON_DEMO") == "1"]
    assert len(demo) == 2
    for step in demo:
        assert step.get("if") == "matrix.shard == 1", step.get("name")
