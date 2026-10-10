"""Stage 4 of the security runner prompt must authenticate the scan's claude.

The agent's shell does not inherit CLAUDE_CODE_OAUTH_TOKEN, so `claude auth
status` reported logged out and the scan was skipped (2026-10-10 log:129).
A token login reports authMethod "oauth_token", which is the subscription.
"""
from pathlib import Path

PROMPT = Path(__file__).resolve().parents[2] / ".claude" / "runner-prompts" / "security.md"


def _stage4() -> str:
    text = PROMPT.read_text()
    start = text.index("### Stage 4: Anthropic Claude Security")
    return text[start:text.index("### Stage 5", start)]


def test_scan_loads_token_from_bot_secrets_file():
    s = _stage4()
    assert 'grep \'^CLAUDE_CODE_OAUTH_TOKEN=\' "$HOME/.radon-runner.env"' in s
    assert s.index("export CLAUDE_CODE_OAUTH_TOKEN") < s.index("claude auth status 2>/dev/null")


def test_oauth_token_counts_as_subscription():
    s = _stage4()
    assert '"(claude\\.ai|oauth_token)"' in s
    assert "grep -Eq" in s
