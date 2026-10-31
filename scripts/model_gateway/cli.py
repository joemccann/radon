"""Local coding-client adapter. Tool execution stays local; model grants stay in the service."""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

if __package__ in {None, ""}:
    # Direct root-owned installed file works under Python -I (no cwd imports).
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from model_gateway.cli_bridge import bridge
    from model_gateway.client import request
else:
    from .cli_bridge import bridge
    from .client import request

# Provider auth and endpoint reroutes are not inherited by coding clients. Tool
# credentials (GitHub), HOME policies, git guards and execution arguments remain.
MODEL_ENV_PREFIXES = ('ANTHROPIC_', 'CLAUDE_CODE_OAUTH', 'CLAUDE_CODE_API_', 'CLAUDE_CODE_USE_', 'CLAUDE_API_', 'OPENAI_', 'XAI_', 'GROK_API_', 'GOOGLE_API_', 'GEMINI_API_', 'ANTIGRAVITY_', 'NVIDIA_', 'CEREBRAS_', 'CURSOR_')
MODEL_ENV_KEYS = frozenset({'CODEX_ACCESS_TOKEN', 'CODEX_REFRESH_TOKEN', 'GROK_AUTH', 'GROK_CODE_XAI_API_KEY', 'GROK_AUTH_PROVIDER_COMMAND', 'GROK_AUTH_PATH', 'GROK_CONFIG_PATH', 'GROK_MODELS_BASE_URL', 'GROK_MODELS_LIST_URL', 'GROK_BASE_URL', 'GROK_LEADER_SOCKET', 'RADON_SERVICE_TOKEN'})


def model_free_env(env):
    return {key: value for key, value in env.items() if key not in MODEL_ENV_KEYS and not key.startswith(MODEL_ENV_PREFIXES)}


@contextmanager
def configured_cli(kind, command, url, token, *, env=None, state_dir=None):
    """Apply verified supported CLI overrides without changing execution policy."""
    if kind not in {'claude', 'codex', 'grok', 'deepsec'} or not command:
        raise ValueError('Unsupported model client')
    if not url.startswith('http://127.0.0.1:') or not token:
        raise ValueError('Invalid private bridge')
    child = model_free_env(dict(os.environ if env is None else env))
    child['RADON_AGENT_BRIDGE_TOKEN'] = token
    child['DISABLE_AUTOUPDATER'] = '1'
    for key in ('NO_PROXY', 'no_proxy'):
        child[key] = ','.join(filter(None, [child.get(key), '127.0.0.1', 'localhost']))
    args = list(command)
    with tempfile.TemporaryDirectory(prefix='radon-model-client-', dir=state_dir) as folder:
        if kind == 'claude':
            child.update(ANTHROPIC_BASE_URL=url, ANTHROPIC_AUTH_TOKEN=token)
        elif kind == 'deepsec':
            # Pinned DeepSec2.3.8 reads this documented custom Responses
            # provider path before subscription auth discovery. These values
            # authenticate only the ephemeral loopback bridge, never a vendor.
            child.update(OPENAI_BASE_URL=url + '/v1', OPENAI_API_KEY=token)
        elif kind == 'codex':
            config = 'model_providers.radon_gateway=' + '{name="Radon",base_url=' + json.dumps(url + '/v1') + ',wire_api="responses",requires_openai_auth=false,env_key="RADON_AGENT_BRIDGE_TOKEN",request_max_retries=0,stream_max_retries=0}'
            # Keep final overrides in the same clap scope as exec-local -c.
            # They must win over caller transport preferences, but never become
            # literal prompt text after a -- terminator.
            subcommand = next((i for i, arg in enumerate(args[1:], 1) if arg in {'exec', 'e'}), 0)
            insert = args.index('--', subcommand + 1) if '--' in args[subcommand + 1:] else len(args)
            args[insert:insert] = ['-c', 'model_provider="radon_gateway"', '-c', config]
        else:
            folder = Path(folder)
            model = None
            for index, arg in enumerate(args[:-1]):
                if arg in {'-m', '--model'}:
                    model = args[index + 1]
            # The server exposes only its known catalog rows; do not synthesize
            # provider capabilities in a caller-owned model list.
            ids = [model] if model else []
            config = folder / 'config.toml'
            config.write_text('\n'.join(f'[model.{json.dumps(mid)}]\nmodel = {json.dumps(mid)}\nbase_url = {json.dumps(url + "/v1")}\napi_backend = "chat_completions"\nenv_key = "RADON_AGENT_BRIDGE_TOKEN"\n' for mid in ids))
            config.chmod(0o600)
            # Pinned Grok1.0.70 accepts the supported external-auth command.
            # It returns only the short-lived loopback application token.
            child.update(GROK_CONFIG_PATH=str(config), GROK_AUTH_PATH=str(folder / 'auth.json'), GROK_MODELS_BASE_URL=url + '/v1', GROK_MODELS_LIST_URL=url + '/v1/models', GROK_BASE_URL=url + '/v1', GROK_AUTH_PROVIDER_COMMAND=shlex.join([sys.executable, '-I', str(Path(__file__).resolve()), 'local-token']))
            args += ['--leader-socket', str(folder / 'leader.sock')]
        yield args, child


def run(kind, command, *, timeout=None, env=None, **kwargs):
    """Run an approved native client inside its existing process/tool boundary."""
    with bridge(timeout_seconds=min(timeout or 1800, 1800)) as handle:
        with configured_cli(kind, command, handle.url, handle.token, env=env) as (args, child):
            return subprocess.run(args, env=child, timeout=timeout, **kwargs)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['launch', 'local-token', 'status', 'models'])
    parser.add_argument('kind', nargs='?', choices=['claude', 'codex', 'grok', 'deepsec'])
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.operation == 'local-token':
        token = os.environ.get('RADON_AGENT_BRIDGE_TOKEN')
        if not token:
            return 1
        print(json.dumps({'access_token': token}))
        return 0
    if args.operation in {'models', 'status'}:
        print(json.dumps(request(args.operation, {}, timeout_seconds=20)))
        return 0
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not args.kind or not command:
        parser.error('launch requires a supported client and command')
    try:
        return run(args.kind, command).returncode
    except KeyboardInterrupt:
        return 130
    except Exception:
        print('Model client gateway unavailable', file=sys.stderr)
        return 75


if __name__ == '__main__':
    raise SystemExit(main())
