"""Static rails for the sibling Radon software factory.

The Foreman app lives in joemccann/radon-factory, not under factory/ here.
These checks keep the stop-list, branch prefix, and secret boundary from
drifting without a docs/factory.md update.
"""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_DOCS = _ROOT / "docs" / "factory.md"
_SETUP = _ROOT / "scripts" / "factory_sandbox_setup.sh"
_OWNERS = _ROOT / "docs" / "owners.json"

STOP_TOKENS = (
    "IB Gateway",
    "live order",
    "trading halt",
    "production .env",
    "deploy.sh",
    "P1",
    "UW_TOKEN",
    "IB_FLEX",
)


def _read(path: Path) -> str:
    assert path.is_file(), f"missing {path.relative_to(_ROOT)}"
    return path.read_text(encoding="utf-8")


class TestFactoryIsSibling:
    def test_factory_app_is_not_in_this_repo(self):
        assert not (_ROOT / "factory").exists()


class TestFactoryDocs:
    def test_owner_doc_states_the_human_merge_gate(self):
        text = _read(_DOCS)
        for token in (
            "draft pull request",
            "never merge",
            "never push main",
            "label `factory`",
            "factory/",
            "joemccann/radon-factory",
        ):
            assert token in text, token

    def test_owner_doc_lists_every_stop_class(self):
        text = _read(_DOCS)
        missing = [t for t in STOP_TOKENS if t not in text]
        assert not missing, missing


class TestSandboxSetup:
    def test_setup_script_never_sources_env(self):
        text = _read(_SETUP)
        assert "set -a" not in text
        assert ". .env" not in text
        assert "source .env" not in text
        assert "radon-cloud/.env" not in text
        assert "IB_FLEX" not in text
        assert "UW_TOKEN" not in text
        assert "bun install" in text
        assert "requirements.txt" in text


class TestFactoryOwners:
    def test_owners_map_covers_setup_script(self):
        data = json.loads(_read(_OWNERS))
        rules = [r for r in data["rules"] if r.get("id") == "factory"]
        assert len(rules) == 1
        rule = rules[0]
        assert "scripts/factory_sandbox_setup.sh" in rule["globs"]
        assert "factory/**" not in rule["globs"]
        assert "docs/factory.md" in rule["owners"]
