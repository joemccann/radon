from pathlib import Path
import pytest


def test_claude_keeps_local_execution_args_but_uses_ephemeral_bridge_auth(tmp_path):
    from model_gateway.cli import configured_cli
    command = ['/trusted/claude', '-p', 'Task', '--dangerously-skip-permissions', '--disallowedTools', 'ScheduleWakeup']
    env = {'HOME': '/operator', 'GH_TOKEN': 'github-tool-auth', 'CLAUDE_CODE_OAUTH_TOKEN': 'forbidden-model-grant'}
    with configured_cli('claude', command, 'http://127.0.0.1:12345', 'app-token', env=env, state_dir=tmp_path) as (args, child):
        assert args == command
        assert child['HOME'] == '/operator'
        assert child['GH_TOKEN'] == 'github-tool-auth'
        assert 'CLAUDE_CODE_OAUTH_TOKEN' not in child
        assert child['ANTHROPIC_BASE_URL'] == 'http://127.0.0.1:12345'
        assert child['ANTHROPIC_AUTH_TOKEN'] == 'app-token'


def test_codex_uses_documented_custom_responses_provider_without_changing_tools(tmp_path):
    from model_gateway.cli import configured_cli
    command = ['/trusted/codex', 'exec', '--dangerously-bypass-approvals-and-sandbox', '-']
    with configured_cli('codex', command, 'http://127.0.0.1:12345', 'app-token', env={'HOME': '/operator'}, state_dir=tmp_path) as (args, child):
        assert args[0] == command[0]
        assert args[:len(command)] == command
        assert any('model_provider="radon_gateway"' == item for item in args)
        assert any('wire_api="responses"' in item for item in args)
        assert '--ignore-user-config' not in args
        assert child['HOME'] == '/operator'
        assert child['RADON_AGENT_BRIDGE_TOKEN'] == 'app-token'


def test_grok_uses_private_leader_and_auth_state_with_supported_endpoint_overrides(tmp_path):
    from model_gateway.cli import configured_cli
    command = ['/trusted/grok', '--prompt-file', '/task', '--always-approve', '--no-auto-update', '-m', 'grok-model']
    with configured_cli('grok', command, 'http://127.0.0.1:12345', 'app-token', env={'HOME': '/operator'}, state_dir=tmp_path) as (args, child):
        assert args[:len(command)] == command
        assert '--leader-socket' in args
        assert child['GROK_AUTH_PATH'].startswith(str(tmp_path))
        assert child['GROK_MODELS_BASE_URL'] == 'http://127.0.0.1:12345/v1'
        assert child['GROK_MODELS_LIST_URL'] == 'http://127.0.0.1:12345/v1/models'
        assert 'local-token' in child['GROK_AUTH_PROVIDER_COMMAND']
        assert child['HOME'] == '/operator'


def test_unknown_cli_cannot_execute_arbitrary_gateway_commands(tmp_path):
    from model_gateway.cli import configured_cli
    with pytest.raises(ValueError):
        with configured_cli('arbitrary', ['/bin/sh'], 'http://127.0.0.1:12345', 'token', env={}, state_dir=tmp_path):
            pass


def test_runner_agent_entrypoints_have_no_private_provider_cli_escape():
    root = Path(__file__).resolve().parents[2]
    source = (root / 'scripts/runner/run_loop.sh').read_text()
    launch = source.split('launch_agent() {', 1)[1].split('# pid<TAB>', 1)[0]
    assert 'model_gateway/cli.py' in launch
    assert 'exec agy ' not in launch
    assert 'exec fx ' not in launch
    assert 'nvidia_rate_limit.py' not in launch
    assert 'CLAUDE_OAUTH' not in source


def test_incident_execution_adapter_keeps_hardened_child_environment():
    root = Path(__file__).resolve().parents[2]
    source = (root / 'scripts/grok_page_responder.py').read_text()
    runner = source.split('def _default_grok_runner(', 1)[1].split('\ndef ', 1)[0]
    assert '_harden_controller_against_child()' in runner
    assert 'model_gateway.cli' in runner
    assert 'grok_runtime.grok_child_env()' in runner


def test_security_catalog_uses_full_service_inventory_and_preserves_skip_newest(monkeypatch):
    from model_gateway import client
    import security_claude_ladder as ladder
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Provider CLI discovery forbidden")))
    calls = []
    def catalog(operation, payload, **kwargs):
        calls.append((operation, payload))
        return {'models': [{'id': mid, 'provider': 'anthropic'} for mid in ['claude-fable-5', 'claude-opus-5', 'claude-sonnet-5']] + [{'id': 'grok-4.7', 'provider': 'grok'}]}
    monkeypatch.setattr(client, 'request', catalog)
    monkeypatch.delenv('RADON_WEEKEND_CLAUDE_CATALOG', raising=False)
    assert ladder.resolve_ladder() == ['claude-opus-5', 'claude-sonnet-5']
    assert calls == [('models', {})]


def test_pinned_grok_bootstrap_uses_only_ephemeral_loopback_application_token(tmp_path):
    from model_gateway.cli import configured_cli
    with configured_cli('grok', ['/trusted/grok', '-m', 'grok-4.7'], 'http://127.0.0.1:12345', 'ephemeral-app-token', env={'XAI_API_KEY': 'forbidden-vendor-grant'}, state_dir=tmp_path) as (_args, child):
        assert 'XAI_API_KEY' not in child
        assert 'local-token' in child['GROK_AUTH_PROVIDER_COMMAND']
        assert child['RADON_AGENT_BRIDGE_TOKEN'] == 'ephemeral-app-token'
        assert 'forbidden-vendor-grant' not in repr(child)
        assert child['GROK_BASE_URL'] == 'http://127.0.0.1:12345/v1'


def test_deepsec_supported_sdk_provider_preserves_execution_policy(tmp_path):
    from model_gateway.cli import configured_cli
    command = ['/trusted/deepsec', 'process', '--agent', 'codex', '--agent-flags', '--approval-policy never']
    with configured_cli('deepsec', command, 'http://127.0.0.1:12345', 'ephemeral-app-token', env={'HOME': '/operator', 'OPENAI_API_KEY': 'forbidden-vendor-grant'}, state_dir=tmp_path) as (args, child):
        assert args == command
        assert child['HOME'] == '/operator'
        assert child['OPENAI_BASE_URL'] == 'http://127.0.0.1:12345/v1'
        assert child['OPENAI_API_KEY'] == 'ephemeral-app-token'
        assert 'forbidden-vendor-grant' not in repr(child)


def test_codex_local_exec_overrides_cannot_discard_gateway_provider(tmp_path):
    from model_gateway.cli import configured_cli
    command = ['/trusted/codex', 'exec', '--dangerously-bypass-approvals-and-sandbox', '-c', 'model_reasoning_effort=medium', '--color', 'never', '-']
    with configured_cli('codex', command, 'http://127.0.0.1:12345', 'app-token', env={'HOME': '/operator'}, state_dir=tmp_path) as (args, child):
        assert args.index('model_provider="radon_gateway"') > args.index('exec')
        assert args.index('model_reasoning_effort=medium') > args.index('exec')
        assert child['RADON_AGENT_BRIDGE_TOKEN'] == 'app-token'


def test_codex_transport_override_is_final_and_precedes_prompt_terminator(tmp_path):
    from model_gateway.cli import configured_cli
    command = ['/trusted/codex', 'exec', '-c', 'model_provider="openai"', '--dangerously-bypass-approvals-and-sandbox', '--', 'Task']
    with configured_cli('codex', command, 'http://127.0.0.1:12345', 'app-token', env={'HOME': '/operator'}, state_dir=tmp_path) as (args, _child):
        assert args.index('model_provider="radon_gateway"') > args.index('model_provider="openai"')
        assert args.index('model_provider="radon_gateway"') < args.index('--')
        assert args[-2:] == ['--', 'Task']
        assert '--dangerously-bypass-approvals-and-sandbox' in args
