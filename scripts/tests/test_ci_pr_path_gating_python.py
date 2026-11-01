"""Step-level path gating for the Python required checks.

`pytest (*)`, `pytest (cloud *)` and `pytest coverage ratchet` are required
contexts, so their JOBS must run on every pull_request and merge_group: a
job-level false condition never expands the matrix and the required names
never report (see test_required_matrix_checks_run_for_every_pr_and_filter_pushes).
8 of 109 PRs changed no Python path, yet each paid the full Python matrix.
The gate therefore moves one level down: on a PR whose path filter says
`python != 'true'` each job runs one notice step and reports green; every
other step carries `needs.changes.outputs.python == 'true'`. path_filter.py
fails closed (an unresolvable range selects both gates), and on push the job
level already requires python == 'true', so pushes run exactly what they ran
before.

Conditions are evaluated here with a whitelisted boolean walker over the
literal atoms these jobs use; nothing from the workflow is executed.
"""

from __future__ import annotations

import ast
import itertools
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

GATED_JOBS = ("py-tests", "cloud-tests", "py-coverage")
PYTHON_CHANGED = "needs.changes.outputs.python == 'true'"
PYTHON_UNCHANGED = "needs.changes.outputs.python != 'true'"
EVENTS = ("pull_request", "merge_group", "push")
STATES = ("ok", "failed", "cancelled")

# What each step ran on before step gating, for a run whose python output is
# 'true'. None is the implicit `success()`. The caddy install used to be
# limited to `al` / `edge`; it now runs on every cloud shard so no module in
# a new shard skips for want of the binary.
BASELINE = {
    ("py-tests", "Evaluate hosted MCP golden contracts"): "!cancelled() && matrix.shard == 'scripts-jm'",
    ("py-tests", "mcp-golden-contract-report"): "!cancelled() && matrix.shard == 'scripts-jm'",
    ("py-coverage", "Demo isolation guard"): "github.event_name == 'push'",
}
STATUS_FUNCTIONS = ("success()", "failure()", "cancelled()", "always()")


def _jobs() -> dict:
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)["jobs"]


def _label(step: dict) -> str:
    return step.get("name") or step.get("with", {}).get("name") or step.get("uses", "")


def _shards(job: dict) -> list[str]:
    matrix = job.get("strategy", {}).get("matrix", {})
    return [str(shard) for shard in matrix.get("shard", [])] or [""]


def _atom(match: re.Match, ctx: dict) -> str:
    key, op, value = match.group(1), match.group(2), match.group(3)
    known = {
        "github.event_name": ctx["event"],
        "matrix.shard": ctx["shard"],
        "needs.changes.outputs.python": ctx["python"],
    }
    assert key in known, f"unmodelled atom {key!r}; extend this evaluator"
    return str((known[key] == value) == (op == "=="))


def _walk(node: ast.AST) -> bool:
    if isinstance(node, ast.Expression):
        return _walk(node.body)
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And):
        return all(_walk(value) for value in node.values)
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        return any(_walk(value) for value in node.values)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _walk(node.operand)
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return node.value
    raise AssertionError(f"unsupported expression node {ast.dump(node)}")


def _runs(condition: str | None, ctx: dict) -> bool:
    """GitHub step semantics: no status function means `success() && (...)`."""
    text = (condition or "").strip()
    if text.startswith("${{") and text.endswith("}}"):
        text = text[3:-2].strip()
    if not text:
        text = "success()"
    elif not any(fn in text for fn in STATUS_FUNCTIONS):
        text = f"success() && ({text})"
    state = ctx["state"]
    status = {
        "success()": state == "ok",
        "failure()": state == "failed",
        "cancelled()": state == "cancelled",
        "always()": True,
    }
    for fn, value in status.items():
        text = text.replace(fn, str(value))
    text = re.sub(r"([a-z_][\w.-]*)\s*(==|!=)\s*'([^']*)'", lambda m: _atom(m, ctx), text)
    text = text.replace("&&", " and ").replace("||", " or ").replace("!", " not ")
    return _walk(ast.parse(text.strip(), mode="eval"))


def _job_runs(name: str, ctx: dict) -> bool:
    if name == "py-coverage":
        # needs.py-tests.result == 'success': py-tests ran (job level) and passed.
        return _job_runs("py-tests", ctx)
    return ctx["event"] != "push" or ctx["python"] == "true"


def _contexts(job: dict):
    for event, python, shard, state in itertools.product(
        EVENTS, ("true", "false"), _shards(job), STATES
    ):
        yield {"event": event, "python": python, "shard": shard, "state": state}


@pytest.mark.parametrize("name", GATED_JOBS)
def test_first_step_is_a_notice_only_skip_report(name: str) -> None:
    first = _jobs()[name]["steps"][0]
    assert first.get("if") == PYTHON_UNCHANGED, first
    assert "uses" not in first, "the skip path must not check out or install anything"
    run = first["run"].strip()
    assert run.startswith('echo "::notice'), run
    assert len(run.splitlines()) == 1, run


@pytest.mark.parametrize("name", GATED_JOBS)
def test_every_other_step_requires_a_python_change(name: str) -> None:
    for step in _jobs()[name]["steps"][1:]:
        assert PYTHON_CHANGED in str(step.get("if", "")), (name, _label(step))


@pytest.mark.parametrize("name", GATED_JOBS)
def test_step_gating_matches_the_path_filter_on_every_event(name: str) -> None:
    job = _jobs()[name]
    notice, *rest = job["steps"]
    for ctx in _contexts(job):
        if not _job_runs(name, ctx):
            continue
        changed = ctx["python"] == "true"
        first_ctx = dict(ctx, state="ok")  # nothing precedes the first step
        assert _runs(notice.get("if"), first_ctx) is (not changed), (name, ctx)
        for step in rest:
            baseline = BASELINE.get((name, _label(step)))
            expected = changed and _runs(baseline, ctx)
            assert _runs(step.get("if"), ctx) is expected, (name, _label(step), ctx, step.get("if"))


@pytest.mark.parametrize("name", GATED_JOBS)
def test_pushes_run_every_step_they_ran_before(name: str) -> None:
    """On push the job only exists when python == 'true'; the gate is a no-op."""
    job = _jobs()[name]
    for ctx in _contexts(job):
        if ctx["event"] != "push" or not _job_runs(name, ctx):
            continue
        assert ctx["python"] == "true"
        for step in job["steps"][1:]:
            baseline = BASELINE.get((name, _label(step)))
            assert _runs(step.get("if"), ctx) is _runs(baseline, ctx), (name, _label(step), ctx)


def test_required_python_jobs_still_report_on_every_pull_request() -> None:
    jobs = _jobs()
    for name in ("py-tests", "cloud-tests"):
        for event in ("pull_request", "merge_group"):
            assert _job_runs(name, {"event": event, "python": "false"})
        condition = jobs[name]["if"]
        assert "github.event_name == 'pull_request'" in condition
        assert "github.event_name == 'merge_group'" in condition
        assert PYTHON_CHANGED in condition
    assert jobs["py-coverage"]["if"] == "needs.py-tests.result == 'success'"


def test_every_job_reading_the_python_output_needs_the_changes_job() -> None:
    """A `needs.<job>` reference to a job not in `needs` is an empty string, so
    `python == 'true'` would be false everywhere and the ratchet would skip its
    own work while reporting green."""
    for name, job in _jobs().items():
        steps = job.get("steps") or []
        if any("needs.changes.outputs.python" in str(step.get("if", "")) for step in steps):
            needs = job.get("needs", [])
            needs = needs if isinstance(needs, list) else [needs]
            assert "changes" in needs, name
    assert "py-tests" in _jobs()["py-coverage"]["needs"]


def test_evaluator_has_teeth() -> None:
    ctx = {"event": "push", "python": "true", "shard": "scripts-jm", "state": "failed"}
    assert _runs(None, ctx) is False
    assert _runs("${{ !cancelled() && matrix.shard == 'scripts-jm' }}", ctx) is True
    assert _runs("github.event_name == 'push' && " + PYTHON_CHANGED, dict(ctx, state="ok")) is True
    assert _runs(PYTHON_UNCHANGED, dict(ctx, state="ok")) is False
