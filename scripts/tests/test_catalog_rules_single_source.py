"""Frontier selection and catalog persistence have one definition.

The app job asks the model gateway to discover rows, then writes them.
It must not keep a second copy of the selectors or the Turso/JSON store.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OWNER = "scripts/model_gateway/_catalog_backend.py"
RULES = (
    "def select_anthropic_frontier(",
    "def select_openai_frontier(",
    "def select_xai_frontier(",
    "def label_for(",
)


def test_catalog_frontier_rules_have_one_definition():
    found: dict[str, list[str]] = {name: [] for name in RULES}
    for path in (REPO_ROOT / "scripts").rglob("*.py"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel.startswith("scripts/tests/") or "/tests/" in f"/{rel}":
            continue
        text = path.read_text(encoding="utf-8")
        for name in RULES:
            if name in text:
                found[name].append(rel)
    assert found == {name: [OWNER] for name in RULES}


def test_app_previous_seed_keeps_every_catalog_provider(monkeypatch, tmp_path):
    """A hosted provider that already has a row must be offered for carry-forward.

    The app used to filter the seed to three providers, so a failed NVIDIA
    or Cerebras poll could not keep the row the gateway already knows.
    """
    import refresh_model_catalog as facade
    from db import writer
    from model_gateway import _catalog_backend as backend

    missing = tmp_path / "absent.json"
    monkeypatch.setattr(backend, "LLM_MODELS_JSON", missing)
    rows = [
        {
            "provider": "nvidia",
            "model_id": "nvidia/nemotron",
            "display_name": "NEMOTRON",
            "refreshed_at": "t",
        },
        {
            "provider": "cerebras",
            "model_id": "cerebras/flagship",
            "display_name": "CEREBRAS",
            "refreshed_at": "t",
        },
        {
            "provider": "anthropic",
            "model_id": "claude-opus-5",
            "display_name": "CLAUDE OPUS 5",
            "refreshed_at": "t",
        },
    ]
    monkeypatch.setattr(writer, "get_llm_model_catalog_rows", lambda: list(rows))

    previous = facade.load_previous()

    assert set(previous) == {"nvidia", "cerebras", "anthropic"}
    assert previous["nvidia"]["model_id"] == "nvidia/nemotron"
