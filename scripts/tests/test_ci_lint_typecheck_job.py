"""The Lint + typecheck job is informational.

It is not a required status check and it is not in deploy.needs. A red or
skipped run must not block a merge or open the production host.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
REQUIRED = ROOT / ".github" / "required-status-checks.json"


def _workflow() -> dict:
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_lint_typecheck_is_present_and_non_required() -> None:
    jobs = _workflow()["jobs"]
    job = jobs["lint-typecheck"]
    assert job["name"] == "Lint + typecheck"
    assert job["runs-on"] == "ubuntu-24.04"
    assert "if" not in job
    assert "lint-typecheck" not in jobs["deploy"]["needs"]
    contexts = json.loads(REQUIRED.read_text(encoding="utf-8"))["contexts"]
    assert job["name"] not in contexts
    commands = "\n".join(str(step.get("run", "")) for step in job["steps"])
    assert "bun run lint:oxlint" in commands
    assert "tsc -p web/tsconfig.json --noEmit" in commands
    assert "tsc -p lib/tsconfig.ci.json --noEmit" in commands
    assert "ubuntu-latest" not in str(job["runs-on"])


def test_lib_tools_ci_project_excludes_only_the_unresolved_module() -> None:
    text = (ROOT / "lib" / "tsconfig.ci.json").read_text(encoding="utf-8")
    assert '"extends": "./tsconfig.json"' in text
    assert "tools/__tests__/**" in text
    assert "tools/pi-tools.ts" in text
    assert "@mariozechner/pi-coding-agent" in text


def test_oxlint_script_uses_the_pinned_config() -> None:
    package = json.loads((ROOT / "web" / "package.json").read_text(encoding="utf-8"))
    assert package["devDependencies"]["oxlint"] == "1.91.0"
    assert package["scripts"]["lint:oxlint"] == (
        "oxlint --config .oxlintrc.json app components lib middleware.ts"
    )
    config = (ROOT / "web" / ".oxlintrc.json").read_text(encoding="utf-8")
    assert "no-restricted-imports" in config
    assert "^node:" in config
    assert "@/lib/order/risk/internal/*" in config
    assert "react/set-state-in-effect" in config
