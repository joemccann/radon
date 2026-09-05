"""T-448 (P2) — demo rebuild must be the LAST work in e2e-financial-smoke.

The job builds the prod bundle, runs the curated prod specs, then rebuilds
with NEXT_PUBLIC_RADON_DEMO=1 for demo-workstation-data.spec.ts. The second
build overwrites web/.next, so any step (or retry) that executes after it
runs against the demo bundle. Pin the safe order: prod build < prod specs <
trace upload < demo build < demo spec, with the demo spec as the final step.
"""

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"


def _steps():
    workflow = yaml.safe_load(WORKFLOW.read_text())
    return workflow["jobs"]["e2e-financial-smoke"]["steps"]


def _index(steps, predicate, label):
    for i, step in enumerate(steps):
        if predicate(step):
            return i
    raise AssertionError(f"no step matching {label} in e2e-financial-smoke")


def test_demo_build_comes_after_trace_upload():
    steps = _steps()
    trace = _index(
        steps, lambda s: "upload-artifact" in str(s.get("uses", "")), "trace upload"
    )
    demo_build = _index(
        steps,
        lambda s: "build" in str(s.get("run", ""))
        and s.get("env", {}).get("NEXT_PUBLIC_RADON_DEMO") == "1",
        "demo build",
    )
    assert demo_build > trace, (
        "the NEXT_PUBLIC_RADON_DEMO=1 rebuild overwrites web/.next; it must run "
        "AFTER the trace-upload step so retried prod-bundle steps are not "
        f"poisoned (demo build at index {demo_build}, trace upload at {trace})"
    )


def test_demo_spec_is_the_final_step_and_follows_demo_build():
    steps = _steps()
    demo_build = _index(
        steps,
        lambda s: "build" in str(s.get("run", ""))
        and s.get("env", {}).get("NEXT_PUBLIC_RADON_DEMO") == "1",
        "demo build",
    )
    demo_spec = _index(
        steps,
        lambda s: "demo-workstation-data.spec.ts" in str(s.get("run", "")),
        "demo spec",
    )
    assert demo_spec == demo_build + 1, "demo spec must immediately follow demo build"
    assert demo_spec == len(steps) - 1, (
        "the demo spec must be the LAST step of e2e-financial-smoke; anything "
        "after it would execute against the demo bundle"
    )
