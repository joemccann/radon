"""Daily Artificial Analysis frontier-basket refresh contracts."""

from __future__ import annotations

import json
import os
from datetime import date
from uuid import NAMESPACE_URL, uuid5

import pytest

import scripts.aa_frontier_refresh as refresh_module
from scripts.aa_frontier_refresh import (
    BASKET_SECRET,
    CREATOR_IDS,
    DEFAULT_PROVIDERS,
    MINIMUM_CATALOG_ROWS,
    CatalogError,
    fetch_catalog,
    refresh_basket,
    run_once,
    select_frontier_basket,
)


def model(
    provider: str,
    slug: str,
    *,
    score: float = 30,
    released: str = "2026-08-01",
    name: str | None = None,
    input_price: float = 1,
    output_price: float = 2,
) -> dict:
    return {
        "id": str(uuid5(NAMESPACE_URL, f"{provider}:{slug}")),
        "slug": slug,
        "name": name or slug,
        "release_date": released,
        "model_creator": {"slug": provider, "id": CREATOR_IDS[provider]},
        "pricing": {
            "price_1m_input_tokens": input_price,
            "price_1m_output_tokens": output_price,
        },
        "evaluations": {"artificial_analysis_intelligence_index": score},
    }


def catalog(*extra: dict) -> dict:
    baseline = [model(provider, f"{provider}-frontier") for provider in DEFAULT_PROVIDERS]
    return {"status": 200, "data": [*baseline, *extra]}


def production_catalog(*extra: dict) -> dict:
    payload = catalog(*extra)
    for index in range(MINIMUM_CATALOG_ROWS - len(payload["data"])):
        payload["data"].append(
            {
                "id": str(uuid5(NAMESPACE_URL, f"untracked:{index}")),
                "slug": f"untracked-{index}",
                "name": f"Untracked {index}",
                "release_date": "2026-01-01",
                "model_creator": {"id": "00000000-0000-0000-0000-000000000000", "slug": "untracked"},
                "pricing": {"price_1m_input_tokens": 1, "price_1m_output_tokens": 1},
                "evaluations": {"artificial_analysis_intelligence_index": 1},
            }
        )
    return payload


def sized_catalog(size: int, *extra: dict) -> dict:
    payload = catalog(*extra)
    for index in range(size - len(payload["data"])):
        payload["data"].append(
            {
                "id": str(uuid5(NAMESPACE_URL, f"sized-untracked:{index}")),
                "slug": f"sized-untracked-{index}",
                "name": f"Untracked {index}",
                "release_date": "2026-01-01",
                "model_creator": {"id": "00000000-0000-0000-0000-000000000000", "slug": "untracked"},
                "pricing": {"price_1m_input_tokens": 1, "price_1m_output_tokens": 1},
                "evaluations": {"artificial_analysis_intelligence_index": 1},
            }
        )
    return payload


def test_selects_highest_scoring_current_text_model_per_provider():
    payload = catalog(
        model("openai", "openai-new-winner", score=52, released="2026-09-01"),
        model("openai", "openai-new-weaker", score=31, released="2026-09-02"),
        model("anthropic", "anthropic-preview", score=99, name="Claude Preview"),
        model("google", "google-vision", score=99, name="Gemini Vision"),
        model("xai", "xai-future", score=99, released="2026-10-01"),
    )

    selected = select_frontier_basket(payload, today=date(2026, 9, 8))

    assert selected[0] == "openai-new-winner"
    assert selected[1] == "anthropic-frontier"
    assert selected[2] == "google-frontier"
    assert selected[3] == "xai-frontier"
    assert len(selected) == len(DEFAULT_PROVIDERS)


def test_latest_release_and_stable_id_break_ties_deterministically():
    payload = catalog(
        model("deepseek", "deepseek-a", score=45, released="2026-09-01"),
        model("deepseek", "deepseek-z", score=45, released="2026-09-01"),
        model("deepseek", "deepseek-old", score=45, released="2026-08-31"),
    )

    selected = select_frontier_basket(payload, today=date(2026, 9, 8))
    payload["data"].reverse()
    reversed_selected = select_frontier_basket(payload, today=date(2026, 9, 8))

    assert selected == reversed_selected
    assert selected[4] in {"deepseek-a", "deepseek-z"}


def test_newer_model_inside_frontier_band_replaces_incumbent_but_weaker_one_waits():
    payload = catalog(
        model("google", "google-best", score=50, released="2026-08-01"),
        model("google", "google-new-frontier", score=45, released="2026-09-01"),
        model("google", "google-new-weak", score=44.9, released="2026-09-02"),
    )
    previous = [
        {
            "provider": "google",
            "model_id": str(uuid5(NAMESPACE_URL, "google:google-best")),
            "release_date": "2026-08-01",
            "slug": "google-best",
        }
    ]

    selected = select_frontier_basket(payload, today=date(2026, 9, 8), previous=previous)

    assert selected[2] == "google-new-frontier"


def test_slug_rename_of_same_model_id_updates_projection_without_identity_churn():
    renamed = model("meta", "meta-new-slug", score=50, released="2026-09-01")
    stable_id = renamed["id"]
    previous = [
        {
            "provider": "meta",
            "model_id": stable_id,
            "release_date": "2026-09-01",
            "slug": "meta-old-slug",
        }
    ]

    selected = select_frontier_basket(catalog(renamed), today=date(2026, 9, 8), previous=previous)

    assert selected[7] == "meta-new-slug"


def test_creator_slug_rename_does_not_break_stable_creator_identity():
    renamed = model("xai", "xai-new", score=50, released="2026-09-01")
    renamed["model_creator"]["slug"] = "renamed-upstream-slug"

    selected = select_frontier_basket(catalog(renamed), today=date(2026, 9, 8))

    assert selected[3] == "xai-new"


def test_live_shaped_anthropic_fallback_flagship_remains_eligible():
    fable = model(
        "anthropic",
        "claude-fable-5-1",
        score=53.4,
        released="2026-09-01",
        name="Claude Fable 5.1 (Adaptive Reasoning, Max Effort, Default Fallback)",
    )

    selected = select_frontier_basket(catalog(fable), today=date(2026, 9, 8))

    assert selected[1] == "claude-fable-5-1"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"status": 200, "data": "not-a-list"},
        {"status": 200, "data": []},
        {"status": 200, "data": [model("openai", "duplicate"), model("anthropic", "duplicate")]},
    ],
)
def test_rejects_malformed_or_ambiguous_catalogs(payload):
    with pytest.raises(CatalogError):
        select_frontier_basket(payload, today=date(2026, 9, 8))


def test_missing_provider_or_valid_pricing_rejects_entire_refresh():
    payload = catalog()
    payload["data"] = [row for row in payload["data"] if row["model_creator"]["slug"] != "meta"]
    payload["data"].append(model("meta", "meta-free", input_price=0, output_price=0))

    with pytest.raises(CatalogError, match="meta"):
        select_frontier_basket(payload, today=date(2026, 9, 8))


class FakeStore:
    def __init__(self, values: dict[str, str]):
        self.values = dict(values)
        self.writes: list[tuple[str, str, str]] = []

    def get_secret(self, name: str) -> str | None:
        return self.values.get(name)

    def set_secret(self, name: str, value: str, actor: str) -> None:
        self.writes.append((name, value, actor))
        self.values[name] = value


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def get(self, *_args, **_kwargs):
        self.calls += 1
        return FakeResponse(self.payload)


def test_refresh_updates_only_after_complete_selection_and_is_idempotent():
    store = FakeStore({BASKET_SECRET: "last-known-good"})
    payload = catalog(model("openai", "openai-winner", score=50))

    first = refresh_basket(store, payload, today=date(2026, 9, 8))
    second = refresh_basket(store, payload, today=date(2026, 9, 8))

    assert first["status"] == "updated"
    assert second["status"] == "unchanged"
    assert len(store.writes) == 1
    assert store.writes[0][0] == BASKET_SECRET
    assert store.writes[0][2] == "aa-frontier-refresh"


def test_failed_refresh_preserves_last_known_good_value():
    store = FakeStore({BASKET_SECRET: "last-known-good"})

    with pytest.raises(CatalogError):
        refresh_basket(store, {"status": 200, "data": []}, today=date(2026, 9, 8))

    assert store.values[BASKET_SECRET] == "last-known-good"
    assert store.writes == []


def test_selected_value_is_canonical_comma_separated_json_safe_text():
    store = FakeStore({})
    result = refresh_basket(store, catalog(), today=date(2026, 9, 8))

    value = store.values[BASKET_SECRET]
    assert value == ",".join(f"{provider}-frontier" for provider in DEFAULT_PROVIDERS)
    assert json.loads(json.dumps(result))["basket"] == value


def test_daily_state_skips_second_fetch_and_is_private(tmp_path):
    store = FakeStore({"ARTIFICIAL_ANALYSIS_API_KEY": "secret"})
    session = FakeSession(production_catalog())
    state_path = tmp_path / "state.json"

    first = run_once(store=store, session=session, state_path=state_path, today=date(2026, 9, 8))
    second = run_once(store=store, session=session, state_path=state_path, today=date(2026, 9, 8))

    assert first["status"] == "updated"
    assert second["status"] == "already-current"
    assert session.calls == 1
    assert os.stat(state_path).st_mode & 0o777 == 0o600
    state = json.loads(state_path.read_text())
    assert state["algorithm_version"] == "aa-frontier-v1"
    assert len(state["models"]) == len(DEFAULT_PROVIDERS)
    assert len(state["catalog_hash"]) == 64


def test_corrupt_state_refetches_without_overwriting_good_basket_on_bad_catalog(tmp_path):
    store = FakeStore(
        {
            "ARTIFICIAL_ANALYSIS_API_KEY": "secret",
            BASKET_SECRET: "last-known-good",
        }
    )
    state_path = tmp_path / "state.json"
    state_path.write_text('{"broken":')

    with pytest.raises(CatalogError):
        run_once(
            store=store,
            session=FakeSession({"status": 200, "data": []}),
            state_path=state_path,
            today=date(2026, 9, 8),
        )

    assert store.values[BASKET_SECRET] == "last-known-good"
    assert store.writes == []


def test_http_auth_failure_is_sanitized_and_never_reads_response_body():
    class Session:
        def get(self, *_args, **_kwargs):
            return FakeResponse({"secret_error": "must not leak"}, status_code=401)

    with pytest.raises(CatalogError, match=r"HTTP 401$") as exc:
        fetch_catalog("secret", session=Session())

    assert "must not leak" not in str(exc.value)


def test_same_date_score_revision_does_not_flip_frontier_incumbent():
    incumbent = model("anthropic", "anthropic-incumbent", score=46, released="2026-09-01")
    challenger = model("anthropic", "anthropic-challenger", score=50, released="2026-09-01")
    previous = [
        {
            "provider": "anthropic",
            "model_id": incumbent["id"],
            "release_date": incumbent["release_date"],
            "slug": incumbent["slug"],
        }
    ]

    selected = select_frontier_basket(
        catalog(incumbent, challenger),
        today=date(2026, 9, 8),
        previous=previous,
    )

    assert selected[1] == "anthropic-incumbent"


def test_incumbent_below_frontier_band_falls_back_immediately():
    incumbent = model("mistral", "mistral-incumbent", score=44.9, released="2026-09-02")
    leader = model("mistral", "mistral-leader", score=50, released="2026-09-01")
    previous = [
        {
            "provider": "mistral",
            "model_id": incumbent["id"],
            "release_date": incumbent["release_date"],
            "slug": incumbent["slug"],
        }
    ]

    selected = select_frontier_basket(
        catalog(incumbent, leader),
        today=date(2026, 9, 8),
        previous=previous,
    )

    assert selected[6] == "mistral-leader"


def test_removed_incumbent_selects_available_frontier_fallback():
    replacement = model("openai", "openai-replacement", score=50, released="2026-09-01")
    previous = [
        {
            "provider": "openai",
            "model_id": str(uuid5(NAMESPACE_URL, "openai:removed")),
            "release_date": "2026-09-02",
            "slug": "removed",
        }
    ]

    selected = select_frontier_basket(
        catalog(replacement),
        today=date(2026, 9, 8),
        previous=previous,
    )

    assert selected[0] == "openai-replacement"


def test_state_write_failure_leaves_encrypted_basket_untouched(tmp_path, monkeypatch):
    store = FakeStore(
        {
            "ARTIFICIAL_ANALYSIS_API_KEY": "secret",
            BASKET_SECRET: "last-known-good",
        }
    )
    monkeypatch.setattr(refresh_module, "atomic_save", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("full")))

    with pytest.raises(OSError, match="full"):
        run_once(
            store=store,
            session=FakeSession(production_catalog()),
            state_path=tmp_path / "state.json",
            today=date(2026, 9, 8),
        )

    assert store.values[BASKET_SECRET] == "last-known-good"
    assert store.writes == []


def test_secret_write_failure_retries_from_durable_state(tmp_path):
    class FailingStore(FakeStore):
        fail = True

        def set_secret(self, name: str, value: str, actor: str) -> None:
            if self.fail:
                raise OSError("secret store unavailable")
            super().set_secret(name, value, actor)

    store = FailingStore(
        {
            "ARTIFICIAL_ANALYSIS_API_KEY": "secret",
            BASKET_SECRET: "last-known-good",
        }
    )
    session = FakeSession(production_catalog())
    state_path = tmp_path / "state.json"

    with pytest.raises(OSError, match="secret store unavailable"):
        run_once(store=store, session=session, state_path=state_path, today=date(2026, 9, 8))
    assert state_path.exists()
    assert store.values[BASKET_SECRET] == "last-known-good"

    store.fail = False
    result = run_once(store=store, session=session, state_path=state_path, today=date(2026, 9, 8))
    assert result["status"] == "updated"
    assert session.calls == 2


def test_catalog_shrink_below_eighty_percent_preserves_last_known_good(tmp_path):
    store = FakeStore({"ARTIFICIAL_ANALYSIS_API_KEY": "secret"})
    state_path = tmp_path / "state.json"
    run_once(
        store=store,
        session=FakeSession(sized_catalog(200)),
        state_path=state_path,
        today=date(2026, 9, 7),
    )
    prior = store.values[BASKET_SECRET]
    writes = len(store.writes)

    with pytest.raises(CatalogError, match="incomplete"):
        run_once(
            store=store,
            session=FakeSession(sized_catalog(159)),
            state_path=state_path,
            today=date(2026, 9, 8),
        )

    assert store.values[BASKET_SECRET] == prior
    assert len(store.writes) == writes


def test_real_encrypted_store_round_trip(tmp_path):
    from scripts.secret_store import SecretStore

    store = SecretStore(db_path=tmp_path / "secrets.db", key_path=tmp_path / "secret.key")
    store.set_secret("ARTIFICIAL_ANALYSIS_API_KEY", "test-api-key", actor="test")

    result = run_once(
        store=store,
        session=FakeSession(production_catalog()),
        state_path=tmp_path / "state.json",
        today=date(2026, 9, 8),
    )

    assert result["status"] == "updated"
    assert store.get_secret(BASKET_SECRET) == result["basket"]
    metadata = next(entry for entry in store.list_secrets() if entry["name"] == BASKET_SECRET)
    assert metadata["updated_by"] == "aa-frontier-refresh"
