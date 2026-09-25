"""The native security-audit workflow must not hand git history to a model.

History inspection belongs to the pinned gitleaks run. The secrets dimension
consumes redacted metadata only.
"""

from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[2] / ".claude" / "workflows" / "security-audit.mjs"


def test_native_workflow_does_not_send_git_history_to_a_model():
    text = WORKFLOW.read_text(encoding="utf-8")
    for banned in ("git log", "log --all", "log -p", "log --all -p"):
        assert banned not in text, banned
    start = text.index("{ key: 'secrets'")
    end = text.index("{ key: 'client-xss'")
    scope = text[start:end]
    assert "gitleaks" in scope
    assert "redacted" in scope
    assert "Do not read commit" in scope
    assert ".env" in scope and "Never open" in scope
