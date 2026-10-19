"""Every required check must report on GitHub's `merge_group` event.

The main branch is merged through a merge queue (ruleset `main-merge-queue`,
2026-10-19). The queue builds a temporary `gh-readonly-queue/main/*` branch and
fires `merge_group`, not `pull_request`; a required check whose workflow never
runs on that event parks the queue entry until `check_response_timeout_minutes`
expires. These tests pin the wiring so a future edit cannot silently drop it.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
_CI = _ROOT / ".github" / "workflows" / "ci.yml"
_PR_BODY = _ROOT / ".github" / "workflows" / "pr-body.yml"

# Jobs that run unconditionally on a PR so the required contexts always report.
_PR_UNCONDITIONAL_JOBS = ("web-tests", "py-tests", "cloud-tests", "perimeter-smoke")


def _load(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _ci_on() -> dict:
    return _load(_CI)["on"]


def test_ci_workflow_triggers_on_merge_group() -> None:
    on = _ci_on()
    assert "merge_group" in on
    assert "pull_request" in on, "PRs still run the suite before entering the queue"
    assert on["push"]["branches"] == ["main"]


def test_required_test_jobs_run_unconditionally_on_merge_group() -> None:
    jobs = _load(_CI)["jobs"]
    for name in _PR_UNCONDITIONAL_JOBS:
        condition = jobs[name]["if"]
        assert "github.event_name == 'pull_request'" in condition, name
        assert "github.event_name == 'merge_group'" in condition, (
            f"{name} must run on the queue's merge result, not only on the PR"
        )


def test_secret_scan_scopes_merge_group_to_base_head() -> None:
    job = _load(_CI)["jobs"]["secret-scan"]
    scan = next(s for s in job["steps"] if "gitleaks detect" in str(s.get("run", "")))
    env = scan["env"]
    assert "github.event.merge_group.base_sha" in env["PR_BASE"]
    assert "github.event.merge_group.head_sha" in env["PR_HEAD"]
    assert '"$EVENT_NAME" = "merge_group"' in scan["run"]


def test_path_filter_uses_the_merge_group_base() -> None:
    changes = _load(_CI)["jobs"]["changes"]
    (filter_step,) = [s for s in changes["steps"] if s.get("id") == "filter"]
    assert "github.event.merge_group.base_sha" in filter_step["env"]["BASE_SHA"]


def test_docs_contract_base_resolves_on_merge_group() -> None:
    env = _load(_CI)["jobs"]["py-tests"]["env"]
    base = env["DOCS_CONTRACT_BASE"]
    assert "github.event.pull_request.base.sha" in base
    assert "github.event.merge_group.base_sha" in base
    assert "github.event.before" in base, "push keeps its previous-tip diff base"


def test_deploy_jobs_stay_push_only() -> None:
    jobs = _load(_CI)["jobs"]
    for name in ("app-images", "stage-release", "prepull-images", "deploy", "release-tag"):
        condition = " ".join(jobs[name]["if"].split())
        assert "github.event_name == 'push'" in condition, name
        assert "merge_group" not in condition, f"{name} must never run on a queue branch"


def test_pr_body_check_reports_on_merge_group_without_a_body() -> None:
    wf = _load(_PR_BODY)
    assert "merge_group" in wf["on"]
    job = wf["jobs"]["pr-body"]
    check = next(s for s in job["steps"] if "check_pr_body.py" in s.get("run", ""))
    assert check["if"] == "github.event_name == 'pull_request'", (
        "a merge group has no PR body; the job must succeed on it without the check"
    )
    assert "github.ref" in wf["concurrency"]["group"], (
        "a queue entry has no PR number; without a fallback every entry shares one "
        "group and cancel-in-progress kills its siblings"
    )
