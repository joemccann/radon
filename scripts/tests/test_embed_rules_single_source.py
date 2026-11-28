"""The knowledge vector decision has one definition.

The app asks the model gateway for vectors. The gateway calls NVIDIA.
Both used to carry the same coverage cache and the same fallback order.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OWNER = "scripts/model_gateway/embed_rules.py"
RULES = (
    "def embedding_text(",
    "def embed_backend(",
    "def dual_write_enabled(",
    "def v2_coverage_ready(",
    "def _probe_v2_coverage(",
    "def resolve_query_vector(",
)
MARKERS = (
    "SELECT 1 FROM knowledge WHERE embedding_v2 IS NULL LIMIT 1",
    "nvidia embedding dim",
)


def _production_sources():
    for path in (REPO_ROOT / "scripts").rglob("*.py"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel.startswith("scripts/tests/") or "/tests/" in f"/{rel}":
            continue
        yield rel, path.read_text(encoding="utf-8")


def test_embed_query_policy_has_one_definition():
    found: dict[str, list[str]] = {name: [] for name in RULES}
    for rel, text in _production_sources():
        for name in RULES:
            if name in text:
                found[name].append(rel)
    assert found == {name: [OWNER] for name in RULES}


def test_embed_query_policy_markers_have_one_home():
    found: dict[str, list[str]] = {marker: [] for marker in MARKERS}
    for rel, text in _production_sources():
        for marker in MARKERS:
            if marker in text:
                found[marker].append(rel)
    assert found == {marker: [OWNER] for marker in MARKERS}
