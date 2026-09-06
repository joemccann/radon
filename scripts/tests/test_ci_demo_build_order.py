"""T-448 / T-475 (P2) — demo bundle ISOLATION in e2e-financial-smoke.

The job builds the prod bundle, runs the curated prod specs, then rebuilds
with NEXT_PUBLIC_RADON_DEMO=1 for demo-workstation-data.spec.ts. The second
build overwrites web/.next, so any step (or failure()-gated retry) executing
after it runs against the demo bundle. The invariant is isolation, not mere
ordering: the demo build and demo spec must be the trailing contiguous pair,
BOTH must carry NEXT_PUBLIC_RADON_DEMO=1, and no step may combine failure()
semantics with a `playwright test` re-run (which would replay prod specs
against whichever bundle last landed in web/.next).
"""

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"

DEMO_SPEC = "demo-workstation-data.spec.ts"


def _steps(path=WORKFLOW):
    workflow = yaml.safe_load(Path(path).read_text())
    return workflow["jobs"]["e2e-financial-smoke"]["steps"]


def _index(steps, predicate, label):
    for i, step in enumerate(steps):
        if predicate(step):
            return i
    raise AssertionError(f"no step matching {label} in e2e-financial-smoke")


def _demo_build_index(steps):
    return _index(
        steps,
        lambda s: "run build" in str(s.get("run", ""))
        and DEMO_SPEC not in str(s.get("run", "")),
        "demo build (a `run build` step distinct from the demo spec step)",
    )


def _demo_spec_index(steps):
    return _index(
        steps, lambda s: DEMO_SPEC in str(s.get("run", "")), "demo spec"
    )


def test_demo_build_and_spec_are_the_trailing_contiguous_pair():
    steps = _steps()
    demo_spec = _demo_spec_index(steps)
    assert demo_spec == len(steps) - 1, (
        "the demo spec must be the LAST step of e2e-financial-smoke; anything "
        "after it would execute against the demo bundle"
    )
    demo_build = demo_spec - 1
    assert "run build" in str(steps[demo_build].get("run", "")), (
        "the step immediately before the demo spec must be the demo rebuild; "
        "any step between them runs against an ambiguous bundle"
    )


def test_both_demo_steps_carry_the_demo_env_flag():
    steps = _steps()
    demo_spec = _demo_spec_index(steps)
    demo_build = demo_spec - 1
    for label, idx in (("demo build", demo_build), ("demo spec", demo_spec)):
        env = steps[idx].get("env") or {}
        assert env.get("NEXT_PUBLIC_RADON_DEMO") == "1", (
            f"the {label} step (index {idx}) must set NEXT_PUBLIC_RADON_DEMO "
            '"1"; without it the step silently runs against the prod bundle '
            "and the demo spec asserts nothing about demo mode"
        )


def test_no_step_combines_failure_with_a_playwright_rerun():
    steps = _steps()
    for i, step in enumerate(steps):
        cond = str(step.get("if", ""))
        run = str(step.get("run", ""))
        assert not ("failure()" in cond and "playwright test" in run), (
            f"step {i} ({step.get('name')}) re-runs `playwright test` under "
            "failure(); a failure()-gated re-run after the demo rebuild would "
            "replay specs against the demo bundle in web/.next"
        )
        if "continue-on-error" in step:
            assert "playwright test" not in run, (
                f"step {i} ({step.get('name')}) marks a `playwright test` run "
                "continue-on-error, inviting a later re-invocation against a "
                "swapped bundle"
            )
