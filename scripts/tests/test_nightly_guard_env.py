"""The gh-guard paths are baked into the shim, never exported to the agent.

An exported RADON_NIGHTLY_REAL_GH in the agent environment names the
unguarded binary and invites redirection around the PR guard; the shim gets
the paths as literals and exports them only to its own children
(nightly_pr_guard.py reads RADON_NIGHTLY_REAL_GH from its environment).
"""

from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent

WRAPPERS = [
    "ci_performance_nightly.sh",
    "documentation_nightly.sh",
    "reliability_weekend.sh",
    "security_nightly.sh",
    "security_deepsec_nightly.sh",
    "testing_weekend.sh",
]


@pytest.mark.parametrize("wrapper", WRAPPERS)
def test_guard_paths_are_not_exported_into_the_agent_env(wrapper: str) -> None:
    text = (SCRIPTS / wrapper).read_text(encoding="utf-8")
    for var in (
        "RADON_NIGHTLY_REAL_GH",
        "RADON_NIGHTLY_GUARD_REPO",
        "RADON_NIGHTLY_GUARD_PYTHON",
    ):
        assert f'export {var}="' not in text, (wrapper, var)
        assert f"printf 'export {var}=%q" in text, (wrapper, var)
