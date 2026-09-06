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

import pytest

from _loop_harness import LADDER, LOOPS, _run

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
