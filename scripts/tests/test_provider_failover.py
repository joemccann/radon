"""A provider's cap must not end the night — some other provider finishes it.

2026-09-05 and again on 09-06: every loop fired at midnight, Claude answered
with a shared session cap, and each phase stopped at INCOMPLETE 75 having
audited nothing. A model ladder cannot help there — the cap is on the account,
not the model. On 09-06 codex was capped at the same time, which is the whole
argument for a ladder that crosses providers rather than models.

The loops that walked a cross-provider ladder (reliability, testing) moved to
scripts/runner/run_loop.sh; the wrappers left are security and DeepSec, which
stay claude-exclusive and are asserted so here.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

_H = Path(__file__).with_name("_loop_harness.py")
_spec = importlib.util.spec_from_file_location("_loop_harness_pf", _H)
_h = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _h
_spec.loader.exec_module(_h)

CLAUDE_LADDER = _h.CLAUDE_LADDER
_run_multi = _h._run_multi
CLAUDE_SESSION_CAP_LINE = _h.CLAUDE_SESSION_CAP_LINE


def providers(tried):
    return [t.split(":", 1)[0] for t in tried]


class TestTheOperatorsDecisionNoPinnedModels:
    """"Do not pin a particular model. New models are released all the time.
    Select the most recent model dynamically and use medium reasoning." """

    @pytest.mark.parametrize("loop", ["security", "security-deepsec"])
    def test_the_security_ladder_uses_the_shared_skip_newest_helper(self, loop):
        body = _h.LOOPS[loop].read_text(encoding="utf-8")
        assert ". \"$REPO/scripts/security_claude_ladder.sh\"" in body, (
            f"{loop}: DeepSec and security must share security_claude_ladder.sh"
        )
        assert not re.search(
            r'^MODEL_LADDER="\$\{RADON_WEEKEND_MODEL_LADDER:-claude-',
            body,
            re.M,
        ), f"{loop}: a static opus pin is the policy Joe rejected"


class TestTheSecurityLoopIsClaudeExclusive:
    def test_its_default_ladder_is_claude_only(self, tmp_path):
        proc, tried, _calls, _argv = _run_multi(tmp_path, "security", "audit")
        assert tried[:1] == [CLAUDE_LADDER[0]], (tried, proc.stdout, proc.stderr)

    def test_a_claude_session_cap_walks_no_further(self, tmp_path):
        """No fallback for the one loop whose output is sanitized."""
        proc, tried, calls, _argv = _run_multi(
            tmp_path, "security", "audit",
            capped_providers=("claude",),
            cap_line=CLAUDE_SESSION_CAP_LINE,
        )
        assert providers(tried) == ["claude"], tried
        assert proc.returncode == 75, (proc.returncode, proc.stdout, proc.stderr)
        assert "all agent providers exhausted" in calls, calls

    def test_a_per_model_quota_still_walks_the_claude_ladder(self, tmp_path):
        proc, tried, _calls, _argv = _run_multi(
            tmp_path, "security", "audit",
            provider_ladder=" ".join(CLAUDE_LADDER),
            capped_providers=(),
        )
        assert providers(tried) == ["claude"], tried
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)

    def test_it_refuses_an_operator_ladder_naming_another_provider(self, tmp_path):
        proc, tried, _calls, _argv = _run_multi(
            tmp_path, "security", "audit",
            provider_ladder="codex:gpt-5.4 claude:claude-opus-5",
        )
        assert tried == [], (
            f"the security loop launched a non-claude provider: {tried}"
        )
        assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
        assert "claude-exclusive" in proc.stderr, proc.stderr
