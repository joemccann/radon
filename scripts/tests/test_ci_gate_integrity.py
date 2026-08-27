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
GATE_JOBS = frozenset(
    {
        "secret-scan",
        "changes",
        "web-tests",
        "py-tests",
        "cloud-tests",
        "perimeter-smoke",
    }
)


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
    offenders = {}
    for name, job in _production_host_jobs().items():
        missing = GATE_JOBS - _needs(job)
        if missing:
            offenders[name] = sorted(missing)
    assert not offenders, (
        "these jobs execute pushed code on the live trading host without "
        f"waiting on the full test gate: {offenders}"
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
