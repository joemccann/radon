"""Nothing may reach the production VPS before the test gate is green.

The ``ci.yml`` header states the contract: ``git push origin main`` only
reaches the VPS if the full Vitest + pytest suites pass. Any job that opens an
SSH session to ``VPS_HOST`` executes pushed code on the live trading host, so
the contract is only real if EVERY such job waits on the same gate ``deploy``
waits on (REL-070 / R-201).
"""

from __future__ import annotations

import re
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


# R-671 (REL-250): classify by mechanism, not by the one secret name. A job
# reaches a host when it consumes any *HOST* secret, uses a remote-exec
# action, or runs ssh/scp itself; renaming `VPS_HOST` must not silently
# remove a job from the guarded population.
_HOST_SECRET_RE = re.compile(r"secrets\.\w*HOST\w*", re.IGNORECASE)
_REMOTE_EXEC_ACTION_RE = re.compile(r"\b(ssh|scp|sftp|rsync)-action\b", re.IGNORECASE)
_REMOTE_EXEC_RUN_RE = re.compile(r"(?:^|&&|\||;)\s*(ssh|scp|sftp)\s", re.MULTILINE)


def _touches_production_host(job: dict) -> bool:
    if _HOST_SECRET_RE.search(yaml.safe_dump(job, default_flow_style=False)):
        return True
    for step in job.get("steps") or []:
        if _REMOTE_EXEC_ACTION_RE.search(str(step.get("uses", ""))):
            return True
        if _REMOTE_EXEC_RUN_RE.search(str(step.get("run", ""))):
            return True
    return False


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


# ---------------------------------------------------------------------------
# R-324 / REL-113: `needs` alone does not gate. The `if:` expression does.
#
# GitHub drops the implicit `success()` requirement on `needs` as soon as the
# `if:` contains a status function, and `!cancelled()` is one. `stage-release`
# carries `!cancelled()` and lists `web-coverage`/`py-coverage` in `needs`, but
# its `if:` never checks their results — so a failing coverage job correctly
# skipped `deploy` while `stage-release` still SSHed to the live trading host
# and ran the pushed commit's own `deploy.sh`. The checks above compare `needs`
# name sets only, which is why this stayed green through REL-070, R-299/REL-102
# and now R-324. This is the third attempt at the invariant, so it is asserted
# on the mechanism that actually gates.


def _if_expression(job: dict) -> str:
    return str(job.get("if") or "")


def _neutralises_implicit_success(expression: str) -> bool:
    """A status function in `if:` removes GitHub's implicit `success()`."""
    return any(
        fn in expression
        for fn in ("cancelled()", "always()", "failure()", "success()")
    )


def test_every_gate_job_is_named_in_each_host_job_if_expression() -> None:
    gate = _gate_jobs(_workflow()["jobs"])
    offenders: dict[str, list[str]] = {}
    for name, job in _production_host_jobs().items():
        expression = _if_expression(job)
        if not _neutralises_implicit_success(expression):
            # No status function -> GitHub's implicit `success()` still binds
            # every entry in `needs`, so the expression need not repeat them.
            continue
        missing = sorted(dep for dep in gate if dep not in expression)
        if missing:
            offenders[name] = missing
    assert not offenders, (
        "these jobs reach the production host behind an `if:` that neutralises "
        "GitHub's implicit success() but does not check every gate job's "
        f"result, so a RED gate job does not stop them: {offenders}"
    )


def test_the_coverage_gates_are_checked_in_the_stage_release_expression() -> None:
    """Names the two R-324 found missing, so a silent removal is caught."""
    expression = _if_expression(_workflow()["jobs"]["stage-release"])
    assert "needs.web-coverage.result" in expression
    assert "needs.py-coverage.result" in expression


def test_removing_a_gate_from_an_if_expression_alone_fails_the_check() -> None:
    """Fault injection: the check must read the expression, not just `needs`.

    Deletes ONLY the `web-coverage` clause from `stage-release.if`, leaving
    `needs` untouched — the exact shape R-324 describes. The `needs`-based
    checks above stay green on this mutation; this one must not.
    """
    workflow = _workflow()
    jobs = workflow["jobs"]
    stage = jobs["stage-release"]
    # The `if:` is a folded scalar, so it arrives as ONE line — strip the
    # clause, not the line.
    stage["if"] = re.sub(
        r"&&\s*\(needs\.web-coverage\.result[^)]*\)\s*",
        "",
        _if_expression(stage),
    )
    assert "needs.web-coverage.result" not in _if_expression(stage)
    # `needs` is untouched, so the name-set invariant still passes ...
    assert "web-coverage" in _needs(stage)

    gate = _gate_jobs(jobs)
    host_jobs = {n: j for n, j in jobs.items() if _touches_production_host(j)}
    offenders = {}
    for name, job in host_jobs.items():
        expression = _if_expression(job)
        if not _neutralises_implicit_success(expression):
            continue
        missing = sorted(dep for dep in gate if dep not in expression)
        if missing:
            offenders[name] = missing
    # ... but the expression-level check catches it.
    assert offenders.get("stage-release") == ["web-coverage"], offenders


def test_e2e_apt_provisioning_is_bounded() -> None:
    """The e2e container's apt-get step must not hang on a degraded mirror.

    2026-09-05: the unbounded ``apt-get update && apt-get install unzip``
    step (norm 5-8s) hit a degraded Ubuntu mirror twice, running 319s and
    448s and tripling the job wall (CIP-007). Every apt invocation in the
    step must carry a retry cap and an HTTP timeout, and the step itself
    must carry a ``timeout-minutes`` bound so a dead mirror fails fast
    instead of eating the job's 25-minute budget.
    """
    job = _workflow()["jobs"]["e2e-financial-smoke"]
    apt_steps = [
        step
        for step in job["steps"]
        if "apt-get" in str(step.get("run", ""))
    ]
    assert apt_steps, "the e2e job no longer provisions via apt-get; retire this test"
    for step in apt_steps:
        run = step["run"]
        for invocation in re.findall(r"apt-get[^&|;]*", run):
            assert "Acquire::Retries=" in invocation, invocation
            assert "Acquire::http::Timeout=" in invocation, invocation
        assert step.get("timeout-minutes"), step


# R-671 (REL-250): the host-job population must derive from what a job DOES
# (a renamed secret or a remote-exec action must not silently exit it), never
# from the one literal string `secrets.VPS_HOST`.


def test_a_renamed_host_secret_stays_in_the_population() -> None:
    job = {
        "runs-on": "ubuntu-latest",
        "steps": [
            {
                "uses": "appleboy/ssh-action@v1.0.3",
                "with": {"host": "${{ secrets.PROD_HOST }}", "script": "true"},
            }
        ],
    }
    assert _touches_production_host(job), (
        "a job reaching a host via secrets.PROD_HOST left the guarded "
        "population because the classifier matched only secrets.VPS_HOST"
    )


def test_a_remote_exec_action_is_flagged_without_any_host_secret() -> None:
    job = {
        "runs-on": "ubuntu-latest",
        "steps": [
            {"uses": "appleboy/scp-action@v0.1.7", "with": {"host": "203.0.113.7"}}
        ],
    }
    assert _touches_production_host(job)


def test_an_ssh_run_step_is_flagged() -> None:
    job = {"steps": [{"run": "scp dist.tar deploy@prod:/srv/\nssh deploy@prod ./install.sh"}]}
    assert _touches_production_host(job)


def test_the_population_still_covers_every_literal_vps_host_job() -> None:
    jobs = _workflow()["jobs"]
    literal = {
        name
        for name, job in jobs.items()
        if "secrets.VPS_HOST" in yaml.safe_dump(job, default_flow_style=False)
    }
    assert literal, "no job references secrets.VPS_HOST any more; re-derive this pin"
    assert literal <= set(_production_host_jobs()), (
        "the mechanism-derived population lost jobs the literal secret name still finds"
    )


def test_e2e_starts_after_secret_scan_to_stay_under_the_job_slot_cap() -> None:
    """CIP-009: the gate fan-out must not exceed the 20-job concurrency cap.

    2026-09-06: ~25 jobs launched at the gate window against the plan's
    20 concurrent-job cap, so one pytest gate shard queued ~37s for a slot
    and the LATE START, not shard work, set the pytest gate wall
    (runs 34009244091, 33983869958). With the shard merges the t=0 fan-out
    is 20 exactly only if the non-gating Playwright job defers behind
    secret-scan (the app-images pattern), freeing its slot for a gate
    shard. The job must stay non-gating for deploy.
    """
    jobs = _workflow()["jobs"]
    e2e = jobs["e2e-financial-smoke"]
    assert "secret-scan" in e2e["needs"], (
        "the non-gating e2e job must defer behind secret-scan so the t=0 "
        "gate fan-out stays at the 20-job concurrency cap"
    )
    assert "changes" in e2e["needs"]
    assert "e2e-financial-smoke" not in jobs["deploy"]["needs"], (
        "the Playwright smoke stays non-gating; deferral must never make "
        "it a deploy dependency"
    )
