"""Privileged no-tool reviewers keep their rails and send inference to the gateway."""
from __future__ import annotations
from contextlib import contextmanager
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
import codex_capability_probe as probe
import unattended_release as releases
from model_gateway import client, specialized


def test_reviewer_subscription_check_uses_service_capability(monkeypatch):
    monkeypatch.setattr(probe.subprocess, 'run', lambda *a, **k: pytest.fail('never inspect native grants'))
    monkeypatch.setattr(client, 'request', lambda *a, **k: {'providers': ['codex']})
    probe.assert_subscription(Path('/signed/codex'), {'HOME': '/private/operator'})
    monkeypatch.setattr(client, 'request', lambda *a, **k: {'providers': ['grok']})
    with pytest.raises(RuntimeError, match='subscription'):
        probe.assert_subscription(Path('/signed/codex'), {})


def test_reviewer_capacity_only_uses_gateway_verified_fields(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(client, 'request', lambda *a, **k: {'models': [{'id': 'gpt-6-luna', 'provider': 'openai', 'contextWindow': 272000, 'effectiveContextWindowPercent': 95, 'source': 'cli-metadata', 'base_instructions': 'MUST_NOT_COPY'}]})
    reviews = releases.CodexReviews(SimpleNamespace(reviewers=("gpt-6.1-sol", "gpt-6-luna")), tmp_path / 'reviews')
    assert reviews._model_context('gpt-6-luna') == {'context_window': 272000, 'effective_context_window_percent': 95}
    with pytest.raises(ValueError): reviews._model_context('unknown')


def test_review_bridge_changes_only_transport_and_removes_model_secrets(monkeypatch):
    from model_gateway import cli
    @contextmanager
    def bridge(**kwargs):
        yield SimpleNamespace(url='http://127.0.0.1:12345', token='ephemeral-private-token')
    monkeypatch.setattr(cli, 'bridge', bridge)
    options = ['-a', 'never', 'exec', '--ignore-user-config', '--ignore-rules', '--sandbox', 'read-only', '-c', 'features.shell_tool=false', '--model', 'gpt-6-luna', '-']
    with probe.gateway_review(Path('/signed/codex'), options, {'HOME': '/home/operator', 'PATH': '/usr/bin', 'OPENAI_API_KEY': 'NEVER_FORWARD'}) as (args, env):
        assert args[0] == '/signed/codex'
        assert args[1:1+len(options)] == options
        assert args.count('-') == 1
        assert args[args.index('-a')+1] == 'never'
        assert args[args.index('--sandbox')+1] == 'read-only'
        assert 'NEVER_FORWARD' not in str(env)
        assert env['RADON_AGENT_BRIDGE_TOKEN'] == 'ephemeral-private-token'
        configurations=[args[index+1] for index,arg in enumerate(args[:-1]) if arg=='-c']
        assert configurations[0] == 'features.shell_tool=false'
        assert configurations[-2] == 'model_provider="radon_gateway"'
        assert configurations[-1].startswith('model_providers.radon_gateway=')
        assert 'base_url="http://127.0.0.1:12345/v1"' in configurations[-1]
        assert 'env_key="RADON_AGENT_BRIDGE_TOKEN"' in configurations[-1]


def test_service_codex_metadata_exposes_capacity_without_prompt_or_auth(monkeypatch, tmp_path):
    from model_gateway import engine
    monkeypatch.setenv('HOME', str(tmp_path))
    directory=tmp_path/'.codex';directory.mkdir(mode=0o700)
    cache=directory/'models_cache.json'
    cache.write_text(json.dumps({'models': [{'slug':'gpt-6-luna','display_name':'Luna','context_window':272000,'effective_context_window_percent':95,'base_instructions':'PRIVATE_SOURCE','tools':[{'name':'shell'}]}]}))
    cache.chmod(0o600)
    monkeypatch.setattr(engine, 'wired_providers', lambda: ('codex',))
    monkeypatch.setattr(engine, '_model_for', lambda *a, **k: 'gpt-6-luna')
    result=specialized.model_inventory()
    row=next(row for row in result['models'] if row['id']=='gpt-6-luna')
    assert row['contextWindow']==272000 and row['effectiveContextWindowPercent']==95
    assert row['source']=='cli-metadata'
    assert 'PRIVATE_SOURCE' not in str(result) and 'shell' not in str(result)


def test_operator_release_installs_only_public_gateway_client_helpers():
    import operator_runtime_refresh
    required={'model_gateway/__init__.py','model_gateway/client.py','model_gateway/cli.py','model_gateway/cli_bridge.py'}
    for name in ('radon-operator-releases','radon-toolchain-updater'):
        files=set(operator_runtime_refresh.FILES[name])
        assert required <= files
        assert 'model_gateway/engine.py' not in files
