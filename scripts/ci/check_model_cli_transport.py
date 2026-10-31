#!/usr/bin/env python3
"""Offline immutable-client upgrade gate. Run in a network-isolated CI step.

Linux: Docker RUN --network=none. Darwin: sandbox-exec with outbound traffic
restricted to loopback. No provider credential or operator HOME is admitted.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time

_PACKAGES = {'claude': '@anthropic-ai/claude-code', 'codex': '@openai/codex', 'grok': '@xai-official/grok'}
_PROTOCOLS = {'claude': 'messages', 'codex': 'responses', 'grok': 'chat_completions'}


def _versions(path):
    doc = json.loads(Path(path).read_text())
    return doc.get('cli_versions') or {kind: doc.get('dependencies', {}).get(package) for kind, package in _PACKAGES.items()}


def _stop_owned(process):
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate(timeout=2)


def _probe(kind, root, expected_version, timeout):
    from model_gateway.cli import configured_cli
    from model_gateway.cli_bridge import bridge
    captures = []
    with tempfile.TemporaryDirectory(prefix='radon-native-cli-canary-') as folder:
        home = Path(folder)
        binary = root / 'bin' / kind
        env = {'HOME': folder, 'PATH': str(root / 'bin') + os.pathsep + os.defpath,
               'USER': 'radon-fixture', 'LOGNAME': 'radon-fixture', 'LANG': 'C.UTF-8',
               'DISABLE_AUTOUPDATER': '1', 'GROK_DISABLE_AUTOUPDATER': '1',
               'GROK_AUTO_UPDATE': '0', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1'}
        # Codex's locked JS launcher requires the image's pinned Node runtime.
        env['PATH'] += os.pathsep + '/usr/local/bin'
        version = subprocess.run([str(binary), '--version'], env=env, cwd=folder,
                                 capture_output=True, text=True, timeout=10, check=True).stdout.strip()
        if not expected_version or expected_version not in version:
            raise RuntimeError(f'{kind}: immutable version receipt mismatch')
        prompt = home / 'fixture.txt'
        prompt.write_text('Reply ok without calling any tools.')
        if kind == 'claude':
            args = [str(binary), '-p', prompt.read_text(), '--model', 'claude-opus-5', '--effort', 'medium',
                    '--dangerously-skip-permissions', '--output-format', 'text',
                    '--disallowedTools', 'ScheduleWakeup', 'Monitor', 'CronCreate']
        elif kind == 'codex':
            args = [str(binary), 'exec', '--dangerously-bypass-approvals-and-sandbox', '--skip-git-repo-check',
                    '--ephemeral', '-m', 'gpt-5.5', '-c', 'model_provider="openai"', '-c', 'model_reasoning_effort=medium', '--color', 'never', '-']
        else:
            args = [str(binary), '--prompt-file', str(prompt), '--cwd', folder, '--always-approve',
                    '--no-auto-update', '--no-plan', '-m', 'grok-4.7', '--reasoning-effort', 'high',
                    '--output-format', 'json']

        def gateway(operation, payload, **_kwargs):
            if operation == 'models':
                return {'models': [{'id': 'grok-4.7', 'provider': 'grok', 'label': 'Fixture'}], 'defaultId': 'grok-4.7'}
            if operation != 'native':
                raise AssertionError('Unexpected gateway operation')
            captures.append(payload)
            # Deliberate refusal stops inference before any local tool executes.
            return {'status': 503, 'content_type': 'application/json',
                    'body': {'error': {'type': 'overloaded_error', 'message': 'Offline transport fixture'}}}

        with bridge(gateway=gateway) as local:
            env.update(HTTP_PROXY=local.url, HTTPS_PROXY=local.url, http_proxy=local.url, https_proxy=local.url)
            with configured_cli(kind, args, local.url, local.token, env=env, state_dir=home) as (args, child):
                process = subprocess.Popen(args, env=child, cwd=folder, stdin=subprocess.PIPE,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
                process.stdin.write(prompt.read_bytes())
                process.stdin.close()
                process.stdin = None
                deadline = time.monotonic() + timeout
                while process.poll() is None and not any(row['protocol'] == _PROTOCOLS[kind] for row in captures) and time.monotonic() < deadline:
                    time.sleep(.05)
                _stop_owned(process)
        primary = next((row['request'] for row in captures if row['protocol'] == _PROTOCOLS[kind]), None)
        if not primary or primary.get('stream') is not True or not primary.get('tools'):
            print(json.dumps({'client': kind, 'received_protocols': [row['protocol'] for row in captures], 'native_request_received': primary is not None, 'native_tool_count': len((primary or {}).get('tools') or [])}), file=sys.stderr)
            raise RuntimeError(f'{kind}: native streaming tool request did not reach the private bridge')
        if kind == 'codex' and not {'custom', 'function'} <= {tool.get('type') for tool in primary['tools']}:
            raise RuntimeError('codex: custom grammar or local function tools were lost')
        if kind == 'claude' and not {'thinking', 'context_management', 'output_config'} <= primary.keys():
            raise RuntimeError('claude: native thinking or context controls were lost')
        print(json.dumps({'client': kind, 'version': version, 'protocol': _PROTOCOLS[kind],
                          'native_tool_count': len(primary['tools']), 'result': 'passed'}), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--clients-root', type=Path, default=Path('/opt/radon-clis'))
    parser.add_argument('--versions-file', type=Path)
    parser.add_argument('--timeout', type=float, default=30)
    parser.add_argument('--network-isolated', action='store_true', required=True)
    args = parser.parse_args(argv)
    if not 1 <= args.timeout <= 60:
        parser.error('timeout must be 1..60 seconds')
    if sys.platform.startswith('linux') and any(name != 'lo' for _, name in socket.if_nameindex()):
        raise RuntimeError('Canary requires Docker RUN --network=none')
    if sys.platform == 'darwin':
        # A flag is not isolation evidence: sandbox-exec must reject even a
        # socket connect to the reserved TEST-NET destination before tools run.
        with socket.socket() as probe:
            probe.settimeout(.2)
            try:
                probe.connect(('203.0.113.1', 443))
            except PermissionError:
                pass
            except OSError as exc:
                raise RuntimeError('Canary requires a loopback-only sandbox-exec profile') from exc
            else:
                raise RuntimeError('Canary requires a loopback-only sandbox-exec profile')
    # Darwin and installed consumer trees carry public helpers. Verify those
    # bytes, not a possibly newer helper imported from the checkout running CI.
    parent = next((path for path in (args.clients_root / 'lib', args.clients_root)
                   if (path / 'model_gateway').exists()), Path(__file__).resolve().parents[1])
    for name in ('__init__.py', 'client.py', 'cli.py', 'cli_bridge.py'):
        if not (parent / 'model_gateway' / name).is_file():
            raise RuntimeError('Incomplete immutable public helper payload')
    if any(name == 'model_gateway' or name.startswith('model_gateway.') for name in sys.modules):
        raise RuntimeError('Canary must import the immutable helper tree in a fresh process')
    sys.path.insert(0, str(parent))
    versions = _versions(args.versions_file or args.clients_root / 'package.json')
    for kind in _PACKAGES:
        _probe(kind, args.clients_root, versions.get(kind), args.timeout)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Never print child output, native bodies or application auth tokens.
        print('Native CLI transport gate failed: ' + (str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__), file=sys.stderr)
        raise SystemExit(1)
