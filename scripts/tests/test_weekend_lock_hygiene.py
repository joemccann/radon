"""Runner-lock hygiene.

The wrapper lock cases (L1-L5, plan cases 1-13: file- and directory-shaped
locks, dead and live pids, reused pids, races, the shared-parent sweep) drove
scripts/security_nightly.sh and scripts/security_deepsec_nightly.sh, which the
runner cutover retired. scripts/runner/run_loop.sh owns the only lock now; its
live, dead and reclaimed cases run in test_runner_run_loop.py. What stays here
is the agent-facing half: no prompt tells an agent to take, probe or reclaim a
lock.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL_ROOTS = (
    REPO / ".claude" / "skills",
    REPO / ".claude" / "runner-prompts",
)


class TestSkillsNeverTouchTheLock:
    def test_no_skill_tells_an_agent_to_reclaim_or_kill0(self):
        instructional = re.compile(
            r"(?i)(?<!never )(?<!not )(?<!don't )(?<!do not )"
            r"(take an exclusive(?: loop| security-loop)? lock|"
            r"kill -0|reclaim(?:ing)? (?:the |a |stale )?(?:runner )?lock|"
            r"create (?:a |the |an )?(?:exclusive )?loop lock)"
        )
        hits = []
        for root in SKILL_ROOTS:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                text = path.read_text(encoding="utf-8")
                for i, line in enumerate(text.splitlines(), 1):
                    if instructional.search(line) and not re.search(
                        r"(?i)never|do not|don't|must not|forbids",
                        line,
                    ):
                        hits.append(f"{path}:{i}:{line.strip()}")
        assert hits == []
