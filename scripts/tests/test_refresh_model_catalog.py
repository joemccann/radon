"""LLM model catalog refresh - red tests for the frontier-selection rules.

Fixtures under ``fixtures/``:
  - ``llm_models_anthropic.json`` - the REAL ``GET /v1/models?limit=100``
    body captured 2026-08-29 with the key from ``web/.env``: 10 models,
    ``claude-opus-5`` newest at 2026-07-24. Every trap the rules must reject
    is already in it (three dated snapshots, ``claude-haiku-4-5-20251001``,
    ``claude-fable-5``), so no expectation below is invented.
  - ``llm_models_openai.json`` / ``llm_models_xai.json`` - CONSTRUCTED from
    the documented envelopes (``{object, data[]}`` for OpenAI,
    ``{models[]}`` for xAI's ``/v1/language-models``); no key for either
    provider exists in this checkout, so these encode the documented field
    set plus every id the selection rules are required to reject.

Expectations are recomputed from the fixtures' own fields inside the tests
rather than from mental arithmetic.
"""

from __future__ import annotations

import copy
import inspect
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from refresh_model_catalog import (
    PROVIDERS,
    build_output,
    label_for,
    persist_result,
    provider_key,
    provider_override,
    select_anthropic_frontier,
    select_openai_frontier,
    select_xai_frontier,
)

FIXTURES = Path(__file__).parent / "fixtures"
MIGRATION = Path(__file__).parents[1] / "db" / "migrations" / "0060_llm_model_catalog.sql"

ANTHROPIC = json.loads((FIXTURES / "llm_models_anthropic.json").read_text())["data"]
OPENAI = json.loads((FIXTURES / "llm_models_openai.json").read_text())["data"]
XAI = json.loads((FIXTURES / "llm_models_xai.json").read_text())["models"]

ALL_KEY_VARS = [
    "ANTHROPIC_API_KEY", "CLAUDE_CODE_API_KEY", "CLAUDE_API_KEY",
    "OPENAI_API_KEY",
    "XAI_API_KEY", "GROK_API_KEY",
    "ANTHROPIC_MODEL", "OPENAI_MODEL", "XAI_MODEL", "GROK_MODEL",
]


@pytest.fixture()
def clean_env(monkeypatch):
    for name in ALL_KEY_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


class TestFixtureAnchors:
    def test_anthropic_fixture_is_the_live_shape(self):
        assert {m["type"] for m in ANTHROPIC} == {"model"}
        assert all("created_at" in m and "max_input_tokens" in m for m in ANTHROPIC)
        # Image input does not discriminate on Anthropic today - every model
        # reports it, so the rules must not lean on it to prune.
        assert all(m["capabilities"]["image_input"]["supported"] for m in ANTHROPIC)

    def test_traps_are_present_to_be_rejected(self):
        ids = {m["id"] for m in ANTHROPIC}
        assert {"claude-fable-5", "claude-opus-4-5-20251101", "claude-haiku-4-5-20251001"} <= ids
        openai_ids = {m["id"] for m in OPENAI}
        assert {"gpt-5.6-preview", "gpt-5.5-mini", "text-embedding-3-large", "whisper-1"} <= openai_ids
        xai_ids = {m["id"] for m in XAI}
        assert {"grok-4.20-0309-reasoning", "grok-imagine-image-2.0", "grok-4.1-fast"} <= xai_ids


class TestSelectAnthropic:
    def test_picks_the_newest_undated_chat_model(self):
        newest = max(
            (m for m in ANTHROPIC if not m["id"].startswith(("claude-fable-", "claude-mythos-"))
             and not m["id"][-8:].isdigit()),
            key=lambda m: m["created_at"],
        )
        assert select_anthropic_frontier(ANTHROPIC)["id"] == newest["id"] == "claude-opus-5"

    def test_dated_snapshots_lose_to_undated_siblings(self):
        chosen = select_anthropic_frontier(ANTHROPIC)["id"]
        dated = [m["id"] for m in ANTHROPIC if m["id"][-8:].isdigit()]
        assert dated  # the fixture carries three
        assert chosen not in dated

    def test_haiku_and_other_cheap_tiers_never_win(self):
        assert "haiku" not in select_anthropic_frontier(ANTHROPIC)["id"]

    def test_premium_gated_tiers_are_rejected_even_when_newest(self):
        # fable/mythos are 2x price and ZDR-incompatible - a deliberate
        # opt-in, never the automatic default, even dated tomorrow.
        models = copy.deepcopy(ANTHROPIC)
        models.append({
            "type": "model", "id": "claude-mythos-5", "display_name": "Claude Mythos 5",
            "created_at": "2027-01-01T00:00:00Z", "max_input_tokens": 1000000,
            "max_tokens": 128000, "capabilities": {"image_input": {"supported": True}},
        })
        assert select_anthropic_frontier(models)["id"] == "claude-opus-5"

    def test_empty_list_selects_nothing(self):
        assert select_anthropic_frontier([]) is None


class TestSelectOpenai:
    def test_picks_the_highest_version_explicit_flagship(self):
        assert select_openai_frontier(OPENAI)["id"] == "gpt-5.6-sol"

    def test_falls_back_to_the_bare_alias_when_the_key_cannot_see_5_6(self):
        # A key without GPT-5.6 access simply never sees those rows.
        visible = [m for m in OPENAI if not m["id"].startswith("gpt-5.6")]
        assert select_openai_frontier(visible)["id"] == "gpt-5.5"

    def test_dated_and_mmdd_snapshots_lose_to_the_undated_alias(self):
        snapshots = [m for m in OPENAI if m["id"] in ("gpt-5.5-2026-04-24", "gpt-5.5-0424")]
        assert len(snapshots) == 2
        assert select_openai_frontier(snapshots + [{"id": "gpt-5.5", "object": "model",
                                                    "created": 1, "shutdown_date": None}])["id"] == "gpt-5.5"

    def test_mini_and_nano_tiers_are_rejected(self):
        cheap = [m for m in OPENAI if m["id"] in ("gpt-5.5-mini", "gpt-5.5-nano", "gpt-5.6-luna")]
        assert len(cheap) == 3
        assert select_openai_frontier(cheap) is None

    def test_non_chat_modalities_are_rejected(self):
        non_chat = [
            m for m in OPENAI
            if m["id"] in (
                "text-embedding-3-large", "whisper-1", "dall-e-3", "gpt-image-1",
                "omni-moderation-latest", "gpt-4o-mini-tts", "gpt-4o-transcribe",
                "gpt-4o-audio-preview", "gpt-4o-realtime-preview", "gpt-4o-search-preview",
                "gpt-5-codex", "gpt-3.5-turbo-instruct",
            )
        ]
        assert len(non_chat) == 12
        assert select_openai_frontier(non_chat) is None

    def test_preview_ids_lose_to_a_stable_sibling(self):
        chosen = select_openai_frontier(OPENAI)["id"]
        assert "preview" not in chosen

    def test_scheduled_shutdown_rows_are_rejected(self):
        retiring = [m for m in OPENAI if m["shutdown_date"]]
        assert retiring  # gpt-4.1 and gpt-4o carry a shutdown_date
        assert select_openai_frontier(retiring) is None

    def test_pro_and_floating_alias_never_win(self):
        chosen = select_openai_frontier(OPENAI)["id"]
        assert not chosen.endswith("-pro")
        assert not chosen.endswith("-chat-latest")


class TestSelectXai:
    def test_picks_the_documented_frontier(self):
        assert select_xai_frontier(XAI)["id"] == "grok-4.6"

    def test_never_ranks_by_context_length(self):
        # grok-4.3 has the largest context (1M vs 500k) and must still lose.
        chosen = select_xai_frontier(XAI)["id"]
        assert chosen != "grok-4.3"

    def test_dotted_minor_versions_use_float_not_semver_ordering(self):
        # Under a semver tuple 4.20 beats 4.6; under parseFloat it is 4.2.
        models = [m for m in XAI if m["id"] in ("grok-4.6", "grok-4.20-0309-reasoning")]
        assert len(models) == 2
        assert select_xai_frontier(models)["id"] == "grok-4.6"

    def test_fast_tier_and_agentic_sku_are_rejected(self):
        narrowed = [m for m in XAI if m["id"] in ("grok-4.1-fast", "grok-build-0.1")]
        assert len(narrowed) == 2
        assert select_xai_frontier(narrowed) is None

    def test_non_text_output_modalities_are_rejected(self):
        media = [
            m for m in XAI
            if m["id"] in ("grok-imagine-image-2.0", "grok-imagine-video-1.5",
                           "grok-voice-think-fast-2.0")
        ]
        assert len(media) == 3
        assert select_xai_frontier(media) is None

    def test_mmdd_snapshots_are_rejected(self):
        snapshots = [m for m in XAI if m["id"] in ("grok-2-1212", "grok-2-vision-1212")]
        assert len(snapshots) == 2
        assert select_xai_frontier(snapshots) is None

    def test_works_without_modality_fields(self):
        # /v1/models has no input_modalities/output_modalities - the id
        # predicates alone must still land on the frontier.
        stripped = [
            {k: v for k, v in m.items() if not k.endswith("_modalities")} for m in XAI
        ]
        assert select_xai_frontier(stripped)["id"] == "grok-4.6"


class TestProviderKeys:
    def test_no_key_means_no_provider(self, clean_env):
        assert [p for p in PROVIDERS if provider_key(p)] == []

    def test_anthropic_key_lights_up_only_anthropic(self, clean_env):
        clean_env.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        assert [p for p in PROVIDERS if provider_key(p)] == ["anthropic"]

    def test_grok_alias_lights_up_xai(self, clean_env):
        clean_env.setenv("GROK_API_KEY", "xai-test")
        assert provider_key("xai") == "xai-test"

    def test_blank_key_does_not_count(self, clean_env):
        clean_env.setenv("OPENAI_API_KEY", "   ")
        assert provider_key("openai") is None


class TestOverrides:
    def test_override_env_var_beats_discovery(self, clean_env):
        clean_env.setenv("XAI_MODEL", "grok-4.7-operator-pin")
        assert provider_override("xai") == "grok-4.7-operator-pin"

    def test_no_override_by_default(self, clean_env):
        assert [provider_override(p) for p in PROVIDERS] == [None, None, None]


class TestBuildOutput:
    def test_rows_carry_the_catalog_reader_fields(self):
        # web/lib/llm/catalog.ts:toModelOption reads provider / model_id /
        # display_name / refreshed_at from BOTH Turso and this JSON.
        scan_time = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        rows = [{"provider": "anthropic", "model_id": "claude-opus-5",
                 "display_name": label_for("claude-opus-5"), "refreshed_at": scan_time}]
        payload = build_output(rows=rows, scan_time=scan_time, preferred="anthropic")
        assert payload["models"] == [{
            "provider": "anthropic",
            "model_id": "claude-opus-5",
            "display_name": "CLAUDE OPUS 5",
            "refreshed_at": scan_time,
        }]
        assert payload["defaultId"] == "claude-opus-5"
        assert payload["refreshed_at"] == scan_time

    def test_default_follows_the_preferred_provider(self):
        scan_time = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        rows = [
            {"provider": "anthropic", "model_id": "claude-opus-5",
             "display_name": "CLAUDE OPUS 5", "refreshed_at": scan_time},
            {"provider": "xai", "model_id": "grok-4.6", "display_name": "GROK 4.6",
             "refreshed_at": scan_time},
        ]
        assert build_output(rows=rows, scan_time=scan_time, preferred="xai")["defaultId"] == "grok-4.6"


@pytest.fixture()
def turso_catalog():
    """Rows the fake Turso ``llm_model_catalog`` table answers with."""
    return []


@pytest.fixture()
def persist_calls(tmp_path, monkeypatch, turso_catalog):
    import refresh_model_catalog as mod

    calls: list[tuple] = []
    monkeypatch.setattr(mod, "LLM_MODELS_JSON", tmp_path / "llm_models.json")
    monkeypatch.setattr(mod.writer, "get_llm_model_catalog_rows",
                        lambda: [dict(row) for row in turso_catalog], raising=False)
    monkeypatch.setattr(mod.writer, "ensure_no_replica_for_writers",
                        lambda: calls.append(("guard",)))
    monkeypatch.setattr(mod.writer, "upsert_llm_model_catalog_rows",
                        lambda rows, refreshed_at=None: calls.append(("rows", len(rows))))
    monkeypatch.setattr(mod.writer, "upsert_scan_snapshot",
                        lambda service, scan_time, payload: calls.append(("snapshot", service)))
    monkeypatch.setattr(mod.writer, "record_service_health",
                        lambda service, state, **kw: calls.append(
                            ("health", service, state, kw.get("error"))))
    return calls


def _health_rows(calls: list[tuple]) -> list[tuple]:
    return [call for call in calls if call[0] == "health"]


class TestPersistResult:
    def test_writes_in_order_then_the_json_fallback(self, persist_calls):
        import refresh_model_catalog as mod

        scan_time = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        rows = [{"provider": "anthropic", "model_id": "claude-opus-5",
                 "display_name": "CLAUDE OPUS 5", "refreshed_at": scan_time}]
        payload = build_output(rows=rows, scan_time=scan_time, preferred="anthropic")
        persist_result(payload, rows)

        assert persist_calls == [
            ("guard",),
            ("rows", 1),
            ("snapshot", "model-catalog"),
            ("health", "model-catalog", "ok", None),
        ]
        fallback = json.loads(mod.LLM_MODELS_JSON.read_text())
        assert fallback["defaultId"] == "claude-opus-5"
        assert fallback["models"][0]["model_id"] == "claude-opus-5"

    def test_zero_rows_keeps_the_catalog_and_reports_the_failure(self, persist_calls):
        """Every keyed provider failed: the last good Turso rows and JSON
        cache must survive untouched (the old guarantee), and the heartbeat
        must say so - an ``ok`` here is the NF-9 shape (R-455): the 26h
        alarm can never fire on a catalog that is silently aging.
        """
        import refresh_model_catalog as mod

        scan_time = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        payload = build_output(rows=[], scan_time=scan_time, preferred="anthropic")
        persist_result(payload, [], failed={"anthropic": "401 unauthorized"})

        assert ("rows", 0) not in persist_calls
        assert ("snapshot", "model-catalog") not in persist_calls
        assert not mod.LLM_MODELS_JSON.exists()
        [(_, service, state, error)] = _health_rows(persist_calls)
        assert (service, state) == ("model-catalog", "error")
        assert error["class"] == "provider_carry_forward"
        assert error["providers"] == ["anthropic"]
        assert "401 unauthorized" in error["message"]

    def test_zero_rows_with_no_keyed_provider_is_a_plain_ok(self, persist_calls):
        # No key anywhere is a supported state, not a failure.
        scan_time = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        payload = build_output(rows=[], scan_time=scan_time, preferred="anthropic")
        persist_result(payload, [])

        assert _health_rows(persist_calls) == [("health", "model-catalog", "ok", None)]

    def test_a_carried_forward_provider_is_named_even_when_others_refreshed(self, persist_calls):
        scan_time = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        stale = (date.today() - timedelta(days=3)).isoformat()
        rows = [
            {"provider": "anthropic", "model_id": "claude-opus-5",
             "display_name": "CLAUDE OPUS 5", "refreshed_at": scan_time},
            {"provider": "openai", "model_id": "gpt-5.5", "display_name": "GPT 5.5",
             "refreshed_at": stale},
        ]
        payload = build_output(rows=rows, scan_time=scan_time, preferred="anthropic")
        persist_result(payload, rows, failed={"openai": "429 rate limited"})

        assert ("rows", 2) in persist_calls
        [(_, _, state, error)] = _health_rows(persist_calls)
        assert state == "error"
        assert error["providers"] == ["openai"]


class TestRun:
    def test_a_provider_with_no_key_produces_no_row_and_no_exception(
        self, persist_calls, clean_env
    ):
        import refresh_model_catalog as mod

        clean_env.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkey = clean_env
        monkey.setattr(mod, "fetch_anthropic_models", lambda key: ANTHROPIC)
        monkey.setattr(mod, "fetch_openai_models", lambda key: OPENAI)
        monkey.setattr(mod, "fetch_xai_models", lambda key: XAI)

        payload = mod.run()

        assert [m["provider"] for m in payload["models"]] == ["anthropic"]
        assert payload["defaultId"] == "claude-opus-5"
        assert ("rows", 1) in persist_calls

    def test_every_keyed_provider_is_polled(self, persist_calls, clean_env):
        import refresh_model_catalog as mod

        clean_env.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        clean_env.setenv("OPENAI_API_KEY", "sk-openai-test")
        clean_env.setenv("XAI_API_KEY", "xai-test")
        clean_env.setattr(mod, "fetch_anthropic_models", lambda key: ANTHROPIC)
        clean_env.setattr(mod, "fetch_openai_models", lambda key: OPENAI)
        clean_env.setattr(mod, "fetch_xai_models", lambda key: XAI)

        payload = mod.run()

        assert {m["model_id"] for m in payload["models"]} == {
            "claude-opus-5", "gpt-5.6-sol", "grok-4.6"
        }
        # resolveProvider() prefers xAI whenever its key is present.
        assert payload["defaultId"] == "grok-4.6"

    def test_a_failing_provider_keeps_its_previous_row(self, persist_calls, clean_env):
        import refresh_model_catalog as mod

        stale = (date.today() - timedelta(days=3)).isoformat()
        mod.LLM_MODELS_JSON.write_text(json.dumps({
            "models": [
                {"provider": "openai", "model_id": "gpt-5.5", "display_name": "GPT 5.5",
                 "refreshed_at": stale},
            ],
            "defaultId": "gpt-5.5",
        }))

        clean_env.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        clean_env.setenv("OPENAI_API_KEY", "sk-openai-test")

        def rate_limited(key):
            raise RuntimeError("429 rate limited")

        clean_env.setattr(mod, "fetch_anthropic_models", lambda key: ANTHROPIC)
        clean_env.setattr(mod, "fetch_openai_models", rate_limited)

        payload = mod.run()

        by_provider = {m["provider"]: m for m in payload["models"]}
        assert by_provider["openai"] == {
            "provider": "openai", "model_id": "gpt-5.5", "display_name": "GPT 5.5",
            "refreshed_at": stale,
        }
        assert by_provider["anthropic"]["model_id"] == "claude-opus-5"

    def test_an_empty_response_keeps_the_previous_row(self, persist_calls, clean_env):
        import refresh_model_catalog as mod

        stale = (date.today() - timedelta(days=3)).isoformat()
        mod.LLM_MODELS_JSON.write_text(json.dumps({
            "models": [{"provider": "xai", "model_id": "grok-4.6",
                        "display_name": "GROK 4.6", "refreshed_at": stale}],
            "defaultId": "grok-4.6",
        }))
        clean_env.setenv("XAI_API_KEY", "xai-test")
        clean_env.setattr(mod, "fetch_xai_models", lambda key: [])

        payload = mod.run()

        assert payload["models"][0]["refreshed_at"] == stale

    def test_override_wins_over_discovery_end_to_end(self, persist_calls, clean_env):
        import refresh_model_catalog as mod

        clean_env.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        clean_env.setenv("ANTHROPIC_MODEL", "claude-opus-4-8")

        def must_not_be_called(key):
            raise AssertionError("discovery ran despite an operator override")

        clean_env.setattr(mod, "fetch_anthropic_models", must_not_be_called)

        payload = mod.run()

        assert payload["models"][0]["model_id"] == "claude-opus-4-8"
        assert payload["models"][0]["display_name"] == "CLAUDE OPUS 4 8"

    def test_every_keyed_provider_failing_is_not_an_ok_heartbeat(self, persist_calls, clean_env):
        """R-455: a revoked key that 401s nightly must not look like a
        healthy daily run with today's finished_at."""
        import refresh_model_catalog as mod

        clean_env.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        clean_env.setenv("OPENAI_API_KEY", "sk-openai-test")

        def unauthorized(key):
            raise RuntimeError("HTTP Error 401: Unauthorized")

        clean_env.setattr(mod, "fetch_anthropic_models", unauthorized)
        clean_env.setattr(mod, "fetch_openai_models", unauthorized)

        payload = mod.run()

        assert payload["models"] == []
        assert ("rows", 0) not in persist_calls
        [(_, service, state, error)] = _health_rows(persist_calls)
        assert service == "model-catalog"
        assert state == "error"
        assert error is not None
        assert error["providers"] == ["anthropic", "openai"]
        assert "401" in error["message"]

    def test_a_carried_forward_provider_is_reported_by_run(self, persist_calls, clean_env):
        import refresh_model_catalog as mod

        stale = (date.today() - timedelta(days=3)).isoformat()
        mod.LLM_MODELS_JSON.write_text(json.dumps({
            "models": [{"provider": "openai", "model_id": "gpt-5.5",
                        "display_name": "GPT 5.5", "refreshed_at": stale}],
            "defaultId": "gpt-5.5",
        }))
        clean_env.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        clean_env.setenv("OPENAI_API_KEY", "sk-openai-test")

        def rate_limited(key):
            raise RuntimeError("429 rate limited")

        clean_env.setattr(mod, "fetch_anthropic_models", lambda key: ANTHROPIC)
        clean_env.setattr(mod, "fetch_openai_models", rate_limited)

        payload = mod.run()

        assert {m["provider"] for m in payload["models"]} == {"anthropic", "openai"}
        [(_, _, state, error)] = _health_rows(persist_calls)
        assert state == "error"
        assert error["providers"] == ["openai"]

    def test_a_writer_failure_leaves_an_error_row_not_a_traceback(
        self, persist_calls, clean_env, capsys
    ):
        """R-455/R-458: a Turso failure in the upsert used to exit with a
        traceback and no row at all, invisible until the 26h window."""
        import refresh_model_catalog as mod

        clean_env.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        clean_env.setattr(mod, "fetch_anthropic_models", lambda key: ANTHROPIC)

        def turso_down(rows, refreshed_at=None):
            raise ConnectionError("Hrana stream closed")

        clean_env.setattr(mod.writer, "upsert_llm_model_catalog_rows", turso_down)

        assert mod.main([]) == 1

        [(_, service, state, error)] = _health_rows(persist_calls)
        assert (service, state) == ("model-catalog", "error")
        assert error["class"] == "cycle_failed"
        assert "Hrana stream closed" in error["message"]
        assert not mod.LLM_MODELS_JSON.exists()

    def test_previous_rows_are_seeded_from_turso_when_the_json_is_absent(
        self, persist_calls, clean_env, turso_catalog
    ):
        """R-456: the carry-forward guarantee must not depend on a
        host-local file that is ephemeral on the VPS."""
        import refresh_model_catalog as mod

        stale = (date.today() - timedelta(days=5)).isoformat()
        turso_catalog.append({"provider": "openai", "model_id": "gpt-5.5",
                              "display_name": "GPT 5.5", "refreshed_at": stale})
        assert not mod.LLM_MODELS_JSON.exists()
        clean_env.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        clean_env.setenv("OPENAI_API_KEY", "sk-openai-test")

        def rate_limited(key):
            raise RuntimeError("429 rate limited")

        clean_env.setattr(mod, "fetch_anthropic_models", lambda key: ANTHROPIC)
        clean_env.setattr(mod, "fetch_openai_models", rate_limited)

        payload = mod.run()

        by_provider = {m["provider"]: m for m in payload["models"]}
        assert by_provider["openai"] == {
            "provider": "openai", "model_id": "gpt-5.5", "display_name": "GPT 5.5",
            "refreshed_at": stale,
        }
        assert ("rows", 2) in persist_calls

    def test_turso_rows_win_over_a_lagging_json(self, persist_calls, clean_env, turso_catalog):
        import refresh_model_catalog as mod

        turso_catalog.append({"provider": "openai", "model_id": "gpt-5.5",
                              "display_name": "GPT 5.5", "refreshed_at": "turso-stamp"})
        mod.LLM_MODELS_JSON.write_text(json.dumps({
            "models": [{"provider": "openai", "model_id": "gpt-5.4",
                        "display_name": "GPT 5.4", "refreshed_at": "json-stamp"}],
            "defaultId": "gpt-5.4",
        }))
        clean_env.setenv("OPENAI_API_KEY", "sk-openai-test")

        def rate_limited(key):
            raise RuntimeError("429 rate limited")

        clean_env.setattr(mod, "fetch_openai_models", rate_limited)

        payload = mod.run()

        assert payload["models"][0]["model_id"] == "gpt-5.5"
        assert payload["models"][0]["refreshed_at"] == "turso-stamp"

    def test_json_still_seeds_previous_when_turso_is_unreachable(self, persist_calls, clean_env):
        import refresh_model_catalog as mod

        def turso_down():
            raise ConnectionError("Hrana stream closed")

        clean_env.setattr(mod.writer, "get_llm_model_catalog_rows", turso_down)
        mod.LLM_MODELS_JSON.write_text(json.dumps({
            "models": [{"provider": "openai", "model_id": "gpt-5.5",
                        "display_name": "GPT 5.5", "refreshed_at": "json-stamp"}],
            "defaultId": "gpt-5.5",
        }))
        clean_env.setenv("OPENAI_API_KEY", "sk-openai-test")

        def rate_limited(key):
            raise RuntimeError("429 rate limited")

        clean_env.setattr(mod, "fetch_openai_models", rate_limited)

        payload = mod.run()

        assert payload["models"][0]["refreshed_at"] == "json-stamp"


class TestFetchBounds:
    def test_anthropic_pagination_terminates_within_the_page_cap(self, monkeypatch):
        """R-458: a server that answers has_more: true forever (or with a
        repeating last_id) used to spin until systemd killed the unit."""
        import refresh_model_catalog as mod

        pages: list[str] = []

        def always_more(url, headers):
            pages.append(url)
            if len(pages) > 50:
                raise AssertionError("pagination is uncapped")
            return {"data": [{"id": "claude-opus-5", "type": "model"}],
                    "has_more": True, "last_id": "claude-opus-5"}

        monkeypatch.setattr(mod, "_http_get_json", always_more)

        with pytest.raises(RuntimeError, match="page"):
            mod.fetch_anthropic_models("sk-ant-test")

        assert 1 < len(pages) <= mod.ANTHROPIC_MAX_PAGES

    def test_anthropic_pagination_still_follows_a_finite_cursor(self, monkeypatch):
        import refresh_model_catalog as mod

        def two_pages(url, headers):
            if "after_id" in url:
                return {"data": [{"id": "b"}], "has_more": False, "last_id": "b"}
            return {"data": [{"id": "a"}], "has_more": True, "last_id": "a"}

        monkeypatch.setattr(mod, "_http_get_json", two_pages)

        assert [m["id"] for m in mod.fetch_anthropic_models("sk-ant-test")] == ["a", "b"]

    def test_a_provider_that_overruns_its_budget_is_carried_forward(self, persist_calls, clean_env):
        """R-458: urlopen's timeout bounds one socket read, not the request;
        each provider gets its own wall-clock budget so one slow provider
        cannot consume the unit's TimeoutStartSec and lose the whole run."""
        import time

        import refresh_model_catalog as mod

        mod.LLM_MODELS_JSON.write_text(json.dumps({
            "models": [{"provider": "anthropic", "model_id": "claude-opus-4-8",
                        "display_name": "CLAUDE OPUS 4 8", "refreshed_at": "stale"}],
            "defaultId": "claude-opus-4-8",
        }))
        clean_env.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        clean_env.setattr(mod, "PROVIDER_BUDGET_S", 0.1, raising=False)

        def slow_drip(key):
            time.sleep(1.5)
            return ANTHROPIC

        clean_env.setattr(mod, "fetch_anthropic_models", slow_drip)

        started = time.monotonic()
        payload = mod.run()
        elapsed = time.monotonic() - started

        assert elapsed < 1.0
        assert payload["models"][0]["model_id"] == "claude-opus-4-8"
        [(_, _, state, error)] = _health_rows(persist_calls)
        assert state == "error"
        assert error["providers"] == ["anthropic"]
        assert "budget" in error["message"]


class TestStorage:
    @pytest.fixture()
    def db(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT);"
        )
        conn.executescript(MIGRATION.read_text())
        yield conn
        conn.close()

    def test_migration_registers_version_60(self, db):
        assert [r[0] for r in db.execute("SELECT version FROM schema_migrations")] == [60]

    def test_migration_is_rerunnable(self, db):
        db.executescript(MIGRATION.read_text())
        assert [r[0] for r in db.execute("SELECT version FROM schema_migrations")] == [60]

    def test_schema_columns_and_provider_primary_key(self, db):
        info = list(db.execute("PRAGMA table_info(llm_model_catalog)"))
        assert [r[1] for r in info] == ["provider", "model_id", "display_name", "refreshed_at"]
        assert {r[1]: r[5] for r in info}["provider"] == 1

    def test_refreshed_at_index_exists(self, db):
        names = {r[1] for r in db.execute("PRAGMA index_list(llm_model_catalog)")}
        assert "idx_llm_model_catalog_refreshed_at" in names

    def test_upsert_is_idempotent_per_provider(self, db, monkeypatch):
        from db import writer

        monkeypatch.setattr(writer, "get_db", lambda: db)
        writer.upsert_llm_model_catalog_rows(
            [{"provider": "anthropic", "model_id": "claude-opus-4-8",
              "display_name": "CLAUDE OPUS 4 8"}],
            refreshed_at="r1",
        )
        writer.upsert_llm_model_catalog_rows(
            [{"provider": "anthropic", "model_id": "claude-opus-5",
              "display_name": "CLAUDE OPUS 5"}],
            refreshed_at="r2",
        )
        rows = list(db.execute(
            "SELECT provider, model_id, display_name, refreshed_at FROM llm_model_catalog"
        ))
        assert rows == [("anthropic", "claude-opus-5", "CLAUDE OPUS 5", "r2")]

    def test_a_carried_forward_row_keeps_its_own_refreshed_at(self, db, monkeypatch):
        """A provider whose poll 429s is carried forward untouched, so its row
        must reach Turso with the stamp of the last run that actually SAW the
        model. Restamping the whole batch with today's scan_time makes
        ``LlmModelOption.refreshedAt`` - the field the picker's freshness copy
        is derived from - report a weeks-old row as refreshed today.
        """
        from db import writer

        monkeypatch.setattr(writer, "get_db", lambda: db)
        stale = (date.today() - timedelta(days=9)).isoformat()
        today = date.today().isoformat()
        writer.upsert_llm_model_catalog_rows(
            [
                {"provider": "openai", "model_id": "gpt-5.5",
                 "display_name": "GPT 5.5", "refreshed_at": stale},
                {"provider": "anthropic", "model_id": "claude-opus-5",
                 "display_name": "CLAUDE OPUS 5", "refreshed_at": today},
            ],
            refreshed_at=today,
        )
        stamps = dict(db.execute("SELECT provider, refreshed_at FROM llm_model_catalog"))
        assert stamps == {"openai": stale, "anthropic": today}

    def test_a_row_without_its_own_stamp_takes_the_batch_stamp(self, db, monkeypatch):
        from db import writer

        monkeypatch.setattr(writer, "get_db", lambda: db)
        writer.upsert_llm_model_catalog_rows(
            [{"provider": "xai", "model_id": "grok-4.6", "display_name": "GROK 4.6"}],
            refreshed_at="batch-stamp",
        )
        assert list(db.execute("SELECT refreshed_at FROM llm_model_catalog")) == [
            ("batch-stamp",)
        ]

    def test_writer_arity(self):
        from db import writer

        assert list(inspect.signature(writer.upsert_llm_model_catalog_rows).parameters) == [
            "rows", "refreshed_at",
        ]

    def test_reader_round_trips_the_catalog_reader_fields(self, db, monkeypatch):
        """The Turso seed for ``load_previous`` (R-456) must hand back the
        exact dict shape ``upsert_llm_model_catalog_rows`` accepts, so a
        carried-forward row round-trips with its own refreshed_at."""
        from db import writer

        monkeypatch.setattr(writer, "get_db", lambda: db)
        assert writer.get_llm_model_catalog_rows() == []
        writer.upsert_llm_model_catalog_rows(
            [{"provider": "openai", "model_id": "gpt-5.5",
              "display_name": "GPT 5.5", "refreshed_at": "r1"}],
            refreshed_at="batch",
        )
        assert writer.get_llm_model_catalog_rows() == [
            {"provider": "openai", "model_id": "gpt-5.5",
             "display_name": "GPT 5.5", "refreshed_at": "r1"},
        ]
