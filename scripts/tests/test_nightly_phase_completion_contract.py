"""REL-188 (R-520, R-534, R-535, R-537): a phase is OK only on affirmative
evidence, uniformly across the five loops.

A `claude -p` round can exit 0 having done nothing — no commits, no ledger
advance, no PR — and every dead-man channel then said OK. Testing and security
each grew their own guard; the other three had none, security's marker match
could be satisfied by the agent quoting the marker mid-sentence, testing's
INCOMPLETE left rc 0, and a failed `gh issue` call inside report() was
indistinguishable from a dead runner.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
COMMIT_EVIDENCE_LOOPS = (
    SCRIPTS / "reliability_weekend.sh",
    SCRIPTS / "testing_weekend.sh",
    SCRIPTS / "ci_performance_nightly.sh",
    SCRIPTS / "documentation_nightly.sh",
)
ALL_LOOPS = COMMIT_EVIDENCE_LOOPS + (SCRIPTS / "security_nightly.sh",)


def _uncommented(path: Path) -> str:
    return "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


@pytest.mark.parametrize("wrapper", COMMIT_EVIDENCE_LOOPS, ids=lambda p: p.name)
def test_an_exit_zero_phase_with_no_commit_is_incomplete(wrapper: Path):
    body = _uncommented(wrapper)
    assert "phase_committed" in body, wrapper.name
    assert "PHASE_HEAD_BEFORE" in body and "PHASE_START_EPOCH" in body, wrapper.name
    assert "! phase_committed" in body, wrapper.name


@pytest.mark.parametrize("wrapper", ALL_LOOPS, ids=lambda p: p.name)
def test_incomplete_exits_nonzero(wrapper: Path):
    """launchd and the cycle exit code must not read an unfinished phase as
    success. Security already used 75; every loop does now."""
    body = _uncommented(wrapper)
    assert "RC=75" in body, wrapper.name
    idx = body.index("INCOMPLETE")
    assert "RC=75" in body[idx:], wrapper.name


def test_the_security_marker_is_anchored_to_the_last_line():
    """R-535: a `grep -qF` over the whole round slice is satisfied by the
    agent RECITING the marker mid-sentence. The skill prints it as the LAST
    line of a finished phase, so that is what must be matched."""
    body = _uncommented(SCRIPTS / "security_nightly.sh")
    start = body.index("PHASE_COMPLETE_MARKER")
    slice_ = body[start:]
    assert "phase_marker_present" in slice_, "the marker check is not factored out"
    fn_start = body.index("phase_marker_present() {")
    fn = body[fn_start:body.index("\n}", fn_start)]
    # The last non-empty line of the slice, not any line in it.
    assert "grep -v '^[[:space:]]*$'" in fn or "sed -n" in fn, fn
    assert "tail -n 1" in fn, fn


@pytest.mark.parametrize("wrapper", ALL_LOOPS, ids=lambda p: p.name)
def test_a_failed_gh_issue_call_is_logged(wrapper: Path):
    """R-537: `gh issue ... || true` swallowed an auth expiry, so a gh that
    can no longer post looks exactly like a runner that never fired."""
    body = _uncommented(wrapper)
    start = body.index("\nreport() {")
    end = body.index("\n}", start)
    report = body[start:end]
    assert "gh issue call failed" in report, wrapper.name
