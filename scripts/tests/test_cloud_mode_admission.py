"""R-729 / REL-313: the laptop launcher attaches to the VPS and starts nothing else.

Execute the actual launcher in a temporary tree with every host/network command
fake. No production env, socket, broker or scheduler is touched.
"""
import os
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]

HOST_LIFECYCLE_COMMANDS = {'ssh', 'docker', 'launchctl', 'gateway', 'ib', 'mode'}
FORBIDDEN_LAUNCHER_SOURCE = (
    'ssh ', 'docker', 'launchctl', '.env.ib-mode', 'radon-ib-gateway-control',
    'ib_2fa_lock', "<<'PYTHON'", "<<'LOCAL'",
)


def _run(tmp_path, *, health='authenticated', operator_hold=False, health_rc=None,
         busy=False, probe_rc=0, routes=''):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    (tmp_path / 'web').mkdir()
    (scripts / 'cloud.sh').write_text((SCRIPTS / 'cloud.sh').read_text())
    log = tmp_path / 'calls'
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    # python3 is the TCP probe; python3.13 executes the real bounded readiness
    # report through a fake transport injected with runpy (no live socket).
    # health_rc injects a transport fault: '401', 'timeout', 'malformed', 'oversize'.
    shim = bin_dir / 'python3.13'
    shim.write_text(f'''#!{os.sys.executable}
import io, json, os, subprocess, sys, urllib.error, urllib.request
class Response(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *args): self.close()
def urlopen(request, **kwargs):
    with open({str(log)!r}, 'a') as f: f.write('health ' + str(request) + ' timeout=' + str(kwargs.get('timeout')) + '\\n')
    fault = {health_rc!r}
    if fault == '401': raise urllib.error.HTTPError(str(request), 401, 'Unauthorized', {{}}, None)
    if fault == 'timeout': raise urllib.error.URLError('injected stalled health')
    if fault == 'malformed': return Response(b'not-json')
    if fault == 'oversize': return Response(b' ' * 65537)
    return Response(json.dumps({{'auth_state': {health!r}, 'port_listening': True, 'operator_hold': {operator_hold!r}}}).encode())
urllib.request.urlopen = urlopen
sys.argv = sys.argv[1:]
exec(compile(sys.stdin.read(), 'cloud-preflight', 'exec'), {{'__name__': '__main__'}})
''')
    shim.chmod(0o755)
    commands = {
        bin_dir / 'git': ('git', 'exit 128'),
        bin_dir / 'python3': ('probe', f'exit {probe_rc}'),
        bin_dir / 'ifconfig': ('ifconfig', 'exit 0'),
        bin_dir / 'netstat': ('netstat', f'cat <<"ROUTES"\n{routes}\nROUTES\nexit 0'),
        bin_dir / 'lsof': ('lsof', f'exit {0 if busy else 1}'),
        bin_dir / 'sleep': ('sleep', 'exit 0'),
        bin_dir / 'npm': ('npm', 'printf "api=%s ws=%s public=%s profile=%s headlines=%s public_headlines=%s\\n" '
                          '"$RADON_API_URL" "$IB_REALTIME_WS_URL" "$NEXT_PUBLIC_IB_REALTIME_WS_URL" '
                          '"$RADON_DEV_PROFILE" "$HEADLINES_WS_URL" "$NEXT_PUBLIC_HEADLINES_WS_URL"'),
    }
    for path, (name, action) in commands.items():
        path.write_text(f'#!/bin/sh\nprintf "%s\\n" "{name} $*" >> "{log}"\n{action}\n')
        path.chmod(0o755)
    result = subprocess.run(['bash', str(scripts / 'cloud.sh')],
                            env={**os.environ, 'PATH': f'{bin_dir}:{os.environ["PATH"]}'},
                            capture_output=True, text=True, timeout=10)
    return result, log.read_text().splitlines()


def _npm_ran(calls):
    return any(c.startswith('npm') for c in calls)


def test_attach_exports_vps_urls_and_runs_next(tmp_path):
    result, calls = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert ('api=http://radon-app:8321 ws=wss://app.radon.run/ws public=wss://app.radon.run/ws '
            'profile=cloud-thin headlines=wss://app.radon.run/ws-headlines '
            'public_headlines=wss://app.radon.run/ws-headlines') in result.stdout
    assert 'health http://radon-app:8321/health/lite timeout=5' in calls
    assert not any('radon-app 4001' in c for c in calls)


def test_attach_never_touches_host_lifecycle(tmp_path):
    result, calls = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert not any(c.split()[0] in HOST_LIFECYCLE_COMMANDS for c in calls)
    source = (SCRIPTS / 'cloud.sh').read_text()
    for forbidden in FORBIDDEN_LAUNCHER_SOURCE:
        assert forbidden not in source, forbidden


def test_busy_workspace_refuses_before_npm(tmp_path):
    result, calls = _run(tmp_path, busy=True)
    assert result.returncode != 0
    assert not _npm_ran(calls)


def test_unreachable_app_refuses(tmp_path):
    result, calls = _run(tmp_path, probe_rc=1)
    assert result.returncode != 0
    assert not _npm_ran(calls)
    assert not any(c.startswith('health') for c in calls)


@pytest.mark.parametrize('fault', ['401', 'malformed', 'oversize', 'timeout'])
def test_readiness_fault_refuses_loudly(tmp_path, fault):
    result, calls = _run(tmp_path, health_rc=fault)
    assert result.returncode != 0
    assert not _npm_ran(calls)
    assert calls.count('health http://radon-app:8321/health/lite timeout=5') == 1
    assert 'Cloud readiness' in result.stderr


@pytest.mark.parametrize('operator_hold', [True, None])
@pytest.mark.parametrize('health', ['awaiting_2fa', 'unknown'])
def test_unready_state_is_report_only(tmp_path, health, operator_hold):
    result, calls = _run(tmp_path, health=health, operator_hold=operator_hold)
    assert result.returncode == 0, result.stderr
    assert _npm_ran(calls)
    assert f'auth_state={health}' in result.stdout


# macOS keeps interface-scoped (flag I) default routes for every point-to-point
# interface, including the ipsec0-2 pairs Continuity creates with the host's own
# LAN address. They never carry traffic; only the unscoped default route does.
SCOPED_IPSEC_ROUTES = """Routing tables

Internet:
Destination        Gateway            Flags               Netif Expire
default            192.168.0.1        UGScg                 en0
default            link#25            UCSIg              ipsec1
default            link#26            UCSIg              ipsec2
192.168.0.0/24     link#14            UCS                   en0
"""

HIJACKED_ROUTES = """Routing tables

Internet:
Destination        Gateway            Flags               Netif Expire
default            10.2.0.1           UGScg               utun5
192.168.0.0/24     link#14            UCS                   en0
"""


def test_scoped_ipsec_default_routes_are_not_a_vpn(tmp_path):
    result, calls = _run(tmp_path, routes=SCOPED_IPSEC_ROUTES)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'owns the default route' not in result.stdout + result.stderr
    assert any(c.startswith('npm') for c in calls)


def test_unscoped_tunnel_default_route_still_refuses(tmp_path):
    result, calls = _run(tmp_path, routes=HIJACKED_ROUTES)
    assert result.returncode != 0
    assert 'utun5 owns the default route' in result.stdout + result.stderr
    assert not any(c.startswith('npm') for c in calls)
    assert not any(c.startswith('health') for c in calls)
