"""A SHARED session cap is not a per-model quota, and must not read as FAILED.

2026-09-05: every loop that fired at midnight got one line from `claude -p`:

    You've hit your session limit · resets 5am (America/Los_Angeles)

`is_quota_exhausted` deliberately excludes that string — the cap is shared
across models, so walking the ladder cannot route around it. But nothing else
claimed it either, so the round exited 1 and fell to the wrapper's generic
`*)` arm: a bare "FAILED (exit 1)" dead-man with no cause, on the public
rolling issue (#83, #204), for a night where nothing was wrong except a clock.

Asserted for every loop: the shared cap is recognised, it does NOT walk the
ladder, the phase is INCOMPLETE-and-resumable (75), and the dead-man names the
cap so the operator does not re-fire into it.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_LADDER_TEST = Path(__file__).with_name("test_weekend_model_ladder.py")
_spec = importlib.util.spec_from_file_location("_weekend_model_ladder", _LADDER_TEST)
_ladder_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _ladder_mod
_spec.loader.exec_module(_ladder_mod)

LADDER = _ladder_mod.LADDER
LOOPS = _ladder_mod.LOOPS
_run = _ladder_mod._run

SESSION_LIMIT_LINE = "You've hit your session limit · resets 5am (America/Los_Angeles)"
WEEKLY_LIMIT_LINE = "You've hit your weekly limit · resets Monday"


@pytest.mark.parametrize("loop", sorted(LOOPS))
class TestASharedSessionCapIsIncompleteNotFailed:
    def test_it_does_not_walk_the_model_ladder(self, tmp_path, loop):
        _proc, models, _calls = _run(
            tmp_path, loop, "audit", LADDER, exhausted_line=SESSION_LIMIT_LINE
        )
        assert models == LADDER[:1], (
            f"{loop}: the session cap is shared across models, so dropping a "
            f"rung burns the ladder for nothing; models attempted: {models!r}"
        )

    def test_the_phase_exits_incomplete_and_resumable(self, tmp_path, loop):
        proc, _models, _calls = _run(
            tmp_path, loop, "audit", LADDER, exhausted_line=SESSION_LIMIT_LINE
        )
        assert proc.returncode == 75, (proc.returncode, proc.stdout, proc.stderr)

    def test_the_deadman_names_the_cap(self, tmp_path, loop):
        _proc, _models, calls = _run(
            tmp_path, loop, "audit", LADDER, exhausted_line=SESSION_LIMIT_LINE
        )
        assert "session limit" in calls.lower(), (
            f"{loop}: the dead-man must name the shared cap, or the operator "
            f"reads a bare FAILED and re-fires into it: {calls!r}"
        )
        assert "INCOMPLETE" in calls, (
            f"{loop}: a cap that resets on its own clock is INCOMPLETE, not "
            f"FAILED: {calls!r}"
        )

    def test_a_weekly_cap_is_handled_the_same_way(self, tmp_path, loop):
        proc, models, calls = _run(
            tmp_path, loop, "audit", LADDER, exhausted_line=WEEKLY_LIMIT_LINE
        )
        assert models == LADDER[:1], (models, proc.stdout, proc.stderr)
        assert proc.returncode == 75, (proc.returncode, proc.stdout, proc.stderr)
        assert "INCOMPLETE" in calls, calls


# R-667 / R-669 / R-670 (REL-248): the detector must match the CLI's verdict
# line, not prose that merely QUOTES a cap phrase. These loops audit their own
# wrappers and routinely echo the trigger strings (this file contains them
# verbatim), so a crashing round that quotes one was classified as a
# subscription cap: rc 75, ladder never walked, and a dead-man naming a cap
# that never resets.
TRACEBACK_QUOTING_CAP = (
    "Traceback (most recent call last):\n"
    "  File 'test_loop_session_limit.py', line 36, in test_the_deadman_names_the_cap\n"
    "AssertionError: expected 'usage limit reached' and 'session limit reached' in calls"
)

_ladder_QUOTA_LINE = _ladder_mod.QUOTA_LINE


@pytest.mark.parametrize("loop", sorted(LOOPS))
class TestQuotedCapProseIsNotACap:
    def test_a_crash_quoting_the_cap_still_walks_the_ladder(self, tmp_path, loop):
        # The round's transcript QUOTES the cap phrase mid-output, but its
        # final line is a genuine per-model quota refusal: the ladder must
        # walk to the next rung instead of stopping on a phantom shared cap.
        proc, models, calls = _run(
            tmp_path, loop, "audit", LADDER[:1],
            exhausted_line=TRACEBACK_QUOTING_CAP + "\n" + _ladder_QUOTA_LINE,
        )
        assert models == LADDER[:2], (models, proc.stdout, proc.stderr)
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
        assert "subscription session limit" not in calls, calls

    def test_a_crash_quoting_the_cap_is_failed_not_incomplete_cap(self, tmp_path, loop):
        proc, models, calls = _run(
            tmp_path, loop, "audit", LADDER, exhausted_line=TRACEBACK_QUOTING_CAP
        )
        assert proc.returncode != 75, (proc.returncode, proc.stdout, proc.stderr)
        assert "subscription session limit" not in calls, (
            f"{loop}: a Traceback quoting 'usage limit reached' was classified "
            f"as the shared subscription cap: {calls!r}"
        )

    def test_an_exit_zero_cap_print_still_names_the_cause(self, tmp_path, loop):
        # R-669: a `claude -p` that prints the cap line as its verdict but
        # exits 0 must still be classified — the cause, not a generic
        # INCOMPLETE, reaches the dead-man.
        proc, _models, calls = _run(
            tmp_path, loop, "audit", LADDER[:1],
            exhausted_line=SESSION_LIMIT_LINE, exhausted_exit=0,
        )
        assert proc.returncode == 75, (proc.returncode, proc.stdout, proc.stderr)
        assert "session limit" in calls.lower(), calls

    def test_the_deadman_does_not_promise_a_resume_no_phase_records(self, tmp_path, loop):
        # R-670: only the deliver phase records a resume point; the audit and
        # remediate phases are simply re-run by the next scheduled fire.
        _proc, _models, calls = _run(
            tmp_path, loop, "audit", LADDER, exhausted_line=SESSION_LIMIT_LINE
        )
        assert "the next fire resumes it once the cap resets" not in calls, calls
        assert "INCOMPLETE" in calls, calls


class TestTheDetectorIsByteIdenticalAcrossLoops:
    def test_is_session_limited_parity(self):
        import re

        bodies = {}
        for name, wrapper in LOOPS.items():
            text = wrapper.read_text(encoding="utf-8")
            m = re.search(r"^is_session_limited\(\) \{\n(?:.*\n)*?^\}$", text, re.M)
            assert m, f"{name}: is_session_limited not found"
            bodies[name] = m.group(0)
        assert len(set(bodies.values())) == 1, (
            "the session-cap detector must stay byte-identical across the five "
            f"wrappers: {sorted(bodies)}"
        )
