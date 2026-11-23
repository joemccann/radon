"""Generation resolves metadata discovered by an otherwise stateless gateway."""
from model_gateway import latest


def test_discovered_anthropic_model_survives_empty_refresh(tmp_path, monkeypatch):
    monkeypatch.setattr(latest, "_DISCOVERED_MODELS", {})
    monkeypatch.setattr(latest, "_catalog_path", lambda: tmp_path / "missing.json")
    model = {"id": "claude-opus-5", "type": "model", "created_at": "2026-11-01T00:00:00Z",
             "capabilities": {"image_input": {"supported": True}}}
    latest.remember_discovered_models("anthropic", [model])
    assert latest.resolved_default("anthropic") == "claude-opus-5"
    latest.remember_discovered_models("anthropic", [])
    assert latest.resolved_default("anthropic") == "claude-opus-5"


def test_inventory_refresh_supplies_generation_model_without_catalog(tmp_path, monkeypatch):
    from model_gateway import engine, specialized, _catalog_backend as catalog
    monkeypatch.setattr(latest, "_DISCOVERED_MODELS", {})
    monkeypatch.setattr(latest, "_catalog_path", lambda: tmp_path / "missing.json")
    monkeypatch.setattr(specialized, "_INVENTORY_METADATA", {})
    monkeypatch.setattr(specialized, "_INVENTORY_FAILURES", {})
    monkeypatch.setattr(specialized, "_INVENTORY_STAMP", None)
    monkeypatch.setattr(catalog, "provider_key", lambda name: "test" if name == "anthropic" else None)
    monkeypatch.setattr(catalog, "fetch_anthropic_models", lambda key: [
        {"id": "claude-opus-5", "type": "model", "created_at": "2026-11-01T00:00:00Z",
         "capabilities": {"image_input": {"supported": True}}}
    ])
    monkeypatch.setattr(engine, "wired_providers", lambda: ["anthropic"])
    inventory = specialized.model_inventory(refresh=True)
    assert inventory["defaultId"] == "claude-opus-5"
    assert engine._model_for("anthropic", {}, kind="text") == "claude-opus-5"


def test_unresolved_anthropic_model_stops_before_real_transport():
    import pytest
    from model_gateway import engine

    with pytest.raises(ValueError, match="model_unavailable"):
        engine._default_post("https://api.anthropic.com/v1/messages",
                             headers={}, json={"model": ""}, timeout=1)
