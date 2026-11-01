"""PR path gating for the web gate happens at STEP level, never job level.

8 of 109 PRs changed only Python (or only web) paths, yet every PR paid the
full web gate: 8 Vitest shards, the coverage merge and the perimeter build.
The cheap fix, a job-level `if` on the path filter, is wrong here: a job-level
false condition on a matrix job never expands the matrix, so the required
contexts "Vitest (shard 1/8)" .. "(shard 8/8)" never report and every PR
blocks (test_required_matrix_checks_run_for_every_pr_and_filter_pushes).

So the job-level conditions stay exactly as they were. Each job's first step
only prints a notice when the filter says web is unchanged, and every other
step (uploads included) is gated on `needs.changes.outputs.web == 'true'`.
On a push the job-level `if` already requires that output, so every step
runs exactly as before. merge_group behaves like pull_request. The filter
fails closed: path_filter.py writes `web=true` for any unknown path.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"

GATE = "needs.changes.outputs.web == 'true'"
NOTICE = "needs.changes.outputs.web != 'true'"
GATED_JOBS = ("web-tests", "perimeter-smoke", "web-coverage")
UNCHANGED_JOB_IFS = {
    "web-tests": (
        "github.event_name == 'pull_request' || github.event_name == 'merge_group'"
        " || needs.changes.outputs.web == 'true'"
    ),
    "perimeter-smoke": (
        "github.event_name == 'pull_request' || github.event_name == 'merge_group'"
        " || needs.changes.outputs.web == 'true'"
    ),
    "web-coverage": "needs.web-tests.result == 'success'",
}


def _jobs() -> dict:
    # BaseLoader keeps GitHub's top-level `on` key a string.
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)["jobs"]


def _strip(expr: str) -> str:
    expr = " ".join(str(expr).split())
    if expr.startswith("${{") and expr.endswith("}}"):
        expr = expr[3:-2].strip()
    return expr


def _top_level(expr: str, op: str) -> list[str]:
    """Split on `op` outside parentheses and quotes."""
    parts, depth, quote, start, i = [], 0, False, 0, 0
    while i < len(expr):
        ch = expr[i]
        if ch == "'":
            quote = not quote
        elif not quote and ch == "(":
            depth += 1
        elif not quote and ch == ")":
            depth -= 1
        elif not quote and depth == 0 and expr.startswith(op, i):
            parts.append(expr[start:i].strip())
            i += len(op)
            start = i
            continue
        i += 1
    parts.append(expr[start:].strip())
    return parts


def _evaluate(expr: str, values: dict[str, str]) -> bool:
    """Evaluate `a == 'x' || b != 'y' && ...` over literal values only.

    Never executes workflow text. An operand this table does not know raises,
    so a reshaped condition fails the test instead of passing by accident.
    """
    expr = _strip(expr)
    disjuncts = _top_level(expr, "||")
    result = False
    for disjunct in disjuncts:
        ok = True
        for clause in _top_level(disjunct, "&&"):
            op = "==" if " == " in clause else "!="
            left, right = (side.strip() for side in clause.split(f" {op} ", 1))
            literal = right.strip("'")
            ok &= (values[left] == literal) if op == "==" else (values[left] != literal)
        result |= ok
    return result


def _notice_and_work(job: dict) -> tuple[dict, list[dict]]:
    steps = job["steps"]
    return steps[0], steps[1:]


def test_job_level_conditions_are_unchanged() -> None:
    jobs = _jobs()
    for name, expected in UNCHANGED_JOB_IFS.items():
        assert _strip(jobs[name]["if"]) == expected, (
            f"{name}: the job-level `if` must keep its semantics. A false "
            "job-level condition on a matrix job never expands the matrix, so "
            "required shard contexts never report and every PR blocks."
        )


def test_first_step_only_prints_a_notice_when_web_is_unchanged() -> None:
    jobs = _jobs()
    for name in GATED_JOBS:
        notice, _ = _notice_and_work(jobs[name])
        assert _strip(notice.get("if", "")) == NOTICE, name
        assert "uses" not in notice, f"{name}: the notice step must not run an action"
        run = str(notice.get("run", "")).strip()
        # One echo of one double-quoted literal: no second command, no
        # substitution, no expression interpolation.
        assert re.fullmatch(r'echo "::notice[^"`$\\]*"', run), (
            f"{name}: the first step must only print a ::notice, got {run!r}"
        )


def test_every_other_step_is_gated_on_the_web_output() -> None:
    jobs = _jobs()
    for name in GATED_JOBS:
        _, work = _notice_and_work(jobs[name])
        assert work, name
        for step in work:
            label = step.get("name") or step.get("uses") or step.get("run")
            condition = _strip(step.get("if", ""))
            assert len(_top_level(condition, "||")) == 1, (
                f"{name} / {label}: a top-level || lets the step run with web "
                f"unchanged: {condition!r}"
            )
            assert GATE in _top_level(condition, "&&"), (
                f"{name} / {label}: every step after the notice must carry "
                f"`{GATE}` as a top-level && conjunct, got {condition!r}"
            )


def test_upload_steps_are_gated_too() -> None:
    jobs = _jobs()
    uploads = [
        (name, step)
        for name in GATED_JOBS
        for step in jobs[name]["steps"]
        if "upload-artifact" in step.get("uses", "")
    ]
    assert uploads, "web-tests uploads its shard coverage; the pin lost sight of it"
    for name, step in uploads:
        assert GATE in _top_level(_strip(step.get("if", "")), "&&"), (
            f"{name}: an ungated upload with `if-no-files-found: error` fails "
            "a web-unchanged PR on a missing coverage directory"
        )


def test_web_coverage_reads_the_path_filter() -> None:
    needs = _jobs()["web-coverage"]["needs"]
    needs = needs if isinstance(needs, list) else [needs]
    assert "changes" in needs, (
        "web-coverage gates its steps on needs.changes.outputs.web; without "
        "`changes` in needs the expression is empty and the merge runs against "
        "shard artifacts that were never uploaded"
    )
    assert "web-tests" in needs


def test_simulated_runs_match_the_contract() -> None:
    """Pushes run every step exactly as before; PRs skip work when web is unchanged."""
    jobs = _jobs()
    for event in ("pull_request", "merge_group", "push"):
        for web in ("true", "false"):
            values = {
                "github.event_name": event,
                "needs.changes.outputs.web": web,
                "needs.web-tests.result": "success",
            }
            web_tests_runs = _evaluate(jobs["web-tests"]["if"], values)
            for name in GATED_JOBS:
                job = jobs[name]
                job_runs = _evaluate(job["if"], values)
                if name == "web-coverage":
                    # Implicit success(): a skipped web-tests skips the merge.
                    job_runs = job_runs and web_tests_runs
                expected_job = event != "push" or web == "true"
                assert job_runs == expected_job, (name, event, web)
                if not job_runs:
                    continue
                notice, work = _notice_and_work(job)
                assert _evaluate(notice["if"], values) == (web != "true"), (name, event, web)
                for step in work:
                    assert _evaluate(step["if"], values) == (web == "true"), (
                        name,
                        event,
                        web,
                        step.get("name") or step.get("uses"),
                    )
