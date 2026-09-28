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
