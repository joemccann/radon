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
CLAUDE_LADDER = _h.CLAUDE_LADDER
_run_multi = _h._run_multi
CODEX_CAP_LINE = _h.CODEX_CAP_LINE
CLAUDE_SESSION_CAP_LINE = _h.CLAUDE_SESSION_CAP_LINE
REJECTION_400_LINE = _h.REJECTION_400_LINE
REJECTION_QUOTED_OUTPUT = _h.REJECTION_QUOTED_OUTPUT


def providers(tried):
    return [t.split(":", 1)[0] for t in tried]


@pytest.mark.parametrize("loop", FALLBACK_LOOPS)
class TestACapContinuesOnTheNextProvider:
    def test_the_default_ladder_leads_with_codex(self, tmp_path, loop):
        proc, tried, _calls, _argv = _run_multi(tmp_path, loop, "audit")
        assert providers(tried)[:1] == ["codex"], (tried, proc.stdout, proc.stderr)

    def test_no_claude_rung_is_ever_launched(self, tmp_path, loop):
        """The claude.ai subscription belongs to the security loop."""
        _proc, tried, _calls, _argv = _run_multi(
            tmp_path, loop, "audit", capped_providers=("codex", "grok", "nvidia", "cerebras")
        )
        assert "claude" not in providers(tried), tried

    def test_a_codex_cap_continues_on_grok(self, tmp_path, loop):
        proc, tried, _calls, _argv = _run_multi(
            tmp_path, loop, "audit", capped_providers=("codex",)
        )
        assert providers(tried)[:2] == ["codex", "grok"], (tried, proc.stderr)
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)

    def test_it_walks_the_whole_ladder_in_order(self, tmp_path, loop):
        proc, tried, _calls, _argv = _run_multi(
            tmp_path, loop, "audit", capped_providers=("codex", "grok", "nvidia")
        )
        assert providers(tried) == ["codex", "grok", "nvidia", "cerebras"], tried
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)

    def test_every_provider_capped_is_one_honest_incomplete(self, tmp_path, loop):
        proc, tried, calls, _argv = _run_multi(
            tmp_path, loop, "audit",
            capped_providers=("codex", "grok", "nvidia", "cerebras"),
        )
        assert providers(tried) == ["codex", "grok", "nvidia", "cerebras"], tried
        assert proc.returncode == 75, (proc.returncode, proc.stdout, proc.stderr)
        assert "all agent providers exhausted" in calls, calls
        assert "INCOMPLETE" in calls, calls

    def test_an_uninstalled_provider_is_skipped_not_crashed(self, tmp_path, loop):
        """A missing binary must cost one rung, never the night."""
        proc, tried, _calls, _argv = _run_multi(
            tmp_path, loop, "audit", capped_providers=("codex",), installed=("codex",)
        )
        assert providers(tried) == ["codex"], tried
        assert proc.returncode == 75, (proc.returncode, proc.stdout, proc.stderr)

    def test_an_unauthenticated_provider_is_skipped(self, tmp_path, loop):
        proc, tried, _calls, _argv = _run_multi(
            tmp_path, loop, "audit",
            capped_providers=("codex", "grok"),
            authed=("codex", "grok", "cerebras"),
        )
        assert providers(tried) == ["codex", "grok", "cerebras"], tried
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
        assert providers(tried)[2:] == ["nvidia", "cerebras"], tried

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
        proc, tried, _calls, _argv = _run_multi(
            tmp_path, loop, "audit", reject_providers=("codex",)
        )
        assert providers(tried)[:2] == ["codex", "grok"], (tried, proc.stderr)
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
        _proc, tried, _calls, _argv = _run_multi(
            tmp_path, loop, "audit",
            reject_providers=("codex",),
            reject_output=REJECTION_QUOTED_OUTPUT,
        )
        assert providers(tried) == ["codex"], (
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
        assert [r.split(":")[0] for r in rungs] == [
            "codex", "grok", "nvidia", "cerebras"
        ], rungs
        assert rungs[0] == "codex", (
            f"{loop}: the codex rung pins a model again: {rungs[0]}"
        )
        assert rungs[1] == "grok", (
            f"{loop}: the grok rung pins a model again: {rungs[1]}"
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

    def test_the_security_ladder_still_names_its_four_claude_models(self):
        body = _h.LOOPS["security"].read_text(encoding="utf-8")
        m = re.search(
            r'^MODEL_LADDER="\$\{RADON_WEEKEND_MODEL_LADDER:-(.+?)\}"$', body, re.M
        )
        assert m, "the security loop lost its claude model ladder"
        assert m.group(1).split() == _h.LADDER, (
            "the security ladder is a deliberate per-model ladder for a shared "
            f"account cap: {m.group(1)}"
        )

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
        _proc, _tried, _calls, argv = _run_multi(tmp_path, loop, "audit")
        assert argv, "codex was never launched"
        assert "--model" not in argv[0].split(), (
            f"a bare rung must omit --model entirely, not pass an empty one: {argv[0]}"
        )
        assert 'model_reasoning_effort=medium' in argv[0].replace('"', ""), argv[0]

    def test_grok_is_launched_with_no_model_and_medium_effort(self, tmp_path, loop):
        _proc, _tried, _calls, argv = _run_multi(
            tmp_path, loop, "audit", capped_providers=("codex",)
        )
        assert len(argv) >= 2, argv
        assert "--model" not in argv[1].split(), argv[1]
        assert "--reasoning-effort medium" in argv[1], argv[1]

    def test_the_hosted_rungs_still_pass_their_config_key(self, tmp_path, loop):
        _proc, tried, _calls, argv = _run_multi(
            tmp_path, loop, "audit", capped_providers=("codex", "grok")
        )
        assert tried[2:3] == ["nvidia:nvidia-latest"], tried
        assert "--reasoning-effort medium" in argv[2], argv[2]


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
