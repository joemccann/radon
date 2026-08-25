"""Deployment workflow concurrency contracts.

The 2026-07-08 production outage was caused by workflow-level
``cancel-in-progress`` terminating the SSH deploy after it had stopped services.
Test jobs may still cancel superseded work, but a deploy that has started must
finish and every deploy must name the exact commit it intends to release.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

import yaml


WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
TEST_JOBS = (
    "secret-scan",
    "web-tests",
    "web-coverage",
    "py-tests",
    "py-coverage",
    "cloud-tests",
    "perimeter-smoke",
)


def _workflow() -> dict:
    # BaseLoader avoids YAML 1.1 coercing GitHub's top-level ``on`` key to bool.
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_workflow_level_cancellation_cannot_kill_a_running_deploy() -> None:
    workflow = _workflow()
    concurrency = workflow.get("concurrency")
    assert concurrency is None or concurrency.get("cancel-in-progress") != "true", (
        "workflow-level cancel-in-progress can terminate Deploy via SSH after "
        "production services have been stopped"
    )


def test_superseded_test_jobs_remain_cancelable() -> None:
    jobs = _workflow()["jobs"]
    for name in TEST_JOBS:
        concurrency = jobs[name].get("concurrency", {})
        assert concurrency.get("cancel-in-progress") == "true", (
            f"{name} must cancel superseded test work without canceling deploy"
        )
        assert "github.ref" in concurrency.get("group", "")


def test_production_deploy_is_serialized_and_never_canceled() -> None:
    deploy = _workflow()["jobs"]["deploy"]
    concurrency = deploy.get("concurrency", {})
    assert concurrency.get("group") == "deploy-production"
    assert concurrency.get("cancel-in-progress") == "false"


def test_deploy_time_budgets_cover_supervisor_and_recovery() -> None:
    deploy = _workflow()["jobs"]["deploy"]
    assert int(deploy["timeout-minutes"]) == 60
    ssh_step = next(step for step in deploy["steps"] if step.get("name") == "Deploy via SSH")
    command_timeout = ssh_step["with"].get("command_timeout", "")
    assert command_timeout == "55m"

    # A fresh process may first recover an abandoned journal, spend the full
    # inner deadline, then recover another interrupted transition. Each recovery
    # has two 180s mutation actions, 30s verify and commit actions, and may wait
    # 190s once for an orphan root action to release the lifecycle lock.
    root_recovery_seconds = 190 + (2 * 180) + 30 + 30
    worst_case_seconds = (2 * root_recovery_seconds) + 900 + 30
    ssh_seconds = int(command_timeout.removesuffix("m")) * 60
    job_seconds = int(deploy["timeout-minutes"]) * 60
    assert ssh_seconds >= worst_case_seconds + 600
    assert job_seconds >= ssh_seconds + 300


def test_deploy_passes_the_explicit_workflow_sha() -> None:
    deploy = _workflow()["jobs"]["deploy"]
    ssh_step = next(step for step in deploy["steps"] if step.get("name") == "Deploy via SSH")
    script = ssh_step["with"]["script"]
    assert "scripts/deploy.sh" in script
    assert "${{ github.sha }}" in script, (
        "deploy.sh must receive the tested commit, not fetch an implicit moving main"
    )
    # Monorepo path: materialize cloud/ from the release SHA; legacy dual-checkout
    # remains the fallback when the SHA predates the fold.
    assert "cloud/scripts/deploy.sh" in script
    assert "cat-file -e" in script
    assert "LEGACY_CLOUD" in script
    assert "RADON_DEPLOY_ENV_FILE" in script


def _job_commands(job: dict) -> str:
    return "\n".join(str(step.get("run", "")) for step in job.get("steps", []))


def test_ci_runs_cloud_infra_pytest() -> None:
    jobs = _workflow()["jobs"]
    cloud = jobs["cloud-tests"]
    assert "pytest cloud/tests" in _job_commands(cloud)
    assert "pytest cloud/tests" not in _job_commands(jobs["py-tests"]), (
        "cloud infra tests must run as their own job so they leave the unit "
        "pytest critical path"
    )
    deploy_needs = jobs["deploy"]["needs"]
    assert "cloud-tests" in deploy_needs
    assert "py-tests" in deploy_needs


def test_python_ci_jobs_cache_pip_and_pin_the_test_toolchain() -> None:
    jobs = _workflow()["jobs"]
    uv_pin = "803947b9bd8e9f986429fa0c5a41c367cd732b41"
    for name in ("py-tests", "cloud-tests"):
        setup_uv = next(
            step
            for step in jobs[name]["steps"]
            if "astral-sh/setup-uv@" in step.get("uses", "")
        )
        assert uv_pin in setup_uv["uses"]
        assert setup_uv["with"]["python-version"] == "3.13"
        assert str(setup_uv["with"].get("enable-cache", "")).lower() in ("true", "True")
        assert str(setup_uv["with"].get("activate-environment", "")).lower() in ("true", "True")
        cache_paths = str(setup_uv["with"].get("cache-dependency-glob", ""))
        assert "requirements-dev.txt" in cache_paths
        commands = _job_commands(jobs[name])
        assert "uv pip install" in commands
        assert "uv pip install --system" not in commands
        assert "requirements-dev.txt" in commands
        assert "pip install --upgrade pip" not in commands
        assert "pip install pytest pytest-asyncio pytest-cov" not in commands


def test_vitest_uses_all_ci_workers() -> None:
    config = (WORKFLOW.parents[2] / "vitest.config.ts").read_text(encoding="utf-8")
    assert re.search(r'maxWorkers:\s*["\']100%["\']', config)
    assert re.search(r"fileParallelism:\s*true", config)


def test_vitest_shards_then_merges_coverage_ratchet() -> None:
    jobs = _workflow()["jobs"]
    web = jobs["web-tests"]
    shards = web["strategy"]["matrix"]["shard"]
    assert [str(item) for item in shards] == ["1", "2", "3", "4", "5", "6", "7", "8"]
    assert "matrix.shard" in web["concurrency"]["group"]
    commands = _job_commands(web)
    assert "--shard=${{ matrix.shard }}/8" in commands
    assert "coverage.thresholds.lines=0" in commands
    assert "coverage.thresholds.functions=0" in commands
    assert "coverage.thresholds.branches=0" in commands
    coverage = jobs["web-coverage"]
    assert "web-tests" in coverage["needs"]
    assert "merge_vitest_coverage" in _job_commands(coverage)
    web_checkout = next(
        step for step in coverage["steps"] if "actions/checkout" in step.get("uses", "")
    )
    sparse = str(web_checkout.get("with", {}).get("sparse-checkout", ""))
    assert "merge_vitest_coverage.py" in sparse
    assert "vitest.config.ts" in sparse


def test_vitest_coverage_uses_text_reporter_only() -> None:
    config = (WORKFLOW.parents[2] / "vitest.config.ts").read_text(encoding="utf-8")
    assert re.search(r'reporter:\s*\[\s*["\']text["\']\s*\]', config), (
        "html/clover/json coverage reports inflate the 307s vitest gate; "
        "the ratchet is thresholds, not artifacts"
    )
    assert "--coverage" in _job_commands(_workflow()["jobs"]["web-tests"])


def test_unit_pytest_uses_xdist_loadfile() -> None:
    commands = _job_commands(_workflow()["jobs"]["py-tests"])
    assert "-n auto" in commands
    assert "--dist loadfile" in commands
    assert "--cov-fail-under=0" in commands
    assert "--cov-branch" not in commands
    assert "term-missing" not in commands


def test_pytest_shards_then_combines_coverage_ratchet() -> None:
    jobs = _workflow()["jobs"]
    py_tests = jobs["py-tests"]
    shards = [str(item) for item in py_tests["strategy"]["matrix"]["shard"]]
    assert shards == [
        "scripts-ac",
        "scripts-df",
        "scripts-i",
        "scripts-ghjm",
        "scripts-npsz",
        "scripts-rs",
        "scripts-daemons",
        "rest-api",
        "rest",
    ]
    assert "matrix.shard" in py_tests["concurrency"]["group"]
    commands = _job_commands(py_tests)
    assert "matrix.paths" in commands
    include = str(py_tests["strategy"]["matrix"]["include"])
    assert "test_[a-c]" in include
    assert "test_[d-f]" in include
    assert "test_i*" in include
    assert "--cov-fail-under=0" in commands
    checkout = next(
        step
        for step in py_tests["steps"]
        if "actions/checkout" in step.get("uses", "")
    )
    fetch_depth = str(checkout.get("with", {}).get("fetch-depth", ""))
    assert "scripts-df" in fetch_depth
    upload = next(
        step
        for step in py_tests["steps"]
        if "upload-artifact" in step.get("uses", "")
    )
    assert upload["with"]["path"] == ".coverage"
    # upload-artifact v4+ skips dotfiles unless this is set (PR 88 first green
    # pytest run: 1586 passed, then "No files were found ... path: .coverage").
    assert str(upload["with"].get("include-hidden-files", "")).lower() in ("true", "True")
    coverage = jobs["py-coverage"]
    assert "py-tests" in coverage["needs"]
    cov_commands = _job_commands(coverage)
    assert "find coverage-artifacts" in cov_commands
    assert "name '.coverage'" in cov_commands
    assert "coverage combine" in cov_commands
    assert "fail-under=56" in cov_commands
    cov_checkout = next(
        step for step in coverage["steps"] if "actions/checkout" in step.get("uses", "")
    )
    sparse = str(cov_checkout.get("with", {}).get("sparse-checkout", ""))
    assert "pyproject.toml" in sparse
    assert "scripts" in sparse


def test_pytest_filename_shards_partition_scripts_tests() -> None:
    py_tests = _workflow()["jobs"]["py-tests"]
    include = py_tests["strategy"]["matrix"]["include"]
    filename_globs: list[str] = []
    other_paths: list[str] = []
    for row in include:
        for token in str(row["paths"]).split():
            token = token.strip('"')
            if token.startswith("scripts/tests/"):
                filename_globs.append(Path(token).name)
            else:
                other_paths.append(token)
    files = [
        path.name
        for path in (WORKFLOW.parents[2] / "scripts" / "tests").glob("test_*.py")
    ]
    assigned = {name: [] for name in files}
    for pattern in filename_globs:
        for name in files:
            if fnmatch.fnmatch(name, pattern):
                assigned[name].append(pattern)
    overlap = {name: hits for name, hits in assigned.items() if len(hits) > 1}
    missing = [name for name, hits in assigned.items() if not hits]
    assert overlap == {}
    assert missing == []
    assert "scripts/api/tests" in other_paths
    assert "scripts/trade_blotter" in other_paths
    assert "tests" in other_paths


def test_coverage_ratchets_gate_deploy() -> None:
    jobs = _workflow()["jobs"]
    needs = jobs["deploy"]["needs"]
    assert "web-coverage" in needs
    assert "py-coverage" in needs
    assert "web-tests" in needs
    assert "py-tests" in needs
    assert "cloud-tests" in needs
    assert "stage-release" not in jobs
    assert "merge_vitest_coverage" in _job_commands(jobs["web-coverage"])
    assert "fail-under=56" in _job_commands(jobs["py-coverage"])


def test_py_coverage_installs_coverage_only() -> None:
    job = _workflow()["jobs"]["py-coverage"]
    commands = _job_commands(job)
    assert "pytest-cov==7.1.0" in commands
    assert "pip install -r requirements-dev.txt" not in commands
    setup_uv = next(
        step for step in job["steps"] if "astral-sh/setup-uv@" in step.get("uses", "")
    )
    assert setup_uv["with"]["python-version"] == "3.13"
    assert str(setup_uv["with"].get("activate-environment", "")).lower() in ("true", "True")
    assert "python -m coverage combine" in commands


def test_bun_jobs_cache_the_install_store() -> None:
    jobs = _workflow()["jobs"]
    pin = "5a3ec84eff668545956fd18022155c47e93e2684"
    for name in ("web-tests", "perimeter-smoke"):
        cache = next(
            step
            for step in jobs[name]["steps"]
            if "actions/cache@" in step.get("uses", "")
        )
        assert pin in cache["uses"]
        assert "~/.bun/install/cache" in str(cache["with"]["path"])
        key = str(cache["with"]["key"])
        assert "bun.lock" in key
        assert "web/bun.lock" in key


def test_ci_uses_frozen_bun_lockfile_contract_for_both_workspaces() -> None:
    workflow = _workflow()
    for job_name in ("web-tests", "perimeter-smoke"):
        setup_bun = next(
            step for step in workflow["jobs"][job_name]["steps"]
            if "oven-sh/setup-bun" in step.get("uses", "")
        )
        assert setup_bun["with"]["bun-version"] == "1.3.14"
        steps = workflow["jobs"][job_name]["steps"]
        commands = "\n".join(str(step.get("run", "")) for step in steps)
        assert commands.count("bun install --frozen-lockfile") == 2
        assert "&" in commands
        assert "wait" in commands


# TEST_AUDIT T-122: the sharded matrix must reach every test module the
# pre-shard recursive invocation collected. A `test_[a-c]*.py` glob cannot
# match a directory, so scripts/tests/test_monitor_daemon/ and
# scripts/tests/test_watchdog/ (752 tests over fills, exit orders, journal
# sync and the whole watchdog) silently left CI on 424e66da while the
# workflow stayed green. Set-equality, not shard names.
PY_COLLECTION_ROOTS = ("scripts/tests", "scripts/api/tests", "scripts/trade_blotter", "tests")


def _test_modules_under(root: Path, base: Path) -> set[str]:
    return {str(f.relative_to(root)) for f in base.rglob("test_*.py")}


def _expand_shard_paths(root: Path, paths: str) -> set[str]:
    out: set[str] = set()
    for token in paths.split():
        target = root / token
        if target.is_dir():
            out |= _test_modules_under(root, target)
        else:
            out |= {str(f.relative_to(root)) for f in root.glob(token) if f.is_file()}
    return out


def test_pytest_shard_union_equals_recursive_collection() -> None:
    root = WORKFLOW.parents[2]
    py_tests = _workflow()["jobs"]["py-tests"]
    sharded: set[str] = set()
    for item in py_tests["strategy"]["matrix"]["include"]:
        sharded |= _expand_shard_paths(root, str(item["paths"]))
    on_disk: set[str] = set()
    for base in PY_COLLECTION_ROOTS:
        on_disk |= _test_modules_under(root, root / base)
    assert on_disk, "collection roots moved; update PY_COLLECTION_ROOTS"
    missing = sorted(on_disk - sharded)
    assert not missing, (
        f"{len(missing)} test modules are collected by NO pytest shard "
        f"(CI reads green without running them): {missing[:6]}"
    )
    stray = sorted(sharded - on_disk)
    assert not stray, f"shard globs reach outside the collection roots: {stray[:6]}"
