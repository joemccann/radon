"""Nothing may reach the production VPS before the test gate is green.

The ``ci.yml`` header states the contract: ``git push origin main`` only
reaches the VPS if the full Vitest + pytest suites pass. Any job that opens an
SSH session to ``VPS_HOST`` executes pushed code on the live trading host, so
the contract is only real if EVERY such job waits on the same gate ``deploy``
waits on (REL-070 / R-201).
"""

from __future__ import annotations

from pathlib import Path

import yaml


WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"

# The gate jobs that stand between a push and the production host.
# NOT a hard-coded list. Hard-coding the six names `stage-release` happened to
# wait on made the invariant trivially true of itself: `web-coverage` and
# `py-coverage` were added to `deploy.needs` and to no other host-touching job,
# and this test stayed green because they were not in the frozenset either.
#
# The real invariant is relational — every job that reaches the production host
# waits on everything `deploy` waits on — so it is DERIVED from `deploy.needs`,
# and a future gate job added to `deploy` alone now fails the check. R-299.
def _gate_jobs(jobs: dict) -> set[str]:
    deploy = jobs.get("deploy")
    assert deploy is not None, "the workflow has no `deploy` job to derive the gate from"
    gate = _needs(deploy)
    assert gate, "`deploy` waits on nothing; there is no gate to enforce"
    # `deploy` waits on the host-touching jobs themselves; a job cannot wait on
    # itself, and needing a peer host job is not a substitute for the gate.
    return gate - set(_production_host_jobs().keys())


def _workflow() -> dict:
    # BaseLoader avoids YAML 1.1 coercing GitHub's top-level ``on`` key to bool.
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _needs(job: dict) -> set[str]:
    needs = job.get("needs")
    if needs is None:
        return set()
    if isinstance(needs, str):
        return {needs}
    return set(needs)


def _touches_production_host(job: dict) -> bool:
    return "secrets.VPS_HOST" in yaml.safe_dump(job, default_flow_style=False)


def _production_host_jobs() -> dict[str, dict]:
    jobs = _workflow()["jobs"]
    return {name: job for name, job in jobs.items() if _touches_production_host(job)}


def test_the_workflow_has_jobs_that_reach_the_production_host() -> None:
    # Guards the two tests below from passing vacuously if the SSH action or
    # the secret name is ever renamed.
    assert _production_host_jobs(), "no job references secrets.VPS_HOST"


def test_every_job_that_ssh_es_to_production_waits_on_the_full_gate() -> None:
    jobs = _workflow()["jobs"]
    gate = _gate_jobs(jobs)
    offenders = {}
    for name, job in _production_host_jobs().items():
        missing = gate - _needs(job)
        if missing:
            offenders[name] = sorted(missing)
    assert not offenders, (
        "these jobs execute pushed code on the live trading host without "
        f"waiting on the full test gate: {offenders}"
    )


def test_the_coverage_gates_are_part_of_the_gate_today() -> None:
    """Names the two jobs R-299 found missing, so a silent removal is caught."""
    gate = _gate_jobs(_workflow()["jobs"])
    assert {"web-coverage", "py-coverage"} <= gate


def test_a_new_gate_job_on_deploy_alone_fails_the_check() -> None:
    """Fault injection: the derived gate must actually bind the other jobs.

    With the old hard-coded frozenset this passed vacuously — a job added to
    `deploy.needs` and nowhere else was invisible to the invariant.
    """
    workflow = _workflow()
    jobs = workflow["jobs"]
    jobs["deploy"]["needs"] = list(_needs(jobs["deploy"])) + ["a-brand-new-gate"]

    gate = _gate_jobs(jobs)
    assert "a-brand-new-gate" in gate

    host_jobs = {n: j for n, j in jobs.items() if _touches_production_host(j)}
    unguarded = [n for n, j in host_jobs.items() if n != "deploy" and "a-brand-new-gate" not in _needs(j)]
    assert unguarded, (
        "the synthetic gate job was not missing from any peer host job, so this "
        "test cannot prove the check would catch it"
    )


def test_no_production_host_job_swallows_its_own_failure() -> None:
    """``continue-on-error`` on a job that touches production hides a breach.

    A hostile or broken side effect on the VPS must surface as a red job, not
    as a normal-looking workflow run.
    """
    offenders = [
        name
        for name, job in _production_host_jobs().items()
        if str(job.get("continue-on-error", "false")).lower() == "true"
    ]
    assert not offenders, (
        f"{offenders} run against the production host with continue-on-error: "
        "a failure there is invisible to the operator"
    )
