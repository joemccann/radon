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
ON = {"RADON_CTA_LLM_COPY": "1", "ANTHROPIC_API_KEY": "sk-test"}


def caller_returning(text: str):
    def _caller(**_kwargs):
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
        monkeypatch.setattr(cta_llm, "_project_env", lambda: {"ANTHROPIC_API_KEY": "live-key"})

        assert cta_llm.setting({}, "ANTHROPIC_API_KEY") == ""
        assert cta_llm.setting({"ANTHROPIC_API_KEY": "given"}, "ANTHROPIC_API_KEY") == "given"

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
