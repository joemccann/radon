"""Deployment workflow concurrency contracts.

The 2026-07-08 production outage was caused by workflow-level
``cancel-in-progress`` terminating the SSH deploy after it had stopped services.
Test jobs may still cancel superseded work, but a deploy that has started must
finish and every deploy must name the exact commit it intends to release.
"""

from __future__ import annotations

from pathlib import Path

import yaml


WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
TEST_JOBS = ("secret-scan", "web-tests", "py-tests", "perimeter-smoke")


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


def test_ci_runs_cloud_infra_pytest() -> None:
    py_tests = _workflow()["jobs"]["py-tests"]
    commands = "\n".join(str(step.get("run", "")) for step in py_tests["steps"])
    assert "pytest cloud/tests" in commands


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
        install_lines = [line.strip() for line in commands.splitlines() if "bun install" in line]
        assert install_lines == [
            "bun install --frozen-lockfile",
            "bun install --frozen-lockfile",
        ]
