"""T-435 — isolated keyless setup-gate Playwright job.

The job must exist, stay keyless (blank Clerk env, no authless flag), use the
dedicated Playwright config and a distDir other than web/.next, stay out of
deploy.needs, and cancel superseded runs by github.ref.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"


def _workflow() -> dict:
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_setup_gate_job_exists_and_is_keyless() -> None:
    jobs = _workflow()["jobs"]
    job = jobs["e2e-setup-gate"]
    env = job.get("env") or {}
    assert env.get("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY") == ""
    assert env.get("CLERK_SECRET_KEY") == ""
    assert env.get("RADON_SETUP_COMPLETE") == ""
    assert env.get("RADON_AUTHLESS_TEST") in (None, "")
    assert env.get("NEXT_DIST_DIR") == ".next-setup-gate"
    assert env.get("NEXT_DIST_DIR") != ".next"

    runs = "\n".join(str(step.get("run", "")) for step in job["steps"])
    assert "--config playwright.setup-gate.config.ts" in runs
    assert "bun run build:setup-gate" in runs

    assert "e2e-setup-gate" not in jobs["deploy"]["needs"]

    concurrency = job["concurrency"]
    assert "github.ref" in concurrency["group"]
    assert concurrency["cancel-in-progress"] == "true"


def test_setup_gate_job_defers_behind_secret_scan() -> None:
    job = _workflow()["jobs"]["e2e-setup-gate"]
    assert "secret-scan" in job["needs"]
    assert "changes" in job["needs"]
