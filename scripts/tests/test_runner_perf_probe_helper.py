"""The performance agent consumes a snapshot without inheriting probe credentials."""
from __future__ import annotations

import importlib.util
import json
import grp
import os
from pathlib import Path
import plistlib
import subprocess
import sys
from datetime import datetime, timezone

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / 'scripts/runner/perf_probe_helper.py'
INSTALL = ROOT / 'scripts/runner/install.sh'


@pytest.fixture
def helper(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('perf_probe_helper', HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    base = tmp_path / 'snapshot-service'
    base.mkdir(mode=0o755)
    private = base / 'private'
    private.mkdir(mode=0o700)
    public = base / 'public'
    public.mkdir(mode=0o750)
    (private / 'id_ed25519').write_text('synthetic fixture key')
    (private / 'known_hosts').write_text('synthetic pinned host')
    for path in private.iterdir():
        path.chmod(0o600)
    config = base / 'config.json'
    config.write_text(json.dumps({'target': 'probe@example.test'}))
    config.chmod(0o640)
    monkeypatch.setattr(module, 'ROOT_UID', os.getuid())
    monkeypatch.setattr(module, 'READ_GROUP', grp.getgrgid(os.getgid()).gr_name)
    monkeypatch.setattr(module, 'BASE', base)
    monkeypatch.setattr(module, 'HELPER_USER', __import__('pwd').getpwuid(os.getuid()).pw_name)
    module.fixture_private, module.fixture_public = private, public
    module.fixture_report = {'schema': 1, 'taken_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'host': 'fixture', 'errors': {}}
    return module


def test_helper_is_a_separate_non_root_scheduled_user_with_fixed_arguments():
    result = subprocess.run(['bash', str(INSTALL), '--print-perf-probe-plist'], capture_output=True, check=True)
    plist = plistlib.loads(result.stdout)
    assert plist['UserName'] == '_radonprobe'
    assert plist['ProgramArguments'][-1].endswith('/perf_probe_helper.py')
    assert '-I' in plist['ProgramArguments']
    assert plist['StartCalendarInterval'] == {'Hour': 21, 'Minute': 55}
    assert not plist.get('RunAtLoad') and not plist.get('KeepAlive')
    assert 'EnvironmentVariables' not in plist


def test_consumer_has_no_ssh_credential_or_same_uid_fallback():
    hook = (ROOT / 'scripts/runner/hooks/performance_pre.sh').read_text()
    assert 'RADON_PERF_SSH_KEY' not in hook
    assert 'RADON_PERF_SSH:-' not in hook
    assert 'radon_perf_probe' not in hook
    assert 'perf_probe_helper.py' in hook and 'read' in hook


def test_collector_only_releases_valid_atomic_snapshot(helper, monkeypatch):
    calls = []
    def capture(argv):
        calls.append(argv)
        return json.dumps(helper.fixture_report).encode()
    monkeypatch.setattr(helper, 'ssh_output', capture)
    helper.collect()
    output = helper.fixture_public / 'prod-probe.json'
    assert json.loads(output.read_text()) == helper.fixture_report
    assert output.stat().st_mode & 0o777 == 0o640
    assert list(helper.fixture_public.iterdir()) == [output]
    argv = calls[0]
    assert argv[0] == '/usr/bin/ssh'
    for option in ('StrictHostKeyChecking=yes', 'IdentitiesOnly=yes', 'IdentityAgent=none', 'BatchMode=yes', 'GlobalKnownHostsFile=/dev/null'):
        assert option in argv
    assert '-F' in argv and '/dev/null' in argv
    assert argv[-2:] == ['probe@example.test', 'probe']


@pytest.mark.parametrize('change', ['bad_json', 'wrong_schema', 'old', 'future', 'oversize'])
def test_invalid_remote_output_never_replaces_existing_snapshot(helper, monkeypatch, change):
    output = helper.fixture_public / 'prod-probe.json'
    output.write_text('preserved')
    output.chmod(0o640)
    report = dict(helper.fixture_report)
    if change == 'wrong_schema': report['schema'] = 999
    if change == 'old': report['taken_at'] = '2000-01-01T00:00:00Z'
    if change == 'future': report['taken_at'] = '2100-01-01T00:00:00Z'
    payload = b'bad' if change == 'bad_json' else json.dumps(report).encode()
    if change == 'oversize': payload = b' ' * 1_000_001
    monkeypatch.setattr(helper, 'ssh_output', lambda argv: payload)
    with pytest.raises((ValueError, RuntimeError)):
        helper.collect()
    assert output.read_text() == 'preserved'
    assert not list(helper.fixture_public.glob('.probe-*'))


@pytest.mark.parametrize('component', ['private', 'public', 'private/id_ed25519', 'private/known_hosts', 'config.json'])
def test_helper_rejects_symlinked_paths_without_touching_targets(helper, tmp_path, monkeypatch, component):
    path = helper.BASE / component
    moved = tmp_path / ('moved-' + component.replace('/', '-'))
    path.rename(moved)
    path.symlink_to(moved, target_is_directory=moved.is_dir())
    monkeypatch.setattr(helper, 'ssh_output', lambda argv: pytest.fail('must refuse before SSH'))
    with pytest.raises((ValueError, RuntimeError, OSError)):
        helper.collect()
    assert not (helper.fixture_public / 'prod-probe.json').exists()


def test_helper_refuses_agent_writable_credential_or_output_directories(helper, monkeypatch):
    helper.fixture_public.chmod(0o777)
    monkeypatch.setattr(helper, 'ssh_output', lambda argv: pytest.fail('must refuse before SSH'))
    with pytest.raises((ValueError, RuntimeError)):
        helper.collect()


def test_reader_rejects_stale_snapshot(helper):
    output = helper.fixture_public / 'prod-probe.json'
    report = dict(helper.fixture_report, taken_at='2000-01-01T00:00:00Z')
    output.write_text(json.dumps(report))
    with pytest.raises((ValueError, RuntimeError)):
        helper.read_snapshot()


def test_root_updater_installs_helper_without_replacing_credentials():
    body = INSTALL.read_text()
    assert 'perf_probe_helper.py' in body
    assert 'install_perf_probe_service' in body
    assert 'ensure_perf_probe_user' in body
    assert 'id_ed25519' not in body.split('install_runner() {', 1)[1].split('\n}\n', 1)[0]


def test_reader_accepts_only_the_authoritative_file(helper):
    output = helper.fixture_public / 'prod-probe.json'
    output.write_text(json.dumps(helper.fixture_report))
    output.chmod(0o640)
    assert helper.read_snapshot() == helper.fixture_report
    output.chmod(0o666)
    with pytest.raises(RuntimeError): helper.read_snapshot()


def test_output_symlink_is_refused_without_touching_its_target(helper, monkeypatch, tmp_path):
    target = tmp_path / 'preserve'
    target.write_text('untouched')
    (helper.fixture_public / 'prod-probe.json').symlink_to(target)
    monkeypatch.setattr(helper, 'ssh_output', lambda argv: json.dumps(helper.fixture_report).encode())
    with pytest.raises(RuntimeError): helper.collect()
    with pytest.raises(OSError): helper.read_snapshot()
    assert target.read_text() == 'untouched'


def test_stream_is_bounded_without_a_network_connection(helper):
    # A synthetic child exercises the real transport's stream cap; no SSH.
    assert helper.ssh_output([sys.executable, '-c', 'print("fixture")']) == b'fixture\n'
    with pytest.raises(ValueError, match='size'):
        helper.ssh_output([sys.executable, '-c', 'import sys; sys.stdout.write("x" * 1000001)'])


def test_transport_timeout_kills_the_synthetic_child(helper, monkeypatch):
    monkeypatch.setattr(helper, 'SSH_SECONDS', 0.05)
    with pytest.raises(RuntimeError, match='deadline'):
        helper.ssh_output([sys.executable, '-c', 'import time; time.sleep(10)'])


def test_failed_transport_cannot_replace_the_snapshot(helper, monkeypatch):
    with pytest.raises(RuntimeError, match='SSH failed'):
        helper.ssh_output([sys.executable, '-c', 'raise SystemExit(9)'])


def test_transport_does_not_inherit_credentials_or_ssh_agent(helper, monkeypatch):
    monkeypatch.setenv('SSH_AUTH_SOCK', 'fixture-socket')
    monkeypatch.setenv('GH_TOKEN', 'synthetic-not-a-credential')
    monkeypatch.setenv('PUSHOVER_TOKEN', 'synthetic-not-a-credential')
    payload = helper.ssh_output([sys.executable, '-c', 'import json,os;print(json.dumps(dict(os.environ)))'])
    env = json.loads(payload)
    assert not {'SSH_AUTH_SOCK', 'GH_TOKEN', 'PUSHOVER_TOKEN'} & env.keys()
    assert env['HOME'] == '/var/empty'


@pytest.mark.parametrize('target', ['-Fbad', 'probe@host -oProxyCommand=bad', 'probe@host\nextra', 'root@'])
def test_target_cannot_inject_ssh_options(helper, monkeypatch, target):
    (helper.BASE / 'config.json').write_text(json.dumps({'target': target}))
    monkeypatch.setattr(helper, 'ssh_output', lambda argv: pytest.fail('invalid target must not execute'))
    with pytest.raises(ValueError): helper.collect()


def test_credentials_must_remain_private(helper, monkeypatch):
    (helper.fixture_private / 'id_ed25519').chmod(0o644)
    monkeypatch.setattr(helper, 'ssh_output', lambda argv: pytest.fail('public key input must not execute'))
    with pytest.raises(RuntimeError): helper.collect()


def test_wrong_uid_and_root_cannot_collect(helper, monkeypatch):
    monkeypatch.setattr(helper.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(helper, 'ssh_output', lambda argv: pytest.fail('root must not collect'))
    with pytest.raises(RuntimeError, match='dedicated account'): helper.collect()


def test_non_root_cannot_provision(helper):
    if os.geteuid() == 0: pytest.skip('requires unprivileged CI runner')
    with pytest.raises(RuntimeError, match='administrator'): helper.provision()


def test_atomic_publication_failure_preserves_prior_snapshot(helper, monkeypatch):
    output = helper.fixture_public / 'prod-probe.json'
    output.write_text('preserved')
    output.chmod(0o640)
    monkeypatch.setattr(helper, 'ssh_output', lambda argv: json.dumps(helper.fixture_report).encode())
    def fail_replace(*args, **kwargs): raise OSError('synthetic replace failure')
    monkeypatch.setattr(helper.os, 'replace', fail_replace)
    with pytest.raises(OSError): helper.collect()
    assert output.read_text() == 'preserved'
    assert list(helper.fixture_public.iterdir()) == [output]


def test_updater_checks_helper_lifecycle_before_install():
    body = (ROOT / 'scripts/runner/update_installed.py').read_text()
    assert 'system/com.radon.runner-perf-probe' in body
    assert 'for service in SERVICES:' in body
    assert 'maintenance.lock' in HELPER.read_text()


def _can_access(info, uid, gids, bit):
    shift = 6 if uid == info.st_uid else 3 if info.st_gid in gids else 0
    return bool((info.st_mode >> shift) & bit)


def test_access_matrix_keeps_private_inputs_helper_only_and_snapshot_group_readable(helper, monkeypatch):
    monkeypatch.setattr(helper, 'ssh_output', lambda argv: json.dumps(helper.fixture_report).encode())
    helper.collect()
    owner = os.getuid()
    group = os.getgid()
    bot_uid, unrelated_uid = owner + 10001, owner + 10002
    private = helper.fixture_private.stat()
    key = (helper.fixture_private / 'id_ed25519').stat()
    public = helper.fixture_public.stat()
    output = (helper.fixture_public / 'prod-probe.json').stat()
    assert _can_access(public, bot_uid, {group}, 1)
    assert _can_access(output, bot_uid, {group}, 4)
    assert not _can_access(public, bot_uid, {group}, 2)
    assert not _can_access(private, bot_uid, {group}, 1)
    assert not _can_access(key, bot_uid, {group}, 4)
    assert not _can_access(public, unrelated_uid, set(), 1)
    assert not _can_access(output, unrelated_uid, set(), 4)
