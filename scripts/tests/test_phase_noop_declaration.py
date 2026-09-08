"""A phase with genuinely nothing to commit must be OK, not INCOMPLETE.

`phase_committed()` (REL-188 / T-379) exists because `claude -p` exits 0 when
the agent answers a nudge with prose and no tool call: no commit, no ledger
advance, and every dead-man channel said OK. The check is right, but it reads
"HEAD did not move" as the only possible cause, and it is not. On 2026-09-08
testing/audit found no new findings in its delta range and documentation/
remediate found 0 source-actionable P0/P1 items. Both ran to completion on the
codex rung, printed their full report, and had nothing to commit — and both
were scored

    INCOMPLETE (agent exited 0 without committing to the nightly branch)

exit 75, on their rolling issues, having done exactly what the contract asked.
An unfinished phase and a finished no-op are indistinguishable from HEAD alone,
so the agent declares the difference: one literal line, checked with the same
scoping discipline as the TRUNCATED detector (R-426) and the cap detectors
(R-530, R-667) — this round's log slice only, wrapper markers excluded, so a
loop that AUDITS this contract and quotes the line in its own report does not
thereby claim it.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_H = Path(__file__).with_name("_loop_harness.py")
_spec = importlib.util.spec_from_file_location("_loop_harness_noop", _H)
_h = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _h
_spec.loader.exec_module(_h)

# security scores on its own completion marker, not on a commit, so it has no
# phase_committed() to soften.
FALLBACK = ["reliability", "testing", "ci-performance", "documentation"]
NOOP_MARKER = "NIGHTLY PHASE NO-OP:"
INCOMPLETE = "without committing to the nightly branch"


def _noop_line(loop: str, phase: str) -> str:
    return f"{NOOP_MARKER} loop={loop} phase={phase} nothing to commit"


@pytest.mark.parametrize("loop", FALLBACK)
@pytest.mark.parametrize("phase", ["audit", "remediate"])
def test_a_declared_noop_is_ok_not_incomplete(tmp_path, loop, phase):
    """The 2026-09-08 shape: real work, real report, no commit, declared."""
    report = (
        "## Findings\n"
        "- None in this delta range.\n\n"
        f"{_noop_line(loop, phase)}\n"
    )
    proc, tried, calls, _ = _h._run_multi(
        tmp_path, loop, phase, committed=False, agent_output=report,
    )
    assert proc.returncode == 0, (
        f"{loop}/{phase} declared a no-op and still exited "
        f"{proc.returncode}\n{proc.stdout[-2000:]}"
    )
    assert INCOMPLETE not in calls, (
        f"{loop}/{phase} reported INCOMPLETE for a declared no-op:\n{calls[:2000]}"
    )
    assert tried, "no provider was launched"


@pytest.mark.parametrize("loop", FALLBACK)
def test_an_undeclared_missing_commit_is_still_incomplete(tmp_path, loop):
    """T-379's actual failure mode must keep failing: silence is not a no-op."""
    proc, _, calls, _ = _h._run_multi(
        tmp_path, loop, "audit", committed=False,
        agent_output="I have finished thinking about the audit.",
    )
    assert proc.returncode == 75, (
        f"{loop}/audit exited 0 with no commit and no declaration"
    )
    assert INCOMPLETE in calls


@pytest.mark.parametrize("loop", FALLBACK)
def test_the_declaration_is_scoped_to_this_round_not_quoted_prose(tmp_path, loop):
    """These loops audit their own wrappers and quote this contract verbatim.

    A report that MENTIONS the line inside a code fence, or a wrapper marker
    that echoes it, must not satisfy the declaration — only the agent printing
    it as its own line does. Same discipline as R-426 / R-530 / R-667.
    """
    quoting = (
        "The wrapper accepts a declared no-op. From docs/operations.md:\n\n"
        "```\n"
        f"    {_noop_line(loop, 'audit')}\n"
        "```\n\n"
        "That is the line a phase prints when it has nothing to commit.\n"
    )
    proc, _, calls, _ = _h._run_multi(
        tmp_path, loop, "audit", committed=False, agent_output=quoting,
    )
    assert proc.returncode == 75, (
        f"{loop}/audit accepted an INDENTED, quoted mention as its own declaration"
    )
    assert INCOMPLETE in calls


@pytest.mark.parametrize("loop", FALLBACK)
def test_the_declaration_must_name_this_loop_and_phase(tmp_path, loop):
    """A stale line copied from a sibling loop's log is not this phase's."""
    other = "reliability" if loop != "reliability" else "testing"
    proc, _, calls, _ = _h._run_multi(
        tmp_path, loop, "audit", committed=False,
        agent_output=_noop_line(other, "deliver"),
    )
    assert proc.returncode == 75, (
        f"{loop}/audit accepted a no-op declared for {other}/deliver"
    )
    assert INCOMPLETE in calls


@pytest.mark.parametrize("loop", FALLBACK)
def test_a_commit_still_wins_without_any_declaration(tmp_path, loop):
    """The ordinary path is untouched: commit evidence alone is still OK."""
    proc, _, calls, _ = _h._run_multi(tmp_path, loop, "audit", committed=True)
    assert proc.returncode == 0, proc.stdout[-2000:]
    assert INCOMPLETE not in calls
