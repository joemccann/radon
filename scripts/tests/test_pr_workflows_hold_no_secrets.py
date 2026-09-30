"""No pull_request-triggered job may receive a repository secret.

This is the invariant that makes an untrusted branch in this public repository
harmless in CI: `pull_request` jobs run attacker-influenceable tests and
scripts, so the only reason they cannot exfiltrate anything is that every
secret-bearing job or step sits behind `github.event_name == 'push'` and
`push` is restricted to `main` (which is reached only after a human merge).
Until now that was held by hand-written `if:` expressions with no test:
deleting one guard, or adding a secret to a test job, would have been silently
green. DS-2026-09-28-02.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"

# `GITHUB_TOKEN` is not a repository secret: it is a per-run token whose
# permissions this repository defaults to `contents: read`.
SECRET_RE = re.compile(r"secrets\.(?!GITHUB_TOKEN\b)([A-Za-z_][A-Za-z0-9_]*)")
PUSH_GUARD = "github.event_name == 'push'"


def _load(text: str) -> dict:
    # BaseLoader: YAML 1.1 would coerce GitHub's top-level `on` key to `True`.
    return yaml.load(text, Loader=yaml.BaseLoader) or {}


def _triggers(wf: dict) -> set[str]:
    on = wf.get("on", wf.get(True, {}))
    if isinstance(on, str):
        return {on}
    return set(on or ())


def _needs(job: dict) -> set[str]:
    needs = job.get("needs") or []
    return {needs} if isinstance(needs, str) else set(needs)


def _secrets_in(node) -> set[str]:
    return set(SECRET_RE.findall(yaml.safe_dump(node, default_flow_style=False)))


def _push_only(name: str, jobs: dict, seen: frozenset[str] = frozenset()) -> bool:
    """True when this job can only run on a push (directly or via `needs`)."""
    if name in seen or name not in jobs:
        return False
    job = jobs[name]
    if PUSH_GUARD in str(job.get("if", "")):
        return True
    needs = _needs(job)
    return bool(needs) and any(_push_only(n, jobs, seen | {name}) for n in needs)


def unguarded_secrets(text: str) -> list[str]:
    """Secret references a `pull_request` run could reach, as `job[/step]: SECRET`."""
    wf = _load(text)
    if not _triggers(wf) & {"pull_request", "pull_request_target"}:
        return []
    bad: list[str] = []
    for name, job in (wf.get("jobs") or {}).items():
        if _push_only(name, wf["jobs"]):
            continue
        steps = job.pop("steps", []) if isinstance(job, dict) else []
        for secret in sorted(_secrets_in(job)):
            bad.append(f"{name}: {secret}")
        for i, step in enumerate(steps):
            if PUSH_GUARD in str(step.get("if", "")):
                continue
            label = step.get("name") or step.get("uses") or f"step {i}"
            for secret in sorted(_secrets_in(step)):
                bad.append(f"{name}/{label}: {secret}")
    return bad


@pytest.mark.parametrize("path", sorted(WORKFLOWS.glob("*.yml")), ids=lambda p: p.name)
def test_no_pull_request_reachable_job_receives_a_secret(path: Path) -> None:
    assert unguarded_secrets(path.read_text(encoding="utf-8")) == []


def test_pull_request_target_is_not_used() -> None:
    # `pull_request_target` runs with the base repository's secrets against a
    # head the author controls. Nothing in this repository may use it.
    for path in WORKFLOWS.glob("*.yml"):
        assert "pull_request_target" not in _triggers(_load(path.read_text(encoding="utf-8"))), path.name


def test_dropping_the_push_guard_from_a_secret_step_is_caught() -> None:
    text = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    assert f"if: {PUSH_GUARD}" in text, "ci.yml no longer guards a secret step by event name"
    mutated = text.replace(f"if: {PUSH_GUARD}", "if: always()", 1)
    assert unguarded_secrets(mutated), "the check does not notice a removed step guard"


def test_dropping_the_push_guard_from_a_deploy_job_is_caught() -> None:
    text = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    mutated = text.replace(PUSH_GUARD, "always()")
    assert unguarded_secrets(mutated), "the check does not notice a removed job guard"


def test_a_secret_added_to_a_pull_request_test_job_is_caught() -> None:
    text = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    mutated = text.replace(
        "      NODE_ENV: test\n",
        "      NODE_ENV: test\n      X: ${{ secrets.VPS_SSH_KEY }}\n",
        1,
    )
    assert mutated != text, "ci.yml no longer has the test job env this mutation targets"
    assert unguarded_secrets(mutated) == ["web-tests: VPS_SSH_KEY"]


# --- Every non-main trigger, not only pull_request (F20260930-A01) ----------
#
# `workflow_dispatch` (and `workflow_call`, `repository_dispatch`, a push to any
# branch) runs the workflow and the repository code from a ref the caller
# picks. Anyone with Write can push a branch and dispatch on it, so a
# secret-bearing job reachable that way hands its secrets to that branch's
# code. Only `schedule` (default branch) and a push restricted to `main` are
# safe on their own; every other trigger needs the job to check the ref.

MAIN_GUARD = "github.ref == 'refs/heads/main'"
# Contexts a dispatcher or PR author controls. Expanded inside `run:`, they are
# pasted into the shell before it parses the line (expression injection).
UNTRUSTED_EXPR_RE = re.compile(r"\$\{\{[^}]*\b(inputs\.|github\.event\.|github\.head_ref)")
# Secret-bearing dispatch workflows: no `${{ }}` in `run:` at all.
SECRET_DISPATCH_WORKFLOWS = ("research-pdf-cut.yml", "external-health-probe.yml")


def _push_main_only(wf: dict) -> bool:
    on = wf.get("on", wf.get(True, {}))
    push = on.get("push") if isinstance(on, dict) else None
    if not isinstance(push, dict):
        return False
    return (
        list(push.get("branches") or []) == ["main"]
        and not {"tags", "branches-ignore", "tags-ignore"} & set(push)
    )


def _non_main_triggers(wf: dict) -> set[str]:
    safe = {"schedule"} | ({"push"} if _push_main_only(wf) else set())
    return _triggers(wf) - safe


def _main_guarded(cond: str, wf: dict) -> bool:
    # A push guard means main when push itself is restricted to main.
    return MAIN_GUARD in cond or (PUSH_GUARD in cond and _push_main_only(wf))


def _main_only(name: str, wf: dict, seen: frozenset[str] = frozenset()) -> bool:
    jobs = wf.get("jobs") or {}
    if name in seen or name not in jobs:
        return False
    job = jobs[name]
    if _main_guarded(str(job.get("if", "")), wf):
        return True
    needs = _needs(job)
    return bool(needs) and any(_main_only(n, wf, seen | {name}) for n in needs)


def non_main_reachable_secrets(text: str) -> list[str]:
    """Secrets a non-main ref could reach, as `job[/step]: SECRET`."""
    wf = _load(text)
    if not _non_main_triggers(wf):
        return []
    bad: list[str] = []
    for name, job in (wf.get("jobs") or {}).items():
        if not isinstance(job, dict) or _main_only(name, wf):
            continue
        job = dict(job)
        steps = job.pop("steps", [])
        for secret in sorted(_secrets_in(job)):
            bad.append(f"{name}: {secret}")
        for i, step in enumerate(steps):
            if _main_guarded(str(step.get("if", "")), wf):
                continue
            label = step.get("name") or step.get("uses") or f"step {i}"
            for secret in sorted(_secrets_in(step)):
                bad.append(f"{name}/{label}: {secret}")
    return bad


def untrusted_run_interpolations(text: str, *, any_expression: bool = False) -> list[str]:
    """`run:` blocks that paste an expression into the shell."""
    wf = _load(text)
    bad: list[str] = []
    for name, job in (wf.get("jobs") or {}).items():
        for i, step in enumerate((job or {}).get("steps") or []):
            run = str(step.get("run", ""))
            hit = "${{" in run if any_expression else UNTRUSTED_EXPR_RE.search(run)
            if hit:
                bad.append(f"{name}/{step.get('name') or f'step {i}'}")
    return bad


@pytest.mark.parametrize("path", sorted(WORKFLOWS.glob("*.yml")), ids=lambda p: p.name)
def test_no_non_main_reachable_job_receives_a_secret(path: Path) -> None:
    assert non_main_reachable_secrets(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("path", sorted(WORKFLOWS.glob("*.yml")), ids=lambda p: p.name)
def test_no_run_block_interpolates_caller_controlled_input(path: Path) -> None:
    assert untrusted_run_interpolations(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("name", SECRET_DISPATCH_WORKFLOWS)
def test_secret_bearing_dispatch_workflows_pass_values_only_through_env(name: str) -> None:
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    assert "workflow_dispatch" in _triggers(_load(text))
    assert untrusted_run_interpolations(text, any_expression=True) == []


def test_research_pdf_cut_validates_the_date_and_publishes_counts_only() -> None:
    text = (WORKFLOWS / "research-pdf-cut.yml").read_text(encoding="utf-8")
    wf = _load(text)
    step = next(s for s in wf["jobs"]["cut"]["steps"] if "research.cut_report" in s.get("run", ""))
    assert step["env"]["CUT_DATE"] == "${{ inputs.date }}"
    run = step["run"]
    assert "^[0-9]{4}-[0-9]{2}-[0-9]{2}$|^$" in run
    assert run.index("[0-9]{4}") < run.index("research.cut_report"), "validate before use"
    assert '--date "$CUT_DATE"' in run
    # The artifact of this public repository must not carry document identifiers.
    assert "--counts-only" in run


def test_dropping_the_main_guard_from_a_dispatch_job_is_caught() -> None:
    for name in SECRET_DISPATCH_WORKFLOWS:
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        assert f"if: {MAIN_GUARD}" in text, f"{name} no longer carries the main-ref guard"
        mutated = text.replace(f"if: {MAIN_GUARD}", "if: always()", 1)
        assert non_main_reachable_secrets(mutated), f"{name}: removed guard not noticed"


def test_a_dispatch_trigger_on_a_push_guarded_workflow_is_caught() -> None:
    wf = (
        "on:\n  workflow_dispatch: {}\n"
        "jobs:\n  j:\n    if: github.event_name == 'push'\n    runs-on: x\n"
        "    steps:\n      - run: echo\n        env:\n          T: ${{ secrets.T }}\n"
    )
    # event_name == 'push' excludes dispatch here, but there is no main-only
    # push trigger, so it is not accepted as a main guard.
    assert non_main_reachable_secrets(wf) == ["j/step 0: T"]


def test_a_push_to_any_branch_counts_as_a_non_main_trigger() -> None:
    wf = (
        "on:\n  push: {}\n"
        "jobs:\n  j:\n    runs-on: x\n    env:\n      T: ${{ secrets.T }}\n    steps: []\n"
    )
    assert non_main_reachable_secrets(wf) == ["j: T"]
    safe = wf.replace("push: {}", "push:\n    branches: [main]")
    assert non_main_reachable_secrets(safe) == []


def test_inputs_pasted_into_run_is_caught() -> None:
    text = (WORKFLOWS / "research-pdf-cut.yml").read_text(encoding="utf-8")
    mutated = text.replace('"$CUT_DATE" --counts-only', '"${{ inputs.date }}" --counts-only', 1)
    assert mutated != text
    assert untrusted_run_interpolations(mutated) == ["cut/Build daily cut report"]
