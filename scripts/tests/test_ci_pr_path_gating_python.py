"""The Python required checks do their full work on every PR and merge_group.

`pytest (*)`, `pytest (cloud *)` and `pytest coverage ratchet` must not gate
their steps on the path filter. path_filter.py's `python=false` is a push-time
saving built on one premise: the PR already ran the whole Python matrix. Its
cross-tree inventory (CROSS_TREE_CONTRACTS, route_documentation) finds only
readers that quote a path literal, and Python tests read every tracked tree:

- cloud/tests/test_integration.py::test_no_real_secrets_in_tracked_files and
  scripts/tests/test_subscriptions_only_policy.py scan `git ls-files`;
- scripts/tests/test_model_gateway_no_bypass.py scans tracked web/ sources;
- scripts/tests/test_docs_contract.py globs docs/**/*.md;
- scripts/tests/test_rel021_p2_batch.py (OptionsChainTab.tsx WS_CAP),
  cloud/tests/test_keepalive_ordering.py (web/package.json) and
  cloud/tests/test_env_example.py (web/.env.example) read web files through
  `root / "web/..."`.

A step gate on `python` (2026-11-01) skipped all of them on web-only PRs, and
one on `python || web` still skips the docs ones on a docs-only PR: a new
docs/*.md claiming the host env file is 0600 fails the docs contract on main
and selects python=false, web=false and no contract. A PR that broke
path_filter.py's own classification would also skip test_path_filter.py, the
test that pins it. No PR diff is provably unread by Python, so the path
filter decides pushes only (job-level `if`, which keeps the required matrix
names reporting on every PR).

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
PR_EVENTS = ("pull_request", "merge_group")
FILTER_OUTPUTS = ("true", "false")
STATES = ("ok", "failed", "cancelled")

# The only step conditions these jobs carry. Every other step runs under the
# implicit success(). The caddy install runs on every cloud shard, so a module
# moved between shards never newly skips for want of the binary.
BASELINE = {
    ("py-tests", "Evaluate hosted MCP golden contracts"): "${{ !cancelled() && matrix.shard == 'scripts-jm' }}",
    ("py-tests", "mcp-golden-contract-report"): "${{ !cancelled() && matrix.shard == 'scripts-jm' }}",
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
        "needs.changes.outputs.web": ctx["web"],
        "needs.py-tests.result": ctx.get("py_tests", "success"),
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
    """GitHub semantics: no status function means `success() && (...)`."""
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


def _pr_contexts(job: dict):
    for event, python, web, shard, state in itertools.product(
        PR_EVENTS, FILTER_OUTPUTS, FILTER_OUTPUTS, _shards(job), STATES
    ):
        yield {"event": event, "python": python, "web": web, "shard": shard, "state": state}


@pytest.mark.parametrize("name", GATED_JOBS)
def test_no_step_reads_the_path_filter(name: str) -> None:
    for step in _jobs()[name]["steps"]:
        assert "needs.changes" not in str(step), (name, _label(step))


@pytest.mark.parametrize("name", GATED_JOBS)
def test_step_conditions_are_exactly_the_baseline(name: str) -> None:
    """No notice-only skip step, no added clause: what is not in BASELINE runs
    under the implicit success()."""
    for step in _jobs()[name]["steps"]:
        assert step.get("if") == BASELINE.get((name, _label(step))), (name, _label(step), step.get("if"))
    named = {label for job, label in BASELINE if job == name}
    assert named <= {_label(step) for step in _jobs()[name]["steps"]}, named


@pytest.mark.parametrize("name", GATED_JOBS)
def test_every_pr_runs_every_step_whatever_the_filter_says(name: str) -> None:
    """Web-only (python=false, web=true), docs-only (false, false) and a
    misclassifying filter all run the same steps as a Python change."""
    jobs = _jobs()
    job = jobs[name]
    gate = "py-tests" if name == "py-coverage" else name
    for ctx in _pr_contexts(job):
        assert _runs(jobs[gate]["if"], dict(ctx, state="ok")), (gate, ctx)
        if name == "py-coverage":
            assert _runs(job["if"], dict(ctx, state="ok")), ctx
        for step in job["steps"]:
            expected = _runs(BASELINE.get((name, _label(step))), dict(ctx, python="true", web="true"))
            assert _runs(step.get("if"), ctx) is expected, (name, _label(step), ctx)


def test_the_path_filter_still_decides_pushes_at_job_level() -> None:
    """Required matrix names report on every PR; a push runs Python only when
    the filter selects it (test_required_matrix_checks_run_for_every_pr_and_filter_pushes)."""
    jobs = _jobs()
    for name in ("py-tests", "cloud-tests"):
        assert jobs[name]["needs"] == ["changes"]
        for python, web in itertools.product(FILTER_OUTPUTS, FILTER_OUTPUTS):
            ctx = {"event": "push", "python": python, "web": web, "shard": "", "state": "ok"}
            assert _runs(jobs[name]["if"], ctx) is (python == "true"), (name, python, web)
    assert jobs["py-coverage"]["if"] == "needs.py-tests.result == 'success'"
    assert jobs["py-coverage"]["needs"] == ["py-tests"]


def test_evaluator_has_teeth() -> None:
    ctx = {"event": "pull_request", "python": "false", "web": "false", "shard": "scripts-jm", "state": "failed"}
    assert _runs(None, ctx) is False
    assert _runs("${{ !cancelled() && matrix.shard == 'scripts-jm' }}", ctx) is True
    # Both rejected designs skip a docs-only PR.
    ok = dict(ctx, state="ok")
    assert _runs("needs.changes.outputs.python == 'true'", ok) is False
    assert _runs(
        "needs.changes.outputs.python == 'true' || needs.changes.outputs.web == 'true'", ok
    ) is False
