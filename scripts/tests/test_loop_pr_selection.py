"""resolve_pr_url must never select a fork PR.

The repo is public: anyone can open a PR from a fork whose head branch is
named with a loop's prefix (``security/...``), and until 2026-09-08 the
newest-updated such PR won ``resolve_pr_url``'s selection — its URL then
flowed into every dead-man comment and Pushover page as the loop's own PR.
Only a same-repo head (which requires write access) is the loop's PR.

The jq selection program is extracted from each wrapper's ``resolve_pr_url``
and run through the real ``jq`` against fixtures, so the exact program gh
would execute is what is proven — in the house style of the other wrapper
contract tests (stub/fixture at the wire, no network, no gh).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

# loop id -> (wrapper, branch prefix)
LOOPS = {
    "reliability": ("reliability_weekend.sh", "reliability/"),
    "testing": ("testing_weekend.sh", "testing/"),
    "ci-performance": ("ci_performance_nightly.sh", "ci-performance/"),
    "documentation": ("documentation_nightly.sh", "documentation/"),
    "security": ("security_nightly.sh", "security/"),
}
LOOP_IDS = sorted(LOOPS)

JQ = shutil.which("jq")

OWN_URL = "https://github.com/joemccann/radon/pull/301"
FORK_URL = "https://github.com/attacker/radon/pull/9"


def _wrapper_text(loop: str) -> str:
    return (REPO / "scripts" / LOOPS[loop][0]).read_text(encoding="utf-8")


def _selection_program(loop: str) -> str:
    """The jq program resolve_pr_url hands gh via ``-q``, prefix substituted."""
    src = _wrapper_text(loop)
    start = src.index("resolve_pr_url() {")
    end = src.index("\n}", start)
    fn = src[start:end]
    m = re.search(r'-q "((?:[^"\\]|\\.)*)"', fn)
    assert m, f"{loop}: no -q jq program in resolve_pr_url"
    program = m.group(1).replace('\\"', '"')
    return program.replace("$PR_BRANCH_PREFIX", LOOPS[loop][1])


def _select(loop: str, prs: list[dict]) -> str:
    proc = subprocess.run(
        [JQ, "-r", _selection_program(loop)],
        input=json.dumps(prs),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout.strip()
    return "" if out == "null" else out


def _pr(url: str, ref: str, updated: str, cross: bool) -> dict:
    return {
        "url": url,
        "headRefName": ref,
        "updatedAt": updated,
        "isCrossRepository": cross,
    }


@pytest.mark.skipif(JQ is None, reason="jq not installed")
class TestForkPrsAreNeverSelected:
    @pytest.mark.parametrize("loop", LOOP_IDS)
    def test_a_newer_fork_pr_with_a_prefix_branch_is_skipped(self, loop):
        prefix = LOOPS[loop][1]
        picked = _select(loop, [
            _pr(FORK_URL, f"{prefix}2026-09-08", "2026-09-08T09:00:00Z", True),
            _pr(OWN_URL, f"{prefix}2026-09-07", "2026-09-07T01:00:00Z", False),
        ])
        assert picked == OWN_URL, (
            f"{loop}: selection picked {picked!r}; a cross-repository PR "
            "with a prefix-named head branch must never win"
        )

    @pytest.mark.parametrize("loop", LOOP_IDS)
    def test_only_fork_prs_means_no_pr(self, loop):
        prefix = LOOPS[loop][1]
        picked = _select(loop, [
            _pr(FORK_URL, f"{prefix}2026-09-08", "2026-09-08T09:00:00Z", True),
        ])
        assert picked == "", f"{loop}: selected the fork PR {picked!r}"

    @pytest.mark.parametrize("loop", LOOP_IDS)
    def test_the_newest_same_repo_prefix_pr_still_wins(self, loop):
        prefix = LOOPS[loop][1]
        newer = OWN_URL
        older = "https://github.com/joemccann/radon/pull/300"
        picked = _select(loop, [
            _pr(older, f"{prefix}2026-09-06", "2026-09-06T01:00:00Z", False),
            _pr(newer, f"{prefix}2026-09-07", "2026-09-07T01:00:00Z", False),
            _pr("https://github.com/joemccann/radon/pull/299", "other/branch",
                "2026-09-08T01:00:00Z", False),
        ])
        assert picked == newer, f"{loop}: picked {picked!r}"


class TestStaticPins:
    @pytest.mark.parametrize("loop", LOOP_IDS)
    def test_resolve_pr_url_requests_and_filters_on_cross_repository(self, loop):
        src = _wrapper_text(loop)
        start = src.index("resolve_pr_url() {")
        fn = src[start:src.index("\n}", start)]
        assert "isCrossRepository" in fn, (
            f"{loop}: resolve_pr_url does not request isCrossRepository"
        )
        assert ".isCrossRepository == false" in fn, (
            f"{loop}: resolve_pr_url does not filter out cross-repository PRs"
        )
