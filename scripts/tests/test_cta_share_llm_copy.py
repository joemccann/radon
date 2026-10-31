"""Optional LLM-written share copy, with a hard numeric firewall.

A generator that can write prose can also write a number, and the 2026-08-07
incident published a fabricated "$90.6B forced selling pipeline" from a
hardcoded literal. Handing the copy to a model reopens exactly that failure
mode, so the model output is treated as untrusted: every figure it emits must
trace back to the fact sheet it was given, and copy that fails the check is
discarded in favour of the deterministic template.

The path is OFF unless the operator opts in, and must degrade to the template
on a missing key, a timeout, an API error, or a missing SDK. A share is never
blocked by the LLM layer.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import generate_cta_share as gcs  # noqa: E402
from utils import cta_llm  # noqa: E402

FACTS = {
    "as_of_date": "2026-08-07",
    "spx": {
        "position_today": 3.69,
        "position_1m_ago": 1.75,
        "z_score_3m": 3.68,
        "percentile_3m": 100,
        "side": "long",
    },
    "est_forced_selling_bn": None,
    "lookback_window_months": 3,
}
TEMPLATE = (
    "CTA positioning update: SPX at +3.69, z-score 3.68.\n\n"
    "SPX sits at the 100th percentile of its 3-month range (was +1.75 a month ago).\n\n"
    "Analyzed by Radon · radon.run"
)
CLEAN_COPY = (
    "CTAs are carrying maximum length: SPX at +3.69, z 3.68, the 100th percentile\n"
    "of its 3-month range against +1.75 a month ago.\n\n"
    "Analyzed by Radon · radon.run"
)
ON = {"RADON_CTA_LLM_COPY": "1", "CLAUDE_CODE_OAUTH_TOKEN": "oauth-test"}


def caller_returning(text: str):
    def _caller(**_kwargs):
        if "Check CTA copy against the source facts" in _kwargs.get("system", ""):
            return json.dumps({"supported": True})
        if "HUMANIZER FINAL EDITORIAL REVIEW" in _kwargs.get("user", ""):
            return json.dumps({"findings": [], "qualitative": []})
        return text

    return _caller


def caller_raising(exc: Exception):
    def _caller(**_kwargs):
        raise exc

    return _caller


class TestOptIn:
    def test_off_by_default(self):
        assert cta_llm.llm_copy_enabled({}) is False

    def test_flag_off_returns_the_template_without_calling_the_model(self):
        def _never(**_kwargs):
            raise AssertionError("the model must not be called when the flag is off")

        assert (
            cta_llm.generate_share_copy(FACTS, TEMPLATE, env={}, caller=_never) == TEMPLATE
        )

    def test_flag_on_publishes_verified_model_copy(self):
        out = cta_llm.generate_share_copy(
            FACTS, TEMPLATE, env=ON, caller=caller_returning(CLEAN_COPY)
        )

        assert out == CLEAN_COPY


class TestHumanizerGate:
    def test_initial_copy_cannot_reverse_direction_with_supported_numbers(self):
        bad = 'CTAs are maximum short at +3.69. Analyzed by Radon · radon.run'
        calls = []

        def caller(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return bad
            if 'HUMANIZER FINAL EDITORIAL REVIEW' in kwargs['user']:
                return json.dumps({'findings': [], 'qualitative': []})
            return json.dumps({'supported': False})

        assert cta_llm.generate_share_copy(FACTS, TEMPLATE, env=ON, caller=caller) == TEMPLATE
        assert len(calls) == 2
        assert 'source facts' in calls[-1]['system']

    def test_clean_copy_gets_final_scored_review_before_return(self):
        calls = []

        def caller(**kwargs):
            calls.append(kwargs)
            return caller_returning(CLEAN_COPY)(**kwargs)

        assert cta_llm.generate_share_copy(FACTS, TEMPLATE, env=ON, caller=caller) == CLEAN_COPY
        assert len(calls) == 3
        assert "Check CTA copy against the source facts" in calls[1]["system"]
        assert "HUMANIZER FINAL EDITORIAL REVIEW" in calls[-1]["user"]
        assert "negative parallelism" in calls[0]["system"].lower()

    def test_malformed_review_cannot_publish_generated_copy(self):
        replies = iter([CLEAN_COPY, json.dumps({"supported": True}), "Looks human. Score: 100."])
        assert cta_llm.generate_share_copy(
            FACTS, TEMPLATE, env=ON, caller=lambda **_: next(replies),
        ) == TEMPLATE

    def test_reviewer_outage_falls_back(self):
        calls = []

        def caller(**kwargs):
            calls.append(kwargs)
            if len(calls) > 2:
                raise TimeoutError("review unavailable")
            return caller_returning(CLEAN_COPY)(**kwargs)

        assert cta_llm.generate_share_copy(FACTS, TEMPLATE, env=ON, caller=caller) == TEMPLATE

    def test_copy_budget_exhaustion_does_not_start_review(self, monkeypatch):
        ticks = iter([0.0, 0.0, cta_llm.REQUEST_TIMEOUT_SECONDS + 1])
        monkeypatch.setattr(cta_llm.time, "monotonic", lambda: next(ticks))
        calls = []

        def caller(**kwargs):
            calls.append(kwargs)
            return CLEAN_COPY

        assert cta_llm.generate_share_copy(FACTS, TEMPLATE, env=ON, caller=caller) == TEMPLATE
        assert len(calls) == 1

    def test_provider_review_uses_remaining_generation_budget(self, monkeypatch):
        ticks = iter([0.0, 0.0, 30.0, 40.0])
        monkeypatch.setattr(cta_llm.time, "monotonic", lambda: next(ticks))
        calls = []

        def caller(**kwargs):
            calls.append(kwargs)
            return caller_returning(CLEAN_COPY)(**kwargs)

        monkeypatch.setattr(cta_llm, "_anthropic_caller", caller)
        assert cta_llm.generate_share_copy(FACTS, TEMPLATE, env=ON) == CLEAN_COPY
        assert [c["timeout"] for c in calls] == [45.0, 15.0, 5.0]

    def test_reviewer_false_clean_does_not_bypass_negative_parallelism(self):
        bad = CLEAN_COPY + "\nThat's a positioning setup, not a confirmed breakout."
        assert cta_llm.generate_share_copy(
            FACTS, TEMPLATE, env=ON, caller=caller_returning(bad),
        ) == TEMPLATE

    def test_rewrite_rechecks_numbers_and_keeps_audit_out_of_copy(self):
        bad = CLEAN_COPY + "\nThat's a positioning setup, not a confirmed breakout."
        replies = iter([
            bad,
            json.dumps({"supported": True}),
            json.dumps({"findings": [], "qualitative": [{
                "field": "content", "text": "That's a positioning setup, not a confirmed breakout.",
                "category": "negative_parallelism",
            }]}),
            json.dumps({"title": "CTA positioning", "content": CLEAN_COPY + " $90.6B selling."}),
        ])
        assert cta_llm.generate_share_copy(
            FACTS, TEMPLATE, env=ON, caller=lambda **_: next(replies),
        ) == TEMPLATE

    def test_clean_rewrite_is_rescored_and_audited(self, capsys):
        bad = CLEAN_COPY + "\nThat's a positioning setup, not a confirmed breakout."
        replies = iter([
            bad,
            json.dumps({"supported": True}),
            json.dumps({"findings": [], "qualitative": [{
                "field": "content", "text": "That's a positioning setup, not a confirmed breakout.",
                "category": "negative_parallelism",
            }]}),
            json.dumps({"title": "CTA positioning", "content": CLEAN_COPY}),
            json.dumps({"supported": True}),
            json.dumps({"findings": [], "qualitative": []}),
        ])
        assert cta_llm.generate_share_copy(
            FACTS, TEMPLATE, env=ON, caller=lambda **_: next(replies),
        ) == CLEAN_COPY
        audit = capsys.readouterr().err
        assert '"status": "passed"' in audit
        assert '"score": 100' in audit
        assert "negative_parallelism" in audit

    def test_rewrite_cannot_reverse_direction_with_supported_numbers(self):
        bad = CLEAN_COPY + "\nThat's a positioning setup, not a confirmed breakout."
        replies = iter([
            bad,
            json.dumps({"supported": True}),
            json.dumps({"findings": [], "qualitative": [{
                "field": "content", "text": "That's a positioning setup, not a confirmed breakout.",
                "category": "negative_parallelism",
            }]}),
            json.dumps({"title": "CTA positioning", "content": "CTAs are maximum short at +3.69."}),
            json.dumps({"supported": False}),
        ])
        assert cta_llm.generate_share_copy(
            FACTS, TEMPLATE, env=ON, caller=lambda **_: next(replies),
        ) == TEMPLATE


class TestNumericFirewall:
    def test_fabricated_dollar_figure_is_rejected(self, capsys):
        smuggled = (
            "CTAs are at maximum length: SPX +3.69, z 3.68, 100th percentile.\n"
            "That is a $90.6B forced selling pipeline if vol expands.\n\n"
            "Analyzed by Radon · radon.run"
        )

        out = cta_llm.generate_share_copy(
            FACTS, TEMPLATE, env=ON, caller=caller_returning(smuggled)
        )

        assert out == TEMPLATE
        assert "90.6" not in out
        assert "rejected" in capsys.readouterr().err

    def test_unsupported_numbers_names_the_offender(self):
        allowed = cta_llm.collect_numbers(FACTS, TEMPLATE)

        assert cta_llm.unsupported_numbers("a $90.6B pipeline", allowed) == ["90.6"]

    def test_rounding_variance_is_accepted(self):
        allowed = cta_llm.collect_numbers(FACTS, TEMPLATE)

        # 3.7 is 3.68 rendered to one decimal, not a new figure.
        assert cta_llm.unsupported_numbers("a 3.7 standard deviation long", allowed) == []

    def test_thousands_separators_are_understood(self):
        allowed = cta_llm.collect_numbers({"contracts": 12500})

        assert cta_llm.unsupported_numbers("12,500 contracts", allowed) == []

    def test_collect_numbers_walks_nested_facts_and_strings(self):
        numbers = cta_llm.collect_numbers(FACTS)

        assert 3.69 in numbers
        assert 100 in numbers
        assert 2026 in numbers  # from the as_of_date string

    def test_em_dash_copy_is_rejected(self):
        out = cta_llm.generate_share_copy(
            FACTS,
            TEMPLATE,
            env=ON,
            caller=caller_returning("SPX at +3.69 — maximum length.\n\nAnalyzed by Radon · radon.run"),
        )

        assert out == TEMPLATE

    def test_empty_copy_is_rejected(self):
        assert (
            cta_llm.generate_share_copy(FACTS, TEMPLATE, env=ON, caller=caller_returning("   "))
            == TEMPLATE
        )

    def test_overlong_copy_is_rejected(self):
        bloated = "SPX at +3.69. " * 200
        assert (
            cta_llm.generate_share_copy(FACTS, TEMPLATE, env=ON, caller=caller_returning(bloated))
            == TEMPLATE
        )


class TestGracefulDegradation:
    @pytest.mark.parametrize(
        "exc",
        [TimeoutError("timed out"), RuntimeError("api error"), ImportError("no anthropic")],
    )
    def test_any_failure_falls_back_to_the_template(self, exc):
        assert (
            cta_llm.generate_share_copy(FACTS, TEMPLATE, env=ON, caller=caller_raising(exc))
            == TEMPLATE
        )

    def test_missing_api_key_never_reaches_the_network(self):
        out = cta_llm.generate_share_copy(
            FACTS, TEMPLATE, env={"RADON_CTA_LLM_COPY": "1"}
        )

        assert out == TEMPLATE

    def test_injected_env_never_falls_back_to_the_project_env_files(self, monkeypatch):
        """An explicit mapping is authoritative; only os.environ reads .env."""
        monkeypatch.setattr(cta_llm, "_project_env", lambda: {"RADON_CTA_LLM_MODEL": "live-value"})

        assert cta_llm.setting({}, "RADON_CTA_LLM_MODEL") == ""
        assert cta_llm.setting({"RADON_CTA_LLM_MODEL": "given"}, "RADON_CTA_LLM_MODEL") == "given"

    def test_model_id_is_configurable_and_defaults_to_current_opus(self):
        assert cta_llm.model_name({}) == "claude-opus-5"
        assert cta_llm.model_name({"RADON_CTA_LLM_MODEL": "claude-sonnet-5"}) == "claude-sonnet-5"


class TestShareCopyWiring:
    PAYLOAD = {
        "date": "2026-08-07",
        "tables": {
            "main": [
                {
                    "underlying": "E-Mini S&P 500 Index",
                    "position_today": 3.69,
                    "position_1m_ago": 1.75,
                    "z_score_3m": 3.68,
                    "percentile_3m": 1.0,
                }
            ],
            "index": [],
            "commodity": [],
            "currency": [],
        },
    }

    def test_default_share_copy_is_the_deterministic_template(self):
        assert gcs.compose_share_copy(self.PAYLOAD, "2026-08-07", env={}) == gcs.build_tweet(
            self.PAYLOAD, "2026-08-07"
        )

    def test_facts_carry_the_figures_the_copy_may_use(self):
        facts = gcs.build_llm_facts(self.PAYLOAD, "2026-08-07")

        assert facts["spx"]["position_today"] == 3.69
        assert facts["spx"]["percentile_3m"] == 100
        assert facts["as_of_date"] == "2026-08-07"

    def test_facts_never_carry_an_unsupplied_forced_selling_figure(self):
        facts = gcs.build_llm_facts(self.PAYLOAD, "2026-08-07")

        assert facts["est_forced_selling_bn"] is None
        assert "90.6" not in json.dumps(facts)

    def test_opted_in_copy_is_still_verified_against_the_payload(self):
        smuggled = "SPX at +3.69 with a $90.6B pipeline.\n\nAnalyzed by Radon · radon.run"

        out = gcs.compose_share_copy(
            self.PAYLOAD,
            "2026-08-07",
            env=ON,
            caller=caller_returning(smuggled),
        )

        assert "90.6" not in out
        assert out == gcs.build_tweet(self.PAYLOAD, "2026-08-07")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestCtaCallerUsesCredentialOwningGateway:
    def test_candidate_env_credentials_never_cross_the_wire(self, monkeypatch):
        seen = []
        monkeypatch.setattr(cta_llm, "gateway_request", lambda operation, payload, **kw: seen.append((operation, payload)) or {"text": "SPX copy"})
        assert cta_llm._anthropic_caller(system="You write posts.", user="draft", model="claude-opus-5", env={"CLAUDE_CODE_OAUTH_TOKEN": "secret"}) == "SPX copy"
        assert seen[0][0] == "chat"
        assert seen[0][1]["system"] == "You write posts."
        assert "secret" not in repr(seen)

    def test_gateway_failure_never_uses_local_prepaid_or_transport(self, monkeypatch):
        from model_gateway.client import GatewayError
        def unavailable(*args, **kwargs):
            raise GatewayError("unavailable")
        monkeypatch.setattr(cta_llm, "gateway_request", unavailable)
        with pytest.raises(GatewayError):
            cta_llm._anthropic_caller(system="s", user="u", model="m",
                env={"ANTHROPIC_API_KEY": "prepaid"},
                post=lambda *a, **k: pytest.fail("must not call a provider from the consumer"))
