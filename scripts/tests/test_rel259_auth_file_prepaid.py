"""Auth-file API keys obey the same explicit prepaid gate as env keys."""
import json

import pytest

from clients.model_ladder import _auth_for, wired_providers


@pytest.mark.parametrize("provider,relative,payload", [
    ("codex", ".codex/auth.json", {"auth_mode": "apikey", "OPENAI_API_KEY": "fake-prepaid"}),
    ("codex", ".codex/auth.json", {"OPENAI_API_KEY": "fake-prepaid"}),
    ("grok", ".grok/auth.json", {"api_key": "fake-prepaid"}),
    ("grok", ".grok/auth.json", {"apiKey": "fake-prepaid"}),
    ("grok", ".grok/auth.json", {"credentials": {"api_key": "fake-prepaid"}}),
])
@pytest.mark.parametrize("allow", [None, "0", "1"])
def test_auth_file_key_requires_explicit_prepaid_opt_in(tmp_path, provider, relative, payload, allow):
    path = tmp_path / relative
    path.parent.mkdir()
    path.write_text(json.dumps(payload))
    env = {"HOME": str(tmp_path), "CODEX_HOME": str(tmp_path / ".codex")}
    if allow is not None:
        env["RADON_LADDER_ALLOW_PREPAID"] = allow
    auth = _auth_for(provider, env)
    if allow == "1":
        assert auth is not None
        assert auth.kind == "api_key"
        assert auth.token == "fake-prepaid"
    else:
        assert auth is None
        assert provider not in wired_providers(env)


@pytest.mark.parametrize("provider,relative,payload", [
    ("codex", ".codex/auth.json", {"auth_mode": "chatgpt", "tokens": {"access_token": "fake-oauth"}}),
    ("grok", ".grok/auth.json", {"access_token": "fake-oauth"}),
    ("grok", ".grok/auth.json", {"credentials": {"access_token": "fake-oauth"}}),
])
def test_oauth_still_wires_without_prepaid(tmp_path, provider, relative, payload):
    path = tmp_path / relative
    path.parent.mkdir()
    path.write_text(json.dumps(payload))
    env = {"HOME": str(tmp_path), "CODEX_HOME": str(tmp_path / ".codex")}
    auth = _auth_for(provider, env)
    assert auth is not None
    assert auth.kind == "subscription"
    assert auth.token == "fake-oauth"
