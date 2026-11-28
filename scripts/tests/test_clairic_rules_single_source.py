"""The Clairic date grammar has one definition.

Research reaches it through the model gateway. The gateway's native
Cloudflare client is the other transport. Neither side keeps a second
copy of the page trim, the request shape, or the accept/reject rules.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OWNER = "scripts/model_gateway/clairic_rules.py"
GRAMMAR = (
    "def build_state(",
    "def build_request(",
    "def sanity_failure(",
    "def reject_clairic(",
    "def apply_date(",
)


def test_clairic_date_grammar_has_one_definition():
    found: dict[str, list[str]] = {name: [] for name in GRAMMAR}
    for path in (REPO_ROOT / "scripts").rglob("*.py"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel.startswith("scripts/tests/") or "/tests/" in f"/{rel}":
            continue
        text = path.read_text(encoding="utf-8")
        for name in GRAMMAR:
            if name in text:
                found[name].append(rel)
    assert found == {name: [OWNER] for name in GRAMMAR}
