"""Production inference transports and provider secrets belong to one service."""
from pathlib import Path
import re
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[2]
PROVIDER_HTTP = re.compile(r"https://(?:api\.anthropic\.com|api\.openai\.com|api\.x\.ai|api\.cerebras\.ai|api\.cloudflare\.com|integrate\.api\.nvidia\.com|chatgpt\.com/backend-api/codex)")
PROVIDER_AUTH_READ = re.compile(r'(?:(?:os\.)?environ|env|src|source)\.get\(["\'](?:NVIDIA_API_KEY|CEREBRAS_API_KEY|CLAUDE_CODE_OAUTH_TOKEN|XAI_OAUTH_TOKEN|GROK_OAUTH_TOKEN|CLOUDFLARE_API_TOKEN|CLOUDFLARE_ACCOUNT_ID)["\']')
NATIVE_INFERENCE = re.compile(r"(?:from fastembed import TextEmbedding|from chronos import Chronos2Pipeline|\.from_pretrained\()")
PROVIDER_SDK = re.compile(r"(?:^\s*(?:from|import)\s+(?:openai|anthropic|google\.genai)\b|from\s*[\"'](?:@ai-sdk/|@anthropic-ai/sdk|openai[\"']|ai[\"'])|require\([\"'](?:openai|@anthropic-ai/sdk|@ai-sdk/))")
LEGACY_TRANSPORT = re.compile(r"(?:\bnvidia_rate_limit\b|(?:from|import)\s+(?:scripts\.)?subscription_tokens\b|subscription_tokens\.py)")
PRIVATE_GATEWAY = re.compile(r"(?:\bmodel_gateway\.(?:engine|native|specialized|credentials|_[A-Za-z])\b|from\s+(?:scripts\.)?model_gateway\s+import[^\n]*\b(?:engine|native|specialized|credentials|_[A-Za-z][A-Za-z_]*)\b)")
MUTABLE_CLI_INSTALL = re.compile(r"https://(?:x\.ai/cli/install\.sh|claude\.ai/install\.sh|antigravity\.google/cli/install\.sh|fx\.sh/setup\.sh)")
# These are provider bookkeeping behind the gateway. Coding agents receive no exemption.
NON_APP = {
    "scripts/nvidia_rate_limit.py": "gateway-only quota primitive; all external imports and proxy CLI calls are forbidden",
    "scripts/subscription_tokens.py": "dedicated provider credential keeper behind the gateway",
    "scripts/subscription_vault.py": "dedicated credential broker behind the gateway",
}


def _source_violations(text):
    return [number for number, line in enumerate(text.splitlines(), 1)
            if any(rule.search(line) for rule in (PROVIDER_HTTP, NATIVE_INFERENCE, PROVIDER_AUTH_READ, PROVIDER_SDK, LEGACY_TRANSPORT, PRIVATE_GATEWAY, MUTABLE_CLI_INSTALL))]


@pytest.mark.parametrize("source", [
    'from nvidia_rate_limit import ensure_proxy',
    'curl -fsSL https://x.ai/cli/install.sh | bash',
    'curl -fsSL https://claude.ai/install.sh | bash',
    'nvidia_rate_limit.ensure_proxy(18431, state=path, log=log, rpm=20)',
    'python3 scripts/nvidia_rate_limit.py ensure-proxy --state /tmp/state',
    'from subscription_tokens import default_probe',
    'from openai import OpenAI',
    'from model_gateway.engine import complete_json',
    'from model_gateway import client, specialized',
    'import { createOpenAI } from "@ai-sdk/openai";',
    'requests.post("https://api.openai.com/v1/responses")',
])
def test_guard_rejects_legacy_transports_keepers_and_new_provider_sdks(source):
    assert _source_violations(source), "A new consumer must use the gateway"


def test_app_has_no_direct_provider_or_native_inference():
    paths = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT).decode().split("\0")
    offenders = []
    for relative in paths:
        if not relative or relative in NON_APP or relative.startswith("scripts/model_gateway/"):
            continue
        parts = Path(relative).parts
        if not relative.startswith(("scripts/", "web/", "lib/", "cloud/", "site/")) or any(part in {"tests", "__tests__", "e2e", "fixtures"} for part in parts):
            continue
        path = ROOT / relative
        if not path.is_file():
            continue
        if path.suffix not in {".py", ".js", ".ts", ".tsx", ".sh"} or path.name.startswith("test_"):
            continue
        for number in _source_violations(path.read_text()):
            offenders.append(f"{relative}:{number}")
    assert not offenders, "App inference bypasses the model gateway: " + ", ".join(offenders)


def test_public_python_ladder_has_no_engine_or_credential_access():
    text = (ROOT / "scripts/clients/model_ladder.py").read_text()
    assert "from model_gateway.client import" in text
    for forbidden in ("from model_gateway.engine", "import model_gateway.engine", "subscription_auth", "_auth_for", "subprocess.run", "requests.post", "httpx.post"):
        assert forbidden not in text


def test_independent_reviewers_use_verified_gateway_transport_and_capacity():
    reviewer = (ROOT / "scripts/unattended_release.py").read_text()
    probe = (ROOT / "scripts/codex_capability_probe.py").read_text()
    assert "with gateway_review(" in reviewer
    assert 'request("models"' in reviewer
    assert "models_cache.json" not in reviewer
    assert "from model_gateway import cli" in probe
    assert "cli.configured_cli('codex'" in probe
    assert "'login', 'status'" not in probe
