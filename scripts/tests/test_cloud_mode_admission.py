"""R-729 / REL-313: cloud-thin admission respects the split host topology.

Execute the actual launcher in a temporary tree with every host/network command
fake. No production env, socket, broker or scheduler is touched.
"""
import os
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]


def _run(tmp_path, *, start_rc=0, stop_rc=0, health='authenticated', busy=False, docker_rc=0):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    (tmp_path / 'web').mkdir()
    (scripts / 'cloud.sh').write_text((SCRIPTS / 'cloud.sh').read_text())
    log = tmp_path / 'calls'
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    # python3 is the legacy TCP probe; python3.13 executes the real bounded
    # preflight through a fake transport injected with runpy (no live socket).
    shim = bin_dir / 'python3.13'
    shim.write_text(f'''#!{os.sys.executable}
import io, json, os, subprocess, sys, urllib.request
real_run = subprocess.run
class Response(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *args): self.close()
def urlopen(request, **kwargs):
    with open({str(log)!r}, 'a') as f: f.write('health ' + str(request) + ' timeout=' + str(kwargs.get('timeout')) + '\\n')
    return Response(json.dumps({{'auth_state': {health!r}, 'port_listening': True, 'operator_hold': False}}).encode())
urllib.request.urlopen = urlopen
sys.argv = sys.argv[1:]
exec(compile(sys.stdin.read(), 'cloud-preflight', 'exec'), {{'__name__': '__main__'}})
''')
    shim.chmod(0o755)
    commands = {
        scripts / 'ib': ('ib', 'exit 0'),
        scripts / '_set_radon_mode.sh': ('mode', 'exit 0'),
        scripts / 'docker_ib_gateway.sh': ('gateway', f'exit {stop_rc}'),
        bin_dir / 'ssh': ('ssh', f'echo "already running"; exit {start_rc}'),
        bin_dir / 'python3': ('probe', 'exit 0'),
        bin_dir / 'docker': ('docker', f'echo ib-gateway-ib-gateway-1; exit {docker_rc}'),
        bin_dir / 'launchctl': ('launchctl', 'exit 0'),
        bin_dir / 'ifconfig': ('ifconfig', 'exit 0'),
        bin_dir / 'netstat': ('netstat', 'exit 0'),
        bin_dir / 'lsof': ('lsof', f'exit {0 if busy else 1}'),
        bin_dir / 'sleep': ('sleep', 'exit 0'),
        bin_dir / 'npm': ('npm', 'printf "api=%s ws=%s public=%s profile=%s\\n" "$RADON_API_URL" "$IB_REALTIME_WS_URL" "$NEXT_PUBLIC_IB_REALTIME_WS_URL" "$RADON_DEV_PROFILE"'),
    }
    for path, (name, action) in commands.items():
        path.write_text(f'#!/bin/sh\nprintf "%s\\n" "{name} $*" >> "{log}"\n{action}\n')
        path.chmod(0o755)
    result = subprocess.run(['bash', str(scripts / 'cloud.sh')],
                            env={**os.environ, 'PATH': f'{bin_dir}:{os.environ["PATH"]}'},
                            capture_output=True, text=True, timeout=10)
    return result, log.read_text().splitlines()


def test_cloud_start_targets_broker_after_local_logout(tmp_path):
    result, calls = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    start = 'ssh -o BatchMode=yes -o ConnectTimeout=5 root@radon-broker /usr/local/bin/radon-ib-gateway-control start'
    assert start in calls
    assert calls.index('gateway stop') < calls.index(start) < calls.index('ib mode cloud')


def test_cloud_workspace_uses_permitted_api_and_authenticated_caddy_relay(tmp_path):
    result, calls = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert 'api=http://radon-app:8321 ws=wss://app.radon.run/ws public=wss://app.radon.run/ws profile=cloud-thin' in result.stdout
    assert not any('radon-app 4001' in c for c in calls)
    assert 'health http://radon-app:8321/health/lite timeout=5' in calls


@pytest.mark.parametrize('health', ['unknown', 'awaiting_2fa', 'unreachable'])
def test_unready_broker_refuses_before_mode_or_workspace(tmp_path, health):
    result, calls = _run(tmp_path, health=health)
    assert result.returncode != 0
    assert 'ib mode cloud' not in calls
    assert not any(c.startswith('npm') or c.startswith('mode ') for c in calls)


def test_failed_local_logout_never_starts_remote_broker(tmp_path):
    result, calls = _run(tmp_path, stop_rc=1)
    assert result.returncode != 0
    assert not any(c.startswith('ssh') for c in calls)
    assert 'ib mode cloud' not in calls


def test_busy_workspace_refuses_before_broker_or_mode_changes(tmp_path):
    result, calls = _run(tmp_path, busy=True)
    assert result.returncode != 0
    assert not any(c.startswith(('ssh', 'gateway', 'ib ', 'mode ')) for c in calls)


@pytest.mark.parametrize('fault', ['timeout', 'spawn-error'])
def test_remote_start_is_bounded_and_not_retried(monkeypatch, fault):
    source = (SCRIPTS / 'cloud.sh').read_text()
    assert "<<'PYTHON'\n" in source
    code = source.split("<<'PYTHON'\n", 1)[1].split('\nPYTHON', 1)[0]
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if fault == 'timeout':
            raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
        raise OSError('injected missing SSH executable')
    monkeypatch.setattr(subprocess, 'run', run)
    with pytest.raises(SystemExit) as result:
        exec(compile(code, 'cloud-start', 'exec'), {})
    assert result.value.code == 1
    assert len(calls) == 1
    assert calls[0][1]['timeout'] == 180
    assert calls[0][0][-2:] == ['root@radon-broker', '/usr/local/bin/radon-ib-gateway-control start']


def test_cloud_mode_persists_broker_host_instead_of_app(tmp_path):
    """Execute mode persistence with its output redirected to disposable state."""
    script = tmp_path / 'ib'
    source = (SCRIPTS / 'ib').read_text().replace(
        'MODE_FILE="$PROJECT_ROOT/.env.ib-mode"', f'MODE_FILE="{tmp_path}/mode.cfg"')
    script.write_text(source)
    result = subprocess.run(['bash', str(script), 'mode', 'cloud'],
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert 'IB_GATEWAY_HOST=radon-broker\n' in (tmp_path / 'mode.cfg').read_text()


@pytest.mark.parametrize('fault', ['unknown-hold', 'held', 'closed-port', 'malformed', 'oversize', 'timeout'])
def test_readiness_fault_never_authorizes_mode(monkeypatch, fault):
    import io
    import json
    import urllib.request
    code = (SCRIPTS / 'cloud.sh').read_text().split("<<'HEALTH'\n", 1)[1].split('\nHEALTH', 1)[0]
    state = {'auth_state': 'authenticated', 'operator_hold': False, 'port_listening': True}
    if fault == 'unknown-hold': state['operator_hold'] = None
    if fault == 'held': state['operator_hold'] = True
    if fault == 'closed-port': state['port_listening'] = False
    class Response(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *args): self.close()
    calls = []
    def urlopen(url, **kwargs):
        calls.append((url, kwargs))
        if fault == 'timeout': raise TimeoutError('injected stalled health')
        raw = b'not-json' if fault == 'malformed' else b' ' * 65537 if fault == 'oversize' else json.dumps(state).encode()
        return Response(raw)
    monkeypatch.setattr(urllib.request, 'urlopen', urlopen)
    with pytest.raises(SystemExit) as result:
        exec(compile(code, 'cloud-readiness', 'exec'), {})
    assert result.value.code == 1
    assert calls == [('http://radon-app:8321/health/lite', {'timeout': 5})]


def test_unknown_local_gateway_inventory_refuses_remote_start(tmp_path):
    result, calls = _run(tmp_path, docker_rc=1)
    assert result.returncode != 0
    assert not any(c.startswith('ssh') for c in calls)
    assert 'ib mode cloud' not in calls


@pytest.mark.parametrize('phase', ['inventory', 'logout'])
@pytest.mark.parametrize('fault', ['timeout', 'spawn-error'])
def test_local_logout_children_are_bounded_and_failure_refuses(monkeypatch, phase, fault):
    from types import SimpleNamespace
    code = (SCRIPTS / 'cloud.sh').read_text().split("<<'LOCAL'\n", 1)[1].split('\nLOCAL', 1)[0]
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if phase == 'logout' and len(calls) == 1:
            return SimpleNamespace(returncode=0, stdout='ib-gateway-ib-gateway-1\n')
        if fault == 'timeout': raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
        raise OSError('injected unavailable command')
    monkeypatch.setattr(subprocess, 'run', run)
    monkeypatch.setattr('sys.argv', ['-', '/fake/docker_ib_gateway.sh'])
    with pytest.raises(SystemExit) as result:
        exec(compile(code, 'cloud-local-logout', 'exec'), {})
    assert result.value.code == 1
    assert [kwargs['timeout'] for _, kwargs in calls] == ([10] if phase == 'inventory' else [10, 60])
