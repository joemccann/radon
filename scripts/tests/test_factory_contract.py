"""Static rails for the Radon software factory.

The factory app lives under factory/ and deploys separately on Vercel.
These checks keep its stop-list, branch prefix, and secret boundary from
drifting without a docs/factory.md update.
"""
from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_DOCS = _ROOT / "docs" / "factory.md"
_FACTORY = _ROOT / "factory"
_CONSTANTS = _FACTORY / "agent" / "lib" / "constants.ts"
_GITHUB_EXT = _FACTORY / "agent" / "extensions" / "github.ts"
_CLASSIFIER = _FACTORY / "agent" / "subagents" / "classifier" / "instructions.md"
_ANALYST = _FACTORY / "agent" / "subagents" / "analyst" / "instructions.md"
_IMPLEMENTER = _FACTORY / "agent" / "subagents" / "implementer" / "instructions.md"
_REVIEWER = _FACTORY / "agent" / "subagents" / "reviewer" / "instructions.md"
_ENV_EXAMPLE = _FACTORY / ".env.example"
_SETUP = _ROOT / "scripts" / "factory_sandbox_setup.sh"
_RADON_EVAL = _FACTORY / "evals" / "safety" / "radon-out-of-scope.eval.ts"

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

MERGE_TOOLS = (
    "mergePullRequest",
    "mergeBranch",
    "github__merge",
)


def _read(path: Path) -> str:
    assert path.is_file(), f"missing {path.relative_to(_ROOT)}"
    return path.read_text(encoding="utf-8")


class TestFactoryDocs:
    def test_owner_doc_states_the_human_merge_gate(self):
        text = _read(_DOCS)
        for token in (
            "draft pull request",
            "never merge",
            "never push main",
            "label `factory`",
            "factory/",
        ):
            assert token in text, token

    def test_owner_doc_lists_every_stop_class(self):
        text = _read(_DOCS)
        missing = [t for t in STOP_TOKENS if t not in text]
        assert not missing, missing


class TestFactoryAppRails:
    def test_label_and_branch_prefix_defaults(self):
        text = _read(_CONSTANTS)
        assert '?? "factory"' in text
        assert '?? "factory/"' in text

    def test_linear_connector_is_optional(self):
        constants = _read(_CONSTANTS)
        assert "requireEnv(\"LINEAR_CONNECTOR\"" not in constants
        assert not (_FACTORY / "agent" / "connections" / "linear.ts").exists()
        assert not (_FACTORY / "agent" / "channels" / "linear.ts").exists()

    def test_merge_tools_are_not_mounted(self):
        text = _read(_GITHUB_EXT)
        for tool in MERGE_TOOLS:
            assert tool not in text, tool
        assert "createPullRequest" in text

    def test_validate_branch_refuses_main(self):
        git_remote = _read(_FACTORY / "agent" / "lib" / "github" / "git-remote.ts")
        assert 'PROTECTED_BRANCHES = new Set(["main", "master"])' in git_remote

    def test_classifier_stop_classes(self):
        text = _read(_CLASSIFIER)
        missing = [t for t in STOP_TOKENS if t not in text]
        assert not missing, missing
        assert "needs_clarification" in text

    def test_analyst_reads_factory_doc_and_scoped_runbooks(self):
        text = _read(_ANALYST)
        assert "docs/factory.md" in text
        assert "CLAUDE.md" in text
        assert "red/green" in text

    def test_implementer_tdd_and_path_scoped_add(self):
        text = _read(_IMPLEMENTER)
        for token in (
            "failing test",
            "git add",
            "git add -A",
            "bunx vitest",
            "python3.13",
            "factory/<type>-",
        ):
            assert token in text, token

    def test_reviewer_rejects_secret_and_trading_surface(self):
        text = _read(_REVIEWER)
        for token in STOP_TOKENS:
            assert token in text, token
        assert "reject" in text
        assert "main" in text

    def test_env_example_targets_radon_and_sandbox_setup(self):
        text = _read(_ENV_EXAMPLE)
        assert "FACTORY_REPO=joemccann/radon" in text
        assert "scripts/factory_sandbox_setup.sh" in text
        assert "LINEAR_CONNECTOR" not in text or text.strip().startswith("#") or (
            "LINEAR_CONNECTOR" in text and "optional" in text.lower()
        )


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


class TestRadonEval:
    def test_out_of_scope_eval_exists(self):
        text = _read(_RADON_EVAL)
        assert "radon-out-of-scope" in text or "out of scope" in text.lower() or "P1" in text
        assert "classifier" in text
        assert "implementer" in text
