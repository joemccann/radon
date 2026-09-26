"""A provider's cap must not end the night — some other provider finishes it.

2026-09-05 and again on 09-06: every loop fired at midnight, Claude answered
with a shared session cap, and each phase stopped at INCOMPLETE 75 having
audited nothing. A model ladder cannot help there — the cap is on the account,
not the model. On 09-06 codex was capped at the same time, which is the whole
argument for a ladder that crosses providers rather than models.

The four non-security loops now run codex, then grok, then NVIDIA, then
Cerebras, and never touch the claude.ai subscription: it is reserved for the
security loop, which stays claude-exclusive and is asserted so here.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

_H = Path(__file__).with_name("_loop_harness.py")
_spec = importlib.util.spec_from_file_location("_loop_harness_pf", _H)
_h = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _h
_spec.loader.exec_module(_h)

FALLBACK_LOOPS = ["ci-performance", "documentation", "reliability", "testing"]
FALLBACK_LADDER = _h.FALLBACK_LADDER
EXPECTED_PROVIDER_ORDER = _h.FALLBACK_PROVIDER_ORDER
CLAUDE_LADDER = _h.CLAUDE_LADDER
_run_multi = _h._run_multi
CODEX_CAP_LINE = _h.CODEX_CAP_LINE
CLAUDE_SESSION_CAP_LINE = _h.CLAUDE_SESSION_CAP_LINE
REJECTION_400_LINE = _h.REJECTION_400_LINE
REJECTION_QUOTED_OUTPUT = _h.REJECTION_QUOTED_OUTPUT
NVIDIA_INTERNAL_ERROR_OUTPUT = _h.NVIDIA_INTERNAL_ERROR_OUTPUT
BROKEN_RUNG_QUOTED_OUTPUT = _h.BROKEN_RUNG_QUOTED_OUTPUT


def providers(tried):
    return [t.split(":", 1)[0] for t in tried]


def ladder_walk(tried):
    """The RUNGS the ladder advanced through, not the launch count.

    A phase with continuation rounds (reliability's audit relaunches an
    INCOMPLETE round inside its cap) re-fires the SAME rung once per round.
    That is not the ladder walking, so collapse consecutive repeats before
    asserting on ladder movement.
    """
    walk = []
    for p in providers(tried):
        if not walk or walk[-1] != p:
            walk.append(p)
    return walk


def order(loop):
    return EXPECTED_PROVIDER_ORDER[loop]


rungs_before = _h.providers_before


def launch_of(tried, argv, provider):
    idx = providers(tried).index(provider)
    return tried[idx], argv[idx]


@pytest.mark.parametrize("loop", FALLBACK_LOOPS)
class TestACapContinuesOnTheNextProvider:
    def test_the_default_ladder_leads_with_its_pinned_first_rung(self, tmp_path, loop):
        proc, tried, _calls, _argv = _run_multi(tmp_path, loop, "audit")
        assert providers(tried)[:1] == order(loop)[:1], (tried, proc.stdout, proc.stderr)

    def test_no_claude_rung_is_ever_launched(self, tmp_path, loop):
        """The claude.ai subscription belongs to the security loop."""
        _proc, tried, _calls, _argv = _run_multi(
            tmp_path, loop, "audit", capped_providers=("codex", "grok", "nvidia", "cerebras")
        )
        assert "claude" not in providers(tried), tried

    def test_a_first_rung_cap_continues_on_the_second(self, tmp_path, loop):
        proc, tried, _calls, _argv = _run_multi(
            tmp_path, loop, "audit", capped_providers=(order(loop)[0],)
        )
        assert providers(tried)[:2] == order(loop)[:2], (tried, proc.stderr)
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)

    def test_it_walks_the_whole_ladder_in_order(self, tmp_path, loop):
        proc, tried, _calls, _argv = _run_multi(
            tmp_path, loop, "audit", capped_providers=tuple(order(loop)[:3])
        )
        assert providers(tried) == order(loop), tried
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)

    def test_every_provider_capped_is_one_honest_incomplete(self, tmp_path, loop):
        proc, tried, calls, _argv = _run_multi(
            tmp_path, loop, "audit",
            capped_providers=("codex", "grok", "nvidia", "cerebras"),
        )
        assert providers(tried) == order(loop), tried
        assert proc.returncode == 75, (proc.returncode, proc.stdout, proc.stderr)
        assert "all agent providers exhausted" in calls, calls
        assert "INCOMPLETE" in calls, calls

    def test_an_uninstalled_provider_is_skipped_not_crashed(self, tmp_path, loop):
        """A missing binary must cost one rung, never the night."""
        proc, tried, _calls, _argv = _run_multi(
            tmp_path, loop, "audit",
            capped_providers=rungs_before(loop, "codex") + ("codex",),
            installed=("codex",),
        )
        assert providers(tried) == ["codex"], tried
        assert proc.returncode == 75, (proc.returncode, proc.stdout, proc.stderr)

    def test_an_unauthenticated_provider_is_skipped(self, tmp_path, loop):
        proc, tried, _calls, _argv = _run_multi(
            tmp_path, loop, "audit",
            capped_providers=tuple(order(loop)[:2]),
            authed=tuple(p for p in order(loop) if p != order(loop)[2]),
        )
        assert providers(tried) == order(loop)[:2] + order(loop)[3:], tried
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)

    def test_an_operator_ladder_overrides_the_default(self, tmp_path, loop):
        _proc, tried, _calls, _argv = _run_multi(
            tmp_path, loop, "audit",
            provider_ladder="grok:grok-4.6 codex:gpt-5.4",
            capped_providers=("grok",),
        )
        assert providers(tried) == ["grok", "codex"], tried

    def test_a_capped_provider_is_not_retried_in_the_same_phase(self, tmp_path, loop):
        _proc, tried, _calls, _argv = _run_multi(
            tmp_path, loop, "audit",
            provider_ladder="codex:gpt-5.4 codex:gpt-5.3 grok:grok-4.6",
            capped_providers=("codex",),
        )
        assert providers(tried) == ["codex", "grok"], (
            "a shared account cap retires the provider; a second codex rung is "
            f"the same wall: {tried}"
        )

    def test_the_grok_hosted_providers_get_their_own_home(self, tmp_path, loop):
        """nvidia and cerebras ride the grok binary; only GROK_HOME tells them
        apart, so a wrong home silently bills the wrong provider."""
        _proc, tried, _calls, _argv = _run_multi(
            tmp_path, loop, "audit", capped_providers=("codex", "grok", "nvidia")
        )
        hosted = [p for p in providers(tried) if p in ("nvidia", "cerebras")]
        assert hosted == ["nvidia", "cerebras"], tried

    def test_a_reduced_rung_is_marked_reduced(self, tmp_path, loop):
        """Fallback rungs run the portable prompt with no subagents, so the
        phase narrows remediation. The flag must reach the agent."""
        wrapper = _h.LOOPS[loop].read_text(encoding="utf-8")
        assert "RADON_WEEKEND_REDUCED" in wrapper


@pytest.mark.parametrize("loop", FALLBACK_LOOPS)
class TestAPermanentRejectionCostsOneRung:
    """2026-09-07: `codex exec --model gpt-5.4` answered HTTP 400
    invalid_request_error. That is neither a cap nor a transient network
    error, so nothing in the wrapper classified it: reliability re-issued the
    same 400 for all eight remediate rounds and three loops died silently."""

    def test_a_rejected_codex_rung_advances_and_the_night_completes(
        self, tmp_path, loop
    ):
        before = rungs_before(loop, "codex")
        proc, tried, _calls, _argv = _run_multi(
            tmp_path, loop, "audit",
            capped_providers=before, reject_providers=("codex",),
        )
        assert providers(tried) == list(before) + ["codex", "grok"], (tried, proc.stderr)
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)

    def test_a_rejection_is_not_a_provider_wide_cap(self, tmp_path, loop):
        """Another model on the same provider may be perfectly fine, so a
        rejection advances ONE rung where a shared account cap retires all."""
        _proc, tried, _calls, _argv = _run_multi(
            tmp_path, loop, "audit",
            provider_ladder="codex:gpt-5.4 codex:gpt-5.5 grok",
            reject_providers=("codex",),
        )
        assert providers(tried) == ["codex", "codex", "grok"], tried

    def test_prose_quoting_a_rejection_is_not_a_rejection(self, tmp_path, loop):
        """These loops audit their own wrappers and echo the trigger text."""
        before = rungs_before(loop, "codex")
        _proc, tried, _calls, _argv = _run_multi(
            tmp_path, loop, "audit",
            capped_providers=before,
            reject_providers=("codex",),
            reject_output=REJECTION_QUOTED_OUTPUT,
        )
        assert ladder_walk(tried) == list(before) + ["codex"], (
            f"a Traceback quoting the 400 walked the ladder: {tried}"
        )

    def test_a_rejection_does_not_consume_a_transient_network_attempt(
        self, tmp_path, loop
    ):
        """Three rungs, all rejecting, is three launches — MAX_ATTEMPTS is 3
        and a rejection must not spend one of them."""
        _proc, tried, _calls, _argv = _run_multi(
            tmp_path, loop, "audit",
            provider_ladder="codex:a codex:b codex:c grok",
            reject_providers=("codex",),
        )
        assert providers(tried) == ["codex", "codex", "codex", "grok"], tried


@pytest.mark.parametrize("loop", FALLBACK_LOOPS)
class TestARungThatCrashesInsideItselfCostsOneRung:
    """2026-09-26: the first night nvidia led documentation's ladder (#728),
    every phase died on `Error: Internal error: {"message": "serialization
    error: ..."}` -- the rung answered, billed tokens, then crashed in its own
    result serializer. Not a cap, not a 400, not a network blip, and its
    verdict is multi-line, so no classifier saw it: audit, remediate and
    deliver each exited 1 with nothing done and the ladder untouched."""

    def test_a_crashing_rung_advances_and_the_night_completes(self, tmp_path, loop):
        before = rungs_before(loop, "nvidia")
        proc, tried, _calls, _argv = _run_multi(
            tmp_path, loop, "audit",
            capped_providers=before,
            reject_providers=("nvidia",),
            reject_output=NVIDIA_INTERNAL_ERROR_OUTPUT,
        )
        # Only the capped rungs before nvidia, nvidia itself, and the first
        # healthy rung after it: that rung answers, so the ladder stops there.
        nxt = order(loop)[len(before) + 1:][:1]
        assert providers(tried) == list(before) + ["nvidia"] + list(nxt), (
            tried, proc.stdout, proc.stderr,
        )
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)

    def test_a_broken_lead_rung_is_survivable(self, tmp_path, loop):
        """The lead rung is the one that has no predecessor to fall back on."""
        _proc, tried, _calls, _argv = _run_multi(
            tmp_path, loop, "audit",
            provider_ladder="nvidia:nvidia-latest codex grok",
            reject_providers=("nvidia",),
            reject_output=NVIDIA_INTERNAL_ERROR_OUTPUT,
        )
        assert providers(tried) == ["nvidia", "codex"], tried

    def test_a_crash_is_not_a_provider_wide_cap(self, tmp_path, loop):
        """Another model behind the same key may serialize fine, so a crash
        costs ONE rung where a shared account cap retires them all."""
        _proc, tried, _calls, _argv = _run_multi(
            tmp_path, loop, "audit",
            provider_ladder="nvidia:a nvidia:b grok",
            reject_providers=("nvidia",),
            reject_output=NVIDIA_INTERNAL_ERROR_OUTPUT,
        )
        assert providers(tried) == ["nvidia", "nvidia", "grok"], tried

    def test_prose_quoting_the_crash_is_not_a_crash(self, tmp_path, loop):
        """These loops audit their own wrappers and echo the trigger text."""
        before = rungs_before(loop, "nvidia")
        _proc, tried, _calls, _argv = _run_multi(
            tmp_path, loop, "audit",
            capped_providers=before,
            reject_providers=("nvidia",),
            reject_output=BROKEN_RUNG_QUOTED_OUTPUT,
        )
        assert ladder_walk(tried) == list(before) + ["nvidia"], (
            f"a Traceback quoting the crash walked the ladder: {tried}"
        )

    def test_a_crash_does_not_consume_a_transient_network_attempt(self, tmp_path, loop):
        _proc, tried, _calls, _argv = _run_multi(
            tmp_path, loop, "audit",
            provider_ladder="nvidia:a nvidia:b nvidia:c grok",
            reject_providers=("nvidia",),
            reject_output=NVIDIA_INTERNAL_ERROR_OUTPUT,
        )
        assert providers(tried) == ["nvidia"] * 3 + ["grok"], tried


class TestAnIncompleteAuditGetsContinuationRounds:
    """2026-09-26: reliability's audit self-declared INCOMPLETE 2 minutes into
    a 2-hour cap (101k tokens, serial review of a 54-commit delta) and the
    phase ended there -- audit ran MAX_ROUNDS=1, so 98% of its own cap and the
    whole 20h cycle budget went unused, and the checkpoint never advanced.
    Remediation already relaunches a continuation round on a non-zero exit;
    audit must too, bounded by the same per-round cap and cycle deadline."""

    def test_an_unclassified_audit_failure_is_relaunched(self, tmp_path):
        proc, tried, _calls, _argv = _run_multi(
            tmp_path, "reliability", "audit",
            provider_ladder="codex",
            capped_providers=("codex",),
            cap_line="NIGHTLY PHASE INCOMPLETE: loop=reliability phase=audit",
            cap_exit=75,
        )
        assert providers(tried) == ["codex"] * 3, (
            "an INCOMPLETE audit must get continuation rounds inside its cap, "
            f"not end the phase on the first one: {tried}\n{proc.stdout}"
        )
        assert proc.returncode == 75, (proc.returncode, proc.stdout, proc.stderr)

    def test_audit_rounds_stay_bounded_by_the_operators_override(self, tmp_path):
        """Three is a default, not a hard-coded number: an operator throttling
        a bad night back to one round must not have to edit the wrapper."""
        proc, tried, _calls, _argv = _run_multi(
            tmp_path, "reliability", "audit",
            provider_ladder="codex",
            capped_providers=("codex",),
            cap_line="NIGHTLY PHASE INCOMPLETE: loop=reliability phase=audit",
            cap_exit=75,
            env_extra={"RADON_WEEKEND_AUDIT_MAX_ROUNDS": "1"},
        )
        assert providers(tried) == ["codex"], (tried, proc.stdout)


class TestTheOperatorsDecisionNoPinnedModels:
    """"Do not pin a particular model. New models are released all the time.
    Select the most recent model dynamically and use medium reasoning." """

    @pytest.mark.parametrize("loop", FALLBACK_LOOPS)
    def test_no_default_ladder_pins_a_codex_or_grok_model(self, loop):
        body = _h.LOOPS[loop].read_text(encoding="utf-8")
        m = re.search(
            r'^PROVIDER_LADDER="\$\{RADON_WEEKEND_PROVIDER_LADDER:-(.+?)\}"$',
            body, re.M,
        )
        assert m, f"{loop}: no default provider ladder"
        rungs = m.group(1).split()
        assert [r.split(":")[0] for r in rungs] == EXPECTED_PROVIDER_ORDER[loop], rungs
        assert "codex" in rungs, (
            f"{loop}: the codex rung pins a model again: {rungs}"
        )
        assert "grok" in rungs, (
            f"{loop}: the grok rung pins a model again: {rungs}"
        )

    @pytest.mark.parametrize("loop", FALLBACK_LOOPS)
    def test_the_hosted_rungs_name_a_config_key_not_a_vendor_model_id(self, loop):
        """nvidia and cerebras MUST name a model — grok resolves it through a
        `[model."<key>"]` block — but the id itself belongs in the bootstrap,
        which reads it from the provider's live /v1/models."""
        body = _h.LOOPS[loop].read_text(encoding="utf-8")
        m = re.search(
            r'^PROVIDER_LADDER="\$\{RADON_WEEKEND_PROVIDER_LADDER:-(.+?)\}"$',
            body, re.M,
        )
        rungs = dict(r.split(":", 1) for r in m.group(1).split() if ":" in r)
        assert rungs["nvidia"] == "nvidia-latest", rungs
        assert rungs["cerebras"] == "cerebras-latest", rungs
        assert "/" not in rungs["nvidia"], (
            f"{loop}: a vendor model id is pinned in the wrapper: {rungs}"
        )

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

    @pytest.mark.parametrize("loop", FALLBACK_LOOPS)
    def test_rung_model_is_empty_for_a_bare_rung(self, tmp_path, loop):
        wrapper = _h.LOOPS[loop]
        out = subprocess.run(
            [_h.BASH, "-c",
             _fn_source(wrapper, "rung_model")
             + '; printf "[%s][%s]" "$(rung_model codex)" "$(rung_model a:b)"'],
            capture_output=True, text=True, timeout=60,
        )
        assert out.stdout == "[][b]", (out.stdout, out.stderr)


def _fn_source(wrapper, name):
    body = wrapper.read_text(encoding="utf-8")
    m = re.search(rf"^{name}\(\) \{{[^\n]*\}}$", body, re.M)
    if not m:
        m = re.search(rf"^{name}\(\) \{{\n(?:.*\n)*?^\}}$", body, re.M)
    assert m, name
    return m.group(0)


@pytest.mark.parametrize("loop", FALLBACK_LOOPS)
class TestMediumReasoningOnEveryFallbackRung:
    def test_codex_is_launched_with_no_model_and_medium_effort(self, tmp_path, loop):
        _proc, tried, _calls, argv = _run_multi(
            tmp_path, loop, "audit", capped_providers=rungs_before(loop, "codex")
        )
        assert "codex" in providers(tried), "codex was never launched"
        _rung, launch = launch_of(tried, argv, "codex")
        assert "--model" not in launch.split(), (
            f"a bare rung must omit --model entirely, not pass an empty one: {launch}"
        )
        assert 'model_reasoning_effort=medium' in launch.replace('"', ""), launch

    def test_grok_is_launched_with_no_model_and_medium_effort(self, tmp_path, loop):
        _proc, tried, _calls, argv = _run_multi(
            tmp_path, loop, "audit", capped_providers=rungs_before(loop, "grok")
        )
        assert "grok" in providers(tried), tried
        _rung, launch = launch_of(tried, argv, "grok")
        assert "--model" not in launch.split(), launch
        assert "--reasoning-effort medium" in launch, launch

    def test_the_hosted_rungs_still_pass_their_config_key(self, tmp_path, loop):
        _proc, tried, _calls, argv = _run_multi(
            tmp_path, loop, "audit", capped_providers=rungs_before(loop, "nvidia")
        )
        rung, launch = launch_of(tried, argv, "nvidia")
        assert rung == "nvidia:nvidia-latest", tried
        assert "--reasoning-effort medium" in launch, launch


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
